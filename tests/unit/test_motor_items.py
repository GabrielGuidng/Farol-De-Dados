"""Testes da classificação de itens da cobrança: M-11."""

from decimal import Decimal

from backend.motor import (
    ReasonCode,
    check_item_classification,
    check_items_total,
    classify_items,
)
from backend.schemas import CheckResult
from tests.fixtures.scenario import make_item, monthly_item, overage_item

ZERO = Decimal("0.00")


def test_single_base_item_and_overage_item_are_separated() -> None:
    result = classify_items([monthly_item("10800.00"), overage_item("8")])
    assert len(result.base_items) == 1
    assert len(result.overage_items) == 1
    assert result.base_unambiguous
    assert result.reasons == ()
    assert result.overage_hours == Decimal("8")
    assert result.overage_amount == Decimal("1200.00")
    assert check_item_classification(result).result is CheckResult.OK


def test_m11_word_adicional_alone_does_not_make_an_overage_item() -> None:
    result = classify_items(
        [monthly_item("10800.00"), make_item("Hora adicional", "8", "150", "1200")]
    )
    assert result.overage_items == ()
    assert len(result.ambiguous_items) == 1
    assert not result.base_unambiguous
    assert [reason.code for reason in result.reasons] == [ReasonCode.AMBIGUOUS_ITEM]
    assert check_item_classification(result).result is CheckResult.REVIEW


def test_m11_adicional_together_with_excedente_is_overage() -> None:
    result = classify_items(
        [monthly_item("10800.00"), make_item("Horas adicionais excedentes", "2", "150", "300")]
    )
    assert len(result.overage_items) == 1
    assert result.ambiguous_items == ()


def test_overage_terms_ignore_case_and_accents() -> None:
    for description in ("EXCEDENTE de horas", "Horas em Excesso", "Horas acima da franquia"):
        result = classify_items([monthly_item("100"), make_item(description, "1", "1", "1")])
        assert len(result.overage_items) == 1, description


def test_m11_more_than_one_base_item_is_ambiguous() -> None:
    result = classify_items(
        [monthly_item("10000.00"), make_item("Licença de software", "1", "800", "800")]
    )
    assert len(result.base_items) == 2
    assert not result.base_unambiguous
    assert [reason.code for reason in result.reasons] == [ReasonCode.MULTIPLE_BASE_ITEMS]
    assert check_item_classification(result).result is CheckResult.REVIEW


def test_only_overage_items_and_no_items_need_review() -> None:
    only_overage = classify_items([overage_item("3")])
    assert [reason.code for reason in only_overage.reasons] == [ReasonCode.NO_BASE_ITEM]
    empty = classify_items([])
    assert [reason.code for reason in empty.reasons] == [ReasonCode.NO_ITEMS]
    assert not empty.base_unambiguous


def test_overage_totals_are_zero_without_overage_items() -> None:
    result = classify_items([monthly_item("100")])
    assert result.overage_hours == 0
    assert result.overage_amount == ZERO


def test_items_total_matches_invoice_total() -> None:
    items = [monthly_item("10800.00"), overage_item("8")]
    check = check_items_total(items, Decimal("12000.00"), ZERO)
    assert check.result is CheckResult.OK
    assert "soma dos itens = R$ 12.000,00" in check.calculation_memory


def test_m11_taxes_or_discounts_make_the_total_differ_and_need_review() -> None:
    items = [monthly_item("10000.00")]
    for total in ("10500.00", "9500.00"):
        check = check_items_total(items, Decimal(total), ZERO)
        assert check.result is CheckResult.REVIEW
        assert "impostos" in check.message


def test_items_total_respects_tolerance_and_missing_items() -> None:
    items = [monthly_item("10000.00")]
    assert check_items_total(items, Decimal("10000.50"), Decimal("1.00")).result is CheckResult.OK
    assert check_items_total([], Decimal("10"), ZERO).result is CheckResult.REVIEW
