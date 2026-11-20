"""Testes de tipos base e enums: S-03, S-04, S-05 e S-08."""

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.schemas import (
    CLASSIFICATION_SEVERITY_ORDER,
    AuditClassification,
    Money,
    ReviewStatus,
    RuleRelation,
    RuleType,
    StrictModel,
    UnitPrice,
    most_severe,
)

money = TypeAdapter(Money)
unit_price = TypeAdapter(UnitPrice)


class _Sample(StrictModel):
    amount: Money


def test_s04_money_rejects_float() -> None:
    with pytest.raises(ValidationError):
        money.validate_python(10.5)


def test_money_accepts_string_and_int() -> None:
    assert money.validate_python("10500.00") == Decimal("10500.00")
    assert money.validate_python(10) == Decimal("10")


def test_s03_money_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        money.validate_python("-0.01")


def test_money_rejects_nan() -> None:
    with pytest.raises(ValidationError):
        money.validate_python("NaN")


def test_unit_price_allows_ten_decimal_places() -> None:
    assert unit_price.validate_python("12.3456789012") == Decimal("12.3456789012")


def test_unit_price_rejects_more_than_ten_places_float_and_negative() -> None:
    for invalid in ("1.12345678901", 1.5, "-1"):
        with pytest.raises(ValidationError):
            unit_price.validate_python(invalid)


def test_s05_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _Sample.model_validate({"amount": "1.00", "unexpected": 1})


def test_strict_model_is_frozen() -> None:
    sample = _Sample(amount=Decimal("1.00"))
    with pytest.raises(ValidationError):
        sample.amount = Decimal("2.00")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("enum_type", "invalid"),
    [
        (RuleRelation, "SUBSTITUTED"),
        (ReviewStatus, "UNKNOWN"),
        (AuditClassification, "MAYBE"),
        (RuleType, "DISCOUNT"),
    ],
)
def test_s08_invalid_enum_value_is_rejected(enum_type: type, invalid: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(enum_type).validate_python(invalid)


def test_enum_values_match_canonical_vocabulary() -> None:
    assert {item.value for item in ReviewStatus} == {
        "PENDING_REVIEW",
        "CONFIRMED",
        "CORRECTED",
        "REJECTED",
        "INCONCLUSIVE",
    }
    assert {item.value for item in RuleRelation} == {"CREATED", "ALTERED", "REVOKED"}


def test_severity_order_is_defined_in_one_place() -> None:
    assert [item.value for item in CLASSIFICATION_SEVERITY_ORDER] == [
        "MISSING_DOCUMENTATION",
        "DIVERGENCE",
        "NEEDS_REVIEW",
        "NO_DIVERGENCE",
    ]
    assert set(CLASSIFICATION_SEVERITY_ORDER) == set(AuditClassification)
    assert (
        most_severe([AuditClassification.NEEDS_REVIEW, AuditClassification.DIVERGENCE])
        is AuditClassification.DIVERGENCE
    )


def test_only_confirmed_and_corrected_are_trusted() -> None:
    trusted = {item for item in ReviewStatus if item.is_trusted}
    assert trusted == {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED}
