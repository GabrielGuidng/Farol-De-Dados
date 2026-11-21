"""Repositórios de contrato, documento, regra, aditivo e cobrança no SQLite."""

import sqlite3

from backend.schemas import (
    Amendment,
    BillingItem,
    Contract,
    ContractRule,
    Document,
    Evidence,
    Invoice,
    ValidityPeriod,
)


def _rule_to_json(rule: ContractRule) -> str:
    return rule.model_dump_json(exclude={"evidence": {"without_page"}})


def _row_to_contract(row: sqlite3.Row) -> Contract:
    return Contract.model_validate(
        {
            "id": row["id"],
            "company": row["company"],
            "supplier": row["supplier"],
            "supplier_cnpj": row["supplier_cnpj"],
            "subject": row["subject"],
            "validity": ValidityPeriod.model_validate(
                {"start": row["validity_start"], "end": row["validity_end"]}
            ),
            "comparison_tolerance": row["comparison_tolerance"],
            "status": row["status"],
        }
    )


def save_contract(conn: sqlite3.Connection, contract: Contract) -> None:
    """Salva ou atualiza um contrato preservando Decimal e datas."""
    with conn:
        conn.execute(
            """
            INSERT INTO contracts (
                id, company, supplier, supplier_cnpj, subject,
                validity_start, validity_end, comparison_tolerance, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                company = excluded.company,
                supplier = excluded.supplier,
                supplier_cnpj = excluded.supplier_cnpj,
                subject = excluded.subject,
                validity_start = excluded.validity_start,
                validity_end = excluded.validity_end,
                comparison_tolerance = excluded.comparison_tolerance,
                status = excluded.status
            """,
            (
                contract.id,
                contract.company,
                contract.supplier,
                contract.supplier_cnpj,
                contract.subject,
                None if contract.validity.start is None else contract.validity.start.isoformat(),
                None if contract.validity.end is None else contract.validity.end.isoformat(),
                str(contract.comparison_tolerance),
                contract.status.value,
            ),
        )


def get_contract(conn: sqlite3.Connection, contract_id: str) -> Contract | None:
    """Carrega um contrato por id ou devolve None se não existir."""
    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    return None if row is None else _row_to_contract(row)


def list_contracts(conn: sqlite3.Connection) -> tuple[Contract, ...]:
    """Lista todos os contratos cadastrados ordenados por id."""
    rows = conn.execute("SELECT * FROM contracts ORDER BY id ASC").fetchall()
    return tuple(_row_to_contract(row) for row in rows)


def _row_to_document(row: sqlite3.Row) -> Document:
    return Document.model_validate(
        {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "document_type": row["document_type"],
            "file_name": row["file_name"],
            "received_at": row["received_at"],
            "file_hash": row["file_hash"],
            "read_quality": row["read_quality"],
            "reviewed": bool(row["reviewed"]),
            "extracted_text": row["extracted_text"],
        }
    )


def save_document(conn: sqlite3.Connection, document: Document) -> None:
    """Salva ou atualiza um documento recebido."""
    with conn:
        conn.execute(
            """
            INSERT INTO documents (
                id, contract_id, document_type, file_name, received_at,
                file_hash, read_quality, reviewed, extracted_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                contract_id = excluded.contract_id,
                document_type = excluded.document_type,
                file_name = excluded.file_name,
                received_at = excluded.received_at,
                file_hash = excluded.file_hash,
                read_quality = excluded.read_quality,
                reviewed = excluded.reviewed,
                extracted_text = excluded.extracted_text
            """,
            (
                document.id,
                document.contract_id,
                document.document_type.value,
                document.file_name,
                document.received_at.isoformat(),
                document.file_hash,
                document.read_quality.value,
                int(document.reviewed),
                document.extracted_text,
            ),
        )


def get_document(conn: sqlite3.Connection, document_id: str) -> Document | None:
    """Carrega um documento por id ou devolve None se não existir."""
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return None if row is None else _row_to_document(row)


def list_documents(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> tuple[Document, ...]:
    """Lista documentos, opcionalmente filtrados por contrato."""
    if contract_id is None:
        rows = conn.execute("SELECT * FROM documents ORDER BY id ASC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM documents WHERE contract_id = ? ORDER BY id ASC",
            (contract_id,),
        ).fetchall()
    return tuple(_row_to_document(row) for row in rows)


def _row_to_rule(row: sqlite3.Row) -> ContractRule:
    evidence = Evidence.model_validate(
        {
            "source_document_id": row["source_document_id"],
            "page": row["page"],
            "clause": row["clause"],
            "paragraph": row["paragraph"],
            "literal_excerpt": row["literal_excerpt"],
        }
    )
    return ContractRule.model_validate(
        {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "rule_type": row["rule_type"],
            "value": row["value"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "adjustment_index": row["adjustment_index"],
            "periodicity_months": row["periodicity_months"],
            "validity": {"start": row["validity_start"], "end": row["validity_end"]},
            "review_status": row["review_status"],
            "verification_score": row["verification_score"],
            "evidence": evidence,
            "relation": row["relation"],
            "version": row["version"],
        }
    )


def _upsert_rule(conn: sqlite3.Connection, rule: ContractRule) -> None:
    conn.execute(
        """
        INSERT INTO rules (
            id, contract_id, rule_type, value, quantity, unit,
            adjustment_index, periodicity_months, validity_start, validity_end,
            review_status, verification_score, relation, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            contract_id = excluded.contract_id,
            rule_type = excluded.rule_type,
            value = excluded.value,
            quantity = excluded.quantity,
            unit = excluded.unit,
            adjustment_index = excluded.adjustment_index,
            periodicity_months = excluded.periodicity_months,
            validity_start = excluded.validity_start,
            validity_end = excluded.validity_end,
            review_status = excluded.review_status,
            verification_score = excluded.verification_score,
            relation = excluded.relation,
            version = excluded.version
        """,
        (
            rule.id,
            rule.contract_id,
            rule.rule_type.value,
            None if rule.value is None else str(rule.value),
            None if rule.quantity is None else str(rule.quantity),
            rule.unit,
            rule.adjustment_index,
            rule.periodicity_months,
            None if rule.validity.start is None else rule.validity.start.isoformat(),
            None if rule.validity.end is None else rule.validity.end.isoformat(),
            rule.review_status.value,
            None if rule.verification_score is None else str(rule.verification_score),
            rule.relation.value,
            rule.version,
        ),
    )
    conn.execute(
        """
        INSERT INTO evidences (
            rule_id, source_document_id, page, clause, paragraph, literal_excerpt, without_page
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rule_id) DO UPDATE SET
            source_document_id = excluded.source_document_id,
            page = excluded.page,
            clause = excluded.clause,
            paragraph = excluded.paragraph,
            literal_excerpt = excluded.literal_excerpt,
            without_page = excluded.without_page
        """,
        (
            rule.id,
            rule.evidence.source_document_id,
            rule.evidence.page,
            rule.evidence.clause,
            rule.evidence.paragraph,
            rule.evidence.literal_excerpt,
            int(rule.evidence.without_page),
        ),
    )


def save_rule(conn: sqlite3.Connection, rule: ContractRule) -> None:
    """Salva ou atualiza uma regra contratual e sua evidência documental."""
    with conn:
        _upsert_rule(conn, rule)


def get_rule(conn: sqlite3.Connection, rule_id: str) -> ContractRule | None:
    """Carrega uma regra com sua evidência ou devolve None se não existir."""
    row = conn.execute(
        """
        SELECT r.*, e.source_document_id, e.page, e.clause, e.paragraph, e.literal_excerpt
        FROM rules r
        JOIN evidences e ON e.rule_id = r.id
        WHERE r.id = ?
        """,
        (rule_id,),
    ).fetchone()
    return None if row is None else _row_to_rule(row)


def list_rules(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> tuple[ContractRule, ...]:
    """Lista regras com evidência, opcionalmente filtradas por contrato."""
    base_query = """
        SELECT r.*, e.source_document_id, e.page, e.clause, e.paragraph, e.literal_excerpt
        FROM rules r
        JOIN evidences e ON e.rule_id = r.id
    """
    if contract_id is None:
        rows = conn.execute(base_query + " ORDER BY r.id ASC").fetchall()
    else:
        rows = conn.execute(
            base_query + " WHERE r.contract_id = ? ORDER BY r.id ASC",
            (contract_id,),
        ).fetchall()
    return tuple(_row_to_rule(row) for row in rows)


def _row_to_amendment(conn: sqlite3.Connection, row: sqlite3.Row) -> Amendment:
    new_rule: ContractRule | None = None
    if row["new_rule_id"] is not None:
        new_rule = get_rule(conn, row["new_rule_id"])
    if new_rule is None and row["new_rule_json"] is not None:
        new_rule = ContractRule.model_validate_json(row["new_rule_json"])
    return Amendment.model_validate(
        {
            "id": row["id"],
            "affected_rule_id": row["affected_rule_id"],
            "new_rule": new_rule,
            "source_document_id": row["source_document_id"],
            "effective_date": row["effective_date"],
            "relation": row["relation"],
        }
    )


def save_amendment(conn: sqlite3.Connection, amendment: Amendment) -> None:
    """Salva ou atualiza um aditivo (e a nova regra associada, quando houver)."""
    with conn:
        if amendment.new_rule is not None:
            _upsert_rule(conn, amendment.new_rule)
        conn.execute(
            """
            INSERT INTO amendments (
                id, affected_rule_id, new_rule_id, new_rule_json,
                source_document_id, effective_date, relation
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                affected_rule_id = excluded.affected_rule_id,
                new_rule_id = excluded.new_rule_id,
                new_rule_json = excluded.new_rule_json,
                source_document_id = excluded.source_document_id,
                effective_date = excluded.effective_date,
                relation = excluded.relation
            """,
            (
                amendment.id,
                amendment.affected_rule_id,
                None if amendment.new_rule is None else amendment.new_rule.id,
                None if amendment.new_rule is None else _rule_to_json(amendment.new_rule),
                amendment.source_document_id,
                amendment.effective_date.isoformat(),
                amendment.relation.value,
            ),
        )


def get_amendment(conn: sqlite3.Connection, amendment_id: str) -> Amendment | None:
    """Carrega um aditivo por id ou devolve None se não existir."""
    row = conn.execute("SELECT * FROM amendments WHERE id = ?", (amendment_id,)).fetchone()
    return None if row is None else _row_to_amendment(conn, row)


def list_amendments(
    conn: sqlite3.Connection, source_document_id: str | None = None
) -> tuple[Amendment, ...]:
    """Lista aditivos, opcionalmente filtrados pelo documento de origem."""
    if source_document_id is None:
        rows = conn.execute(
            "SELECT * FROM amendments ORDER BY effective_date ASC, id ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM amendments
            WHERE source_document_id = ?
            ORDER BY effective_date ASC, id ASC
            """,
            (source_document_id,),
        ).fetchall()
    return tuple(_row_to_amendment(conn, row) for row in rows)


def _row_to_invoice(conn: sqlite3.Connection, row: sqlite3.Row) -> Invoice:
    item_rows = conn.execute(
        """
        SELECT description, quantity, unit, unit_value, total_value
        FROM billing_items
        WHERE invoice_id = ?
        ORDER BY position ASC
        """,
        (row["id"],),
    ).fetchall()
    items = tuple(
        BillingItem.model_validate(
            {
                "description": item["description"],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "unit_value": item["unit_value"],
                "total_value": item["total_value"],
            }
        )
        for item in item_rows
    )
    return Invoice.model_validate(
        {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "supplier": row["supplier"],
            "supplier_cnpj": row["supplier_cnpj"],
            "number": row["number"],
            "issue_date": row["issue_date"],
            "reference_period": row["reference_period"],
            "competence_inferred": bool(row["competence_inferred"]),
            "total": row["total"],
            "measured_hours": row["measured_hours"],
            "source": row["source"],
            "items": items,
        }
    )


def save_invoice(conn: sqlite3.Connection, invoice: Invoice) -> None:
    """Salva ou atualiza uma cobrança e seus itens preservando a precisão unitária."""
    with conn:
        conn.execute(
            """
            INSERT INTO invoices (
                id, contract_id, supplier, supplier_cnpj, number, issue_date,
                reference_period, competence_inferred, total, measured_hours, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                contract_id = excluded.contract_id,
                supplier = excluded.supplier,
                supplier_cnpj = excluded.supplier_cnpj,
                number = excluded.number,
                issue_date = excluded.issue_date,
                reference_period = excluded.reference_period,
                competence_inferred = excluded.competence_inferred,
                total = excluded.total,
                measured_hours = excluded.measured_hours,
                source = excluded.source
            """,
            (
                invoice.id,
                invoice.contract_id,
                invoice.supplier,
                invoice.supplier_cnpj,
                invoice.number,
                invoice.issue_date.isoformat(),
                str(invoice.reference_period),
                int(invoice.competence_inferred),
                str(invoice.total),
                None if invoice.measured_hours is None else str(invoice.measured_hours),
                invoice.source.value,
            ),
        )
        conn.execute("DELETE FROM billing_items WHERE invoice_id = ?", (invoice.id,))
        for position, item in enumerate(invoice.items):
            conn.execute(
                """
                INSERT INTO billing_items (
                    invoice_id, position, description, quantity, unit, unit_value, total_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice.id,
                    position,
                    item.description,
                    str(item.quantity),
                    item.unit,
                    str(item.unit_value),
                    str(item.total_value),
                ),
            )


def get_invoice(conn: sqlite3.Connection, invoice_id: str) -> Invoice | None:
    """Carrega uma cobrança e seus itens ou devolve None se não existir."""
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    return None if row is None else _row_to_invoice(conn, row)


def list_invoices(conn: sqlite3.Connection, contract_id: str | None = None) -> tuple[Invoice, ...]:
    """Lista cobranças com seus itens, opcionalmente filtradas por contrato."""
    if contract_id is None:
        rows = conn.execute("SELECT * FROM invoices ORDER BY id ASC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE contract_id = ? ORDER BY id ASC",
            (contract_id,),
        ).fetchall()
    return tuple(_row_to_invoice(conn, row) for row in rows)
