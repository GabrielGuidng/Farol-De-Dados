"""Varredura por AST do código do motor contra float: M-16."""

import ast
from pathlib import Path

MOTOR_DIR = Path(__file__).resolve().parents[2] / "backend" / "motor"


def find_float_usage(source: str) -> list[str]:
    """Devolve as ocorrências de `float` (chamada, anotação, nome) e de literais decimais."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == "float":
            problems.append(f"linha {node.lineno}: uso de float")
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            problems.append(f"linha {node.lineno}: literal decimal {node.value!r}")
    return problems


def test_m16_scan_covers_the_motor_sources() -> None:
    files = sorted(MOTOR_DIR.glob("*.py"))
    assert MOTOR_DIR / "calculator.py" in files
    assert len(files) >= 2


def test_m16_motor_has_no_float() -> None:
    found = {
        path.name: problems
        for path in sorted(MOTOR_DIR.glob("*.py"))
        if (problems := find_float_usage(path.read_text(encoding="utf-8")))
    }
    assert found == {}


def test_m16_scanner_detects_float_call_annotation_and_literal() -> None:
    assert find_float_usage('x = float("1.5")')
    assert find_float_usage("def f(value: float) -> None:\n    pass")
    assert find_float_usage("def f() -> float:\n    return 1")
    assert find_float_usage("price = 0.1")
    assert not find_float_usage('from decimal import Decimal\nprice = Decimal("0.1")')
