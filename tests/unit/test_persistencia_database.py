"""Testes do banco SQLite e do esquema SQL (Etapa 3.1)."""

import sqlite3
from pathlib import Path

from backend.persistencia import TABLE_NAMES, connect, init_db


def test_init_db_creates_all_expected_tables() -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    created = {row["name"] for row in rows}
    assert created == set(TABLE_NAMES)
    conn.close()


def test_init_db_is_idempotent_and_preserves_data(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "farol.sqlite"
    conn = init_db(db_file)
    conn.execute(
        """
        INSERT INTO contracts (
            id, company, supplier, supplier_cnpj, subject,
            validity_start, validity_end, comparison_tolerance, status
        ) VALUES ('CT-1', 'Emp', 'Forn', '11222333000181', 'Suporte', NULL, NULL, '0.00', 'ACTIVE')
        """
    )
    conn.commit()

    # Reexecutar init_db na mesma conexão e em nova conexão no mesmo arquivo mantém os dados.
    init_db(conn)
    conn.close()

    reopened = init_db(db_file)
    row = reopened.execute("SELECT id, comparison_tolerance FROM contracts").fetchone()
    assert row is not None
    assert row["id"] == "CT-1"
    assert row["comparison_tolerance"] == "0.00"
    reopened.close()


def test_schema_never_uses_real_or_float_columns() -> None:
    conn = init_db()
    for table in TABLE_NAMES:
        columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert columns, f"tabela {table} sem colunas"
        for col in columns:
            col_type = str(col["type"]).upper()
            assert "REAL" not in col_type
            assert "FLOAT" not in col_type
            assert "DOUBLE" not in col_type
    conn.close()


def test_connect_enables_foreign_keys_and_row_factory() -> None:
    conn = connect()
    fk_row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk_row is not None
    assert isinstance(fk_row, sqlite3.Row)
    assert fk_row[0] == 1
    conn.close()
