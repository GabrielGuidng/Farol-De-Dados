"""Verificação determinística por código da ancoragem documental das regras extraídas."""

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum

from backend.leitura._parsing import parse_decimal
from backend.schemas import ReviewStatus, RuleType, StrictModel, VerificationScore

DEFAULT_CONFIDENCE_THRESHOLD = Decimal("0.85")

_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9])[0-9]+(?:[.,][0-9]+)*")
_TRANSITION_PATTERN = re.compile(
    r"(?i)\bde\s+(?:R\$\s*)?(?P<old>[0-9][0-9.,]*)(?:\s*\([^)]*\))?\s+"
    r"para\s+(?:R\$\s*)?(?P<new>[0-9][0-9.,]*)"
)
_QUANTITY_DELTA_TRANSITION_RE = re.compile(
    r"(?is)(?:acresc\w+|reduz\w+|suprim\w+|elevad\w+|aumentad\w+)[^.\n]{0,90}?"
    r"\bem\s+(?P<delta>[0-9][0-9.,]*)(?:\s*\([^)]*\))?[^.\n]{0,90}?"
    r"\bde\s+(?P<old>[0-9][0-9.,]*)(?:\s*\([^)]*\))?\s+para\s+(?P<new>[0-9][0-9.,]*)"
)
_QUANTITY_TRANSITION_NO_CURRENCY_RE = re.compile(
    r"(?i)\bde\s+(?!R\$)(?P<old>[0-9][0-9.,]*)(?:\s*\([^)]*\))?\s+"
    r"para\s+(?!R\$)(?P<new>[0-9][0-9.,]*)"
)
_PREVIOUS_PATTERN = re.compile(
    r"(?i)(?:antes(?:\s+era|\s+de)?|anteriormente(?:\s+de)?|"
    r"em\s+substitui[çc][ãa]o\s+a(?:o\s+valor\s+de)?|onde\s+se\s+lia|era\s+de)"
    r"\s+(?:R\$\s*)?(?P<old>[0-9][0-9.,]*)"
)

_QUOTE_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-",
    }
)


class AnchoringIssueCode(StrEnum):
    """Motivo da falha na verificação determinística de ancoragem."""

    EXCERPT_NOT_FOUND = "EXCERPT_NOT_FOUND"
    VALUE_NOT_IN_EXCERPT = "VALUE_NOT_IN_EXCERPT"
    NEW_EQUALS_PREVIOUS = "NEW_EQUALS_PREVIOUS"
    EXTRACTED_PREVIOUS_VALUE = "EXTRACTED_PREVIOUS_VALUE"


class AnchoringCheckResult(StrictModel):
    """Resultado das verificações por código; pontuação não vem da autoavaliação do modelo."""

    review_status: ReviewStatus
    verification_score: VerificationScore
    low_confidence: bool
    issues: tuple[AnchoringIssueCode, ...] = ()
    messages: tuple[str, ...] = ()


def normalize_for_match(text: str) -> str:
    """Normaliza aspas curvas, travessões e espaços para comparar trechos literais."""
    return " ".join(text.translate(_QUOTE_TRANSLATION).split())


def excerpt_exists_in_document(
    literal_excerpt: str,
    document_text: str,
    *,
    page: int | None = None,
    pages_by_number: dict[int, str] | None = None,
) -> bool:
    """Confere se o trecho literal normalizado existe no documento (ou na página informada)."""
    needle = normalize_for_match(literal_excerpt)
    if not needle:
        return False
    if pages_by_number is not None and page is not None:
        page_text = pages_by_number.get(page)
        if page_text is None:
            return False
        return needle in normalize_for_match(page_text)
    return needle in normalize_for_match(document_text)


def extract_numbers_from_text(text: str) -> tuple[Decimal, ...]:
    """Extrai valores numéricos de texto livre convertendo-os diretamente para Decimal."""
    numbers: list[Decimal] = []
    for match in _NUMBER_TOKEN.finditer(text):
        token = match.group(0).rstrip(".,")
        if not token:
            continue
        try:
            numbers.append(parse_decimal(token, "extracted_number"))
        except ValueError:
            continue
    return tuple(numbers)


def _try_decimal(token: str) -> Decimal | None:
    cleaned = token.rstrip(".,")
    if not cleaned:
        return None
    try:
        return parse_decimal(cleaned, "pattern_number")
    except ValueError:
        return None


def _value_matches_excerpt(
    *,
    rule_type: RuleType,
    literal_excerpt: str,
    new_value: Decimal | None,
    quantity: Decimal | None,
    adjustment_index: str | None,
    validity_end: date | None,
) -> bool:
    excerpt_numbers = extract_numbers_from_text(literal_excerpt)
    if rule_type is RuleType.ITEM_PRICE:
        value_ok = new_value is not None and new_value in excerpt_numbers
        qty_ok = quantity is None or quantity in excerpt_numbers
        return value_ok and qty_ok
    if rule_type in (
        RuleType.MONTHLY_PRICE,
        RuleType.OVERAGE_PRICE,
        RuleType.INSS_RETENTION,
        RuleType.ISS_RETENTION,
        RuleType.APPROVAL_LIMIT,
    ):
        return new_value is not None and new_value in excerpt_numbers
    if rule_type in (RuleType.FRANCHISE, RuleType.PAYMENT_TERMS):
        return quantity is not None and quantity in excerpt_numbers
    if rule_type is RuleType.ADJUSTMENT:
        return (
            adjustment_index is not None
            and adjustment_index.strip().casefold()
            in normalize_for_match(literal_excerpt).casefold()
        )
    if rule_type is RuleType.EXTENSION:
        if validity_end is None:
            return False
        markers = (
            str(validity_end.year),
            validity_end.strftime("%d/%m/%Y"),
            validity_end.isoformat(),
        )
        return any(marker in literal_excerpt for marker in markers)
    return False


def resolve_transition_final_quantity(
    literal_excerpt: str, extracted_quantity: Decimal | None
) -> Decimal | None:
    """Detecta se a quantidade extraída pegou a variação ou anterior em vez do novo total."""
    if extracted_quantity is None:
        return None
    normalized = normalize_for_match(literal_excerpt)
    for match in _QUANTITY_DELTA_TRANSITION_RE.finditer(normalized):
        delta_val = _try_decimal(match.group("delta"))
        old_val = _try_decimal(match.group("old"))
        new_val = _try_decimal(match.group("new"))
        if new_val is not None and extracted_quantity != new_val:
            if extracted_quantity == delta_val or extracted_quantity == old_val:
                return new_val
    for match in _QUANTITY_TRANSITION_NO_CURRENCY_RE.finditer(normalized):
        old_val = _try_decimal(match.group("old"))
        new_val = _try_decimal(match.group("new"))
        if old_val is not None and new_val is not None and old_val != new_val:
            if extracted_quantity == old_val:
                return new_val
    return None


def _captured_previous_value(literal_excerpt: str, new_value: Decimal | None) -> bool:
    if new_value is None:
        return False
    normalized = normalize_for_match(literal_excerpt)
    for match in _TRANSITION_PATTERN.finditer(normalized):
        old_val = _try_decimal(match.group("old"))
        new_val = _try_decimal(match.group("new"))
        if old_val is not None and new_value == old_val and new_value != new_val:
            return True
    for match in _PREVIOUS_PATTERN.finditer(normalized):
        old_val = _try_decimal(match.group("old"))
        if old_val is not None and new_value == old_val:
            return True
    return False


def verify_rule_anchoring(
    *,
    rule_type: RuleType,
    literal_excerpt: str,
    document_text: str,
    new_value: Decimal | None = None,
    previous_value: Decimal | None = None,
    quantity: Decimal | None = None,
    adjustment_index: str | None = None,
    validity_end: date | None = None,
    page: int | None = None,
    pages_by_number: dict[int, str] | None = None,
    confidence_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
) -> AnchoringCheckResult:
    """
    Executa as 4 verificações por código da regra extraída:
    (1) trecho existe; (2) valor consta no trecho; (3) novo != anterior; (4) não capturou
    o valor anterior. Qualquer falha resulta em INCONCLUSIVE; aprovação dá PENDING_REVIEW.
    """
    issues: list[AnchoringIssueCode] = []
    messages: list[str] = []

    if not excerpt_exists_in_document(
        literal_excerpt, document_text, page=page, pages_by_number=pages_by_number
    ):
        issues.append(AnchoringIssueCode.EXCERPT_NOT_FOUND)
        messages.append("O trecho literal citado não foi encontrado no texto do documento.")

    if not _value_matches_excerpt(
        rule_type=rule_type,
        literal_excerpt=literal_excerpt,
        new_value=new_value,
        quantity=quantity,
        adjustment_index=adjustment_index,
        validity_end=validity_end,
    ):
        issues.append(AnchoringIssueCode.VALUE_NOT_IN_EXCERPT)
        messages.append("O valor ou parâmetro extraído não aparece no trecho literal citado.")

    if previous_value is not None and new_value is not None and new_value == previous_value:
        issues.append(AnchoringIssueCode.NEW_EQUALS_PREVIOUS)
        messages.append("O valor novo extraído é igual ao valor anterior citado.")

    if _captured_previous_value(literal_excerpt, new_value) or (
        rule_type is RuleType.ITEM_PRICE
        and quantity is not None
        and resolve_transition_final_quantity(literal_excerpt, quantity) is not None
    ):
        issues.append(AnchoringIssueCode.EXTRACTED_PREVIOUS_VALUE)
        messages.append(
            "O trecho indica que o valor ou quantidade extraída corresponde à variação ou "
            "ao valor anterior, e não ao total vigente."
        )

    passed = 4 - len(issues)
    score = Decimal("0.25") * passed
    status = ReviewStatus.PENDING_REVIEW if not issues else ReviewStatus.INCONCLUSIVE
    low_confidence = score < confidence_threshold or status is ReviewStatus.INCONCLUSIVE
    return AnchoringCheckResult(
        review_status=status,
        verification_score=score,
        low_confidence=low_confidence,
        issues=tuple(issues),
        messages=tuple(messages),
    )
