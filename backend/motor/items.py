"""Itens da cobrança: separa base de excedente e confere a soma dos itens com o total.

Até o Agente Tradutor existir, a classificação usa regras simples de texto; depois ela passará a
receber os vínculos item-regra do Tradutor. A palavra "adicional" sozinha não classifica excedente.
"""

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from backend.motor.calculator import add_money, require_decimal, subtract_money
from backend.motor.formatting import format_money
from backend.motor.results import Reason, ReasonCode
from backend.schemas import BillingItem, Check, CheckResult, DivergenceDirection, StrictModel

ITEM_CLASSIFICATION_CHECK = "item_classification"
ITEMS_TOTAL_CHECK = "items_total"

_OVERAGE_TERMS = re.compile(r"\b(excedentes?|excesso|acima da franquia|fora da franquia)\b")
_AMBIGUOUS_TERMS = re.compile(r"\badicion(?:al|ais)\b")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


class ItemClassification(StrictModel):
    """Itens separados em base, excedente e ambíguos (texto com "adicional" sem "excedente")."""

    base_items: tuple[BillingItem, ...] = ()
    overage_items: tuple[BillingItem, ...] = ()
    ambiguous_items: tuple[BillingItem, ...] = ()
    reasons: tuple[Reason, ...] = ()

    @property
    def base_unambiguous(self) -> bool:
        """Verdadeiro só com exatamente um item base e nenhum item ambíguo."""
        return len(self.base_items) == 1 and not self.ambiguous_items

    @property
    def overage_hours(self) -> Decimal:
        """Soma das quantidades dos itens de excedente (sem arredondar)."""
        return sum((item.quantity for item in self.overage_items), Decimal("0"))

    @property
    def overage_amount(self) -> Decimal:
        """Soma dos valores totais dos itens de excedente."""
        return add_money(*(item.total_value for item in self.overage_items))


def classify_items(items: Sequence[BillingItem]) -> ItemClassification:
    """
    Recebe os itens da cobrança; separa excedente de base por texto da descrição.
    Mais de um item base, item ambíguo, ausência de item base ou de itens gera motivos (revisão).
    """
    base: list[BillingItem] = []
    overage: list[BillingItem] = []
    ambiguous: list[BillingItem] = []
    for item in items:
        text = _normalize(item.description)
        if _OVERAGE_TERMS.search(text):
            overage.append(item)
        elif _AMBIGUOUS_TERMS.search(text):
            ambiguous.append(item)
        else:
            base.append(item)

    reasons: list[Reason] = []
    if not items:
        reasons.append(
            Reason(code=ReasonCode.NO_ITEMS, message="A cobrança não traz itens para classificar.")
        )
    if len(base) > 1:
        reasons.append(
            Reason(
                code=ReasonCode.MULTIPLE_BASE_ITEMS,
                message=(
                    f"Há {len(base)} itens base na cobrança; a comparação com o preço mensal "
                    "é ambígua."
                ),
            )
        )
    if items and not base and not ambiguous:
        reasons.append(
            Reason(
                code=ReasonCode.NO_BASE_ITEM,
                message="A cobrança só tem itens de excedente; não há item base para comparar.",
            )
        )
    if ambiguous:
        names = "; ".join(item.description for item in ambiguous)
        reasons.append(
            Reason(
                code=ReasonCode.AMBIGUOUS_ITEM,
                message=(
                    f'O item "{names}" não foi classificado como excedente: a palavra '
                    '"adicional" sozinha não basta.'
                ),
            )
        )
    return ItemClassification(
        base_items=tuple(base),
        overage_items=tuple(overage),
        ambiguous_items=tuple(ambiguous),
        reasons=tuple(reasons),
    )


def check_item_classification(classification: ItemClassification) -> Check:
    """Devolve REVIEW (comparação ambígua) se houver motivos na classificação; senão OK."""
    if not classification.reasons:
        return Check(
            name=ITEM_CLASSIFICATION_CHECK,
            result=CheckResult.OK,
            message="Itens classificados sem ambiguidade (um item base).",
        )
    return Check(
        name=ITEM_CLASSIFICATION_CHECK,
        result=CheckResult.REVIEW,
        direction=DivergenceDirection.UNVERIFIABLE,
        message="Comparação ambígua: "
        + " ".join(reason.message for reason in classification.reasons),
    )


def check_items_total(
    items: Sequence[BillingItem], invoice_total: Decimal, tolerance: Decimal
) -> Check:
    """
    Recebe os itens, o total da cobrança e a tolerância do contrato; confere a soma com o total.
    Diferença acima da tolerância (impostos, descontos, item faltante) gera revisão.
    """
    total = require_decimal(invoice_total, "invoice_total")
    tolerance_value = require_decimal(tolerance, "tolerance")
    if not items:
        return Check(
            name=ITEMS_TOTAL_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message="Não há itens para conferir com o total da cobrança.",
        )
    items_sum = add_money(*(item.total_value for item in items))
    difference = subtract_money(total, items_sum)
    memory = (
        f"soma dos itens = {format_money(items_sum)}",
        f"total da cobrança = {format_money(total)}",
        f"diferença = total − itens = {format_money(difference)}",
        f"tolerância do contrato = {format_money(tolerance_value)}",
    )
    if abs(difference) <= tolerance_value:
        return Check(
            name=ITEMS_TOTAL_CHECK,
            result=CheckResult.OK,
            message="A soma dos itens confere com o total da cobrança.",
            calculation_memory=memory,
        )
    return Check(
        name=ITEMS_TOTAL_CHECK,
        result=CheckResult.REVIEW,
        direction=DivergenceDirection.UNVERIFIABLE,
        message=(
            f"A soma dos itens ({format_money(items_sum)}) difere do total "
            f"({format_money(total)}) em {format_money(abs(difference))}; impostos, descontos "
            "ou itens não listados podem explicar. Revisão humana necessária."
        ),
        calculation_memory=memory,
    )
