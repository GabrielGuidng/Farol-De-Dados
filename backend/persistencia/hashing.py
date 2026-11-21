"""Cálculo de hash SHA-256 e verificação de integridade dos documentos recebidos."""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from backend.persistencia.repositories import save_document
from backend.schemas import Document, DocumentType, ReadQuality

_CHUNK_SIZE = 65536


def compute_sha256(source: bytes | Path) -> str:
    """Calcula o resumo criptográfico SHA-256 em hexadecimal a partir de bytes ou arquivo."""
    if isinstance(source, bytes):
        return hashlib.sha256(source).hexdigest()
    if isinstance(source, Path):
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()
    raise TypeError(f"esperado bytes ou Path, recebeu {type(source).__name__}")


def receive_document(
    *,
    document_id: str,
    document_type: DocumentType,
    file_name: str,
    content: bytes | Path,
    received_at: datetime,
    read_quality: ReadQuality = ReadQuality.OK,
    extracted_text: str = "Texto extraído.",
    contract_id: str | None = None,
    reviewed: bool = False,
    conn: sqlite3.Connection | None = None,
) -> Document:
    """Calcula o SHA-256 do conteúdo recebido, instancia o Document e o salva se houver conexão."""
    file_hash = compute_sha256(content)
    document = Document(
        id=document_id,
        contract_id=contract_id,
        document_type=document_type,
        file_name=file_name,
        received_at=received_at,
        file_hash=file_hash,
        read_quality=read_quality,
        reviewed=reviewed,
        extracted_text=extracted_text,
    )
    if conn is not None:
        save_document(conn, document)
    return document


def verify_document_integrity(document: Document, content: bytes | Path) -> bool:
    """Confere se o conteúdo informado mantém o mesmo SHA-256 gravado no documento."""
    return compute_sha256(content) == document.file_hash
