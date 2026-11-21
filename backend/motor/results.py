"""Resultados tipados do motor: incerteza de domínio é resultado, não exceção."""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import model_validator

from backend.schemas import Check, ContractRule, DivergenceDirection, StrictModel
from backend.schemas.types import Money, NonEmptyStr, SignedMoney


class ReasonCode(StrEnum):
    """Motivo estável (legível por máquina) de uma abstenção ou alerta do motor."""

    NO_APPLICABLE_RULE = "NO_APPLICABLE_RULE"
    MID_PERIOD_CHANGE = "MID_PERIOD_CHANGE"
    CONFLICT = "CONFLICT"
    MISSING_START_DATE = "MISSING_START_DATE"
    UNKNOWN_AFFECTED_RULE = "UNKNOWN_AFFECTED_RULE"
    AMENDMENT_TYPE_MISMATCH = "AMENDMENT_TYPE_MISMATCH"
    INVALID_EFFECTIVE_DATE = "INVALID_EFFECTIVE_DATE"
    DUPLICATE_RULE_ID = "DUPLICATE_RULE_ID"
    AMENDMENT_CONFLICT = "AMENDMENT_CONFLICT"
    START_DATE_MISMATCH = "START_DATE_MISMATCH"
    OUTSIDE_CONTRACT_VALIDITY = "OUTSIDE_CONTRACT_VALIDITY"
    PENDING_EXTENSION = "PENDING_EXTENSION"
    NO_ITEMS = "NO_ITEMS"
    NO_BASE_ITEM = "NO_BASE_ITEM"
    MULTIPLE_BASE_ITEMS = "MULTIPLE_BASE_ITEMS"
    AMBIGUOUS_ITEM = "AMBIGUOUS_ITEM"
    ITEMS_TOTAL_MISMATCH = "ITEMS_TOTAL_MISMATCH"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"


class Reason(StrictModel):
    """Motivo com mensagem em português e as regras envolvidas."""

    code: ReasonCode
    message: NonEmptyStr
    rule_ids: tuple[str, ...] = ()


class SelectionStatus(StrEnum):
    """Situação da seleção da regra vigente."""

    SELECTED = "SELECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_RULE = "NO_RULE"


class RuleSelection(StrictModel):
    """Regra vigente escolhida, ou nenhuma (com os motivos). Nunca escolhe em silêncio."""

    status: SelectionStatus
    rule: ContractRule | None = None
    reasons: tuple[Reason, ...] = ()

    @model_validator(mode="after")
    def _rule_only_when_selected(self) -> Self:
        if (self.status is SelectionStatus.SELECTED) != (self.rule is not None):
            raise ValueError("a regra só existe quando o status é SELECTED")
        return self


class TimelineResult(StrictModel):
    """Todas as versões das regras após os aditivos, mais os problemas encontrados."""

    rules: tuple[ContractRule, ...]
    issues: tuple[Reason, ...] = ()

    @property
    def complete(self) -> bool:
        """Falso se algum aditivo não pôde ser aplicado; nesse caso exige revisão humana."""
        return not self.issues


class ValidityStatus(StrEnum):
    """Situação da competência frente à vigência do contrato."""

    WITHIN = "WITHIN"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ContractValidityResult(StrictModel):
    """Fim efetivo do contrato (None = aberto) e o veredito sobre a competência."""

    status: ValidityStatus
    effective_end: date | None = None
    reasons: tuple[Reason, ...] = ()


class CheckOutcome(StrictModel):
    """Verificação com os números que a originaram: base esperada, diferença e percentual."""

    check: Check
    direction: DivergenceDirection | None = None
    base: Money | None = None
    difference: SignedMoney | None = None
    percentage: SignedMoney | None = None
