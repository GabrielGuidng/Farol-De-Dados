"""Parser extensível de NFS-e (XML), com suporte inicial a ABRASF e Paulistana."""

import xml.etree.ElementTree as ET
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from backend.leitura._parsing import (
    extract_competence,
    extract_contract_id,
    find_element,
    find_text,
    parse_date_text,
    parse_decimal,
    safe_parse_xml,
)
from backend.schemas import BillingItem, Invoice, InvoiceSource, ReferencePeriod

NfseLayoutParser = Callable[[ET.Element, str | None], Invoice]
NfseLayoutDetector = Callable[[ET.Element], bool]

_LAYOUT_REGISTRY: list[tuple[str, NfseLayoutDetector, NfseLayoutParser]] = []


def register_nfse_layout(name: str, detector: NfseLayoutDetector, parser: NfseLayoutParser) -> None:
    """Registra ou substitui um leiaute municipal de NFS-e para detecção automática."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("nome do leiaute não pode ser vazio")
    for index, (existing_name, _, _) in enumerate(_LAYOUT_REGISTRY):
        if existing_name == clean_name:
            _LAYOUT_REGISTRY[index] = (clean_name, detector, parser)
            return
    _LAYOUT_REGISTRY.append((clean_name, detector, parser))


def list_nfse_layouts() -> tuple[str, ...]:
    """Devolve os nomes dos leiautes de NFS-e registrados."""
    return tuple(name for name, _, _ in _LAYOUT_REGISTRY)


def detect_nfse_layout(root: ET.Element) -> str | None:
    """Identifica o leiaute do XML de NFS-e entre os registrados, ou devolve None."""
    for name, detector, _ in _LAYOUT_REGISTRY:
        if detector(root):
            return name
    return None


def _detect_abrasf(root: ET.Element) -> bool:
    return find_element(root, "InfNfse") is not None


def _parse_competence_field(raw: str) -> ReferencePeriod:
    extracted = extract_competence(raw)
    if extracted is not None:
        return extracted
    parsed_date = parse_date_text(raw)
    return ReferencePeriod(year=parsed_date.year, month=parsed_date.month)


def _parse_abrasf(root: ET.Element, invoice_id: str | None) -> Invoice:
    inf_nfse = find_element(root, "InfNfse")
    if inf_nfse is None:
        raise ValueError("NFS-e ABRASF sem bloco <InfNfse>")

    number = find_text(inf_nfse, "Numero")
    emission_raw = find_text(inf_nfse, "DataEmissao")
    if number is None or emission_raw is None:
        raise ValueError("NFS-e ABRASF sem Numero ou DataEmissao")
    issue_date = parse_date_text(emission_raw)

    provider = find_element(inf_nfse, "PrestadorServico")
    if provider is None:
        provider = find_element(inf_nfse, "Prestador")
    if provider is None:
        raise ValueError("NFS-e ABRASF sem bloco de prestador")
    supplier = find_text(provider, "RazaoSocial") or find_text(provider, "NomeFantasia")
    cnpj = (
        find_text(provider, "IdentificacaoPrestador", "CpfCnpj", "Cnpj")
        or find_text(provider, "CpfCnpj", "Cnpj")
        or find_text(provider, "Cnpj")
    )
    if supplier is None or cnpj is None:
        raise ValueError("NFS-e ABRASF sem razão social ou CNPJ do prestador")

    service = find_element(inf_nfse, "Servico")
    if service is None:
        service = inf_nfse
    description = find_text(service, "Discriminacao") or f"Serviços NFS-e {number}"
    total_raw = (
        find_text(service, "Valores", "ValorServicos")
        or find_text(inf_nfse, "ValoresNfse", "ValorLiquidoNfse")
        or find_text(service, "Valores", "ValorLiquidoNfse")
    )
    if total_raw is None:
        raise ValueError("NFS-e ABRASF sem ValorServicos")
    total = parse_decimal(total_raw, "ValorServicos")

    competence_raw = find_text(inf_nfse, "Competencia")
    competence: ReferencePeriod | None = (
        _parse_competence_field(competence_raw)
        if competence_raw is not None
        else extract_competence(description)
    )
    competence_inferred = competence is None
    if competence is None:
        competence = ReferencePeriod(year=issue_date.year, month=issue_date.month)

    contract_id = extract_contract_id(description)
    resolved_id = invoice_id or inf_nfse.attrib.get("Id") or f"nfse-{cnpj}-{number}"
    item = BillingItem(
        description=description,
        quantity=Decimal("1"),
        unit="serviço",
        unit_value=total,
        total_value=total,
    )
    return Invoice(
        id=resolved_id,
        contract_id=contract_id,
        supplier=supplier,
        supplier_cnpj=cnpj,
        number=number,
        issue_date=issue_date,
        reference_period=competence,
        competence_inferred=competence_inferred,
        total=total,
        source=InvoiceSource.NFE_XML,
        items=(item,),
    )


def _detect_sp_paulistana(root: ET.Element) -> bool:
    return find_element(root, "ChaveNFe", "NumeroNFe") is not None


def _parse_sp_paulistana(root: ET.Element, invoice_id: str | None) -> Invoice:
    number = find_text(root, "ChaveNFe", "NumeroNFe")
    emission_raw = find_text(root, "DataEmissaoNFe")
    supplier = find_text(root, "RazaoSocialPrestador")
    cnpj = find_text(root, "CPFCNPJPrestador", "CNPJ")
    total_raw = find_text(root, "ValorServicos")
    if None in (number, emission_raw, supplier, cnpj, total_raw):
        raise ValueError("NFS-e Paulistana sem campos obrigatórios")
    assert number is not None
    assert emission_raw is not None
    assert supplier is not None
    assert cnpj is not None
    assert total_raw is not None

    issue_date = parse_date_text(emission_raw)
    total = parse_decimal(total_raw, "ValorServicos")
    description = find_text(root, "Discriminacao") or f"Serviços NFS-e {number}"
    competence = extract_competence(description)
    competence_inferred = competence is None
    if competence is None:
        competence = ReferencePeriod(year=issue_date.year, month=issue_date.month)

    contract_id = extract_contract_id(description)
    resolved_id = invoice_id or f"nfse-sp-{cnpj}-{number}"
    item = BillingItem(
        description=description,
        quantity=Decimal("1"),
        unit="serviço",
        unit_value=total,
        total_value=total,
    )
    return Invoice(
        id=resolved_id,
        contract_id=contract_id,
        supplier=supplier,
        supplier_cnpj=cnpj,
        number=number,
        issue_date=issue_date,
        reference_period=competence,
        competence_inferred=competence_inferred,
        total=total,
        source=InvoiceSource.NFE_XML,
        items=(item,),
    )


register_nfse_layout("abrasf", _detect_abrasf, _parse_abrasf)
register_nfse_layout("sp_paulistana", _detect_sp_paulistana, _parse_sp_paulistana)


def parse_nfse(
    source: str | bytes | Path,
    *,
    layout: str | None = None,
    invoice_id: str | None = None,
) -> Invoice:
    """Lê XML de NFS-e de forma segura usando leiaute explícito ou autodetectado."""
    root = safe_parse_xml(source)
    target_layout = layout if layout is not None else detect_nfse_layout(root)
    if target_layout is None:
        raise ValueError("leiaute de NFS-e não reconhecido entre os formatos registrados")

    for name, _, parser in _LAYOUT_REGISTRY:
        if name == target_layout:
            return parser(root, invoice_id)
    raise ValueError(f"leiaute de NFS-e desconhecido: {target_layout}")
