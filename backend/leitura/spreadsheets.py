"""Importador determinístico de cobranças e medições em CSV (';' e ',') e XLSX."""

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from backend.leitura._parsing import (
    parse_date_text,
    parse_decimal,
    strip_accents,
)
from backend.schemas import (
    BillingItem,
    Cnpj,
    Invoice,
    InvoiceSource,
    Quantity,
    ReferencePeriod,
    StrictModel,
)
from backend.schemas.types import EntityId, NonEmptyStr

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
        invoices.append(
            Invoice(
                id=f"sheet-{cnpj_digits}-{number}",
                contract_id=contract_id,
                supplier=supplier,
                supplier_cnpj=first["supplier_cnpj"],
                number=number,
                issue_date=issue_date,
                reference_period=reference_period,
                competence_inferred=competence_inferred,
                total=invoice_total,
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
