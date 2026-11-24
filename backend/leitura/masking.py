"""Mascaramento de CPF e RG antes de qualquer envio de texto a serviço externo."""

import re

from pydantic import Field

from backend.schemas import StrictModel

CPF_MASK = "[CPF_MASCARADO]"
RG_MASK = "[RG_MASCARADO]"

_FORMATTED_CPF = re.compile(r"(?<!\d)[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}(?!\d)")
_LABELED_UNFORMATTED_CPF = re.compile(
    r"(?i)(\bCPF\s*(?:n[º°o.]*|numero|número)?\s*[:\-]?\s*)[0-9]{11}(?!\d)"
)
_LABELED_RG = re.compile(
    r"(?i)(\bR\.?\s*G\.?\s*(?:n[º°o.]*|numero|número)?\s*[:\-]?\s*)"
    r"(?:[A-Z]{2}[-\s]?)?(?:[0-9]{1,2}(?:\.[0-9]{3}){2}(?:-[0-9X])?|[0-9]{6,10}(?:-[0-9X])?)"
    r"(?![\d/])"
)
_FORMATTED_RG = re.compile(r"(?<!\d)[0-9]{1,2}\.[0-9]{3}\.[0-9]{3}-[0-9Xx](?![\d/])")


class MaskingResult(StrictModel):
    """Texto mascarado e contagens de CPFs e RGs substituídos."""

    text: str
    cpf_count: int = Field(ge=0)
    rg_count: int = Field(ge=0)


def mask_sensitive_data(text: str) -> MaskingResult:
    """Localiza CPF e RG por padrões de texto e os substitui por marcadores fixos."""
    masked, cpf_formatted = _FORMATTED_CPF.subn(CPF_MASK, text)
    masked, cpf_labeled = _LABELED_UNFORMATTED_CPF.subn(rf"\1{CPF_MASK}", masked)
    masked, rg_labeled = _LABELED_RG.subn(rf"\1{RG_MASK}", masked)
    masked, rg_formatted = _FORMATTED_RG.subn(RG_MASK, masked)
    return MaskingResult(
        text=masked,
        cpf_count=cpf_formatted + cpf_labeled,
        rg_count=rg_labeled + rg_formatted,
    )


def mask_text(text: str) -> str:
    """Devolve o texto com CPF e RG substituídos por marcadores."""
    return mask_sensitive_data(text).text
