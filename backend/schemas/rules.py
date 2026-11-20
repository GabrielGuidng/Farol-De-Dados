"""Regra contratual e aditivo."""

from typing import Self

from pydantic import Field, model_validator

from backend.schemas.base import StrictModel
from backend.schemas.enums import ReviewStatus, RuleRelation, RuleType
from backend.schemas.evidence import Evidence
from backend.schemas.periods import ValidityPeriod
from backend.schemas.types import (
    EntityId,
    NonEmptyStr,
    PlainDate,
    Quantity,
    UnitPrice,
    VerificationScore,
)

_PRICE_TYPES = (RuleType.MONTHLY_PRICE, RuleType.OVERAGE_PRICE)


class ContractRule(StrictModel):
    """Regra extraida de um documento; sem evidencia nao existe regra."""

    id: EntityId
    contract_id: EntityId
    rule_type: RuleType
    value: UnitPrice | None = None
    quantity: Quantity | None = None
    unit: NonEmptyStr | None = None
    adjustment_index: NonEmptyStr | None = None
    periodicity_months: int | None = Field(default=None, ge=1)
    validity: ValidityPeriod = ValidityPeriod()
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    verification_score: VerificationScore | None = None
    evidence: Evidence
    relation: RuleRelation = RuleRelation.CREATED
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _require_fields_for_type(self) -> Self:
        if self.rule_type in _PRICE_TYPES and self.value is None:
            raise ValueError("regra de preco exige valor")
        if self.rule_type is RuleType.FRANCHISE and self.quantity is None:
            raise ValueError("franquia exige quantidade")
        if self.rule_type is RuleType.ADJUSTMENT and self.adjustment_index is None:
            raise ValueError("reajuste exige indice")
        if self.rule_type is RuleType.EXTENSION and self.validity.end is None:
            raise ValueError("prorrogacao exige o novo fim de vigencia")
        return self


class Amendment(StrictModel):
    """Aditivo que altera (ALTERED, com nova regra) ou revoga (REVOKED) uma regra existente."""

    id: EntityId
    affected_rule_id: EntityId
    new_rule: ContractRule | None = None
    source_document_id: EntityId
    effective_date: PlainDate
    relation: RuleRelation

    @model_validator(mode="after")
    def _check_relation(self) -> Self:
        if self.relation is RuleRelation.CREATED:
            raise ValueError("aditivo so pode alterar (ALTERED) ou revogar (REVOKED)")
        if self.relation is RuleRelation.ALTERED and self.new_rule is None:
            raise ValueError("aditivo ALTERED exige a nova regra")
        if self.relation is RuleRelation.REVOKED and self.new_rule is not None:
            raise ValueError("aditivo REVOKED nao tem nova regra")
        return self
