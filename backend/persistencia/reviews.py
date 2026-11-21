"""Registro de decisões humanas em modo de acréscimo (trilha de auditoria)."""

import sqlite3
from datetime import datetime

from backend.persistencia.repositories import get_document, get_rule, save_rule
from backend.schemas import (
    ContractRule,
    HumanReview,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
)

_DECISION_TO_STATUS: dict[ReviewDecision, ReviewStatus] = {
    ReviewDecision.CONFIRM: ReviewStatus.CONFIRMED,
    ReviewDecision.CORRECT: ReviewStatus.CORRECTED,
    ReviewDecision.REJECT: ReviewStatus.REJECTED,
}


def append_review(conn: sqlite3.Connection, review: HumanReview) -> int:
    """Acrescenta uma decisão na tabela `reviews` e sincroniza o estado do alvo, se houver."""
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO reviews (
                target_type, target_id, user, decision, justification,
                previous_status, new_status, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.target_type.value,
                review.target_id,
                review.user,
                review.decision.value,
                review.justification,
                review.previous_status.value,
                review.new_status.value,
                review.reviewed_at.isoformat(),
            ),
        )
        if review.target_type is ReviewTargetType.RULE:
            conn.execute(
                "UPDATE rules SET review_status = ? WHERE id = ?",
                (review.new_status.value, review.target_id),
            )
        elif review.target_type is ReviewTargetType.DOCUMENT:
            conn.execute(
                "UPDATE documents SET reviewed = ? WHERE id = ?",
                (int(review.new_status.is_trusted), review.target_id),
            )
    if cursor.lastrowid is None:
        raise RuntimeError("falha ao obter o identificador da revisão inserida")
    return cursor.lastrowid


def record_rule_review(
    conn: sqlite3.Connection,
    rule_id: str,
    *,
    user: str,
    decision: ReviewDecision,
    justification: str,
    reviewed_at: datetime,
    corrected_rule: ContractRule | None = None,
) -> HumanReview:
    """Registra decisão humana sobre uma regra (confirmar, corrigir ou rejeitar)."""
    existing = get_rule(conn, rule_id)
    if existing is None:
        raise ValueError(f"regra {rule_id} não encontrada")
    new_status = _DECISION_TO_STATUS[decision]
    if corrected_rule is not None:
        if corrected_rule.id != rule_id:
            raise ValueError(
                f"regra corrigida ({corrected_rule.id}) não corresponde ao alvo ({rule_id})"
            )
        save_rule(conn, corrected_rule.model_copy(update={"review_status": new_status}))
    review = HumanReview(
        target_type=ReviewTargetType.RULE,
        target_id=rule_id,
        user=user,
        decision=decision,
        justification=justification,
        previous_status=existing.review_status,
        new_status=new_status,
        reviewed_at=reviewed_at,
    )
    append_review(conn, review)
    return review


def record_document_review(
    conn: sqlite3.Connection,
    document_id: str,
    *,
    user: str,
    decision: ReviewDecision,
    justification: str,
    reviewed_at: datetime,
) -> HumanReview:
    """Registra decisão humana sobre a revisão de um documento (ex.: aditivo)."""
    document = get_document(conn, document_id)
    if document is None:
        raise ValueError(f"documento {document_id} não encontrado")
    previous_status = ReviewStatus.CONFIRMED if document.reviewed else ReviewStatus.PENDING_REVIEW
    review = HumanReview(
        target_type=ReviewTargetType.DOCUMENT,
        target_id=document_id,
        user=user,
        decision=decision,
        justification=justification,
        previous_status=previous_status,
        new_status=_DECISION_TO_STATUS[decision],
        reviewed_at=reviewed_at,
    )
    append_review(conn, review)
    return review


def record_audit_review(
    conn: sqlite3.Connection,
    audit_id: str,
    *,
    user: str,
    decision: ReviewDecision,
    justification: str,
    reviewed_at: datetime,
    previous_status: ReviewStatus = ReviewStatus.PENDING_REVIEW,
) -> HumanReview:
    """Registra decisão humana sobre um resultado de auditoria."""
    review = HumanReview(
        target_type=ReviewTargetType.AUDIT_RESULT,
        target_id=audit_id,
        user=user,
        decision=decision,
        justification=justification,
        previous_status=previous_status,
        new_status=_DECISION_TO_STATUS[decision],
        reviewed_at=reviewed_at,
    )
    append_review(conn, review)
    return review


def list_reviews(
    conn: sqlite3.Connection,
    *,
    target_type: ReviewTargetType | None = None,
    target_id: str | None = None,
) -> tuple[HumanReview, ...]:
    """Lista revisões em ordem cronológica de inserção, com filtros opcionais por alvo."""
    clauses: list[str] = []
    params: list[str] = []
    if target_type is not None:
        clauses.append("target_type = ?")
        params.append(target_type.value)
    if target_id is not None:
        clauses.append("target_id = ?")
        params.append(target_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM reviews{where} ORDER BY id ASC",
        tuple(params),
    ).fetchall()
    return tuple(
        HumanReview.model_validate(
            {
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "user": row["user"],
                "decision": row["decision"],
                "justification": row["justification"],
                "previous_status": row["previous_status"],
                "new_status": row["new_status"],
                "reviewed_at": row["reviewed_at"],
            }
        )
        for row in rows
    )
