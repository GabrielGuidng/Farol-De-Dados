"""Construtores de cenários reutilizáveis para os testes do motor (ver testes.md, seção 4)."""

from datetime import date, datetime
from decimal import Decimal

from backend.schemas import (
    Amendment,
    BillingItem,
    Contract,
    ContractRule,
    Document,
    DocumentType,
    Invoice,
    ReadQuality,
    ReviewStatus,
    RuleRelation,
    RuleType,
)

CNPJ = "11.222.333/0001-81"


def make_rule(
    rule_id: str,
    rule_type: RuleType = RuleType.MONTHLY_PRICE,
    *,
    value: str | None = None,
    quantity: str | None = None,
    start: date | None = None,
    end: date | None = None,
    status: ReviewStatus = ReviewStatus.CONFIRMED,
    document: str = "contrato",
    page: int = 1,
    version: int = 1,
    excerpt: str = "Trecho literal de teste.",
    index: str | None = None,
    periodicity: int | None = None,
) -> ContractRule:
    return ContractRule.model_validate(
        {
            "id": rule_id,
            "contract_id": "CT-2026-001",
            "rule_type": rule_type,
            "value": value,
            "quantity": quantity,
            "adjustment_index": index,
            "periodicity_months": periodicity,
            "validity": {"start": start, "end": end},
            "review_status": status,
            "evidence": {
                "source_document_id": document,
                "page": page,
                "literal_excerpt": excerpt,
            },
            "version": version,
        }
    )


def make_amendment(
    amendment_id: str,
    affected_rule_id: str,
    effective: date,
    new_rule: ContractRule | None,
    relation: RuleRelation = RuleRelation.ALTERED,
) -> Amendment:
    return Amendment(
        id=amendment_id,
        affected_rule_id=affected_rule_id,
        new_rule=new_rule,
        source_document_id=f"doc-{amendment_id}",
        effective_date=effective,
        relation=relation,
    )


def make_contract(
    *, start: date | None = date(2026, 1, 1), end: date | None = date(2026, 12, 31)
) -> Contract:
    return Contract.model_validate(
        {
            "id": "CT-2026-001",
            "company": "Empresa Exemplo",
            "supplier": "Fornecedor Exemplo",
            "supplier_cnpj": CNPJ,
            "subject": "Serviços de suporte",
            "validity": {"start": start, "end": end},
            "comparison_tolerance": "0.00",
            "status": "ACTIVE",
        }
    )


def base_rules(status: ReviewStatus = ReviewStatus.CONFIRMED) -> list[ContractRule]:
    """Contrato CT-2026-001: preço base R$ 10.000,00, franquia de 40 h, excedente R$ 150,00/h."""
    jan = date(2026, 1, 1)
    return [
        make_rule(
            "price-base",
            value="10000.00",
            start=jan,
            status=status,
            page=2,
            excerpt="O valor mensal é de R$ 10.000,00.",
        ),
        make_rule(
            "franchise",
            RuleType.FRANCHISE,
            quantity="40",
            start=jan,
            status=status,
            page=3,
            excerpt="Franquia de 40 horas mensais.",
        ),
        make_rule(
            "overage",
            RuleType.OVERAGE_PRICE,
            value="150.00",
            start=jan,
            status=status,
            page=3,
            excerpt="Hora excedente a R$ 150,00.",
        ),
    ]


def dev_amendments(status: ReviewStatus = ReviewStatus.CONFIRMED) -> list[Amendment]:
    """Aditivo 01 (R$ 10.800,00 desde 01/05/2026) e Aditivo 02 (R$ 10.500,00 desde 01/09/2026)."""
    add01 = make_rule(
        "price-add01",
        value="10800.00",
        status=status,
        document="aditivo-01",
        page=1,
        excerpt="Passa a R$ 10.800,00 a partir de maio de 2026.",
    )
    add02 = make_rule(
        "price-add02",
        value="10500.00",
        status=status,
        document="aditivo-02",
        page=2,
        excerpt="Passa a R$ 10.500,00 a partir de setembro de 2026.",
    )
    return [
        make_amendment("add01", "price-base", date(2026, 5, 1), add01),
        make_amendment("add02", "price-add01", date(2026, 9, 1), add02),
    ]


def make_item(
    description: str,
    quantity: str,
    unit_value: str,
    total_value: str,
    unit: str | None = None,
) -> BillingItem:
    return BillingItem(
        description=description,
        quantity=quantity,
        unit=unit,
        unit_value=unit_value,
        total_value=total_value,
    )


def make_invoice(
    invoice_id: str,
    number: str,
    period: str,
    items: list[BillingItem],
    *,
    total: str | None = None,
    measured_hours: str | None = None,
    contract_id: str | None = "CT-2026-001",
    inferred: bool = False,
    cnpj: str = CNPJ,
) -> Invoice:
    items_total = sum((item.total_value for item in items), Decimal("0"))
    return Invoice(
        id=invoice_id,
        contract_id=contract_id,
        supplier="Fornecedor Exemplo",
        supplier_cnpj=cnpj,
        number=number,
        issue_date=date(2027, 2, 1),
        reference_period=period,
        competence_inferred=inferred,
        total=total if total is not None else items_total,
        measured_hours=measured_hours,
        source="PDF",
        items=tuple(items),
    )


def monthly_item(amount: str) -> BillingItem:
    return make_item("Mensalidade de suporte", "1", amount, amount, "mês")


def overage_item(hours: str, unit_price: str = "150.00") -> BillingItem:
    total = Decimal(hours) * Decimal(unit_price)
    return make_item("Horas excedentes de suporte", hours, unit_price, str(total), "hora")


def make_document(document_id: str, *, reviewed: bool = True) -> Document:
    return Document(
        id=document_id,
        contract_id="CT-2026-001",
        document_type=DocumentType.AMENDMENT,
        file_name=f"{document_id}.pdf",
        received_at=datetime(2026, 11, 1, 10, 0, 0),
        file_hash="a" * 64,
        read_quality=ReadQuality.OK,
        reviewed=reviewed,
        extracted_text="Texto do aditivo.",
    )


def dev_documents(*, reviewed: bool = True) -> list[Document]:
    """Documentos dos dois aditivos do cenário (ids `doc-add01` e `doc-add02`)."""
    return [
        make_document("doc-add01", reviewed=reviewed),
        make_document("doc-add02", reviewed=reviewed),
    ]


def dev_invoices() -> list[Invoice]:
    """As 8 cobranças do cenário de desenvolvimento (testes.md, seção 4)."""
    return [
        make_invoice("inv-101", "101", "2026-04", [monthly_item("10000.00")], measured_hours="38"),
        make_invoice("inv-102", "102", "2026-06", [monthly_item("10800.00")], measured_hours="40"),
        make_invoice(
            "inv-103",
            "103",
            "2026-07",
            [monthly_item("10800.00"), overage_item("8")],
            measured_hours="46",
        ),
        make_invoice("inv-104", "104", "2026-10", [monthly_item("10800.00")]),
        make_invoice("inv-105", "105", "2026-08", [monthly_item("10800.00")], measured_hours="40"),
        make_invoice("inv-106", "106", "2026-08", [monthly_item("10800.00")], measured_hours="40"),
        make_invoice("inv-107", "107", "2027-01", [monthly_item("10500.00")]),
        make_invoice(
            "inv-108",
            "108",
            "2026-11",
            [monthly_item("10500.00"), overage_item("3")],
        ),
    ]
