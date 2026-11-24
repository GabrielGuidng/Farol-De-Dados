"""Leitor de PDF por página: preserva numeração e sinaliza PDF sem camada de texto."""

import io
from pathlib import Path

import pdfplumber
from pydantic import Field

from backend.schemas import ReadQuality, StrictModel

DEFAULT_MIN_TEXT_CHARS = 20

_NO_TEXT_WARNING = (
    "PDF sem camada de texto legível (provavelmente escaneado); OCR é etapa posterior "
    "e o documento não foi processado."
)


class PdfPage(StrictModel):
    """Texto extraído de uma única página do PDF."""

    page_number: int = Field(ge=1)
    text: str


class PdfReadResult(StrictModel):
    """Resultado da leitura de PDF, com texto paginado e qualidade de leitura."""

    pages: tuple[PdfPage, ...]
    read_quality: ReadQuality
    full_text: str
    warning: str | None = None


def read_pdf(
    source: Path | bytes, *, min_text_chars: int = DEFAULT_MIN_TEXT_CHARS
) -> PdfReadResult:
    """Lê PDF página a página preservando a numeração; PDF sem texto recebe NO_TEXT e aviso."""
    if min_text_chars < 1:
        raise ValueError("min_text_chars deve ser maior ou igual a 1")
    target: Path | io.BytesIO
    if isinstance(source, Path):
        target = source
    elif isinstance(source, bytes):
        target = io.BytesIO(source)
    else:
        raise TypeError(f"esperado Path ou bytes, recebeu {type(source).__name__}")

    extracted_pages: list[PdfPage] = []
    with pdfplumber.open(target) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            extracted_pages.append(PdfPage(page_number=index, text=text))

    non_whitespace_chars = sum(len("".join(page.text.split())) for page in extracted_pages)
    if not extracted_pages or non_whitespace_chars < min_text_chars:
        return PdfReadResult(
            pages=tuple(extracted_pages),
            read_quality=ReadQuality.NO_TEXT,
            full_text="",
            warning=_NO_TEXT_WARNING,
        )

    full_text = "\n\n".join(page.text for page in extracted_pages if page.text)
    return PdfReadResult(
        pages=tuple(extracted_pages),
        read_quality=ReadQuality.OK,
        full_text=full_text,
        warning=None,
    )
