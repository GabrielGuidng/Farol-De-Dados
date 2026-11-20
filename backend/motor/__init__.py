"""Motor determinístico: vigência, cálculo e verificações, sem IA."""

from backend.motor.calculator import (
    add_money,
    multiply_money,
    percentage,
    require_decimal,
    round_money,
    subtract_money,
)
from backend.motor.results import Reason, ReasonCode, TimelineResult
from backend.motor.timeline import apply_amendments

__all__ = [
    "Reason",
    "ReasonCode",
    "TimelineResult",
    "add_money",
    "apply_amendments",
    "multiply_money",
    "percentage",
    "require_decimal",
    "round_money",
    "subtract_money",
]
