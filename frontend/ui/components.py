"""Componentes HTML/CSS reutilizáveis seguindo a fronteira IA × cálculo determinístico."""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import streamlit as st

from backend.schemas import ContractRule, Evidence, RuleType
from frontend.ui.formatting import (
    RULE_TYPE_LABELS,
    format_brl,
    format_date_br,
    format_percent,
    format_quantity,
)

BadgeTone = Literal["ok", "bad", "info", "ai"]


@dataclass(frozen=True)
class KpiItem:
    """Representa um indicador na faixa única de KPIs da vista."""

    label: str
    value: str
    hint: str | None = None


def escape_text(value: object) -> str:
    """Escapa texto arbitrário antes de embutir em marcação HTML."""
    return html.escape("" if value is None else str(value), quote=True)


def render_badge(tone: BadgeTone, icon: str, text: str) -> str:
    """Constrói um badge acessível com cor, ícone e texto."""
    return (
        f'<span class="badge {tone}">'
        f"<span>{escape_text(icon)}</span>"
        f"<span>{escape_text(text)}</span>"
        "</span>"
    )


def render_page_header(title: str, subtitle: str, *, eyebrow: str | None = None) -> None:
    """Exibe cabeçalho limpo de página com hierarquia tipográfica fixa."""
    eyebrow_html = f'<div class="farol-eyebrow">{escape_text(eyebrow)}</div>' if eyebrow else ""
    st.markdown(
        '<div class="farol-header">'
        f"{eyebrow_html}"
        f'<h1 class="farol-title">{escape_text(title)}</h1>'
        f'<p class="farol-subtitle">{escape_text(subtitle)}</p>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_kpi_row(items: Sequence[KpiItem]) -> None:
    """Renderiza até 4 cartões KPI em faixa única com filete superior em ouro."""
    if not items:
        return
    cards: list[str] = []
    for item in items[:4]:
        hint_html = f'<div class="hint">{escape_text(item.hint)}</div>' if item.hint else ""
        cards.append(
            '<div class="kpi">'
            f'<div class="label">{escape_text(item.label)}</div>'
            f'<div class="value">{escape_text(item.value)}</div>'
            f"{hint_html}"
            "</div>"
        )
    st.markdown(f'<div class="kpi-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def format_evidence_location(evidence: Evidence, document_name: str | None = None) -> str:
    """Monta o rótulo monoespaçado de origem (`DOC • PÁG. X` ou cláusula/parágrafo)."""
    parts: list[str] = [document_name or evidence.source_document_id]
    if evidence.page is not None:
        parts.append(f"Pág. {evidence.page}")
    else:
        parts.append("Sem página (DOCX/texto)")
    if evidence.clause:
        parts.append(evidence.clause)
    if evidence.paragraph:
        parts.append(f"Parág. {evidence.paragraph}")
    return " • ".join(parts)


_INVOICE_IDS_MARKER_RE = re.compile(r"\n?\[invoice_ids:[^\]]+\]\s*")
_EXCERPT_CHAR_EQUIV: dict[str, str] = {
    '"': '["“”„«»]',
    "“": '["“”„«»]',
    "”": '["“”„«»]',
    "„": '["“”„«»]',
    "«": '["“”„«»]',
    "»": '["“”„«»]',
    "'": "['‘’]",
    "‘": "['‘’]",
    "’": "['‘’]",
    "-": "[-–—]",
    "–": "[-–—]",
    "—": "[-–—]",
}


def strip_internal_markers(text: str) -> str:
    """Remove marcadores técnicos internos antes de exibir o texto do documento na UI."""
    return _INVOICE_IDS_MARKER_RE.sub("", text).strip()


def find_excerpt_span(full_text: str, excerpt: str) -> tuple[int, int] | None:
    """Localiza o intervalo `(início, fim)` do trecho citado dentro do texto original."""
    clean_excerpt = excerpt.strip()
    if not full_text or not clean_excerpt:
        return None

    exact_idx = full_text.find(clean_excerpt)
    if exact_idx != -1:
        return exact_idx, exact_idx + len(clean_excerpt)

    tokens = clean_excerpt.split()
    if not tokens:
        return None
    token_patterns = [
        "".join(_EXCERPT_CHAR_EQUIV.get(ch, re.escape(ch)) for ch in token) for token in tokens
    ]
    pattern_str = r"\s+".join(token_patterns)
    match = re.search(pattern_str, full_text, re.DOTALL)
    if match is None:
        match = re.search(pattern_str, full_text, re.DOTALL | re.IGNORECASE)
    if match is None:
        return None
    return match.start(), match.end()


def highlight_excerpt_in_text(full_text: str, excerpt: str) -> tuple[str, bool]:
    """Escapa o texto original e destaca o trecho citado com `<mark class="doc-highlight">`."""
    cleaned_full = strip_internal_markers(full_text)
    if not cleaned_full:
        return "", False
    span = find_excerpt_span(cleaned_full, excerpt)
    if span is None:
        return escape_text(cleaned_full), False
    start, end = span
    before = escape_text(cleaned_full[:start])
    matched = escape_text(cleaned_full[start:end])
    after = escape_text(cleaned_full[end:])
    return f'{before}<mark class="doc-highlight">{matched}</mark>{after}', True


def render_evidence_box(
    evidence: Evidence,
    *,
    document_name: str | None = None,
    full_document_text: str | None = None,
) -> None:
    """Renderiza o trecho literal documental e, se disponível, destaca-o no texto original."""
    meta = format_evidence_location(evidence, document_name=document_name)
    original_block = ""
    if full_document_text:
        highlighted_html, matched = highlight_excerpt_in_text(
            full_document_text,
            evidence.literal_excerpt,
        )
        if highlighted_html:
            hint = (
                "Trecho destacado no texto integral extraído do documento:"
                if matched
                else (
                    "Texto integral extraído do documento "
                    "(correspondência exata não destacada automaticamente):"
                )
            )
            original_block = (
                '<details class="original-doc-details">'
                "<summary>Ver onde este trecho aparece no documento original</summary>"
                f'<div class="original-doc-hint">{escape_text(hint)}</div>'
                f'<div class="original-doc-viewer">{highlighted_html}</div>'
                "</details>"
            )

    st.markdown(
        '<div class="evidence">'
        f'<div class="meta">{escape_text(meta)}</div>'
        f'<div class="excerpt">{escape_text(evidence.literal_excerpt)}</div>'
        f"{original_block}"
        "</div>",
        unsafe_allow_html=True,
    )


def render_banner(tone: BadgeTone, title: str, message: str) -> None:
    """Exibe aviso contextual discreto seguindo os tokens de status da Seção 3.6."""
    st.markdown(
        f'<div class="farol-banner {tone}">'
        f"<strong>{escape_text(title)}</strong> — {escape_text(message)}"
        "</div>",
        unsafe_allow_html=True,
    )


def render_empty_state(title: str, description: str, *, next_action: str | None = None) -> None:
    """Exibe estado vazio padronizado sugerindo o próximo passo ao auditor."""
    next_html = (
        f'<p class="empty-desc" style="margin-top:8px;"><strong>Próximo passo:</strong> '
        f"{escape_text(next_action)}</p>"
        if next_action
        else ""
    )
    st.markdown(
        '<div class="empty-state">'
        f'<div class="empty-title">{escape_text(title)}</div>'
        f'<p class="empty-desc">{escape_text(description)}</p>'
        f"{next_html}"
        "</div>",
        unsafe_allow_html=True,
    )


def render_html_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    numeric_cols: set[int] | None = None,
    html_cols: set[int] | None = None,
) -> None:
    """Renderiza tabela HTML temática sem índice artificial e fiel aos dois temas."""
    numeric = numeric_cols or set()
    trusted_html = html_cols or set()

    head_cells = "".join(
        f'<th class="{"num" if idx in numeric else ""}">{escape_text(label)}</th>'
        for idx, label in enumerate(headers)
    )
    body_rows: list[str] = []
    for row in rows:
        tds: list[str] = []
        for idx, cell in enumerate(row):
            cls_attr = ' class="num"' if idx in numeric else ""
            content = cell if idx in trusted_html else escape_text(cell)
            tds.append(f"<td{cls_attr}>{content}</td>")
        body_rows.append(f"<tr>{''.join(tds)}</tr>")

    st.markdown(
        '<div class="farol-table-wrap">'
        '<table class="farol-table">'
        f"<thead><tr>{head_cells}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
        "</div>",
        unsafe_allow_html=True,
    )


def describe_rule_value(rule: ContractRule) -> str:
    """Resume o parâmetro principal da regra contratual em formato legível."""
    if rule.rule_type in (
        RuleType.MONTHLY_PRICE,
        RuleType.OVERAGE_PRICE,
        RuleType.APPROVAL_LIMIT,
    ):
        prefix = "Até " if rule.rule_type is RuleType.APPROVAL_LIMIT else ""
        return f"{prefix}{format_brl(rule.value)}"
    if rule.rule_type is RuleType.ITEM_PRICE:
        label = rule.item_name or "Item"
        if rule.quantity is not None:
            scope_suffix = " acum." if rule.quantity_scope == "TOTAL" else "/mês"
            qty_txt = format_quantity(rule.quantity, rule.unit or None)
            return f"{label}: {format_brl(rule.value)} ({qty_txt}{scope_suffix})"
        return f"{label}: {format_brl(rule.value)}"
    if rule.rule_type in (RuleType.INSS_RETENTION, RuleType.ISS_RETENTION):
        return f"{format_percent(rule.value)} sobre bruto"
    if rule.rule_type in (RuleType.FRANCHISE, RuleType.PAYMENT_TERMS):
        default_unit = "dias" if rule.rule_type is RuleType.PAYMENT_TERMS else ""
        return format_quantity(rule.quantity, rule.unit or default_unit or None)
    if rule.rule_type is RuleType.ADJUSTMENT:
        periodicity = f" a cada {rule.periodicity_months} meses" if rule.periodicity_months else ""
        return f"{rule.adjustment_index or 'Índice não informado'}{periodicity}"
    if rule.rule_type is RuleType.EXTENSION:
        return f"Até {format_date_br(rule.validity.end)}"
    return RULE_TYPE_LABELS.get(rule.rule_type, str(rule.rule_type))
