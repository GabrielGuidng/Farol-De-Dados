"""Testes do importador de CSV e planilhas XLSX (Etapa 4.5)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend.leitura import (
    import_invoices_csv,
    import_invoices_spreadsheet,
    import_invoices_xlsx,
    import_measurements_csv,
    import_measurements_spreadsheet,
    import_measurements_xlsx,
)
from backend.leitura._parsing import parse_decimal
from backend.schemas import InvoiceSource, ReferencePeriod


def test_parse_decimal_brazilian_format_and_float_rejection() -> None:
    assert parse_decimal("10.800,00") == Decimal("10800.00")
    assert parse_decimal("R$ 1.250,50") == Decimal("1250.50")
    assert parse_decimal("150,1234567890") == Decimal("150.1234567890")
    with pytest.raises(TypeError):
        parse_decimal(10.8)  # type: ignore[arg-type]


def test_import_invoices_csv_semicolon_groups_items_and_parses_brazilian_decimals() -> None:
    csv_text = (
        "\ufeffnumero;fornecedor;cnpj;contrato;emissao;competencia;total_nota;horas_medidas;"
        "descricao;quantidade;unidade;valor_unitario;valor_total\n"
        "103;Fornecedor Exemplo;11.222.333/0001-81;CT-2026-001;01/08/2026;2026-07;"
        "12.000,00;46;Mensalidade de suporte;1;mês;10.800,00;10.800,00\n"
        "103;Fornecedor Exemplo;11222333000181;CT-2026-001;01/08/2026;2026-07;"
        "12.000,00;46;Horas excedentes;8;hora;150,00;1.200,00\n"
        "104;Fornecedor Exemplo;11.222.333/0001-81;CT-2026-001;05/11/2026;;"
        "10.800,00;;Mensalidade de suporte;1;mês;10.800,00;10.800,00\n"
    )
    invoices = import_invoices_csv(csv_text)
    assert len(invoices) == 2

    inv103, inv104 = invoices
    assert inv103.source is InvoiceSource.SPREADSHEET
    assert inv103.number == "103"
    assert inv103.total == Decimal("12000.00")
    assert inv103.measured_hours == Decimal("46")
    assert inv103.competence_inferred is False
    assert len(inv103.items) == 2
    assert inv103.items[0].unit_value == Decimal("10800.00")
    assert inv103.items[1].total_value == Decimal("1200.00")

    assert inv104.reference_period == ReferencePeriod(year=2026, month=11)
    assert inv104.competence_inferred is True


def test_import_invoices_csv_comma_delimiter_and_path(tmp_path: Path) -> None:
    csv_file = tmp_path / "cobrancas.csv"
    csv_file.write_text(
        "numero,fornecedor,cnpj,emissao,competencia,"
        "descricao,quantidade,valor_unitario,valor_total\n"
        "101,Fornecedor Exemplo,11222333000181,2026-05-02,"
        "2026-04,Mensalidade,1,10000.00,10000.00\n",
        encoding="utf-8",
    )
    invoices = import_invoices_spreadsheet(csv_file)
    assert len(invoices) == 1
    assert invoices[0].total == Decimal("10000.00")
    assert invoices[0].issue_date == date(2026, 5, 2)


def test_import_invoices_and_measurements_xlsx(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Faturas"
    ws.append(
        [
            "numero",
            "fornecedor",
            "cnpj",
            "contrato",
            "emissao",
            "competencia",
            "descricao",
            "quantidade",
            "valor_unitario",
            "valor_total",
        ]
    )
    ws.append(
        [
            "200",
            "Fornecedor XLSX",
            "11.222.333/0001-81",
            "CT-2026-001",
            date(2026, 10, 31),
            "2026-10",
            "Mensalidade",
            1,
            "10.500,00",
            "10.500,00",
        ]
    )
    ws.append(
        [
            "200",
            "Fornecedor XLSX",
            "11222333000181",
            "CT-2026-001",
            date(2026, 10, 31),
            "2026-10",
            "Horas excedentes",
            2,
            150.25,
            300.50,
        ]
    )
    xlsx_path = tmp_path / "lote.xlsx"
    wb.save(xlsx_path)

    invoices = import_invoices_spreadsheet(xlsx_path)
    assert len(invoices) == 1
    assert invoices[0].total == Decimal("10800.50")
    assert len(invoices[0].items) == 2
    assert invoices[0].items[1].unit_value == Decimal("150.25")

    m_wb = Workbook()
    m_ws = m_wb.active
    assert m_ws is not None
    m_ws.append(["contrato", "cnpj", "numero", "competencia", "horas_medidas"])
    m_ws.append(["CT-2026-001", "11.222.333/0001-81", "103", "2026-07", "46,5"])
    m_xlsx = tmp_path / "medicoes.xlsx"
    m_wb.save(m_xlsx)

    records_xlsx = import_measurements_spreadsheet(m_xlsx)
    assert len(records_xlsx) == 1
    assert records_xlsx[0].measured_hours == Decimal("46.5")
    assert records_xlsx[0].reference_period == ReferencePeriod(year=2026, month=7)

    records_csv = import_measurements_csv(
        "contrato;competencia;horas_medidas\nCT-2026-001;2026-07;46\n"
    )
    assert len(records_csv) == 1
    assert records_csv[0].measured_hours == Decimal("46")
    assert import_invoices_xlsx(xlsx_path.read_bytes()) == invoices
    assert import_measurements_xlsx(m_xlsx.read_bytes()) == records_xlsx
