"""Modelo de tempo: competencia (mes) e vigencia (intervalo de datas)."""

import calendar
import re
from datetime import date
from typing import Self

from pydantic import Field, model_serializer, model_validator

from backend.schemas.base import StrictModel
from backend.schemas.types import PlainDate

_PERIOD_FORMAT = re.compile(r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})")


class ReferencePeriod(StrictModel):
    """Competencia no formato AAAA-MM: um periodo (mes inteiro), nao um dia."""

    year: int = Field(ge=1, le=9999)
    month: int = Field(ge=1, le=12)

    @model_validator(mode="before")
    @classmethod
    def _parse_text(cls, data: object) -> object:
        if isinstance(data, str):
            match = _PERIOD_FORMAT.fullmatch(data)
            if match is None:
                raise ValueError("competencia deve estar no formato AAAA-MM")
            return {"year": int(match["year"]), "month": int(match["month"])}
        return data

    @model_serializer
    def _serialize(self) -> str:
        return str(self)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def first_day(self) -> date:
        """Primeiro dia do mes."""
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        """Ultimo dia do mes."""
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])


class ValidityPeriod(StrictModel):
    """Vigencia com datas de calendario; fim inclusivo; None significa em aberto/nao informado."""

    start: PlainDate | None = None
    end: PlainDate | None = None

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("data final da vigencia nao pode ser anterior a data inicial")
        return self
