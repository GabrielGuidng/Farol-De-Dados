"""Persistência de auditorias com snapshot das regras e memória de cálculo da época."""

import json
import sqlite3
from collections.abc import Sequence

from backend.persistencia.repositories import get_rule
from backend.schemas import AuditResult, ContractRule, StrictModel


class StoredAudit(StrictModel):
    """Resultado de auditoria acompanhado do snapshot das regras vigentes na época."""

    result: AuditResult
    rules_snapshot: tuple[ContractRule, ...] = ()


def save_audit(
    conn: sqlite3.Connection,
    result: AuditResult,
    rules_snapshot: Sequence[ContractRule] | None = None,
) -> StoredAudit:
    """Grava a auditoria e congela cópia das regras aplicadas e da memória de cálculo."""
    if rules_snapshot is None:
        collected: list[ContractRule] = []
        seen: set[str] = set()
        for applied in result.applied_rules:
            if applied.rule_id in seen:
                continue
            seen.add(applied.rule_id)
            rule = get_rule(conn, applied.rule_id)
            if rule is not None:
                collected.append(rule)
        snapshot = tuple(collected)
    else:
        snapshot = tuple(rules_snapshot)

    checks_json = json.dumps([check.model_dump(mode="json") for check in result.checks])
    applied_rules_json = json.dumps(
        [applied.model_dump(mode="json") for applied in result.applied_rules]
    )
    rules_snapshot_json = json.dumps(
        [rule.model_dump(mode="json", exclude={"evidence": {"without_page"}}) for rule in snapshot]
    )
    with conn:
        conn.execute(
            """
            INSERT INTO audits (
                id, invoice_id, classification, reference_period, base, difference, total,
                checks_json, applied_rules_json, rules_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                invoice_id = excluded.invoice_id,
                classification = excluded.classification,
                reference_period = excluded.reference_period,
                base = excluded.base,
                difference = excluded.difference,
                total = excluded.total,
                checks_json = excluded.checks_json,
                applied_rules_json = excluded.applied_rules_json,
                rules_snapshot_json = excluded.rules_snapshot_json
            """,
            (
                result.id,
                result.invoice_id,
                result.classification.value,
                str(result.reference_period),
                None if result.base is None else str(result.base),
                None if result.difference is None else str(result.difference),
                str(result.total),
                checks_json,
                applied_rules_json,
                rules_snapshot_json,
            ),
        )
    return StoredAudit(result=result, rules_snapshot=snapshot)


def _row_to_stored_audit(row: sqlite3.Row) -> StoredAudit:
    result = AuditResult.model_validate(
        {
            "id": row["id"],
            "invoice_id": row["invoice_id"],
            "classification": row["classification"],
            "checks": json.loads(row["checks_json"]),
            "applied_rules": json.loads(row["applied_rules_json"]),
            "reference_period": row["reference_period"],
            "base": row["base"],
            "difference": row["difference"],
            "total": row["total"],
        }
    )
    snapshot = tuple(
        ContractRule.model_validate(item) for item in json.loads(row["rules_snapshot_json"])
    )
    return StoredAudit(result=result, rules_snapshot=snapshot)


def get_audit(conn: sqlite3.Connection, audit_id: str) -> StoredAudit | None:
    """Carrega uma auditoria gravada com o snapshot de regras da época."""
    row = conn.execute("SELECT * FROM audits WHERE id = ?", (audit_id,)).fetchone()
    return None if row is None else _row_to_stored_audit(row)


def list_audits(conn: sqlite3.Connection, invoice_id: str | None = None) -> tuple[StoredAudit, ...]:
    """Lista auditorias gravadas, opcionalmente filtradas por cobrança."""
    if invoice_id is None:
        rows = conn.execute("SELECT * FROM audits ORDER BY id ASC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audits WHERE invoice_id = ? ORDER BY id ASC",
            (invoice_id,),
        ).fetchall()
    return tuple(_row_to_stored_audit(row) for row in rows)
