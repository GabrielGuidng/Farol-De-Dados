"""Testes de integração I-03: repositórios SQLite preservam Decimal, datas e evidências."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from backend.persistencia import (
    get_amendment,
    get_contract,
    get_document,
    get_invoice,
    get_rule,
    init_db,
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
from backend.schemas import (
    Amendment,
    BillingItem,
    Contract,
    ContractRule,
    Document,
    DocumentType,
    Evidence,
    Invoice,
    InvoiceSource,
    ReadQuality,
    ReviewStatus,
    RuleRelation,
    RuleType,
)
from tests.fixtures.scenario import CNPJ, make_contract, make_document, make_rule


def test_i03_roundtrip_contract_and_document_across_connections(tmp_path: Path) -> None:
    db_file = tmp_path / "repo.db"
    conn = init_db(db_file)

    contract = Contract.model_validate(
        {
            **make_contract(start=date(2026, 1, 1), end=None).model_dump(),
            "comparison_tolerance": "0.05",
        }
    )
    document = Document(
        id="doc-1",
        contract_id=contract.id,
        document_type=DocumentType.CONTRACT,
        file_name="contrato.pdf",
        received_at=datetime(2026, 11, 10, 14, 30, 15),
        file_hash="b" * 64,
        read_quality=ReadQuality.OK,
        reviewed=True,
        extracted_text="Texto completo do contrato.",
    )
    save_contract(conn, contract)
    save_document(conn, document)
    conn.close()

    reopened = init_db(db_file)
    loaded_contract = get_contract(reopened, contract.id)
    loaded_document = get_document(reopened, document.id)
    assert loaded_contract == contract
    assert loaded_contract is not None
    assert loaded_contract.comparison_tolerance == Decimal("0.05")
    assert loaded_contract.validity.start == date(2026, 1, 1)
    assert loaded_contract.validity.end is None
    assert loaded_document == document
    assert list_contracts(reopened) == (contract,)
    assert list_documents(reopened, contract_id=contract.id) == (document,)
    assert list_documents(reopened, contract_id="OUTRO") == ()
    assert get_contract(reopened, "inexistente") is None
    assert get_document(reopened, "inexistente") is None
    reopened.close()


def test_i03_roundtrip_rules_with_pdf_and_docx_evidence_and_amendments() -> None:
    conn = init_db()
    pdf_rule = make_rule(
        "rule-pdf",
        RuleType.MONTHLY_PRICE,
        value="10500.00",
        start=date(2026, 5, 1),
        end=date(2026, 12, 31),
        status=ReviewStatus.CONFIRMED,
        document="doc-1",
        page=2,
        version=2,
        excerpt="Valor mensal R$ 10.500,00.",
    ).model_copy(update={"verification_score": Decimal("0.9375")})

    docx_rule = ContractRule(
        id="rule-docx",
        contract_id="CT-2026-001",
        rule_type=RuleType.ADJUSTMENT,
        adjustment_index="IPCA",
        periodicity_months=12,
        unit="meses",
        evidence=Evidence(
            source_document_id="doc-docx",
            clause="8.1",
            paragraph="2",
            literal_excerpt="Reajuste anual pelo IPCA.",
        ),
    )

    save_rule(conn, pdf_rule)
    save_rule(conn, docx_rule)

    altered = Amendment(
        id="amend-1",
        affected_rule_id="rule-pdf",
        new_rule=make_rule(
            "rule-pdf-v3",
            value="10800.00",
            start=date(2026, 9, 1),
            document="doc-add01",
            page=1,
            version=3,
            excerpt="Novo valor R$ 10.800,00.",
        ),
        source_document_id="doc-add01",
        effective_date=date(2026, 9, 1),
        relation=RuleRelation.ALTERED,
    )
    revoked = Amendment(
        id="amend-2",
        affected_rule_id="rule-docx",
        new_rule=None,
        source_document_id="doc-add02",
        effective_date=date(2026, 10, 1),
        relation=RuleRelation.REVOKED,
    )
    save_amendment(conn, altered)
    save_amendment(conn, revoked)

    loaded_pdf = get_rule(conn, "rule-pdf")
    loaded_docx = get_rule(conn, "rule-docx")
    assert loaded_pdf == pdf_rule
    assert loaded_pdf is not None and loaded_pdf.evidence.without_page is False
    assert loaded_pdf.verification_score == Decimal("0.9375")
    assert loaded_docx == docx_rule
    assert loaded_docx is not None and loaded_docx.evidence.without_page is True
    assert get_amendment(conn, "amend-1") == altered
    assert get_amendment(conn, "amend-2") == revoked
    assert len(list_rules(conn, contract_id="CT-2026-001")) == 3
    assert list_amendments(conn, source_document_id="doc-add01") == (altered,)
    assert get_rule(conn, "nao-existe") is None
    assert get_amendment(conn, "nao-existe") is None
    conn.close()


def test_i03_roundtrip_invoice_preserves_ten_decimal_unit_price_and_items_order() -> None:
    conn = init_db()
    items = (
        BillingItem(
            description="Mensalidade base",
            quantity=Decimal("1"),
            unit="mês",
            unit_value=Decimal("10500.00"),
            total_value=Decimal("10500.00"),
        ),
        BillingItem(
            description="Horas excedentes fracionadas",
            quantity=Decimal("3.25"),
            unit="hora",
            unit_value=Decimal("150.1234567890"),
            total_value=Decimal("487.90"),
        ),
    )
    invoice = Invoice.model_validate(
        {
            "id": "inv-1",
            "contract_id": None,
            "supplier": "Fornecedor Exemplo",
            "supplier_cnpj": CNPJ,
            "number": "NF-900",
            "issue_date": "2026-11-05",
            "reference_period": "2026-10",
            "competence_inferred": True,
            "total": "10987.90",
            "measured_hours": "43.25",
            "source": InvoiceSource.NFE_XML,
            "items": items,
        }
    )
    save_invoice(conn, invoice)

    loaded = get_invoice(conn, "inv-1")
    assert loaded == invoice
    assert loaded is not None
    assert loaded.items[1].unit_value == Decimal("150.1234567890")
    assert loaded.measured_hours == Decimal("43.25")
    assert str(loaded.reference_period) == "2026-10"
    assert loaded.issue_date == date(2026, 11, 5)
    assert loaded.competence_inferred is True

    # Atualizar a cobrança com novos itens substitui a lista sem deixar sobras.
    updated = invoice.model_copy(
        update={
            "contract_id": "CT-2026-001",
            "items": (items[0],),
            "total": Decimal("10500.00"),
        }
    )
    save_invoice(conn, updated)
    reloaded = get_invoice(conn, "inv-1")
    assert reloaded == updated
    assert list_invoices(conn, contract_id="CT-2026-001") == (updated,)
    assert get_invoice(conn, "inexistente") is None

    # Também valida upsert de documento já existente.
    doc = make_document("doc-up", reviewed=False)
    save_document(conn, doc)
    save_document(conn, doc.model_copy(update={"reviewed": True}))
    assert get_document(conn, "doc-up") == doc.model_copy(update={"reviewed": True})
    conn.close()
