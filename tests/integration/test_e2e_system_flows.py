"""Testes de integração ponta a ponta (E2E) unindo leitura, hash, fluxos 1 e 2, motor e SQLite."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from backend.agentes import (
    ContractWorkflowState,
    ExtractedBillingItemCandidate,
    ExtractedRuleCandidate,
    FakeLLMClient,
    FiscalExtractionPayload,
    FiscalPdfInput,
    InvoiceWorkflowState,
    ItemRuleLinkCandidate,
    JuristaExtractionPayload,
    JuristaInput,
    ReportExplanationPayload,
    RuleReviewInput,
    TradutorExtractionPayload,
    build_contract_graph,
    build_invoice_graph,
    load_contract_state,
    load_invoice_state,
    run_fiscal_pdf_agent,
)
from backend.leitura import (
    CPF_MASK,
    RG_MASK,
    import_invoices_csv,
    parse_nfe,
    parse_nfse,
    read_docx,
    read_pdf,
)
from backend.motor import apply_amendments, audit_invoice
from backend.persistencia import (
    append_review,
    connect,
    get_audit,
    get_contract,
    init_db,
    list_amendments,
    list_documents,
    list_invoices,
    list_reviews,
    list_rules,
    receive_document,
    record_document_review,
    save_amendment,
    save_audit,
    save_contract,
    save_document,
    save_invoice,
    save_rule,
    verify_document_integrity,
)
from backend.schemas import (
    Amendment,
    AuditClassification,
    Contract,
    ContractRule,
    DocumentType,
    Invoice,
    ReadQuality,
    ReviewDecision,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from tests.fixtures.synthetic_files import SYNTHETIC_DIR, build_synthetic_dataset_bytes

_REVIEW_TS = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


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


def _ct_2026_001_contract() -> Contract:
    return Contract.model_validate(
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


def _run_contract_flow_to_completion(
    *,
    contract: Contract,
    document,
    jurista_input: JuristaInput,
    extraction_payload: JuristaExtractionPayload,
    decisions_builder,
    thread_id: str,
):
    client = FakeLLMClient([extraction_payload])
    graph = build_contract_graph(client, _consolidator, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": thread_id}}
    initial = ContractWorkflowState(
        contract=contract,
        document=document,
        jurista_input=jurista_input,
    )
    from backend.agentes import dump_contract_state

    paused_raw = graph.invoke(dump_contract_state(initial), config=config)
    paused = load_contract_state(paused_raw)
    assert paused.awaiting_human_review is True

    decisions = tuple(decisions_builder(paused.extracted_rules))
    graph.update_state(
        config,
        dump_contract_state(paused.model_copy(update={"review_decisions": decisions})),
    )
    resumed_raw = graph.invoke(None, config=config)
    return load_contract_state(resumed_raw), client


def _run_invoice_flow(
    *,
    invoice: Invoice,
    contract: Contract | None,
    rules: tuple[ContractRule, ...],
    amendments: tuple[Amendment, ...] = (),
    documents=(),
    other_invoices: tuple[Invoice, ...] = (),
    tradutor_links: tuple[ItemRuleLinkCandidate, ...] = (),
    report_client: FakeLLMClient | None = None,
    thread_id: str,
):
    from backend.agentes import dump_invoice_state

    tradutor_client = FakeLLMClient(
        [TradutorExtractionPayload(links=tradutor_links)] if tradutor_links else []
    )
    graph = build_invoice_graph(
        tradutor_client,
        _auditor,
        report_client=report_client,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": thread_id}}
    initial = InvoiceWorkflowState(
        invoice=invoice,
        contract=contract,
        consolidated_rules=rules,
        amendments=amendments,
        documents=tuple(documents),
        other_invoices=other_invoices,
    )
    output_raw = graph.invoke(dump_invoice_state(initial), config=config)
    return load_invoice_state(output_raw), graph, config


def _setup_ct_2026_001_in_sqlite(db_path: Path) -> tuple[Contract, tuple[str, ...]]:
    init_db(db_path)
    contract = _ct_2026_001_contract()

    base_pdf = read_pdf(SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf")
    add01_docx = read_docx(SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx")
    add02_pdf = read_pdf(SYNTHETIC_DIR / "ct_2026_001_aditivo_02.pdf")

    with connect(db_path) as conn:
        save_contract(conn, contract)
        doc_base = receive_document(
            document_id="doc-ct1-base",
            contract_id=contract.id,
            document_type=DocumentType.CONTRACT,
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            received_at=_REVIEW_TS,
            read_quality=base_pdf.read_quality,
            extracted_text=base_pdf.full_text,
            reviewed=False,
            conn=conn,
        )
        doc_add01 = receive_document(
            document_id="doc-ct1-add01",
            contract_id=contract.id,
            document_type=DocumentType.AMENDMENT,
            file_name="ct_2026_001_aditivo_01.docx",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx",
            received_at=_REVIEW_TS,
            read_quality=add01_docx.read_quality,
            extracted_text=add01_docx.full_text,
            reviewed=False,
            conn=conn,
        )
        doc_add02 = receive_document(
            document_id="doc-ct1-add02",
            contract_id=contract.id,
            document_type=DocumentType.AMENDMENT,
            file_name="ct_2026_001_aditivo_02.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_02.pdf",
            received_at=_REVIEW_TS,
            read_quality=add02_pdf.read_quality,
            extracted_text=add02_pdf.full_text,
            reviewed=False,
            conn=conn,
        )

    base_state, base_client = _run_contract_flow_to_completion(
        contract=contract,
        document=doc_base,
        jurista_input=JuristaInput.from_pdf(
            doc_base.id,
            contract.id,
            base_pdf,
            file_hash=doc_base.file_hash,
        ),
        extraction_payload=JuristaExtractionPayload(
            rules=(
                ExtractedRuleCandidate(
                    rule_type=RuleType.MONTHLY_PRICE,
                    new_value=Decimal("10000.00"),
                    validity_start=date(2026, 2, 1),
                    validity_end=date(2026, 12, 31),
                    relation=RuleRelation.CREATED,
                    page=2,
                    literal_excerpt=(
                        "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 até "
                        "31/12/2026."
                    ),
                ),
                ExtractedRuleCandidate(
                    rule_type=RuleType.FRANCHISE,
                    quantity=Decimal("40"),
                    unit="hora",
                    validity_start=date(2026, 1, 1),
                    relation=RuleRelation.CREATED,
                    page=3,
                    literal_excerpt=(
                        "Cláusula 4ª: A franquia mensal é de 40 horas técnicas desde 01/01/2026."
                    ),
                ),
                ExtractedRuleCandidate(
                    rule_type=RuleType.OVERAGE_PRICE,
                    new_value=Decimal("150.00"),
                    unit="hora",
                    validity_start=date(2026, 1, 1),
                    relation=RuleRelation.CREATED,
                    page=3,
                    literal_excerpt=(
                        "Cláusula 5ª: A hora excedente custa R$ 150,00 desde 01/01/2026."
                    ),
                ),
                ExtractedRuleCandidate(
                    rule_type=RuleType.ADJUSTMENT,
                    adjustment_index="IPCA",
                    periodicity_months=12,
                    validity_start=date(2026, 1, 1),
                    relation=RuleRelation.CREATED,
                    page=3,
                    literal_excerpt=(
                        "Cláusula 6ª: Reajuste pelo índice IPCA a cada 12 meses desde 01/01/2026."
                    ),
                ),
                ExtractedRuleCandidate(
                    rule_type=RuleType.MONTHLY_PRICE,
                    new_value=Decimal("10000.00"),
                    validity_start=date(2026, 1, 1),
                    relation=RuleRelation.CREATED,
                    page=1,
                    literal_excerpt="Cláusula 1ª: Vigência de 01/01/2026 até 31/12/2026.",
                ),
            )
        ),
        decisions_builder=lambda rules: (
            RuleReviewInput(
                rule_id=rules[0].id,
                user="auditora.maria",
                decision=ReviewDecision.CORRECT,
                justification="Corrigida data inicial conforme Cláusula 1ª/3ª (01/01/2026).",
                reviewed_at=_REVIEW_TS,
                corrected_rule=rules[0].model_copy(
                    update={
                        "validity": ValidityPeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
                    }
                ),
            ),
            RuleReviewInput(
                rule_id=rules[1].id,
                user="auditora.maria",
                decision=ReviewDecision.CONFIRM,
                justification="Franquia conferida.",
                reviewed_at=_REVIEW_TS,
            ),
            RuleReviewInput(
                rule_id=rules[2].id,
                user="auditora.maria",
                decision=ReviewDecision.CONFIRM,
                justification="Valor de excedente conferido.",
                reviewed_at=_REVIEW_TS,
            ),
            RuleReviewInput(
                rule_id=rules[3].id,
                user="auditora.maria",
                decision=ReviewDecision.CONFIRM,
                justification="Cláusula de reajuste conferida.",
                reviewed_at=_REVIEW_TS,
            ),
            RuleReviewInput(
                rule_id=rules[4].id,
                user="auditora.maria",
                decision=ReviewDecision.REJECT,
                justification="Regra espúria sem ancoragem monetária no trecho.",
                reviewed_at=_REVIEW_TS,
            ),
        ),
        thread_id="e2e-ct1-base",
    )
    sent_prompt = base_client.calls[0].user_prompt
    assert CPF_MASK in sent_prompt
    assert RG_MASK in sent_prompt
    assert "123.456.789-09" not in sent_prompt
    assert "12.345.678-9" not in sent_prompt
    assert "11.222.333/0001-81" in sent_prompt
    assert base_state.extracted_rules[4].review_status is ReviewStatus.REJECTED
    assert all(r.id != base_state.extracted_rules[4].id for r in base_state.consolidated_rules)

    add01_state, _ = _run_contract_flow_to_completion(
        contract=contract,
        document=doc_add01,
        jurista_input=JuristaInput.from_docx(
            doc_add01.id,
            contract.id,
            add01_docx,
            file_hash=doc_add01.file_hash,
            is_amendment=True,
        ),
        extraction_payload=JuristaExtractionPayload(
            rules=(
                ExtractedRuleCandidate(
                    rule_type=RuleType.MONTHLY_PRICE,
                    new_value=Decimal("10800.00"),
                    previous_value=Decimal("10000.00"),
                    validity_start=date(2026, 5, 1),
                    relation=RuleRelation.ALTERED,
                    clause="Cláusula 1ª - Reajuste Contratual",
                    paragraph="1",
                    literal_excerpt=(
                        "Pelo 1º Termo Aditivo ao contrato CT-2026-001, o valor mensal passa de "
                        "R$ 10.000,00 para R$ 10.800,00 a partir de 01/05/2026."
                    ),
                ),
            )
        ),
        decisions_builder=lambda rules: (
            RuleReviewInput(
                rule_id=rules[0].id,
                user="auditora.maria",
                decision=ReviewDecision.CONFIRM,
                justification="Aditivo 01 em DOCX conferido por cláusula e parágrafo.",
                reviewed_at=_REVIEW_TS,
            ),
        ),
        thread_id="e2e-ct1-add01",
    )
    assert add01_state.extracted_rules[0].evidence.without_page is True

    add02_state, _ = _run_contract_flow_to_completion(
        contract=contract,
        document=doc_add02,
        jurista_input=JuristaInput.from_pdf(
            doc_add02.id,
            contract.id,
            add02_pdf,
            file_hash=doc_add02.file_hash,
            is_amendment=True,
        ),
        extraction_payload=JuristaExtractionPayload(
            rules=(
                ExtractedRuleCandidate(
                    rule_type=RuleType.MONTHLY_PRICE,
                    new_value=Decimal("10500.00"),
                    previous_value=Decimal("10800.00"),
                    validity_start=date(2026, 9, 1),
                    relation=RuleRelation.ALTERED,
                    page=2,
                    literal_excerpt=(
                        "Cláusula 2ª: Passa de R$ 10.800,00 para R$ 10.500,00 a partir de "
                        "01/09/2026."
                    ),
                ),
            )
        ),
        decisions_builder=lambda rules: (
            RuleReviewInput(
                rule_id=rules[0].id,
                user="auditora.maria",
                decision=ReviewDecision.CONFIRM,
                justification="Aditivo 02 conferido na página 2.",
                reviewed_at=_REVIEW_TS,
            ),
        ),
        thread_id="e2e-ct1-add02",
    )

    amendment_01 = Amendment(
        id="add-ct1-01",
        affected_rule_id=base_state.extracted_rules[0].id,
        new_rule=add01_state.extracted_rules[0].model_copy(
            update={"validity": ValidityPeriod(start=date(2026, 5, 1), end=None), "version": 2}
        ),
        source_document_id=doc_add01.id,
        effective_date=date(2026, 5, 1),
        relation=RuleRelation.ALTERED,
    )
    amendment_02 = Amendment(
        id="add-ct1-02",
        affected_rule_id=add01_state.extracted_rules[0].id,
        new_rule=add02_state.extracted_rules[0].model_copy(
            update={"validity": ValidityPeriod(start=date(2026, 9, 1), end=None), "version": 3}
        ),
        source_document_id=doc_add02.id,
        effective_date=date(2026, 9, 1),
        relation=RuleRelation.ALTERED,
    )
    timeline = apply_amendments(
        base_state.consolidated_rules,
        (amendment_01, amendment_02),
    )
    assert timeline.complete is True

    with connect(db_path) as conn:
        save_document(conn, base_state.document)
        save_document(conn, add01_state.document)
        save_document(conn, add02_state.document)
        for rule in base_state.consolidated_rules:
            save_rule(conn, rule)
        save_amendment(conn, amendment_01)
        save_amendment(conn, amendment_02)
        for rule in timeline.rules:
            save_rule(conn, rule)
        for review in (
            *base_state.human_reviews,
            *add01_state.human_reviews,
            *add02_state.human_reviews,
        ):
            append_review(conn, review)

    return contract, (
        base_state.document.file_hash,
        add01_state.document.file_hash,
        add02_state.document.file_hash,
    )


def test_synthetic_dataset_files_exist_and_parse_cleanly() -> None:
    expected = build_synthetic_dataset_bytes()
    assert len(expected) == 13
    for file_name in expected:
        assert (SYNTHETIC_DIR / file_name).is_file()

    assert read_pdf(SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf").read_quality is ReadQuality.OK
    assert read_docx(SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx").without_page is True
    assert read_pdf(SYNTHETIC_DIR / "ct_2026_001_aditivo_02.pdf").read_quality is ReadQuality.OK
    assert parse_nfe(SYNTHETIC_DIR / "nfe_101_caso_b_regular.xml").number == "101"
    assert parse_nfe(SYNTHETIC_DIR / "nfe_104_caso_a_divergencia.xml").number == "104"
    nfse_109 = parse_nfse(SYNTHETIC_DIR / "nfse_109_competencia_inferida.xml")
    assert nfse_109.number == "109"
    assert nfse_109.competence_inferred is True
    assert len(import_invoices_csv(SYNTHETIC_DIR / "cobrancas_lote_ct_2026_001.csv")) == 5
    fatura_110_pdf = read_pdf(SYNTHETIC_DIR / "fatura_110_pdf_item_ambiguo.pdf")
    ct2_docx = read_docx(SYNTHETIC_DIR / "ct_2026_002_contrato_armadilhas.docx")
    ct2_add_pdf = read_pdf(SYNTHETIC_DIR / "ct_2026_002_aditivo_meio_periodo.pdf")
    assert fatura_110_pdf.read_quality is ReadQuality.OK
    assert ct2_docx.read_quality is ReadQuality.OK
    assert ct2_add_pdf.read_quality is ReadQuality.OK
    assert parse_nfe(SYNTHETIC_DIR / "nfe_201_meio_periodo.xml").number == "201"
    assert parse_nfe(SYNTHETIC_DIR / "nfe_202_sem_contrato.xml").contract_id is None
    assert (
        read_pdf(SYNTHETIC_DIR / "contrato_escaneado_sem_texto.pdf").read_quality
        is ReadQuality.NO_TEXT
    )


def test_e2e_ct_2026_001_full_lifecycle_pdf_docx_sqlite_reviews_and_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "farol.db"
    _setup_ct_2026_001_in_sqlite(db_path)

    base_bytes = (SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf").read_bytes()
    with connect(db_path) as conn:
        contract = get_contract(conn, "CT-2026-001")
        assert contract is not None
        loaded_docs = list_documents(conn, "CT-2026-001")
        loaded_rules = list_rules(conn, "CT-2026-001")
        loaded_amendments = list_amendments(conn)
        reviews = list_reviews(conn)

        doc_base = next(doc for doc in loaded_docs if doc.id == "doc-ct1-base")
        assert verify_document_integrity(doc_base, base_bytes) is True
        assert verify_document_integrity(doc_base, base_bytes + b"x") is False
        assert len(loaded_amendments) == 2
        assert len(reviews) == 7

    # 1. Caso B regular (2026-04) vindo de NF-e XML
    inv_101 = parse_nfe(SYNTHETIC_DIR / "nfe_101_caso_b_regular.xml", invoice_id="inv-101")
    out_101, _, _ = _run_invoice_flow(
        invoice=inv_101,
        contract=contract,
        rules=loaded_rules,
        documents=loaded_docs,
        tradutor_links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="doc-ct1-base-rule-1",
                item_excerpt="Mensalidade de suporte",
                rule_excerpt=(
                    "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 até 31/12/2026."
                ),
                similarity_score=Decimal("0.98"),
            ),
        ),
        thread_id="e2e-inv-101",
    )
    assert out_101.audit_result is not None
    assert out_101.audit_result.classification is AuditClassification.NO_DIVERGENCE

    # 2. Caso A divergência (2026-10) + relatório com número alucinado rejeitado (R-01)
    inv_104 = parse_nfe(SYNTHETIC_DIR / "nfe_104_caso_a_divergencia.xml", invoice_id="inv-104")
    hallucinating_report = FakeLLMClient(
        [
            ReportExplanationPayload(
                summary="Divergência em 10/2026 com multa extra de R$ 999,00 aplicada."
            )
        ]
    )
    out_104, _, _ = _run_invoice_flow(
        invoice=inv_104,
        contract=contract,
        rules=loaded_rules,
        documents=loaded_docs,
        tradutor_links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="doc-ct1-add02-rule-1",
                item_excerpt="Sustentação técnica continuada mensal",
                rule_excerpt=(
                    "Cláusula 2ª: Passa de R$ 10.800,00 para R$ 10.500,00 a partir de 01/09/2026."
                ),
                similarity_score=Decimal("0.93"),
            ),
        ),
        report_client=hallucinating_report,
        thread_id="e2e-inv-104",
    )
    assert out_104.audit_result is not None
    assert out_104.audit_result.classification is AuditClassification.DIVERGENCE
    assert out_104.audit_result.difference == Decimal("300.00")
    assert out_104.report is not None
    assert out_104.report.rejected_llm_text is True
    assert out_104.report.used_llm_text is False
    assert Decimal("999.00") in out_104.report.unauthorized_numbers
    assert out_104.report.final_text == out_104.report.deterministic_text

    # 3. NFS-e sem competência explícita -> competência inferida (M-14)
    inv_109 = parse_nfse(SYNTHETIC_DIR / "nfse_109_competencia_inferida.xml", invoice_id="inv-109")
    assert inv_109.competence_inferred is True
    out_109, _, _ = _run_invoice_flow(
        invoice=inv_109,
        contract=contract,
        rules=loaded_rules,
        documents=loaded_docs,
        tradutor_links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="doc-ct1-add01-rule-1",
                item_excerpt="Mensalidade de suporte referente ao Contrato CT-2026-001",
                rule_excerpt=(
                    "Pelo 1º Termo Aditivo ao contrato CT-2026-001, o valor mensal passa de "
                    "R$ 10.000,00 para R$ 10.800,00 a partir de 01/05/2026."
                ),
                similarity_score=Decimal("0.96"),
            ),
        ),
        thread_id="e2e-inv-109",
    )
    assert out_109.audit_result is not None
    assert out_109.audit_result.classification is AuditClassification.NEEDS_REVIEW

    # 4. Salvar auditorias e comprovar snapshot imutável após alteração de regra no banco (I-06)
    with connect(db_path) as conn:
        save_invoice(conn, inv_101)
        save_invoice(conn, inv_104)
        save_invoice(conn, inv_109)
        save_audit(conn, out_101.audit_result)
        save_audit(conn, out_104.audit_result)

        rule_add02 = next(r for r in loaded_rules if r.id == "doc-ct1-add02-rule-1")
        save_rule(conn, rule_add02.model_copy(update={"value": Decimal("10900.00")}))

        stored_104 = get_audit(conn, out_104.audit_result.id)
        assert stored_104 is not None
        assert stored_104.result.difference == Decimal("300.00")
        snapshot_add02 = next(
            r for r in stored_104.rules_snapshot if r.id == "doc-ct1-add02-rule-1"
        )
        assert snapshot_add02.value == Decimal("10500.00")


def test_e2e_csv_batch_overage_duplicates_out_of_validity_and_missing_measurement(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "lote.db"
    _setup_ct_2026_001_in_sqlite(db_path)
    batch_invoices = import_invoices_csv(SYNTHETIC_DIR / "cobrancas_lote_ct_2026_001.csv")

    with connect(db_path) as conn:
        contract = get_contract(conn, "CT-2026-001")
        loaded_rules = list_rules(conn, "CT-2026-001")
        loaded_docs = list_documents(conn, "CT-2026-001")
        for inv in batch_invoices:
            save_invoice(conn, inv)
        saved_invoices = list_invoices(conn, "CT-2026-001")

    results_by_number: dict[str, AuditClassification] = {}
    checks_by_number: dict[str, set[str]] = {}
    diff_by_number: dict[str, Decimal | None] = {}

    rule_add01 = next(r for r in loaded_rules if r.id == "doc-ct1-add01-rule-1")
    rule_add02 = next(r for r in loaded_rules if r.id == "doc-ct1-add02-rule-1")
    rule_overage = next(r for r in loaded_rules if r.id == "doc-ct1-base-rule-3")

    for inv in saved_invoices:
        others = tuple(other for other in saved_invoices if other.id != inv.id)
        if inv.number == "103":
            links = (
                ItemRuleLinkCandidate(
                    item_index=0,
                    rule_id=rule_add01.id,
                    item_excerpt="Mensalidade de suporte",
                    rule_excerpt=rule_add01.evidence.literal_excerpt,
                    similarity_score=Decimal("0.97"),
                ),
                ItemRuleLinkCandidate(
                    item_index=1,
                    rule_id=rule_overage.id,
                    item_excerpt="Horas excedentes de suporte",
                    rule_excerpt=rule_overage.evidence.literal_excerpt,
                    similarity_score=Decimal("0.96"),
                ),
            )
        elif inv.number in ("105", "106"):
            links = (
                ItemRuleLinkCandidate(
                    item_index=0,
                    rule_id=rule_add01.id,
                    item_excerpt="Mensalidade de suporte",
                    rule_excerpt=rule_add01.evidence.literal_excerpt,
                    similarity_score=Decimal("0.97"),
                ),
            )
        elif inv.number == "107":
            links = (
                ItemRuleLinkCandidate(
                    item_index=0,
                    rule_id=rule_add02.id,
                    item_excerpt="Mensalidade de suporte",
                    rule_excerpt=rule_add02.evidence.literal_excerpt,
                    similarity_score=Decimal("0.97"),
                ),
            )
        elif inv.number == "108":
            links = (
                ItemRuleLinkCandidate(
                    item_index=0,
                    rule_id=rule_add02.id,
                    item_excerpt="Mensalidade de suporte",
                    rule_excerpt=rule_add02.evidence.literal_excerpt,
                    similarity_score=Decimal("0.97"),
                ),
                ItemRuleLinkCandidate(
                    item_index=1,
                    rule_id=rule_overage.id,
                    item_excerpt="Horas excedentes de suporte",
                    rule_excerpt=rule_overage.evidence.literal_excerpt,
                    similarity_score=Decimal("0.96"),
                ),
            )
        else:
            links = ()

        state, _, _ = _run_invoice_flow(
            invoice=inv,
            contract=contract,
            rules=loaded_rules,
            documents=loaded_docs,
            other_invoices=others,
            tradutor_links=links,
            thread_id=f"e2e-csv-{inv.number}",
        )
        assert state.audit_result is not None
        results_by_number[inv.number] = state.audit_result.classification
        checks_by_number[inv.number] = {c.name for c in state.audit_result.checks}
        diff_by_number[inv.number] = state.audit_result.difference

    assert results_by_number["103"] is AuditClassification.DIVERGENCE
    assert diff_by_number["103"] == Decimal("300.00")
    assert results_by_number["105"] is AuditClassification.NEEDS_REVIEW
    assert "duplicate_invoice" in checks_by_number["105"]
    assert results_by_number["106"] is AuditClassification.NEEDS_REVIEW
    assert "duplicate_invoice" in checks_by_number["106"]
    assert results_by_number["107"] is AuditClassification.NEEDS_REVIEW
    assert "contract_validity" in checks_by_number["107"]
    assert results_by_number["108"] is AuditClassification.NEEDS_REVIEW
    assert "overage_quantity" in checks_by_number["108"]


def test_e2e_fiscal_pdf_ambiguous_item_tradutor_pause_and_motor_defense_in_depth(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fiscal.db"
    _setup_ct_2026_001_in_sqlite(db_path)

    with connect(db_path) as conn:
        contract = get_contract(conn, "CT-2026-001")
        loaded_rules = list_rules(conn, "CT-2026-001")
        loaded_docs = list_documents(conn, "CT-2026-001")

    pdf_inv = read_pdf(SYNTHETIC_DIR / "fatura_110_pdf_item_ambiguo.pdf")
    fiscal_client = FakeLLMClient(
        [
            FiscalExtractionPayload(
                contract_id="CT-2026-001",
                supplier="Fornecedor Exemplo Ltda.",
                supplier_cnpj="11.222.333/0001-81",
                number="110",
                issue_date=date(2026, 9, 28),
                reference_period="2026-09",
                total=Decimal("11000.00"),
                page=1,
                literal_excerpt="FATURA DE SERVICOS N. 110 - Contrato CT-2026-001",
                items=(
                    ExtractedBillingItemCandidate(
                        description="Mensalidade de suporte",
                        quantity=Decimal("1"),
                        unit="mes",
                        unit_value=Decimal("10500.00"),
                        total_value=Decimal("10500.00"),
                        literal_excerpt="Item 1: Mensalidade de suporte (1 mes) - R$ 10.500,00",
                    ),
                    ExtractedBillingItemCandidate(
                        description="Servico adicional de plantao",
                        quantity=Decimal("1"),
                        unit="un",
                        unit_value=Decimal("500.00"),
                        total_value=Decimal("500.00"),
                        literal_excerpt="Item 2: Servico adicional de plantao (1 un) - R$ 500,00",
                    ),
                ),
            )
        ]
    )
    fiscal_res = run_fiscal_pdf_agent(
        FiscalPdfInput.from_pdf("doc-inv-110", pdf_inv),
        fiscal_client,
    )
    assert fiscal_res.invoice is not None
    assert len(fiscal_res.invoice.items) == 2

    rule_add02 = next(r for r in loaded_rules if r.id == "doc-ct1-add02-rule-1")
    rule_overage = next(r for r in loaded_rules if r.id == "doc-ct1-base-rule-3")
    ambiguous_links = (
        ItemRuleLinkCandidate(
            item_index=0,
            rule_id=rule_add02.id,
            item_excerpt="Mensalidade de suporte",
            rule_excerpt=rule_add02.evidence.literal_excerpt,
            similarity_score=Decimal("0.96"),
        ),
        ItemRuleLinkCandidate(
            item_index=1,
            rule_id=rule_overage.id,
            item_excerpt="Servico adicional de plantao",
            rule_excerpt=rule_overage.evidence.literal_excerpt,
            similarity_score=Decimal("0.72"),
            ambiguous=True,
        ),
    )

    paused_1, graph_1, cfg_1 = _run_invoice_flow(
        invoice=fiscal_res.invoice,
        contract=contract,
        rules=loaded_rules,
        documents=loaded_docs,
        tradutor_links=ambiguous_links,
        thread_id="e2e-inv-110-reject",
    )
    assert paused_1.awaiting_link_review is True
    assert paused_1.audit_result is None

    resumed_unapproved = load_invoice_state(graph_1.invoke(None, config=cfg_1))
    assert resumed_unapproved.audit_result is not None
    assert resumed_unapproved.audit_result.classification is AuditClassification.NEEDS_REVIEW
    assert [c.name for c in resumed_unapproved.audit_result.checks] == ["semantic_item_link"]

    from backend.agentes import dump_invoice_state

    paused_2, graph_2, cfg_2 = _run_invoice_flow(
        invoice=fiscal_res.invoice,
        contract=contract,
        rules=loaded_rules,
        documents=loaded_docs,
        tradutor_links=ambiguous_links,
        thread_id="e2e-inv-110-approve",
    )
    graph_2.update_state(
        cfg_2,
        dump_invoice_state(paused_2.model_copy(update={"links_approved_by_human": True})),
    )
    resumed_approved = load_invoice_state(graph_2.invoke(None, config=cfg_2))
    assert resumed_approved.audit_result is not None
    assert resumed_approved.audit_result.classification is AuditClassification.NEEDS_REVIEW
    assert "item_classification" in {c.name for c in resumed_approved.audit_result.checks}


def test_e2e_ct_2026_002_traps_unreviewed_amendment_mid_period_and_orphan(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "armadilhas.db"
    init_db(db_path)

    # 1. PDF sem camada de texto -> não processa e permanece não revisado (A-07)
    scanned_pdf = read_pdf(SYNTHETIC_DIR / "contrato_escaneado_sem_texto.pdf")
    assert scanned_pdf.read_quality is ReadQuality.NO_TEXT
    ct2 = Contract.model_validate(
        {
            "id": "CT-2026-002",
            "company": "Empresa Exemplo S.A.",
            "supplier": "InfraCloud Brasil S.A.",
            "supplier_cnpj": "33.000.167/0001-01",
            "subject": "Serviços de nuvem gerenciada",
            "validity": {"start": "2026-01-01", "end": "2026-06-30"},
            "comparison_tolerance": "0.00",
            "status": "ACTIVE",
        }
    )
    doc_scanned = receive_document(
        document_id="doc-scanned",
        contract_id=ct2.id,
        document_type=DocumentType.CONTRACT,
        file_name="contrato_escaneado_sem_texto.pdf",
        content=SYNTHETIC_DIR / "contrato_escaneado_sem_texto.pdf",
        received_at=_REVIEW_TS,
        read_quality=scanned_pdf.read_quality,
        extracted_text=scanned_pdf.full_text or "sem texto",
        reviewed=False,
    )
    scanned_client = FakeLLMClient([])
    scanned_graph = build_contract_graph(scanned_client, _consolidator, checkpointer=MemorySaver())
    from backend.agentes import dump_contract_state

    scanned_paused = load_contract_state(
        scanned_graph.invoke(
            dump_contract_state(
                ContractWorkflowState(
                    contract=ct2,
                    document=doc_scanned,
                    jurista_input=JuristaInput.from_pdf(doc_scanned.id, ct2.id, scanned_pdf),
                )
            ),
            config={"configurable": {"thread_id": "e2e-scanned"}},
        )
    )
    assert scanned_paused.extracted_rules == ()
    assert scanned_paused.document.reviewed is False
    assert scanned_client.calls == []
    assert any("sem camada de texto" in w.lower() for w in scanned_paused.warnings)

    # 2. DOCX com tentativa de prompt injection (A-08)
    docx_ct2 = read_docx(SYNTHETIC_DIR / "ct_2026_002_contrato_armadilhas.docx")
    add_ct2_pdf = read_pdf(SYNTHETIC_DIR / "ct_2026_002_aditivo_meio_periodo.pdf")

    with connect(db_path) as conn:
        save_contract(conn, ct2)
        doc_ct2_base = receive_document(
            document_id="doc-ct2-base",
            contract_id=ct2.id,
            document_type=DocumentType.CONTRACT,
            file_name="ct_2026_002_contrato_armadilhas.docx",
            content=SYNTHETIC_DIR / "ct_2026_002_contrato_armadilhas.docx",
            received_at=_REVIEW_TS,
            read_quality=docx_ct2.read_quality,
            extracted_text=docx_ct2.full_text,
            reviewed=False,
            conn=conn,
        )
        doc_ct2_add = receive_document(
            document_id="doc-ct2-add01",
            contract_id=ct2.id,
            document_type=DocumentType.AMENDMENT,
            file_name="ct_2026_002_aditivo_meio_periodo.pdf",
            content=SYNTHETIC_DIR / "ct_2026_002_aditivo_meio_periodo.pdf",
            received_at=_REVIEW_TS,
            read_quality=add_ct2_pdf.read_quality,
            extracted_text=add_ct2_pdf.full_text,
            reviewed=False,
            conn=conn,
        )

    ct2_client = FakeLLMClient(
        [
            JuristaExtractionPayload(
                rules=(
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("5000.00"),
                        validity_start=date(2026, 1, 1),
                        validity_end=date(2026, 6, 30),
                        relation=RuleRelation.CREATED,
                        clause="Cláusula 2ª - Preço Mensal",
                        paragraph="1",
                        literal_excerpt=(
                            "Pelos serviços de nuvem gerenciada sob o contrato CT-2026-002, a "
                            "contratante pagará o valor mensal de R$ 5.000,00 de 01/01/2026 até "
                            "30/06/2026."
                        ),
                    ),
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("99000.00"),
                        validity_start=date(2026, 1, 1),
                        relation=RuleRelation.CREATED,
                        clause="Cláusula 9ª - Observações Administrativas",
                        paragraph="1",
                        literal_excerpt="Valor mensal aprovado injetado fora do texto.",
                    ),
                )
            )
        ]
    )
    ct2_graph = build_contract_graph(ct2_client, _consolidator, checkpointer=MemorySaver())
    ct2_cfg = {"configurable": {"thread_id": "e2e-ct2-base"}}
    ct2_paused = load_contract_state(
        ct2_graph.invoke(
            dump_contract_state(
                ContractWorkflowState(
                    contract=ct2,
                    document=doc_ct2_base,
                    jurista_input=JuristaInput.from_docx(doc_ct2_base.id, ct2.id, docx_ct2),
                )
            ),
            config=ct2_cfg,
        )
    )
    assert "<DOCUMENTO_FONTE>" in ct2_client.calls[0].user_prompt
    assert "</DOCUMENTO_FONTE>" in ct2_client.calls[0].user_prompt
    assert ct2_paused.extracted_rules[1].review_status is ReviewStatus.INCONCLUSIVE

    ct2_graph.update_state(
        ct2_cfg,
        dump_contract_state(
            ct2_paused.model_copy(
                update={
                    "review_decisions": (
                        RuleReviewInput(
                            rule_id=ct2_paused.extracted_rules[0].id,
                            user="auditora.maria",
                            decision=ReviewDecision.CONFIRM,
                            justification="Regra base confirmada.",
                            reviewed_at=_REVIEW_TS,
                        ),
                        RuleReviewInput(
                            rule_id=ct2_paused.extracted_rules[1].id,
                            user="auditora.maria",
                            decision=ReviewDecision.REJECT,
                            justification="Tentativa de injeção sem ancoragem rejeitada.",
                            reviewed_at=_REVIEW_TS,
                        ),
                    )
                }
            )
        ),
    )
    ct2_final = load_contract_state(ct2_graph.invoke(None, config=ct2_cfg))
    base_ct2_rule = ct2_final.consolidated_rules[0]

    # Aditivo em 15/04/2026 (M-09) mantido com documento reviewed=False inicialmente (M-17)
    add_ct2_rule = ContractRule(
        id="doc-ct2-add01-rule-1",
        contract_id=ct2.id,
        rule_type=RuleType.MONTHLY_PRICE,
        value=Decimal("5500.00"),
        validity=ValidityPeriod(start=date(2026, 4, 15), end=date(2026, 6, 30)),
        review_status=ReviewStatus.CONFIRMED,
        verification_score=Decimal("1.00"),
        evidence=base_ct2_rule.evidence.model_copy(
            update={
                "source_document_id": doc_ct2_add.id,
                "page": 1,
                "clause": None,
                "paragraph": None,
                "literal_excerpt": (
                    "Cláusula 1ª: Passa de R$ 5.000,00 para R$ 5.500,00 a partir de 15/04/2026."
                ),
            }
        ),
        relation=RuleRelation.ALTERED,
    )
    amendment_ct2 = Amendment(
        id="add-ct2-01",
        affected_rule_id=base_ct2_rule.id,
        new_rule=add_ct2_rule,
        source_document_id=doc_ct2_add.id,
        effective_date=date(2026, 4, 15),
        relation=RuleRelation.ALTERED,
    )

    with connect(db_path) as conn:
        save_document(conn, ct2_final.document)
        save_rule(conn, base_ct2_rule)
        save_amendment(conn, amendment_ct2)

        loaded_ct2 = get_contract(conn, ct2.id)
        all_ct2_rules = list_rules(conn, ct2.id)
        base_only_rules = tuple(r for r in all_ct2_rules if r.id != add_ct2_rule.id)
        loaded_amendments = list_amendments(conn, source_document_id=doc_ct2_add.id)
        docs_unreviewed = list_documents(conn, ct2.id)

    # Antes de revisar o aditivo de abr/2026: março segue regular e maio exige revisão (M-17)
    from tests.fixtures.scenario import make_item

    inv_mar = Invoice(
        id="inv-ct2-mar",
        contract_id=ct2.id,
        supplier="InfraCloud Brasil S.A.",
        supplier_cnpj="33.000.167/0001-01",
        number="200",
        issue_date=date(2026, 3, 30),
        reference_period="2026-03",
        total=Decimal("5000.00"),
        source="NFE_XML",
        items=(make_item("Servicos de nuvem gerenciada", "1", "5000.00", "5000.00", "mes"),),
    )
    mar_state, _, _ = _run_invoice_flow(
        invoice=inv_mar,
        contract=loaded_ct2,
        rules=base_only_rules,
        amendments=loaded_amendments,
        documents=docs_unreviewed,
        tradutor_links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id=base_ct2_rule.id,
                item_excerpt="Servicos de nuvem gerenciada",
                rule_excerpt=base_ct2_rule.evidence.literal_excerpt,
                similarity_score=Decimal("0.97"),
            ),
        ),
        thread_id="e2e-ct2-mar",
    )
    assert mar_state.audit_result is not None
    assert mar_state.audit_result.classification is AuditClassification.NO_DIVERGENCE

    inv_mai = inv_mar.model_copy(
        update={
            "id": "inv-ct2-mai",
            "number": "203",
            "issue_date": date(2026, 5, 30),
            "reference_period": "2026-05",
            "total": Decimal("5500.00"),
            "items": (make_item("Servicos de nuvem gerenciada", "1", "5500.00", "5500.00", "mes"),),
        }
    )
    mai_pending, _, _ = _run_invoice_flow(
        invoice=inv_mai,
        contract=loaded_ct2,
        rules=base_only_rules,
        amendments=loaded_amendments,
        documents=docs_unreviewed,
        tradutor_links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id=base_ct2_rule.id,
                item_excerpt="Servicos de nuvem gerenciada",
                rule_excerpt=base_ct2_rule.evidence.literal_excerpt,
                similarity_score=Decimal("0.97"),
            ),
        ),
        thread_id="e2e-ct2-mai-pending",
    )
    assert mai_pending.audit_result is not None
    assert mai_pending.audit_result.classification is AuditClassification.NEEDS_REVIEW

    # Após revisão do documento do aditivo: abr/2026 acusa mudança no meio do período (M-09)
    with connect(db_path) as conn:
        record_document_review(
            conn,
            doc_ct2_add.id,
            user="auditora.maria",
            decision=ReviewDecision.CONFIRM,
            justification="Documento do aditivo conferido.",
            reviewed_at=_REVIEW_TS,
        )
        docs_reviewed = list_documents(conn, ct2.id)

    inv_201 = parse_nfe(SYNTHETIC_DIR / "nfe_201_meio_periodo.xml", invoice_id="inv-201")
    abr_state, _, _ = _run_invoice_flow(
        invoice=inv_201,
        contract=loaded_ct2,
        rules=base_only_rules,
        amendments=loaded_amendments,
        documents=docs_reviewed,
        tradutor_links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id=base_ct2_rule.id,
                item_excerpt="Servicos de nuvem gerenciada",
                rule_excerpt=base_ct2_rule.evidence.literal_excerpt,
                similarity_score=Decimal("0.97"),
            ),
        ),
        thread_id="e2e-ct2-abr-mid",
    )
    assert abr_state.audit_result is not None
    assert abr_state.audit_result.classification is AuditClassification.NEEDS_REVIEW
    monthly_check = next(c for c in abr_state.audit_result.checks if c.name == "monthly_price")
    assert monthly_check.result.value == "REVIEW"

    mai_ok, _, _ = _run_invoice_flow(
        invoice=inv_mai,
        contract=loaded_ct2,
        rules=base_only_rules,
        amendments=loaded_amendments,
        documents=docs_reviewed,
        tradutor_links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id=base_ct2_rule.id,
                item_excerpt="Servicos de nuvem gerenciada",
                rule_excerpt=base_ct2_rule.evidence.literal_excerpt,
                similarity_score=Decimal("0.97"),
            ),
        ),
        thread_id="e2e-ct2-mai-ok",
    )
    assert mai_ok.audit_result is not None
    assert mai_ok.audit_result.classification is AuditClassification.NO_DIVERGENCE

    # 3. NF-e sem contrato vinculado -> MISSING_DOCUMENTATION (M-15)
    inv_202 = parse_nfe(SYNTHETIC_DIR / "nfe_202_sem_contrato.xml", invoice_id="inv-202")
    orphan_state, _, _ = _run_invoice_flow(
        invoice=inv_202,
        contract=None,
        rules=(),
        thread_id="e2e-orphan",
    )
    assert orphan_state.audit_result is not None
    assert orphan_state.audit_result.classification is AuditClassification.MISSING_DOCUMENTATION
