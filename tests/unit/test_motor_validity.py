"""Testes da vigência do contrato e da prorrogação: M-08."""

from datetime import date

from backend.motor import (
    ReasonCode,
    ValidityStatus,
    check_contract_validity,
    effective_contract_end,
)
from backend.schemas import ReferencePeriod, ReviewStatus, RuleType
from tests.fixtures.scenario import make_contract, make_rule


def period(text: str) -> ReferencePeriod:
    return ReferencePeriod.model_validate(text)


def extension(status: ReviewStatus, end: date = date(2027, 12, 31)):
    return make_rule("ext", RuleType.EXTENSION, start=None, end=end, status=status)


def test_effective_end_without_extension_is_the_original_end() -> None:
    assert effective_contract_end(make_contract(), []) == date(2026, 12, 31)


def test_effective_end_uses_the_farthest_confirmed_extension() -> None:
    near = make_rule("e1", RuleType.EXTENSION, end=date(2027, 6, 30), status=ReviewStatus.CONFIRMED)
    far = make_rule("e2", RuleType.EXTENSION, end=date(2027, 12, 31), status=ReviewStatus.CORRECTED)
    assert effective_contract_end(make_contract(), [near, far]) == date(2027, 12, 31)


def test_pending_or_rejected_extension_does_not_extend() -> None:
    for status in (ReviewStatus.PENDING_REVIEW, ReviewStatus.REJECTED, ReviewStatus.INCONCLUSIVE):
        assert effective_contract_end(make_contract(), [extension(status)]) == date(2026, 12, 31)


def test_open_ended_contract_has_no_end() -> None:
    assert effective_contract_end(make_contract(end=None), []) is None


def test_m08_period_inside_validity_passes() -> None:
    result = check_contract_validity(make_contract(), [], period("2026-12"))
    assert result.status is ValidityStatus.WITHIN
    assert result.reasons == ()


def test_m08_after_end_without_extension_needs_review_and_mentions_extension() -> None:
    result = check_contract_validity(make_contract(), [], period("2027-01"))
    assert result.status is ValidityStatus.NEEDS_REVIEW
    assert result.reasons[0].code is ReasonCode.OUTSIDE_CONTRACT_VALIDITY
    assert "prorrogação" in result.reasons[0].message
    assert "não foi enviada" in result.reasons[0].message


def test_m08_confirmed_extension_makes_period_pass() -> None:
    result = check_contract_validity(
        make_contract(), [extension(ReviewStatus.CONFIRMED)], period("2027-01")
    )
    assert result.status is ValidityStatus.WITHIN
    assert result.effective_end == date(2027, 12, 31)


def test_m08_pending_extension_still_needs_review_and_is_reported() -> None:
    result = check_contract_validity(
        make_contract(), [extension(ReviewStatus.PENDING_REVIEW)], period("2027-01")
    )
    assert result.status is ValidityStatus.NEEDS_REVIEW
    assert [reason.code for reason in result.reasons] == [
        ReasonCode.OUTSIDE_CONTRACT_VALIDITY,
        ReasonCode.PENDING_EXTENSION,
    ]


def test_period_before_contract_start_needs_review() -> None:
    result = check_contract_validity(make_contract(), [], period("2025-12"))
    assert result.status is ValidityStatus.NEEDS_REVIEW


def test_contract_ending_mid_period_needs_review() -> None:
    contract = make_contract(end=date(2026, 12, 15))
    result = check_contract_validity(contract, [], period("2026-12"))
    assert result.status is ValidityStatus.NEEDS_REVIEW
    assert result.reasons[0].code is ReasonCode.MID_PERIOD_CHANGE
