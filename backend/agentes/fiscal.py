"""Agente Fiscal para faturas em PDF (XML e planilhas usam os parsers do Épico 4)."""

from datetime import UTC, datetime

from pydantic import Field, ValidationError

from backend.agentes.anchoring import excerpt_exists_in_document, extract_numbers_from_text
from backend.agentes.client import LLMClient
from backend.agentes.jurista import MAX_RETRY_ATTEMPTS, ExtractionMetadata
from backend.agentes.prompts import (
    FISCAL_AGENT_PROMPT_VERSION,
    FISCAL_AGENT_SYSTEM_PROMPT,
    FISCAL_AGENT_USER_TEMPLATE,
)
from backend.leitura import PdfPage, PdfReadResult, mask_text
from backend.schemas import (
    BillingItem,
    Cnpj,
    DocumentType,
    Invoice,
    InvoiceSource,
    Money,
    Quantity,
    ReadQuality,
    ReferencePeriod,
    StrictModel,
    UnitPrice,
)
from backend.schemas.types import EntityId, NonEmptyStr, PlainDate

_NO_TEXT_WARNING = (
    "PDF de cobrança sem camada de texto legível; o Agente Fiscal não processou o arquivo."
)


class ExtractedBillingItemCandidate(StrictModel):
    """Item de fatura extraído pelo modelo a partir de PDF, com trecho literal."""

    description: NonEmptyStr
    quantity: Quantity
    unit: NonEmptyStr | None = None
    unit_value: UnitPrice
    total_value: Money
    literal_excerpt: NonEmptyStr


class FiscalExtractionPayload(StrictModel):
    """Saída estruturada devolvida pelo modelo na leitura de fatura em PDF."""

    contract_id: EntityId | None = None
    supplier: NonEmptyStr
    supplier_cnpj: Cnpj
    number: NonEmptyStr
    issue_date: PlainDate
    reference_period: ReferencePeriod | None = None
    total: Money
    measured_hours: Quantity | None = None
    page: int = Field(ge=1)
    literal_excerpt: NonEmptyStr
    items: tuple[ExtractedBillingItemCandidate, ...] = ()


class FiscalPdfInput(StrictModel):
    """Entrada tipada para o Agente Fiscal (exclusiva para PDFs)."""

    document_id: EntityId
    document_type: DocumentType = DocumentType.INVOICE
    file_hash: str | None = None
    read_quality: ReadQuality = ReadQuality.OK
    pages: tuple[PdfPage, ...] = ()

    @classmethod
    def from_pdf(
        cls,
        document_id: str,
        pdf_result: PdfReadResult,
        *,
        file_hash: str | None = None,
    ) -> "FiscalPdfInput":
        """Constrói entrada do Fiscal a partir do resultado de `read_pdf`."""
        return cls(
            document_id=document_id,
            document_type=DocumentType.INVOICE,
            file_hash=file_hash,
            read_quality=pdf_result.read_quality,
            pages=pdf_result.pages,
        )


class FiscalResult(StrictModel):
    """Resultado do Agente Fiscal para PDF: `Invoice` validada, ancoragem e avisos."""

    invoice: Invoice | None = None
    anchoring_valid: bool = True
    schema_valid: bool = True
    warnings: tuple[str, ...] = ()
    attempts: int = Field(default=0, ge=0)
    metadata: ExtractionMetadata


def run_fiscal_pdf_agent(
    payload: FiscalPdfInput,
    client: LLMClient,
    *,
    max_retries: int = MAX_RETRY_ATTEMPTS,
    extracted_at: datetime | None = None,
) -> FiscalResult:
    """
    Extrai cobrança de PDF usando LLM com máscara de CPF/RG e validação rígida.
    XML e planilhas são recusados explicitamente para garantir uso dos parsers determinísticos.
    """
    if max_retries < 1:
        raise ValueError("max_retries deve ser maior ou igual a 1")
    if payload.document_type in (DocumentType.NFE_XML, DocumentType.SPREADSHEET):
        raise ValueError(
            "XML e planilhas não passam pelo modelo de linguagem; use os parsers "
            "determinísticos de backend.leitura."
        )

    timestamp = extracted_at if extracted_at is not None else datetime.now(UTC)
    metadata = ExtractionMetadata(
        prompt_version=FISCAL_AGENT_PROMPT_VERSION,
        model_name=client.model_name,
        source_document_id=payload.document_id,
        file_hash=payload.file_hash,
        extracted_at=timestamp,
    )

    if payload.read_quality is ReadQuality.NO_TEXT or not payload.pages:
        return FiscalResult(
            invoice=None,
            anchoring_valid=False,
            schema_valid=True,
            warnings=(_NO_TEXT_WARNING,),
            attempts=0,
            metadata=metadata,
        )

    masked_pages = {page.page_number: mask_text(page.text) for page in payload.pages}
    document_text = "\n\n".join(
        f"[Página {page.page_number}]\n{masked_pages[page.page_number]}" for page in payload.pages
    )
    user_prompt = FISCAL_AGENT_USER_TEMPLATE.format(document_text=document_text)

    parsed: FiscalExtractionPayload | None = None
    last_error = ""
    attempts = 0
    for attempt in range(1, max_retries + 1):
        attempts = attempt
        response = client.generate(
            system_prompt=FISCAL_AGENT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=FiscalExtractionPayload,
        )
        try:
            parsed = FiscalExtractionPayload.model_validate_json(response.content)
            break
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)

    if parsed is None:
        return FiscalResult(
            invoice=None,
            anchoring_valid=False,
            schema_valid=False,
            warnings=(
                f"Falha na validação de esquema da fatura após {attempts} tentativa(s): "
                f"extração descartada ({last_error}).",
            ),
            attempts=attempts,
            metadata=metadata,
        )

    warnings: list[str] = []
    competence_inferred = parsed.reference_period is None
    reference_period = (
        parsed.reference_period
        if parsed.reference_period is not None
        else ReferencePeriod(year=parsed.issue_date.year, month=parsed.issue_date.month)
    )
    if competence_inferred:
        warnings.append(
            f"Competência {reference_period} inferida pela data de emissão "
            f"({parsed.issue_date.isoformat()}); requer revisão humana."
        )

    anchoring_valid = excerpt_exists_in_document(
        parsed.literal_excerpt,
        document_text,
        page=parsed.page,
        pages_by_number=masked_pages,
    ) and parsed.total in extract_numbers_from_text(parsed.literal_excerpt)

    for item in parsed.items:
        item_ok = excerpt_exists_in_document(
            item.literal_excerpt, document_text
        ) and item.total_value in extract_numbers_from_text(item.literal_excerpt)
        if not item_ok:
            anchoring_valid = False

    if not anchoring_valid:
        warnings.append(
            "A ancoragem documental da fatura em PDF falhou; confirme os valores e trechos "
            "manualmente antes da auditoria."
        )

    items = tuple(
        BillingItem(
            description=item.description,
            quantity=item.quantity,
            unit=item.unit,
            unit_value=item.unit_value,
            total_value=item.total_value,
        )
        for item in parsed.items
    )
    invoice = Invoice(
        id=f"pdf-{payload.document_id}",
        contract_id=parsed.contract_id,
        supplier=parsed.supplier,
        supplier_cnpj=parsed.supplier_cnpj,
        number=parsed.number,
        issue_date=parsed.issue_date,
        reference_period=reference_period,
        competence_inferred=competence_inferred,
        total=parsed.total,
        measured_hours=parsed.measured_hours,
        source=InvoiceSource.PDF,
        items=items,
    )
    return FiscalResult(
        invoice=invoice,
        anchoring_valid=anchoring_valid,
        schema_valid=True,
        warnings=tuple(warnings),
        attempts=attempts,
        metadata=metadata,
    )
