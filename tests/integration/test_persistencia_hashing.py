"""Testes de integração I-05: cálculo e verificação de hash SHA-256 no recebimento."""

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from backend.persistencia import (
    compute_sha256,
    get_document,
    init_db,
    receive_document,
    verify_document_integrity,
)
from backend.schemas import DocumentType, ReadQuality


def test_i05_receive_from_bytes_saves_hash_and_detects_tampering() -> None:
    conn = init_db()
    original = b"Contrato de prestacao de servicos v1 - R$ 10.000,00"
    tampered = b"Contrato de prestacao de servicos v1 - R$ 10.800,00"

    doc = receive_document(
        document_id="doc-1",
        contract_id="CT-2026-001",
        document_type=DocumentType.CONTRACT,
        file_name="contrato.pdf",
        content=original,
        received_at=datetime(2026, 11, 20, 15, 0, 0),
        conn=conn,
    )
    assert doc.file_hash == hashlib.sha256(original).hexdigest()
    loaded = get_document(conn, "doc-1")
    assert loaded == doc
    assert verify_document_integrity(loaded, original) is True
    assert verify_document_integrity(loaded, tampered) is False
    assert compute_sha256(tampered) != doc.file_hash
    conn.close()


def test_i05_receive_from_file_handles_large_chunks_and_alteration(tmp_path: Path) -> None:
    file_path = tmp_path / "aditivo.pdf"
    payload = b"ABCD" * 25000  # > 64 KiB para exercitar leitura em blocos
    file_path.write_bytes(payload)

    doc = receive_document(
        document_id="doc-file",
        document_type=DocumentType.AMENDMENT,
        file_name=file_path.name,
        content=file_path,
        received_at=datetime(2026, 11, 20, 16, 0, 0),
        read_quality=ReadQuality.NO_TEXT,
        extracted_text="",
    )
    assert verify_document_integrity(doc, file_path) is True

    # Alterar um único byte do arquivo muda o hash e reprova a verificação.
    file_path.write_bytes(payload[:-1] + b"X")
    assert verify_document_integrity(doc, file_path) is False
    assert compute_sha256(file_path) != doc.file_hash


def test_compute_sha256_rejects_unsupported_source_type() -> None:
    with pytest.raises(TypeError):
        compute_sha256("caminho/como/str")  # type: ignore[arg-type]
