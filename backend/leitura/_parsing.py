"""Utilitários internos compartilhados pelos leitores de XML e planilhas."""

import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from defusedxml import ElementTree as DefusedET
from defusedxml.common import (
    DTDForbidden,
    EntitiesForbidden,
    ExternalReferenceForbidden,
)

from backend.schemas import ReferencePeriod

_ISO_DATE_PREFIX = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})")
_BR_DATE = re.compile(r"^(?P<day>[0-9]{2})/(?P<month>[0-9]{2})/(?P<year>[0-9]{4})$")
_ISO_PERIOD = re.compile(r"(?<!\d)(?P<year>(?:19|20)[0-9]{2})-(?P<month>0[1-9]|1[0-2])(?![-\d])")
_BR_PERIOD = re.compile(r"(?<!\d)(?P<month>0[1-9]|1[0-2])/(?P<year>(?:19|20)[0-9]{2})(?![/\d])")
_TEXT_PERIOD = re.compile(r"\b(?P<month>[a-z]{3,9})\s*(?:/|de)\s*(?P<year>(?:19|20)[0-9]{2})\b")
_CONTRACT_CODE = re.compile(r"\b(CT-[0-9]{4}-[0-9]+[A-Za-z0-9-]*)\b")
_CONTRACT_LABEL = re.compile(
    r"(?i)\bcontrato\s*(?:n[º°o.]*|numero|número)?\s*[:\-]\s*"
    r"([A-Za-z0-9][A-Za-z0-9._/-]{1,})"
)

_MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "janeiro": 1,
    "fev": 2,
    "fevereiro": 2,
    "mar": 3,
    "marco": 3,
    "abr": 4,
    "abril": 4,
    "mai": 5,
    "maio": 5,
    "jun": 6,
    "junho": 6,
    "jul": 7,
    "julho": 7,
    "ago": 8,
    "agosto": 8,
    "set": 9,
    "setembro": 9,
    "out": 10,
    "outubro": 10,
    "nov": 11,
    "novembro": 11,
    "dez": 12,
    "dezembro": 12,
}


class UnsafeXmlError(ValueError):
    """XML recusado por conter DOCTYPE, ENTITY ou referência externa."""


def strip_accents(text: str) -> str:
    """Remove acentos e converte para minúsculas para comparação flexível de texto."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def safe_parse_xml(source: str | bytes | Path) -> ET.Element:
    """Lê XML com `defusedxml` recusando DOCTYPE, ENTITY e referências externas."""
    payload: str | bytes
    if isinstance(source, Path):
        payload = source.read_bytes()
    elif isinstance(source, str | bytes):
        payload = source
    else:
        raise TypeError(f"esperado str, bytes ou Path, recebeu {type(source).__name__}")

    try:
        root = DefusedET.fromstring(
            payload,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise UnsafeXmlError(
            "XML recusado por conter DOCTYPE, ENTITY ou referência externa."
        ) from exc
    except ET.ParseError as exc:
        raise ValueError(f"XML inválido: {exc}") from exc

    if not isinstance(root, ET.Element):
        raise ValueError("raiz XML inválida")
    return root


def local_name(tag: str) -> str:
    """Devolve o nome local da tag XML sem o prefixo de namespace."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def find_element(element: ET.Element, *tag_path: str) -> ET.Element | None:
    """Localiza o primeiro descendente seguindo o caminho de tags (ignora namespaces)."""
    if not tag_path:
        return element
    head, *tail = tag_path
    for node in element.iter():
        if node is element:
            continue
        if local_name(node.tag) == head:
            found = find_element(node, *tail)
            if found is not None:
                return found
    return None


def find_all_elements(element: ET.Element, tag_name: str) -> list[ET.Element]:
    """Lista todos os descendentes com o nome local informado (ignora namespaces)."""
    return [
        node for node in element.iter() if node is not element and local_name(node.tag) == tag_name
    ]


def find_text(element: ET.Element, *tag_path: str) -> str | None:
    """Devolve o texto limpo do primeiro elemento no caminho, ou None se ausente/vazio."""
    found = find_element(element, *tag_path)
    if found is None or found.text is None:
        return None
    stripped = found.text.strip()
    return stripped or None


def parse_decimal(raw: str | int | Decimal, field_name: str = "value") -> Decimal:
    """Converte texto (formato padrão ou brasileiro) ou inteiro diretamente para Decimal."""
    if isinstance(raw, bool) or isinstance(raw, float):
        raise TypeError(f"{field_name} não aceita {type(raw).__name__}: use str ou Decimal")
    if isinstance(raw, Decimal):
        if not raw.is_finite():
            raise ValueError(f"{field_name} deve ser finito")
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if not isinstance(raw, str):
        raise TypeError(f"{field_name} não aceita {type(raw).__name__}")

    cleaned = raw.strip()
    if cleaned.casefold().startswith("r$"):
        cleaned = cleaned[2:].strip()
    cleaned = cleaned.replace(" ", "")
    if not cleaned:
        raise ValueError(f"{field_name} está vazio")

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} inválido para Decimal: {raw!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{field_name} deve ser finito")
    return number


def parse_date_text(raw: str) -> date:
    """Converte data ISO (`AAAA-MM-DD`, timestamp ISO) ou brasileira (`DD/MM/AAAA`) para `date`."""
    text = raw.strip()
    iso_match = _ISO_DATE_PREFIX.match(text)
    if iso_match is not None:
        return date(int(iso_match["year"]), int(iso_match["month"]), int(iso_match["day"]))
    br_match = _BR_DATE.fullmatch(text)
    if br_match is not None:
        return date(int(br_match["year"]), int(br_match["month"]), int(br_match["day"]))
    raise ValueError(f"data inválida: {raw!r}")


def extract_competence(text: str) -> ReferencePeriod | None:
    """Extrai competência (`AAAA-MM`, `MM/AAAA` ou `mês/AAAA` em português) de texto livre."""
    if not text.strip():
        return None
    iso_match = _ISO_PERIOD.search(text)
    if iso_match is not None:
        return ReferencePeriod.model_validate(f"{iso_match['year']}-{iso_match['month']}")
    br_match = _BR_PERIOD.search(text)
    if br_match is not None:
        return ReferencePeriod.model_validate(f"{br_match['year']}-{br_match['month']}")
    normalized = strip_accents(text)
    for text_match in _TEXT_PERIOD.finditer(normalized):
        month_num = _MONTH_NAMES.get(text_match["month"])
        if month_num is not None:
            return ReferencePeriod.model_validate(f"{text_match['year']}-{month_num:02d}")
    return None


def extract_contract_id(text: str) -> str | None:
    """Extrai o código do contrato de observações/informações adicionais, se presente."""
    if not text.strip():
        return None
    code_match = _CONTRACT_CODE.search(text)
    if code_match is not None:
        return code_match.group(1)
    label_match = _CONTRACT_LABEL.search(text)
    if label_match is not None:
        candidate = label_match.group(1).rstrip(".,;)")
        if candidate:
            return candidate
    return None
