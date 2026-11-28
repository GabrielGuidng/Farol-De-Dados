"""Testes dos schemas de pagamentos, certidões, resumo financeiro e novos RuleType."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.schemas import (
    CertificateType,
    ContractRule,
    DossierFinancialSummary,
    PaymentOrder,
    ReviewStatus,
    RuleType,
    TaxCertificate,
)


def test_new_rule_types_validate_required_fields(rule_payload: dict) -> None:
    for rtype in (RuleType.INSS_RETENTION, RuleType.ISS_RETENTION, RuleType.APPROVAL_LIMIT):
        bad = {**rule_payload, "rule_type": rtype.value, "value": None}
        with pytest.raises(ValidationError):
            ContractRule.model_validate(bad)
        ok = ContractRule.model_validate({**rule_payload, "rule_type": rtype.value, "value": "11"})
        assert ok.rule_type is rtype
        assert ok.review_status is ReviewStatus.PENDING_REVIEW

    terms_bad = {**rule_payload, "rule_type": RuleType.PAYMENT_TERMS.value, "value": None}
    with pytest.raises(ValidationError):
        ContractRule.model_validate(terms_bad)
    terms_ok = ContractRule.model_validate(
        {**rule_payload, "rule_type": RuleType.PAYMENT_TERMS.value, "value": None, "quantity": "30"}
    )
    assert terms_ok.quantity == Decimal("30")


def test_payment_order_certificate_and_summary_models() -> None:
    op = PaymentOrder(
        id="op-1",
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
        approver_role="Gerente Financeira",
        approver_limit=Decimal("50000.00"),
    )
    assert op.beneficiary_cnpj == "31926485000172"
    assert op.paid_amount == Decimal("42195.00")

    cert = TaxCertificate(
        id="cert-1",
        contract_id="2025/0187",
        certificate_type=CertificateType.CRF_FGTS,
        issue_date=date(2025, 10, 20),
        expiry_date=date(2025, 11, 18),
        raw_status="Não localizada",
    )
    assert cert.certificate_type is CertificateType.CRF_FGTS

    summary = DossierFinancialSummary(
        id="sum-1",
        contract_id="2025/0187",
        source_document_id="doc-1",
        competences_count=10,
        reported_gross_total=Decimal("537300.00"),
        reported_net_paid_total=Decimal("467436.00"),
    )
    assert summary.reported_net_paid_total == Decimal("467436.00")

    with pytest.raises(ValidationError):
        PaymentOrder.model_validate(
            {
                "id": "op-2",
                "op_number": "2025/1",
                "payment_date": "2025-04-28",
                "invoice_number": "1031",
                "beneficiary_name": "Fornecedor",
                "paid_amount": 100.5,
            }
        )
