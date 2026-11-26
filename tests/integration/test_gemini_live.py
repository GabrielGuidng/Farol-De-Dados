"""Testes reais contra a API do Google Gemini (fora da suíte padrão; rodar com `-m llm`)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from backend.agentes import (
    DEFAULT_GEMINI_MODEL,
    ContractWorkflowState,
    GeminiLLMClient,
    InvoiceWorkflowState,
    JuristaInput,
    LLMConfig,
    RuleReviewInput,
    build_contract_graph,
    build_invoice_graph,
    dump_contract_state,
    dump_invoice_state,
    load_contract_state,
    load_invoice_state,
    run_jurista_agent,
)
from backend.leitura import parse_nfe, read_docx, read_pdf
from backend.motor import apply_amendments, audit_invoice
from backend.persistencia import (
    connect,
    get_contract,
    init_db,
    list_documents,
    list_rules,
    receive_document,
    save_amendment,
    save_audit,
    save_contract,
    save_document,
    save_invoice,
    save_rule,
)
from backend.schemas import (
    Amendment,
    AuditClassification,
    Contract,
    DocumentType,
    ReviewDecision,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from tests.fixtures.synthetic_files import SYNTHETIC_DIR

_LIVE_TS = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _get_live_client_or_skip() -> GeminiLLMClient:
    try:
        config = LLMConfig.from_env()
    except ValueError:
        pytest.skip("Defina LLM_API_KEY (no .env ou ambiente) para rodar testes reais do Gemini.")
    if config.model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        config = config.model_copy(update={"model": DEFAULT_GEMINI_MODEL})
    return GeminiLLMClient(config)


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


def _review_extracted_rules(
    rules,
    *,
    fallback_start: date | None = None,
) -> tuple[RuleReviewInput, ...]:
    decisions: list[RuleReviewInput] = []
    for rule in rules:
        if rule.review_status is ReviewStatus.INCONCLUSIVE:
            decisions.append(
                RuleReviewInput(
                    rule_id=rule.id,
                    user="auditora.live",
                    decision=ReviewDecision.REJECT,
                    justification="Regra inconclusiva descartada na revisão humana.",
                    reviewed_at=_LIVE_TS,
                )
            )
        elif rule.validity.start is None and fallback_start is not None:
            decisions.append(
                RuleReviewInput(
                    rule_id=rule.id,
                    user="auditora.live",
                    decision=ReviewDecision.CORRECT,
                    justification="Início de vigência explicitado pela revisora.",
                    reviewed_at=_LIVE_TS,
                    corrected_rule=rule.model_copy(
                        update={
                            "validity": ValidityPeriod(start=fallback_start, end=rule.validity.end)
                        }
                    ),
                )
            )
        else:
            decisions.append(
                RuleReviewInput(
                    rule_id=rule.id,
                    user="auditora.live",
                    decision=ReviewDecision.CONFIRM,
                    justification="Regra conferida no documento sintético.",
                    reviewed_at=_LIVE_TS,
                )
            )
    return tuple(decisions)


@pytest.mark.llm
def test_gemini_live_e2e_contract_and_amendment_to_invoice_divergence(tmp_path: Path) -> None:
    client = _get_live_client_or_skip()
    db_path = tmp_path / "live.db"
    init_db(db_path)

    contract = Contract.model_validate(
        {
            "id": "CT-2026-001",
            "company": "Empresa Exemplo S.A.",
            "supplier": "Fornecedor Exemplo Ltda.",
            "supplier_cnpj": "11.222.333/0001-81",
            "subject": "Serviços de suporte técnico",
            "validity": {"start": "2026-01-01", "end": "2026-12-31"},
            "comparison_tolerance": "0.00",
            "status": "ACTIVE",
        }
    )

    base_pdf = read_pdf(SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf")
    add02_pdf = read_pdf(SYNTHETIC_DIR / "ct_2026_001_aditivo_02.pdf")

    with connect(db_path) as conn:
        save_contract(conn, contract)
        doc_base = receive_document(
            document_id="doc-live-base",
            contract_id=contract.id,
            document_type=DocumentType.CONTRACT,
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            received_at=_LIVE_TS,
            read_quality=base_pdf.read_quality,
            extracted_text=base_pdf.full_text,
            reviewed=False,
            conn=conn,
        )
        doc_add02 = receive_document(
            document_id="doc-live-add02",
            contract_id=contract.id,
            document_type=DocumentType.AMENDMENT,
            file_name="ct_2026_001_aditivo_02.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_02.pdf",
            received_at=_LIVE_TS,
            read_quality=add02_pdf.read_quality,
            extracted_text=add02_pdf.full_text,
            reviewed=False,
            conn=conn,
        )

    contract_graph = build_contract_graph(client, _consolidator, checkpointer=MemorySaver())

    cfg_base = {"configurable": {"thread_id": "live-base"}}
    paused_base = load_contract_state(
        contract_graph.invoke(
            dump_contract_state(
                ContractWorkflowState(
                    contract=contract,
                    document=doc_base,
                    jurista_input=JuristaInput.from_pdf(doc_base.id, contract.id, base_pdf),
                )
            ),
            config=cfg_base,
        )
    )
    assert paused_base.awaiting_human_review is True
    assert all(r.review_status is not ReviewStatus.CONFIRMED for r in paused_base.extracted_rules)
    contract_graph.update_state(
        cfg_base,
        dump_contract_state(
            paused_base.model_copy(
                update={
                    "review_decisions": _review_extracted_rules(
                        paused_base.extracted_rules, fallback_start=date(2026, 1, 1)
                    )
                }
            )
        ),
    )
    final_base = load_contract_state(contract_graph.invoke(None, config=cfg_base))
    base_monthly = next(
        rule
        for rule in final_base.consolidated_rules
        if rule.rule_type is RuleType.MONTHLY_PRICE and rule.value == Decimal("10000.00")
    )

    cfg_add02 = {"configurable": {"thread_id": "live-add02"}}
    paused_add02 = load_contract_state(
        contract_graph.invoke(
            dump_contract_state(
                ContractWorkflowState(
                    contract=contract,
                    document=doc_add02,
                    jurista_input=JuristaInput.from_pdf(
                        doc_add02.id, contract.id, add02_pdf, is_amendment=True
                    ),
                )
            ),
            config=cfg_add02,
        )
    )
    contract_graph.update_state(
        cfg_add02,
        dump_contract_state(
            paused_add02.model_copy(
                update={
                    "review_decisions": _review_extracted_rules(
                        paused_add02.extracted_rules, fallback_start=date(2026, 9, 1)
                    )
                }
            )
        ),
    )
    final_add02 = load_contract_state(contract_graph.invoke(None, config=cfg_add02))
    add02_monthly = next(
        rule
        for rule in final_add02.consolidated_rules
        if rule.rule_type is RuleType.MONTHLY_PRICE and rule.value == Decimal("10500.00")
    )

    amendment_02 = Amendment(
        id="live-add02",
        affected_rule_id=base_monthly.id,
        new_rule=add02_monthly.model_copy(
            update={
                "validity": ValidityPeriod(start=date(2026, 9, 1), end=None),
                "relation": RuleRelation.ALTERED,
                "version": 2,
            }
        ),
        source_document_id=doc_add02.id,
        effective_date=date(2026, 9, 1),
        relation=RuleRelation.ALTERED,
    )
    consolidated = apply_amendments(final_base.consolidated_rules, (amendment_02,)).rules

    with connect(db_path) as conn:
        save_document(conn, final_base.document)
        save_document(conn, final_add02.document)
        for rule in final_base.consolidated_rules:
            save_rule(conn, rule)
        save_amendment(conn, amendment_02)
        for rule in consolidated:
            save_rule(conn, rule)

        loaded_contract = get_contract(conn, contract.id)
        loaded_rules = list_rules(conn, contract.id)
        loaded_docs = list_documents(conn, contract.id)

    invoice = parse_nfe(SYNTHETIC_DIR / "nfe_104_caso_a_divergencia.xml", invoice_id="inv-live-104")
    invoice_graph = build_invoice_graph(
        client,
        _auditor,
        report_client=client,
        checkpointer=MemorySaver(),
    )
    inv_cfg = {"configurable": {"thread_id": "live-inv-104"}}
    inv_raw = invoice_graph.invoke(
        dump_invoice_state(
            InvoiceWorkflowState(
                invoice=invoice,
                contract=loaded_contract,
                consolidated_rules=loaded_rules,
                documents=loaded_docs,
            )
        ),
        config=inv_cfg,
    )
    inv_state = load_invoice_state(inv_raw)
    if inv_state.awaiting_link_review:
        invoice_graph.update_state(
            inv_cfg,
            dump_invoice_state(inv_state.model_copy(update={"links_approved_by_human": True})),
        )
        inv_state = load_invoice_state(invoice_graph.invoke(None, config=inv_cfg))

    assert inv_state.audit_result is not None
    assert inv_state.audit_result.classification is AuditClassification.DIVERGENCE
    assert inv_state.audit_result.difference == Decimal("300.00")
    assert inv_state.report is not None

    with connect(db_path) as conn:
        save_invoice(conn, invoice)
        save_audit(conn, inv_state.audit_result)


@pytest.mark.llm
def test_gemini_live_docx_without_page_and_prompt_injection_guard() -> None:
    client = _get_live_client_or_skip()

    add01_docx = read_docx(SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx")
    res_add01 = run_jurista_agent(
        JuristaInput.from_docx("doc-live-docx-1", "CT-2026-001", add01_docx, is_amendment=True),
        client,
    )
    assert res_add01.schema_valid is True
    assert any(
        rule.value == Decimal("10800.00") and rule.evidence.without_page for rule in res_add01.rules
    )
    assert all(rule.review_status is not ReviewStatus.CONFIRMED for rule in res_add01.rules)

    trap_docx = read_docx(SYNTHETIC_DIR / "ct_2026_002_contrato_armadilhas.docx")
    res_trap = run_jurista_agent(
        JuristaInput.from_docx("doc-live-docx-2", "CT-2026-002", trap_docx),
        client,
    )
    assert res_trap.schema_valid is True
    assert any(rule.value == Decimal("5000.00") for rule in res_trap.rules)
    assert all(not rule.review_status.is_trusted for rule in res_trap.rules)
