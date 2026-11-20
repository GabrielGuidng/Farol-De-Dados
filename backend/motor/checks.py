"""Verificações de cobrança: preço mensal, preço do excedente e quantidade de horas excedentes.

Só vira DIVERGENCE quando todas as regras usadas são CONFIRMED ou CORRECTED; com regra não
revisada, o máximo é revisão ("possível divergência"). Incerteza é resultado, não exceção.
"""

from decimal import Decimal

from backend.motor.calculator import (
    percentage,
    require_decimal,
    round_money,
    subtract_money,
)
from backend.motor.formatting import (
    describe_rule_source,
    format_money,
    format_percent,
)
from backend.motor.results import CheckOutcome, RuleSelection, SelectionStatus
from backend.schemas import Check, CheckResult, ContractRule, DivergenceDirection, RuleType

MONTHLY_PRICE_CHECK = "monthly_price"


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
