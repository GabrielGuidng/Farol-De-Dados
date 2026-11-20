"""Modelo base estrito de todos os contratos de dados do projeto."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Campo desconhecido e erro (extra="forbid"); instancias sao imutaveis (frozen=True)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
