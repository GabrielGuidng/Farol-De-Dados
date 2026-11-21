"""Motor determinístico: vigência, cálculo e verificações, sem IA."""

from backend.motor.calculator import (
    add_money,
    multiply_money,
    percentage,
    require_decimal,
    round_money,
    subtract_money,
)
from backend.motor.checks import (
    check_monthly_price,
    check_overage,
    check_overage_price,
    check_overage_quantity,
)
from backend.motor.items import (
    ItemClassification,
    check_item_classification,
    check_items_total,
    classify_items,
)
from backend.motor.results import (
    CheckOutcome,
    ContractValidityResult,
    DivergenceDirection,
    Reason,
    ReasonCode,
    RuleSelection,
    SelectionStatus,
    TimelineResult,
    ValidityStatus,
)
from backend.motor.timeline import (
    apply_amendments,
    select_applicable_rule,
)
from backend.motor.validity import (
    check_contract_validity,
    effective_contract_end,
)

__all__ = [
    "CheckOutcome",
    "ContractValidityResult",
    "DivergenceDirection",
    "ItemClassification",
    "Reason",
    "ReasonCode",
    "RuleSelection",
    "SelectionStatus",
    "TimelineResult",
    "ValidityStatus",
    "add_money",
    "apply_amendments",
    "check_contract_validity",
    "check_item_classification",
    "check_items_total",
    "check_monthly_price",
    "check_overage",
    "check_overage_price",
    "check_overage_quantity",
    "classify_items",
    "effective_contract_end",
    "multiply_money",
    "percentage",
    "require_decimal",
    "round_money",
    "select_applicable_rule",
    "subtract_money",
]
