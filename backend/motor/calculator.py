"""Calculadora monetária: tudo em Decimal; o arredondamento é aplicado uma vez, no resultado."""

from decimal import ROUND_HALF_UP, Decimal, localcontext

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")
_DIVISION_PRECISION = 60


def require_decimal(value: object, name: str) -> Decimal:
    """Recebe um valor bruto; devolve Decimal (aceita int). Levanta TypeError para float e afins."""
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise TypeError(f"{name} deve ser Decimal, recebeu {type(value).__name__}")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError(f"{name} deve ser um número finito")
    return number


def round_money(value: Decimal) -> Decimal:
    """Arredonda para 2 casas com ROUND_HALF_UP. É a única função de arredondamento do motor."""
    return require_decimal(value, "value").quantize(_CENT, rounding=ROUND_HALF_UP)


def add_money(*values: Decimal) -> Decimal:
    """Soma os valores sem arredondar as parcelas; arredonda apenas o total."""
    total = Decimal("0")
    for index, value in enumerate(values):
        total += require_decimal(value, f"values[{index}]")
    return round_money(total)


def subtract_money(minuend: Decimal, subtrahend: Decimal) -> Decimal:
    """Diferença com sinal (minuendo − subtraendo); arredonda apenas o resultado."""
    return round_money(
        require_decimal(minuend, "minuend") - require_decimal(subtrahend, "subtrahend")
    )


def multiply_money(quantity: Decimal, unit_price: Decimal) -> Decimal:
    """Horas × preço: multiplica com a precisão original e arredonda só o total."""
    return round_money(
        require_decimal(quantity, "quantity") * require_decimal(unit_price, "unit_price")
    )


def percentage(part: Decimal, whole: Decimal) -> Decimal:
    """Percentual de `part` sobre `whole`, com 2 casas (só para exibição); `whole` zero é erro."""
    numerator = require_decimal(part, "part")
    denominator = require_decimal(whole, "whole")
    if denominator == 0:
        raise ValueError("whole não pode ser zero")
    with localcontext() as context:
        context.prec = _DIVISION_PRECISION
        return round_money(numerator * _HUNDRED / denominator)
