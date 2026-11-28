"""Contratos de dados Pydantic: nada passa entre modulos sem ser validado aqui."""

from backend.schemas.audit import AppliedRule, AuditResult, Check, HumanReview
from backend.schemas.base import StrictModel
from backend.schemas.cnpj import Cnpj, calculate_cnpj_check_digits, is_valid_cnpj, normalize_cnpj
from backend.schemas.contract import Contract, Document
from backend.schemas.enums import (
    CLASSIFICATION_SEVERITY_ORDER,
    AuditClassification,
    CertificateType,
    CheckResult,
    ContractStatus,
    DivergenceDirection,
    DocumentType,
    InvoiceSource,
    ReadQuality,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    RuleRelation,
    RuleType,
    most_severe,
)
from backend.schemas.evidence import Evidence
from backend.schemas.invoice import BillingItem, Invoice
from backend.schemas.payment import DossierFinancialSummary, PaymentOrder, TaxCertificate
from backend.schemas.periods import ReferencePeriod, ValidityPeriod
from backend.schemas.rules import Amendment, ContractRule
from backend.schemas.types import (
    Money,
    PlainDate,
    Quantity,
    SignedMoney,
    UnitPrice,
    VerificationScore,
)

__all__ = [
    "CLASSIFICATION_SEVERITY_ORDER",
    "Amendment",
    "AppliedRule",
    "AuditClassification",
    "AuditResult",
    "BillingItem",
    "CertificateType",
    "Check",
    "CheckResult",
    "Cnpj",
    "Contract",
    "ContractRule",
    "ContractStatus",
    "DivergenceDirection",
    "Document",
    "DocumentType",
    "DossierFinancialSummary",
    "Evidence",
    "HumanReview",
    "Invoice",
    "InvoiceSource",
    "Money",
    "PaymentOrder",
    "PlainDate",
    "Quantity",
    "ReadQuality",
    "ReferencePeriod",
    "ReviewDecision",
    "ReviewStatus",
    "ReviewTargetType",
    "RuleRelation",
    "RuleType",
    "SignedMoney",
    "StrictModel",
    "TaxCertificate",
    "UnitPrice",
    "ValidityPeriod",
    "VerificationScore",
    "calculate_cnpj_check_digits",
    "is_valid_cnpj",
    "most_severe",
    "normalize_cnpj",
]
