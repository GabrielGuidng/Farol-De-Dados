"""Testes de CNPJ: S-02."""

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.schemas import Cnpj, is_valid_cnpj, normalize_cnpj
from backend.schemas.cnpj import calculate_cnpj_check_digits

cnpj = TypeAdapter(Cnpj)


def test_s02_valid_cnpj_passes_and_is_normalized() -> None:
    assert cnpj.validate_python("11.222.333/0001-81") == "11222333000181"
    assert cnpj.validate_python("11222333000181") == "11222333000181"


@pytest.mark.parametrize(
    "malformed",
    ["", "123", "11.222.333/0001-8", "11-222-333/0001-81", "11222333000181X", "abcdefghijklmn"],
)
def test_s02_malformed_cnpj_fails(malformed: str) -> None:
    with pytest.raises(ValidationError):
        cnpj.validate_python(malformed)


@pytest.mark.parametrize("wrong_digit", ["11.222.333/0001-82", "11222333000191", "00000000000000"])
def test_s02_cnpj_with_wrong_check_digit_fails(wrong_digit: str) -> None:
    with pytest.raises(ValidationError):
        cnpj.validate_python(wrong_digit)


def test_cnpj_rejects_non_string() -> None:
    with pytest.raises(ValidationError):
        cnpj.validate_python(11222333000181)


def test_pure_functions() -> None:
    assert normalize_cnpj("11.222.333/0001-81") == "11222333000181"
    assert calculate_cnpj_check_digits("112223330001") == "81"
    assert is_valid_cnpj("11.444.777/0001-61")
    assert not is_valid_cnpj("11.444.777/0001-62")
