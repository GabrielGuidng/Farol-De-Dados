"""Verificações de cobrança: preço mensal, preço do excedente e quantidade de horas excedentes.

Só vira DIVERGENCE quando todas as regras usadas são CONFIRMED ou CORRECTED; com regra não
revisada, o máximo é revisão ("possível divergência"). Incerteza é resultado, não exceção.
"""

from decimal import Decimal

from backend.motor.calculator import (
    multiply_money,
    percentage,
    require_decimal,
    round_money,
    subtract_money,
)
from backend.motor.formatting import (
    describe_rule_source,
    format_money,
    format_percent,
    format_quantity,
)
from backend.motor.results import CheckOutcome, RuleSelection, SelectionStatus
from backend.schemas import Check, CheckResult, ContractRule, DivergenceDirection, RuleType

MONTHLY_PRICE_CHECK = "monthly_price"
OVERAGE_PRICE_CHECK = "overage_price"
OVERAGE_QUANTITY_CHECK = "overage_quantity"

_ZERO = Decimal("0")


def _outcome(
    name: str,
    result: CheckResult,
    message: str,
    memory: list[str],
    rule: ContractRule | None = None,
    *,
    direction: DivergenceDirection | None = None,
    base: Decimal | None = None,
    difference: Decimal | None = None,
    percent: Decimal | None = None,
) -> CheckOutcome:
    check = Check(
        name=name,
        result=result,
        direction=direction,
        message=message,
        calculation_memory=tuple(memory),
        rule_id=None if rule is None else rule.id,
    )
    return CheckOutcome(
        check=check,
        direction=direction,
        base=base,
        difference=difference,
        percentage=percent,
    )


def _selection_problem(name: str, selection: RuleSelection) -> CheckOutcome:
    """Seleção sem regra única: a verificação vira revisão, com os motivos da seleção."""
    reasons = " ".join(reason.message for reason in selection.reasons)
    return _outcome(
        name,
        CheckResult.REVIEW,
        f"Não foi possível escolher a regra vigente. {reasons}".strip(),
        [],
        direction=DivergenceDirection.UNVERIFIABLE,
    )


def _selected_rule(selection: RuleSelection, expected_type: RuleType) -> ContractRule | None:
    rule = selection.rule if selection.status is SelectionStatus.SELECTED else None
    if rule is not None and rule.rule_type is not expected_type:
        raise ValueError(f"esperada regra {expected_type}, recebida {rule.rule_type}")
    return rule


def _unit_price(rule: ContractRule) -> Decimal | None:
    return rule.value


def _direction(difference: Decimal) -> str:
    return "a mais" if difference > 0 else "a menos"


def _divergence_direction(difference: Decimal) -> DivergenceDirection:
    return DivergenceDirection.OVERCHARGE if difference > 0 else DivergenceDirection.UNDERCHARGE


def check_monthly_price(
    charged_base: Decimal, selection: RuleSelection, tolerance: Decimal
) -> CheckOutcome:
    """
    Recebe o valor base cobrado, a regra vigente selecionada e a tolerância do contrato.
    Devolve a verificação com memória de cálculo; DIVERGENCE só se a regra for confiável.
    """
    charged = require_decimal(charged_base, "charged_base")
    tolerance_value = require_decimal(tolerance, "tolerance")
    rule = _selected_rule(selection, RuleType.MONTHLY_PRICE)
    if rule is None:
        return _selection_problem(MONTHLY_PRICE_CHECK, selection)
    rule_value = _unit_price(rule)
    if rule_value is None:
        return _outcome(
            MONTHLY_PRICE_CHECK,
            CheckResult.REVIEW,
            "A regra vigente não informa valor; revisão humana necessária.",
            [],
            rule,
            direction=DivergenceDirection.UNVERIFIABLE,
        )

    difference = subtract_money(charged, rule_value)
    percent = percentage(difference, rule_value) if rule_value != 0 else None
    shown = f"{format_money(difference)}" + (
        "" if percent is None else f" ({format_percent(percent)})"
    )
    memory = [
        f"valor cobrado = {format_money(charged)}",
        f"valor da regra vigente = {format_money(rule_value)}",
        f"diferença = cobrado − regra = {shown}",
        f"tolerância do contrato = {format_money(tolerance_value)}",
    ]
    source = f"Fonte da regra: {describe_rule_source(rule)}."
    base = round_money(rule_value)
    trusted = rule.review_status.is_trusted
    if abs(difference) <= tolerance_value:
        if trusted:
            return _outcome(
                MONTHLY_PRICE_CHECK,
                CheckResult.OK,
                f"Valor cobrado de {format_money(charged)} confere com a regra vigente. {source}",
                memory,
                rule,
                base=base,
                difference=difference,
                percent=percent,
            )
        return _outcome(
            MONTHLY_PRICE_CHECK,
            CheckResult.REVIEW,
            "O valor confere com a regra vigente, mas a regra ainda não foi revisada por uma "
            f"pessoa. {source}",
            memory,
            rule,
            direction=DivergenceDirection.UNVERIFIABLE,
            base=base,
            difference=difference,
            percent=percent,
        )
    detail = (
        f"cobrado {format_money(charged)}, regra vigente {format_money(rule_value)}; "
        f"diferença de {format_money(abs(difference))} {_direction(difference)} "
        f"({format_percent(abs(percent)) if percent is not None else 'sem percentual'}). {source}"
    )
    if trusted:
        return _outcome(
            MONTHLY_PRICE_CHECK,
            CheckResult.DIVERGENCE,
            f"Divergência no preço mensal: {detail}",
            memory,
            rule,
            direction=_divergence_direction(difference),
            base=base,
            difference=difference,
            percent=percent,
        )
    return _outcome(
        MONTHLY_PRICE_CHECK,
        CheckResult.REVIEW,
        f"Possível divergência no preço mensal (regra ainda não revisada): {detail}",
        memory,
        rule,
        direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
        base=base,
        difference=difference,
        percent=percent,
    )


def check_overage_price(
    billed_hours: Decimal,
    billed_amount: Decimal,
    price_selection: RuleSelection,
    tolerance: Decimal,
) -> CheckOutcome:
    """
    Recebe horas excedentes cobradas, o valor cobrado por elas, o preço do excedente vigente
    e a tolerância. Confere horas × preço (arredondado uma vez) com o valor cobrado.
    """
    hours = require_decimal(billed_hours, "billed_hours")
    amount = require_decimal(billed_amount, "billed_amount")
    tolerance_value = require_decimal(tolerance, "tolerance")
    if hours == 0 and amount == 0:
        return _outcome(
            OVERAGE_PRICE_CHECK, CheckResult.OK, "Nenhum excedente cobrado nesta cobrança.", []
        )
    rule = _selected_rule(price_selection, RuleType.OVERAGE_PRICE)
    if rule is None:
        return _selection_problem(OVERAGE_PRICE_CHECK, price_selection)
    unit_price = _unit_price(rule)
    if unit_price is None:
        return _outcome(
            OVERAGE_PRICE_CHECK,
            CheckResult.REVIEW,
            "A regra de preço do excedente não informa valor; revisão humana necessária.",
            [],
            rule,
            direction=DivergenceDirection.UNVERIFIABLE,
        )

    expected = multiply_money(hours, unit_price)
    difference = subtract_money(amount, expected)
    memory = [
        f"horas excedentes cobradas = {format_quantity(hours)}",
        f"preço do excedente = {format_money(unit_price)} por hora",
        f"valor esperado = horas × preço = {format_money(expected)}",
        f"valor cobrado = {format_money(amount)}",
        f"diferença = cobrado − esperado = {format_money(difference)}",
        f"tolerância do contrato = {format_money(tolerance_value)}",
    ]
    source = f"Fonte da regra: {describe_rule_source(rule)}."
    trusted = rule.review_status.is_trusted
    if abs(difference) <= tolerance_value:
        if trusted:
            return _outcome(
                OVERAGE_PRICE_CHECK,
                CheckResult.OK,
                f"O valor do excedente confere com o preço vigente. {source}",
                memory,
                rule,
                base=expected,
                difference=difference,
            )
        return _outcome(
            OVERAGE_PRICE_CHECK,
            CheckResult.REVIEW,
            "O valor do excedente confere, mas a regra de preço ainda não foi revisada por uma "
            f"pessoa. {source}",
            memory,
            rule,
            direction=DivergenceDirection.UNVERIFIABLE,
            base=expected,
            difference=difference,
        )
    detail = (
        f"cobrado {format_money(amount)} por {format_quantity(hours)} h, esperado "
        f"{format_money(expected)}; diferença de {format_money(abs(difference))} "
        f"{_direction(difference)}. {source}"
    )
    if trusted:
        return _outcome(
            OVERAGE_PRICE_CHECK,
            CheckResult.DIVERGENCE,
            f"Divergência no preço do excedente: {detail}",
            memory,
            rule,
            direction=_divergence_direction(difference),
            base=expected,
            difference=difference,
        )
    return _outcome(
        OVERAGE_PRICE_CHECK,
        CheckResult.REVIEW,
        f"Possível divergência no preço do excedente (regra ainda não revisada): {detail}",
        memory,
        rule,
        direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
        base=expected,
        difference=difference,
    )


def check_overage_quantity(
    billed_hours: Decimal,
    measured_hours: Decimal | None,
    franchise_selection: RuleSelection,
    price_selection: RuleSelection,
    tolerance: Decimal,
) -> CheckOutcome:
    """
    Recebe horas excedentes cobradas, horas medidas (None se não houver medição), a franquia
    e o preço do excedente. Horas permitidas = máx(0, medidas − franquia); o que passar disso
    é cobrança a mais. Excedente cobrado sem medição é NEEDS_REVIEW (revisão).
    """
    hours = require_decimal(billed_hours, "billed_hours")
    tolerance_value = require_decimal(tolerance, "tolerance")
    if hours == 0:
        return _outcome(
            OVERAGE_QUANTITY_CHECK, CheckResult.OK, "Nenhuma hora excedente cobrada.", []
        )
    if measured_hours is None:
        return _outcome(
            OVERAGE_QUANTITY_CHECK,
            CheckResult.REVIEW,
            "Excedente cobrado sem medição de horas: não é possível conferir a quantidade.",
            [
                f"horas excedentes cobradas = {format_quantity(hours)}",
                "horas medidas = não informadas",
            ],
            direction=DivergenceDirection.UNVERIFIABLE,
        )
    measured = require_decimal(measured_hours, "measured_hours")
    franchise = _selected_rule(franchise_selection, RuleType.FRANCHISE)
    if franchise is None:
        return _selection_problem(OVERAGE_QUANTITY_CHECK, franchise_selection)
    price = _selected_rule(price_selection, RuleType.OVERAGE_PRICE)
    if price is None:
        return _selection_problem(OVERAGE_QUANTITY_CHECK, price_selection)
    franchise_hours = franchise.quantity
    unit_price = _unit_price(price)
    if franchise_hours is None or unit_price is None:
        return _outcome(
            OVERAGE_QUANTITY_CHECK,
            CheckResult.REVIEW,
            "A franquia ou o preço do excedente não informa quantidade ou valor; "
            "revisão necessária.",
            [],
            franchise,
            direction=DivergenceDirection.UNVERIFIABLE,
        )

    permitted = max(_ZERO, measured - franchise_hours)
    extra_hours = hours - permitted
    memory = [
        f"horas medidas = {format_quantity(measured)}",
        f"franquia = {format_quantity(franchise_hours)} h",
        f"horas permitidas = máx(0, {format_quantity(measured)} − "
        f"{format_quantity(franchise_hours)}) = {format_quantity(permitted)}",
        f"horas excedentes cobradas = {format_quantity(hours)}",
    ]
    base = multiply_money(permitted, unit_price)
    if extra_hours <= 0:
        memory.append("horas a mais = 0")
        trusted_ok = franchise.review_status.is_trusted and price.review_status.is_trusted
        return _outcome(
            OVERAGE_QUANTITY_CHECK,
            CheckResult.OK if trusted_ok else CheckResult.REVIEW,
            "As horas excedentes cobradas estão dentro do permitido."
            + ("" if trusted_ok else " Porém, há regra ainda não revisada por uma pessoa."),
            memory,
            franchise,
            direction=None if trusted_ok else DivergenceDirection.UNVERIFIABLE,
            base=base,
            difference=_ZERO,
        )

    difference = multiply_money(extra_hours, unit_price)
    memory.append(
        f"horas a mais = {format_quantity(hours)} − {format_quantity(permitted)} = "
        f"{format_quantity(extra_hours)}"
    )
    memory.append(
        f"valor a mais = {format_quantity(extra_hours)} × {format_money(unit_price)} = "
        f"{format_money(difference)}"
    )
    memory.append(f"tolerância do contrato = {format_money(tolerance_value)}")
    sources = (
        f"Fonte da franquia: {describe_rule_source(franchise)}. "
        f"Fonte do preço: {describe_rule_source(price)}."
    )
    detail = (
        f"cobradas {format_quantity(hours)} h com {format_quantity(permitted)} h permitidas; "
        f"{format_money(difference)} a mais. {sources}"
    )
    trusted_ok = franchise.review_status.is_trusted and price.review_status.is_trusted
    if difference <= tolerance_value:
        return _outcome(
            OVERAGE_QUANTITY_CHECK,
            CheckResult.OK if trusted_ok else CheckResult.REVIEW,
            f"Excesso de horas dentro da tolerância do contrato: {detail}",
            memory,
            franchise,
            direction=None if trusted_ok else DivergenceDirection.UNVERIFIABLE,
            base=base,
            difference=difference,
        )
    if trusted_ok:
        return _outcome(
            OVERAGE_QUANTITY_CHECK,
            CheckResult.DIVERGENCE,
            f"Divergência na quantidade de horas excedentes: {detail}",
            memory,
            franchise,
            direction=DivergenceDirection.OVERCHARGE,
            base=base,
            difference=difference,
        )
    return _outcome(
        OVERAGE_QUANTITY_CHECK,
        CheckResult.REVIEW,
        f"Possível divergência na quantidade de horas excedentes (regra ainda não revisada): "
        f"{detail}",
        memory,
        franchise,
        direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
        base=base,
        difference=difference,
    )


def check_overage(
    billed_hours: Decimal,
    billed_amount: Decimal,
    measured_hours: Decimal | None,
    franchise_selection: RuleSelection,
    price_selection: RuleSelection,
    tolerance: Decimal,
) -> tuple[CheckOutcome, CheckOutcome]:
    """Executa as verificações de preço e de quantidade do excedente e devolve as duas."""
    return (
        check_overage_price(billed_hours, billed_amount, price_selection, tolerance),
        check_overage_quantity(
            billed_hours, measured_hours, franchise_selection, price_selection, tolerance
        ),
    )
