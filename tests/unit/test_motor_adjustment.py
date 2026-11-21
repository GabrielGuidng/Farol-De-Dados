"""Testes do reajuste periódico: M-13."""

from datetime import date

from backend.motor import (
    RuleSelection,
    SelectionStatus,
    add_months,
    check_periodic_adjustment,
    latest_due_adjustment,
    select_applicable_rule,
)
from backend.schemas import (
    CheckResult,
    ContractRule,
    DivergenceDirection,
    ReferencePeriod,
    RuleType,
)
from tests.fixtures.scenario import make_rule

JAN_2026 = date(2026, 1, 1)


def period(text: str) -> ReferencePeriod:
    return ReferencePeriod.model_validate(text)


def adjustment_rule(**extra: object) -> ContractRule:
    rule = make_rule("adj", RuleType.ADJUSTMENT, start=JAN_2026, index="IPCA")
    return rule.model_copy(update=extra)


def price_selection(start: date) -> RuleSelection:
    rule = make_rule("price", value="10000", start=start)
    return select_applicable_rule([rule], period("2027-02"))


def test_add_months_handles_month_end_and_year_rollover() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert add_months(date(2026, 1, 1), 12) == date(2027, 1, 1)


def test_m13_latest_due_adjustment_every_twelve_months() -> None:
    assert latest_due_adjustment(JAN_2026, 12, period("2026-12")) is None
    assert latest_due_adjustment(JAN_2026, 12, period("2027-01")) == date(2027, 1, 1)
    assert latest_due_adjustment(JAN_2026, 12, period("2028-06")) == date(2028, 1, 1)
    assert latest_due_adjustment(JAN_2026, 6, period("2026-12")) == date(2026, 7, 1)


def test_m13_no_adjustment_rule_means_no_check() -> None:
    selection = price_selection(JAN_2026)
    assert check_periodic_adjustment([], selection, period("2027-02")) is None


def test_m13_nothing_due_yet_is_ok() -> None:
    check = check_periodic_adjustment(
        [adjustment_rule()], price_selection(JAN_2026), period("2026-10")
    )
    assert check is not None
    assert check.result is CheckResult.OK


def test_m13_price_older_than_due_adjustment_needs_review() -> None:
    check = check_periodic_adjustment(
        [adjustment_rule()], price_selection(date(2026, 9, 1)), period("2027-02")
    )
    assert check is not None
    assert check.result is CheckResult.REVIEW
    assert check.direction is DivergenceDirection.POSSIBLE_DIVERGENCE
    assert "2027-01-01" in check.message
    assert "IPCA" in check.message
    assert "não calcula índices" in check.message


def test_m13_price_after_the_due_adjustment_has_no_alert() -> None:
    check = check_periodic_adjustment(
        [adjustment_rule()], price_selection(date(2027, 1, 1)), period("2027-02")
    )
    assert check is not None
    assert check.result is CheckResult.OK
    assert (
        check_periodic_adjustment(
            [adjustment_rule()], price_selection(date(2027, 1, 15)), period("2027-02")
        ).result
        is CheckResult.OK
    )


def test_m13_default_periodicity_is_twelve_and_custom_is_respected() -> None:
    selection = price_selection(JAN_2026)
    default = check_periodic_adjustment([adjustment_rule()], selection, period("2026-08"))
    assert default is not None and default.result is CheckResult.OK
    custom = check_periodic_adjustment(
        [adjustment_rule(periodicity_months=6)], selection, period("2026-08")
    )
    assert custom is not None and custom.result is CheckResult.REVIEW


def test_m13_without_price_selection_the_adjustment_cannot_be_checked() -> None:
    none = RuleSelection(status=SelectionStatus.NO_RULE)
    check = check_periodic_adjustment([adjustment_rule()], none, period("2027-02"))
    assert check is not None
    assert check.result is CheckResult.REVIEW
    assert check.direction is DivergenceDirection.UNVERIFIABLE


def test_m13_adjustment_rule_without_base_date_is_not_presumed() -> None:
    rule = make_rule("adj", RuleType.ADJUSTMENT, index="IPCA")
    check = check_periodic_adjustment([rule], price_selection(JAN_2026), period("2027-02"))
    assert check is not None
    assert check.result is CheckResult.REVIEW
