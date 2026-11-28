"""Itens da cobrança: separa base de excedente e confere a soma dos itens com o total.

Até o Agente Tradutor existir, a classificação usa regras simples de texto; depois ela passará a
receber os vínculos item-regra do Tradutor. A palavra "adicional" sozinha não classifica excedente.
"""

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from backend.motor.calculator import (
    add_money,
    multiply_money,
    require_decimal,
    round_money,
    subtract_money,
)
from backend.motor.formatting import format_money, format_quantity
from backend.motor.results import Reason, ReasonCode, RuleSelection, SelectionStatus
from backend.schemas import (
    AppliedRule,
    BillingItem,
    Check,
    CheckResult,
    DivergenceDirection,
    Invoice,
    RuleType,
    StrictModel,
)

ITEM_CLASSIFICATION_CHECK = "item_classification"
ITEMS_TOTAL_CHECK = "items_total"
ITEMS_ARITHMETIC_CHECK = "items_arithmetic"
HOURLY_LIMIT_CHECK = "hourly_limit"
HOURS_WITHOUT_OS_CHECK = "hours_without_os"

_ZERO = Decimal("0")
_ONE = Decimal("1")
_OVERAGE_TERMS = re.compile(r"\b(excedentes?|excesso|acima da franquia|fora da franquia)\b")
_AMBIGUOUS_TERMS = re.compile(r"\badicion(?:al|ais)\b")
_DISCOUNT_TERMS = re.compile(
    r"\b(glosa|desconto|abatimento|credito\s+de\s+sla|penalidade\s+sla|multa\s+sla)\b"
)
_FIXED_BASE_TERMS = re.compile(
    r"\b(mensalidade|taxa\s+fixa|valor\s+fixo|parcela\s+fixa|sustentacao|suporte\s+mensal|"
    r"assinatura\s+mensal|servico\s+fixo|gestao\s+mensal)\b"
)
_HOURLY_UNITS = frozenset({"h", "hr", "hrs", "hora", "horas"})
_HOURLY_DESC_TERMS = re.compile(
    r"\bhoras?\s+(?:tecnicas?|sob\s+demanda|consultoria|desenvolvimento|suporte|avulsas?|"
    r"extras?|trabalhadas?|apontadas?|consumidas?)\b|\bbanco\s+de\s+horas\b"
)
_OS_REFERENCE_RE = re.compile(
    r"\b(?:os|o\.s\.|ordem\s+de\s+servi[cç]o|chamado|ticket)\s*[:#nº°.\-]*\s*[A-Z0-9]*\d+",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


class ItemClassification(StrictModel):
    """Itens separados em base, excedente, desconto/glosa e ambíguos."""

    base_items: tuple[BillingItem, ...] = ()
    overage_items: tuple[BillingItem, ...] = ()
    discount_items: tuple[BillingItem, ...] = ()
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
    Recebe os itens da cobrança; separa excedente, glosa/desconto e base por texto da descrição.
    Mais de um item base, item ambíguo, ausência de item base ou de itens gera motivos (revisão).
    """
    base: list[BillingItem] = []
    overage: list[BillingItem] = []
    discounts: list[BillingItem] = []
    ambiguous: list[BillingItem] = []
    for item in items:
        text = _normalize(item.description)
        if _DISCOUNT_TERMS.search(text):
            discounts.append(item)
        elif _OVERAGE_TERMS.search(text):
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
        discount_items=tuple(discounts),
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
    Quando há itens de glosa/desconto SLA, aceita tanto a soma bruta quanto a soma com abatimento.
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

    classification = classify_items(items)
    items_sum = add_money(*(item.total_value for item in items))
    difference = subtract_money(total, items_sum)

    if classification.discount_items and abs(difference) > tolerance_value:
        gross_items = (
            *classification.base_items,
            *classification.overage_items,
            *classification.ambiguous_items,
        )
        non_discount_sum = add_money(*(item.total_value for item in gross_items))
        discount_sum = add_money(*(item.total_value for item in classification.discount_items))
        net_items_sum = subtract_money(non_discount_sum, discount_sum)
        net_diff = subtract_money(total, net_items_sum)
        if abs(net_diff) <= tolerance_value:
            return Check(
                name=ITEMS_TOTAL_CHECK,
                result=CheckResult.OK,
                message=(
                    "A soma dos itens descontando glosa/desconto SLA confere com o total "
                    "da cobrança."
                ),
                calculation_memory=(
                    f"soma bruta dos itens = {format_money(non_discount_sum)}",
                    f"glosas/descontos = {format_money(discount_sum)}",
                    f"soma líquida dos itens = {format_money(net_items_sum)}",
                    f"total da cobrança = {format_money(total)}",
                    f"diferença = total − líquido dos itens = {format_money(net_diff)}",
                    f"tolerância do contrato = {format_money(tolerance_value)}",
                ),
            )

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


def _is_hourly_or_measured_item(item: BillingItem) -> bool:
    unit_norm = (item.unit or "").strip().casefold()
    if unit_norm in _HOURLY_UNITS:
        return True
    if _HOURLY_DESC_TERMS.search(_normalize(item.description)):
        return True
    return item.quantity > _ONE


def select_fixed_base_item(base_items: Sequence[BillingItem]) -> BillingItem | None:
    """
    Quando há mais de um item base, seleciona a mensalidade fixa somente se existir
    exatamente 1 item fixo (qty=1 com termo de mensalidade) e todos os demais forem
    variáveis/horários medidos (preserva ambiguidade entre dois itens fixos distintos).
    """
    if len(base_items) < 2:
        return None
    fixed_candidates = [
        item
        for item in base_items
        if item.quantity == _ONE and _FIXED_BASE_TERMS.search(_normalize(item.description))
    ]
    if len(fixed_candidates) != 1:
        return None
    fixed = fixed_candidates[0]
    others = [item for item in base_items if item is not fixed]
    if others and all(_is_hourly_or_measured_item(item) for item in others):
        return fixed
    return None


def check_items_arithmetic(items: Sequence[BillingItem], tolerance: Decimal) -> Check | None:
    """
    Confere item a item se `quantidade × valor_unitário` equivale ao `total_value` do item.
    Devolve None quando todos os itens conferem (ou quando não há itens).
    """
    if not items:
        return None
    tolerance_value = require_decimal(tolerance, "tolerance")
    mismatches: list[str] = []
    total_abs_diff = _ZERO
    signed_diff = _ZERO
    for item in items:
        expected = multiply_money(item.quantity, item.unit_value)
        diff = subtract_money(item.total_value, expected)
        if abs(diff) > tolerance_value:
            total_abs_diff += abs(diff)
            signed_diff += diff
            mismatches.append(
                f"{item.description}: {format_quantity(item.quantity)} × "
                f"{format_money(item.unit_value)} = {format_money(expected)}, "
                f"cobrado {format_money(item.total_value)} (diferença {format_money(diff)})"
            )
    if not mismatches:
        return None

    direction = (
        DivergenceDirection.OVERCHARGE if signed_diff >= _ZERO else DivergenceDirection.UNDERCHARGE
    )
    return Check(
        name=ITEMS_ARITHMETIC_CHECK,
        result=CheckResult.DIVERGENCE,
        direction=direction,
        message=(
            f"Erro de cálculo item a item (quantidade × unitário) com diferença de "
            f"{format_money(total_abs_diff)}: " + "; ".join(mismatches) + "."
        ),
        calculation_memory=(
            *mismatches,
            f"diferença acumulada nos itens = {format_money(total_abs_diff)}",
        ),
    )


def check_hourly_items(
    invoice: Invoice,
    *,
    franchise_selection: RuleSelection,
    overage_selection: RuleSelection,
    tolerance: Decimal,
) -> tuple[list[Check], list[AppliedRule]]:
    """
    Audita itens horários que não usaram o termo 'excedente': verifica teto/franquia de horas
    e ausência de Ordem de Serviço (OS) ou boletim de medição.
    """
    tolerance_value = require_decimal(tolerance, "tolerance")
    hourly_items: list[BillingItem] = []
    for item in invoice.items:
        norm_desc = _normalize(item.description)
        if _DISCOUNT_TERMS.search(norm_desc) or _OVERAGE_TERMS.search(norm_desc):
            continue
        unit_norm = (item.unit or "").strip().casefold()
        if unit_norm in _HOURLY_UNITS or _HOURLY_DESC_TERMS.search(norm_desc):
            hourly_items.append(item)

    if not hourly_items:
        return [], []

    checks: list[Check] = []
    applied: list[AppliedRule] = []
    total_hours = sum((item.quantity for item in hourly_items), _ZERO)
    total_amount = add_money(*(item.total_value for item in hourly_items))

    franchise_rule = (
        franchise_selection.rule
        if franchise_selection.status is SelectionStatus.SELECTED
        and franchise_selection.rule is not None
        and franchise_selection.rule.rule_type is RuleType.FRANCHISE
        else None
    )
    if (
        franchise_rule is not None
        and franchise_rule.quantity is not None
        and total_hours > franchise_rule.quantity
    ):
        excess_hours = total_hours - franchise_rule.quantity
        overage_rule = (
            overage_selection.rule
            if overage_selection.status is SelectionStatus.SELECTED
            and overage_selection.rule is not None
            and overage_selection.rule.rule_type is RuleType.OVERAGE_PRICE
            and overage_selection.rule.value is not None
            else None
        )
        unit_rate = (
            overage_rule.value
            if overage_rule is not None and overage_rule.value is not None
            else (
                round_money(total_amount / total_hours)
                if total_hours > _ZERO
                else hourly_items[0].unit_value
            )
        )
        excess_amount = multiply_money(excess_hours, unit_rate)
        applied.append(AppliedRule(rule_id=franchise_rule.id, version=franchise_rule.version))
        if overage_rule is not None:
            applied.append(AppliedRule(rule_id=overage_rule.id, version=overage_rule.version))

        trusted = franchise_rule.review_status.is_trusted and (
            overage_rule is None or overage_rule.review_status.is_trusted
        )
        if excess_amount > tolerance_value:
            checks.append(
                Check(
                    name=HOURLY_LIMIT_CHECK,
                    result=CheckResult.DIVERGENCE if trusted else CheckResult.REVIEW,
                    direction=(
                        DivergenceDirection.OVERCHARGE
                        if trusted
                        else DivergenceDirection.POSSIBLE_DIVERGENCE
                    ),
                    message=(
                        f"Horas cobradas ({format_quantity(total_hours)} h) ultrapassam o limite "
                        f"contratual ({format_quantity(franchise_rule.quantity)} h) em "
                        f"{format_quantity(excess_hours)} h ({format_money(excess_amount)})."
                    ),
                    calculation_memory=(
                        f"horas cobradas = {format_quantity(total_hours)}",
                        f"limite contratual = {format_quantity(franchise_rule.quantity)}",
                        f"horas acima do limite = {format_quantity(excess_hours)}",
                        f"valor excedente = {format_money(excess_amount)}",
                    ),
                    rule_id=franchise_rule.id,
                )
            )

    combined_os_text = " ".join(
        [invoice.service_description or "", *(item.description for item in hourly_items)]
    )
    has_os_ref = bool(_OS_REFERENCE_RE.search(combined_os_text))
    if not has_os_ref and invoice.measured_hours is None:
        checks.append(
            Check(
                name=HOURS_WITHOUT_OS_CHECK,
                result=CheckResult.REVIEW,
                direction=DivergenceDirection.UNVERIFIABLE,
                message=(
                    f"Horas cobradas ({format_quantity(total_hours)} h) sem Ordem de Serviço (OS) "
                    "ou boletim de medição comprovando o apontamento."
                ),
                calculation_memory=(
                    f"horas cobradas = {format_quantity(total_hours)}",
                    "ordem de serviço / medição = não informada",
                ),
            )
        )

    return checks, applied
