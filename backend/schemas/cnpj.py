"""Validacao de CNPJ: formato e digitos verificadores."""

import re
from typing import Annotated

from pydantic import AfterValidator

_CNPJ_FORMAT = re.compile(r"[0-9]{14}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2}")
_FIRST_WEIGHTS = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_SECOND_WEIGHTS = (6, *_FIRST_WEIGHTS)


def normalize_cnpj(raw: str) -> str:
    """Recebe um CNPJ com ou sem pontuacao; devolve somente os digitos."""
    return re.sub(r"[^0-9]", "", raw)


def _check_digit(digits: str, weights: tuple[int, ...]) -> int:
    total = sum(int(digit) * weight for digit, weight in zip(digits, weights, strict=True))
    remainder = total % 11
    return 0 if remainder < 2 else 11 - remainder


def calculate_cnpj_check_digits(base: str) -> str:
    """Recebe os 12 primeiros digitos; devolve os dois digitos verificadores."""
    if re.fullmatch(r"[0-9]{12}", base) is None:
        raise ValueError("a base do CNPJ deve ter 12 digitos")
    first = _check_digit(base, _FIRST_WEIGHTS)
    second = _check_digit(base + str(first), _SECOND_WEIGHTS)
    return f"{first}{second}"


def is_valid_cnpj(raw: str) -> bool:
    """Confere formato (14 digitos, com ou sem pontuacao) e digitos verificadores."""
    if _CNPJ_FORMAT.fullmatch(raw) is None:
        return False
    digits = normalize_cnpj(raw)
    if len(set(digits)) == 1:
        return False
    return calculate_cnpj_check_digits(digits[:12]) == digits[12:]


def validate_cnpj(value: str) -> str:
    """Devolve o CNPJ normalizado (14 digitos) ou levanta ValueError se for invalido."""
    if not is_valid_cnpj(value):
        raise ValueError("CNPJ invalido: formato ou digitos verificadores incorretos")
    return normalize_cnpj(value)


# CNPJ guardado sempre normalizado, apenas com os 14 digitos.
Cnpj = Annotated[str, AfterValidator(validate_cnpj)]
