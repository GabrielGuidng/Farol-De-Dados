"""Leitura segura de documentos (PDF, DOCX, NF-e, NFS-e, planilhas) e mascaramento."""

from backend.leitura._parsing import UnsafeXmlError
from backend.leitura.docx_reader import DocxParagraph, DocxReadResult, read_docx
from backend.leitura.nfe import parse_nfe
from backend.leitura.nfse import (
    NfseLayoutDetector,
    NfseLayoutParser,
    detect_nfse_layout,
    list_nfse_layouts,
    parse_nfse,
    register_nfse_layout,
)
from backend.leitura.pdf import DEFAULT_MIN_TEXT_CHARS, PdfPage, PdfReadResult, read_pdf
from backend.leitura.spreadsheets import (
    MeasurementRecord,
    import_invoices_csv,
    import_invoices_spreadsheet,
    import_invoices_xlsx,
    import_measurements_csv,
    import_measurements_spreadsheet,
    import_measurements_xlsx,
)

__all__ = [
    "DEFAULT_MIN_TEXT_CHARS",
    "DocxParagraph",
    "DocxReadResult",
    "MeasurementRecord",
    "NfseLayoutDetector",
    "NfseLayoutParser",
    "PdfPage",
    "PdfReadResult",
    "UnsafeXmlError",
    "detect_nfse_layout",
    "import_invoices_csv",
    "import_invoices_spreadsheet",
    "import_invoices_xlsx",
    "import_measurements_csv",
    "import_measurements_spreadsheet",
    "import_measurements_xlsx",
    "list_nfse_layouts",
    "parse_nfe",
    "parse_nfse",
    "read_docx",
    "read_pdf",
    "register_nfse_layout",
]
