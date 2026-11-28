"""Testes do motor de conformidade cadastral, retenções, CNPJ e reajuste sem aditivo."""

from datetime import date
from decimal import Decimal

from backend.motor import (
    INSS_RETENTION_CHECK,
    SUPPLIER_IDENTITY_CHECK,
    UNAPPROVED_ADJUSTMENT_CHECK,
    audit_invoice,
    check_contract_link,
)
from backend.schemas import (
    AuditClassification,
    BillingItem,
    CheckResult,
    Contract,
    ContractRule,
    ContractStatus,
    DivergenceDirection,
    Evidence,
    Invoice,
    InvoiceSource,
    ReferencePeriod,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)


def _contract() -> Contract:
    return Contract(
        id="2025/0187",
        company="Alvorada Distribuidora de Alimentos Ltda",
        supplier="Norte Sul Facilities e Serviços Ltda",
        supplier_cnpj="28.741.963/0001-65",
        legal_nature="Sociedade Empresária Limitada",
        bank_code="341",
        bank_agency="1287",
        bank_account="45912-3",
        subject="Manutenção predial e facilities",
        validity=ValidityPeriod(start=date(2025, 3, 1), end=date(2026, 2, 28)),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )


def _rules() -> tuple[ContractRule, ...]:
    ev_price = Evidence(
        source_document_id="doc-ct",
        page=3,
        clause="Cláusula 2ª",
        literal_excerpt="Valor mensal fixo de R$ 48.500,00.",
    )
    ev_ret = Evidence(
        source_document_id="doc-ct",
        page=3,
        clause="Cláusula 5ª",
        literal_excerpt="Retenção de 11% de INSS e 2% de ISS.",
    )
    validity = ValidityPeriod(start=date(2025, 3, 1), end=date(2026, 2, 28))
    return (
        ContractRule(
            id="r-price",
            contract_id="2025/0187",
            rule_type=RuleType.MONTHLY_PRICE,
            value=Decimal("48500.00"),
            validity=validity,
            review_status=ReviewStatus.CONFIRMED,
            evidence=ev_price,
            relation=RuleRelation.CREATED,
        ),
        ContractRule(
            id="r-inss",
            contract_id="2025/0187",
            rule_type=RuleType.INSS_RETENTION,
            value=Decimal("11"),
            unit="%",
            validity=validity,
            review_status=ReviewStatus.CONFIRMED,
            evidence=ev_ret,
            relation=RuleRelation.CREATED,
        ),
        ContractRule(
            id="r-iss",
            contract_id="2025/0187",
            rule_type=RuleType.ISS_RETENTION,
            value=Decimal("2"),
            unit="%",
            validity=validity,
            review_status=ReviewStatus.CONFIRMED,
            evidence=ev_ret,
            relation=RuleRelation.CREATED,
        ),
    )


def _base_invoice(
    number: str,
    month: int,
    *,
    supplier: str = "Norte Sul Facilities e Serviços Ltda",
    supplier_cnpj: str = "28.741.963/0001-65",
    raw_supplier_cnpj: str | None = None,
    supplier_legal_nature: str | None = None,
    total: str = "48500.00",
    inss_withheld: str = "5335.00",
    iss_withheld: str = "970.00",
    service_description: str | None = None,
) -> Invoice:
    val = Decimal(total)
    return Invoice(
        id=f"inv-{number}",
        contract_id="2025/0187",
        supplier=supplier,
        supplier_cnpj=supplier_cnpj,
        raw_supplier_cnpj=raw_supplier_cnpj,
        supplier_legal_nature=supplier_legal_nature,
        number=number,
        issue_date=date(2025, month, 28),
        reference_period=ReferencePeriod(year=2025, month=month),
        total=val,
        inss_withheld=Decimal(inss_withheld),
        iss_withheld=Decimal(iss_withheld),
        service_description=service_description,
        source=InvoiceSource.PDF,
        items=(
            BillingItem(
                description=service_description or "Serviços de manutenção predial",
                quantity=Decimal("1"),
                unit="mês",
                unit_value=val,
                total_value=val,
            ),
        ),
    )


def test_nf1049_invalid_cnpj_check_digit_still_runs_independent_price_audit() -> None:
    nf1049 = _base_invoice(
        "1049",
        8,
        supplier_cnpj="00.000.000/0001-91",
        raw_supplier_cnpj="28.741.963/0001-68",
    )
    link = check_contract_link(_contract(), nf1049)
    assert link.result is CheckResult.PENDING
    assert "dígito verificador inválido" in link.message
    assert "-65" in link.message

    result = audit_invoice(nf1049, contract=_contract(), rules=_rules())
    assert result.classification is AuditClassification.MISSING_DOCUMENTATION
    assert result.base == Decimal("48500.00")
    assert result.difference == Decimal("0.00")
    by_name = {c.name: c for c in result.checks}
    assert by_name["monthly_price"].result is CheckResult.OK
    assert by_name["inss_retention"].result is CheckResult.OK


def test_nf1044_supplier_name_and_legal_nature_mismatch_triggers_review() -> None:
    nf1044 = _base_invoice(
        "1044",
        6,
        supplier="Norte Sul Facilites e Serviços Eireli",
        supplier_legal_nature="EIRELI",
    )
    result = audit_invoice(nf1044, contract=_contract(), rules=_rules())
    assert result.classification is AuditClassification.NEEDS_REVIEW
    by_name = {c.name: c for c in result.checks}
    assert SUPPLIER_IDENTITY_CHECK in by_name
    assert by_name[SUPPLIER_IDENTITY_CHECK].result is CheckResult.REVIEW
    assert "EIRELI" in by_name[SUPPLIER_IDENTITY_CHECK].message


def test_nf1055_inss_retention_shortfall_detects_485_divergence() -> None:
    nf1055 = _base_invoice("1055", 10, inss_withheld="4850.00")
    result = audit_invoice(nf1055, contract=_contract(), rules=_rules())
    assert result.classification is AuditClassification.DIVERGENCE
    assert result.difference == Decimal("0.00")
    by_name = {c.name: c for c in result.checks}
    inss_chk = by_name[INSS_RETENTION_CHECK]
    assert inss_chk.result is CheckResult.DIVERGENCE
    assert inss_chk.direction is DivergenceDirection.UNDERCHARGE
    assert "R$ 485,00" in inss_chk.message


def test_nf1051_detects_overcharge_and_unapproved_adjustment_description() -> None:
    nf1051 = _base_invoice(
        "1051",
        9,
        total="52300.00",
        inss_withheld="5753.00",
        iss_withheld="1046.00",
        service_description=(
            "Serviços conforme contrato, acrescido de reajuste contratual de R$ 3.800,00."
        ),
    )
    result = audit_invoice(nf1051, contract=_contract(), rules=_rules())
    assert result.classification is AuditClassification.DIVERGENCE
    assert result.difference == Decimal("3800.00")
    by_name = {c.name: c for c in result.checks}
    assert UNAPPROVED_ADJUSTMENT_CHECK in by_name
    assert by_name[UNAPPROVED_ADJUSTMENT_CHECK].result is CheckResult.DIVERGENCE
