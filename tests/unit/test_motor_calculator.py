"""Testes da calculadora monetária: M-03."""

from decimal import Decimal

import pytest

from backend.motor import (
    add_money,
    multiply_money,
    percentage,
    round_money,
    subtract_money,
)


def test_m03_decimal_arithmetic_is_exact() -> None:
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    assert add_money(Decimal("0.1"), Decimal("0.2")) == Decimal("0.30")


def test_m03_round_money_uses_half_up_with_two_places() -> None:
    assert round_money(Decimal("0.125")) == Decimal("0.13")
    assert round_money(Decimal("2.675")) == Decimal("2.68")
    assert round_money(Decimal("-0.125")) == Decimal("-0.13")
    assert round_money(Decimal("10")) == Decimal("10.00")


def test_m03_rounds_only_the_result_not_intermediates() -> None:
    # Arredondar cada parcela daria 0.00; arredondar só o total dá 0.01.
    assert add_money(Decimal("0.004"), Decimal("0.004")) == Decimal("0.01")
    # Preço unitário com 10 casas mantém a precisão: 3 × 0.3333333333 = 0.9999999999 → 1.00.
    assert multiply_money(Decimal("3"), Decimal("0.3333333333")) == Decimal("1.00")
    assert multiply_money(Decimal("8"), Decimal("150.00")) == Decimal("1200.00")


def test_subtract_money_keeps_sign() -> None:
    assert subtract_money(Decimal("10800.00"), Decimal("10500.00")) == Decimal("300.00")
    assert subtract_money(Decimal("10000.00"), Decimal("10500.00")) == Decimal("-500.00")


def test_percentage_matches_case_a() -> None:
    assert percentage(Decimal("300.00"), Decimal("10500.00")) == Decimal("2.86")
    with pytest.raises(ValueError):
        percentage(Decimal("1"), Decimal("0"))


@pytest.mark.parametrize(
    "call",
    [
        lambda: round_money(1.5),
        lambda: add_money(Decimal("1"), 0.1),
        lambda: subtract_money(1.5, Decimal("1")),
        lambda: multiply_money(2.0, Decimal("1")),
        lambda: multiply_money(Decimal("1"), 0.5),
        lambda: percentage(1.0, Decimal("2")),
    ],
)
def test_m03_float_raises_type_error(call) -> None:
    with pytest.raises(TypeError):
        call()


def test_other_types_raise_type_error_and_nan_value_error() -> None:
    with pytest.raises(TypeError):
        round_money("1.00")
    with pytest.raises(TypeError):
        round_money(True)
    with pytest.raises(ValueError):
        round_money(Decimal("NaN"))
