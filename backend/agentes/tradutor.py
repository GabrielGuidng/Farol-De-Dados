"""Agente Tradutor: liga itens cobrados a regras vigentes por equivalência semântica."""

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, ValidationError

from backend.agentes.anchoring import DEFAULT_CONFIDENCE_THRESHOLD, normalize_for_match
from backend.agentes.client import LLMClient
from backend.agentes.jurista import MAX_RETRY_ATTEMPTS, ExtractionMetadata
from backend.agentes.prompts import (
    TRADUTOR_AGENT_PROMPT_VERSION,
    TRADUTOR_AGENT_SYSTEM_PROMPT,
    TRADUTOR_AGENT_USER_TEMPLATE,
)
from backend.leitura import mask_text
from backend.schemas import (
    BillingItem,
    ContractRule,
    RuleType,
    StrictModel,
    VerificationScore,
)
from backend.schemas.types import EntityId, NonEmptyStr


class TranslationStatus(StrEnum):
    """Situação da vinculação semântica item-regra."""

    LINKED = "LINKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class LinkIssueCode(StrEnum):
    """Motivo pelo qual um vínculo item-regra exige revisão humana."""

    BELOW_SIMILARITY_THRESHOLD = "BELOW_SIMILARITY_THRESHOLD"
    AMBIGUOUS_LINK = "AMBIGUOUS_LINK"
    DUPLICATE_RULE_CANDIDATES = "DUPLICATE_RULE_CANDIDATES"
    DUPLICATE_ITEM_LINKS = "DUPLICATE_ITEM_LINKS"
    UNLINKED_ITEM = "UNLINKED_ITEM"
    UNKNOWN_RULE_OR_ITEM = "UNKNOWN_RULE_OR_ITEM"
    EXCERPT_NOT_ANCHORED = "EXCERPT_NOT_ANCHORED"


class ItemRuleLinkCandidate(StrictModel):
    """Vínculo candidato devolvido pelo modelo; trecho justificativo é obrigatório no esquema."""

    item_index: int = Field(ge=0)
    rule_id: EntityId
    item_excerpt: NonEmptyStr
    rule_excerpt: NonEmptyStr
    similarity_score: VerificationScore
    ambiguous: bool = False


class TradutorExtractionPayload(StrictModel):
    """Saída estruturada do Agente Tradutor."""

    links: tuple[ItemRuleLinkCandidate, ...] = ()


class ItemRuleLink(StrictModel):
    """Vínculo item-regra verificado por código, sem cálculos nem classificação financeira."""

    item_index: int = Field(ge=0)
    rule_id: EntityId
    rule_type: RuleType | None = None
    item_excerpt: NonEmptyStr
    rule_excerpt: NonEmptyStr
    similarity_score: VerificationScore
    ambiguous: bool = False
    status: TranslationStatus = TranslationStatus.LINKED
    issues: tuple[LinkIssueCode, ...] = ()


class TradutorInput(StrictModel):
    """Entrada do Tradutor: itens da cobrança e regras contratuais vigentes."""

    document_id: EntityId
    file_hash: str | None = None
    items: tuple[BillingItem, ...]
    active_rules: tuple[ContractRule, ...]


class TradutorResult(StrictModel):
    """Resultado do Agente Tradutor: vínculos e sinalização para cálculo ou revisão."""

    status: TranslationStatus
    ready_for_calculation: bool
    links: tuple[ItemRuleLink, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_valid: bool = True
    attempts: int = Field(default=0, ge=0)
    metadata: ExtractionMetadata


def run_tradutor_agent(
    payload: TradutorInput,
    client: LLMClient,
    *,
    similarity_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    max_retries: int = MAX_RETRY_ATTEMPTS,
    extracted_at: datetime | None = None,
) -> TradutorResult:
    """
    Liga itens da cobrança às regras vigentes por equivalência semântica, sem calcular valores.
    Vínculo ambíguo, abaixo do limiar, sem ancoragem ou com dois candidatos para a mesma regra
    devolve NEEDS_REVIEW e impede cálculo automático.
    """
    if max_retries < 1:
        raise ValueError("max_retries deve ser maior ou igual a 1")
    timestamp = extracted_at if extracted_at is not None else datetime.now(UTC)
    metadata = ExtractionMetadata(
        prompt_version=TRADUTOR_AGENT_PROMPT_VERSION,
        model_name=client.model_name,
        source_document_id=payload.document_id,
        file_hash=payload.file_hash,
        extracted_at=timestamp,
    )

    if not payload.items or not payload.active_rules:
        return TradutorResult(
            status=TranslationStatus.NEEDS_REVIEW,
            ready_for_calculation=False,
            links=(),
            warnings=("Não há itens ou regras vigentes suficientes para vincular.",),
            schema_valid=True,
            attempts=0,
            metadata=metadata,
        )

    masked_items = {index: mask_text(item.description) for index, item in enumerate(payload.items)}
    masked_rules = {
        rule.id: mask_text(rule.evidence.literal_excerpt) for rule in payload.active_rules
    }
    rules_by_id = {rule.id: rule for rule in payload.active_rules}

    item_lines = [f"[Item {index}] {desc}" for index, desc in masked_items.items()]
    rule_lines = [
        f"[Regra {rule.id} | tipo={rule.rule_type.value}] {masked_rules[rule.id]}"
        for rule in payload.active_rules
    ]
    document_text = (
        "ITENS DA COBRANÇA:\n"
        + "\n".join(item_lines)
        + "\n\nREGRAS VIGENTES:\n"
        + ("\n".join(rule_lines))
    )
    user_prompt = TRADUTOR_AGENT_USER_TEMPLATE.format(document_text=document_text)

    parsed: TradutorExtractionPayload | None = None
    last_error = ""
    attempts = 0
    for attempt in range(1, max_retries + 1):
        attempts = attempt
        response = client.generate(
            system_prompt=TRADUTOR_AGENT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=TradutorExtractionPayload,
        )
        try:
            parsed = TradutorExtractionPayload.model_validate_json(response.content)
            break
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)

    if parsed is None:
        return TradutorResult(
            status=TranslationStatus.NEEDS_REVIEW,
            ready_for_calculation=False,
            links=(),
            warnings=(
                f"Falha na validação de esquema do Tradutor após {attempts} tentativa(s): "
                f"vínculos descartados ({last_error}).",
            ),
            schema_valid=False,
            attempts=attempts,
            metadata=metadata,
        )

    rule_counts = Counter(candidate.rule_id for candidate in parsed.links)
    item_counts = Counter(candidate.item_index for candidate in parsed.links)

    verified_links: list[ItemRuleLink] = []
    warnings: list[str] = []

    for candidate in parsed.links:
        issues: list[LinkIssueCode] = []
        rule = rules_by_id.get(candidate.rule_id)
        item_desc = masked_items.get(candidate.item_index)

        if rule is None or item_desc is None:
            issues.append(LinkIssueCode.UNKNOWN_RULE_OR_ITEM)
        else:
            item_ok = normalize_for_match(candidate.item_excerpt) in normalize_for_match(item_desc)
            rule_ok = normalize_for_match(candidate.rule_excerpt) in normalize_for_match(
                masked_rules[rule.id]
            )
            if not (item_ok and rule_ok):
                issues.append(LinkIssueCode.EXCERPT_NOT_ANCHORED)

        if rule_counts[candidate.rule_id] > 1:
            issues.append(LinkIssueCode.DUPLICATE_RULE_CANDIDATES)
        if item_counts[candidate.item_index] > 1:
            issues.append(LinkIssueCode.DUPLICATE_ITEM_LINKS)
        if candidate.ambiguous:
            issues.append(LinkIssueCode.AMBIGUOUS_LINK)
        if candidate.similarity_score < similarity_threshold:
            issues.append(LinkIssueCode.BELOW_SIMILARITY_THRESHOLD)

        link_status = TranslationStatus.NEEDS_REVIEW if issues else TranslationStatus.LINKED
        verified_links.append(
            ItemRuleLink(
                item_index=candidate.item_index,
                rule_id=candidate.rule_id,
                rule_type=None if rule is None else rule.rule_type,
                item_excerpt=candidate.item_excerpt,
                rule_excerpt=candidate.rule_excerpt,
                similarity_score=candidate.similarity_score,
                ambiguous=candidate.ambiguous,
                status=link_status,
                issues=tuple(issues),
            )
        )
        if issues:
            codes = ", ".join(issue.value for issue in issues)
            warnings.append(
                f"Vínculo item {candidate.item_index} -> regra {candidate.rule_id} encaminhado "
                f"para revisão ({codes})."
            )

    linked_indices = {candidate.item_index for candidate in parsed.links}
    missing_items = sorted(set(masked_items) - linked_indices)
    for missing_idx in missing_items:
        warnings.append(
            f"O item {missing_idx} ({masked_items[missing_idx]}) ficou sem vínculo com regra; "
            "revisão humana necessária."
        )

    all_clean = (
        bool(verified_links)
        and not missing_items
        and all(link.status is TranslationStatus.LINKED for link in verified_links)
    )
    overall_status = TranslationStatus.LINKED if all_clean else TranslationStatus.NEEDS_REVIEW
    return TradutorResult(
        status=overall_status,
        ready_for_calculation=all_clean,
        links=tuple(verified_links),
        warnings=tuple(warnings),
        schema_valid=True,
        attempts=attempts,
        metadata=metadata,
    )
