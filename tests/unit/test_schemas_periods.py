"""Testes de competencia e vigencia: S-01 e S-07."""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from backend.schemas import ReferencePeriod, ValidityPeriod


def test_reference_period_gives_first_and_last_day() -> None:
    period = ReferencePeriod.model_validate("2026-10")
    assert period.first_day == date(2026, 10, 1)
    assert period.last_day == date(2026, 10, 31)
    assert str(period) == "2026-10"


def test_reference_period_handles_february_and_leap_year() -> None:
    assert ReferencePeriod.model_validate("2026-02").last_day == date(2026, 2, 28)
    assert ReferencePeriod.model_validate("2028-02").last_day == date(2028, 2, 29)


@pytest.mark.parametrize(
    "invalid", ["2026-13", "2026-00", "2026-1", "26-10", "2026/10", "2026-10-01"]
)
def test_s07_invalid_reference_period_fails(invalid: str) -> None:
    with pytest.raises(ValidationError):
        ReferencePeriod.model_validate(invalid)


def test_reference_period_round_trips_as_text() -> None:
    period = ReferencePeriod.model_validate("2026-10")
    assert period.model_dump() == "2026-10"
    assert ReferencePeriod.model_validate(period.model_dump()) == period


def test_s01_end_before_start_fails() -> None:
    with pytest.raises(ValidationError):
        ValidityPeriod(start=date(2026, 10, 1), end=date(2026, 9, 30))


def test_validity_end_is_inclusive_and_open_end_is_none() -> None:
    same_day = ValidityPeriod(start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert same_day.end == same_day.start
    assert ValidityPeriod(start=date(2026, 10, 1)).end is None


def test_validity_rejects_datetime() -> None:
    with pytest.raises(ValidationError):
        ValidityPeriod(start=datetime(2026, 10, 1, 8, 30))
    with pytest.raises(ValidationError):
        ValidityPeriod.model_validate({"start": "2026-10-01T08:30:00"})
