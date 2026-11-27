"""Testes unitários da fundação visual, formatação pt-BR e aderência ao guia de estilo."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from backend.schemas import ReviewDecision
from frontend.ui.components import highlight_excerpt_in_text, strip_internal_markers
from frontend.ui.formatting import (
    AUDIT_DECISION_LABELS,
    format_brl,
    format_date_br,
    format_percent,
    format_quantity,
    format_signed_brl,
    format_validity_br,
    parse_decimal_input,
)
from frontend.ui.theme import build_theme_css
from frontend.ui.tokens import CHART_COLORS, FORBIDDEN_HEX, THEMES

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


def test_brl_and_numeric_formatting_uses_pt_br_conventions() -> None:
    assert format_brl(None) == "—"
    assert format_brl(Decimal("0.00")) == "R$ 0,00"
    assert format_brl(Decimal("1250.00")) == "R$ 1.250,00"
    assert format_brl(Decimal("-45.5")) == "-R$ 45,50"

    assert format_signed_brl(None) == "—"
    assert format_signed_brl(Decimal("0.00")) == "R$ 0,00"
    assert format_signed_brl(Decimal("300.00")) == "+R$ 300,00"
    assert format_signed_brl(Decimal("-300.00")) == "-R$ 300,00"

    assert format_quantity(Decimal("40"), "horas") == "40 horas"
    assert format_quantity(Decimal("12.5"), "un") == "12,50 un"
    assert format_percent(Decimal("8.00")) == "8,00%"
    assert format_date_br(date(2026, 5, 1)) == "01/05/2026"
    assert format_validity_br(date(2026, 1, 1), None) == "Desde 01/01/2026 (sem término)"


def test_parse_decimal_input_handles_ptbr_and_dot_formats() -> None:
    assert parse_decimal_input("R$ 10.800,00") == Decimal("10800.00")
    assert parse_decimal_input("10800.50") == Decimal("10800.50")
    assert parse_decimal_input("0,00") == Decimal("0.00")
    with pytest.raises(InvalidOperation):
        parse_decimal_input("   ")


def test_themes_share_exact_tokens_and_reject_forbidden_colors() -> None:
    assert set(THEMES["dark"].keys()) == set(THEMES["light"].keys())
    assert len(CHART_COLORS["dark"]) == 4
    assert len(CHART_COLORS["light"]) == 4

    forbidden_upper = {color.upper() for color in FORBIDDEN_HEX}
    for theme_name, mapping in THEMES.items():
        for token_name, value in mapping.items():
            for match in _HEX_RE.findall(value):
                assert match.upper() not in forbidden_upper, (
                    f"Cor proibida {match} encontrada em {theme_name}.{token_name}"
                )

    assert THEMES["dark"]["bg-canvas"].upper() != "#000000"
    assert THEMES["dark"]["text-primary"].upper() != "#FFFFFF"


def test_no_hex_colors_outside_tokens_and_no_forbidden_css_effects() -> None:
    for path in sorted(_FRONTEND_DIR.rglob("*")):
        if path.suffix not in {".py", ".css"}:
            continue
        if path.name == "tokens.py":
            continue
        content = path.read_text(encoding="utf-8")
        found_hex = _HEX_RE.findall(content)
        assert not found_hex, f"Hexadecimal encontrado fora de tokens.py em {path}: {found_hex}"

    styles = (_FRONTEND_DIR / "ui" / "styles.css").read_text(encoding="utf-8")
    assert "box-shadow" not in styles
    assert "backdrop-filter" not in styles
    assert "linear-gradient" not in styles
    assert "radial-gradient" not in styles
    assert "var(--canvas-gradient)" in styles

    dark_style = build_theme_css("dark")
    light_style = build_theme_css("light")
    assert "--bg-canvas:#0B0D12" in dark_style
    assert "--bg-canvas:#F7F8FC" in light_style
    assert "--canvas-gradient:" in dark_style
    assert "--canvas-gradient:" in light_style
    assert "linear-gradient(180deg" in THEMES["dark"]["canvas-gradient"]
    assert "#141A40 100%" in THEMES["dark"]["canvas-gradient"]
    assert "linear-gradient(180deg" in THEMES["light"]["canvas-gradient"]
    assert "#FFFFFF 100%" in THEMES["light"]["canvas-gradient"]
    assert AUDIT_DECISION_LABELS == {
        ReviewDecision.CONFIRM: "Confirmar",
        ReviewDecision.CORRECT: "Ressalvar",
        ReviewDecision.REJECT: "Rejeitar",
    }


def test_highlight_excerpt_in_text_normalizes_whitespace_escapes_html_and_strips_markers() -> None:
    raw_doc = (
        "Cláusula 1ª: Contrato <alpha> & 'teste'.\n\n"
        "Cláusula 3ª: O valor   mensal é de\n“R$ 10.000,00” — vigente em 2026.\n"
        "[invoice_ids:pdf-doc-123]"
    )
    cleaned = strip_internal_markers(raw_doc)
    assert "[invoice_ids:" not in cleaned

    rendered, matched = highlight_excerpt_in_text(
        raw_doc,
        'Cláusula 3ª: O valor mensal é de "R$ 10.000,00" - vigente em 2026.',
    )
    assert matched is True
    assert '<mark class="doc-highlight">' in rendered
    assert "&lt;alpha&gt;" in rendered
    assert "[invoice_ids:" not in rendered

    not_found_html, not_found_match = highlight_excerpt_in_text(raw_doc, "trecho inexistente")
    assert not_found_match is False
    assert "<mark" not in not_found_html
