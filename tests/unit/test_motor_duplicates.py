"""Testes da detecção de duplicidade: M-12."""

from backend.motor import check_duplicates
from backend.schemas import CheckResult, DivergenceDirection
from tests.fixtures.scenario import make_invoice, monthly_item


def invoice(
    invoice_id: str, number: str, period: str, amount: str, contract: str | None = "CT-2026-001"
):
    return make_invoice(invoice_id, number, period, [monthly_item(amount)], contract_id=contract)


def test_m12_same_number_flags_both_invoices() -> None:
    alerts = check_duplicates(
        [invoice("a", "500", "2026-04", "100"), invoice("b", "500", "2026-05", "200")]
    )
    assert set(alerts) == {"a", "b"}
    assert alerts["a"].result is CheckResult.REVIEW
    assert alerts["a"].direction is DivergenceDirection.POSSIBLE_DIVERGENCE
    assert "mesmo número" in alerts["a"].message
    assert "Pode ser legítimo" in alerts["a"].message


def test_m12_same_period_and_value_flags_both_invoices() -> None:
    alerts = check_duplicates(
        [invoice("a", "105", "2026-08", "10800"), invoice("b", "106", "2026-08", "10800")]
    )
    assert set(alerts) == {"a", "b"}
    assert "mesma competência (2026-08) e mesmo valor (R$ 10.800,00)" in alerts["b"].message


def test_number_comparison_ignores_case_and_spaces() -> None:
    alerts = check_duplicates(
        [invoice("a", " nf-1 ", "2026-04", "1"), invoice("b", "NF-1", "2026-05", "2")]
    )
    assert set(alerts) == {"a", "b"}


def test_same_value_in_different_periods_is_not_duplicate() -> None:
    assert (
        check_duplicates([invoice("a", "1", "2026-04", "100"), invoice("b", "2", "2026-05", "100")])
        == {}
    )


def test_same_period_with_different_values_is_not_duplicate() -> None:
    assert (
        check_duplicates([invoice("a", "1", "2026-04", "100"), invoice("b", "2", "2026-04", "101")])
        == {}
    )


def test_other_contracts_and_missing_contracts_are_not_compared() -> None:
    other = check_duplicates(
        [invoice("a", "1", "2026-04", "100", "CT-1"), invoice("b", "1", "2026-04", "100", "CT-2")]
    )
    none = check_duplicates(
        [invoice("a", "1", "2026-04", "100", None), invoice("b", "1", "2026-04", "100", None)]
    )
    assert other == {}
    assert none == {}


def test_same_invoice_listed_twice_is_not_a_duplicate_of_itself() -> None:
    single = invoice("a", "1", "2026-04", "100")
    assert check_duplicates([single, single]) == {}


def test_three_way_duplicate_lists_every_match() -> None:
    alerts = check_duplicates(
        [
            invoice("a", "1", "2026-04", "100"),
            invoice("b", "2", "2026-04", "100"),
            invoice("c", "3", "2026-04", "100"),
        ]
    )
    assert set(alerts) == {"a", "b", "c"}
    assert len(alerts["a"].calculation_memory) == 2
