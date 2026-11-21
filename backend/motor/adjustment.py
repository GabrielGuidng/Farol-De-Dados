"""Reajuste periódico: detecta se um reajuste é devido; não calcula índices oficiais."""

import calendar
from collections.abc import Sequence
from datetime import date

from backend.motor.results import RuleSelection, SelectionStatus
from backend.motor.timeline import select_applicable_rule
from backend.schemas import (
    Check,
    CheckResult,
    ContractRule,
    DivergenceDirection,
    ReferencePeriod,
)

ADJUSTMENT_CHECK = "periodic_adjustment"
DEFAULT_PERIODICITY_MONTHS = 12


def add_months(day: date, months: int) -> date:
    """Soma meses a uma data; se o dia não existir no mês de destino, usa o último dia."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def latest_due_adjustment(
    base_date: date, periodicity_months: int, reference_period: ReferencePeriod
) -> date | None:
    """
    Recebe a data-base e a periodicidade; devolve a data do último reajuste devido até o fim da
    competência (base + n × periodicidade, n ≥ 1), ou None se nenhum for devido ainda.
    """
    latest: date | None = None
    step = 1
    while True:
        due = add_months(base_date, step * periodicity_months)
        if due > reference_period.last_day:
            return latest
        latest = due
        step += 1


def check_periodic_adjustment(
    adjustment_rules: Sequence[ContractRule],
    price_selection: RuleSelection,
    reference_period: ReferencePeriod,
) -> Check | None:
    """
    Recebe as regras de reajuste, a regra de preço vigente e a competência.
    Se o preço vigente começou antes do último reajuste devido, devolve REVIEW (possível
    reajuste não aplicado). Sem regra de reajuste vigente, devolve None. Índices não são calculados.
    """
    selection = select_applicable_rule(adjustment_rules, reference_period)
    if selection.status is SelectionStatus.NO_RULE:
        return None
    if selection.rule is None:
        return Check(
            name=ADJUSTMENT_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message="Não foi possível escolher a regra de reajuste. "
            + " ".join(reason.message for reason in selection.reasons),
        )
    rule = selection.rule
    base_date = rule.validity.start
    if base_date is None:
        return Check(
            name=ADJUSTMENT_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message="A regra de reajuste não informa a data-base; o sistema não presume uma data.",
            rule_id=rule.id,
        )
    periodicity = rule.periodicity_months or DEFAULT_PERIODICITY_MONTHS
    due = latest_due_adjustment(base_date, periodicity, reference_period)
    memory = [
        f"data-base = {base_date.isoformat()}",
        f"periodicidade = {periodicity} meses",
        "último reajuste devido = " + ("nenhum" if due is None else due.isoformat()),
    ]
    if due is None:
        return Check(
            name=ADJUSTMENT_CHECK,
            result=CheckResult.OK,
            message=f"Nenhum reajuste é devido até a competência {reference_period}.",
            calculation_memory=tuple(memory),
            rule_id=rule.id,
        )
    price = price_selection.rule if price_selection.status is SelectionStatus.SELECTED else None
    price_start = None if price is None else price.validity.start
    if price is None or price_start is None:
        return Check(
            name=ADJUSTMENT_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message=(
                f"Reajuste devido em {due.isoformat()}, mas não há preço vigente com início "
                "conhecido para conferir."
            ),
            calculation_memory=tuple(memory),
            rule_id=rule.id,
        )
    memory.append(f"início do preço vigente = {price_start.isoformat()}")
    if price_start < due:
        index = f" ({rule.adjustment_index})" if rule.adjustment_index else ""
        return Check(
            name=ADJUSTMENT_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
            message=(
                f"Reajuste{index} possivelmente devido em {due.isoformat()}: o preço vigente "
                f"começou em {price_start.isoformat()}, antes dessa data. O sistema não calcula "
                "índices oficiais; revisão humana necessária."
            ),
            calculation_memory=tuple(memory),
            rule_id=rule.id,
        )
    return Check(
        name=ADJUSTMENT_CHECK,
        result=CheckResult.OK,
        message="O preço vigente é posterior ao último reajuste devido.",
        calculation_memory=tuple(memory),
        rule_id=rule.id,
    )
