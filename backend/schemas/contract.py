"""Contrato e documento recebido."""

import re
from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from backend.schemas.base import StrictModel
from backend.schemas.cnpj import Cnpj
from backend.schemas.enums import ContractStatus, DocumentType, ReadQuality
from backend.schemas.periods import ValidityPeriod
from backend.schemas.types import EntityId, Money, NonEmptyStr

_SHA256_HEX = r"[0-9a-f]{64}"


class Contract(StrictModel):
    """Contrato auditado; a tolerancia de comparacao e parametro explicito de cada contrato."""

    id: EntityId
    company: NonEmptyStr
    supplier: NonEmptyStr
    supplier_cnpj: Cnpj
    legal_nature: str | None = None
    bank_code: str | None = None
    bank_agency: str | None = None
    bank_account: str | None = None
    subject: NonEmptyStr
    validity: ValidityPeriod
    comparison_tolerance: Money
    status: ContractStatus


class Document(StrictModel):
    """Arquivo recebido, com hash de integridade e qualidade de leitura."""

    id: EntityId
    contract_id: EntityId | None = None
    document_type: DocumentType
    file_name: NonEmptyStr
    received_at: datetime
    file_hash: str = Field(pattern=_SHA256_HEX)
    read_quality: ReadQuality
    reviewed: bool = False
    extracted_text: str = ""

    @model_validator(mode="after")
    def _text_matches_quality(self) -> Self:
        if self.read_quality is ReadQuality.OK and re.fullmatch(r"\s*", self.extracted_text):
            raise ValueError("leitura OK exige texto extraido; sem texto use NO_TEXT")
        return self
