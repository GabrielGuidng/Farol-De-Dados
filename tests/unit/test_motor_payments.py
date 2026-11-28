"""Testes do motor de ordens de pagamento, certidões e reconciliação (P-05 a P-09)."""

from datetime import date
from decimal import Decimal

from backend.motor import (
    PAYMENT_APPROVAL_CHECK,
    PAYMENT_ATTESTATION_CHECK,
    PAYMENT_BENEFICIARY_CHECK,
    PAYMENT_BUSINESS_DAY_CHECK,
    PAYMENT_SEGREGATION_CHECK,
    audit_payment_cycle,
)
from backend.schemas import (
    CertificateType,
    Contract,
    ContractRule,
    ContractStatus,
    DossierFinancialSummary,
    Evidence,
    Invoice,
    InvoiceSource,
    PaymentOrder,
    ReferencePeriod,
    ReviewStatus,
    RuleRelation,
    RuleType,
    TaxCertificate,
    ValidityPeriod,
)


def _contract() -> Contract:
    return Contract(
        id="2025/0187",
        company="Alvorada Distribuidora de Alimentos Ltda",
        supplier="Norte Sul Facilities e Serviços Ltda",
        supplier_cnpj="28.741.963/0001-65",
        bank_code="341",
        bank_agency="1287",
        bank_account="45912-3",
        subject="Manutenção predial",
        validity=ValidityPeriod(start=date(2025, 3, 1), end=date(2026, 2, 28)),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )


def _approval_rule() -> ContractRule:
    return ContractRule(
        id="r-app",
        contract_id="2025/0187",
        rule_type=RuleType.APPROVAL_LIMIT,
        value=Decimal("50000.00"),
        validity=ValidityPeriod(start=date(2025, 3, 1), end=date(2026, 2, 28)),
        review_status=ReviewStatus.CONFIRMED,
        evidence=Evidence(
            source_document_id="doc-ct",
            page=3,
            clause="Cláusula 6ª",
            literal_excerpt="Valores até R$ 50.000,00 pelo Gerente; acima pela Diretoria.",
        ),
        relation=RuleRelation.CREATED,
    )


def _inv(number: str, month: int, att: date, total: str = "48500.00") -> Invoice:
    return Invoice(
        id=f"inv-{number}",
        contract_id="2025/0187",
        supplier="Norte Sul Facilities e Serviços Ltda",
        supplier_cnpj="28.741.963/0001-65",
        number=number,
        issue_date=date(2025, month, 28),
        attestation_date=att,
        reference_period=ReferencePeriod(year=2025, month=month),
        total=Decimal(total),
        source=InvoiceSource.PDF,
    )


def test_audit_payment_cycle_detects_all_traps_p05_to_p09() -> None:
    invoices = (
        _inv("1036", 4, date(2025, 5, 5)),
        _inv("1040", 5, date(2025, 6, 3)),
        _inv("1051", 9, date(2025, 10, 2), total="52300.00"),
        _inv("1058", 11, date(2025, 12, 5)),
        _inv("1062", 12, date(2026, 1, 5)),
    )
    orders = (
        PaymentOrder(
            id="op-0561",
            contract_id="2025/0187",
            op_number="2025/0561",
            payment_date=date(2025, 5, 29),
            invoice_number="1036",
            beneficiary_name="Norte Sul Facilities e Serv. Ltda",
            beneficiary_cnpj="28.741.963/0001-65",
            bank_code="341",
            bank_agency="1287",
            bank_account="45912-3",
            paid_amount=Decimal("42195.00"),
            requester_id="20417",
            approver_id="20417",
        ),
        PaymentOrder(
            id="op-0690",
            contract_id="2025/0187",
            op_number="2025/0690",
            payment_date=date(2025, 6, 27),
            invoice_number="1040",
            beneficiary_name="Norte Sul Serviços Gerais ME",
            beneficiary_cnpj="31.926.485/0001-72",
            bank_code="237",
            bank_agency="3340",
            bank_account="90218-7",
            paid_amount=Decimal("42195.00"),
            requester_id="27730",
            approver_id="18843",
        ),
        PaymentOrder(
            id="op-1188",
            contract_id="2025/0187",
            op_number="2025/1188",
            payment_date=date(2025, 10, 31),
            invoice_number="1051",
            beneficiary_name="Norte Sul Facilities e Serv. Ltda",
            beneficiary_cnpj="28.741.963/0001-65",
            bank_code="341",
            bank_agency="1287",
            bank_account="45912-3",
            paid_amount=Decimal("45501.00"),
            requester_id="27730",
            approver_id="18843",
            approver_role="Gerente Financeira",
            approver_limit=Decimal("50000.00"),
        ),
        PaymentOrder(
            id="op-1301",
            contract_id="2025/0187",
            op_number="2025/1301",
            payment_date=date(2025, 12, 2),
            invoice_number="1058",
            beneficiary_name="Norte Sul Facilities e Serv. Ltda",
            beneficiary_cnpj="28.741.963/0001-65",
            bank_code="341",
            bank_agency="1287",
            bank_account="45912-3",
            paid_amount=Decimal("42195.00"),
            requester_id="27730",
            approver_id="18843",
        ),
        PaymentOrder(
            id="op-0074",
            contract_id="2025/0187",
            op_number="2026/0074",
            payment_date=date(2026, 1, 31),  # Sábado
            invoice_number="1062",
            beneficiary_name="Norte Sul Facilities e Serv. Ltda",
            beneficiary_cnpj="28.741.963/0001-65",
            bank_code="341",
            bank_agency="1287",
            bank_account="45912-3",
            paid_amount=Decimal("42195.00"),
            requester_id="27730",
            approver_id="18843",
        ),
    )
    certificates = (
        TaxCertificate(
            id="c-cnd",
            contract_id="2025/0187",
            certificate_type=CertificateType.CND_FEDERAL,
            issue_date=date(2024, 12, 17),
            expiry_date=date(2025, 6, 15),
            raw_status="Não localizada",
        ),
        TaxCertificate(
            id="c-crf",
            contract_id="2025/0187",
            certificate_type=CertificateType.CRF_FGTS,
            issue_date=date(2025, 10, 20),
            expiry_date=date(2025, 11, 18),
            raw_status="Não localizada",
        ),
    )
    summary = DossierFinancialSummary(
        id="s-1",
        contract_id="2025/0187",
        source_document_id="doc-ct",
        reported_net_paid_total=Decimal("213781.00"),  # R$ 500,00 abaixo dos R$ 214.281,00 reais
    )

    res = audit_payment_cycle(
        _contract(),
        rules=(_approval_rule(),),
        invoices=invoices,
        payment_orders=orders,
        certificates=certificates,
        financial_summaries=(summary,),
    )

    codes = {f.code for f in res.findings}
    assert codes == {
        "PAYMENT_BENEFICIARY",
        "PAYMENT_BEFORE_ATTESTATION",
        "PAYMENT_SEGREGATION",
        "PAYMENT_APPROVAL_LIMIT",
        "PAYMENT_NON_BUSINESS_DAY",
        "CERTIFICATE_CND_FEDERAL",
        "CERTIFICATE_CRF_FGTS",
        "SUMMARY_NET_PAID_MISMATCH",
    }
    benef_finding = next(f for f in res.findings if f.code == "PAYMENT_BENEFICIARY")
    assert benef_finding.risk_amount == Decimal("42195.00")

    assert any(c.name == PAYMENT_SEGREGATION_CHECK for c in res.checks_by_invoice_number["1036"])
    assert any(c.name == PAYMENT_BENEFICIARY_CHECK for c in res.checks_by_invoice_number["1040"])
    assert any(c.name == PAYMENT_APPROVAL_CHECK for c in res.checks_by_invoice_number["1051"])
    assert any(c.name == PAYMENT_ATTESTATION_CHECK for c in res.checks_by_invoice_number["1058"])
    assert any(c.name == PAYMENT_BUSINESS_DAY_CHECK for c in res.checks_by_invoice_number["1062"])
