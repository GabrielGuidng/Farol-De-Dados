"""Formatação monetária/numérica brasileira (a partir de Decimal) e rótulos em português."""

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from backend.motor import SelectionStatus
from backend.schemas import (
    AuditClassification,
    CheckResult,
    ContractStatus,
    DocumentType,
    Invoice,
    InvoiceSource,
    ReadQuality,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    RuleRelation,
    RuleType,
)

BadgeToneLiteral = Literal["ok", "bad", "info", "ai"]

_CENTS = Decimal("0.01")
_MONTH_ABBR_PT: tuple[str, ...] = (
    "",
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)
_PERIOD_RE = re.compile(r"^([0-9]{4})-([0-9]{2})$")
_LAST_INT_RE = re.compile(r"([0-9]+)(?!.*[0-9])")
_DOC_BY_ID_RE = re.compile(
    r"\bdocumento\s+\(?((?:pdf-doc|doc)-[A-Za-z0-9][A-Za-z0-9_-]*)\)?",
    re.IGNORECASE,
)
_PAREN_TECH_ID_RE = re.compile(r"\s*\((?:audit-)?(?:pdf-doc|doc|inv)-[A-Za-z0-9][A-Za-z0-9_-]*\)")
_BARE_TECH_ID_RE = re.compile(r"\b(?:audit-)?(?:pdf-doc|doc|inv)-[A-Za-z0-9]{6,}(?:-[0-9]+)?\b")
_TECH_TERM_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("MISSING_DOCUMENTATION", "falta de documentação"),
    ("NO_DIVERGENCE", "sem divergência"),
    ("POSSIBLE_DIVERGENCE", "possível divergência"),
    ("NEEDS_REVIEW", "revisão humana necessária"),
    ("OVERCHARGE", "cobrança a maior"),
    ("UNDERCHARGE", "cobrança a menor"),
    ("UNVERIFIABLE", "não verificável"),
    ("contract_link", "vínculo contratual"),
    ("duplicate_invoice", "duplicidade"),
    ("contract_validity", "vigência"),
    ("rule_timeline", "linha do tempo"),
    ("items_total", "soma dos itens"),
    ("item_classification", "itens cobrados"),
    ("monthly_price", "preço mensal"),
    ("overage_price", "preço de excedente"),
    ("overage_quantity", "horas excedentes"),
    ("periodic_adjustment", "reajuste"),
    ("competence_inferred", "competência"),
    ("amendments_reviewed", "aditivos"),
    ("rules_start_date", "início das regras"),
)


def _format_ptbr_number(value: Decimal, *, places: int = 2) -> str:
    quant = Decimal("1") if places == 0 else Decimal(f"0.{'0' * places}")
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    raw = f"{rounded:,}"
    return raw.replace(",", "X").replace(".", ",").replace("X", ".")


def format_brl(value: Decimal | None) -> str:
    """Formata um valor `Decimal` em moeda brasileira (`R$ 1.250,00`)."""
    if value is None:
        return "—"
    if value < 0:
        return f"-R$ {_format_ptbr_number(abs(value), places=2)}"
    return f"R$ {_format_ptbr_number(value, places=2)}"


def format_signed_brl(value: Decimal | None) -> str:
    """Formata uma diferença monetária com sinal explícito (`+R$ 300,00` ou `-R$ 300,00`)."""
    if value is None:
        return "—"
    if value > 0:
        return f"+R$ {_format_ptbr_number(value, places=2)}"
    if value < 0:
        return f"-R$ {_format_ptbr_number(abs(value), places=2)}"
    return "R$ 0,00"


def format_quantity(value: Decimal | None, unit: str | None = None) -> str:
    """Formata uma quantidade decimal com unidade opcional."""
    if value is None:
        return "—"
    normalized = value.normalize()
    places = 0 if normalized == normalized.to_integral_value() else 2
    formatted = _format_ptbr_number(value, places=places)
    return f"{formatted} {unit}".strip() if unit else formatted


def format_percent(value: Decimal | None) -> str:
    """Formata um percentual a partir de `Decimal` com 2 casas decimais."""
    if value is None:
        return "—"
    return f"{_format_ptbr_number(value, places=2)}%"


def format_date_br(value: date | None) -> str:
    """Formata data ISO no padrão brasileiro `DD/MM/AAAA`."""
    if value is None:
        return "—"
    return value.strftime("%d/%m/%Y")


def format_datetime_br(value: datetime | None) -> str:
    """Formata data/hora em `DD/MM/AAAA HH:MM`."""
    if value is None:
        return "—"
    return value.strftime("%d/%m/%Y %H:%M")


def format_validity_br(start: date | None, end: date | None) -> str:
    """Descreve o intervalo de vigência em português claro."""
    if start is None and end is None:
        return "Vigência não informada"
    if start is None:
        return f"Início indefinido até {format_date_br(end)}"
    if end is None:
        return f"Desde {format_date_br(start)} (sem término)"
    return f"{format_date_br(start)} até {format_date_br(end)}"


def parse_decimal_input(raw: str) -> Decimal:
    """Converte entrada monetária/numérica (pt-BR ou ponto decimal) para Decimal."""
    cleaned = raw.strip().replace("R$", "").replace(" ", "")
    if not cleaned:
        raise InvalidOperation("valor vazio")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    return Decimal(cleaned)


RULE_TYPE_LABELS: dict[RuleType, str] = {
    RuleType.MONTHLY_PRICE: "Mensalidade fixa",
    RuleType.FRANCHISE: "Franquia inclusa",
    RuleType.OVERAGE_PRICE: "Hora / unidade excedente",
    RuleType.ADJUSTMENT: "Índice de reajuste",
    RuleType.EXTENSION: "Prorrogação de vigência",
}

RULE_RELATION_LABELS: dict[RuleRelation, str] = {
    RuleRelation.CREATED: "Regra original",
    RuleRelation.ALTERED: "Alterada por aditivo",
    RuleRelation.REVOKED: "Revogada por aditivo",
}

DOCUMENT_TYPE_LABELS: dict[DocumentType, str] = {
    DocumentType.CONTRACT: "Contrato-base",
    DocumentType.AMENDMENT: "Termo aditivo",
    DocumentType.INVOICE: "Fatura / Nota PDF",
    DocumentType.NFE_XML: "Nota Fiscal XML",
    DocumentType.SPREADSHEET: "Planilha CSV/XLSX",
}

CONTRACT_STATUS_LABELS: dict[ContractStatus, str] = {
    ContractStatus.ACTIVE: "Ativo",
    ContractStatus.ENDED: "Encerrado",
    ContractStatus.CANCELED: "Cancelado",
}

READ_QUALITY_LABELS: dict[ReadQuality, str] = {
    ReadQuality.OK: "Leitura OK",
    ReadQuality.NO_TEXT: "PDF sem texto",
}

REVIEW_DECISION_LABELS: dict[ReviewDecision, str] = {
    ReviewDecision.CONFIRM: "Confirmar",
    ReviewDecision.CORRECT: "Corrigir",
    ReviewDecision.REJECT: "Rejeitar",
}

REVIEW_TARGET_LABELS: dict[ReviewTargetType, str] = {
    ReviewTargetType.RULE: "Regra contratual",
    ReviewTargetType.DOCUMENT: "Documento",
    ReviewTargetType.AUDIT_RESULT: "Auditoria de cobrança",
}

# Cada status combina tom visual (`ok`/`bad`/`info`/`ai`), ícone e texto legível.
REVIEW_STATUS_BADGES: dict[ReviewStatus, tuple[BadgeToneLiteral, str, str]] = {
    ReviewStatus.CONFIRMED: ("ok", "✓", "Confirmada"),
    ReviewStatus.CORRECTED: ("ok", "✓", "Corrigida"),
    ReviewStatus.PENDING_REVIEW: ("ai", "✦", "Sugestão IA — revisar"),
    ReviewStatus.INCONCLUSIVE: ("ai", "✦", "Inconclusiva — revisar"),
    ReviewStatus.REJECTED: ("bad", "▲", "Rejeitada"),
}

SELECTION_STATUS_BADGES: dict[SelectionStatus, tuple[BadgeToneLiteral, str, str]] = {
    SelectionStatus.SELECTED: ("ok", "✓", "Vigente na competência"),
    SelectionStatus.NEEDS_REVIEW: ("ai", "✦", "Exige revisão humana"),
    SelectionStatus.NO_RULE: ("info", "●", "Sem regra vigente"),
}

AUDIT_CLASSIFICATION_BADGES: dict[AuditClassification, tuple[BadgeToneLiteral, str, str]] = {
    AuditClassification.NO_DIVERGENCE: ("ok", "✓", "Sem divergência"),
    AuditClassification.DIVERGENCE: ("bad", "▲", "Com divergência"),
    AuditClassification.NEEDS_REVIEW: ("ai", "✦", "Em revisão humana"),
    AuditClassification.MISSING_DOCUMENTATION: ("bad", "▲", "Falta documentação"),
}

INVOICE_SOURCE_LABELS: dict[InvoiceSource, str] = {
    InvoiceSource.PDF: "Fatura PDF",
    InvoiceSource.NFE_XML: "Nota Fiscal XML",
    InvoiceSource.SPREADSHEET: "Planilha CSV/XLSX",
}

AUDIT_DECISION_LABELS: dict[ReviewDecision, str] = {
    ReviewDecision.CONFIRM: "Confirmar",
    ReviewDecision.CORRECT: "Ressalvar",
    ReviewDecision.REJECT: "Rejeitar",
}

AUDIT_REVIEW_STATUS_BADGES: dict[ReviewStatus, tuple[BadgeToneLiteral, str, str]] = {
    ReviewStatus.CONFIRMED: ("ok", "✓", "Confirmada"),
    ReviewStatus.CORRECTED: ("ai", "✦", "Com ressalva"),
    ReviewStatus.REJECTED: ("bad", "▲", "Rejeitada"),
    ReviewStatus.PENDING_REVIEW: ("info", "●", "Aguardando decisão"),
    ReviewStatus.INCONCLUSIVE: ("ai", "✦", "Inconclusiva"),
}

CHECK_RESULT_BADGES: dict[CheckResult, tuple[BadgeToneLiteral, str, str]] = {
    CheckResult.OK: ("ok", "✓", "Conforme"),
    CheckResult.DIVERGENCE: ("bad", "▲", "Divergência"),
    CheckResult.REVIEW: ("ai", "✦", "Revisão"),
    CheckResult.PENDING: ("bad", "▲", "Pendência"),
}

RULE_RELATION_SHORT_LABELS: dict[RuleRelation, str] = {
    RuleRelation.CREATED: "Criada",
    RuleRelation.ALTERED: "Alterada",
    RuleRelation.REVOKED: "Revogada",
}

CHECK_NAME_LABELS: dict[str, str] = {
    "contract_link": "Vínculo contratual",
    "duplicate_invoice": "Duplicidade",
    "contract_validity": "Vigência",
    "rule_timeline": "Linha do tempo",
    "items_total": "Soma dos itens",
    "item_classification": "Itens cobrados",
    "monthly_price": "Preço",
    "overage_price": "Preço de excedente",
    "overage_quantity": "Horas excedentes",
    "periodic_adjustment": "Reajuste",
    "competence_inferred": "Competência",
    "amendments_reviewed": "Aditivos",
    "rules_start_date": "Início das regras",
}


def humanize_check_name(name: str) -> str:
    """Converte o identificador técnico da verificação para rótulo claro em português."""
    return CHECK_NAME_LABELS.get(name, name.replace("_", " ").capitalize())


def format_competence_pt(period: object) -> str:
    """Converte competência `AAAA-MM` para o formato brasileiro `MM/AAAA`."""
    raw = str(period).strip()
    match = _PERIOD_RE.fullmatch(raw)
    if match is None:
        return raw or "—"
    return f"{match.group(2)}/{match.group(1)}"


def format_competence_short(period: object) -> str:
    """Converte competência `AAAA-MM` para forma curta `mes/AAAA` (ex.: `set/2025`)."""
    raw = str(period).strip()
    match = _PERIOD_RE.fullmatch(raw)
    if match is None:
        return raw or "—"
    month_num = int(match.group(2))
    abbr = _MONTH_ABBR_PT[month_num] if 1 <= month_num <= 12 else match.group(2)
    return f"{abbr}/{match.group(1)}"


def format_month_year_short(value: date | None) -> str:
    """Formata uma data de vigência como `mes/AAAA` (ex.: `dez/2020`)."""
    if value is None:
        return "—"
    return f"{_MONTH_ABBR_PT[value.month]}/{value.year}"


def format_compact_validity(start: date | None, end: date | None) -> str:
    """Formata vigência de forma compacta (`dez/2020 a abr/2021`)."""
    if start is None and end is None:
        return "Não informada"
    if start is None:
        return f"Até {format_month_year_short(end)}"
    if end is None:
        return f"Desde {format_month_year_short(start)}"
    return f"{format_month_year_short(start)} a {format_month_year_short(end)}"


def format_confidence_pct(score: Decimal | None) -> str:
    """Formata confiança (0 a 1) como percentual inteiro (ex.: `96%`)."""
    if score is None:
        return "Manual"
    pct = (score * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{int(pct)}%"


def period_sort_tuple(period: object) -> tuple[int, int]:
    """Extrai `(ano, mês)` para ordenação cronológica estável de competências."""
    match = _PERIOD_RE.fullmatch(str(period).strip())
    if match is None:
        return (9999, 99)
    return (int(match.group(1)), int(match.group(2)))


def natural_number_sort_tuple(number: str) -> tuple[int, int, str]:
    """Chave de ordenação numérica natural para números de nota (`1031` < `1036` < `1062`)."""
    clean = number.strip()
    if clean.isdigit():
        return (0, int(clean), clean.casefold())
    match = _LAST_INT_RE.search(clean)
    if match is not None:
        return (1, int(match.group(1)), clean.casefold())
    return (2, 0, clean.casefold())


def natural_invoice_sort_key(invoice: Invoice) -> tuple[int, int, int, int, str]:
    """Ordena cobranças por competência cronológica e número natural da nota."""
    year, month = period_sort_tuple(invoice.reference_period)
    kind_rank, num_val, text_key = natural_number_sort_tuple(invoice.number)
    return (year, month, kind_rank, num_val, text_key)


def clean_technical_ids_in_message(
    text: str,
    doc_names: dict[str, str] | None = None,
) -> str:
    """Remove IDs técnicos internos e traduz termos em inglês em mensagens exibidas na UI."""
    cleaned = text
    if doc_names:

        def _replace_doc(match: re.Match[str]) -> str:
            doc_id = match.group(1)
            return f"documento {doc_names[doc_id]}" if doc_id in doc_names else match.group(0)

        cleaned = _DOC_BY_ID_RE.sub(_replace_doc, cleaned)

    cleaned = _PAREN_TECH_ID_RE.sub("", cleaned)
    if doc_names:
        for doc_id, file_name in doc_names.items():
            if doc_id in cleaned:
                cleaned = cleaned.replace(doc_id, file_name)
    cleaned = _BARE_TECH_ID_RE.sub("", cleaned)
    for tech, pt in _TECH_TERM_REPLACEMENTS:
        cleaned = cleaned.replace(tech, pt)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return cleaned.strip()
