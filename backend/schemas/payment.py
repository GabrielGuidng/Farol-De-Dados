"""Ordens de pagamento, certidoes de regularidade e resumo financeiro."""

from pydantic import Field

from backend.schemas.base import StrictModel
from backend.schemas.cnpj import Cnpj
from backend.schemas.enums import CertificateType
from backend.schemas.types import EntityId, Money, NonEmptyStr, PlainDate


class PaymentOrder(StrictModel):
    """Ordem de pagamento vinculada a uma nota fiscal e a um contrato."""

    id: EntityId
    contract_id: EntityId | None = None
    source_document_id: EntityId | None = None
    op_number: NonEmptyStr
    payment_date: PlainDate
    invoice_number: NonEmptyStr
    invoice_id: EntityId | None = None
    beneficiary_name: NonEmptyStr
    beneficiary_cnpj: Cnpj | None = None
    raw_beneficiary_cnpj: str | None = None
    bank_code: str | None = None
    bank_agency: str | None = None
    bank_account: str | None = None
    paid_amount: Money
    requester_id: str | None = None
    approver_id: str | None = None
    approver_role: str | None = None
    approver_limit: Money | None = None


class TaxCertificate(StrictModel):
    """Certidao de regularidade fiscal ou trabalhista da contratada."""

    id: EntityId
    contract_id: EntityId
    source_document_id: EntityId | None = None
    certificate_type: CertificateType
    issue_date: PlainDate | None = None
    expiry_date: PlainDate
    raw_status: str | None = None


class DossierFinancialSummary(StrictModel):
    """Totais declarados em quadro de resumo financeiro para reconciliacao (P-09)."""

    id: EntityId
    contract_id: EntityId
    source_document_id: EntityId
    competences_count: int | None = Field(default=None, ge=0)
    reported_contracted_total: Money | None = None
    reported_gross_total: Money | None = None
    reported_inss_total: Money | None = None
    reported_iss_total: Money | None = None
    reported_net_paid_total: Money | None = None
