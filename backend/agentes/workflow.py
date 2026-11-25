"""Orquestração do fluxo de auditoria com nós tipados e grafo LangGraph.

Os nós são funções comuns `Estado -> Estado` testáveis sem o LangGraph. O módulo não importa
`backend.motor` nem `backend.persistencia`: recebe consolidador e auditor por injeção.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from backend.agentes.anchoring import DEFAULT_CONFIDENCE_THRESHOLD, AnchoringCheckResult
from backend.agentes.client import LLMClient
from backend.agentes.jurista import JuristaInput, run_jurista_agent
from backend.agentes.report import AuditReport, generate_audit_report
from backend.agentes.tradutor import TradutorInput, TradutorResult, run_tradutor_agent
from backend.schemas import (
    Amendment,
    AuditClassification,
    AuditResult,
    Check,
    CheckResult,
    Contract,
    ContractRule,
    DivergenceDirection,
    Document,
    HumanReview,
    Invoice,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    StrictModel,
)
from backend.schemas.types import EntityId, NonEmptyStr

ConsolidateTimelineFn = Callable[
    [Sequence[ContractRule], Sequence[Amendment]],
    tuple[ContractRule, ...],
]
AuditInvoiceFn = Callable[
    [
        Invoice,
        Contract | None,
        Sequence[ContractRule],
        Sequence[Amendment],
        Sequence[Document],
        Sequence[Invoice],
    ],
    AuditResult,
]

_DECISION_TO_STATUS: dict[ReviewDecision, ReviewStatus] = {
    ReviewDecision.CONFIRM: ReviewStatus.CONFIRMED,
    ReviewDecision.CORRECT: ReviewStatus.CORRECTED,
    ReviewDecision.REJECT: ReviewStatus.REJECTED,
}


class RuleReviewInput(StrictModel):
    """Decisão humana submetida para aprovar, corrigir ou rejeitar uma regra extraída."""

    rule_id: EntityId
    user: NonEmptyStr
    decision: ReviewDecision
    justification: NonEmptyStr
    reviewed_at: datetime
    corrected_rule: ContractRule | None = None


class ContractWorkflowState(StrictModel):
    """Estado do fluxo de contrato: extração → verificação → revisão humana → consolidação."""

    contract: Contract
    document: Document
    jurista_input: JuristaInput
    extracted_rules: tuple[ContractRule, ...] = ()
    anchoring_results: tuple[AnchoringCheckResult, ...] = ()
    amendments: tuple[Amendment, ...] = ()
    review_decisions: tuple[RuleReviewInput, ...] = ()
    human_reviews: tuple[HumanReview, ...] = ()
    consolidated_rules: tuple[ContractRule, ...] = ()
    awaiting_human_review: bool = False
    warnings: tuple[str, ...] = ()


class InvoiceWorkflowState(StrictModel):
    """Estado do fluxo de cobrança: regras → vínculo → revisão → motor → relatório."""

    invoice: Invoice
    contract: Contract | None = None
    consolidated_rules: tuple[ContractRule, ...] = ()
    amendments: tuple[Amendment, ...] = ()
    documents: tuple[Document, ...] = ()
    other_invoices: tuple[Invoice, ...] = ()
    active_rules_for_period: tuple[ContractRule, ...] = ()
    translation_result: TradutorResult | None = None
    awaiting_link_review: bool = False
    links_approved_by_human: bool = False
    audit_result: AuditResult | None = None
    report: AuditReport | None = None
    warnings: tuple[str, ...] = ()


class ContractGraphEnvelope(TypedDict):
    """Envelope serializado em JSON para checkpoint seguro no LangGraph."""

    state_json: str


class InvoiceGraphEnvelope(TypedDict):
    """Envelope serializado em JSON para checkpoint seguro no LangGraph."""

    state_json: str


def _strip_evidence_computed_fields(node: object, *, inside_evidence: bool = False) -> object:
    if isinstance(node, list):
        return [_strip_evidence_computed_fields(item) for item in node]
    if isinstance(node, dict):
        cleaned: dict[str, object] = {}
        for key, value in node.items():
            if inside_evidence and key == "without_page":
                continue
            cleaned[key] = _strip_evidence_computed_fields(
                value, inside_evidence=(key == "evidence")
            )
        return cleaned
    return node


def dump_contract_state(state: ContractWorkflowState) -> ContractGraphEnvelope:
    """Serializa `ContractWorkflowState` para o envelope JSON do grafo LangGraph."""
    raw = _strip_evidence_computed_fields(state.model_dump(mode="json"))
    return {"state_json": json.dumps(raw)}


def load_contract_state(envelope: Mapping[str, object]) -> ContractWorkflowState:
    """Reconstrói `ContractWorkflowState` a partir do envelope retornado pelo grafo."""
    raw = envelope.get("state_json")
    if not isinstance(raw, str):
        raise ValueError("envelope de contrato sem state_json")
    return ContractWorkflowState.model_validate_json(raw)


def dump_invoice_state(state: InvoiceWorkflowState) -> InvoiceGraphEnvelope:
    """Serializa `InvoiceWorkflowState` para o envelope JSON do grafo LangGraph."""
    raw = _strip_evidence_computed_fields(state.model_dump(mode="json"))
    return {"state_json": json.dumps(raw)}


def load_invoice_state(envelope: Mapping[str, object]) -> InvoiceWorkflowState:
    """Reconstrói `InvoiceWorkflowState` a partir do envelope retornado pelo grafo."""
    raw = envelope.get("state_json")
    if not isinstance(raw, str):
        raise ValueError("envelope de cobrança sem state_json")
    return InvoiceWorkflowState.model_validate_json(raw)


def extract_contract_node(state: ContractWorkflowState, client: LLMClient) -> ContractWorkflowState:
    """Executa o Agente Jurista sobre o documento de contrato ou aditivo."""
    result = run_jurista_agent(state.jurista_input, client)
    return state.model_copy(
        update={
            "extracted_rules": result.rules,
            "anchoring_results": result.anchoring_results,
            "warnings": state.warnings + result.warnings,
            "awaiting_human_review": True,
        }
    )


def verify_contract_extraction_node(state: ContractWorkflowState) -> ContractWorkflowState:
    """Exige revisão humana sempre que houver regra não confirmada ou documento sem revisão."""
    pending = any(
        not rule.review_status.is_trusted and rule.review_status is not ReviewStatus.REJECTED
        for rule in state.extracted_rules
    )
    needs_review = pending or not state.document.reviewed
    return state.model_copy(update={"awaiting_human_review": needs_review})


def human_review_rules_node(state: ContractWorkflowState) -> ContractWorkflowState:
    """Aplica decisões humanas às regras extraídas e grava a trilha de revisão."""
    decisions_by_rule = {item.rule_id: item for item in state.review_decisions}
    updated_rules: list[ContractRule] = []
    reviews: list[HumanReview] = list(state.human_reviews)

    for rule in state.extracted_rules:
        decision_input = decisions_by_rule.get(rule.id)
        if decision_input is None:
            updated_rules.append(rule)
            continue
        new_status = _DECISION_TO_STATUS[decision_input.decision]
        base_rule = (
            decision_input.corrected_rule if decision_input.corrected_rule is not None else rule
        )
        if base_rule.id != rule.id:
            raise ValueError(
                f"regra corrigida ({base_rule.id}) não corresponde ao alvo ({rule.id})"
            )
        updated_rules.append(base_rule.model_copy(update={"review_status": new_status}))
        reviews.append(
            HumanReview(
                target_type=ReviewTargetType.RULE,
                target_id=rule.id,
                user=decision_input.user,
                decision=decision_input.decision,
                justification=decision_input.justification,
                previous_status=rule.review_status,
                new_status=new_status,
                reviewed_at=decision_input.reviewed_at,
            )
        )

    still_pending = any(
        not rule.review_status.is_trusted and rule.review_status is not ReviewStatus.REJECTED
        for rule in updated_rules
    )
    document = (
        state.document if still_pending else state.document.model_copy(update={"reviewed": True})
    )
    return state.model_copy(
        update={
            "extracted_rules": tuple(updated_rules),
            "human_reviews": tuple(reviews),
            "document": document,
            "awaiting_human_review": still_pending,
        }
    )


def consolidate_contract_rules_node(
    state: ContractWorkflowState, consolidator: ConsolidateTimelineFn
) -> ContractWorkflowState:
    """Consolida as regras não rejeitadas com os aditivos usando a função determinística."""
    usable = tuple(
        rule for rule in state.extracted_rules if rule.review_status is not ReviewStatus.REJECTED
    )
    consolidated = tuple(consolidator(usable, state.amendments))
    return state.model_copy(update={"consolidated_rules": consolidated})


def fetch_invoice_rules_node(state: InvoiceWorkflowState) -> InvoiceWorkflowState:
    """Seleciona de `consolidated_rules` as regras não rejeitadas que tocam a competência."""
    period = state.invoice.reference_period
    selected = tuple(
        rule
        for rule in state.consolidated_rules
        if rule.review_status is not ReviewStatus.REJECTED
        and (rule.validity.start is None or rule.validity.start <= period.last_day)
        and (rule.validity.end is None or rule.validity.end >= period.first_day)
    )
    return state.model_copy(update={"active_rules_for_period": selected})


def link_invoice_items_node(
    state: InvoiceWorkflowState,
    client: LLMClient,
    *,
    similarity_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
) -> InvoiceWorkflowState:
    """Liga os itens da cobrança às regras vigentes usando o Agente Tradutor."""
    if state.contract is None or not state.invoice.items or not state.active_rules_for_period:
        return state
    translation = run_tradutor_agent(
        TradutorInput(
            document_id=state.invoice.id,
            items=state.invoice.items,
            active_rules=state.active_rules_for_period,
        ),
        client,
        similarity_threshold=similarity_threshold,
    )
    needs_review = not translation.ready_for_calculation and not state.links_approved_by_human
    return state.model_copy(
        update={
            "translation_result": translation,
            "awaiting_link_review": needs_review,
            "warnings": state.warnings + translation.warnings,
        }
    )


def review_ambiguous_links_node(state: InvoiceWorkflowState) -> InvoiceWorkflowState:
    """Registra conclusão da pausa de revisão humana para vínculos ambíguos."""
    return state.model_copy(update={"awaiting_link_review": False})


def run_invoice_motor_node(
    state: InvoiceWorkflowState, auditor_fn: AuditInvoiceFn
) -> InvoiceWorkflowState:
    """Executa o motor determinístico; vínculo não resolvido gera NEEDS_REVIEW sem cálculo."""
    translation = state.translation_result
    if (
        translation is not None
        and not translation.ready_for_calculation
        and not state.links_approved_by_human
    ):
        detail = " ".join(translation.warnings) or "Vínculo item-regra exige revisão humana."
        review_check = Check(
            name="semantic_item_link",
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message=detail,
        )
        result = AuditResult(
            id=f"audit-{state.invoice.id}",
            invoice_id=state.invoice.id,
            classification=AuditClassification.NEEDS_REVIEW,
            checks=(review_check,),
            applied_rules=(),
            reference_period=state.invoice.reference_period,
            total=state.invoice.total,
        )
        return state.model_copy(update={"audit_result": result})

    result = auditor_fn(
        state.invoice,
        state.contract,
        state.consolidated_rules,
        state.amendments,
        state.documents,
        state.other_invoices,
    )
    return state.model_copy(update={"audit_result": result})


def build_invoice_report_node(
    state: InvoiceWorkflowState, *, report_client: LLMClient | None = None
) -> InvoiceWorkflowState:
    """Monta o relatório final verificado a partir do `AuditResult` do motor."""
    if state.audit_result is None:
        raise ValueError("audit_result ausente ao montar o relatório")
    report = generate_audit_report(state.audit_result, client=report_client)
    return state.model_copy(update={"report": report, "warnings": state.warnings + report.warnings})


def build_contract_graph(
    client: LLMClient,
    consolidator: ConsolidateTimelineFn,
    *,
    checkpointer: MemorySaver | None = None,
) -> CompiledStateGraph[ContractGraphEnvelope, None, ContractGraphEnvelope, ContractGraphEnvelope]:
    """Compila o grafo LangGraph do fluxo de contrato com pausa para revisão humana."""

    def _extract(envelope: ContractGraphEnvelope) -> ContractGraphEnvelope:
        return dump_contract_state(extract_contract_node(load_contract_state(envelope), client))

    def _verify(envelope: ContractGraphEnvelope) -> ContractGraphEnvelope:
        return dump_contract_state(verify_contract_extraction_node(load_contract_state(envelope)))

    def _human_review(envelope: ContractGraphEnvelope) -> ContractGraphEnvelope:
        return dump_contract_state(human_review_rules_node(load_contract_state(envelope)))

    def _consolidate(envelope: ContractGraphEnvelope) -> ContractGraphEnvelope:
        return dump_contract_state(
            consolidate_contract_rules_node(load_contract_state(envelope), consolidator)
        )

    def _route_after_verify(envelope: ContractGraphEnvelope) -> str:
        return (
            "human_review"
            if load_contract_state(envelope).awaiting_human_review
            else ("consolidate")
        )

    builder: StateGraph[
        ContractGraphEnvelope, None, ContractGraphEnvelope, ContractGraphEnvelope
    ] = StateGraph(ContractGraphEnvelope)
    builder.add_node("extract", _extract)
    builder.add_node("verify", _verify)
    builder.add_node("human_review", _human_review)
    builder.add_node("consolidate", _consolidate)

    builder.add_edge(START, "extract")
    builder.add_edge("extract", "verify")
    builder.add_conditional_edges(
        "verify",
        _route_after_verify,
        {"human_review": "human_review", "consolidate": "consolidate"},
    )
    builder.add_edge("human_review", "consolidate")
    builder.add_edge("consolidate", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer, interrupt_before=["human_review"])
    return builder.compile()


def build_invoice_graph(
    tradutor_client: LLMClient,
    auditor_fn: AuditInvoiceFn,
    *,
    report_client: LLMClient | None = None,
    similarity_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    checkpointer: MemorySaver | None = None,
) -> CompiledStateGraph[InvoiceGraphEnvelope, None, InvoiceGraphEnvelope, InvoiceGraphEnvelope]:
    """Compila o grafo LangGraph do fluxo de cobrança com pausa em vínculos ambíguos."""

    def _fetch_rules(envelope: InvoiceGraphEnvelope) -> InvoiceGraphEnvelope:
        return dump_invoice_state(fetch_invoice_rules_node(load_invoice_state(envelope)))

    def _link_items(envelope: InvoiceGraphEnvelope) -> InvoiceGraphEnvelope:
        return dump_invoice_state(
            link_invoice_items_node(
                load_invoice_state(envelope),
                tradutor_client,
                similarity_threshold=similarity_threshold,
            )
        )

    def _review_links(envelope: InvoiceGraphEnvelope) -> InvoiceGraphEnvelope:
        return dump_invoice_state(review_ambiguous_links_node(load_invoice_state(envelope)))

    def _run_motor(envelope: InvoiceGraphEnvelope) -> InvoiceGraphEnvelope:
        return dump_invoice_state(run_invoice_motor_node(load_invoice_state(envelope), auditor_fn))

    def _build_report(envelope: InvoiceGraphEnvelope) -> InvoiceGraphEnvelope:
        return dump_invoice_state(
            build_invoice_report_node(load_invoice_state(envelope), report_client=report_client)
        )

    def _route_after_link(envelope: InvoiceGraphEnvelope) -> str:
        return "review_links" if load_invoice_state(envelope).awaiting_link_review else "run_motor"

    builder: StateGraph[InvoiceGraphEnvelope, None, InvoiceGraphEnvelope, InvoiceGraphEnvelope] = (
        StateGraph(InvoiceGraphEnvelope)
    )
    builder.add_node("fetch_rules", _fetch_rules)
    builder.add_node("link_items", _link_items)
    builder.add_node("review_links", _review_links)
    builder.add_node("run_motor", _run_motor)
    builder.add_node("build_report", _build_report)

    builder.add_edge(START, "fetch_rules")
    builder.add_edge("fetch_rules", "link_items")
    builder.add_conditional_edges(
        "link_items",
        _route_after_link,
        {"review_links": "review_links", "run_motor": "run_motor"},
    )
    builder.add_edge("review_links", "run_motor")
    builder.add_edge("run_motor", "build_report")
    builder.add_edge("build_report", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer, interrupt_before=["review_links"])
    return builder.compile()
