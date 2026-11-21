"""Teste de integração I-02: Casos A e B e as 8 cobranças do cenário de desenvolvimento.

Gabarito escrito à mão a partir da tabela de testes.md (seção 4), independente do código.
Só os Casos A e B (cobranças 104 e 101) vêm do Documento 01; as demais são cenário de
desenvolvimento. A cobrança 107 é NEEDS_REVIEW (ver decisoes.md).
"""

from decimal import Decimal

from backend.motor import audit_invoices
from backend.schemas import AuditClassification, AuditResult
from tests.fixtures.scenario import (
    base_rules,
    dev_amendments,
    dev_documents,
    dev_invoices,
    make_contract,
)

EXPECTED = {
    "101": AuditClassification.NO_DIVERGENCE,  # Caso B (abr/2026)
    "102": AuditClassification.NO_DIVERGENCE,
    "103": AuditClassification.DIVERGENCE,  # R$ 300,00 a mais
    "104": AuditClassification.DIVERGENCE,  # Caso A (out/2026): R$ 300,00 (2,86%)
    "105": AuditClassification.NEEDS_REVIEW,  # duplicidade
    "106": AuditClassification.NEEDS_REVIEW,  # duplicidade
    "107": AuditClassification.NEEDS_REVIEW,  # fora da vigência, sem prorrogação
    "108": AuditClassification.NEEDS_REVIEW,  # excedente sem medição
}


def run() -> dict[str, AuditResult]:
    results = audit_invoices(
        dev_invoices(),
        contract=make_contract(),
        rules=base_rules(),
        amendments=dev_amendments(),
        documents=dev_documents(),
    )
    return {result.invoice_id.removeprefix("inv-"): result for result in results}


def alerts(result: AuditResult) -> dict[str, str]:
    return {check.name: check.result.value for check in result.checks}


def test_i02_the_eight_classifications_match_the_table() -> None:
    results = run()
    assert {number: result.classification for number, result in results.items()} == EXPECTED


def test_i02_case_b_april_uses_the_base_rule() -> None:
    result = run()["101"]
    assert [(rule.rule_id, rule.version) for rule in result.applied_rules] == [("price-base", 1)]
    assert result.difference == Decimal("0.00")


def test_i02_case_a_october_points_300_and_2_86_percent_with_the_amendment_02() -> None:
    result = run()["104"]
    assert result.base == Decimal("10500.00")
    assert result.difference == Decimal("300.00")
    price = next(check for check in result.checks if check.name == "monthly_price")
    assert price.rule_id == "price-add02"
    assert "diferença = cobrado − regra = R$ 300,00 (2,86%)" in price.calculation_memory
    assert "aditivo-02" in price.message


def test_i02_invoice_103_points_300_more_for_8_billed_hours_with_6_permitted() -> None:
    result = run()["103"]
    assert result.difference == Decimal("300.00")
    quantity = next(check for check in result.checks if check.name == "overage_quantity")
    assert "horas permitidas = máx(0, 46 − 40) = 6" in quantity.calculation_memory
    assert "valor a mais = 2 × R$ 150,00 = R$ 300,00" in quantity.calculation_memory


def test_i02_invoices_105_and_106_are_flagged_as_possible_duplicates_of_each_other() -> None:
    results = run()
    for number, other in (("105", "106"), ("106", "105")):
        duplicate = next(c for c in results[number].checks if c.name == "duplicate_invoice")
        assert other in duplicate.message
        assert "Pode ser legítimo" in duplicate.message
    for number in ("101", "102", "103", "104", "107", "108"):
        assert "duplicate_invoice" not in alerts(results[number])


def test_i02_invoice_107_is_review_because_the_extension_may_not_have_been_sent() -> None:
    result = run()["107"]
    validity = next(check for check in result.checks if check.name == "contract_validity")
    assert "prorrogação" in validity.message
    assert "monthly_price" not in alerts(result)


def test_i02_invoice_108_is_review_because_there_is_no_measurement() -> None:
    result = run()["108"]
    quantity = next(check for check in result.checks if check.name == "overage_quantity")
    assert quantity.result.value == "REVIEW"
    assert "sem medição" in quantity.message
