"""Resultado da auditoria, verificacoes individuais e revisao humana."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from backend.schemas.base import StrictModel
from backend.schemas.enums import (
    AuditClassification,
    CheckResult,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
)
from backend.schemas.periods import ReferencePeriod
from backend.schemas.types import EntityId, Money, NonEmptyStr, SignedMoney

_DECISION_TO_STATUS = {
    ReviewDecision.CONFIRM: ReviewStatus.CONFIRMED,
    ReviewDecision.CORRECT: ReviewStatus.CORRECTED,
    ReviewDecision.REJECT: ReviewStatus.REJECTED,
}


class Check(StrictModel):
    """Verificacao individual, com a memoria de calculo (uma linha por passo) e a regra usada."""

    name: NonEmptyStr
    result: CheckResult
    message: NonEmptyStr
    calculation_memory: tuple[str, ...] = ()
    rule_id: EntityId | None = None


class AppliedRule(StrictModel):
    """Regra usada na auditoria, com a versao vigente na epoca."""

    rule_id: EntityId
    version: int = Field(ge=1)


class AuditResult(StrictModel):
    """Resultado da auditoria de uma cobranca; base e diferenca ficam vazias se nao calculadas."""

    id: EntityId
    invoice_id: EntityId
    classification: AuditClassification
    checks: tuple[Check, ...] = ()
    applied_rules: tuple[AppliedRule, ...] = ()
    reference_period: ReferencePeriod
    base: Money | None = None
    difference: SignedMoney | None = None
    total: Money


class HumanReview(StrictModel):
    """Decisao humana registrada na trilha: alvo, estados anterior e posterior, justificativa."""

    target_type: ReviewTargetType
    target_id: EntityId
    user: NonEmptyStr
    decision: ReviewDecision
    justification: NonEmptyStr
    previous_status: ReviewStatus
    new_status: ReviewStatus
    reviewed_at: datetime

    @model_validator(mode="after")
    def _status_matches_decision(self) -> Self:
        if self.new_status is not _DECISION_TO_STATUS[self.decision]:
            raise ValueError("estado posterior incompativel com a decisao")
        return self
