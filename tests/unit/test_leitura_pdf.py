"""Testes do leitor de PDF por página: cenário A-07 (Etapa 4.1)."""

from pathlib import Path

import pytest

from backend.leitura import read_pdf
from backend.schemas import ReadQuality


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_minimal_pdf(pages_text: list[str]) -> bytes:
    """Constrói um PDF 1.4 válido mínimo em memória com uma página por item de `pages_text`."""
    objects: list[bytes] = []
    # Objeto 1: Catalog; Objeto 2: Pages (preenchido depois); Objeto 3: Fonte Type1
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"")  # placeholder para Pages
    objects.append(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    page_refs: list[str] = []
    next_id = 4
    for text in pages_text:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_refs.append(f"{page_id} 0 R")

        stream_body = (
            f"BT /F1 12 Tf 72 720 Td ({_escape_pdf_text(text)}) Tj ET".encode("latin-1")
            if text
            else b""
        )
        objects.append(
            f"{page_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>\n"
            f"endobj\n".encode("ascii")
        )
        objects.append(
            f"{content_id} 0 obj\n<< /Length {len(stream_body)} >>\nstream\n".encode("ascii")
            + stream_body
            + b"\nendstream\nendobj\n"
        )

    objects[1] = (
        f"2 0 obj\n<< /Type /Pages /Kids [{' '.join(page_refs)}] "
        f"/Count {len(page_refs)} >>\nendobj\n".encode("ascii")
    )

    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for obj in objects:
        offsets.append(len(buf))
        buf.extend(obj)

    xref_offset = len(buf)
    buf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(buf)


def test_read_pdf_preserves_page_numbers_from_bytes_and_path(tmp_path: Path) -> None:
    pdf_bytes = build_minimal_pdf(
        [
            "Clausula 1: O valor mensal do contrato e R$ 10.000,00.",
            "Aditivo 02: O valor mensal passa para R$ 10.500,00.",
        ]
    )
    pdf_file = tmp_path / "contrato.pdf"
    pdf_file.write_bytes(pdf_bytes)

    from_bytes = read_pdf(pdf_bytes)
    from_path = read_pdf(pdf_file)

    assert from_bytes == from_path
    assert from_bytes.read_quality is ReadQuality.OK
    assert from_bytes.warning is None
    assert len(from_bytes.pages) == 2
    assert from_bytes.pages[0].page_number == 1
    assert "10.000,00" in from_bytes.pages[0].text
    assert from_bytes.pages[1].page_number == 2
    assert "10.500,00" in from_bytes.pages[1].text
    assert "10.500,00" in from_bytes.full_text


def test_a07_pdf_without_text_layer_is_flagged_no_text_and_not_processed(tmp_path: Path) -> None:
    scanned_like = build_minimal_pdf(["", "   "])
    pdf_file = tmp_path / "escaneado.pdf"
    pdf_file.write_bytes(scanned_like)

    result = read_pdf(pdf_file)
    assert result.read_quality is ReadQuality.NO_TEXT
    assert result.full_text == ""
    assert result.warning is not None
    assert "sem camada de texto" in result.warning.lower()
    assert len(result.pages) == 2


def test_a07_pdf_with_very_little_text_below_threshold_is_flagged_no_text() -> None:
    tiny_noise = build_minimal_pdf(["p.1"])
    result = read_pdf(tiny_noise)
    assert result.read_quality is ReadQuality.NO_TEXT
    assert result.full_text == ""

    custom = read_pdf(tiny_noise, min_text_chars=2)
    assert custom.read_quality is ReadQuality.OK
    assert "p.1" in custom.full_text


def test_read_pdf_rejects_invalid_source_type() -> None:
    with pytest.raises(TypeError):
        read_pdf("caminho/como/str")  # type: ignore[arg-type]
