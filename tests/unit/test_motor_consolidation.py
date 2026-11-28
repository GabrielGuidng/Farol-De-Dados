"""Testes da consolidação e dos dados faltantes: M-07, M-14, M-15 e M-17."""

from datetime import date

from backend.motor import (
    check_amendment_review,
    check_competence_inferred,
    check_contract_link,
    consolidate_rules,
)
from backend.schemas import CheckResult, DivergenceDirection, ReferencePeriod
from tests.fixtures.scenario import (
    CNPJ,
    dev_amendments,
    dev_documents,
    make_contract,
    make_document,
    make_invoice,
    make_rule,
    monthly_item,
)


def period(text: str) -> ReferencePeriod:
    return ReferencePeriod.model_validate(text)


def test_m07_unreviewed_amendment_makes_consolidation_incomplete() -> None:
    documents = [make_document("doc-add01"), make_document("doc-add02", reviewed=False)]
    check = check_amendment_review(dev_amendments(), documents, period("2026-10"))
    assert check.result is CheckResult.REVIEW
    assert check.direction is DivergenceDirection.UNVERIFIABLE
    assert "Consolidação incompleta" in check.message
    assert "doc-add02" in check.message


def test_m07_amendment_whose_document_was_not_received_counts_as_unreviewed() -> None:
    check = check_amendment_review(
        dev_amendments(), [make_document("doc-add01")], period("2026-10")
    )
    assert check.result is CheckResult.REVIEW
    assert "não encontrado" in check.message


def test_m17_only_the_unreviewed_amendment_in_force_triggers_review() -> None:
    documents = [make_document("doc-add01"), make_document("doc-add02", reviewed=False)]
    amendments = dev_amendments()
    # Aditivo 02 passa a valer em set/2026: não afeta ago/2026, mas afeta out/2026.
    assert check_amendment_review(amendments, documents, period("2026-08")).result is CheckResult.OK
    assert (
        check_amendment_review(amendments, documents, period("2026-09")).result
        is CheckResult.REVIEW
    )
    assert (
        check_amendment_review(amendments, documents, period("2026-10")).result
        is CheckResult.REVIEW
    )


def test_m17_unreviewed_first_amendment_affects_every_later_period() -> None:
    documents = [make_document("doc-add01", reviewed=False), make_document("doc-add02")]
    amendments = dev_amendments()
    assert check_amendment_review(amendments, documents, period("2026-04")).result is CheckResult.OK
    assert (
        check_amendment_review(amendments, documents, period("2026-05")).result
        is CheckResult.REVIEW
    )
    assert (
        check_amendment_review(amendments, documents, period("2026-10")).result
        is CheckResult.REVIEW
    )


def test_m07_all_reviewed_or_no_amendments_is_ok() -> None:
    assert (
        check_amendment_review(dev_amendments(), dev_documents(), period("2026-12")).result
        is CheckResult.OK
    )
    assert check_amendment_review([], [], period("2026-12")).result is CheckResult.OK


def test_rule_without_start_is_alerted_and_left_out_of_the_consolidation() -> None:
    with_start = make_rule("a", value="1", start=date(2026, 1, 1))
    without_start = make_rule("b", value="2")
    result = consolidate_rules([with_start, without_start])
    assert [rule.id for rule in result.rules] == ["a"]
    assert result.check.result is CheckResult.REVIEW
    assert "b" in result.check.message
    assert "fora da consolidação" in result.check.message


def test_rules_with_start_pass_the_consolidation_untouched() -> None:
    rules = [make_rule("a", value="1", start=date(2026, 1, 1))]
    result = consolidate_rules(rules)
    assert result.rules == tuple(rules)
    assert result.check.result is CheckResult.OK


def test_m14_inferred_competence_needs_review() -> None:
    inferred = make_invoice("i", "1", "2026-10", [monthly_item("100")], inferred=True)
    informed = make_invoice("j", "2", "2026-10", [monthly_item("100")])
    check = check_competence_inferred(inferred)
    assert check.result is CheckResult.REVIEW
    assert "inferida" in check.message
    assert check_competence_inferred(informed).result is CheckResult.OK


def test_m15_invoice_without_contract_is_missing_documentation() -> None:
    invoice = make_invoice("i", "1", "2026-10", [monthly_item("100")], contract_id=None)
    check = check_contract_link(make_contract(), invoice)
    assert check.result is CheckResult.PENDING
    assert check.result.classification.value == "MISSING_DOCUMENTATION"
    assert check_contract_link(
        None, make_invoice("j", "2", "2026-10", [monthly_item("100")])
    ).result is (CheckResult.PENDING)


def test_m15_other_contract_or_other_cnpj_is_not_a_safe_link() -> None:
    other_contract = make_invoice(
        "i", "1", "2026-10", [monthly_item("100")], contract_id="CT-OUTRO"
    )
    other_cnpj = make_invoice("j", "2", "2026-10", [monthly_item("100")], cnpj="11.444.777/0001-61")
    assert check_contract_link(make_contract(), other_contract).result is CheckResult.PENDING
    assert check_contract_link(make_contract(), other_cnpj).result is CheckResult.PENDING


def test_m15_invoice_without_total_and_items_is_missing_documentation() -> None:
    empty = make_invoice("i", "1", "2026-10", [], total="0")
    assert check_contract_link(make_contract(), empty).result is CheckResult.PENDING


def test_m15_linked_invoice_is_ok() -> None:
    invoice = make_invoice("i", "1", "2026-10", [monthly_item("100")])
    assert (
        invoice.supplier_cnpj
        == make_contract().supplier_cnpj
        == CNPJ.replace(".", "").replace("/", "").replace("-", "")
    )
    assert check_contract_link(make_contract(), invoice).result is CheckResult.OK


def test_provisional_contract_separates_invalid_branch_and_third_party() -> None:
    provisional_contract = make_contract().model_copy(update={"supplier_cnpj": "00000000000191"})
    valid_inv = make_invoice("ok-1", "8801", "2026-04", [monthly_item("10000.00")])
    invalid_dv_inv = make_invoice(
        "bad-dv", "8815", "2026-04", [monthly_item("10000.00")]
    ).model_copy(update={"raw_supplier_cnpj": "11.222.333/0001-99"})

    assert check_contract_link(provisional_contract, valid_inv).result is CheckResult.OK
    dv_chk = check_contract_link(provisional_contract, invalid_dv_inv)
    assert dv_chk.result is CheckResult.PENDING
    assert "dígito verificador inválido" in dv_chk.message.casefold()

    real_contract = make_contract()  # 11.222.333/0001-81
    branch_inv = make_invoice(
        "branch-1",
        "8830",
        "2026-04",
        [monthly_item("10000.00")],
        cnpj="11.222.333/0002-62",
    )
    branch_chk = check_contract_link(real_contract, branch_inv)
    assert branch_chk.result is CheckResult.PENDING
    assert "filial" in branch_chk.message.casefold()

    other_inv = make_invoice(
        "other-1",
        "8890",
        "2026-04",
        [monthly_item("10000.00")],
        cnpj="33.000.167/0001-01",
    )
    other_chk = check_contract_link(real_contract, other_inv)
    assert other_chk.result is CheckResult.PENDING
    assert "outro fornecedor" in other_chk.message.casefold()
