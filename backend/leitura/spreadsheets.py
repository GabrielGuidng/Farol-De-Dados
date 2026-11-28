"""Importador determinístico de cobranças e medições em CSV (';' e ',') e XLSX."""

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook

from backend.leitura._parsing import (
    parse_date_text,
    parse_decimal,
    strip_accents,
)
from backend.schemas import (
    BillingItem,
    CertificateType,
    Cnpj,
    Invoice,
    InvoiceSource,
    PaymentOrder,
    Quantity,
    ReferencePeriod,
    StrictModel,
    TaxCertificate,
    is_valid_cnpj,
    normalize_cnpj,
)
from backend.schemas.types import EntityId, NonEmptyStr

SpreadsheetKind = Literal["INVOICES", "MEASUREMENTS", "PAYMENT_ORDERS", "CERTIFICATES"]
_PROVISIONAL_SHEET_CNPJ = "00000000000191"

_INVOICE_COLUMN_ALIASES: dict[str, str] = {
    "numero": "number",
    "n_nota": "number",
    "nf": "number",
    "number": "number",
    "fornecedor": "supplier",
    "emitente": "supplier",
    "supplier": "supplier",
    "cnpj": "supplier_cnpj",
    "cnpj_fornecedor": "supplier_cnpj",
    "supplier_cnpj": "supplier_cnpj",
    "contrato": "contract_id",
    "id_contrato": "contract_id",
    "contract_id": "contract_id",
    "emissao": "issue_date",
    "data_emissao": "issue_date",
    "issue_date": "issue_date",
    "competencia": "reference_period",
    "reference_period": "reference_period",
    "total_nota": "total",
    "total": "total",
    "horas_medidas": "measured_hours",
    "medicao": "measured_hours",
    "measured_hours": "measured_hours",
    "descricao": "description",
    "item": "description",
    "description": "description",
    "quantidade": "quantity",
    "qtd": "quantity",
    "quantity": "quantity",
    "unidade": "unit",
    "un": "unit",
    "unit": "unit",
    "valor_unitario": "unit_value",
    "unitario": "unit_value",
    "unit_value": "unit_value",
    "valor_total": "total_value",
    "total_item": "total_value",
    "total_value": "total_value",
    "atesto": "attestation_date",
    "data_atesto": "attestation_date",
    "attestation_date": "attestation_date",
    "inss": "inss_withheld",
    "inss_ret": "inss_withheld",
    "inss_retido": "inss_withheld",
    "inss_withheld": "inss_withheld",
    "iss": "iss_withheld",
    "iss_ret": "iss_withheld",
    "iss_retido": "iss_withheld",
    "iss_withheld": "iss_withheld",
    "liquido": "net_total",
    "valor_liquido": "net_total",
    "liquido_nf": "net_total",
    "net_total": "net_total",
    "natureza_juridica": "supplier_legal_nature",
    "supplier_legal_nature": "supplier_legal_nature",
    "descricao_servico": "service_description",
    "discriminacao": "service_description",
    "service_description": "service_description",
}

_PAYMENT_ORDER_COLUMN_ALIASES: dict[str, str] = {
    "op": "op_number",
    "op_n": "op_number",
    "op_numero": "op_number",
    "ordem_pagamento": "op_number",
    "op_number": "op_number",
    "data_pgto": "payment_date",
    "data_pagamento": "payment_date",
    "payment_date": "payment_date",
    "nf": "invoice_number",
    "numero_nf": "invoice_number",
    "nota": "invoice_number",
    "invoice_number": "invoice_number",
    "beneficiario": "beneficiary_name",
    "favorecido": "beneficiary_name",
    "beneficiary_name": "beneficiary_name",
    "cnpj": "beneficiary_cnpj",
    "cnpj_favorecido": "beneficiary_cnpj",
    "beneficiary_cnpj": "beneficiary_cnpj",
    "banco": "bank_code",
    "bank_code": "bank_code",
    "agencia": "bank_agency",
    "ag": "bank_agency",
    "bank_agency": "bank_agency",
    "conta": "bank_account",
    "bank_account": "bank_account",
    "valor_pago": "paid_amount",
    "pago": "paid_amount",
    "paid_amount": "paid_amount",
    "solicitante": "requester_id",
    "requester_id": "requester_id",
    "aprovador": "approver_id",
    "approver_id": "approver_id",
    "cargo_aprovador": "approver_role",
    "approver_role": "approver_role",
    "alcada": "approver_limit",
    "approver_limit": "approver_limit",
    "contrato": "contract_id",
    "id_contrato": "contract_id",
    "contract_id": "contract_id",
}

_CERTIFICATE_COLUMN_ALIASES: dict[str, str] = {
    "certidao": "certificate_type",
    "tipo_certidao": "certificate_type",
    "tipo": "certificate_type",
    "certificate_type": "certificate_type",
    "data_emissao": "issue_date",
    "emissao": "issue_date",
    "issue_date": "issue_date",
    "validade": "expiry_date",
    "data_validade": "expiry_date",
    "vencimento": "expiry_date",
    "expiry_date": "expiry_date",
    "renovacao": "raw_status",
    "status": "raw_status",
    "situacao": "raw_status",
    "raw_status": "raw_status",
    "contrato": "contract_id",
    "id_contrato": "contract_id",
    "contract_id": "contract_id",
}

_MEASUREMENT_COLUMN_ALIASES: dict[str, str] = {
    "contrato": "contract_id",
    "id_contrato": "contract_id",
    "contract_id": "contract_id",
    "cnpj": "supplier_cnpj",
    "cnpj_fornecedor": "supplier_cnpj",
    "supplier_cnpj": "supplier_cnpj",
    "numero": "invoice_number",
    "numero_nota": "invoice_number",
    "invoice_number": "invoice_number",
    "competencia": "reference_period",
    "reference_period": "reference_period",
    "horas_medidas": "measured_hours",
    "horas": "measured_hours",
    "medicao": "measured_hours",
    "measured_hours": "measured_hours",
}


class MeasurementRecord(StrictModel):
    """Registro de medição de horas importado de planilha para conferência de excedentes."""

    contract_id: EntityId | None = None
    supplier_cnpj: Cnpj | None = None
    invoice_number: NonEmptyStr | None = None
    reference_period: ReferencePeriod
    measured_hours: Quantity


def _normalize_header(raw: str) -> str:
    cleaned = strip_accents(raw).strip()
    for sep in ("-", "/", ".", " "):
        cleaned = cleaned.replace(sep, "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def _read_text(source: str | bytes | Path) -> str:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8-sig")
    if isinstance(source, bytes):
        return source.decode("utf-8-sig")
    if isinstance(source, str):
        return source.lstrip("\ufeff")
    raise TypeError(f"esperado str, bytes ou Path, recebeu {type(source).__name__}")


def _detect_delimiter(header_line: str) -> str:
    return ";" if header_line.count(";") >= header_line.count(",") else ","


def _parse_csv_rows(
    source: str | bytes | Path, *, delimiter: str | None = None
) -> list[dict[str, str]]:
    text = _read_text(source)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    sep = delimiter if delimiter is not None else _detect_delimiter(lines[0])
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=sep)
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        normalized_row = {
            _normalize_header(key): (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if any(normalized_row.values()):
            rows.append(normalized_row)
    return rows


def _cell_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _parse_xlsx_rows(
    source: bytes | Path, *, sheet_name: str | None = None
) -> list[dict[str, str]]:
    target: Path | io.BytesIO
    if isinstance(source, Path):
        target = source
    elif isinstance(source, bytes):
        target = io.BytesIO(source)
    else:
        raise TypeError(f"esperado bytes ou Path, recebeu {type(source).__name__}")

    workbook = load_workbook(target, read_only=True, data_only=True)
    try:
        sheet = workbook[sheet_name] if sheet_name is not None else workbook.active
        if sheet is None:
            return []
        iterator = sheet.iter_rows(values_only=True)
        header_row = next(iterator, None)
        if header_row is None:
            return []
        headers = [_normalize_header(_cell_to_text(cell)) for cell in header_row]
        rows: list[dict[str, str]] = []
        for raw_values in iterator:
            row = {
                header: _cell_to_text(cell)
                for header, cell in zip(headers, raw_values, strict=False)
                if header
            }
            if any(row.values()):
                rows.append(row)
        return rows
    finally:
        workbook.close()


def _canonicalize_row(raw_row: dict[str, str], aliases: dict[str, str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for key, value in raw_row.items():
        canonical = aliases.get(key)
        if canonical is not None and value:
            mapped[canonical] = value
    return mapped


def _require(row: dict[str, str], key: str, row_number: int) -> str:
    value = row.get(key, "").strip()
    if not value:
        raise ValueError(f"linha {row_number} sem campo obrigatório: {key}")
    return value


def _build_invoices_from_rows(raw_rows: Sequence[dict[str, str]]) -> tuple[Invoice, ...]:
    grouped: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = {}
    for index, raw_row in enumerate(raw_rows, start=2):
        row = _canonicalize_row(raw_row, _INVOICE_COLUMN_ALIASES)
        number = _require(row, "number", index)
        cnpj_digits = "".join(ch for ch in _require(row, "supplier_cnpj", index) if ch.isdigit())
        group_key = (number.casefold(), cnpj_digits)
        grouped.setdefault(group_key, []).append((index, row))

    invoices: list[Invoice] = []
    for (_, cnpj_digits), entries in grouped.items():
        first_index, first = entries[0]
        number = first["number"]
        supplier = _require(first, "supplier", first_index)
        issue_date = parse_date_text(_require(first, "issue_date", first_index))
        period_raw = first.get("reference_period", "").strip()
        competence_inferred = not period_raw
        reference_period = (
            ReferencePeriod.model_validate(period_raw)
            if period_raw
            else ReferencePeriod(year=issue_date.year, month=issue_date.month)
        )
        contract_id = first.get("contract_id")
        measured_raw = first.get("measured_hours")
        measured_hours = (
            parse_decimal(measured_raw, "measured_hours") if measured_raw is not None else None
        )

        items: list[BillingItem] = []
        for row_index, row in entries:
            description = _require(row, "description", row_index)
            quantity = parse_decimal(_require(row, "quantity", row_index), "quantity")
            unit_value = parse_decimal(_require(row, "unit_value", row_index), "unit_value")
            total_value_raw = row.get("total_value")
            total_value = (
                parse_decimal(total_value_raw, "total_value")
                if total_value_raw is not None
                else quantity * unit_value
            )
            items.append(
                BillingItem(
                    description=description,
                    quantity=quantity,
                    unit=row.get("unit"),
                    unit_value=unit_value,
                    total_value=total_value,
                )
            )

        explicit_total = first.get("total")
        invoice_total = (
            parse_decimal(explicit_total, "total")
            if explicit_total is not None
            else sum((item.total_value for item in items), Decimal("0"))
        )
        raw_cnpj_value = first["supplier_cnpj"].strip()
        resolved_cnpj = (
            normalize_cnpj(raw_cnpj_value)
            if is_valid_cnpj(raw_cnpj_value)
            else _PROVISIONAL_SHEET_CNPJ
        )
        att_raw = first.get("attestation_date")
        inss_raw = first.get("inss_withheld")
        iss_raw = first.get("iss_withheld")
        net_raw = first.get("net_total")
        invoices.append(
            Invoice(
                id=f"sheet-{cnpj_digits or 'invalid'}-{number}",
                contract_id=contract_id,
                supplier=supplier,
                supplier_cnpj=resolved_cnpj,
                raw_supplier_cnpj=raw_cnpj_value or None,
                supplier_legal_nature=first.get("supplier_legal_nature"),
                number=number,
                issue_date=issue_date,
                attestation_date=parse_date_text(att_raw) if att_raw is not None else None,
                reference_period=reference_period,
                competence_inferred=competence_inferred,
                total=invoice_total,
                inss_withheld=(
                    parse_decimal(inss_raw, "inss_withheld") if inss_raw is not None else None
                ),
                iss_withheld=(
                    parse_decimal(iss_raw, "iss_withheld") if iss_raw is not None else None
                ),
                net_total=parse_decimal(net_raw, "net_total") if net_raw is not None else None,
                service_description=first.get("service_description") or first.get("description"),
                measured_hours=measured_hours,
                source=InvoiceSource.SPREADSHEET,
                items=tuple(items),
            )
        )
    return tuple(invoices)


def _build_measurements_from_rows(
    raw_rows: Sequence[dict[str, str]],
) -> tuple[MeasurementRecord, ...]:
    records: list[MeasurementRecord] = []
    for index, raw_row in enumerate(raw_rows, start=2):
        row = _canonicalize_row(raw_row, _MEASUREMENT_COLUMN_ALIASES)
        period = ReferencePeriod.model_validate(_require(row, "reference_period", index))
        hours = parse_decimal(_require(row, "measured_hours", index), "measured_hours")
        records.append(
            MeasurementRecord(
                contract_id=row.get("contract_id"),
                supplier_cnpj=row.get("supplier_cnpj"),
                invoice_number=row.get("invoice_number"),
                reference_period=period,
                measured_hours=hours,
            )
        )
    return tuple(records)


def import_invoices_csv(
    source: str | bytes | Path, *, delimiter: str | None = None
) -> tuple[Invoice, ...]:
    """Importa cobranças de CSV (';' ou ','), agrupando linhas por número e fornecedor."""
    return _build_invoices_from_rows(_parse_csv_rows(source, delimiter=delimiter))


def import_invoices_xlsx(
    source: bytes | Path, *, sheet_name: str | None = None
) -> tuple[Invoice, ...]:
    """Importa cobranças de planilha XLSX agrupando linhas por número e fornecedor."""
    return _build_invoices_from_rows(_parse_xlsx_rows(source, sheet_name=sheet_name))


def import_invoices_spreadsheet(source: Path) -> tuple[Invoice, ...]:
    """Despacha a importação de cobranças por extensão (`.csv` ou `.xlsx`)."""
    suffix = source.suffix.casefold()
    if suffix == ".csv":
        return import_invoices_csv(source)
    if suffix == ".xlsx":
        return import_invoices_xlsx(source)
    raise ValueError(f"formato de planilha não suportado: {source.suffix}")


def import_measurements_csv(
    source: str | bytes | Path, *, delimiter: str | None = None
) -> tuple[MeasurementRecord, ...]:
    """Importa medições de horas de um arquivo CSV (';' ou ',')."""
    return _build_measurements_from_rows(_parse_csv_rows(source, delimiter=delimiter))


def import_measurements_xlsx(
    source: bytes | Path, *, sheet_name: str | None = None
) -> tuple[MeasurementRecord, ...]:
    """Importa medições de horas de uma planilha XLSX."""
    return _build_measurements_from_rows(_parse_xlsx_rows(source, sheet_name=sheet_name))


def import_measurements_spreadsheet(source: Path) -> tuple[MeasurementRecord, ...]:
    """Despacha a importação de medições por extensão (`.csv` ou `.xlsx`)."""
    suffix = source.suffix.casefold()
    if suffix == ".csv":
        return import_measurements_csv(source)
    if suffix == ".xlsx":
        return import_measurements_xlsx(source)
    raise ValueError(f"formato de planilha não suportado: {source.suffix}")


def _map_certificate_type(raw: str) -> CertificateType:
    cleaned = strip_accents(raw)
    if "fgts" in cleaned or "crf" in cleaned:
        return CertificateType.CRF_FGTS
    if "cndt" in cleaned or "trabalhist" in cleaned:
        return CertificateType.CNDT
    if "municipal" in cleaned or "prefeitura" in cleaned:
        return CertificateType.CND_MUNICIPAL
    if "estadual" in cleaned or "sefaz" in cleaned:
        return CertificateType.CND_ESTADUAL
    return CertificateType.CND_FEDERAL


def _build_payment_orders_from_rows(
    raw_rows: Sequence[dict[str, str]],
    *,
    default_contract_id: str | None = None,
    source_document_id: str | None = None,
) -> tuple[PaymentOrder, ...]:
    orders: list[PaymentOrder] = []
    doc_prefix = source_document_id or "sheet"
    for index, raw_row in enumerate(raw_rows, start=2):
        row = _canonicalize_row(raw_row, _PAYMENT_ORDER_COLUMN_ALIASES)
        op_number = _require(row, "op_number", index)
        payment_date = parse_date_text(_require(row, "payment_date", index))
        invoice_number = _require(row, "invoice_number", index)
        beneficiary_name = _require(row, "beneficiary_name", index)
        paid_amount = parse_decimal(_require(row, "paid_amount", index), "paid_amount")
        raw_cnpj = row.get("beneficiary_cnpj", "").strip() or None
        valid_cnpj = normalize_cnpj(raw_cnpj) if raw_cnpj and is_valid_cnpj(raw_cnpj) else None
        limit_raw = row.get("approver_limit")
        approver_limit = (
            parse_decimal(limit_raw, "approver_limit") if limit_raw is not None else None
        )
        safe_op = "".join(ch if ch.isalnum() else "-" for ch in op_number).strip("-")
        orders.append(
            PaymentOrder(
                id=f"op-{doc_prefix}-{safe_op or index}",
                contract_id=row.get("contract_id") or default_contract_id,
                source_document_id=source_document_id,
                op_number=op_number,
                payment_date=payment_date,
                invoice_number=invoice_number,
                beneficiary_name=beneficiary_name,
                beneficiary_cnpj=valid_cnpj,
                raw_beneficiary_cnpj=raw_cnpj,
                bank_code=row.get("bank_code"),
                bank_agency=row.get("bank_agency"),
                bank_account=row.get("bank_account"),
                paid_amount=paid_amount,
                requester_id=row.get("requester_id"),
                approver_id=row.get("approver_id"),
                approver_role=row.get("approver_role"),
                approver_limit=approver_limit,
            )
        )
    return tuple(orders)


def _build_certificates_from_rows(
    raw_rows: Sequence[dict[str, str]],
    *,
    default_contract_id: str,
    source_document_id: str | None = None,
) -> tuple[TaxCertificate, ...]:
    certs: list[TaxCertificate] = []
    doc_prefix = source_document_id or "sheet"
    for index, raw_row in enumerate(raw_rows, start=2):
        row = _canonicalize_row(raw_row, _CERTIFICATE_COLUMN_ALIASES)
        cert_type = _map_certificate_type(_require(row, "certificate_type", index))
        issue_raw = row.get("issue_date")
        issue_date = parse_date_text(issue_raw) if issue_raw is not None else None
        expiry_date = parse_date_text(_require(row, "expiry_date", index))
        contract_id = row.get("contract_id") or default_contract_id
        certs.append(
            TaxCertificate(
                id=f"cert-{doc_prefix}-{cert_type.value.lower()}-{expiry_date.isoformat()}-{index}",
                contract_id=contract_id,
                source_document_id=source_document_id,
                certificate_type=cert_type,
                issue_date=issue_date,
                expiry_date=expiry_date,
                raw_status=row.get("raw_status"),
            )
        )
    return tuple(certs)


def detect_spreadsheet_kind(
    source: bytes | Path,
    *,
    is_xlsx: bool,
) -> SpreadsheetKind:
    """Identifica pelos cabeçalhos se a planilha contém NFs, OPs, certidões ou medições."""
    rows = _parse_xlsx_rows(source) if is_xlsx else _parse_csv_rows(source)
    if not rows:
        return "INVOICES"
    keys = set(rows[0].keys())
    if keys & {"op", "op_n", "op_numero", "ordem_pagamento", "op_number"}:
        return "PAYMENT_ORDERS"
    if keys & {"certidao", "tipo_certidao", "certificate_type", "validade", "expiry_date"}:
        return "CERTIFICATES"
    if "horas_medidas" in keys and not (keys & {"numero", "n_nota", "nf", "number", "total"}):
        return "MEASUREMENTS"
    return "INVOICES"


def import_payment_orders_csv(
    source: str | bytes | Path,
    *,
    delimiter: str | None = None,
    default_contract_id: str | None = None,
    source_document_id: str | None = None,
) -> tuple[PaymentOrder, ...]:
    """Importa ordens de pagamento a partir de arquivo CSV."""
    return _build_payment_orders_from_rows(
        _parse_csv_rows(source, delimiter=delimiter),
        default_contract_id=default_contract_id,
        source_document_id=source_document_id,
    )


def import_payment_orders_xlsx(
    source: bytes | Path,
    *,
    sheet_name: str | None = None,
    default_contract_id: str | None = None,
    source_document_id: str | None = None,
) -> tuple[PaymentOrder, ...]:
    """Importa ordens de pagamento a partir de planilha XLSX."""
    return _build_payment_orders_from_rows(
        _parse_xlsx_rows(source, sheet_name=sheet_name),
        default_contract_id=default_contract_id,
        source_document_id=source_document_id,
    )


def import_certificates_csv(
    source: str | bytes | Path,
    *,
    default_contract_id: str,
    delimiter: str | None = None,
    source_document_id: str | None = None,
) -> tuple[TaxCertificate, ...]:
    """Importa certidões de regularidade a partir de arquivo CSV."""
    return _build_certificates_from_rows(
        _parse_csv_rows(source, delimiter=delimiter),
        default_contract_id=default_contract_id,
        source_document_id=source_document_id,
    )


def import_certificates_xlsx(
    source: bytes | Path,
    *,
    default_contract_id: str,
    sheet_name: str | None = None,
    source_document_id: str | None = None,
) -> tuple[TaxCertificate, ...]:
    """Importa certidões de regularidade a partir de planilha XLSX."""
    return _build_certificates_from_rows(
        _parse_xlsx_rows(source, sheet_name=sheet_name),
        default_contract_id=default_contract_id,
        source_document_id=source_document_id,
    )
