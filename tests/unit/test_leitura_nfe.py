"""Testes do parser de NF-e XML (Etapa 4.3)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.leitura import UnsafeXmlError, parse_nfe
from backend.schemas import InvoiceSource, ReferencePeriod

NFE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35261011222333000181550010000001041000001040" versao="4.00">
      <ide>
        <nNF>104</nNF>
        <dhEmi>2026-11-05T10:15:00-03:00</dhEmi>
      </ide>
      <emit>
        <CNPJ>11222333000181</CNPJ>
        <xNome>Fornecedor Exemplo LTDA</xNome>
      </emit>
      <det nItem="1">
        <prod>
          <xProd>Mensalidade de suporte</xProd>
          <uCom>MES</uCom>
          <qCom>1.0000</qCom>
          <vUnCom>10500.0000000000</vUnCom>
          <vProd>10500.00</vProd>
        </prod>
      </det>
      <det nItem="2">
        <prod>
          <xProd>Horas excedentes</xProd>
          <uCom>HR</uCom>
          <qCom>2.0000</qCom>
          <vUnCom>150.1234567890</vUnCom>
          <vProd>300.25</vProd>
        </prod>
      </det>
      <total>
        <ICMSTot>
          <vNF>10800.25</vNF>
        </ICMSTot>
      </total>
      <infAdic>
        <infCpl>{inf_cpl}</infCpl>
      </infAdic>
    </infNFe>
  </NFe>
</nfeProc>
"""


def test_parse_nfe_extracts_fields_decimal_precision_contract_and_competence(
    tmp_path: Path,
) -> None:
    xml_text = NFE_TEMPLATE.format(
        inf_cpl="Referente ao Contrato: CT-2026-001. Competencia 2026-10."
    )
    xml_path = tmp_path / "nfe_104.xml"
    xml_path.write_text(xml_text, encoding="utf-8")

    invoice = parse_nfe(xml_path)
    assert invoice.source is InvoiceSource.NFE_XML
    assert invoice.contract_id == "CT-2026-001"
    assert invoice.supplier == "Fornecedor Exemplo LTDA"
    assert invoice.supplier_cnpj == "11222333000181"
    assert invoice.number == "104"
    assert invoice.issue_date == date(2026, 11, 5)
    assert invoice.reference_period == ReferencePeriod(year=2026, month=10)
    assert invoice.competence_inferred is False
    assert invoice.total == Decimal("10800.25")
    assert len(invoice.items) == 2
    assert invoice.items[0].unit_value == Decimal("10500.0000000000")
    assert invoice.items[1].unit_value == Decimal("150.1234567890")
    assert invoice.items[1].total_value == Decimal("300.25")


def test_parse_nfe_infers_competence_when_missing_in_additional_info() -> None:
    xml_text = NFE_TEMPLATE.format(inf_cpl="Contrato CT-2026-001 - servicos prestados.")
    invoice = parse_nfe(xml_text)
    assert invoice.contract_id == "CT-2026-001"
    assert invoice.reference_period == ReferencePeriod(year=2026, month=11)
    assert invoice.competence_inferred is True


@pytest.mark.parametrize(
    "malicious",
    [
        '<?xml version="1.0"?><!DOCTYPE nfeProc [<!ELEMENT nfeProc ANY>]><nfeProc/>',
        (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<nfeProc>&xxe;</nfeProc>"
        ),
    ],
)
def test_parse_nfe_rejects_doctype_and_entity(malicious: str) -> None:
    with pytest.raises(UnsafeXmlError):
        parse_nfe(malicious)
