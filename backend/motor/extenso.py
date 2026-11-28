"""Verificação determinística de valor por extenso (BRL) usando exclusivamente Decimal."""

import re
import unicodedata
from decimal import Decimal

from backend.motor.calculator import round_money, subtract_money
from backend.motor.formatting import format_money
from backend.schemas import Check, CheckResult, DivergenceDirection, Invoice

WRITTEN_AMOUNT_CHECK = "written_amount_mismatch"

_ZERO = Decimal("0")
_UNITS: dict[str, int] = {
    "zero": 0,
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "onze": 11,
    "doze": 12,
    "treze": 13,
    "quatorze": 14,
    "catorze": 14,
    "quinze": 15,
    "dezesseis": 16,
    "dezasseis": 16,
    "dezessete": 17,
    "dezoito": 18,
    "dezenove": 19,
}
_TENS: dict[str, int] = {
    "vinte": 20,
    "trinta": 30,
    "quarenta": 40,
    "cinquenta": 50,
    "cincoenta": 50,
    "sessenta": 60,
    "setenta": 70,
    "oitenta": 80,
    "noventa": 90,
}
_HUNDREDS: dict[str, int] = {
    "cem": 100,
    "cento": 100,
    "duzentos": 200,
    "duzentas": 200,
    "trezentos": 300,
    "trezentas": 300,
    "quatrocentos": 400,
    "quatrocentas": 400,
    "quinhentos": 500,
    "quinhentas": 500,
    "seiscentos": 600,
    "seiscentas": 600,
    "setecentos": 700,
    "setecentas": 700,
    "oitocentos": 800,
    "oitocentas": 800,
    "novecentos": 900,
    "novecentas": 900,
}
_SCALES: dict[str, int] = {
    "mil": 1_000,
    "milhao": 1_000_000,
    "milhoes": 1_000_000,
    "bilhao": 1_000_000_000,
    "bilhoes": 1_000_000_000,
}
_CONNECTORS = frozenset({"e", "de"})
_NUMBER_WORDS = frozenset({*_UNITS.keys(), *_TENS.keys(), *_HUNDREDS.keys(), *_SCALES.keys()})
_REAL_ANCHOR_RE = re.compile(r"\brea(?:is|l)\b")
_CENTAVO_TAIL_RE = re.compile(r"^\s*(?:,\s*|\be\s+)?([a-z,\s]+?)\s+centavos?\b")


def _strip_accents_lower(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return ascii_only.casefold()


def _parse_int_words(tokens: list[str]) -> int | None:
    if not tokens:
        return None
    total = 0
    current = 0
    has_num = False
    for tok in tokens:
        if tok in _CONNECTORS:
            continue
        if tok in _UNITS:
            current += _UNITS[tok]
            has_num = True
        elif tok in _TENS:
            current += _TENS[tok]
            has_num = True
        elif tok in _HUNDREDS:
            current += _HUNDREDS[tok]
            has_num = True
        elif tok == "mil":
            chunk = current if current > 0 else 1
            total += chunk * 1_000
            current = 0
            has_num = True
        elif tok in _SCALES:
            scale = _SCALES[tok]
            chunk = current if current > 0 else 1
            total += chunk * scale
            current = 0
            has_num = True
        else:
            return None
    return (total + current) if has_num else None


def _collect_left_numeral_tokens(prefix: str) -> list[str]:
    clean = re.sub(r"[,;]+", " ", prefix)
    raw_words = re.findall(r"[a-z]+", clean)
    if not raw_words:
        return []
    collected: list[str] = []
    for word in reversed(raw_words):
        if word in _NUMBER_WORDS:
            collected.append(word)
        elif word in _CONNECTORS and collected:
            collected.append(word)
        else:
            break
    while collected and collected[-1] in _CONNECTORS:
        collected.pop()
    collected.reverse()
    return collected


def _collect_centavos(suffix: str) -> int | None:
    match = _CENTAVO_TAIL_RE.match(suffix)
    if match is None:
        return None
    raw_words = [
        w for w in re.findall(r"[a-z]+", match.group(1)) if w in _NUMBER_WORDS or w in _CONNECTORS
    ]
    parsed = _parse_int_words(raw_words)
    if parsed is None or parsed < 0 or parsed >= 100:
        return None
    return parsed


def parse_brl_extenso(text: str) -> Decimal | None:
    """Converte valor monetário em reais por extenso para `Decimal` com 2 casas."""
    normalized = _strip_accents_lower(text)
    if not normalized.strip():
        return None

    for anchor in _REAL_ANCHOR_RE.finditer(normalized):
        left_tokens = _collect_left_numeral_tokens(normalized[: anchor.start()])
        if not left_tokens:
            continue
        reais = _parse_int_words(left_tokens)
        if reais is None:
            continue
        cents = _collect_centavos(normalized[anchor.end() :]) or 0
        amount = Decimal(reais) + (Decimal(cents) / Decimal("100"))
        return round_money(amount)
    return None


def check_written_amount(invoice: Invoice, tolerance: Decimal) -> Check | None:
    """
    Confere o valor por extenso (quando informado na descrição ou itens) contra o total da NF.
    Devolve None quando não houver menção válida por extenso ou quando os valores coincidirem.
    """
    candidates = [invoice.service_description or "", *(item.description for item in invoice.items)]
    parsed_amount: Decimal | None = None
    matched_source = ""
    for candidate in candidates:
        if not candidate.strip():
            continue
        amount = parse_brl_extenso(candidate)
        if amount is not None:
            parsed_amount = amount
            matched_source = candidate.strip()
            break

    if parsed_amount is None:
        return None

    diff = subtract_money(invoice.total, parsed_amount)
    if abs(diff) <= tolerance:
        return None

    direction = DivergenceDirection.OVERCHARGE if diff > _ZERO else DivergenceDirection.UNDERCHARGE
    return Check(
        name=WRITTEN_AMOUNT_CHECK,
        result=CheckResult.DIVERGENCE,
        direction=direction,
        message=(
            f"Valor por extenso ({format_money(parsed_amount)}) difere do valor numérico "
            f"da cobrança ({format_money(invoice.total)}) em {format_money(abs(diff))}."
        ),
        calculation_memory=(
            f"valor por extenso = {format_money(parsed_amount)}",
            f"valor numérico da nota = {format_money(invoice.total)}",
            f"diferença = numérico − extenso = {format_money(diff)}",
            f"trecho analisado = {matched_source}",
        ),
    )
