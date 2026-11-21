"""Detecção de duplicidade: mesmo número, ou mesma competência e mesmo valor, no mesmo contrato."""

from collections.abc import Sequence

from backend.motor.formatting import format_money
from backend.schemas import Check, CheckResult, DivergenceDirection, Invoice

DUPLICATE_CHECK = "duplicate_invoice"


def _number(invoice: Invoice) -> str:
    return invoice.number.strip().casefold()


def check_duplicates(invoices: Sequence[Invoice]) -> dict[str, Check]:
    """
    Recebe as cobranças; devolve, por id, o alerta (REVIEW) de cada cobrança que tem par duplicado.
    Os dois lados do par recebem o alerta. Cobranças sem contrato não são comparadas.
    """
    matches: dict[str, list[str]] = {}
    for index, left in enumerate(invoices):
        for right in invoices[index + 1 :]:
            if left.id == right.id or left.contract_id is None:
                continue
            if left.contract_id != right.contract_id:
                continue
            same_number = _number(left) == _number(right)
            same_period_and_value = (
                left.reference_period == right.reference_period and left.total == right.total
            )
            if not (same_number or same_period_and_value):
                continue
            motive = (
                "mesmo número"
                if same_number
                else f"mesma competência ({left.reference_period}) e mesmo valor "
                f"({format_money(left.total)})"
            )
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
