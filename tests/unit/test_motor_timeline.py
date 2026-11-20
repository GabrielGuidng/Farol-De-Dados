"""Testes da linha do tempo e da seleção da regra vigente: M-01 (Caso B), M-02, M-05, M-09."""

from datetime import date

from backend.motor import (
    ReasonCode,
    SelectionStatus,
    apply_amendments,
    select_applicable_rule,
)
from backend.schemas import ReferencePeriod, ReviewStatus, RuleRelation, RuleType
from tests.fixtures.scenario import base_rules, dev_amendments, make_amendment, make_rule

JAN = date(2026, 1, 1)


def period(text: str) -> ReferencePeriod:
    return ReferencePeriod.model_validate(text)


def by_id(rules, rule_id):
    return next(rule for rule in rules if rule.id == rule_id)


def test_m02_amendment_closes_previous_price_the_day_before_and_franchise_stays_open() -> None:
    result = apply_amendments(base_rules(), dev_amendments()[:1])
    assert result.complete
    assert by_id(result.rules, "price-base").validity.end == date(2026, 4, 30)
    assert by_id(result.rules, "franchise").validity.end is None
    assert by_id(result.rules, "overage").validity.end is None
    new = by_id(result.rules, "price-add01")
    assert new.validity.start == date(2026, 5, 1)
    assert new.validity.end is None
    assert new.version == 2
    assert new.relation is RuleRelation.ALTERED


def test_m02_chain_of_two_amendments() -> None:
    result = apply_amendments(base_rules(), dev_amendments())
    assert by_id(result.rules, "price-base").validity.end == date(2026, 4, 30)
    assert by_id(result.rules, "price-add01").validity.end == date(2026, 8, 31)
    assert by_id(result.rules, "price-add02").validity.start == date(2026, 9, 1)
    assert by_id(result.rules, "price-add02").version == 3


def test_m02_amendment_order_does_not_depend_on_input_order() -> None:
    forward = apply_amendments(base_rules(), dev_amendments())
    backward = apply_amendments(base_rules(), list(reversed(dev_amendments())))
    assert {rule.id: rule.validity for rule in forward.rules} == {
        rule.id: rule.validity for rule in backward.rules
    }


def test_m02_revoked_amendment_closes_rule_without_new_one() -> None:
    revoke = make_amendment("rev", "overage", date(2026, 7, 1), None, RuleRelation.REVOKED)
    result = apply_amendments(base_rules(), [revoke])
    revoked = by_id(result.rules, "overage")
    assert revoked.validity.end == date(2026, 6, 30)
    assert revoked.relation is RuleRelation.REVOKED
    assert len(result.rules) == 3


def test_m02_amendment_does_not_extend_a_rule_that_already_ended() -> None:
    rules = [make_rule("price", value="100", start=JAN, end=date(2026, 3, 31))]
    new = make_rule("price-2", value="120")
    result = apply_amendments(rules, [make_amendment("a", "price", date(2026, 6, 1), new)])
    assert by_id(result.rules, "price").validity.end == date(2026, 3, 31)


def test_m02_problems_become_issues_not_exceptions() -> None:
    new = make_rule("price-2", value="120")
    unknown = make_amendment("a1", "does-not-exist", date(2026, 6, 1), new)
    too_early = make_amendment("a2", "price-base", date(2026, 1, 1), new)
    wrong_type = make_amendment("a3", "franchise", date(2026, 6, 1), make_rule("x", value="1"))
    result = apply_amendments(base_rules(), [unknown, too_early, wrong_type])
    assert not result.complete
    codes = {issue.code for issue in result.issues}
    assert codes == {
        ReasonCode.UNKNOWN_AFFECTED_RULE,
        ReasonCode.INVALID_EFFECTIVE_DATE,
        ReasonCode.AMENDMENT_TYPE_MISMATCH,
    }
    assert [rule.id for rule in result.rules] == ["price-base", "franchise", "overage"]


def test_m05_conflicting_amendments_on_same_date_apply_none() -> None:
    first = make_amendment("a1", "price-base", date(2026, 5, 1), make_rule("p1", value="10800"))
    second = make_amendment("a2", "price-base", date(2026, 5, 1), make_rule("p2", value="11000"))
    result = apply_amendments(base_rules(), [first, second])
    assert {issue.code for issue in result.issues} == {ReasonCode.AMENDMENT_CONFLICT}
    assert by_id(result.rules, "price-base").validity.end is None
    assert len(result.rules) == 3


def test_amendment_start_mismatch_is_flagged_and_effective_date_is_used() -> None:
    new = make_rule("p1", value="10800", start=date(2026, 5, 15))
    result = apply_amendments(
        base_rules(), [make_amendment("a1", "price-base", date(2026, 5, 1), new)]
    )
    assert {issue.code for issue in result.issues} == {ReasonCode.START_DATE_MISMATCH}
    assert by_id(result.rules, "p1").validity.start == date(2026, 5, 1)


def test_duplicate_rule_id_is_reported() -> None:
    rules = [*base_rules(), make_rule("price-base", value="1", start=JAN)]
    result = apply_amendments(rules, [])
    assert {issue.code for issue in result.issues} == {ReasonCode.DUPLICATE_RULE_ID}
    assert len(result.rules) == 3


def test_m01_case_b_april_selects_base_rule_not_the_latest() -> None:
    timeline = apply_amendments(base_rules(), dev_amendments())
    prices = [rule for rule in timeline.rules if rule.rule_type is RuleType.MONTHLY_PRICE]
    selection = select_applicable_rule(prices, period("2026-04"))
    assert selection.status is SelectionStatus.SELECTED
    assert selection.rule is not None and selection.rule.id == "price-base"


def test_m01_case_b_also_holds_without_timeline_reconstruction() -> None:
    prices = [
        make_rule("base", value="10000", start=JAN),
        make_rule("add01", value="10800", start=date(2026, 5, 1)),
    ]
    selection = select_applicable_rule(prices, period("2026-04"))
    assert selection.rule is not None and selection.rule.id == "base"


def test_m01_each_month_selects_the_expected_amendment() -> None:
    timeline = apply_amendments(base_rules(), dev_amendments())
    prices = [rule for rule in timeline.rules if rule.rule_type is RuleType.MONTHLY_PRICE]
    expected = {
        "2026-04": "price-base",
        "2026-05": "price-add01",
        "2026-08": "price-add01",
        "2026-09": "price-add02",
        "2026-10": "price-add02",
        "2026-12": "price-add02",
    }
    for text, rule_id in expected.items():
        selection = select_applicable_rule(prices, period(text))
        assert selection.rule is not None and selection.rule.id == rule_id, text


def test_m05_same_start_and_different_values_selects_none() -> None:
    rules = [
        make_rule("a", value="10000", start=JAN),
        make_rule("b", value="11000", start=JAN),
    ]
    selection = select_applicable_rule(rules, period("2026-04"))
    assert selection.status is SelectionStatus.NEEDS_REVIEW
    assert selection.rule is None
    assert selection.reasons[0].code is ReasonCode.CONFLICT
    assert set(selection.reasons[0].rule_ids) == {"a", "b"}


def test_overlapping_rules_with_same_terms_are_not_a_conflict() -> None:
    rules = [make_rule("a", value="10000", start=JAN), make_rule("b", value="10000.00", start=JAN)]
    selection = select_applicable_rule(rules, period("2026-04"))
    assert selection.status is SelectionStatus.SELECTED


def test_m09_rule_starting_mid_period_needs_review() -> None:
    rules = [
        make_rule("old", value="10000", start=JAN, end=date(2026, 5, 14)),
        make_rule("new", value="10800", start=date(2026, 5, 15)),
    ]
    selection = select_applicable_rule(rules, period("2026-05"))
    assert selection.status is SelectionStatus.NEEDS_REVIEW
    assert selection.rule is None
    assert [reason.code for reason in selection.reasons] == [ReasonCode.MID_PERIOD_CHANGE]


def test_m09_rule_ending_mid_period_needs_review() -> None:
    rules = [make_rule("old", value="10000", start=JAN, end=date(2026, 5, 20))]
    selection = select_applicable_rule(rules, period("2026-05"))
    assert selection.status is SelectionStatus.NEEDS_REVIEW
    assert selection.reasons[0].code is ReasonCode.MID_PERIOD_CHANGE


def test_m09_end_on_last_day_and_start_on_first_day_are_not_mid_period() -> None:
    rules = [
        make_rule("old", value="10000", start=JAN, end=date(2026, 4, 30)),
        make_rule("new", value="10800", start=date(2026, 5, 1)),
    ]
    assert select_applicable_rule(rules, period("2026-04")).rule.id == "old"
    assert select_applicable_rule(rules, period("2026-05")).rule.id == "new"


def test_no_applicable_rule_returns_empty_result_with_reason() -> None:
    rules = [make_rule("old", value="10000", start=JAN, end=date(2026, 3, 31))]
    selection = select_applicable_rule(rules, period("2026-06"))
    assert selection.status is SelectionStatus.NO_RULE
    assert selection.rule is None
    assert selection.reasons[0].code is ReasonCode.NO_APPLICABLE_RULE
    assert select_applicable_rule([], period("2026-06")).status is SelectionStatus.NO_RULE


def test_unknown_start_date_is_not_presumed() -> None:
    selection = select_applicable_rule([make_rule("a", value="10000")], period("2026-06"))
    assert selection.status is SelectionStatus.NEEDS_REVIEW
    assert selection.reasons[0].code is ReasonCode.MISSING_START_DATE


def test_rejected_rules_are_ignored() -> None:
    rules = [
        make_rule("good", value="10000", start=JAN),
        make_rule("bad", value="99999", start=JAN, status=ReviewStatus.REJECTED),
    ]
    assert select_applicable_rule(rules, period("2026-06")).rule.id == "good"


def test_pending_rule_is_selected_but_keeps_its_status() -> None:
    rules = [make_rule("a", value="10000", start=JAN, status=ReviewStatus.PENDING_REVIEW)]
    selection = select_applicable_rule(rules, period("2026-06"))
    assert selection.rule is not None
    assert selection.rule.review_status is ReviewStatus.PENDING_REVIEW


def test_mixed_rule_types_are_a_usage_error() -> None:
    import pytest

    rules = [
        make_rule("a", value="1", start=JAN),
        make_rule("b", RuleType.FRANCHISE, quantity="40", start=JAN),
    ]
    with pytest.raises(ValueError):
        select_applicable_rule(rules, period("2026-06"))
