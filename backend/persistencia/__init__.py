"""Camada de persistência SQLite: esquema, repositórios, decisões, snapshots e hash."""

from backend.persistencia.audits import StoredAudit, get_audit, list_audits, save_audit
from backend.persistencia.database import SCHEMA_SQL, TABLE_NAMES, connect, init_db
from backend.persistencia.hashing import (
    compute_sha256,
    receive_document,
    verify_document_integrity,
)
from backend.persistencia.repositories import (
    get_amendment,
    get_contract,
    get_document,
    get_invoice,
    get_rule,
    list_amendments,
    list_contracts,
    list_documents,
    list_invoices,
    list_rules,
    save_amendment,
    save_contract,
    save_document,
    save_invoice,
    save_rule,
)
from backend.persistencia.reviews import (
    append_review,
    list_reviews,
    record_audit_review,
    record_document_review,
    record_rule_review,
)

__all__ = [
    "SCHEMA_SQL",
    "TABLE_NAMES",
    "StoredAudit",
    "append_review",
    "compute_sha256",
    "connect",
    "get_amendment",
    "get_audit",
    "get_contract",
    "get_document",
    "get_invoice",
    "get_rule",
    "init_db",
    "list_amendments",
    "list_audits",
    "list_contracts",
    "list_documents",
    "list_invoices",
    "list_reviews",
    "list_rules",
    "receive_document",
    "record_audit_review",
    "record_document_review",
    "record_rule_review",
    "save_amendment",
    "save_audit",
    "save_contract",
    "save_document",
    "save_invoice",
    "save_rule",
    "verify_document_integrity",
]
