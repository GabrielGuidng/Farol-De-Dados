"""Leitura segura de documentos (PDF, DOCX, NF-e, NFS-e, planilhas) e mascaramento."""

from backend.leitura._parsing import UnsafeXmlError
from backend.leitura.docx_reader import DocxParagraph, DocxReadResult, read_docx
from backend.leitura.dossier import (
    ContractDossierMetadata,
    DossierExtractionResult,
    InvoiceDossierPatch,
    extract_dossier_artifacts,
    has_structured_financial_sections,
    has_supplemental_contract_rules,
)
from backend.leitura.masking import (
    CPF_MASK,
    RG_MASK,
    MaskingResult,
    mask_sensitive_data,
    mask_text,
)
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
    SpreadsheetKind,
    detect_spreadsheet_kind,
    import_certificates_csv,
    import_certificates_xlsx,
    import_invoices_csv,
    import_invoices_spreadsheet,
    import_invoices_xlsx,
    import_measurements_csv,
    import_measurements_spreadsheet,
    import_measurements_xlsx,
    import_payment_orders_csv,
    import_payment_orders_xlsx,
)

__all__ = [
    "CPF_MASK",
    "DEFAULT_MIN_TEXT_CHARS",
    "RG_MASK",
    "ContractDossierMetadata",
    "DocxParagraph",
    "DocxReadResult",
    "DossierExtractionResult",
    "InvoiceDossierPatch",
    "MaskingResult",
    "MeasurementRecord",
    "NfseLayoutDetector",
    "NfseLayoutParser",
    "PdfPage",
    "PdfReadResult",
    "SpreadsheetKind",
    "UnsafeXmlError",
    "detect_nfse_layout",
    "detect_spreadsheet_kind",
    "extract_dossier_artifacts",
    "has_structured_financial_sections",
    "has_supplemental_contract_rules",
    "import_certificates_csv",
    "import_certificates_xlsx",
    "import_invoices_csv",
    "import_invoices_spreadsheet",
    "import_invoices_xlsx",
    "import_measurements_csv",
    "import_measurements_spreadsheet",
    "import_measurements_xlsx",
    "import_payment_orders_csv",
    "import_payment_orders_xlsx",
    "list_nfse_layouts",
    "mask_sensitive_data",
    "mask_text",
    "parse_nfe",
    "parse_nfse",
    "read_docx",
    "read_pdf",
    "register_nfse_layout",
]
