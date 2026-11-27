"""Testes do Agente Fiscal para PDF (Etapa 5.5)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from backend.agentes import (
    FISCAL_AGENT_PROMPT_VERSION,
    MAX_RETRY_ATTEMPTS,
    ExtractedBillingItemCandidate,
    ExtractedInvoiceEntry,
    FakeLLMClient,
    FiscalExtractionPayload,
    FiscalPdfInput,
    run_fiscal_pdf_agent,
)
from backend.leitura import CPF_MASK, DocxParagraph, DocxReadResult, PdfPage, PdfReadResult
from backend.schemas import DocumentType, InvoiceSource, ReadQuality, ReferencePeriod


def test_fiscal_pdf_extracts_invoice_masks_pii_and_checks_anchoring() -> None:
    pdf_result = PdfReadResult(
        pages=(
            PdfPage(
                page_number=1,
                text=(
                    "Fornecedor Exemplo LTDA - CNPJ 11.222.333/0001-81\n"
                    "Responsável: CPF 123.456.789-01\n"
                    "Nota Fiscal nº 104 - Emissão 05/11/2026 - Competência 2026-10\n"
                    "Contrato CT-2026-001\n"
                    "Mensalidade de suporte: R$ 10.800,00\n"
                    "Total da Nota: R$ 10.800,00"
                ),
            ),
        ),
        read_quality=ReadQuality.OK,
        full_text="...",
    )
    payload = FiscalPdfInput.from_pdf("doc-nf-104", pdf_result, file_hash="b" * 64)
    extracted = FiscalExtractionPayload(
        contract_id="CT-2026-001",
        supplier="Fornecedor Exemplo LTDA",
        supplier_cnpj="11.222.333/0001-81",
        number="104",
        issue_date=date(2026, 11, 5),
        reference_period=ReferencePeriod(year=2026, month=10),
        total=Decimal("10800.00"),
        page=1,
        literal_excerpt="Total da Nota: R$ 10.800,00",
        items=(
            ExtractedBillingItemCandidate(
                description="Mensalidade de suporte",
                quantity=Decimal("1"),
                unit="mês",
                unit_value=Decimal("10800.00"),
                total_value=Decimal("10800.00"),
                literal_excerpt="Mensalidade de suporte: R$ 10.800,00",
            ),
        ),
    )
    client = FakeLLMClient([extracted])

    result = run_fiscal_pdf_agent(
        payload, client, extracted_at=datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    )

    assert "123.456.789-01" not in client.calls[0].user_prompt
    assert CPF_MASK in client.calls[0].user_prompt
    assert result.schema_valid is True
    assert result.anchoring_valid is True
    assert result.warnings == ()
    assert result.metadata.prompt_version == FISCAL_AGENT_PROMPT_VERSION
    assert result.invoice is not None
    assert result.invoice.source is InvoiceSource.PDF
    assert result.invoice.competence_inferred is False
    assert result.invoice.total == Decimal("10800.00")


def test_fiscal_pdf_infers_competence_and_flags_bad_anchoring() -> None:
    pdf_result = PdfReadResult(
        pages=(PdfPage(page_number=1, text="Fatura 108 - Total R$ 10.950,00"),),
        read_quality=ReadQuality.OK,
        full_text="Fatura 108 - Total R$ 10.950,00",
    )
    extracted = FiscalExtractionPayload(
        supplier="Fornecedor Exemplo",
        supplier_cnpj="11.222.333/0001-81",
        number="108",
        issue_date=date(2026, 11, 10),
        reference_period=None,
        total=Decimal("99999.00"),
        page=1,
        literal_excerpt="Total inventado R$ 99.999,00",
    )
    client = FakeLLMClient([extracted])
    result = run_fiscal_pdf_agent(FiscalPdfInput.from_pdf("doc-108", pdf_result), client)

    assert result.invoice is not None
    assert result.invoice.reference_period == ReferencePeriod(year=2026, month=11)
    assert result.invoice.competence_inferred is True
    assert result.anchoring_valid is False
    assert any("inferida" in msg.lower() for msg in result.warnings)
    assert any("ancoragem" in msg.lower() for msg in result.warnings)


def test_fiscal_pdf_retries_invalid_schema_and_warns_without_silent_drop() -> None:
    pdf_result = PdfReadResult(
        pages=(PdfPage(page_number=1, text="Fatura com texto válido."),),
        read_quality=ReadQuality.OK,
        full_text="Fatura com texto válido.",
    )
    client = FakeLLMClient(["{}", "invalido", '{"total": "nao"}'])
    result = run_fiscal_pdf_agent(FiscalPdfInput.from_pdf("doc-bad", pdf_result), client)

    assert result.invoice is None
    assert result.schema_valid is False
    assert result.attempts == MAX_RETRY_ATTEMPTS
    assert len(client.calls) == MAX_RETRY_ATTEMPTS
    assert result.warnings


def test_fiscal_pdf_skips_no_text_and_rejects_xml_or_spreadsheets() -> None:
    no_text = PdfReadResult(
        pages=(PdfPage(page_number=1, text=""),),
        read_quality=ReadQuality.NO_TEXT,
        full_text="",
    )
    client = FakeLLMClient([])
    res_no_text = run_fiscal_pdf_agent(FiscalPdfInput.from_pdf("doc-scan", no_text), client)
    assert res_no_text.invoice is None
    assert len(client.calls) == 0
    assert res_no_text.warnings

    for non_pdf in (DocumentType.NFE_XML, DocumentType.SPREADSHEET):
        wrong_input = FiscalPdfInput(
            document_id="doc-xml",
            document_type=non_pdf,
            pages=(PdfPage(page_number=1, text="conteudo"),),
        )
        with pytest.raises(ValueError, match="parsers determinísticos"):
            run_fiscal_pdf_agent(wrong_input, client)


def test_fiscal_input_from_docx_extracts_invoice_with_anchoring() -> None:
    docx_res = DocxReadResult(
        paragraphs=(
            DocxParagraph(
                clause="Fatura",
                paragraph="1",
                text=(
                    "Fatura nº 501 - Contrato CT-2026-500 - CNPJ 11.222.333/0001-81 - "
                    "Competência 2026-08 - Mensalidade: R$ 12.000,00 - Total: R$ 12.000,00"
                ),
            ),
        ),
        read_quality=ReadQuality.OK,
        full_text=(
            "Fatura nº 501 - Contrato CT-2026-500 - CNPJ 11.222.333/0001-81 - "
            "Competência 2026-08 - Mensalidade: R$ 12.000,00 - Total: R$ 12.000,00"
        ),
    )
    payload = FiscalPdfInput.from_docx("doc-docx-501", docx_res, file_hash="c" * 64)
    extracted = FiscalExtractionPayload(
        contract_id="CT-2026-500",
        supplier="Fornecedor Exemplo LTDA",
        supplier_cnpj="11.222.333/0001-81",
        number="501",
        issue_date=date(2026, 8, 30),
        reference_period=ReferencePeriod(year=2026, month=8),
        total=Decimal("12000.00"),
        page=1,
        literal_excerpt="Total: R$ 12.000,00",
        items=(
            ExtractedBillingItemCandidate(
                description="Mensalidade de suporte",
                quantity=Decimal("1"),
                unit="mês",
                unit_value=Decimal("12000.00"),
                total_value=Decimal("12000.00"),
                literal_excerpt="Mensalidade: R$ 12.000,00",
            ),
        ),
    )
    res = run_fiscal_pdf_agent(payload, FakeLLMClient([extracted]))
    assert res.schema_valid is True
    assert res.anchoring_valid is True
    assert res.invoice is not None
    assert res.invoice.number == "501"
    assert res.invoice.total == Decimal("12000.00")


def test_fiscal_extracts_multiple_invoices_and_warns_on_masked_cnpj() -> None:
    pdf_result = PdfReadResult(
        pages=(
            PdfPage(
                page_number=1,
                text=(
                    "DEMONSTRATIVO DE FATURAMENTO\n"
                    "CNPJ do fornecedor: 45.781.236/0001-??\n"
                    "NS-2026/091 30/09/2026 09/2026 Mensalidade R$ 18.750,00\n"
                    "NS-2026/102 31/10/2026 10/2026 Mensalidade R$ 18.750,00\n"
                    "NS-2026/114 30/11/2026 11/2026 Plantão adicional R$ 900,00"
                ),
            ),
        ),
        read_quality=ReadQuality.OK,
        full_text="...",
    )
    payload = FiscalPdfInput.from_pdf("doc-multi", pdf_result, file_hash="d" * 64)
    extracted = FiscalExtractionPayload(
        contract_id="CS-047/2026",
        supplier="Norte Sul Ltda.",
        supplier_cnpj="45.781.236/0001-??",
        number="NS-2026/091",
        issue_date=date(2026, 9, 30),
        reference_period=ReferencePeriod(year=2026, month=9),
        total=Decimal("18750.00"),
        page=1,
        literal_excerpt="NS-2026/091 30/09/2026 09/2026 Mensalidade R$ 18.750,00",
        items=(
            ExtractedBillingItemCandidate(
                description="Mensalidade",
                quantity=Decimal("1"),
                unit="mês",
                unit_value=Decimal("18750.00"),
                total_value=Decimal("18750.00"),
                literal_excerpt="NS-2026/091 30/09/2026 09/2026 Mensalidade R$ 18.750,00",
            ),
        ),
        additional_invoices=(
            ExtractedInvoiceEntry(
                contract_id="CS-047/2026",
                supplier="Norte Sul Ltda.",
                supplier_cnpj="45.781.236/0001-??",
                number="NS-2026/102",
                issue_date=date(2026, 10, 31),
                reference_period=ReferencePeriod(year=2026, month=10),
                total=Decimal("18750.00"),
                page=1,
                literal_excerpt="NS-2026/102 31/10/2026 10/2026 Mensalidade R$ 18.750,00",
                items=(
                    ExtractedBillingItemCandidate(
                        description="Mensalidade",
                        quantity=Decimal("1"),
                        unit="mês",
                        unit_value=Decimal("18750.00"),
                        total_value=Decimal("18750.00"),
                        literal_excerpt="NS-2026/102 31/10/2026 10/2026 Mensalidade R$ 18.750,00",
                    ),
                ),
            ),
            ExtractedInvoiceEntry(
                contract_id="CS-047/2026",
                supplier="Norte Sul Ltda.",
                supplier_cnpj=None,
                number="NS-2026/114",
                issue_date=date(2026, 11, 30),
                reference_period=ReferencePeriod(year=2026, month=11),
                total=Decimal("900.00"),
                page=1,
                literal_excerpt="NS-2026/114 30/11/2026 11/2026 Plantão adicional R$ 900,00",
                items=(
                    ExtractedBillingItemCandidate(
                        description="Plantão adicional",
                        quantity=Decimal("1"),
                        unit="un",
                        unit_value=Decimal("900.00"),
                        total_value=Decimal("900.00"),
                        literal_excerpt=(
                            "NS-2026/114 30/11/2026 11/2026 Plantão adicional R$ 900,00"
                        ),
                    ),
                ),
            ),
        ),
    )
    res = run_fiscal_pdf_agent(payload, FakeLLMClient([extracted]))
    assert res.schema_valid is True
    assert res.anchoring_valid is True
    assert [inv.number for inv in res.invoices] == [
        "NS-2026/091",
        "NS-2026/102",
        "NS-2026/114",
    ]
    assert all(inv.supplier_cnpj == "00000000000191" for inv in res.invoices)
    assert any("45.781.236/0001-??" in msg for msg in res.warnings)
    assert any("ausente" in msg for msg in res.warnings)
