"""Teste de integração ponta a ponta: migração SQLite, dossiê 2025/0187 e relatório."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from backend.api import (
    audit_stored_invoice,
    delete_uploaded_document,
    fetch_payment_cycle_audit,
    ingest_uploaded_files,
    register_contract,
    review_document,
    review_rule,
)
from backend.persistencia import (
    connect,
    init_db,
    list_invoices,
    list_rules,
    receive_document,
    save_rule,
)
from backend.schemas import (
    Contract,
    ContractRule,
    ContractStatus,
    Evidence,
    ReviewDecision,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from frontend.ui.executive_report import build_executive_report

_APP_PATH = str(Path(__file__).resolve().parents[2] / "frontend" / "app.py")

_TS = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)


def test_init_db_migrates_legacy_sqlite_schema_without_data_loss(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    raw_conn = connect(db_path)
    raw_conn.executescript(
        """
        CREATE TABLE contracts (
            id TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            supplier TEXT NOT NULL,
            supplier_cnpj TEXT NOT NULL,
            subject TEXT NOT NULL,
            validity_start TEXT,
            validity_end TEXT,
            comparison_tolerance TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE invoices (
            id TEXT PRIMARY KEY,
            contract_id TEXT,
            supplier TEXT NOT NULL,
            supplier_cnpj TEXT NOT NULL,
            number TEXT NOT NULL,
            issue_date TEXT NOT NULL,
            reference_period TEXT NOT NULL,
            competence_inferred INTEGER NOT NULL DEFAULT 0,
            total TEXT NOT NULL,
            measured_hours TEXT,
            source TEXT NOT NULL
        );
        INSERT INTO contracts VALUES (
            '2025/0187', 'Alvorada', 'Norte Sul', '28741963000165', 'Servicos',
            '2025-03-01', '2026-02-28', '0.00', 'ACTIVE'
        );
        INSERT INTO invoices VALUES (
            'inv-legacy-1', '2025/0187', 'Norte Sul', '28741963000165', '1031',
            '2025-03-31', '2025-03', 0, '48500.00', NULL, 'PDF'
        );
        """
    )
    raw_conn.close()

    migrated = init_db(db_path)
    inv_cols = {row["name"] for row in migrated.execute("PRAGMA table_info(invoices)").fetchall()}
    assert {"raw_supplier_cnpj", "attestation_date", "inss_withheld"}.issubset(inv_cols)
    invoices = list_invoices(migrated, "2025/0187")
    assert len(invoices) == 1
    assert invoices[0].number == "1031"
    migrated.close()


def test_full_cycle_with_csv_invoices_ops_certificates_and_cleanup() -> None:
    conn = init_db(":memory:")
    invoices_csv = (
        "numero;fornecedor;cnpj;contrato;emissao;competencia;atesto;total_nota;inss;iss;liquido;"
        "natureza_juridica;descricao;quantidade;valor_unitario;valor_total\n"
        "1031;Norte Sul Facilities e Serviços Ltda;28.741.963/0001-65;2025/0187;31/03/2025;2025-03;"
        "03/04/2025;48.500,00;5.335,00;970,00;42.195,00;Sociedade Empresária Limitada;"
        "Serviços contrato 2025/0187;1;48.500,00;48.500,00\n"
        "1049;Norte Sul Facilities e Serviços Ltda;28.741.963/0001-68;2025/0187;01/09/2025;2025-08;"
        "03/09/2025;48.500,00;5.335,00;970,00;42.195,00;Sociedade Empresária Limitada;"
        "Serviços contrato 2025/0187;1;48.500,00;48.500,00\n"
        "1055;Norte Sul Facilities e Serviços Ltda;28.741.963/0001-65;2025/0187;31/10/2025;2025-10;"
        "04/11/2025;48.500,00;4.850,00;970,00;42.680,00;Sociedade Empresária Limitada;"
        "Serviços contrato 2025/0187;1;48.500,00;48.500,00\n"
    ).encode()
    ops_csv = (
        "op_numero;data_pgto;nf;beneficiario;cnpj;banco;agencia;conta;valor_pago;solicitante;"
        "aprovador;contrato\n"
        "2025/0412;28/04/2025;1031;Norte Sul Facilities e Serviços Ltda;28.741.963/0001-65;"
        "341;1287;45912-3;42.195,00;27730;18843;2025/0187\n"
    ).encode()
    certs_csv = (
        "certidao;emissao;validade;status;contrato\n"
        "CRF - FGTS;20/10/2025;18/11/2025;Não localizada;2025/0187\n"
    ).encode()

    register_contract(
        conn,
        Contract(
            id="2025/0187",
            company="Alvorada",
            supplier="Norte Sul Facilities e Serviços Ltda",
            supplier_cnpj="28.741.963/0001-65",
            legal_nature="Sociedade Empresária Limitada",
            bank_code="341",
            bank_agency="1287",
            bank_account="45912-3",
            subject="Facilities",
            validity=ValidityPeriod(start=datetime(2025, 3, 1, tzinfo=UTC).date()),
            comparison_tolerance=Decimal("0.00"),
            status=ContractStatus.ACTIVE,
        ),
    )
    doc = receive_document(
        document_id="doc-ct",
        contract_id="2025/0187",
        document_type="CONTRACT",
        file_name="contrato.pdf",
        content=b"contrato",
        received_at=_TS,
        extracted_text="Valor R$ 48.500,00; INSS 11%; ISS 2%.",
        conn=conn,
    )
    ev = Evidence(
        source_document_id=doc.id, page=1, clause="Cláusula 2ª", literal_excerpt="48.500,00"
    )
    for rid, rtype, val in (
        ("r-p", RuleType.MONTHLY_PRICE, Decimal("48500.00")),
        ("r-inss", RuleType.INSS_RETENTION, Decimal("11")),
        ("r-iss", RuleType.ISS_RETENTION, Decimal("2")),
    ):
        save_rule(
            conn,
            ContractRule(
                id=rid,
                contract_id="2025/0187",
                rule_type=rtype,
                value=val,
                validity=ValidityPeriod(start=datetime(2025, 3, 1, tzinfo=UTC).date()),
                review_status=ReviewStatus.PENDING_REVIEW,
                evidence=ev,
                relation=RuleRelation.CREATED,
            ),
        )
        review_rule(conn, rid, user="auditor", decision=ReviewDecision.CONFIRM, justification="ok")
    review_document(
        conn, doc.id, user="auditor", decision=ReviewDecision.CONFIRM, justification="ok"
    )

    batch = ingest_uploaded_files(
        conn,
        [("notas.csv", invoices_csv), ("ops.csv", ops_csv), ("certs.csv", certs_csv)],
        selected_contract_id="2025/0187",
        received_at=_TS,
    )
    assert len(list_invoices(conn, "2025/0187")) == 3
    audited = [
        (inv, audit_stored_invoice(conn, inv.id).stored_audit)
        for inv in list_invoices(conn, "2025/0187")
    ]
    pa = fetch_payment_cycle_audit(conn, "2025/0187")
    assert pa.payment_orders_count == 1
    assert pa.certificates_count == 1

    rep = build_executive_report(audited, payment_audit=pa)
    # 1049 tem CNPJ inválido (n/a), 1055 tem INSS retido a menor (+485,00), CRF vencida (n/a)
    assert rep.exposure_total == Decimal("485.00")
    assert any("dígitos" in f.problem or "dígito verificador" in f.problem for f in rep.findings)
    assert any("INSS" in f.problem for f in rep.findings)

    # Verifica que deletar os documentos enviados limpa em cascata OPs e certidões sem erro de FK
    for outcome in batch.outcomes:
        if outcome.document is not None:
            delete_uploaded_document(conn, outcome.document.id)
    assert fetch_payment_cycle_audit(conn, "2025/0187").payment_orders_count == 0
    assert len(list_rules(conn, "2025/0187")) == 3
    conn.close()


def test_streamlit_views_render_full_payment_and_retention_cycle(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    db_path = tmp_path / "ui_full_cycle.db"
    conn = init_db(db_path)
    register_contract(
        conn,
        Contract(
            id="2025/0187",
            company="Alvorada",
            supplier="Norte Sul Facilities e Serviços Ltda",
            supplier_cnpj="28.741.963/0001-65",
            bank_code="341",
            bank_agency="1287",
            bank_account="45912-3",
            subject="Facilities",
            validity=ValidityPeriod(start=datetime(2025, 3, 1, tzinfo=UTC).date()),
            comparison_tolerance=Decimal("0.00"),
            status=ContractStatus.ACTIVE,
        ),
    )
    doc = receive_document(
        document_id="doc-ui",
        contract_id="2025/0187",
        document_type="CONTRACT",
        file_name="dossie.pdf",
        content=b"dossie",
        received_at=_TS,
        extracted_text="Cláusula 2ª R$ 48.500,00. Cláusula 5ª INSS 11% e ISS 2%.",
        conn=conn,
    )
    ev = Evidence(
        source_document_id=doc.id, page=1, clause="Cláusula 2ª", literal_excerpt="48.500,00"
    )
    for rid, rtype, val in (
        ("r-ui-p", RuleType.MONTHLY_PRICE, Decimal("48500.00")),
        ("r-ui-inss", RuleType.INSS_RETENTION, Decimal("11")),
    ):
        save_rule(
            conn,
            ContractRule(
                id=rid,
                contract_id="2025/0187",
                rule_type=rtype,
                value=val,
                validity=ValidityPeriod(start=datetime(2025, 3, 1, tzinfo=UTC).date()),
                review_status=ReviewStatus.PENDING_REVIEW,
                evidence=ev,
                relation=RuleRelation.CREATED,
            ),
        )
        review_rule(conn, rid, user="auditor", decision=ReviewDecision.CONFIRM, justification="ok")
    review_document(
        conn, doc.id, user="auditor", decision=ReviewDecision.CONFIRM, justification="ok"
    )

    inv_csv = (
        b"numero;fornecedor;cnpj;contrato;emissao;competencia;atesto;total_nota;inss;"
        b"descricao;quantidade;valor_unitario;valor_total\n"
        b"1055;Norte Sul Facilities e Servicos Ltda;28.741.963/0001-65;2025/0187;"
        b"31/10/2025;2025-10;04/11/2025;48.500,00;4.850,00;Servicos;1;48.500,00;48.500,00\n"
    )
    op_csv = (
        b"op_numero;data_pgto;nf;beneficiario;cnpj;banco;agencia;conta;valor_pago;"
        b"solicitante;aprovador;contrato\n"
        b"2025/1260;28/11/2025;1055;Norte Sul Ltda;28.741.963/0001-65;"
        b"341;1287;45912-3;42.680,00;27730;18843;2025/0187\n"
    )
    cert_csv = (
        b"certidao;emissao;validade;status;contrato\n"
        b"CRF - FGTS;20/10/2025;18/11/2025;Nao localizada;2025/0187\n"
    )
    ingest_uploaded_files(
        conn,
        [("n.csv", inv_csv), ("o.csv", op_csv), ("c.csv", cert_csv)],
        selected_contract_id="2025/0187",
        received_at=_TS,
    )
    conn.close()

    monkeypatch.setenv("FAROL_DB_PATH", str(db_path))
    at = AppTest.from_file(_APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception
    for page_label in ("Regras e Vigência", "Cobranças e Auditoria", "Painel e Relatório"):
        at.sidebar.radio(key="nav_view_radio").set_value(page_label).run()
        assert not at.exception
