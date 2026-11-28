"""Testes da classificação de itens da cobrança: M-11."""

from decimal import Decimal

from backend.motor import (
    HOURLY_LIMIT_CHECK,
    HOURS_WITHOUT_OS_CHECK,
    ITEMS_ARITHMETIC_CHECK,
    ReasonCode,
    RuleSelection,
    SelectionStatus,
    check_hourly_items,
    check_item_classification,
    check_items_arithmetic,
    check_items_total,
    classify_items,
    select_fixed_base_item,
)
from backend.schemas import CheckResult, RuleType
from tests.fixtures.scenario import (
    make_invoice,
    make_item,
    make_rule,
    monthly_item,
    overage_item,
)

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


def test_discount_sla_items_are_separated_and_reconciled_in_total() -> None:
    items = [
        monthly_item("10000.00"),
        make_item("Glosa de SLA (indisponibilidade)", "1", "500.00", "500.00"),
    ]
    cls = classify_items(items)
    assert cls.base_unambiguous
    assert len(cls.discount_items) == 1
    assert check_items_total(items, Decimal("9500.00"), ZERO).result is CheckResult.OK


def test_items_arithmetic_and_fixed_plus_hourly_checks() -> None:
    bad_qty_item = make_item("Horas técnicas (OS 42)", "10", "150.00", "1800.00", "h")
    arith_chk = check_items_arithmetic([bad_qty_item], ZERO)
    assert arith_chk is not None
    assert arith_chk.name == ITEMS_ARITHMETIC_CHECK
    assert arith_chk.result is CheckResult.DIVERGENCE
    assert "R$ 300,00" in arith_chk.message

    fixed = monthly_item("10000.00")
    hours_no_os = make_item("Horas técnicas sob demanda", "50", "150.00", "7500.00", "h")
    selected = select_fixed_base_item([fixed, hours_no_os])
    assert selected is fixed
    # Dois itens fixos sem item variável/horário continuam ambíguos (preserva M-11).
    two_fixed = [fixed, make_item("Licença de software", "1", "800", "800")]
    assert select_fixed_base_item(two_fixed) is None

    inv = make_invoice("inv-h1", "8823", "2026-04", [fixed, hours_no_os], total="17500.00")
    franchise_sel = RuleSelection(
        status=SelectionStatus.SELECTED,
        rule=make_rule("fran-1", RuleType.FRANCHISE, quantity="40", start=inv.issue_date),
    )
    overage_sel = RuleSelection(
        status=SelectionStatus.SELECTED,
        rule=make_rule("over-1", RuleType.OVERAGE_PRICE, value="150.00", start=inv.issue_date),
    )
    h_checks, h_applied = check_hourly_items(
        inv,
        franchise_selection=franchise_sel,
        overage_selection=overage_sel,
        tolerance=ZERO,
    )
    assert [c.name for c in h_checks] == [HOURLY_LIMIT_CHECK, HOURS_WITHOUT_OS_CHECK]
    assert "R$ 1.500,00" in h_checks[0].message
    assert len(h_applied) == 2
