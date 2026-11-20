"""Tipos anotados reutilizados pelos esquemas: dinheiro, quantidade, datas e textos."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator, Field, StringConstraints

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def reject_float(value: object) -> object:
    """Recebe o valor bruto; devolve o mesmo valor ou levanta ValueError se for float."""
    if isinstance(value, float):
        raise ValueError("float nao e permitido para dinheiro: use Decimal ou str")
    return value


def require_plain_date(value: object) -> object:
    """Recebe o valor bruto; rejeita datetime e texto com hora (so data, sem hora)."""
    if isinstance(value, datetime):
        raise ValueError("use datetime.date, nao datetime")
    if isinstance(value, str) and _ISO_DATE.fullmatch(value) is None:
        raise ValueError("data deve estar no formato AAAA-MM-DD")
    return value


# Dinheiro: Decimal, nunca float, nunca negativo (style.md, secao 2).
Money = Annotated[Decimal, BeforeValidator(reject_float), Field(ge=0)]

# Preco unitario: precisao original (a NF-e admite ate 10 casas); so o total e arredondado.
UnitPrice = Annotated[
    Decimal, BeforeValidator(reject_float), Field(ge=0, max_digits=21, decimal_places=10)
]

# Diferenca monetaria: pode ser negativa (cobrado a menos).
SignedMoney = Annotated[Decimal, BeforeValidator(reject_float)]

# Quantidade (horas, unidades): Decimal nao negativo.
Quantity = Annotated[Decimal, BeforeValidator(reject_float), Field(ge=0)]

# Pontuacao das verificacoes por codigo, entre 0 e 1 (nao e autoavaliacao do modelo).
VerificationScore = Annotated[Decimal, BeforeValidator(reject_float), Field(ge=0, le=1)]

# Data de calendario (datetime.date); fim de vigencia e inclusivo.
PlainDate = Annotated[date, BeforeValidator(require_plain_date)]

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EntityId = NonEmptyStr
