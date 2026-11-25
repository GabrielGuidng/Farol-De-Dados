"""Testes de integração I-01: orquestração do fluxo de auditoria com e sem LangGraph."""

from datetime import UTC, datetime
from decimal import Decimal

from langgraph.checkpoint.memory import MemorySaver

from backend.agentes import (
    ContractWorkflowState,
    ExtractedRuleCandidate,
    FakeLLMClient,
    InvoiceWorkflowState,
    ItemRuleLinkCandidate,
    JuristaExtractionPayload,
    JuristaInput,
    ReportExplanationPayload,
    RuleReviewInput,
    TradutorExtractionPayload,
    build_contract_graph,
    build_invoice_graph,
    consolidate_contract_rules_node,
    dump_contract_state,
    dump_invoice_state,
    extract_contract_node,
    fetch_invoice_rules_node,
    human_review_rules_node,
    link_invoice_items_node,
    load_contract_state,
    load_invoice_state,
    run_invoice_motor_node,
    verify_contract_extraction_node,
)
from backend.leitura import PdfPage, PdfReadResult
from backend.motor import apply_amendments, audit_invoice
from backend.schemas import (
    AuditClassification,
    ReviewDecision,
    ReviewStatus,
    RuleRelation,
    RuleType,
)
from tests.fixtures.scenario import (
    dev_amendments,
    dev_documents,
    make_contract,
    make_document,
    make_invoice,
    monthly_item,
)


def _consolidator(rules, amendments):
    return apply_amendments(rules, amendments).rules


def _auditor(invoice, contract, rules, amendments, documents, other_invoices):
    return audit_invoice(
        invoice,
        contract=contract,
        rules=rules,
        amendments=amendments,
        documents=documents,
        other_invoices=other_invoices,
    )


def _amendments_for_extracted_base():
    add01, add02 = dev_amendments()
    return (
        add01.model_copy(update={"affected_rule_id": "doc-contrato-rule-1"}),
        add02,
    )


def _base_contract_state() -> ContractWorkflowState:
    pdf_res = PdfReadResult(
        pages=(PdfPage(page_number=2, text="O valor mensal é de R$ 10.000,00."),),
        read_quality="OK",
        full_text="O valor mensal é de R$ 10.000,00.",
    )
    return ContractWorkflowState(
        contract=make_contract(),
        document=make_document("doc-contrato", reviewed=False),
        jurista_input=JuristaInput.from_pdf("doc-contrato", "CT-2026-001", pdf_res),
        amendments=_amendments_for_extracted_base(),
    )


def _jurista_fake_client() -> FakeLLMClient:
    return FakeLLMClient(
        [
            JuristaExtractionPayload(
                rules=(
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("10000.00"),
                        validity_start="2026-01-01",
                        relation=RuleRelation.CREATED,
                        page=2,
                        literal_excerpt="O valor mensal é de R$ 10.000,00.",
                    ),
                )
            )
        ]
    )


def test_i01_pure_nodes_work_without_langgraph() -> None:
    s1 = extract_contract_node(_base_contract_state(), _jurista_fake_client())
    s2 = verify_contract_extraction_node(s1)
    assert s2.extracted_rules[0].review_status is ReviewStatus.PENDING_REVIEW
    assert s2.awaiting_human_review is True

    reviewed = s2.model_copy(
        update={
            "review_decisions": (
                RuleReviewInput(
                    rule_id=s2.extracted_rules[0].id,
                    user="auditora.simulada",
                    decision=ReviewDecision.CONFIRM,
                    justification="Cláusula conferida no contrato.",
                    reviewed_at=datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
                ),
            )
        }
    )
    s3 = human_review_rules_node(reviewed)
    assert s3.awaiting_human_review is False
    assert s3.document.reviewed is True
    assert s3.extracted_rules[0].review_status is ReviewStatus.CONFIRMED
    assert len(s3.human_reviews) == 1

    s4 = consolidate_contract_rules_node(s3, _consolidator)
    assert len(s4.consolidated_rules) == 3

    invoice = make_invoice("inv-104", "104", "2026-10", [monthly_item("10800.00")])
    inv1 = fetch_invoice_rules_node(
        InvoiceWorkflowState(
            invoice=invoice,
            contract=s4.contract,
            consolidated_rules=s4.consolidated_rules,
            documents=tuple(dev_documents()),
        )
    )
    tradutor_client = FakeLLMClient(
        [
            TradutorExtractionPayload(
                links=(
                    ItemRuleLinkCandidate(
                        item_index=0,
                        rule_id="price-add02",
                        item_excerpt="Mensalidade de suporte",
                        rule_excerpt="Passa a R$ 10.500,00 a partir de setembro de 2026.",
                        similarity_score=Decimal("0.96"),
                    ),
                )
            )
        ]
    )
    inv2 = link_invoice_items_node(inv1, tradutor_client)
    assert inv2.awaiting_link_review is False
    inv3 = run_invoice_motor_node(inv2, _auditor)
    assert inv3.audit_result is not None
    assert inv3.audit_result.classification is AuditClassification.DIVERGENCE
    assert inv3.audit_result.difference == Decimal("300.00")


def test_i01_langgraph_pauses_for_human_review_then_completes_case_a() -> None:
    checkpointer = MemorySaver()
    contract_graph = build_contract_graph(
        _jurista_fake_client(), _consolidator, checkpointer=checkpointer
    )
    config = {"configurable": {"thread_id": "contrato-1"}}

    # 1. Roda até a pausa obrigatória antes de human_review.
    paused = contract_graph.invoke(dump_contract_state(_base_contract_state()), config=config)
    paused_state = load_contract_state(paused)
    assert paused_state.awaiting_human_review is True
    assert paused_state.extracted_rules[0].review_status is ReviewStatus.PENDING_REVIEW
    assert paused_state.consolidated_rules == ()

    # 2. Pessoa revisora confirma a regra e retoma o grafo.
    decision = RuleReviewInput(
        rule_id=paused_state.extracted_rules[0].id,
        user="ana.auditora",
        decision=ReviewDecision.CONFIRM,
        justification="Confirmado com o contrato-base.",
        reviewed_at=datetime(2026, 11, 25, 11, 0, tzinfo=UTC),
    )
    contract_graph.update_state(
        config,
        dump_contract_state(paused_state.model_copy(update={"review_decisions": (decision,)})),
    )
    resumed = contract_graph.invoke(None, config=config)
    final_contract = load_contract_state(resumed)
    assert final_contract.document.reviewed is True
    assert final_contract.extracted_rules[0].review_status is ReviewStatus.CONFIRMED
    assert len(final_contract.consolidated_rules) == 3

    # 3. Fluxo de cobrança para Caso A (out/2026).
    tradutor_client = FakeLLMClient(
        [
            TradutorExtractionPayload(
                links=(
                    ItemRuleLinkCandidate(
                        item_index=0,
                        rule_id="price-add02",
                        item_excerpt="Mensalidade de suporte",
                        rule_excerpt="Passa a R$ 10.500,00 a partir de setembro de 2026.",
                        similarity_score=Decimal("0.95"),
                    ),
                )
            )
        ]
    )
    report_client = FakeLLMClient(
        [
            ReportExplanationPayload(
                summary=(
                    "Em 10/2026, cobrado R$ 10.800,00 frente a R$ 10.500,00 "
                    "(diferença de R$ 300,00; 2,86%)."
                )
            )
        ]
    )
    invoice_graph = build_invoice_graph(
        tradutor_client,
        _auditor,
        report_client=report_client,
        checkpointer=MemorySaver(),
    )
    inv_config = {"configurable": {"thread_id": "inv-104"}}
    inv_out = invoice_graph.invoke(
        dump_invoice_state(
            InvoiceWorkflowState(
                invoice=make_invoice("inv-104", "104", "2026-10", [monthly_item("10800.00")]),
                contract=final_contract.contract,
                consolidated_rules=final_contract.consolidated_rules,
                documents=tuple(dev_documents()),
            )
        ),
        config=inv_config,
    )
    final_inv = load_invoice_state(inv_out)
    assert final_inv.audit_result is not None
    assert final_inv.audit_result.classification is AuditClassification.DIVERGENCE
    assert final_inv.audit_result.difference == Decimal("300.00")
    assert final_inv.report is not None
    assert final_inv.report.used_llm_text is True


def test_i01_invoice_graph_pauses_on_ambiguous_link_and_yields_needs_review() -> None:
    tradutor_client = FakeLLMClient(
        [
            TradutorExtractionPayload(
                links=(
                    ItemRuleLinkCandidate(
                        item_index=0,
                        rule_id="price-add02",
                        item_excerpt="Mensalidade de suporte",
                        rule_excerpt="Passa a R$ 10.500,00 a partir de setembro de 2026.",
                        similarity_score=Decimal("0.60"),
                    ),
                )
            )
        ]
    )
    invoice_graph = build_invoice_graph(
        tradutor_client,
        _auditor,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "inv-amb"}}
    base_extracted = (
        extract_contract_node(_base_contract_state(), _jurista_fake_client())
        .extracted_rules[0]
        .model_copy(update={"review_status": ReviewStatus.CONFIRMED})
    )
    consolidated = _consolidator([base_extracted], _amendments_for_extracted_base())
    paused = invoice_graph.invoke(
        dump_invoice_state(
            InvoiceWorkflowState(
                invoice=make_invoice("inv-104", "104", "2026-10", [monthly_item("10800.00")]),
                contract=make_contract(),
                consolidated_rules=consolidated,
                documents=tuple(dev_documents()),
            )
        ),
        config=config,
    )
    assert load_invoice_state(paused).awaiting_link_review is True
    assert load_invoice_state(paused).audit_result is None

    # Retoma sem aprovar vínculo: o motor não calcula e classifica como NEEDS_REVIEW.
    resumed = invoice_graph.invoke(None, config=config)
    final_state = load_invoice_state(resumed)
    assert final_state.audit_result is not None
    assert final_state.audit_result.classification is AuditClassification.NEEDS_REVIEW
    assert final_state.report is not None
