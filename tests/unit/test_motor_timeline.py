"""Testes da linha do tempo e da seleção da regra vigente: M-01 (Caso B), M-02, M-05, M-09."""

from datetime import date

from backend.motor import (
    ReasonCode,
    apply_amendments,
)
from backend.schemas import RuleRelation
from tests.fixtures.scenario import base_rules, dev_amendments, make_amendment, make_rule

JAN = date(2026, 1, 1)


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
