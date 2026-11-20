"""Contratos de dados Pydantic: nada passa entre modulos sem ser validado aqui."""

from backend.schemas.base import StrictModel
from backend.schemas.enums import (
    CLASSIFICATION_SEVERITY_ORDER,
    AuditClassification,
    CheckResult,
    ContractStatus,
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
    "AuditClassification",
    "CheckResult",
    "ContractStatus",
    "DocumentType",
    "InvoiceSource",
    "Money",
    "PlainDate",
    "Quantity",
    "ReadQuality",
    "ReviewDecision",
    "ReviewStatus",
    "ReviewTargetType",
    "RuleRelation",
    "RuleType",
    "SignedMoney",
    "StrictModel",
    "UnitPrice",
    "VerificationScore",
    "most_severe",
]
