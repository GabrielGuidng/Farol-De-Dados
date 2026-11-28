"""Inicialização do banco SQLite e esquema SQL das entidades do sistema."""

import sqlite3
from pathlib import Path

TABLE_NAMES: tuple[str, ...] = (
    "contracts",
    "documents",
    "evidences",
    "rules",
    "amendments",
    "invoices",
    "billing_items",
    "audits",
    "reviews",
    "payment_orders",
    "tax_certificates",
    "financial_summaries",
)

# Valores monetários, decimais e datas são guardados como TEXT (nunca REAL/FLOAT) para preservar
# a precisão exata de Decimal e o formato ISO-8601.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    supplier TEXT NOT NULL,
    supplier_cnpj TEXT NOT NULL,
    legal_nature TEXT,
    bank_code TEXT,
    bank_agency TEXT,
    bank_account TEXT,
    subject TEXT NOT NULL,
    validity_start TEXT,
    validity_end TEXT,
    comparison_tolerance TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    contract_id TEXT,
    document_type TEXT NOT NULL,
    file_name TEXT NOT NULL,
    received_at TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    read_quality TEXT NOT NULL,
    reviewed INTEGER NOT NULL DEFAULT 0,
    extracted_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    value TEXT,
    quantity TEXT,
    unit TEXT,
    item_name TEXT,
    quantity_scope TEXT,
    adjustment_index TEXT,
    periodicity_months INTEGER,
    validity_start TEXT,
    validity_end TEXT,
    review_status TEXT NOT NULL,
    verification_score TEXT,
    relation TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS evidences (
    rule_id TEXT PRIMARY KEY REFERENCES rules(id) ON DELETE CASCADE,
    source_document_id TEXT NOT NULL,
    page INTEGER,
    clause TEXT,
    paragraph TEXT,
    literal_excerpt TEXT NOT NULL,
    without_page INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS amendments (
    id TEXT PRIMARY KEY,
    affected_rule_id TEXT NOT NULL,
    new_rule_id TEXT,
    new_rule_json TEXT,
    source_document_id TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    relation TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    contract_id TEXT,
    supplier TEXT NOT NULL,
    supplier_cnpj TEXT NOT NULL,
    raw_supplier_cnpj TEXT,
    supplier_legal_nature TEXT,
    number TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    attestation_date TEXT,
    reference_period TEXT NOT NULL,
    competence_inferred INTEGER NOT NULL DEFAULT 0,
    total TEXT NOT NULL,
    inss_withheld TEXT,
    iss_withheld TEXT,
    net_total TEXT,
    service_description TEXT,
    measured_hours TEXT,
    expected_glosa TEXT,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    description TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit TEXT,
    unit_value TEXT NOT NULL,
    total_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL,
    classification TEXT NOT NULL,
    reference_period TEXT NOT NULL,
    base TEXT,
    difference TEXT,
    total TEXT NOT NULL,
    checks_json TEXT NOT NULL,
    applied_rules_json TEXT NOT NULL,
    rules_snapshot_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    user TEXT NOT NULL,
    decision TEXT NOT NULL,
    justification TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS reviews_no_update
BEFORE UPDATE ON reviews
BEGIN
    SELECT RAISE(ABORT, 'reviews is append-only');
END;

CREATE TRIGGER IF NOT EXISTS reviews_no_delete
BEFORE DELETE ON reviews
BEGIN
    SELECT RAISE(ABORT, 'reviews is append-only');
END;

CREATE TABLE IF NOT EXISTS payment_orders (
    id TEXT PRIMARY KEY,
    contract_id TEXT,
    source_document_id TEXT,
    op_number TEXT NOT NULL,
    payment_date TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_id TEXT,
    beneficiary_name TEXT NOT NULL,
    beneficiary_cnpj TEXT,
    raw_beneficiary_cnpj TEXT,
    bank_code TEXT,
    bank_agency TEXT,
    bank_account TEXT,
    paid_amount TEXT NOT NULL,
    requester_id TEXT,
    approver_id TEXT,
    approver_role TEXT,
    approver_limit TEXT
);

CREATE TABLE IF NOT EXISTS tax_certificates (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    source_document_id TEXT,
    certificate_type TEXT NOT NULL,
    issue_date TEXT,
    expiry_date TEXT NOT NULL,
    raw_status TEXT
);

CREATE TABLE IF NOT EXISTS financial_summaries (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    source_document_id TEXT NOT NULL,
    competences_count INTEGER,
    reported_contracted_total TEXT,
    reported_gross_total TEXT,
    reported_inss_total TEXT,
    reported_iss_total TEXT,
    reported_net_paid_total TEXT
);
"""


_COLUMN_MIGRATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "contracts": (
        ("legal_nature", "TEXT"),
        ("bank_code", "TEXT"),
        ("bank_agency", "TEXT"),
        ("bank_account", "TEXT"),
    ),
    "rules": (
        ("item_name", "TEXT"),
        ("quantity_scope", "TEXT"),
    ),
    "invoices": (
        ("raw_supplier_cnpj", "TEXT"),
        ("supplier_legal_nature", "TEXT"),
        ("attestation_date", "TEXT"),
        ("inss_withheld", "TEXT"),
        ("iss_withheld", "TEXT"),
        ("net_total", "TEXT"),
        ("service_description", "TEXT"),
        ("expected_glosa", "TEXT"),
    ),
}


def _ensure_column_migrations(conn: sqlite3.Connection) -> None:
    """Adiciona colunas novas de forma idempotente em bancos SQLite pré-existentes."""
    for table, specs in _COLUMN_MIGRATIONS.items():
        existing = {
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for col_name, col_type in specs:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


def connect(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    """Abre conexão SQLite com acesso por nome de coluna e chaves estrangeiras ativas."""
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    elif db_path != ":memory:" and not db_path.startswith("file:"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(target: sqlite3.Connection | str | Path = ":memory:") -> sqlite3.Connection:
    """Cria tabelas e triggers de forma idempotente; aceita conexão aberta ou caminho do banco."""
    conn = target if isinstance(target, sqlite3.Connection) else connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_SQL)
    _ensure_column_migrations(conn)
    return conn
