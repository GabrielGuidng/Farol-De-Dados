"""Agente Fiscal para faturas em PDF (XML e planilhas usam os parsers do Épico 4)."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Self

from pydantic import Field, ValidationError

from backend.agentes.anchoring import excerpt_exists_in_document, extract_numbers_from_text
from backend.agentes.client import LLMClient
from backend.agentes.jurista import MAX_RETRY_ATTEMPTS, ExtractionMetadata
from backend.agentes.prompts import (
    FISCAL_AGENT_PROMPT_VERSION,
    FISCAL_AGENT_SYSTEM_PROMPT,
    FISCAL_AGENT_USER_TEMPLATE,
)
from backend.leitura import DocxReadResult, PdfPage, PdfReadResult, mask_text
from backend.schemas import (
    BillingItem,
    DocumentType,
    Invoice,
    InvoiceSource,
    Money,
    Quantity,
    ReadQuality,
    ReferencePeriod,
    StrictModel,
    is_valid_cnpj,
    normalize_cnpj,
)
from backend.schemas.types import EntityId, NonEmptyStr, PlainDate

_NO_TEXT_WARNING = (
    "PDF de cobrança sem camada de texto legível; o Agente Fiscal não processou o arquivo."
)
_PROVISIONAL_FISCAL_CNPJ = "00000000000191"
_DADOS_ADICIONAIS_RE = re.compile(
    r"(?is)DADOS\s+ADICIONAIS\s*\n\s*(?P<txt>[^\n]+(?:\n(?!(?:DANFE|NF-e|P[áa]g\.))[^\n]+)?)"
)
_PAGE_CONTRACT_REF_RE = re.compile(
    r"(?i)\b(?:Ata\s+de\s+Registro\s+de\s+Pre[çc]os|Contrato(?:\s+de\s+Loca[çc][ãa]o"
    r"(?:\s+N[ãa]o\s+Residencial)?)?|Termo\s+de\s+Contrato|ARP)\s*(?:n[º°.]?|n[úu]mero)?\s*"
    r"([A-Z]{0,6}-?\d{2,6}/\d{4})\b"
)
_DISCOUNT_WORD_RE = re.compile(r"(?i)\b(?:desconto|glosa|abatimento|sla)\b")


class ExtractedBillingItemCandidate(StrictModel):
    """Item de fatura extraído pelo modelo a partir de PDF, com trecho literal."""

    description: NonEmptyStr
    quantity: Quantity
    unit: NonEmptyStr | None = None
    unit_value: Decimal
    total_value: Decimal
    literal_excerpt: NonEmptyStr


class ExtractedInvoiceEntry(StrictModel):
    """Entrada estruturada de fatura/nota fiscal extraída pelo Agente Fiscal."""

    contract_id: EntityId | None = None
    supplier: NonEmptyStr
    supplier_cnpj: str | None = None
    supplier_legal_nature: str | None = None
    number: NonEmptyStr
    issue_date: PlainDate
    attestation_date: PlainDate | None = None
    reference_period: ReferencePeriod | None = None
    total: Money
    inss_withheld: Money | None = None
    iss_withheld: Money | None = None
    net_total: Money | None = None
    service_description: str | None = None
    measured_hours: Quantity | None = None
    page: int = Field(default=1, ge=1)
    literal_excerpt: NonEmptyStr
    items: tuple[ExtractedBillingItemCandidate, ...] = ()


class FiscalExtractionPayload(StrictModel):
    """Saída estruturada devolvida pelo modelo na leitura de fatura(s) em PDF/DOCX."""

    contract_id: EntityId | None = None
    supplier: NonEmptyStr
    supplier_cnpj: str | None = None
    supplier_legal_nature: str | None = None
    number: NonEmptyStr
    issue_date: PlainDate
    attestation_date: PlainDate | None = None
    reference_period: ReferencePeriod | None = None
    total: Money
    inss_withheld: Money | None = None
    iss_withheld: Money | None = None
    net_total: Money | None = None
    service_description: str | None = None
    measured_hours: Quantity | None = None
    page: int = Field(ge=1)
    literal_excerpt: NonEmptyStr
    items: tuple[ExtractedBillingItemCandidate, ...] = ()
    additional_invoices: tuple[ExtractedInvoiceEntry, ...] = ()


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
    ) -> Self:
        """Constrói entrada do Fiscal a partir do resultado de `read_pdf`."""
        return cls(
            document_id=document_id,
            document_type=DocumentType.INVOICE,
            file_hash=file_hash,
            read_quality=pdf_result.read_quality,
            pages=pdf_result.pages,
        )

    @classmethod
    def from_docx(
        cls,
        document_id: str,
        docx_result: DocxReadResult,
        *,
        file_hash: str | None = None,
    ) -> Self:
        """Constrói entrada do Fiscal a partir do resultado de `read_docx`."""
        has_text = docx_result.read_quality is ReadQuality.OK and bool(
            docx_result.full_text.strip()
        )
        pages = (PdfPage(page_number=1, text=docx_result.full_text),) if has_text else ()
        return cls(
            document_id=document_id,
            document_type=DocumentType.INVOICE,
            file_hash=file_hash,
            read_quality=docx_result.read_quality if has_text else ReadQuality.NO_TEXT,
            pages=pages,
        )


class FiscalResult(StrictModel):
    """Resultado do Agente Fiscal para PDF/DOCX: `Invoice`(s) validadas, ancoragem e avisos."""

    invoice: Invoice | None = None
    invoices: tuple[Invoice, ...] = ()
    anchoring_valid: bool = True
    schema_valid: bool = True
    warnings: tuple[str, ...] = ()
    attempts: int = Field(default=0, ge=0)
    metadata: ExtractionMetadata


def _is_goods_danfe_page(page_text: str, service_description: str | None) -> bool:
    combined = f"{page_text}\n{service_description or ''}".casefold()
    if (
        "nfs-e" in combined
        or "nota fiscal de serviços" in combined
        or "nota fiscal de servicos" in combined
    ):
        return False
    return any(
        marker in combined
        for marker in ("danfe", "venda de mercadoria", "entrega conforme af", "af-")
    )


def _enrich_service_description_from_page(
    current: str | None,
    page_text: str,
) -> str | None:
    if not page_text.strip():
        return current
    additions: list[str] = []
    m_dados = _DADOS_ADICIONAIS_RE.search(page_text)
    if m_dados is not None:
        raw_dados = " ".join(m_dados.group("txt").split())
        if raw_dados and (current is None or raw_dados.casefold() not in current.casefold()):
            additions.append(raw_dados)
    for m_ref in _PAGE_CONTRACT_REF_RE.finditer(page_text):
        snippet = " ".join(m_ref.group(0).split())
        if current is None or m_ref.group(1).casefold() not in current.casefold():
            if all(m_ref.group(1).casefold() not in a.casefold() for a in additions):
                additions.append(snippet)
    if not additions:
        return current
    if current and current.strip():
        return f"{current.strip()} | {' | '.join(additions)}"
    return " | ".join(additions)


def _try_reconstruct_wrapped_cnpj(raw_cnpj: str, page_text: str) -> str | None:
    digits = normalize_cnpj(raw_cnpj)
    if len(digits) != 13 or not page_text:
        return None
    formatted_13 = rf"{digits[:2]}\.?{digits[2:5]}\.?{digits[5:8]}/?{digits[8:12]}-?{digits[12]}"
    for m in re.finditer(formatted_13 + r"(?![0-9])([\s\S]{0,90})", page_text):
        tail_digits = re.findall(r"(?<![0-9/.-])(\d)\b", m.group(1))
        for td in tail_digits[:2]:
            candidate = digits + td
            if is_valid_cnpj(candidate):
                return candidate
    return None


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

    raw_entries: list[ExtractedInvoiceEntry] = [
        ExtractedInvoiceEntry(
            contract_id=parsed.contract_id,
            supplier=parsed.supplier,
            supplier_cnpj=parsed.supplier_cnpj,
            supplier_legal_nature=parsed.supplier_legal_nature,
            number=parsed.number,
            issue_date=parsed.issue_date,
            attestation_date=parsed.attestation_date,
            reference_period=parsed.reference_period,
            total=parsed.total,
            inss_withheld=parsed.inss_withheld,
            iss_withheld=parsed.iss_withheld,
            net_total=parsed.net_total,
            service_description=parsed.service_description,
            measured_hours=parsed.measured_hours,
            page=parsed.page,
            literal_excerpt=parsed.literal_excerpt,
            items=parsed.items,
        ),
        *parsed.additional_invoices,
    ]

    warnings: list[str] = []
    anchoring_valid = True
    built_invoices: list[Invoice] = []

    raw_pages_by_num = {page.page_number: page.text for page in payload.pages}
    for idx, entry in enumerate(raw_entries, start=1):
        page_text = raw_pages_by_num.get(entry.page, "")
        enriched_desc = _enrich_service_description_from_page(entry.service_description, page_text)
        competence_inferred = entry.reference_period is None and not _is_goods_danfe_page(
            page_text, enriched_desc
        )
        reference_period = (
            entry.reference_period
            if entry.reference_period is not None
            else ReferencePeriod(year=entry.issue_date.year, month=entry.issue_date.month)
        )
        if competence_inferred:
            warnings.append(
                f"Competência {reference_period} inferida pela data de emissão "
                f"({entry.issue_date.isoformat()}) na cobrança {entry.number}; requer revisão "
                "humana."
            )

        raw_cnpj = (entry.supplier_cnpj or "").strip()
        if raw_cnpj and not is_valid_cnpj(raw_cnpj):
            rebuilt = _try_reconstruct_wrapped_cnpj(raw_cnpj, page_text)
            if rebuilt is not None:
                resolved_cnpj = rebuilt
                raw_cnpj = rebuilt
            else:
                resolved_cnpj = _PROVISIONAL_FISCAL_CNPJ
                warnings.append(
                    f"CNPJ do emitente na cobrança {entry.number} ('{raw_cnpj}') está incompleto "
                    "ou inválido; utilizado CNPJ provisório para permitir revisão e auditoria."
                )
        elif raw_cnpj and is_valid_cnpj(raw_cnpj):
            resolved_cnpj = normalize_cnpj(raw_cnpj)
        else:
            resolved_cnpj = _PROVISIONAL_FISCAL_CNPJ
            warnings.append(
                f"CNPJ do emitente ausente na cobrança {entry.number}; utilizado CNPJ "
                "provisório para permitir revisão e auditoria."
            )

        entry_anchoring = excerpt_exists_in_document(
            entry.literal_excerpt,
            document_text,
            page=entry.page,
            pages_by_number=masked_pages,
        ) and entry.total in extract_numbers_from_text(entry.literal_excerpt)

        for item in entry.items:
            item_ok = excerpt_exists_in_document(item.literal_excerpt, document_text) and abs(
                item.total_value
            ) in extract_numbers_from_text(item.literal_excerpt)
            if not item_ok:
                entry_anchoring = False
        if not entry_anchoring:
            anchoring_valid = False

        items: list[BillingItem] = []
        for item in entry.items:
            is_negative = item.total_value < Decimal("0") or item.unit_value < Decimal("0")
            desc = item.description
            if is_negative and not _DISCOUNT_WORD_RE.search(desc):
                desc = f"Desconto / glosa — {desc}"
            items.append(
                BillingItem(
                    description=desc,
                    quantity=abs(item.quantity),
                    unit=item.unit,
                    unit_value=abs(item.unit_value),
                    total_value=abs(item.total_value),
                )
            )
        inv_id = f"pdf-{payload.document_id}" if idx == 1 else f"pdf-{payload.document_id}-{idx}"
        built_invoices.append(
            Invoice(
                id=inv_id,
                contract_id=entry.contract_id,
                supplier=entry.supplier,
                supplier_cnpj=resolved_cnpj,
                raw_supplier_cnpj=raw_cnpj or None,
                supplier_legal_nature=entry.supplier_legal_nature,
                number=entry.number,
                issue_date=entry.issue_date,
                attestation_date=entry.attestation_date,
                reference_period=reference_period,
                competence_inferred=competence_inferred,
                total=entry.total,
                inss_withheld=entry.inss_withheld,
                iss_withheld=entry.iss_withheld,
                net_total=entry.net_total,
                service_description=enriched_desc,
                measured_hours=entry.measured_hours,
                source=InvoiceSource.PDF,
                items=tuple(items),
            )
        )

    if not anchoring_valid:
        warnings.append(
            "A ancoragem documental da fatura em PDF falhou; confirme os valores e trechos "
            "manualmente antes da auditoria."
        )

    return FiscalResult(
        invoice=built_invoices[0] if built_invoices else None,
        invoices=tuple(built_invoices),
        anchoring_valid=anchoring_valid,
        schema_valid=True,
        warnings=tuple(warnings),
        attempts=attempts,
        metadata=metadata,
    )
