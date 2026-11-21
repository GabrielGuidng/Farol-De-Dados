"""Testes do auditor e da classificação final: M-10 (e integração dos portões de segurança)."""

from decimal import Decimal

from backend.motor import audit_invoice, audit_invoices
from backend.schemas import (
    AuditClassification,
    CheckResult,
    DivergenceDirection,
    ReviewStatus,
    RuleType,
)
from tests.fixtures.scenario import (
    base_rules,
    dev_amendments,
    dev_documents,
    make_contract,
    make_document,
    make_invoice,
    make_rule,
    monthly_item,
    overage_item,
)


def audit(invoice, status=ReviewStatus.CONFIRMED, documents=None, others=(), contract=...):
    return audit_invoice(
        invoice,
        contract=make_contract() if contract is ... else contract,
        rules=base_rules(status),
        amendments=dev_amendments(status),
        documents=dev_documents() if documents is None else documents,
        other_invoices=others,
    )


def names(result) -> dict[str, CheckResult]:
    return {check.name: check.result for check in result.checks}


def test_m10_confirmed_divergence_with_duplicate_alert_stays_divergence_and_lists_the_alert() -> (
    None
):
    first = make_invoice("a", "201", "2026-10", [monthly_item("10800.00")])
    second = make_invoice("b", "202", "2026-10", [monthly_item("10800.00")])
    results = audit_invoices(
        [first, second],
        contract=make_contract(),
        rules=base_rules(),
        amendments=dev_amendments(),
        documents=dev_documents(),
    )
    for result in results:
        assert result.classification is AuditClassification.DIVERGENCE
        assert names(result)["monthly_price"] is CheckResult.DIVERGENCE
        assert names(result)["duplicate_invoice"] is CheckResult.REVIEW
        assert result.difference == Decimal("300.00")
        assert result.base == Decimal("10500.00")
        assert result.total == Decimal("10800.00")


def test_two_base_items_make_the_price_comparison_ambiguous() -> None:
    invoice = make_invoice(
        "a",
        "201",
        "2026-10",
        [monthly_item("10800.00"), make_item_other()],
    )
    result = audit(invoice)
    # Dois itens base: comparação ambígua → revisão, nunca divergência.
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert names(result)["item_classification"] is CheckResult.REVIEW
    assert "monthly_price" not in names(result)


def make_item_other():
    from tests.fixtures.scenario import make_item

    return make_item("Licença de software", "1", "800", "800")


def test_clean_invoice_is_no_divergence_with_applied_rules_and_memory() -> None:
    invoice = make_invoice("a", "101", "2026-04", [monthly_item("10000.00")], measured_hours="38")
    result = audit(invoice)
    assert result.classification is AuditClassification.NO_DIVERGENCE
    assert result.applied_rules[0].rule_id == "price-base"
    assert result.applied_rules[0].version == 1
    assert result.difference == Decimal("0.00")
    price_check = next(c for c in result.checks if c.name == "monthly_price")
    assert price_check.calculation_memory


def test_applied_rules_carry_the_version_of_the_amendment_chain() -> None:
    invoice = make_invoice("a", "104", "2026-10", [monthly_item("10500.00")])
    result = audit(invoice)
    assert [(r.rule_id, r.version) for r in result.applied_rules] == [("price-add02", 3)]


def test_missing_documentation_prevails_over_everything_and_skips_comparisons() -> None:
    invoice = make_invoice("a", "1", "2026-10", [monthly_item("10800.00")], contract_id=None)
    duplicate = make_invoice("b", "2", "2026-10", [monthly_item("10800.00")], contract_id=None)
    result = audit(invoice, others=[duplicate])
    assert result.classification is AuditClassification.MISSING_DOCUMENTATION
    assert names(result) == {"contract_link": CheckResult.PENDING}
    result_without_contract = audit(
        make_invoice("c", "3", "2026-10", [monthly_item("10800.00")]), contract=None
    )
    assert result_without_contract.classification is AuditClassification.MISSING_DOCUMENTATION


def test_m07_unreviewed_amendment_caps_at_needs_review_without_comparing_values() -> None:
    documents = [make_document("doc-add01"), make_document("doc-add02", reviewed=False)]
    invoice = make_invoice("a", "104", "2026-10", [monthly_item("10800.00")])
    result = audit(invoice, documents=documents)
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert names(result)["amendments_reviewed"] is CheckResult.REVIEW
    assert "monthly_price" not in names(result)


def test_m17_same_documents_only_the_affected_competence_needs_review() -> None:
    documents = [make_document("doc-add01"), make_document("doc-add02", reviewed=False)]
    august = make_invoice("a", "105", "2026-08", [monthly_item("10800.00")])
    october = make_invoice("b", "104", "2026-10", [monthly_item("10800.00")])
    assert audit(august, documents=documents).classification is AuditClassification.NO_DIVERGENCE
    assert audit(october, documents=documents).classification is AuditClassification.NEEDS_REVIEW


def test_m14_inferred_competence_needs_review_even_with_a_divergence() -> None:
    invoice = make_invoice("a", "104", "2026-10", [monthly_item("10800.00")], inferred=True)
    result = audit(invoice)
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert "monthly_price" not in names(result)


def test_pending_rules_cap_a_divergence_at_needs_review_but_still_compare() -> None:
    invoice = make_invoice("a", "104", "2026-10", [monthly_item("10800.00")])
    result = audit(invoice, status=ReviewStatus.PENDING_REVIEW, documents=dev_documents())
    assert result.classification is AuditClassification.NEEDS_REVIEW
    check = next(c for c in result.checks if c.name == "monthly_price")
    assert check.direction is DivergenceDirection.POSSIBLE_DIVERGENCE
    assert result.difference == Decimal("300.00")


def test_m08_outside_contract_validity_is_needs_review_not_divergence() -> None:
    invoice = make_invoice("a", "107", "2027-01", [monthly_item("99999.00")])
    result = audit(invoice)
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert names(result)["contract_validity"] is CheckResult.REVIEW
    assert "monthly_price" not in names(result)


def test_unapplicable_amendment_blocks_value_comparison() -> None:
    bad = dev_amendments()[0].model_copy(update={"affected_rule_id": "unknown"})
    invoice = make_invoice("a", "104", "2026-10", [monthly_item("10800.00")])
    result = audit_invoice(
        invoice,
        contract=make_contract(),
        rules=base_rules(),
        amendments=[bad],
        documents=[make_document("doc-add01")],
    )
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert names(result)["rule_timeline"] is CheckResult.REVIEW
    assert "monthly_price" not in names(result)


def test_rule_without_start_is_left_out_and_blocks_value_comparison() -> None:
    rules = [make_rule("price-base", value="10000.00"), *base_rules()[1:]]
    invoice = make_invoice("a", "101", "2026-04", [monthly_item("10000.00")])
    result = audit_invoice(invoice, contract=make_contract(), rules=rules)
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert names(result)["rules_start_date"] is CheckResult.REVIEW


def test_overage_divergence_lists_all_rules_and_difference() -> None:
    invoice = make_invoice(
        "a", "103", "2026-07", [monthly_item("10800.00"), overage_item("8")], measured_hours="46"
    )
    result = audit(invoice)
    assert result.classification is AuditClassification.DIVERGENCE
    assert result.difference == Decimal("300.00")
    assert {r.rule_id for r in result.applied_rules} == {"price-add01", "franchise", "overage"}
    assert names(result)["overage_quantity"] is CheckResult.DIVERGENCE
    assert names(result)["overage_price"] is CheckResult.OK


def test_items_total_mismatch_needs_review_even_when_price_matches() -> None:
    invoice = make_invoice("a", "101", "2026-04", [monthly_item("10000.00")], total="10500.00")
    result = audit(invoice)
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert names(result)["items_total"] is CheckResult.REVIEW
    assert names(result)["monthly_price"] is CheckResult.OK


def test_adjustment_rule_is_reported_in_the_audit() -> None:
    from datetime import date

    adjustment = make_rule("adj", RuleType.ADJUSTMENT, start=date(2026, 1, 1), index="IPCA")
    contract = make_contract(end=None)
    invoice = make_invoice("a", "300", "2027-02", [monthly_item("10500.00")])
    result = audit_invoice(
        invoice,
        contract=contract,
        rules=[*base_rules(), adjustment],
        amendments=dev_amendments(),
        documents=dev_documents(),
    )
    assert names(result)["periodic_adjustment"] is CheckResult.REVIEW
    assert result.classification is AuditClassification.NEEDS_REVIEW
    assert ("adj", 1) in {(r.rule_id, r.version) for r in result.applied_rules}
