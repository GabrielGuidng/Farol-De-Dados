"""Leitor de DOCX: extrai parágrafos ancorados por cláusula e parágrafo (sem página)."""

import io
import re
from pathlib import Path

from docx import Document as load_docx_document

from backend.schemas import Evidence, ReadQuality, StrictModel
from backend.schemas.types import NonEmptyStr

_DEFAULT_CLAUSE = "Preâmbulo"
_NO_TEXT_WARNING = "Documento DOCX sem texto legível."

_CLAUSE_KEYWORD = re.compile(r"^(?i:cl[aá]usula)\b[^\n]*")
_NUMBERED_HEADING = re.compile(r"^[0-9]+(?:\.[0-9]+)*\.?(?:\s+[^\n.!?]{1,80})?$")


class DocxParagraph(StrictModel):
    """Parágrafo de DOCX ancorado por cláusula e parágrafo, marcado sem página."""

    clause: NonEmptyStr
    paragraph: NonEmptyStr
    text: NonEmptyStr
    without_page: bool = True

    def to_evidence(self, source_document_id: str, literal_excerpt: str | None = None) -> Evidence:
        """Constrói uma Evidence válida sem página (para DOCX sem conversão em PDF)."""
        return Evidence(
            source_document_id=source_document_id,
            page=None,
            clause=self.clause,
            paragraph=self.paragraph,
            literal_excerpt=self.text if literal_excerpt is None else literal_excerpt,
        )


class DocxReadResult(StrictModel):
    """Resultado da leitura de um DOCX, sempre sinalizado como sem página."""

    paragraphs: tuple[DocxParagraph, ...]
    read_quality: ReadQuality
    without_page: bool = True
    full_text: str
    warning: str | None = None


def _is_clause_header(text: str, style_name: str) -> bool:
    normalized_style = style_name.casefold()
    if normalized_style.startswith(("heading", "título", "titulo")):
        return True
    if _CLAUSE_KEYWORD.fullmatch(text) and len(text) <= 100 and not text.endswith("."):
        return True
    if _NUMBERED_HEADING.fullmatch(text) and not text.endswith("."):
        return True
    return False


def read_docx(source: Path | bytes) -> DocxReadResult:
    """Lê DOCX preservando cláusula e parágrafo, sinalizando `without_page=True`."""
    target: Path | io.BytesIO
    if isinstance(source, Path):
        target = source
    elif isinstance(source, bytes):
        target = io.BytesIO(source)
    else:
        raise TypeError(f"esperado Path ou bytes, recebeu {type(source).__name__}")

    doc = load_docx_document(target)
    current_clause = _DEFAULT_CLAUSE
    paragraph_index = 0
    items: list[DocxParagraph] = []

    for raw_paragraph in doc.paragraphs:
        text = " ".join(raw_paragraph.text.split())
        if not text:
            continue
        style_name = "" if raw_paragraph.style is None else (raw_paragraph.style.name or "")
        if _is_clause_header(text, style_name):
            current_clause = text
            paragraph_index = 0
            continue
        paragraph_index += 1
        items.append(
            DocxParagraph(
                clause=current_clause,
                paragraph=str(paragraph_index),
                text=text,
                without_page=True,
            )
        )

    if not items:
        return DocxReadResult(
            paragraphs=(),
            read_quality=ReadQuality.NO_TEXT,
            without_page=True,
            full_text="",
            warning=_NO_TEXT_WARNING,
        )

    full_text = "\n\n".join(item.text for item in items)
    return DocxReadResult(
        paragraphs=tuple(items),
        read_quality=ReadQuality.OK,
        without_page=True,
        full_text=full_text,
        warning=None,
    )
