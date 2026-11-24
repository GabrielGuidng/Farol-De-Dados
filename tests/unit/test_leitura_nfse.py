"""Testes do parser extensível de NFS-e XML (Etapa 4.4)."""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import pytest

from backend.leitura import (
    UnsafeXmlError,
    detect_nfse_layout,
    list_nfse_layouts,
    parse_nfse,
    register_nfse_layout,
)
from backend.leitura._parsing import find_text, parse_date_text, parse_decimal, safe_parse_xml
from backend.schemas import BillingItem, Invoice, InvoiceSource, ReferencePeriod

ABRASF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CompNfse xmlns="http://www.abrasf.org.br/nfse.xsd">
  <Nfse>
    <InfNfse Id="nfse-501">
      <Numero>501</Numero>
      <DataEmissao>2026-11-02T09:00:00</DataEmissao>
      <Competencia>{competencia}</Competencia>
      <Servico>
        <Valores>
          <ValorServicos>10500.00</ValorServicos>
        </Valores>
        <Discriminacao>Suporte mensal Contrato CT-2026-001 - competencia out/2026.</Discriminacao>
      </Servico>
      <PrestadorServico>
        <IdentificacaoPrestador>
          <CpfCnpj>
            <Cnpj>11222333000181</Cnpj>
          </CpfCnpj>
        </IdentificacaoPrestador>
        <RazaoSocial>Fornecedor Exemplo LTDA</RazaoSocial>
      </PrestadorServico>
    </InfNfse>
  </Nfse>
</CompNfse>
"""

PAULISTANA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<NFe xmlns="http://www.prefeitura.sp.gov.br/nfe">
  <ChaveNFe>
    <NumeroNFe>889</NumeroNFe>
  </ChaveNFe>
  <DataEmissaoNFe>2026-10-28T11:20:00</DataEmissaoNFe>
  <CPFCNPJPrestador>
    <CNPJ>11.222.333/0001-81</CNPJ>
  </CPFCNPJPrestador>
  <RazaoSocialPrestador>Fornecedor Paulista SA</RazaoSocialPrestador>
  <ValorServicos>10800,00</ValorServicos>
  <Discriminacao>Contrato CT-2026-001. Competencia 09/2026.</Discriminacao>
</NFe>
"""


def test_parse_nfse_abrasf_with_explicit_competence() -> None:
    invoice = parse_nfse(ABRASF_XML.format(competencia="2026-10-01"))
    assert "abrasf" in list_nfse_layouts()
    assert invoice.source is InvoiceSource.NFE_XML
    assert invoice.contract_id == "CT-2026-001"
    assert invoice.number == "501"
    assert invoice.issue_date == date(2026, 11, 2)
    assert invoice.reference_period == ReferencePeriod(year=2026, month=10)
    assert invoice.competence_inferred is False
    assert invoice.total == Decimal("10500.00")


def test_parse_nfse_abrasf_infers_competence_when_missing() -> None:
    xml_no_comp = ABRASF_XML.replace("<Competencia>{competencia}</Competencia>", "").replace(
        " - competencia out/2026", ""
    )
    invoice = parse_nfse(xml_no_comp)
    assert invoice.reference_period == ReferencePeriod(year=2026, month=11)
    assert invoice.competence_inferred is True


def test_parse_nfse_sp_paulistana_autodetected_and_explicit() -> None:
    root = safe_parse_xml(PAULISTANA_XML)
    assert detect_nfse_layout(root) == "sp_paulistana"

    invoice = parse_nfse(PAULISTANA_XML, layout="sp_paulistana")
    assert invoice.supplier_cnpj == "11222333000181"
    assert invoice.number == "889"
    assert invoice.total == Decimal("10800.00")
    assert invoice.reference_period == ReferencePeriod(year=2026, month=9)
    assert invoice.competence_inferred is False


def test_register_custom_municipal_nfse_layout() -> None:
    def detect_custom(root: ET.Element) -> bool:
        return root.tag == "NfseMaringa"

    def parse_custom(root: ET.Element, invoice_id: str | None) -> Invoice:
        total = parse_decimal(find_text(root, "Total") or "0")
        issue = parse_date_text(find_text(root, "Emissao") or "2026-10-01")
        return Invoice(
            id=invoice_id or "nfse-maringa-1",
            supplier=find_text(root, "Prestador") or "Prestador",
            supplier_cnpj="11222333000181",
            number=find_text(root, "Numero") or "1",
            issue_date=issue,
            reference_period=ReferencePeriod(year=issue.year, month=issue.month),
            competence_inferred=True,
            total=total,
            source=InvoiceSource.NFE_XML,
            items=(
                BillingItem(
                    description="Servico municipal",
                    quantity=Decimal("1"),
                    unit_value=total,
                    total_value=total,
                ),
            ),
        )

    register_nfse_layout("maringa_custom", detect_custom, parse_custom)
    xml = (
        "<NfseMaringa><Numero>42</Numero><Emissao>2026-08-15</Emissao>"
        "<Prestador>Tech Sul</Prestador><Total>5500,00</Total></NfseMaringa>"
    )
    invoice = parse_nfse(xml)
    assert invoice.number == "42"
    assert invoice.total == Decimal("5500.00")


def test_parse_nfse_rejects_unknown_layout_and_unsafe_xml() -> None:
    with pytest.raises(ValueError):
        parse_nfse("<OutroXml><Numero>1</Numero></OutroXml>")
    with pytest.raises(ValueError):
        parse_nfse(PAULISTANA_XML, layout="municipio_inexistente")
    with pytest.raises(UnsafeXmlError):
        parse_nfse('<!DOCTYPE xml [<!ENTITY a "b">]><CompNfse>&a;</CompNfse>')
