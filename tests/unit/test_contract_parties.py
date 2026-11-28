"""Testes unitários de leitura de preâmbulo, papéis contratuais, vigência e CNPJ."""

from datetime import date
from decimal import Decimal

from backend.agentes.fiscal import _try_reconstruct_wrapped_cnpj
from backend.api.services import (
    _extract_contract_id_hint,
    _extract_contract_parties,
    _extract_contract_validity,
    _extract_supplier_cnpj_info,
    resolve_contract_link,
)
from backend.motor import check_supplier_identity
from backend.schemas import (
    Contract,
    ContractStatus,
    Invoice,
    InvoiceSource,
    ReferencePeriod,
    ValidityPeriod,
)


def test_preamble_locadora_before_locataria_and_validity_over_insurance() -> None:
    text = """
    DOSSIÊ DE CONFERÊNCIA CONTRATUAL
    Contrato de Locação Não Residencial nº LOC-2024/017
    Grupo Maré Alta Logística Ltda. (locatária) e
    Imobiliária Cordeiro Empreendimentos Ltda. (locadora)

    CONTRATO DE LOCAÇÃO NÃO RESIDENCIAL Nº LOC-2024/017
    Pelo presente instrumento particular, de um lado IMOBILIÁRIA CORDEIRO EMPREENDIMENTOS
    LTDA., pessoa jurídica de direito privado, inscrita no CNPJ sob o nº 55.173.042/0001-16,
    com sede na Rua Barão de Itamaracá, 540, Espinheiro, Recife/PE, representada por Álvaro
    Cordeiro Ferraz, doravante denominada LOCADORA; e, de outro lado, GRUPO MARÉ ALTA
    LOGÍSTICA LTDA., inscrita no CNPJ sob o nº 08.391.647/0001-01, com sede na Av. Cais do Apolo,
    doravante denominada LOCATÁRIA, têm entre si justo e contratado o que segue:
    CLÁUSULA 1ª - DO OBJETO. Galpão logístico.
    CLÁUSULA 2ª - DO PRAZO. A locação vigorará por 36 meses, de 01/09/2024 a 31/08/2027.
    Seguro-fiança locatícia
    Apólice FL-0034/2024-77120
    Vigência 01/09/2024 a 31/08/2026 (renovável anualmente)
    """
    parties = _extract_contract_parties(text)
    assert parties.supplier == "IMOBILIÁRIA CORDEIRO EMPREENDIMENTOS LTDA."
    assert parties.supplier_cnpj == "55173042000116"
    assert parties.company == "GRUPO MARÉ ALTA LOGÍSTICA LTDA."
    assert parties.client_cnpj == "08391647000101"

    supplier_cnpj, invalid = _extract_supplier_cnpj_info(text)
    assert supplier_cnpj == "55173042000116"
    assert invalid is None

    assert _extract_contract_id_hint(text, "A1_dossie_locacao_controle.pdf") == "LOC-2024/017"
    validity = _extract_contract_validity(text)
    assert validity == ValidityPeriod(start=date(2024, 9, 1), end=date(2027, 8, 31))


def test_preamble_publico_contratante_por_intermedio_e_contratada() -> None:
    text = """
    ESTADO DE PERNAMBUCO
    TERMO DE CONTRATO Nº 018/2024
    Processo Administrativo nº 2024.0387 - Pregão Eletrônico nº 045/2024
    O MUNICÍPIO DE SERRA VERDE, por intermédio da FUNDAÇÃO MUNICIPAL DE SAÚDE DE
    SERRA VERDE, pessoa jurídica de direito público, inscrita no CNPJ sob o nº 11.029.435/0001-85,
    doravante denominada CONTRATANTE, e a empresa LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA.,
    inscrita no CNPJ sob o nº 24.681.035/0001-00, doravante denominada CONTRATADA:
    CLÁUSULA TERCEIRA - DA VIGÊNCIA. O contrato terá vigência de 02/01/2025 a 31/12/2025.
    """
    parties = _extract_contract_parties(text)
    assert parties.company == "FUNDAÇÃO MUNICIPAL DE SAÚDE DE SERRA VERDE"
    assert parties.client_cnpj == "11029435000185"
    assert parties.supplier == "LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA."
    assert parties.supplier_cnpj == "24681035000100"
    assert _extract_contract_id_hint(text, "B1_contrato_018_2024.pdf") == "018/2024"
    assert _extract_contract_validity(text) == ValidityPeriod(
        start=date(2025, 1, 2), end=date(2025, 12, 31)
    )


def test_preamble_ata_orgao_gerenciador_vs_detentora_never_confuses_cnpj() -> None:
    text = """
    ESTADO DE PERNAMBUCO
    INSTITUTO DE PREVIDÊNCIA DOS SERVIDORES DE VALE DOURADO
    ATA DE REGISTRO DE PREÇOS Nº 007/2025
    Pregão Eletrônico nº 012/2025 - Processo nº 2025.0144
    Aos dez dias do mês de março de 2025, o INSTITUTO DE PREVIDÊNCIA DOS SERVIDORES DE VALE
    DOURADO, inscrito no CNPJ sob o nº 29.384.756/0001-63, com sede na Rua do Comércio, 300,
    doravante ÓRGÃO GERENCIADOR, e a empresa PAPELARIA E SUPRIMENTOS BOA VISTA LTDA.,
    inscrita no CNPJ sob o nº 60.271.584/0001-61, doravante DETENTORA, firmam a presente Ata.
    CLÁUSULA 2ª - DA VIGÊNCIA. A Ata terá vigência de 12 (doze) meses, de 10/03/2025 a 09/03/2026.
    """
    parties = _extract_contract_parties(text)
    assert parties.company == "INSTITUTO DE PREVIDÊNCIA DOS SERVIDORES DE VALE DOURADO"
    assert parties.client_cnpj == "29384756000163"
    assert parties.supplier == "PAPELARIA E SUPRIMENTOS BOA VISTA LTDA."
    assert parties.supplier_cnpj == "60271584000161"
    assert _extract_contract_id_hint(text, "C1_ata.pdf") == "ARP-007/2025"


def test_supplier_identity_ignores_placeholders_and_accepts_abbreviations() -> None:
    placeholder_contract = Contract(
        id="LOC-2024/017",
        company="Grupo Maré Alta",
        supplier="Fornecedor Identificado no Documento",
        supplier_cnpj="55173042000116",
        subject="Locação",
        validity=ValidityPeriod(),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    inv = Invoice(
        id="inv-1",
        contract_id="LOC-2024/017",
        supplier="Imobiliária Cordeiro Empreend. Ltda.",
        supplier_cnpj="55173042000116",
        number="2025/001",
        issue_date=date(2024, 12, 24),
        reference_period=ReferencePeriod(year=2025, month=1),
        total="22400.00",
        source=InvoiceSource.PDF,
    )
    assert check_supplier_identity(placeholder_contract, inv) is None

    real_contract = placeholder_contract.model_copy(
        update={"supplier": "IMOBILIÁRIA CORDEIRO EMPREENDIMENTOS LTDA."}
    )
    assert check_supplier_identity(real_contract, inv) is None


def test_wrapped_cnpj_reconstruction_and_ata_link_resolution() -> None:
    page_text = (
        "Imobiliária Cordeiro 55.173.042/0001-1\n"
        "2025/001 jan/2025 24/12/2024 08/01/2025 22.400,00\n"
        "Empreend. Ltda. 6\n"
    )
    assert _try_reconstruct_wrapped_cnpj("55.173.042/0001-1", page_text) == "55173042000116"

    ata = Contract(
        id="ARP-007/2025",
        company="Instituto de Previdência",
        supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA.",
        supplier_cnpj="60271584000161",
        subject="Registro de preços",
        validity=ValidityPeriod(),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    other_contract = Contract(
        id="018/2024",
        company="FMS",
        supplier="LIMPAR MAIS SERVIÇOS LTDA.",
        supplier_cnpj="24681035000100",
        subject="Limpeza",
        validity=ValidityPeriod(),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    rogue_inv = Invoice(
        id="inv-1204",
        contract_id=None,
        supplier="ALFA COMERCIAL DE ESCRITÓRIO LTDA",
        supplier_cnpj="41820397000145",
        number="001204",
        issue_date=date(2025, 11, 25),
        reference_period=ReferencePeriod(year=2025, month=11),
        total="1890.00",
        service_description="Ref. AF-0209 - Ata de Registro de Preços nº 007/2025.",
        source=InvoiceSource.PDF,
    )
    cid, warn = resolve_contract_link([other_contract, ata], rogue_inv)
    assert cid == "ARP-007/2025"
    assert warn is not None and "diverge" in warn


def test_sync_reconciles_auto_approved_quantity_and_multimodal_no_text_ocr() -> None:
    from datetime import UTC, datetime

    from backend.agentes import ExtractedRuleCandidate, FakeLLMClient, JuristaExtractionPayload
    from backend.api import (
        fetch_rules,
        ingest_uploaded_files,
        register_contract,
        review_rule,
        sync_contract_dossier_data,
    )
    from backend.persistencia import init_db, receive_document, save_rule
    from backend.schemas import (
        ContractRule,
        DocumentType,
        Evidence,
        ReadQuality,
        ReviewDecision,
        ReviewStatus,
        RuleRelation,
        RuleType,
    )
    from tests.fixtures.synthetic_files import build_synthetic_pdf

    conn = init_db(":memory:")
    try:
        register_contract(
            conn,
            Contract(
                id="018/2024",
                company="FMS",
                supplier="LIMPAR MAIS SERVIÇOS LTDA.",
                supplier_cnpj="24681035000100",
                subject="Limpeza",
                validity=ValidityPeriod(start=date(2025, 1, 2), end=date(2025, 12, 31)),
                comparison_tolerance=Decimal("0.00"),
                status=ContractStatus.ACTIVE,
            ),
        )
        add_text = (
            "PRIMEIRO TERMO ADITIVO AO CONTRATO Nº 018/2024\n"
            "CLÁUSULA PRIMEIRA. Fica acrescido o quantitativo em 2 (dois) postos de servente de "
            "limpeza, passando de 12 (doze) para 14 (quatorze), com efeitos a partir de "
            "01/04/2025, ao preço unitário de R$ 5.920,00 (cinco mil, novecentos e vinte reais) "
            "por posto/mês.\n"
        )
        receive_document(
            document_id="doc-b2",
            contract_id="018/2024",
            document_type=DocumentType.AMENDMENT,
            file_name="B2_aditivos_1_e_2.pdf",
            content=b"b2",
            received_at=datetime(2025, 12, 1, 10, 0, tzinfo=UTC),
            read_quality=ReadQuality.OK,
            extracted_text=add_text,
            reviewed=True,
            conn=conn,
        )
        wrong_ai_rule = ContractRule(
            id="doc-b2-rule-1",
            contract_id="018/2024",
            rule_type=RuleType.ITEM_PRICE,
            item_name="Servente de limpeza",
            value=Decimal("5920.00"),
            quantity=Decimal("2"),
            unit="posto",
            quantity_scope="MONTHLY",
            validity=ValidityPeriod(start=date(2025, 4, 1)),
            review_status=ReviewStatus.PENDING_REVIEW,
            verification_score=Decimal("1.00"),
            evidence=Evidence(
                source_document_id="doc-b2", page=1, literal_excerpt=add_text.strip()
            ),
            relation=RuleRelation.ALTERED,
        )
        save_rule(conn, wrong_ai_rule)
        review_rule(
            conn,
            wrong_ai_rule.id,
            user="sistema.auto",
            decision=ReviewDecision.CONFIRM,
            justification="Autoaprovada",
        )
        sync_contract_dossier_data(conn, "018/2024", auto_approve_above_threshold=True)
        reconciled = next(r for r in fetch_rules(conn, "018/2024") if r.id == "doc-b2-rule-1")
        assert reconciled.quantity == Decimal("14")

        scanned_bytes = build_synthetic_pdf([[""]])
        ocr_text = (
            "TERMO DE CONTRATO Nº CT-SCAN-001\n"
            "CLÁUSULA 1ª: Valor mensal de R$ 8.500,00 com vigência de 01/01/2026 a 31/12/2026."
        )
        ocr_client = FakeLLMClient(
            [
                JuristaExtractionPayload(
                    rules=(
                        ExtractedRuleCandidate(
                            rule_type=RuleType.MONTHLY_PRICE,
                            new_value=Decimal("8500.00"),
                            validity_start=date(2026, 1, 1),
                            validity_end=date(2026, 12, 31),
                            relation=RuleRelation.CREATED,
                            page=1,
                            literal_excerpt=(
                                "Valor mensal de R$ 8.500,00 com vigência de "
                                "01/01/2026 a 31/12/2026."
                            ),
                        ),
                    )
                )
            ],
            pdf_pages=((1, ocr_text),),
        )
        batch = ingest_uploaded_files(
            conn,
            [("contrato_digitalizado.pdf", scanned_bytes)],
            client=ocr_client,
        )
        assert len(batch.outcomes) == 1
        assert batch.outcomes[0].document is not None
        assert batch.outcomes[0].document.read_quality is ReadQuality.OK
        assert batch.outcomes[0].rules_count == 1
    finally:
        conn.close()
