"""Camada de persistência SQLite: esquema, repositórios, decisões, snapshots e hash."""

from backend.persistencia.database import SCHEMA_SQL, TABLE_NAMES, connect, init_db

__all__ = [
    "SCHEMA_SQL",
    "TABLE_NAMES",
    "connect",
    "init_db",
]
