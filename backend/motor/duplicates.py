"""Detecção de duplicidade: mesmo número, mesma competência/valor ou mesma AF."""

import re
from collections.abc import Sequence

from backend.motor.formatting import format_money
from backend.schemas import Check, CheckResult, DivergenceDirection, Invoice

DUPLICATE_CHECK = "duplicate_invoice"

_AF_TOKEN_RE = re.compile(
    r"(?i)\b(?:AF[-/\s]*0*(\d+)|autoriza[çc][ãa]o\s+de\s+fornecimento\s*(?:n[º°.]?\s*)?([A-Z0-9/-]+))"
)


def extract_af_tokens(invoice: Invoice) -> tuple[str, ...]:
    """Extrai códigos normalizados de Autorização de Fornecimento (ex.: AF-118)."""
    if not invoice.service_description:
        return ()
    tokens: list[str] = []
    for m in _AF_TOKEN_RE.finditer(invoice.service_description):
        raw = m.group(1) or m.group(2) or ""
        clean = re.sub(r"[^A-Z0-9]", "", raw.upper()).lstrip("0") or raw.upper()
        if clean:
            tok = f"AF-{clean}"
            if tok not in tokens:
                tokens.append(tok)
    return tuple(tokens)


def _number(invoice: Invoice) -> str:
    return invoice.number.strip().casefold()


def check_duplicates(invoices: Sequence[Invoice]) -> dict[str, Check]:
    """
    Recebe as cobranças; devolve, por id, o alerta (REVIEW) de cada cobrança que tem par duplicado.
    Os dois lados do par recebem o alerta. Cobranças sem contrato não são comparadas.
    """
    matches: dict[str, list[str]] = {}
    af_by_id = {inv.id: set(extract_af_tokens(inv)) for inv in invoices}
    for index, left in enumerate(invoices):
        left_afs = af_by_id.get(left.id, set())
        for right in invoices[index + 1 :]:
            if left.id == right.id or left.contract_id is None:
                continue
            if left.contract_id != right.contract_id:
                continue
            same_number = _number(left) == _number(right)
            same_period_and_value = (
                left.reference_period == right.reference_period and left.total == right.total
            )
            shared_afs = sorted(left_afs & af_by_id.get(right.id, set()))
            if not (same_number or same_period_and_value or shared_afs):
                continue
            if same_number:
                motive = "mesmo número"
            elif same_period_and_value:
                motive = (
                    f"mesma competência ({left.reference_period}) e mesmo valor "
                    f"({format_money(left.total)})"
                )
            else:
                motive = f"mesma Autorização de Fornecimento ({', '.join(shared_afs)})"
            matches.setdefault(left.id, []).append(
                f"cobrança {right.number} ({right.id}): {motive}"
            )
            matches.setdefault(right.id, []).append(f"cobrança {left.number} ({left.id}): {motive}")

    return {
        invoice_id: Check(
            name=DUPLICATE_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
            message=(
                "Possível duplicidade: " + "; ".join(lines) + ". Pode ser legítimo "
                "(por exemplo, cobranças complementares); revisão humana necessária."
            ),
            calculation_memory=tuple(lines),
        )
        for invoice_id, lines in matches.items()
    }
