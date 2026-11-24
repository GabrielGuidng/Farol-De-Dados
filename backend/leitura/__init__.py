"""Leitura segura de documentos (PDF, DOCX, NF-e, NFS-e, planilhas) e mascaramento."""

from backend.leitura.pdf import DEFAULT_MIN_TEXT_CHARS, PdfPage, PdfReadResult, read_pdf

__all__ = [
    "DEFAULT_MIN_TEXT_CHARS",
    "PdfPage",
    "PdfReadResult",
    "read_pdf",
]
