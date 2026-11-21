"""Testes de integração I-06: auditoria preserva regras e cálculo da época após correção."""

from datetime import date, datetime
from decimal import Decimal

from backend.motor import audit_invoice
from backend.persistencia import (
    get_audit,
    get_rule,
    init_db,
    list_audits,
    record_rule_review,
    save_audit,
    save_rule,
)
from backend.schemas import AuditClassification, ReviewDecision, ReviewStatus
from tests.fixtures.scenario import (
    base_rules,
    dev_amendments,
    dev_documents,
    make_contract,
    make_invoice,
    make_rule,
    monthly_item,
)


def test_i06_correcting_rule_later_preserves_old_audit_snapshot_and_memory() -> None:
    conn = init_db()
    price_rule = make_rule(
        "price-add02",
        value="10500.00",
        start=date(2026, 9, 1),
        status=ReviewStatus.CONFIRMED,
        document="aditivo-02",
        page=2,
        version=3,
        excerpt="Passa a R$ 10.500,00 a partir de setembro de 2026.",
    )
    save_rule(conn, price_rule)

    invoice = make_invoice("inv-104", "104", "2026-10", [monthly_item("10800.00")])
    result = audit_invoice(
        invoice,
        contract=make_contract(),
        rules=[*base_rules()[1:], price_rule],
        documents=dev_documents(),
    )
    assert result.classification is AuditClassification.DIVERGENCE
    assert result.base == Decimal("10500.00")
    assert result.difference == Decimal("300.00")

    stored = save_audit(conn, result)
    assert stored.rules_snapshot == (price_rule,)

    # Mais tarde, uma pessoa revisora corrige o valor da regra no repositório para R$ 10.800,00.
    corrected_rule = price_rule.model_copy(update={"value": Decimal("10800.00")})
    record_rule_review(
        conn,
        "price-add02",
        user="bruno.supervisor",
        decision=ReviewDecision.CORRECT,
        justification="Erro material identificado na página 2 do aditivo.",
        reviewed_at=datetime(2026, 11, 21, 9, 0, 0),
        corrected_rule=corrected_rule,
    )

    current_rule = get_rule(conn, "price-add02")
    assert current_rule is not None
    assert current_rule.value == Decimal("10800.00")
    assert current_rule.review_status is ReviewStatus.CORRECTED

    # A auditoria antiga deve manter exatamente a regra (R$ 10.500,00) e a memória da época.
    reloaded = get_audit(conn, result.id)
    assert reloaded is not None
    assert reloaded.result == result
    assert reloaded.result.base == Decimal("10500.00")
    assert reloaded.result.difference == Decimal("300.00")
    assert reloaded.rules_snapshot == (price_rule,)
    assert reloaded.rules_snapshot[0].value == Decimal("10500.00")
    assert reloaded.rules_snapshot[0].review_status is ReviewStatus.CONFIRMED

    price_check = next(c for c in reloaded.result.checks if c.name == "monthly_price")
    assert "diferença = cobrado − regra = R$ 300,00 (2,86%)" in price_check.calculation_memory
    conn.close()


def test_i06_save_audit_explicit_snapshot_and_list_filters() -> None:
    conn = init_db()
    rules = base_rules()
    amendments = dev_amendments()
    documents = dev_documents()
    inv_a = make_invoice("inv-101", "101", "2026-04", [monthly_item("10000.00")])
    inv_b = make_invoice("inv-104", "104", "2026-10", [monthly_item("10800.00")])

    res_a = audit_invoice(
        inv_a, contract=make_contract(), rules=rules, amendments=amendments, documents=documents
    )
    res_b = audit_invoice(
        inv_b, contract=make_contract(), rules=rules, amendments=amendments, documents=documents
    )

    save_audit(conn, res_a, rules_snapshot=[rules[0]])
    save_audit(conn, res_b, rules_snapshot=[amendments[1].new_rule])

    assert len(list_audits(conn)) == 2
    by_invoice = list_audits(conn, invoice_id="inv-104")
    assert len(by_invoice) == 1
    assert by_invoice[0].result == res_b
    assert get_audit(conn, "inexistente") is None
    conn.close()
