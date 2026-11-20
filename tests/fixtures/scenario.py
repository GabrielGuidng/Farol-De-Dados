"""Construtores de cenários reutilizáveis para os testes do motor (ver testes.md, seção 4)."""

from datetime import date

from backend.schemas import (
    Amendment,
    Contract,
    ContractRule,
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
) -> ContractRule:
    return ContractRule.model_validate(
        {
            "id": rule_id,
            "contract_id": "CT-2026-001",
            "rule_type": rule_type,
            "value": value,
            "quantity": quantity,
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
