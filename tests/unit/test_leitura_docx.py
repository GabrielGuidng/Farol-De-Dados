"""Testes do leitor de DOCX: ancoragem por cláusula e parágrafo / S-09 (Etapa 4.2)."""

import io
from pathlib import Path

import pytest
from docx import Document as DocxBuilder

from backend.leitura import read_docx
from backend.schemas import Evidence, ReadQuality


def _build_docx_bytes(populate) -> bytes:
    doc = DocxBuilder()
    populate(doc)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_s09_read_docx_anchors_clauses_and_paragraphs_without_page(tmp_path: Path) -> None:
    def fill(doc: DocxBuilder) -> None:
        doc.add_paragraph("Contrato de prestacao de servicos de suporte tecnico.")
        doc.add_heading("Cláusula 1 - Do Objeto", level=1)
        doc.add_paragraph("O objeto deste contrato e o suporte mensal continuo.")
        doc.add_paragraph("A franquia mensal e de 40 horas.")
        doc.add_paragraph("5.2")
        doc.add_paragraph("O valor mensal passa a ser de R$ 10.500,00.")

    docx_bytes = _build_docx_bytes(fill)
    docx_file = tmp_path / "aditivo.docx"
    docx_file.write_bytes(docx_bytes)

    from_bytes = read_docx(docx_bytes)
    from_path = read_docx(docx_file)

    assert from_bytes == from_path
    assert from_bytes.read_quality is ReadQuality.OK
    assert from_bytes.without_page is True
    assert from_bytes.warning is None
    assert len(from_bytes.paragraphs) == 4

    assert from_bytes.paragraphs[0].clause == "Preâmbulo"
    assert from_bytes.paragraphs[0].paragraph == "1"
    assert from_bytes.paragraphs[1].clause == "Cláusula 1 - Do Objeto"
    assert from_bytes.paragraphs[1].paragraph == "1"
    assert from_bytes.paragraphs[2].clause == "Cláusula 1 - Do Objeto"
    assert from_bytes.paragraphs[2].paragraph == "2"

    target = from_bytes.paragraphs[3]
    assert target.clause == "5.2"
    assert target.paragraph == "1"
    assert target.without_page is True

    evidence = target.to_evidence("doc-docx")
    assert isinstance(evidence, Evidence)
    assert evidence.page is None
    assert evidence.without_page is True
    assert evidence.clause == "5.2"
    assert evidence.paragraph == "1"
    assert evidence.literal_excerpt == "O valor mensal passa a ser de R$ 10.500,00."


def test_read_docx_empty_document_returns_no_text_with_warning() -> None:
    empty_bytes = _build_docx_bytes(lambda doc: doc.add_paragraph("   "))
    result = read_docx(empty_bytes)
    assert result.read_quality is ReadQuality.NO_TEXT
    assert result.without_page is True
    assert result.paragraphs == ()
    assert result.full_text == ""
    assert result.warning is not None


def test_read_docx_rejects_invalid_source_type() -> None:
    with pytest.raises(TypeError):
        read_docx("caminho/como/str")  # type: ignore[arg-type]
