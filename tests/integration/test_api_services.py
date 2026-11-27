"""Testes de integração da camada de serviços (`backend/api/services.py`)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from backend.agentes import (
    ExtractedBillingItemCandidate,
    ExtractedInvoiceEntry,
    ExtractedRuleCandidate,
    FakeLLMClient,
    FiscalExtractionPayload,
    ItemRuleLinkCandidate,
    JuristaExtractionPayload,
    ReportExplanationPayload,
    TradutorExtractionPayload,
)
from backend.api import (
    assign_invoice_contract,
    audit_stored_invoice,
    delete_all_uploaded_documents,
    delete_empty_contract,
    delete_uploaded_document,
    fetch_amendments,
    fetch_audits,
    fetch_contract,
    fetch_contracts,
    fetch_documents,
    fetch_invoices,
    fetch_reviews,
    fetch_rules,
    get_contract_timeline,
    get_rules_for_competence,
    import_billing_document,
    ingest_uploaded_files,
    mark_amendment_reviewed,
    process_contract_document,
    record_audit_decision,
    register_amendment,
    register_contract,
    register_human_decision,
    reset_all_data,
    resolve_contract_link,
    review_document,
    review_rule,
    seed_synthetic_demo,
)
from backend.leitura import CPF_MASK, RG_MASK
from backend.motor import SelectionStatus
from backend.persistencia import connect, init_db
from backend.schemas import (
    Amendment,
    AuditClassification,
    Contract,
    DocumentType,
    ReadQuality,
    ReferencePeriod,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from tests.fixtures.synthetic_files import SYNTHETIC_DIR, build_synthetic_docx, build_synthetic_pdf

_TS = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)


def _contract(
    contract_id: str = "CT-2026-001",
    *,
    cnpj: str = "11.222.333/0001-81",
) -> Contract:
    return Contract.model_validate(
        {
            "id": contract_id,
            "company": "Empresa Exemplo S.A.",
            "supplier": "Fornecedor Exemplo Ltda.",
            "supplier_cnpj": cnpj,
            "subject": "Serviços de suporte técnico",
            "validity": {"start": "2026-01-01", "end": "2026-12-31"},
            "comparison_tolerance": "0.00",
            "status": "ACTIVE",
        }
    )


def _nfe_xml_without_contract(*, number: str = "301", cnpj: str = "11222333000181") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">\n'
        "  <NFe>\n"
        f'    <infNFe Id="NFe-{number}" versao="4.00">\n'
        f"      <ide><nNF>{number}</nNF><dhEmi>2026-04-25T10:00:00-03:00</dhEmi></ide>\n"
        f"      <emit><CNPJ>{cnpj}</CNPJ><xNome>Fornecedor Exemplo Ltda.</xNome></emit>\n"
        '      <det nItem="1"><prod>\n'
        "        <xProd>Mensalidade de suporte</xProd><qCom>1</qCom><uCom>mes</uCom>\n"
        "        <vUnCom>10000.00</vUnCom><vProd>10000.00</vProd>\n"
        "      </prod></det>\n"
        "      <total><ICMSTot><vNF>10000.00</vNF></ICMSTot></total>\n"
        "      <infAdic><infCpl>Competencia 2026-04 sem codigo de contrato</infCpl></infAdic>\n"
        "    </infNFe>\n"
        "  </NFe>\n"
        "</nfeProc>\n"
    ).encode()


def _nfe_xml_with_unknown_contract() -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">\n'
        b"  <NFe>\n"
        b'    <infNFe Id="NFe-302" versao="4.00">\n'
        b"      <ide><nNF>302</nNF><dhEmi>2026-04-25T10:00:00-03:00</dhEmi></ide>\n"
        b"      <emit><CNPJ>11222333000181</CNPJ><xNome>Fornecedor Exemplo Ltda.</xNome></emit>\n"
        b'      <det nItem="1"><prod>\n'
        b"        <xProd>Mensalidade de suporte</xProd><qCom>1</qCom><uCom>mes</uCom>\n"
        b"        <vUnCom>10000.00</vUnCom><vProd>10000.00</vProd>\n"
        b"      </prod></det>\n"
        b"      <total><ICMSTot><vNF>10000.00</vNF></ICMSTot></total>\n"
        b"      <infAdic><infCpl>Contrato CT-2026-999 competencia 2026-04</infCpl></infAdic>\n"
        b"    </infNFe>\n"
        b"  </NFe>\n"
        b"</nfeProc>\n"
    )


def test_register_contract_and_process_pdf_docx_and_no_text_documents(tmp_path: Path) -> None:
    db_path = tmp_path / "services.db"
    init_db(db_path)

    with connect(db_path) as conn:
        created = register_contract(conn, _contract())
        assert fetch_contract(conn, created.id) == created
        assert fetch_contracts(conn) == (created,)

        base_client = FakeLLMClient(
            [
                JuristaExtractionPayload(
                    rules=(
                        ExtractedRuleCandidate(
                            rule_type=RuleType.MONTHLY_PRICE,
                            new_value=Decimal("10000.00"),
                            validity_start=date(2026, 1, 1),
                            validity_end=date(2026, 12, 31),
                            relation=RuleRelation.CREATED,
                            page=2,
                            literal_excerpt=(
                                "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 até "
                                "31/12/2026."
                            ),
                        ),
                    )
                )
            ]
        )
        base_res = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-base",
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            client=base_client,
            document_type=DocumentType.CONTRACT,
            received_at=_TS,
        )
        assert base_res.schema_valid is True
        assert len(base_res.document.file_hash) == 64
        assert base_res.document.reviewed is False
        assert len(base_res.rules) == 1
        assert base_res.rules[0].review_status is ReviewStatus.PENDING_REVIEW
        assert CPF_MASK in base_client.calls[0].user_prompt
        assert RG_MASK in base_client.calls[0].user_prompt

        add_client = FakeLLMClient(
            [
                JuristaExtractionPayload(
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
                                "Pelo 1º Termo Aditivo ao contrato CT-2026-001, o valor mensal "
                                "passa de R$ 10.000,00 para R$ 10.800,00 a partir de 01/05/2026."
                            ),
                        ),
                    )
                )
            ]
        )
        add_res = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-add01",
            file_name="ct_2026_001_aditivo_01.docx",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx",
            client=add_client,
            document_type=DocumentType.AMENDMENT,
            received_at=_TS,
        )
        assert add_res.rules[0].evidence.without_page is True
        assert len(fetch_rules(conn, "CT-2026-001", ReviewStatus.PENDING_REVIEW)) == 2

        scanned_client = FakeLLMClient([])
        scanned_res = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-scanned",
            file_name="contrato_escaneado_sem_texto.pdf",
            content=SYNTHETIC_DIR / "contrato_escaneado_sem_texto.pdf",
            client=scanned_client,
            document_type=DocumentType.CONTRACT,
            received_at=_TS,
        )
        assert scanned_res.document.read_quality is ReadQuality.NO_TEXT
        assert scanned_res.rules == ()
        assert scanned_client.calls == []
        assert any("sem camada de texto" in w.lower() for w in scanned_res.warnings)

        with pytest.raises(ValueError, match="não encontrado"):
            process_contract_document(
                conn,
                contract_id="CT-DESCONHECIDO",
                document_id="doc-err-1",
                file_name="ct.pdf",
                content=b"%PDF-1.4",
                client=scanned_client,
            )
        with pytest.raises(ValueError, match="CONTRACT ou AMENDMENT"):
            process_contract_document(
                conn,
                contract_id="CT-2026-001",
                document_id="doc-err-2",
                file_name="ct.pdf",
                content=b"%PDF-1.4",
                client=scanned_client,
                document_type=DocumentType.INVOICE,
            )
        with pytest.raises(ValueError, match="formato não suportado"):
            process_contract_document(
                conn,
                contract_id="CT-2026-001",
                document_id="doc-err-3",
                file_name="contrato.txt",
                content=b"texto",
                client=scanned_client,
            )


def test_review_rule_decisions_and_mark_amendment_reviewed(tmp_path: Path) -> None:
    db_path = tmp_path / "reviews.db"
    init_db(db_path)

    with connect(db_path) as conn:
        register_contract(conn, _contract())
        client = FakeLLMClient(
            [
                JuristaExtractionPayload(
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
                                "Cláusula 4ª: A franquia mensal é de 40 horas técnicas desde "
                                "01/01/2026."
                            ),
                        ),
                        ExtractedRuleCandidate(
                            rule_type=RuleType.OVERAGE_PRICE,
                            new_value=Decimal("999.00"),
                            validity_start=date(2026, 1, 1),
                            relation=RuleRelation.CREATED,
                            page=3,
                            literal_excerpt=(
                                "Cláusula 5ª: A hora excedente custa R$ 150,00 desde 01/01/2026."
                            ),
                        ),
                    )
                )
            ]
        )
        base_proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-base",
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            client=client,
            received_at=_TS,
        )
        r_price, r_franq, r_bad = base_proc.rules
        assert r_bad.review_status is ReviewStatus.INCONCLUSIVE

        corrected = review_rule(
            conn,
            r_price.id,
            user="ana.auditora",
            decision=ReviewDecision.CORRECT,
            justification="Ajustada data de início para 01/01/2026.",
            reviewed_at=_TS,
            corrected_rule=r_price.model_copy(
                update={"validity": ValidityPeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))}
            ),
        )
        assert corrected.rule.review_status is ReviewStatus.CORRECTED
        assert corrected.rule.validity.start == date(2026, 1, 1)

        confirmed = review_rule(
            conn,
            r_franq.id,
            user="ana.auditora",
            decision=ReviewDecision.CONFIRM,
            justification="Franquia conferida.",
            reviewed_at=_TS,
        )
        assert confirmed.rule.review_status is ReviewStatus.CONFIRMED

        rejected = review_rule(
            conn,
            r_bad.id,
            user="ana.auditora",
            decision=ReviewDecision.REJECT,
            justification="Valor extraído incorretamente.",
            reviewed_at=_TS,
        )
        assert rejected.rule.review_status is ReviewStatus.REJECTED

        add_proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-add01",
            file_name="ct_2026_001_aditivo_01.docx",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
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
                                    "Pelo 1º Termo Aditivo ao contrato CT-2026-001, o valor mensal "
                                    "passa de R$ 10.000,00 para R$ 10.800,00 a partir de "
                                    "01/05/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            document_type=DocumentType.AMENDMENT,
            received_at=_TS,
        )
        amendment = Amendment(
            id="add-01",
            affected_rule_id=r_price.id,
            new_rule=add_proc.rules[0].model_copy(
                update={"review_status": ReviewStatus.CONFIRMED, "version": 2}
            ),
            source_document_id="doc-add01",
            effective_date=date(2026, 5, 1),
            relation=RuleRelation.ALTERED,
        )
        register_amendment(conn, amendment)

        reviewed_add = mark_amendment_reviewed(
            conn,
            "doc-add01",
            user="ana.auditora",
            justification="Aditivo 01 revisado na íntegra.",
            reviewed_at=_TS,
        )
        assert reviewed_add.document.reviewed is True
        assert reviewed_add.review.target_type is ReviewTargetType.DOCUMENT

        with pytest.raises(ValueError, match="não é um aditivo"):
            mark_amendment_reviewed(
                conn,
                "doc-base",
                user="ana.auditora",
                justification="Tentativa em contrato base.",
                reviewed_at=_TS,
            )

        assert len(fetch_reviews(conn, target_type=ReviewTargetType.RULE)) == 3
        assert len(fetch_reviews(conn, target_type=ReviewTargetType.DOCUMENT)) == 1


def test_import_billing_document_contract_linking_rules_and_formats(tmp_path: Path) -> None:
    db_path = tmp_path / "billing.db"
    init_db(db_path)

    with connect(db_path) as conn:
        c1 = register_contract(conn, _contract("CT-2026-001", cnpj="11.222.333/0001-81"))

        # 1. Vínculo explícito por nº do contrato
        nfe_101 = import_billing_document(
            conn,
            document_id="doc-inv-101",
            file_name="nfe_101_caso_b_regular.xml",
            content=SYNTHETIC_DIR / "nfe_101_caso_b_regular.xml",
            received_at=_TS,
            invoice_id="inv-101",
        )
        assert nfe_101.invoices[0].contract_id == c1.id
        assert nfe_101.document.contract_id == c1.id
        assert nfe_101.unlinked_invoice_ids == ()

        # 2. Nº de contrato informado mas não cadastrado -> sem vínculo com aviso
        unknown_res = import_billing_document(
            conn,
            document_id="doc-inv-302",
            file_name="nfe_302.xml",
            content=_nfe_xml_with_unknown_contract(),
            received_at=_TS,
            invoice_id="inv-302",
        )
        assert unknown_res.invoices[0].contract_id is None
        assert unknown_res.unlinked_invoice_ids == ("inv-302",)
        assert any("não está cadastrado" in w for w in unknown_res.warnings)

        # 3. Sem nº de contrato, CNPJ único -> vincula automaticamente
        unique_cnpj_res = import_billing_document(
            conn,
            document_id="doc-inv-301",
            file_name="nfe_301.xml",
            content=_nfe_xml_without_contract(),
            received_at=_TS,
            invoice_id="inv-301",
        )
        assert unique_cnpj_res.invoices[0].contract_id == c1.id
        assert unique_cnpj_res.unlinked_invoice_ids == ()

        # 4. Segundo contrato com mesmo CNPJ -> CNPJ ambíguo -> sem vínculo
        c2_same_cnpj = register_contract(conn, _contract("CT-2026-DUP", cnpj="11.222.333/0001-81"))
        ambiguous_res = import_billing_document(
            conn,
            document_id="doc-inv-303",
            file_name="nfe_303.xml",
            content=_nfe_xml_without_contract(number="303"),
            received_at=_TS,
            invoice_id="inv-303",
        )
        assert ambiguous_res.invoices[0].contract_id is None
        assert ambiguous_res.unlinked_invoice_ids == ("inv-303",)
        assert any("Mais de um contrato" in w for w in ambiguous_res.warnings)

        # Verifica também regra de divergência de CNPJ em resolve_contract_link
        _, cnpj_warn = resolve_contract_link(
            (c1, c2_same_cnpj),
            ambiguous_res.invoices[0].model_copy(
                update={"contract_id": "CT-2026-001", "supplier_cnpj": "60.746.948/0001-12"}
            ),
        )
        assert cnpj_warn is not None and "diverge" in cnpj_warn

        # 5. CNPJ desconhecido (nfe_202_sem_contrato.xml)
        orphan_res = import_billing_document(
            conn,
            document_id="doc-inv-202",
            file_name="nfe_202_sem_contrato.xml",
            content=SYNTHETIC_DIR / "nfe_202_sem_contrato.xml",
            received_at=_TS,
            invoice_id="inv-202",
        )
        assert orphan_res.invoices[0].contract_id is None
        assert orphan_res.unlinked_invoice_ids == ("inv-202",)

        # 6. NFS-e XML + CSV em lote + PDF via Agente Fiscal
        nfse_res = import_billing_document(
            conn,
            document_id="doc-inv-109",
            file_name="nfse_109_competencia_inferida.xml",
            content=SYNTHETIC_DIR / "nfse_109_competencia_inferida.xml",
            received_at=_TS,
            invoice_id="inv-109",
        )
        assert nfse_res.invoices[0].competence_inferred is True
        assert nfse_res.invoices[0].contract_id == "CT-2026-001"

        csv_res = import_billing_document(
            conn,
            document_id="doc-inv-csv",
            file_name="cobrancas_lote_ct_2026_001.csv",
            content=SYNTHETIC_DIR / "cobrancas_lote_ct_2026_001.csv",
            received_at=_TS,
        )
        assert csv_res.document.document_type is DocumentType.SPREADSHEET
        assert len(csv_res.invoices) == 5
        assert all(inv.contract_id == "CT-2026-001" for inv in csv_res.invoices)

        with pytest.raises(ValueError, match="exige LLMClient"):
            import_billing_document(
                conn,
                document_id="doc-inv-110-err",
                file_name="fatura_110_pdf_item_ambiguo.pdf",
                content=SYNTHETIC_DIR / "fatura_110_pdf_item_ambiguo.pdf",
                received_at=_TS,
            )

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
                            literal_excerpt=(
                                "Item 2: Servico adicional de plantao (1 un) - R$ 500,00"
                            ),
                        ),
                    ),
                )
            ]
        )
        pdf_res = import_billing_document(
            conn,
            document_id="doc-inv-110",
            file_name="fatura_110_pdf_item_ambiguo.pdf",
            content=SYNTHETIC_DIR / "fatura_110_pdf_item_ambiguo.pdf",
            client=fiscal_client,
            received_at=_TS,
        )
        assert pdf_res.document.document_type is DocumentType.INVOICE
        assert len(pdf_res.invoices) == 1
        assert pdf_res.invoices[0].number == "110"
        assert len(fetch_invoices(conn, "CT-2026-001")) == 9


def test_audit_stored_invoice_full_service_lifecycle_and_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "lifecycle.db"
    init_db(db_path)

    with connect(db_path) as conn:
        register_contract(conn, _contract())
        base_proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-base",
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
                        rules=(
                            ExtractedRuleCandidate(
                                rule_type=RuleType.MONTHLY_PRICE,
                                new_value=Decimal("10000.00"),
                                validity_start=date(2026, 1, 1),
                                validity_end=date(2026, 12, 31),
                                relation=RuleRelation.CREATED,
                                page=2,
                                literal_excerpt=(
                                    "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 "
                                    "até 31/12/2026."
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
                                    "Cláusula 4ª: A franquia mensal é de 40 horas técnicas desde "
                                    "01/01/2026."
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
                                    "Cláusula 5ª: A hora excedente custa R$ 150,00 desde "
                                    "01/01/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            received_at=_TS,
        )
        for rule in base_proc.rules:
            review_rule(
                conn,
                rule.id,
                user="auditora",
                decision=ReviewDecision.CONFIRM,
                justification="Confirmada regra do contrato base.",
                reviewed_at=_TS,
            )
        review_document(
            conn,
            "doc-base",
            user="auditora",
            decision=ReviewDecision.CONFIRM,
            justification="Contrato base revisado.",
            reviewed_at=_TS,
        )

        add1_proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-add01",
            file_name="ct_2026_001_aditivo_01.docx",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
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
                                    "Pelo 1º Termo Aditivo ao contrato CT-2026-001, o valor mensal "
                                    "passa de R$ 10.000,00 para R$ 10.800,00 a partir de "
                                    "01/05/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            document_type=DocumentType.AMENDMENT,
            received_at=_TS,
        )
        add1_rule = review_rule(
            conn,
            add1_proc.rules[0].id,
            user="auditora",
            decision=ReviewDecision.CONFIRM,
            justification="Regra do Aditivo 01 confirmada.",
            reviewed_at=_TS,
        ).rule
        register_amendment(
            conn,
            Amendment(
                id="add-01",
                affected_rule_id=base_proc.rules[0].id,
                new_rule=add1_rule.model_copy(update={"version": 2}),
                source_document_id="doc-add01",
                effective_date=date(2026, 5, 1),
                relation=RuleRelation.ALTERED,
            ),
        )

        add2_proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-add02",
            file_name="ct_2026_001_aditivo_02.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_02.pdf",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
                        rules=(
                            ExtractedRuleCandidate(
                                rule_type=RuleType.MONTHLY_PRICE,
                                new_value=Decimal("10500.00"),
                                previous_value=Decimal("10800.00"),
                                validity_start=date(2026, 9, 1),
                                relation=RuleRelation.ALTERED,
                                page=2,
                                literal_excerpt=(
                                    "Cláusula 2ª: Passa de R$ 10.800,00 para R$ 10.500,00 a "
                                    "partir de 01/09/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            document_type=DocumentType.AMENDMENT,
            received_at=_TS,
        )
        add2_rule = review_rule(
            conn,
            add2_proc.rules[0].id,
            user="auditora",
            decision=ReviewDecision.CONFIRM,
            justification="Regra do Aditivo 02 confirmada.",
            reviewed_at=_TS,
        ).rule
        register_amendment(
            conn,
            Amendment(
                id="add-02",
                affected_rule_id=add1_rule.id,
                new_rule=add2_rule.model_copy(update={"version": 3}),
                source_document_id="doc-add02",
                effective_date=date(2026, 9, 1),
                relation=RuleRelation.ALTERED,
            ),
        )

        import_billing_document(
            conn,
            document_id="doc-inv-104",
            file_name="nfe_104_caso_a_divergencia.xml",
            content=SYNTHETIC_DIR / "nfe_104_caso_a_divergencia.xml",
            received_at=_TS,
            invoice_id="inv-104",
        )

        # Aditivos ainda não marcados como revisados -> NEEDS_REVIEW (M-17)
        pending_outcome = audit_stored_invoice(conn, "inv-104")
        assert (
            pending_outcome.stored_audit.result.classification is AuditClassification.NEEDS_REVIEW
        )

        mark_amendment_reviewed(
            conn,
            "doc-add01",
            user="auditora",
            justification="Aditivo 01 revisado.",
            reviewed_at=_TS,
        )
        mark_amendment_reviewed(
            conn,
            "doc-add02",
            user="auditora",
            justification="Aditivo 02 revisado.",
            reviewed_at=_TS,
        )

        tradutor_ok = FakeLLMClient(
            [
                TradutorExtractionPayload(
                    links=(
                        ItemRuleLinkCandidate(
                            item_index=0,
                            rule_id=add2_rule.id,
                            item_excerpt="Sustentação técnica continuada mensal",
                            rule_excerpt=(
                                "Cláusula 2ª: Passa de R$ 10.800,00 para R$ 10.500,00 a partir "
                                "de 01/09/2026."
                            ),
                            similarity_score=Decimal("0.94"),
                        ),
                    )
                )
            ]
        )
        hallucinating_report = FakeLLMClient(
            [
                ReportExplanationPayload(
                    summary="Diferença inventada de R$ 999,00 na competência 10/2026."
                )
            ]
        )
        outcome_104 = audit_stored_invoice(
            conn,
            "inv-104",
            tradutor_client=tradutor_ok,
            report_client=hallucinating_report,
        )
        assert outcome_104.stored_audit.result.classification is AuditClassification.DIVERGENCE
        assert outcome_104.stored_audit.result.difference == Decimal("300.00")
        assert outcome_104.report.rejected_llm_text is True
        assert outcome_104.report.final_text == outcome_104.report.deterministic_text

        # Corrige a regra depois no banco -> auditoria salva preserva snapshot da época (I-06)
        review_rule(
            conn,
            add2_rule.id,
            user="auditora",
            decision=ReviewDecision.CORRECT,
            justification="Alteração posterior para teste de snapshot.",
            reviewed_at=_TS,
            corrected_rule=add2_rule.model_copy(update={"value": Decimal("10900.00")}),
        )
        saved_audits = fetch_audits(conn, "inv-104")
        assert len(saved_audits) == 1
        assert saved_audits[0].result.difference == Decimal("300.00")
        snap_add2 = next(r for r in saved_audits[0].rules_snapshot if r.id == add2_rule.id)
        assert snap_add2.value == Decimal("10500.00")

        # Fatura PDF com vínculo ambíguo pausado e depois liberado por humano
        fiscal_res = import_billing_document(
            conn,
            document_id="doc-inv-110",
            file_name="fatura_110_pdf_item_ambiguo.pdf",
            content=SYNTHETIC_DIR / "fatura_110_pdf_item_ambiguo.pdf",
            client=FakeLLMClient(
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
                                literal_excerpt=(
                                    "Item 1: Mensalidade de suporte (1 mes) - R$ 10.500,00"
                                ),
                            ),
                            ExtractedBillingItemCandidate(
                                description="Servico adicional de plantao",
                                quantity=Decimal("1"),
                                unit="un",
                                unit_value=Decimal("500.00"),
                                total_value=Decimal("500.00"),
                                literal_excerpt=(
                                    "Item 2: Servico adicional de plantao (1 un) - R$ 500,00"
                                ),
                            ),
                        ),
                    )
                ]
            ),
            received_at=_TS,
        )
        inv_110 = fiscal_res.invoices[0]
        ambiguous_links = TradutorExtractionPayload(
            links=(
                ItemRuleLinkCandidate(
                    item_index=0,
                    rule_id=add2_rule.id,
                    item_excerpt="Mensalidade de suporte",
                    rule_excerpt=add2_rule.evidence.literal_excerpt,
                    similarity_score=Decimal("0.96"),
                ),
                ItemRuleLinkCandidate(
                    item_index=1,
                    rule_id=base_proc.rules[2].id,
                    item_excerpt="Servico adicional de plantao",
                    rule_excerpt=base_proc.rules[2].evidence.literal_excerpt,
                    similarity_score=Decimal("0.70"),
                    ambiguous=True,
                ),
            )
        )
        paused_110 = audit_stored_invoice(
            conn, inv_110.id, tradutor_client=FakeLLMClient([ambiguous_links])
        )
        assert paused_110.awaiting_link_review is True
        assert paused_110.stored_audit.result.classification is AuditClassification.NEEDS_REVIEW
        assert [c.name for c in paused_110.stored_audit.result.checks] == ["semantic_item_link"]

        approved_110 = audit_stored_invoice(
            conn,
            inv_110.id,
            tradutor_client=FakeLLMClient([ambiguous_links]),
            links_approved_by_human=True,
        )
        assert approved_110.awaiting_link_review is False
        assert "item_classification" in {c.name for c in approved_110.stored_audit.result.checks}

        # Cobrança sem contrato vinculado -> MISSING_DOCUMENTATION (M-15)
        import_billing_document(
            conn,
            document_id="doc-inv-202",
            file_name="nfe_202_sem_contrato.xml",
            content=SYNTHETIC_DIR / "nfe_202_sem_contrato.xml",
            received_at=_TS,
            invoice_id="inv-202",
        )
        orphan_audit = audit_stored_invoice(conn, "inv-202")
        assert (
            orphan_audit.stored_audit.result.classification
            is AuditClassification.MISSING_DOCUMENTATION
        )
        assert len(fetch_documents(conn, "CT-2026-001")) == 5


def test_register_human_decision_for_audit_rule_and_document(tmp_path: Path) -> None:
    db_path = tmp_path / "decisions.db"
    init_db(db_path)

    with connect(db_path) as conn:
        register_contract(conn, _contract())
        proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-base",
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
                        rules=(
                            ExtractedRuleCandidate(
                                rule_type=RuleType.MONTHLY_PRICE,
                                new_value=Decimal("10000.00"),
                                validity_start=date(2026, 1, 1),
                                relation=RuleRelation.CREATED,
                                page=2,
                                literal_excerpt=(
                                    "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 "
                                    "até 31/12/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            received_at=_TS,
        )
        imp = import_billing_document(
            conn,
            document_id="doc-inv-101",
            file_name="nfe_101_caso_b_regular.xml",
            content=SYNTHETIC_DIR / "nfe_101_caso_b_regular.xml",
            received_at=_TS,
            invoice_id="inv-101",
        )
        audit_out = audit_stored_invoice(conn, imp.invoices[0].id)
        audit_id = audit_out.stored_audit.result.id

        r1 = register_human_decision(
            conn,
            target_type=ReviewTargetType.RULE,
            target_id=proc.rules[0].id,
            user="auditora",
            decision=ReviewDecision.CONFIRM,
            justification="Regra confirmada.",
            reviewed_at=_TS,
        )
        r2 = register_human_decision(
            conn,
            target_type=ReviewTargetType.DOCUMENT,
            target_id="doc-base",
            user="auditora",
            decision=ReviewDecision.CONFIRM,
            justification="Documento confirmado.",
            reviewed_at=_TS,
        )
        r3 = register_human_decision(
            conn,
            target_type=ReviewTargetType.AUDIT_RESULT,
            target_id=audit_id,
            user="gestor.financeiro",
            decision=ReviewDecision.CONFIRM,
            justification="Auditoria validada para pagamento.",
            reviewed_at=_TS,
        )
        assert r1.target_type is ReviewTargetType.RULE
        assert r2.target_type is ReviewTargetType.DOCUMENT
        assert r3.target_type is ReviewTargetType.AUDIT_RESULT
        assert len(fetch_reviews(conn)) == 3

        with pytest.raises(ValueError, match="não encontrada"):
            record_audit_decision(
                conn,
                "audit-inexistente",
                user="gestor.financeiro",
                decision=ReviewDecision.REJECT,
                justification="Não existe.",
                reviewed_at=_TS,
            )


def test_timeline_competence_lookup_assign_seed_and_reset(tmp_path: Path) -> None:
    db_path = tmp_path / "demo_services.db"
    init_db(db_path).close()

    with connect(db_path) as conn:
        seeded_ids = seed_synthetic_demo(conn, SYNTHETIC_DIR, reviewed_at=_TS)
        assert seeded_ids[0] == "CT-2026-001"
        assert len(fetch_amendments(conn, "CT-2026-001")) == 2

        timeline = get_contract_timeline(conn, "CT-2026-001")
        assert timeline.complete is True
        monthly_versions = sorted(
            (rule for rule in timeline.rules if rule.rule_type is RuleType.MONTHLY_PRICE),
            key=lambda rule: rule.version,
        )
        assert [rule.value for rule in monthly_versions] == [
            Decimal("10000.00"),
            Decimal("10800.00"),
            Decimal("10500.00"),
        ]

        abr = get_rules_for_competence(
            conn, "CT-2026-001", ReferencePeriod.model_validate("2026-04")
        )
        assert abr.selections[RuleType.MONTHLY_PRICE].status is SelectionStatus.SELECTED
        assert abr.selections[RuleType.MONTHLY_PRICE].rule is not None
        assert abr.selections[RuleType.MONTHLY_PRICE].rule.value == Decimal("10000.00")

        mai = get_rules_for_competence(
            conn, "CT-2026-001", ReferencePeriod.model_validate("2026-05")
        )
        assert mai.selections[RuleType.MONTHLY_PRICE].rule is not None
        assert mai.selections[RuleType.MONTHLY_PRICE].rule.value == Decimal("10800.00")

        out = get_rules_for_competence(
            conn, "CT-2026-001", ReferencePeriod.model_validate("2026-10")
        )
        assert out.selections[RuleType.MONTHLY_PRICE].rule is not None
        assert out.selections[RuleType.MONTHLY_PRICE].rule.value == Decimal("10500.00")
        assert out.selections[RuleType.EXTENSION].status is SelectionStatus.NO_RULE

        orphan = import_billing_document(
            conn,
            document_id="doc-orphan",
            file_name="nfe_202_sem_contrato.xml",
            content=SYNTHETIC_DIR / "nfe_202_sem_contrato.xml",
            received_at=_TS,
            invoice_id="inv-orphan",
        )
        assert orphan.unlinked_invoice_ids == ("inv-orphan",)
        linked = assign_invoice_contract(conn, "inv-orphan", "CT-2026-001")
        assert linked.contract_id == "CT-2026-001"

        with pytest.raises(ValueError, match="já está cadastrado"):
            seed_synthetic_demo(conn, SYNTHETIC_DIR, reviewed_at=_TS)

    reset_all_data(db_path)
    with connect(db_path) as conn:
        assert fetch_contracts(conn) == ()
        assert fetch_rules(conn) == ()
        assert fetch_reviews(conn) == ()
        assert fetch_invoices(conn) == ()


def test_ingest_uploaded_files_auto_registers_contract_and_handles_all_extensions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "batch_upload.db"
    init_db(db_path).close()

    files = [
        (
            "ct_2026_001_contrato_base.pdf",
            (SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf").read_bytes(),
        ),
        (
            "ct_2026_001_aditivo_01.docx",
            (SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx").read_bytes(),
        ),
        (
            "nfe_101_caso_b_regular.xml",
            (SYNTHETIC_DIR / "nfe_101_caso_b_regular.xml").read_bytes(),
        ),
        (
            "cobrancas_lote_ct_2026_001.csv",
            (SYNTHETIC_DIR / "cobrancas_lote_ct_2026_001.csv").read_bytes(),
        ),
    ]
    client = FakeLLMClient(
        [
            JuristaExtractionPayload(
                rules=(
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("10000.00"),
                        validity_start=date(2026, 1, 1),
                        validity_end=date(2026, 12, 31),
                        relation=RuleRelation.CREATED,
                        page=2,
                        literal_excerpt=(
                            "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 até "
                            "31/12/2026."
                        ),
                    ),
                )
            ),
            JuristaExtractionPayload(
                rules=(
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("10800.00"),
                        validity_start=date(2026, 5, 1),
                        relation=RuleRelation.ALTERED,
                        clause="Cláusula 1ª - Reajuste Contratual",
                        paragraph="1",
                        literal_excerpt=(
                            "Pelo 1º Termo Aditivo ao contrato CT-2026-001, o valor mensal "
                            "passa de R$ 10.000,00 para R$ 10.800,00 a partir de 01/05/2026."
                        ),
                    ),
                )
            ),
        ]
    )

    with connect(db_path) as conn:
        batch = ingest_uploaded_files(
            conn,
            files,
            mode="AUTO",
            selected_contract_id=None,
            client=client,
            received_at=_TS,
        )
        assert [c.id for c in batch.auto_registered_contracts] == ["CT-2026-001"]
        assert batch.active_contract_id == "CT-2026-001"
        assert len(batch.outcomes) == 4
        assert len(fetch_contracts(conn)) == 1
        assert len(fetch_documents(conn)) == 4
        assert len(fetch_rules(conn, "CT-2026-001")) == 2
        invoices = fetch_invoices(conn, "CT-2026-001")
        assert len(invoices) >= 2

        no_ai = ingest_uploaded_files(
            conn,
            [
                (
                    "contrato_escaneado_sem_texto.pdf",
                    (SYNTHETIC_DIR / "contrato_escaneado_sem_texto.pdf").read_bytes(),
                ),
                ("arquivo.txt", b"conteudo qualquer"),
            ],
            mode="AUTO",
            selected_contract_id="CT-2026-001",
            client=None,
            received_at=_TS,
        )
        assert no_ai.outcomes[0].document is not None
        assert no_ai.outcomes[0].document.read_quality is ReadQuality.NO_TEXT
        assert no_ai.outcomes[1].detected_kind == "SKIPPED"


def test_delete_uploaded_document_removes_derived_entities_and_keeps_reviews(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "delete_docs.db"
    init_db(db_path).close()

    with connect(db_path) as conn:
        seed_synthetic_demo(conn, SYNTHETIC_DIR, reviewed_at=_TS)
        audit_stored_invoice(conn, "inv-demo-104")
        reviews_before = fetch_reviews(conn)
        assert len(reviews_before) >= 8

        # Apagar documento do 2º Aditivo remove a regra do aditivo e o vínculo Amendment.
        del_add2 = delete_uploaded_document(conn, "doc-ct-2026-001-add-02")
        assert del_add2.deleted_rules == 1
        assert del_add2.deleted_amendments == 1
        assert len(fetch_amendments(conn, "CT-2026-001")) == 1

        # Apagar NF-e 104 remove a cobrança e a auditoria associada.
        del_nfe = delete_uploaded_document(conn, "doc-inv-demo-104")
        assert del_nfe.deleted_invoices == 1
        assert del_nfe.deleted_audits == 1
        assert all(inv.id != "inv-demo-104" for inv in fetch_invoices(conn))

        # Apagar todos os documentos restantes limpa documentos, regras e cobranças,
        # preservando a trilha append-only em `reviews`.
        deleted_rest = delete_all_uploaded_documents(conn, contract_id="CT-2026-001")
        assert len(deleted_rest) >= 4
        assert fetch_documents(conn, "CT-2026-001") == ()
        assert fetch_rules(conn, "CT-2026-001") == ()
        assert fetch_invoices(conn, "CT-2026-001") == ()
        assert fetch_reviews(conn) == reviews_before

        with pytest.raises(ValueError, match="não encontrado"):
            delete_uploaded_document(conn, "doc-inexistente")


def test_ingest_uploaded_files_handles_no_text_and_docx_without_cnpj_on_empty_db(
    tmp_path: Path,
) -> None:
    # 1) PDF escaneado sem texto em banco vazio não pode ser descartado como SKIPPED.
    db_no_text = tmp_path / "no_text.db"
    init_db(db_no_text).close()
    with connect(db_no_text) as conn:
        res_no_text = ingest_uploaded_files(
            conn,
            [
                (
                    "contrato_escaneado_sem_texto.pdf",
                    (SYNTHETIC_DIR / "contrato_escaneado_sem_texto.pdf").read_bytes(),
                )
            ],
            selected_contract_id=None,
            client=None,
            received_at=_TS,
        )
        out = res_no_text.outcomes[0]
        assert out.detected_kind == "CONTRACT"
        assert out.document is not None
        assert out.document.read_quality is ReadQuality.NO_TEXT
        assert len(fetch_documents(conn)) == 1

    # 2) DOCX sem CNPJ no texto auto-cria contrato provisório; PDF seguinte com CNPJ atualiza.
    db_provisional = tmp_path / "provisional.db"
    init_db(db_provisional).close()
    with connect(db_provisional) as conn:
        docx_client = FakeLLMClient(
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
                                "contratante pagará o valor mensal de R$ 5.000,00 de 01/01/2026 "
                                "até 30/06/2026."
                            ),
                        ),
                    )
                )
            ]
        )
        res_docx = ingest_uploaded_files(
            conn,
            [
                (
                    "ct_2026_002_contrato_armadilhas.docx",
                    (SYNTHETIC_DIR / "ct_2026_002_contrato_armadilhas.docx").read_bytes(),
                )
            ],
            selected_contract_id=None,
            client=docx_client,
            received_at=_TS,
        )
        assert res_docx.outcomes[0].detected_kind == "CONTRACT"
        assert res_docx.outcomes[0].document is not None
        assert res_docx.outcomes[0].rules_count == 1
        ct2 = fetch_contract(conn, "CT-2026-002")
        assert ct2 is not None
        assert ct2.supplier_cnpj == "00000000000191"

        add_client = FakeLLMClient(
            [
                JuristaExtractionPayload(
                    rules=(
                        ExtractedRuleCandidate(
                            rule_type=RuleType.MONTHLY_PRICE,
                            new_value=Decimal("5500.00"),
                            validity_start=date(2026, 4, 15),
                            relation=RuleRelation.ALTERED,
                            page=1,
                            literal_excerpt=(
                                "Cláusula 1ª: Passa de R$ 5.000,00 para R$ 5.500,00 a "
                                "partir de 15/04/2026."
                            ),
                        ),
                    )
                )
            ]
        )
        res_add = ingest_uploaded_files(
            conn,
            [
                (
                    "ct_2026_002_aditivo_meio_periodo.pdf",
                    (SYNTHETIC_DIR / "ct_2026_002_aditivo_meio_periodo.pdf").read_bytes(),
                )
            ],
            selected_contract_id=None,
            client=add_client,
            received_at=_TS,
        )
        assert res_add.outcomes[0].contract_id == "CT-2026-002"
        upgraded = fetch_contract(conn, "CT-2026-002")
        assert upgraded is not None
        assert upgraded.supplier_cnpj == "33000167000101"

    # 3) Lote misto com dois contratos distintos + respectivas NF-es em banco vazio.
    db_mixed = tmp_path / "mixed_batch.db"
    init_db(db_mixed).close()
    with connect(db_mixed) as conn:
        mixed_client = FakeLLMClient(
            [
                JuristaExtractionPayload(
                    rules=(
                        ExtractedRuleCandidate(
                            rule_type=RuleType.MONTHLY_PRICE,
                            new_value=Decimal("10000.00"),
                            validity_start=date(2026, 1, 1),
                            validity_end=date(2026, 12, 31),
                            relation=RuleRelation.CREATED,
                            page=2,
                            literal_excerpt=(
                                "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 "
                                "até 31/12/2026."
                            ),
                        ),
                    )
                ),
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
                                "contratante pagará o valor mensal de R$ 5.000,00 de 01/01/2026 "
                                "até 30/06/2026."
                            ),
                        ),
                    )
                ),
            ]
        )
        mixed_res = ingest_uploaded_files(
            conn,
            [
                (
                    "ct_2026_001_contrato_base.pdf",
                    (SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf").read_bytes(),
                ),
                (
                    "ct_2026_002_contrato_armadilhas.docx",
                    (SYNTHETIC_DIR / "ct_2026_002_contrato_armadilhas.docx").read_bytes(),
                ),
                (
                    "nfe_101_caso_b_regular.xml",
                    (SYNTHETIC_DIR / "nfe_101_caso_b_regular.xml").read_bytes(),
                ),
                (
                    "nfe_201_meio_periodo.xml",
                    (SYNTHETIC_DIR / "nfe_201_meio_periodo.xml").read_bytes(),
                ),
            ],
            selected_contract_id=None,
            client=mixed_client,
            received_at=_TS,
        )
        assert {c.id for c in fetch_contracts(conn)} == {"CT-2026-001", "CT-2026-002"}
        assert all(o.detected_kind != "SKIPPED" for o in mixed_res.outcomes)
        by_num = {inv.number: inv.contract_id for inv in fetch_invoices(conn)}
        assert by_num == {"101": "CT-2026-001", "201": "CT-2026-002"}


def test_ingest_dossier_docx_auto_audits_divergence_and_cleans_up_empty_contract(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dossier.db"
    init_db(db_path).close()

    dossier_bytes = build_synthetic_docx(
        [
            (
                "Cláusula 1ª - Objeto e Preço",
                [
                    (
                        "Contrato CT-2026-900 entre Empresa Teste S.A. e Fornecedor Dossiê Ltda. "
                        "(CNPJ 11.222.333/0001-81). O valor mensal contratado é de R$ 10.000,00 "
                        "desde 01/01/2026 até 31/12/2026."
                    )
                ],
            ),
            (
                "Cobrança Associada",
                [
                    (
                        "Fatura nº 901 referente à competência 2026-04: Mensalidade de suporte "
                        "cobrada em R$ 12.000,00. Total da fatura: R$ 12.000,00."
                    )
                ],
            ),
        ]
    )
    dossier_client = FakeLLMClient(
        [
            JuristaExtractionPayload(
                rules=(
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("10000.00"),
                        validity_start=date(2026, 1, 1),
                        validity_end=date(2026, 12, 31),
                        relation=RuleRelation.CREATED,
                        clause="Cláusula 1ª - Objeto e Preço",
                        paragraph="1",
                        literal_excerpt=(
                            "O valor mensal contratado é de R$ 10.000,00 desde 01/01/2026 até "
                            "31/12/2026."
                        ),
                    ),
                )
            ),
            FiscalExtractionPayload(
                contract_id="CT-2026-900",
                supplier="Fornecedor Dossiê Ltda.",
                supplier_cnpj="11.222.333/0001-81",
                number="901",
                issue_date=date(2026, 4, 30),
                reference_period=ReferencePeriod(year=2026, month=4),
                total=Decimal("12000.00"),
                page=1,
                literal_excerpt="Total da fatura: R$ 12.000,00.",
                items=(
                    ExtractedBillingItemCandidate(
                        description="Mensalidade de suporte",
                        quantity=Decimal("1"),
                        unit="mês",
                        unit_value=Decimal("12000.00"),
                        total_value=Decimal("12000.00"),
                        literal_excerpt="Mensalidade de suporte cobrada em R$ 12.000,00.",
                    ),
                ),
            ),
        ]
    )

    with connect(db_path) as conn:
        batch = ingest_uploaded_files(
            conn,
            [("dossie_teste_audit.docx", dossier_bytes)],
            mode="AUTO",
            selected_contract_id=None,
            client=dossier_client,
            received_at=_TS,
        )
        assert batch.active_contract_id == "CT-2026-900"
        assert batch.outcomes[0].detected_kind == "DOSSIER"
        assert batch.outcomes[0].rules_count == 1
        assert batch.outcomes[0].invoices_count == 1
        assert batch.audits_count == 1
        assert batch.divergences_count == 1

        saved_invoices = fetch_invoices(conn, "CT-2026-900")
        assert len(saved_invoices) == 1
        audits = fetch_audits(conn, saved_invoices[0].id)
        assert len(audits) == 1
        assert audits[0].result.classification is AuditClassification.NEEDS_REVIEW
        assert audits[0].result.difference == Decimal("2000.00")

        rules_900 = fetch_rules(conn, "CT-2026-900")
        review_rule(
            conn,
            rules_900[0].id,
            user="auditor.teste",
            decision=ReviewDecision.CONFIRM,
            justification="Regra confirmada contra o dossiê.",
            reviewed_at=_TS,
        )
        confirmed_audit = audit_stored_invoice(conn, saved_invoices[0].id)
        assert confirmed_audit.stored_audit.result.classification is AuditClassification.DIVERGENCE
        assert confirmed_audit.stored_audit.result.difference == Decimal("2000.00")

        # Não deve permitir apagar contrato enquanto houver documentos/regras/cobranças.
        assert delete_empty_contract(conn, "CT-2026-900") is False

        doc = batch.outcomes[0].document
        assert doc is not None
        deleted = delete_uploaded_document(conn, doc.id)
        assert deleted.deleted_rules == 1
        assert deleted.deleted_invoices == 1
        assert deleted.deleted_audits == 1

        assert delete_empty_contract(conn, "CT-2026-900") is True
        assert fetch_contract(conn, "CT-2026-900") is None


def test_ingest_realistic_commercial_dossier_pdf_with_masked_supplier_cnpj_and_four_invoices(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "realistic_dossier.db"
    init_db(db_path).close()

    dossier_pdf = build_synthetic_pdf(
        [
            [
                "CONTRATO PARTICULAR DE PRESTAÇÃO DE SERVIÇOS",
                "INSTRUMENTO Nº CS-047/2026",
                "CONTRATANTE CONTRATADA",
                "ATLAS ADMINISTRAÇÃO PREDIAL LTDA. NORTE SUL MANUTENÇÃO LTDA.",
                "CNPJ: 12.345.678/0001-95 CNPJ: 45.781.236/0001-??",
                "CLÁUSULA 3ª — DO PRAZO E DA VIGÊNCIA",
                (
                    "3.1. A vigência será de 12 meses de 02/09/2026 até 01/09/2027, "
                    "prorrogável por termo aditivo."
                ),
                "CLÁUSULA 4ª — DO PREÇO",
                "4.1. O valor mensal fixo é de R$ 18.750,00 a partir de 02/09/2026.",
            ],
            [
                "ANEXO II — TERMO ADITIVO Nº 01",
                (
                    "2.2. A partir de 01/11/2026, o valor mensal passa de R$ 18.750,00 "
                    "para R$ 19.200,00."
                ),
            ],
            [
                "ANEXO III — DEMONSTRATIVO DE FATURAMENTO",
                "DOCUMENTOS FISCAIS — SETEMBRO A NOVEMBRO/2026",
                "NF Emissão Competência Vencimento Descrição Valor",
                (
                    "NS-2026/091 30/09/2026 09/2026 10/10/2026 "
                    "Serviços e materiais — mensalidade R$ 18.750,00"
                ),
                (
                    "NS-2026/102 31/10/2026 10/2026 10/11/2026 "
                    "Serviços e materiais — mensalidade R$ 18.750,00"
                ),
                (
                    "NS-2026/113 30/11/2026 11/2026 10/12/2026 "
                    "Serviços e materiais — mensalidade R$ 18.750,00"
                ),
                (
                    "NS-2026/114 30/11/2026 11/2026 10/12/2026 "
                    "Plantão adicional extraordinário R$ 900,00"
                ),
            ],
        ]
    )
    client = FakeLLMClient(
        [
            JuristaExtractionPayload(
                rules=(
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("18750.00"),
                        validity_start=date(2026, 9, 2),
                        relation=RuleRelation.CREATED,
                        page=1,
                        literal_excerpt=(
                            "4.1. O valor mensal fixo é de R$ 18.750,00 a partir de 02/09/2026."
                        ),
                    ),
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("19200.00"),
                        previous_value=Decimal("18750.00"),
                        validity_start=date(2026, 11, 1),
                        relation=RuleRelation.ALTERED,
                        page=2,
                        literal_excerpt=(
                            "2.2. A partir de 01/11/2026, o valor mensal passa de R$ 18.750,00 "
                            "para R$ 19.200,00."
                        ),
                    ),
                )
            ),
            FiscalExtractionPayload(
                contract_id="CS-047/2026",
                supplier="Norte Sul Manutenção Ltda.",
                supplier_cnpj="45.781.236/0001-??",
                number="NS-2026/091",
                issue_date=date(2026, 9, 30),
                reference_period=ReferencePeriod(year=2026, month=9),
                total=Decimal("18750.00"),
                page=3,
                literal_excerpt=(
                    "NS-2026/091 30/09/2026 09/2026 10/10/2026 Serviços e materiais — mensalidade "
                    "R$ 18.750,00"
                ),
                items=(
                    ExtractedBillingItemCandidate(
                        description="Serviços e materiais — mensalidade",
                        quantity=Decimal("1"),
                        unit="mês",
                        unit_value=Decimal("18750.00"),
                        total_value=Decimal("18750.00"),
                        literal_excerpt=(
                            "NS-2026/091 30/09/2026 09/2026 10/10/2026 Serviços e materiais — "
                            "mensalidade R$ 18.750,00"
                        ),
                    ),
                ),
                additional_invoices=(
                    ExtractedInvoiceEntry(
                        contract_id="CS-047/2026",
                        supplier="Norte Sul Manutenção Ltda.",
                        supplier_cnpj="45.781.236/0001-??",
                        number="NS-2026/102",
                        issue_date=date(2026, 10, 31),
                        reference_period=ReferencePeriod(year=2026, month=10),
                        total=Decimal("18750.00"),
                        page=3,
                        literal_excerpt=(
                            "NS-2026/102 31/10/2026 10/2026 10/11/2026 Serviços e materiais — "
                            "mensalidade R$ 18.750,00"
                        ),
                        items=(
                            ExtractedBillingItemCandidate(
                                description="Serviços e materiais — mensalidade",
                                quantity=Decimal("1"),
                                unit="mês",
                                unit_value=Decimal("18750.00"),
                                total_value=Decimal("18750.00"),
                                literal_excerpt=(
                                    "NS-2026/102 31/10/2026 10/2026 10/11/2026 Serviços e "
                                    "materiais — mensalidade R$ 18.750,00"
                                ),
                            ),
                        ),
                    ),
                    ExtractedInvoiceEntry(
                        contract_id="CS-047/2026",
                        supplier="Norte Sul Manutenção Ltda.",
                        supplier_cnpj="45.781.236/0001-??",
                        number="NS-2026/113",
                        issue_date=date(2026, 11, 30),
                        reference_period=ReferencePeriod(year=2026, month=11),
                        total=Decimal("18750.00"),
                        page=3,
                        literal_excerpt=(
                            "NS-2026/113 30/11/2026 11/2026 10/12/2026 Serviços e materiais — "
                            "mensalidade R$ 18.750,00"
                        ),
                        items=(
                            ExtractedBillingItemCandidate(
                                description="Serviços e materiais — mensalidade",
                                quantity=Decimal("1"),
                                unit="mês",
                                unit_value=Decimal("18750.00"),
                                total_value=Decimal("18750.00"),
                                literal_excerpt=(
                                    "NS-2026/113 30/11/2026 11/2026 10/12/2026 Serviços e "
                                    "materiais — mensalidade R$ 18.750,00"
                                ),
                            ),
                        ),
                    ),
                    ExtractedInvoiceEntry(
                        contract_id="CS-047/2026",
                        supplier="Norte Sul Manutenção Ltda.",
                        supplier_cnpj="45.781.236/0001-??",
                        number="NS-2026/114",
                        issue_date=date(2026, 11, 30),
                        reference_period=ReferencePeriod(year=2026, month=11),
                        total=Decimal("900.00"),
                        page=3,
                        literal_excerpt=(
                            "NS-2026/114 30/11/2026 11/2026 10/12/2026 Plantão adicional "
                            "extraordinário R$ 900,00"
                        ),
                        items=(
                            ExtractedBillingItemCandidate(
                                description="Plantão adicional extraordinário",
                                quantity=Decimal("1"),
                                unit="un",
                                unit_value=Decimal("900.00"),
                                total_value=Decimal("900.00"),
                                literal_excerpt=(
                                    "NS-2026/114 30/11/2026 11/2026 10/12/2026 Plantão adicional "
                                    "extraordinário R$ 900,00"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ]
    )

    with connect(db_path) as conn:
        # Simula contrato anterior selecionado na UI para garantir que CS-047/2026 não se misture.
        register_contract(conn, _contract("CT-2026-002", cnpj="33.000.167/0001-01"))
        batch = ingest_uploaded_files(
            conn,
            [("contrato_comercial_simulado_com_anexos.pdf", dossier_pdf)],
            mode="AUTO",
            selected_contract_id="CT-2026-002",
            client=client,
            received_at=_TS,
        )
        assert batch.outcomes[0].detected_kind == "DOSSIER"
        assert batch.active_contract_id == "CS-047/2026"
        assert batch.outcomes[0].invoices_count == 4
        assert batch.audits_count == 4
        assert any("45.781.236/0001-??" in msg for msg in batch.warnings)

        cs_contract = fetch_contract(conn, "CS-047/2026")
        assert cs_contract is not None
        assert cs_contract.supplier_cnpj == "00000000000191"
        assert fetch_invoices(conn, "CT-2026-002") == ()

        cs_invoices = {inv.number: inv for inv in fetch_invoices(conn, "CS-047/2026")}
        assert set(cs_invoices.keys()) == {
            "NS-2026/091",
            "NS-2026/102",
            "NS-2026/113",
            "NS-2026/114",
        }

        for rule in fetch_rules(conn, "CS-047/2026"):
            review_rule(
                conn,
                rule.id,
                user="auditor.teste",
                decision=ReviewDecision.CONFIRM,
                justification="Regra confirmada contra o dossiê.",
                reviewed_at=_TS,
            )

        out_113 = audit_stored_invoice(conn, cs_invoices["NS-2026/113"].id)
        assert out_113.stored_audit.result.classification is AuditClassification.DIVERGENCE
        assert out_113.stored_audit.result.difference == Decimal("-450.00")

        out_114 = audit_stored_invoice(conn, cs_invoices["NS-2026/114"].id)
        assert out_114.stored_audit.result.classification is AuditClassification.NEEDS_REVIEW

        # Exclusão individual de cobrança remove fatura e auditoria preservando trilha `reviews`
        from backend.api import delete_stored_invoice, fetch_reviews

        reviews_before_del = len(fetch_reviews(conn))
        deleted_ok = delete_stored_invoice(conn, cs_invoices["NS-2026/114"].id)
        assert deleted_ok is True
        assert delete_stored_invoice(conn, "inexistente") is False
        assert len(fetch_invoices(conn, "CS-047/2026")) == 3
        assert len(fetch_audits(conn, cs_invoices["NS-2026/114"].id)) == 0
        assert len(fetch_reviews(conn)) == reviews_before_del
