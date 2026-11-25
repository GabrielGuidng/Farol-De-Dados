"""Agente Jurista: extrai regras contratuais e submete cada uma à verificação de ancoragem."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Self

from pydantic import Field, ValidationError, model_validator

from backend.agentes.anchoring import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    AnchoringCheckResult,
    verify_rule_anchoring,
)
from backend.agentes.client import LLMClient
from backend.agentes.prompts import (
    LEGAL_AGENT_PROMPT_VERSION,
    LEGAL_AGENT_SYSTEM_PROMPT,
    LEGAL_AGENT_USER_TEMPLATE,
)
from backend.leitura import DocxParagraph, DocxReadResult, PdfPage, PdfReadResult, mask_text
from backend.schemas import (
    ContractRule,
    Evidence,
    Quantity,
    ReadQuality,
    ReviewStatus,
    RuleRelation,
    RuleType,
    StrictModel,
    UnitPrice,
    ValidityPeriod,
)
from backend.schemas.types import EntityId, NonEmptyStr, PlainDate

MAX_RETRY_ATTEMPTS = 3

_NO_TEXT_WARNING = (
    "Documento sem camada de texto legível; o Agente Jurista não processou o arquivo e "
    "ele permanece não revisado."
)
_NO_RULES_WARNING = (
    "Nenhuma regra foi extraída do documento; o sistema não presume que nada mudou e o "
    "documento permanece não revisado."
)


class ExtractionMetadata(StrictModel):
    """Rastreabilidade da execução do agente: prompt, modelo, documento, hash e horário."""

    prompt_version: NonEmptyStr
    model_name: NonEmptyStr
    source_document_id: EntityId
    file_hash: str | None = None
    extracted_at: datetime


class ExtractedRuleCandidate(StrictModel):
    """Regra candidata devolvida pelo modelo antes da verificação por código."""

    rule_type: RuleType
    new_value: UnitPrice | None = None
    previous_value: UnitPrice | None = None
    quantity: Quantity | None = None
    unit: NonEmptyStr | None = None
    adjustment_index: NonEmptyStr | None = None
    periodicity_months: int | None = Field(default=None, ge=1)
    validity_start: PlainDate | None = None
    validity_end: PlainDate | None = None
    relation: RuleRelation = RuleRelation.CREATED
    page: int | None = Field(default=None, ge=1)
    clause: NonEmptyStr | None = None
    paragraph: NonEmptyStr | None = None
    literal_excerpt: NonEmptyStr

    @model_validator(mode="after")
    def _validate_locator_and_required_fields(self) -> Self:
        if self.page is None and (self.clause is None or self.paragraph is None):
            raise ValueError("regra extraída exige página ou cláusula e parágrafo")
        if self.rule_type in (RuleType.MONTHLY_PRICE, RuleType.OVERAGE_PRICE) and (
            self.new_value is None
        ):
            raise ValueError("regra de preço exige new_value")
        if self.rule_type is RuleType.FRANCHISE and self.quantity is None:
            raise ValueError("franquia exige quantity")
        if self.rule_type is RuleType.ADJUSTMENT and self.adjustment_index is None:
            raise ValueError("reajuste exige adjustment_index")
        if self.rule_type is RuleType.EXTENSION and self.validity_end is None:
            raise ValueError("prorrogação exige validity_end")
        return self


class JuristaExtractionPayload(StrictModel):
    """Envelope estruturado da resposta do Agente Jurista."""

    rules: tuple[ExtractedRuleCandidate, ...] = ()


class JuristaInput(StrictModel):
    """Entrada tipada do Agente Jurista a partir de PDF paginado ou DOCX por cláusula."""

    document_id: EntityId
    contract_id: EntityId
    file_hash: str | None = None
    read_quality: ReadQuality = ReadQuality.OK
    pages: tuple[PdfPage, ...] = ()
    docx_paragraphs: tuple[DocxParagraph, ...] = ()
    is_amendment: bool = False

    @classmethod
    def from_pdf(
        cls,
        document_id: str,
        contract_id: str,
        pdf_result: PdfReadResult,
        *,
        file_hash: str | None = None,
        is_amendment: bool = False,
    ) -> Self:
        """Constrói entrada do Jurista a partir do resultado de `read_pdf`."""
        return cls(
            document_id=document_id,
            contract_id=contract_id,
            file_hash=file_hash,
            read_quality=pdf_result.read_quality,
            pages=pdf_result.pages,
            is_amendment=is_amendment,
        )

    @classmethod
    def from_docx(
        cls,
        document_id: str,
        contract_id: str,
        docx_result: DocxReadResult,
        *,
        file_hash: str | None = None,
        is_amendment: bool = False,
    ) -> Self:
        """Constrói entrada do Jurista a partir do resultado de `read_docx`."""
        return cls(
            document_id=document_id,
            contract_id=contract_id,
            file_hash=file_hash,
            read_quality=docx_result.read_quality,
            docx_paragraphs=docx_result.paragraphs,
            is_amendment=is_amendment,
        )


class JuristaResult(StrictModel):
    """Saída tipada do Agente Jurista com regras pendentes/inconclusivas e avisos."""

    rules: tuple[ContractRule, ...] = ()
    anchoring_results: tuple[AnchoringCheckResult, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_valid: bool = True
    document_reviewed: bool = False
    attempts: int = Field(default=0, ge=0)
    metadata: ExtractionMetadata


def _prepare_masked_content(payload: JuristaInput) -> tuple[str, dict[int, str] | None]:
    if payload.pages:
        masked_pages = {page.page_number: mask_text(page.text) for page in payload.pages}
        blocks = [
            f"[Página {page.page_number}]\n{masked_pages[page.page_number]}"
            for page in payload.pages
        ]
        return "\n\n".join(blocks), masked_pages

    blocks = [
        f"[Cláusula {para.clause} | Parágrafo {para.paragraph}]\n{mask_text(para.text)}"
        for para in payload.docx_paragraphs
    ]
    return "\n\n".join(blocks), None


def run_jurista_agent(
    payload: JuristaInput,
    client: LLMClient,
    *,
    max_retries: int = MAX_RETRY_ATTEMPTS,
    confidence_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    extracted_at: datetime | None = None,
) -> JuristaResult:
    """
    Executa o Agente Jurista: mascara CPF/RG, delimita o texto, valida o esquema com retries e
    confere a ancoragem por código. Nunca devolve regra CONFIRMED.
    """
    if max_retries < 1:
        raise ValueError("max_retries deve ser maior ou igual a 1")
    timestamp = extracted_at if extracted_at is not None else datetime.now(UTC)
    metadata = ExtractionMetadata(
        prompt_version=LEGAL_AGENT_PROMPT_VERSION,
        model_name=client.model_name,
        source_document_id=payload.document_id,
        file_hash=payload.file_hash,
        extracted_at=timestamp,
    )

    if payload.read_quality is ReadQuality.NO_TEXT or (
        not payload.pages and not payload.docx_paragraphs
    ):
        return JuristaResult(
            rules=(),
            anchoring_results=(),
            warnings=(_NO_TEXT_WARNING,),
            schema_valid=True,
            document_reviewed=False,
            attempts=0,
            metadata=metadata,
        )

    document_text, masked_pages = _prepare_masked_content(payload)
    document_kind = "termo aditivo" if payload.is_amendment else "contrato"
    user_prompt = LEGAL_AGENT_USER_TEMPLATE.format(
        document_kind=document_kind,
        document_text=document_text,
    )

    parsed: JuristaExtractionPayload | None = None
    last_error = ""
    attempts = 0
    for attempt in range(1, max_retries + 1):
        attempts = attempt
        response = client.generate(
            system_prompt=LEGAL_AGENT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=JuristaExtractionPayload,
        )
        try:
            parsed = JuristaExtractionPayload.model_validate_json(response.content)
            break
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)

    if parsed is None:
        return JuristaResult(
            rules=(),
            anchoring_results=(),
            warnings=(
                f"Falha na validação de esquema após {attempts} tentativa(s): a extração foi "
                f"descartada e o documento permanece sem revisão ({last_error}).",
            ),
            schema_valid=False,
            document_reviewed=False,
            attempts=attempts,
            metadata=metadata,
        )

    if not parsed.rules:
        return JuristaResult(
            rules=(),
            anchoring_results=(),
            warnings=(_NO_RULES_WARNING,),
            schema_valid=True,
            document_reviewed=False,
            attempts=attempts,
            metadata=metadata,
        )

    rules: list[ContractRule] = []
    anchoring_results: list[AnchoringCheckResult] = []
    warnings: list[str] = []

    for index, candidate in enumerate(parsed.rules, start=1):
        anchoring = verify_rule_anchoring(
            rule_type=candidate.rule_type,
            literal_excerpt=candidate.literal_excerpt,
            document_text=document_text,
            new_value=candidate.new_value,
            previous_value=candidate.previous_value,
            quantity=candidate.quantity,
            adjustment_index=candidate.adjustment_index,
            validity_end=candidate.validity_end,
            page=candidate.page,
            pages_by_number=masked_pages,
            confidence_threshold=confidence_threshold,
        )
        evidence = Evidence(
            source_document_id=payload.document_id,
            page=candidate.page,
            clause=candidate.clause,
            paragraph=candidate.paragraph,
            literal_excerpt=candidate.literal_excerpt,
        )
        rule = ContractRule(
            id=f"{payload.document_id}-rule-{index}",
            contract_id=payload.contract_id,
            rule_type=candidate.rule_type,
            value=candidate.new_value,
            quantity=candidate.quantity,
            unit=candidate.unit,
            adjustment_index=candidate.adjustment_index,
            periodicity_months=candidate.periodicity_months,
            validity=ValidityPeriod(start=candidate.validity_start, end=candidate.validity_end),
            review_status=anchoring.review_status,
            verification_score=anchoring.verification_score,
            evidence=evidence,
            relation=candidate.relation,
        )
        rules.append(rule)
        anchoring_results.append(anchoring)
        if anchoring.review_status is ReviewStatus.INCONCLUSIVE:
            warnings.append(
                f"Regra {rule.id} marcada como INCONCLUSIVE: {' '.join(anchoring.messages)}"
            )
        elif anchoring.low_confidence:
            warnings.append(
                f"Regra {rule.id} abaixo do limiar de confiança ({anchoring.verification_score})."
            )

    return JuristaResult(
        rules=tuple(rules),
        anchoring_results=tuple(anchoring_results),
        warnings=tuple(warnings),
        schema_valid=True,
        document_reviewed=False,
        attempts=attempts,
        metadata=metadata,
    )
