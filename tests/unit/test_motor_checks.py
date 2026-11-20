"""Testes das verificações de preço mensal, franquia e excedente: M-01 (Caso A), M-04, M-06."""

from datetime import date
from decimal import Decimal

import pytest

from backend.motor import (
    apply_amendments,
    check_monthly_price,
    check_overage,
    check_overage_price,
    check_overage_quantity,
    select_applicable_rule,
)
from backend.motor.results import RuleSelection, SelectionStatus
from backend.schemas import (
    CheckResult,
    DivergenceDirection,
    ReferencePeriod,
    ReviewStatus,
    RuleType,
)
from tests.fixtures.scenario import base_rules, dev_amendments, make_rule

ZERO = Decimal("0.00")


def select(status: ReviewStatus, rule_type: RuleType, text: str) -> RuleSelection:
    timeline = apply_amendments(base_rules(status), dev_amendments(status))
    rules = [rule for rule in timeline.rules if rule.rule_type is rule_type]
    return select_applicable_rule(rules, ReferencePeriod.model_validate(text))


def test_m01_case_a_october_points_300_and_the_amendment_02() -> None:
    selection = select(ReviewStatus.CONFIRMED, RuleType.MONTHLY_PRICE, "2026-10")
    outcome = check_monthly_price(Decimal("10800.00"), selection, ZERO)
    assert outcome.check.result is CheckResult.DIVERGENCE
    assert outcome.direction is DivergenceDirection.OVERCHARGE
    assert outcome.check.direction is DivergenceDirection.OVERCHARGE
    assert outcome.difference == Decimal("300.00")
    assert outcome.percentage == Decimal("2.86")
    assert outcome.base == Decimal("10500.00")
    assert outcome.check.rule_id == "price-add02"
    assert "diferença = cobrado − regra = R$ 300,00 (2,86%)" in outcome.check.calculation_memory
    assert "aditivo-02" in outcome.check.message
    assert "página 2" in outcome.check.message
    assert "Passa a R$ 10.500,00" in outcome.check.message


def test_m01_case_b_april_is_ok_with_base_rule() -> None:
    selection = select(ReviewStatus.CONFIRMED, RuleType.MONTHLY_PRICE, "2026-04")
    outcome = check_monthly_price(Decimal("10000.00"), selection, ZERO)
    assert outcome.check.result is CheckResult.OK
    assert outcome.direction is None
    assert outcome.difference == ZERO


def test_m06_pending_rule_never_yields_divergence() -> None:
    selection = select(ReviewStatus.PENDING_REVIEW, RuleType.MONTHLY_PRICE, "2026-10")
    outcome = check_monthly_price(Decimal("10800.00"), selection, ZERO)
    assert outcome.check.result is CheckResult.REVIEW
    assert outcome.direction is DivergenceDirection.POSSIBLE_DIVERGENCE
    assert "possível divergência" in outcome.check.message.lower()
    assert outcome.difference == Decimal("300.00")


@pytest.mark.parametrize("status", [ReviewStatus.PENDING_REVIEW, ReviewStatus.INCONCLUSIVE])
def test_m06_untrusted_statuses_are_capped_at_review(status: ReviewStatus) -> None:
    selection = select(status, RuleType.MONTHLY_PRICE, "2026-10")
    outcome = check_monthly_price(Decimal("99999"), selection, ZERO)
    assert outcome.check.result is CheckResult.REVIEW
    assert outcome.direction is DivergenceDirection.POSSIBLE_DIVERGENCE


def test_corrected_rule_is_trusted() -> None:
    selection = select(ReviewStatus.CORRECTED, RuleType.MONTHLY_PRICE, "2026-10")
    outcome = check_monthly_price(Decimal("10800.00"), selection, ZERO)
    assert outcome.check.result is CheckResult.DIVERGENCE
    assert outcome.direction is DivergenceDirection.OVERCHARGE


def test_pending_rule_with_matching_value_is_not_silently_ok() -> None:
    selection = select(ReviewStatus.PENDING_REVIEW, RuleType.MONTHLY_PRICE, "2026-10")
    outcome = check_monthly_price(Decimal("10500.00"), selection, ZERO)
    assert outcome.check.result is CheckResult.REVIEW
    assert outcome.direction is DivergenceDirection.UNVERIFIABLE


def test_difference_within_contract_tolerance_is_ok() -> None:
    selection = select(ReviewStatus.CONFIRMED, RuleType.MONTHLY_PRICE, "2026-10")
    assert check_monthly_price(Decimal("10500.50"), selection, Decimal("1.00")).check.result is (
        CheckResult.OK
    )
    assert check_monthly_price(Decimal("10501.00"), selection, Decimal("1.00")).check.result is (
        CheckResult.OK
    )
    assert check_monthly_price(Decimal("10501.01"), selection, Decimal("1.00")).check.result is (
        CheckResult.DIVERGENCE
    )


def test_undercharge_is_also_a_divergence_with_negative_difference() -> None:
    selection = select(ReviewStatus.CONFIRMED, RuleType.MONTHLY_PRICE, "2026-10")
    outcome = check_monthly_price(Decimal("10000.00"), selection, ZERO)
    assert outcome.check.result is CheckResult.DIVERGENCE
    assert outcome.direction is DivergenceDirection.UNDERCHARGE
    assert outcome.difference == Decimal("-500.00")
    assert "a menos" in outcome.check.message


def test_selection_without_rule_becomes_review_with_reasons() -> None:
    selection = select(ReviewStatus.CONFIRMED, RuleType.MONTHLY_PRICE, "2025-01")
    assert selection.status is SelectionStatus.NO_RULE
    outcome = check_monthly_price(Decimal("10000.00"), selection, ZERO)
    assert outcome.check.result is CheckResult.REVIEW
    assert outcome.direction is DivergenceDirection.UNVERIFIABLE
    assert "Nenhuma regra vigente" in outcome.check.message


def test_conflicting_selection_becomes_review() -> None:
    rules = [
        make_rule("a", value="100", start=date(2026, 1, 1)),
        make_rule("b", value="200", start=date(2026, 1, 1)),
    ]
    selection = select_applicable_rule(rules, ReferencePeriod.model_validate("2026-04"))
    outcome = check_monthly_price(Decimal("100"), selection, ZERO)
    assert outcome.check.result is CheckResult.REVIEW
    assert outcome.direction is DivergenceDirection.UNVERIFIABLE


def test_checks_reject_float_and_wrong_rule_type() -> None:
    selection = select(ReviewStatus.CONFIRMED, RuleType.MONTHLY_PRICE, "2026-10")
    with pytest.raises(TypeError):
        check_monthly_price(10800.0, selection, ZERO)
    with pytest.raises(TypeError):
        check_monthly_price(Decimal("1"), selection, 0.0)
    overage = select(ReviewStatus.CONFIRMED, RuleType.OVERAGE_PRICE, "2026-10")
    with pytest.raises(ValueError):
        check_monthly_price(Decimal("1"), overage, ZERO)


def overage_selections(status: ReviewStatus = ReviewStatus.CONFIRMED, text: str = "2026-07"):
    return (
        select(status, RuleType.FRANCHISE, text),
        select(status, RuleType.OVERAGE_PRICE, text),
    )


def test_m04_eight_hours_billed_with_six_permitted_points_300_more() -> None:
    franchise, price = overage_selections()
    outcome = check_overage_quantity(Decimal("8"), Decimal("46"), franchise, price, ZERO)
    assert outcome.check.result is CheckResult.DIVERGENCE
    assert outcome.direction is DivergenceDirection.OVERCHARGE
    assert outcome.difference == Decimal("300.00")
    assert outcome.base == Decimal("900.00")
    memory = outcome.check.calculation_memory
    assert "horas permitidas = máx(0, 46 − 40) = 6" in memory
    assert "valor a mais = 2 × R$ 150,00 = R$ 300,00" in memory


def test_m04_overage_price_matches_hours_times_price() -> None:
    _, price = overage_selections()
    outcome = check_overage_price(Decimal("8"), Decimal("1200.00"), price, ZERO)
    assert outcome.check.result is CheckResult.OK
    assert outcome.direction is None
    assert outcome.base == Decimal("1200.00")


def test_m04_overage_price_mismatch_is_divergence() -> None:
    _, price = overage_selections()
    outcome = check_overage_price(Decimal("8"), Decimal("1300.00"), price, ZERO)
    assert outcome.check.result is CheckResult.DIVERGENCE
    assert outcome.direction is DivergenceDirection.OVERCHARGE
    assert outcome.difference == Decimal("100.00")
    under = check_overage_price(Decimal("8"), Decimal("1100.00"), price, ZERO)
    assert under.check.result is CheckResult.DIVERGENCE
    assert under.direction is DivergenceDirection.UNDERCHARGE


def test_m04_overage_without_measurement_needs_review() -> None:
    franchise, price = overage_selections(text="2026-11")
    outcome = check_overage_quantity(Decimal("3"), None, franchise, price, ZERO)
    assert outcome.check.result is CheckResult.REVIEW
    assert outcome.direction is DivergenceDirection.UNVERIFIABLE
    assert "sem medição" in outcome.check.message


def test_m04_hours_within_the_permitted_amount_are_ok() -> None:
    franchise, price = overage_selections()
    outcome = check_overage_quantity(Decimal("6"), Decimal("46"), franchise, price, ZERO)
    assert outcome.check.result is CheckResult.OK
    assert outcome.direction is None
    assert outcome.difference == ZERO


def test_m04_measurement_below_franchise_permits_zero_hours() -> None:
    franchise, price = overage_selections()
    outcome = check_overage_quantity(Decimal("2"), Decimal("38"), franchise, price, ZERO)
    assert outcome.check.result is CheckResult.DIVERGENCE
    assert outcome.direction is DivergenceDirection.OVERCHARGE
    assert outcome.difference == Decimal("300.00")
    assert "máx(0, 38 − 40) = 0" in outcome.check.calculation_memory[2]


def test_m04_no_overage_billed_needs_no_selection_or_measurement() -> None:
    empty = RuleSelection(status=SelectionStatus.NO_RULE)
    price = check_overage_price(Decimal("0"), Decimal("0.00"), empty, ZERO)
    quantity = check_overage_quantity(Decimal("0"), None, empty, empty, ZERO)
    assert price.check.result is CheckResult.OK
    assert quantity.check.result is CheckResult.OK


def test_m04_pending_rules_cap_overage_divergence_at_review() -> None:
    franchise, price = overage_selections(ReviewStatus.PENDING_REVIEW)
    outcome = check_overage_quantity(Decimal("8"), Decimal("46"), franchise, price, ZERO)
    assert outcome.check.result is CheckResult.REVIEW
    assert outcome.direction is DivergenceDirection.POSSIBLE_DIVERGENCE
    assert "possível divergência" in outcome.check.message.lower()
    price_outcome = check_overage_price(Decimal("8"), Decimal("1300.00"), price, ZERO)
    assert price_outcome.check.result is CheckResult.REVIEW
    assert price_outcome.direction is DivergenceDirection.POSSIBLE_DIVERGENCE


def test_m04_one_untrusted_rule_is_enough_to_cap_at_review() -> None:
    franchise = select(ReviewStatus.CONFIRMED, RuleType.FRANCHISE, "2026-07")
    price = select(ReviewStatus.PENDING_REVIEW, RuleType.OVERAGE_PRICE, "2026-07")
    outcome = check_overage_quantity(Decimal("8"), Decimal("46"), franchise, price, ZERO)
    assert outcome.check.result is CheckResult.REVIEW


def test_dev_scenario_invoice_103_combines_price_and_quantity_checks() -> None:
    franchise, price = overage_selections()
    price_outcome, quantity_outcome = check_overage(
        Decimal("8"), Decimal("1200.00"), Decimal("46"), franchise, price, ZERO
    )
    assert price_outcome.check.result is CheckResult.OK
    assert quantity_outcome.check.result is CheckResult.DIVERGENCE
    assert quantity_outcome.difference == Decimal("300.00")


def test_dev_scenario_invoice_108_without_measurement_is_review() -> None:
    franchise, price = overage_selections(text="2026-11")
    price_outcome, quantity_outcome = check_overage(
        Decimal("3"), Decimal("450.00"), None, franchise, price, ZERO
    )
    assert price_outcome.check.result is CheckResult.OK
    assert quantity_outcome.check.result is CheckResult.REVIEW


def test_overage_checks_reject_float() -> None:
    franchise, price = overage_selections()
    with pytest.raises(TypeError):
        check_overage_quantity(8.0, Decimal("46"), franchise, price, ZERO)
    with pytest.raises(TypeError):
        check_overage_quantity(Decimal("8"), 46.0, franchise, price, ZERO)
    with pytest.raises(TypeError):
        check_overage_price(Decimal("8"), 1200.0, price, ZERO)
