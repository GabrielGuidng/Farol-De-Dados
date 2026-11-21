"""Testes de integração I-04: registro de decisões humanas em modo de acréscimo."""

import sqlite3
from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.persistencia import (
    get_document,
    get_rule,
    init_db,
    list_reviews,
    record_audit_review,
    record_document_review,
    record_rule_review,
    save_document,
    save_rule,
)
from backend.schemas import (
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
)
from tests.fixtures.scenario import make_document, make_rule


def test_i04_rule_decisions_update_rule_and_keep_full_history() -> None:
    conn = init_db()
    rule = make_rule(
        "rule-1",
        value="10000.00",
        start=date(2026, 1, 1),
        status=ReviewStatus.PENDING_REVIEW,
    )
    save_rule(conn, rule)

    t1 = datetime(2026, 11, 20, 10, 0, 0)
    t2 = datetime(2026, 11, 20, 10, 5, 0)
    t3 = datetime(2026, 11, 20, 10, 10, 0)

    rev1 = record_rule_review(
        conn,
        "rule-1",
        user="ana.auditora",
        decision=ReviewDecision.CONFIRM,
        justification="Valor conferido na cláusula 2.",
        reviewed_at=t1,
    )
    assert rev1.previous_status is ReviewStatus.PENDING_REVIEW
    assert rev1.new_status is ReviewStatus.CONFIRMED
    assert get_rule(conn, "rule-1").review_status is ReviewStatus.CONFIRMED

    corrected = rule.model_copy(update={"value": Decimal("10500.00")})
    rev2 = record_rule_review(
        conn,
        "rule-1",
        user="bruno.supervisor",
        decision=ReviewDecision.CORRECT,
        justification="Ajustado para refletir o valor líquido da cláusula.",
        reviewed_at=t2,
        corrected_rule=corrected,
    )
    assert rev2.previous_status is ReviewStatus.CONFIRMED
    assert rev2.new_status is ReviewStatus.CORRECTED
    loaded = get_rule(conn, "rule-1")
    assert loaded is not None
    assert loaded.value == Decimal("10500.00")
    assert loaded.review_status is ReviewStatus.CORRECTED

    rev3 = record_rule_review(
        conn,
        "rule-1",
        user="carla.auditora",
        decision=ReviewDecision.REJECT,
        justification="Regra revogada por termo posterior.",
        reviewed_at=t3,
    )
    assert rev3.previous_status is ReviewStatus.CORRECTED
    assert rev3.new_status is ReviewStatus.REJECTED
    assert get_rule(conn, "rule-1").review_status is ReviewStatus.REJECTED

    history = list_reviews(conn, target_type=ReviewTargetType.RULE, target_id="rule-1")
    assert history == (rev1, rev2, rev3)
    conn.close()


def test_i04_document_and_audit_reviews_and_filtering() -> None:
    conn = init_db()
    doc = make_document("doc-add01", reviewed=False)
    save_document(conn, doc)

    t1 = datetime(2026, 11, 20, 11, 0, 0)
    t2 = datetime(2026, 11, 20, 11, 30, 0)

    doc_rev = record_document_review(
        conn,
        "doc-add01",
        user="ana.auditora",
        decision=ReviewDecision.CONFIRM,
        justification="Aditivo revisado integralmente.",
        reviewed_at=t1,
    )
    assert doc_rev.previous_status is ReviewStatus.PENDING_REVIEW
    assert doc_rev.new_status is ReviewStatus.CONFIRMED
    assert get_document(conn, "doc-add01").reviewed is True

    audit_rev = record_audit_review(
        conn,
        "audit-inv-107",
        user="ana.auditora",
        decision=ReviewDecision.CONFIRM,
        justification="Confirmada ausência de prorrogação junto ao fornecedor.",
        reviewed_at=t2,
    )
    assert audit_rev.target_type is ReviewTargetType.AUDIT_RESULT
    assert audit_rev.target_id == "audit-inv-107"

    assert list_reviews(conn) == (doc_rev, audit_rev)
    assert list_reviews(conn, target_type=ReviewTargetType.DOCUMENT) == (doc_rev,)
    assert list_reviews(conn, target_id="audit-inv-107") == (audit_rev,)
    conn.close()


def test_i04_reviews_table_is_strictly_append_only() -> None:
    conn = init_db()
    record_audit_review(
        conn,
        "audit-1",
        user="ana.auditora",
        decision=ReviewDecision.CONFIRM,
        justification="Decisão registrada.",
        reviewed_at=datetime(2026, 11, 20, 12, 0, 0),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE reviews SET justification = 'adulterada' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM reviews WHERE id = 1")
    conn.close()


def test_i04_missing_target_or_mismatched_corrected_rule_raises_value_error() -> None:
    conn = init_db()
    now = datetime(2026, 11, 20, 12, 0, 0)
    with pytest.raises(ValueError):
        record_rule_review(
            conn,
            "inexistente",
            user="ana",
            decision=ReviewDecision.CONFIRM,
            justification="ok",
            reviewed_at=now,
        )
    with pytest.raises(ValueError):
        record_document_review(
            conn,
            "inexistente",
            user="ana",
            decision=ReviewDecision.CONFIRM,
            justification="ok",
            reviewed_at=now,
        )
    rule = make_rule("rule-1", value="100.00", start=date(2026, 1, 1))
    save_rule(conn, rule)
    other = make_rule("rule-2", value="200.00", start=date(2026, 1, 1))
    with pytest.raises(ValueError):
        record_rule_review(
            conn,
            "rule-1",
            user="ana",
            decision=ReviewDecision.CORRECT,
            justification="id errado",
            reviewed_at=now,
            corrected_rule=other,
        )
    conn.close()
