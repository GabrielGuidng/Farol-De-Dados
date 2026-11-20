"""Motor determinístico: vigência, cálculo e verificações, sem IA."""

from backend.motor.calculator import (
    add_money,
    multiply_money,
    percentage,
    require_decimal,
    round_money,
    subtract_money,
)
from backend.motor.results import (
    ContractValidityResult,
    Reason,
    ReasonCode,
    RuleSelection,
    SelectionStatus,
    TimelineResult,
    ValidityStatus,
)
from backend.motor.timeline import apply_amendments, select_applicable_rule
from backend.motor.validity import check_contract_validity, effective_contract_end

__all__ = [
    "ContractValidityResult",
    "Reason",
    "ReasonCode",
    "RuleSelection",
    "SelectionStatus",
    "TimelineResult",
    "ValidityStatus",
    "add_money",
    "apply_amendments",
    "check_contract_validity",
    "effective_contract_end",
    "multiply_money",
    "percentage",
    "require_decimal",
    "round_money",
    "select_applicable_rule",
    "subtract_money",
]
