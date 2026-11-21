"""Camada de persistência SQLite: esquema, repositórios, decisões, snapshots e hash."""

from backend.persistencia.database import SCHEMA_SQL, TABLE_NAMES, connect, init_db
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

__all__ = [
    "SCHEMA_SQL",
    "TABLE_NAMES",
    "connect",
    "get_amendment",
    "get_contract",
    "get_document",
    "get_invoice",
    "get_rule",
    "init_db",
    "list_amendments",
    "list_contracts",
    "list_documents",
    "list_invoices",
    "list_rules",
    "save_amendment",
    "save_contract",
    "save_document",
    "save_invoice",
    "save_rule",
]
