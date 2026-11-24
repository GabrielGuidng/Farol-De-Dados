"""Parser determinístico de NF-e (XML), com leitura segura e precisão Decimal."""

import xml.etree.ElementTree as ET
from pathlib import Path

from backend.leitura._parsing import (
    extract_competence,
    extract_contract_id,
    find_all_elements,
    find_element,
    find_text,
    parse_date_text,
    parse_decimal,
    safe_parse_xml,
)
from backend.schemas import BillingItem, Invoice, InvoiceSource, ReferencePeriod


def _require_text(element: ET.Element, *tag_path: str, label: str) -> str:
    text = find_text(element, *tag_path)
    if text is None:
        raise ValueError(f"NF-e sem campo obrigatório: {label}")
    return text


def parse_nfe(source: str | bytes | Path, *, invoice_id: str | None = None) -> Invoice:
    """Lê XML de NF-e de forma segura e devolve `Invoice` com valores em Decimal."""
    root = safe_parse_xml(source)
    inf_nfe = find_element(root, "infNFe")
    if inf_nfe is None:
        raise ValueError("XML não contém o bloco <infNFe>")

    supplier = _require_text(inf_nfe, "emit", "xNome", label="emit/xNome")
    cnpj = _require_text(inf_nfe, "emit", "CNPJ", label="emit/CNPJ")
    number = _require_text(inf_nfe, "ide", "nNF", label="ide/nNF")
    emission_raw = find_text(inf_nfe, "ide", "dhEmi") or find_text(inf_nfe, "ide", "dEmi")
    if emission_raw is None:
        raise ValueError("NF-e sem data de emissão (ide/dhEmi ou ide/dEmi)")
    issue_date = parse_date_text(emission_raw)

    total_raw = _require_text(inf_nfe, "total", "ICMSTot", "vNF", label="total/ICMSTot/vNF")
    total = parse_decimal(total_raw, "total")

    items: list[BillingItem] = []
    for det in find_all_elements(inf_nfe, "det"):
        prod = find_element(det, "prod")
        if prod is None:
            continue
        description = _require_text(prod, "xProd", label="det/prod/xProd")
        quantity = parse_decimal(_require_text(prod, "qCom", label="det/prod/qCom"), "qCom")
        unit = find_text(prod, "uCom")
        unit_value = parse_decimal(_require_text(prod, "vUnCom", label="det/prod/vUnCom"), "vUnCom")
        total_value = parse_decimal(_require_text(prod, "vProd", label="det/prod/vProd"), "vProd")
        items.append(
            BillingItem(
                description=description,
                quantity=quantity,
                unit=unit,
                unit_value=unit_value,
                total_value=total_value,
            )
        )

    extra_parts = [
        part
        for part in (
            find_text(inf_nfe, "infAdic", "infCpl"),
            find_text(inf_nfe, "infAdic", "infAdFisco"),
        )
        if part is not None
    ]
    extra_text = "\n".join(extra_parts)
    contract_id = extract_contract_id(extra_text)
    competence = extract_competence(extra_text)
    competence_inferred = competence is None
    if competence is None:
        competence = ReferencePeriod(year=issue_date.year, month=issue_date.month)

    resolved_id = invoice_id or inf_nfe.attrib.get("Id") or f"nfe-{cnpj}-{number}"
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
        items=tuple(items),
    )
