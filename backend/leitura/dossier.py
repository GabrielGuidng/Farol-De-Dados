"""Extrator determinístico de metadados contratuais, retenções, OPs, certidões e resumos."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backend.leitura._parsing import parse_date_text, parse_decimal
from backend.schemas import (
    CertificateType,
    ContractRule,
    DossierFinancialSummary,
    Evidence,
    PaymentOrder,
    ReferencePeriod,
    ReviewStatus,
    RuleRelation,
    RuleType,
    TaxCertificate,
    ValidityPeriod,
    is_valid_cnpj,
    normalize_cnpj,
)

_PAGE_FOOTER_RE = re.compile(r"(?im)P[áa]g\.\s*([0-9]+)\b")
_PAGE_HEADER_RE = re.compile(r"(?im)^\[P[áa]gina\s+([0-9]+)\]$")

_BANK_INFO_RE = re.compile(
    r"(?i)(?:conta(?:\s+banc[áa]ria)?\s+da\s+(?:contratada|locadora|detentora)|"
    r"conta\s+banc[áa]ria\s+da\s+contratada)\s*:?\s*"
    r"Banco\s*([0-9A-Za-z-]+)\s*[,;\-]\s*Ag[êe]ncia\s*([0-9A-Za-z-]+)\s*[,;\-]\s*"
    r"Conta(?:\s+corrente)?\s*([0-9A-Za-z-]+)"
)
_LEGAL_NATURE_RE = re.compile(r"(?i)Natureza\s+jur[íi]dica\s*:\s*([^\n(]+)")
_VALIDITY_CLAUSE_PRIORITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?is)CL[ÁA]USULA\s+(?:\d+[ªa.º°]?|PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA|"
        r"SEXTA|S[ÉE]TIMA|OITAVA|NONA|D[ÉE]CIMA)[^\n]*(?:PRAZO|VIG[ÊE]NCIA)[\s\S]{0,260}?"
        r"(?:de\s+|in[íi]cio\s+em\s+)?(\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*(\d{2}/\d{2}/\d{4})"
    ),
    re.compile(
        r"(?i)(?:o\s+contrato|a\s+loca[çc][ãa]o|a\s+ata|o\s+instrumento)\s+"
        r"(?:ter[áa]\s+vig[êe]ncia|vigorar[áa])[^.\n]{0,140}?"
        r"(?:de\s+|in[íi]cio\s+em\s+)?(\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*(\d{2}/\d{2}/\d{4})"
    ),
    re.compile(
        r"(?i)A?\s*vig[êe]ncia[^.\n]{0,80}?"
        r"(?:de\s+|in[íi]cio\s+em\s+)?(\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*(\d{2}/\d{2}/\d{4})"
    ),
)
_INSURANCE_CONTEXT_RE = re.compile(r"(?i)\b(?:ap[óo]lice|seguro|garantia|certid[ãa]o)\b")

_INVOICE_TABLE_ROW_RE = re.compile(
    r"(?m)^\s*(?P<number>\d{2,10})\s+"
    r"(?P<comp>\d{2}/\d{4})\s+"
    r"(?P<issue>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<att>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<gross>[\d.,]+)\s+"
    r"(?P<inss>[\d.,]+)\s+"
    r"(?P<iss>[\d.,]+)\s+"
    r"(?P<net>[\d.,]+)\s*$"
)
_NF_COPY_BLOCK_RE = re.compile(
    r"(?is)C[óo]pia\s+resumida\s+da\s+NF\s+n[º°.]?\s*(?P<number>\d{2,10})"
    r"(?P<body>.*?)(?=C[óo]pia\s+resumida\s+da\s+NF|Extrato\s+banc[áa]rio|"
    r"^\s*\d+\.\s+[A-ZÁÉÍÓÚ]|$)"
)
_PRESTADOR_LINE_RE = re.compile(
    r"(?im)^\s*Prestador\s+(?P<name>[^\n-]+?)\s*-\s*CNPJ\s+"
    r"(?P<cnpj>\d{2}\.\d{3}\.\d{3}/\d{4}-[0-9?X*]{2})"
)
_DISCRIMINACAO_RE = re.compile(r"(?is)Discrimina[çc][ãa]o\s+(?P<desc>.+?)(?=\n\s*Valor\s+total|\Z)")

_OP_BLOCK_RE = re.compile(
    r"(?m)^(?P<benef>[^\n]+?)\s+"
    r"(?P<bank>\d{3,4})\s*/\s*(?P<ag>\d{3,5})\s*/\s*\r?\n"
    r"\s*(?P<op>\d{4}/\d{3,6})\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<nf>\d{2,10})\s+"
    r"(?P<amt>[\d.,]+)\s+"
    r"(?P<req>\d{3,8})\s+"
    r"(?P<app>\d{3,8})\s*\r?\n"
    r"\s*(?P<cnpj>\d{2}\.\d{3}\.\d{3}/\d{4}-[0-9?X*]{2})\s+"
    r"(?P<acc>[0-9A-Za-z-]+)\s*$"
)
_OP_APPROVAL_ROW_RE = re.compile(
    r"(?im)^\s*(?P<op_or_all>\d{4}/\d{3,6}|Demais\s+OPs)\s+"
    r"(?:R\$\s*)?[\d.,]+\s+"
    r"(?P<id>\d{3,8})\s*-\s*(?P<role>[^\n(]+?)"
    r"(?:\s*\([^\n)]+\))?\s+At[ée]\s+R\$\s*(?P<limit>[\d.,]+)"
)
_STAFF_ROLE_RE = re.compile(r"(\d{3,8})\s*-\s*([^();]+?)\s*\(([^)]+)\)")

_CERT_ROW_RE = re.compile(
    r"(?im)^\s*(?P<label>(?:CND|CRF|CNDT|Certid[ãa]o\s+de\s+Tributos\s+Federais|"
    r"Certid[ãa]o\s+Negativa|Certid[ãa]o\s+Positiva)[^\n]*?)\s+"
    r"(?P<issue>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<expiry>\d{2}/\d{2}/\d{4})"
    r"(?:\s+(?P<status>[^\n]+?))?\s*$"
)
_INSURANCE_GUARANTEE_RE = re.compile(
    r"(?is)Seguro-?garantia[\s\S]{0,240}?"
    r"Vig[êe]ncia\s+da\s+ap[óo]lice\s+(?P<start>\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*"
    r"(?P<end>\d{2}/\d{2}/\d{4})"
    r"(?:[\s\S]{0,120}?Renova[çc][ãa]o\s*/\s*endosso\s+(?P<renewal>[^\n]+))?"
)
_PUBLIC_OB_ROW_RE = re.compile(
    r"(?m)(?P<op>20\d{2}OB\d+)\s+"
    r"(?P<pay_date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<empenho>\S+)\s+"
    r"(?P<nf>\d{4}/\d{2,10}|\d{2,10})\s+"
    r"(?:(?P<inline_benef>.+?)\s+)?"
    r"(?P<amt>\d{1,3}(?:\.\d{3})*,\d{2})\s+"
    r"(?P<att_date>\d{2}/\d{2}/\d{4})\s*$"
)
_GLOSA_SECTION_RE = re.compile(
    r"(?is)Relat[óo]rio\s+mensal\s+de\s+frequ[êe]ncia.+?"
    r"(?=REGULARIDADE\s+FISCAL|\Z)"
)
_GLOSA_ROW_RE = re.compile(r"(?m)^\s*(?P<comp>\d{2}/\d{4})\s+\d+\s+(?P<glosa>[\d.,]+)\s*$")

_SUMMARY_COMP_RE = re.compile(r"(?im)N[úu]mero\s+de\s+compet[êe]ncias[^\n]*?\s+(\d+)\s*$")
_SUMMARY_CONTRACTED_RE = re.compile(
    r"(?im)Total\s+contratado\s+para\s+o\s+per[íi]odo[^\n]*?\s+([\d.,]+)\s*$"
)
_SUMMARY_GROSS_RE = re.compile(r"(?im)Total\s+bruto\s+faturado[^\n]*?\s+([\d.,]+)\s*$")
_SUMMARY_INSS_RE = re.compile(
    r"(?im)Total\s+de\s+reten[çc][õo]es\s+de\s+INSS[^\n]*?\s+([\d.,]+)\s*$"
)
_SUMMARY_ISS_RE = re.compile(r"(?im)Total\s+de\s+reten[çc][õo]es\s+de\s+ISS[^\n]*?\s+([\d.,]+)\s*$")
_SUMMARY_NET_RE = re.compile(r"(?im)Total\s+l[íi]quido\s+pago[^\n]*?\s+([\d.,]+)\s*$")

_CLAUSE_TOKEN = (
    r"(?:\d+[ªa.º°]?|PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA|"
    r"SEXTA|S[ÉE]TIMA|OITAVA|NONA|D[ÉE]CIMA(?:\s+PRIMEIRA|\s+SEGUNDA)?)"
)
_CLAUSE_RETENTION_RE = re.compile(
    rf"(?is)(CL[ÁA]USULA\s+{_CLAUSE_TOKEN}\s*-\s*DAS\s+RETEN[ÇC][ÕO]ES\..+?)"
    r"(?=\n\s*CL[ÁA]USULA\s+|\Z)"
)
_CLAUSE_APPROVAL_RE = re.compile(
    rf"(?is)(CL[ÁA]USULA\s+{_CLAUSE_TOKEN}\s*-\s*DA\s+AL[ÇC]ADA[^\n]*\..+?)"
    r"(?=\n\s*CL[ÁA]USULA\s+|\Z)"
)
_CLAUSE_PAYMENT_RE = re.compile(
    rf"(?is)(CL[ÁA]USULA\s+{_CLAUSE_TOKEN}\s*-\s*DO\s+FATURAMENTO\s+E\s+(?:DO\s+)?PAGAMENTO\..+?)"
    r"(?=\n\s*CL[ÁA]USULA\s+|\Z)"
)
_CLAUSE_ADJUSTMENT_RE = re.compile(
    rf"(?is)(CL[ÁA]USULA\s+{_CLAUSE_TOKEN}\s*-\s*DO\s+(?:PRE[ÇC]O|REAJUSTE|REPACTUA[ÇC][ÃA]O)\..+?)"
    r"(?=\n\s*CL[ÁA]USULA\s+|\Z)"
)
_POSTS_TABLE_SECTION_RE = re.compile(
    r"(?is)Posto\s+Quantidade\s+Valor\s+mensal\s+por\s+posto(?P<body>.+?)"
    r"(?=\n\s*(?:§|CL[ÁA]USULA)\s+|\Z)"
)
_POSTS_TABLE_ROW_RE = re.compile(
    r"(?im)^\s*(?P<name>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][^\n0-9]+?)\s+"
    r"(?P<qty>\d+(?:,\d+)?)\s+"
    r"(?P<unit_price>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
    r"(?:\s*\([^)]+\))?\s+\d{1,3}(?:\.\d{3})*,\d{2}"
)
_ATA_TABLE_SECTION_RE = re.compile(
    r"(?is)Item\s+Descri[çc][ãa]o\s+Unidade\s+Quantidade\s+m[áa]xima\s+Pre[çc]o\s+unit[áa]rio"
    r"(?P<body>.+?)(?=\n\s*CL[ÁA]USULA\s+|\Z)"
)
_ATA_TABLE_ROW_RE = re.compile(
    r"(?im)^\s*\d+\s+(?P<desc>.+?)\s+"
    r"(?P<unit>resma|unidade|caixa|frasco|pacote|metro|litro|kg|un|cx)\s+"
    r"(?P<qty>[\d.,]+)\s+(?P<price>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*$"
)


@dataclass(frozen=True)
class ContractDossierMetadata:
    """Metadados contratuais e bancários extraídos deterministicamente do texto."""

    legal_nature: str | None = None
    bank_code: str | None = None
    bank_agency: str | None = None
    bank_account: str | None = None
    validity_start: date | None = None
    validity_end: date | None = None


@dataclass(frozen=True)
class InvoiceDossierPatch:
    """Campos complementares por número da NF (atesto, retenções, razão social, descrição)."""

    invoice_number: str
    reference_period: ReferencePeriod | None = None
    issue_date: date | None = None
    attestation_date: date | None = None
    inss_withheld: Decimal | None = None
    iss_withheld: Decimal | None = None
    net_total: Decimal | None = None
    supplier: str | None = None
    raw_supplier_cnpj: str | None = None
    supplier_legal_nature: str | None = None
    service_description: str | None = None


@dataclass(frozen=True)
class DossierExtractionResult:
    """Conjunto estruturado de artefatos financeiros e complementares de um dossiê."""

    contract_metadata: ContractDossierMetadata
    invoice_patches: dict[str, InvoiceDossierPatch]
    payment_orders: tuple[PaymentOrder, ...]
    tax_certificates: tuple[TaxCertificate, ...]
    financial_summary: DossierFinancialSummary | None
    supplemental_rules: tuple[ContractRule, ...]
    expected_glosas_by_period: dict[str, Decimal] | None = None


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").casefold() or "item"


def _page_for_offset(text: str, offset: int) -> int:
    # Prioriza cabeçalho `[Página N]` se presente; senão procura próximo rodapé `Pág. N`.
    before = text[:offset]
    header_matches = list(_PAGE_HEADER_RE.finditer(before))
    if header_matches:
        return int(header_matches[-1].group(1))
    after = text[offset:]
    footer_match = _PAGE_FOOTER_RE.search(after)
    if footer_match is not None:
        return int(footer_match.group(1))
    return 1


def _expand_invoice_numbers_token(raw_spec: str, known_numbers: list[str]) -> list[str]:
    clean = raw_spec.replace("\n", " ").strip()
    range_match = re.match(r"^\s*(\d+)\s+a\s+(\d+)\s*(?:,\s*(.+))?$", clean, re.IGNORECASE)
    selected: list[str] = []
    if range_match is not None:
        start_num = int(range_match.group(1))
        end_num = int(range_match.group(2))
        for num in known_numbers:
            if num.isdigit() and start_num <= int(num) <= end_num:
                selected.append(num)
        rest = range_match.group(3)
        if rest:
            for part in rest.split(","):
                tok = part.strip()
                if tok and tok not in selected:
                    selected.append(tok)
        return selected
    for part in clean.split(","):
        tok = part.strip()
        if tok.isdigit() and tok not in selected:
            selected.append(tok)
    return selected


def _merge_patch(
    patches: dict[str, InvoiceDossierPatch],
    number: str,
    *,
    reference_period: ReferencePeriod | None = None,
    issue_date: date | None = None,
    attestation_date: date | None = None,
    inss_withheld: Decimal | None = None,
    iss_withheld: Decimal | None = None,
    net_total: Decimal | None = None,
    supplier: str | None = None,
    raw_supplier_cnpj: str | None = None,
    supplier_legal_nature: str | None = None,
    service_description: str | None = None,
) -> None:
    key = number.strip().casefold()
    current = patches.get(key, InvoiceDossierPatch(invoice_number=number.strip()))
    patches[key] = InvoiceDossierPatch(
        invoice_number=current.invoice_number,
        reference_period=(
            reference_period if reference_period is not None else current.reference_period
        ),
        issue_date=issue_date if issue_date is not None else current.issue_date,
        attestation_date=(
            attestation_date if attestation_date is not None else current.attestation_date
        ),
        inss_withheld=inss_withheld if inss_withheld is not None else current.inss_withheld,
        iss_withheld=iss_withheld if iss_withheld is not None else current.iss_withheld,
        net_total=net_total if net_total is not None else current.net_total,
        supplier=supplier if supplier is not None else current.supplier,
        raw_supplier_cnpj=(
            raw_supplier_cnpj if raw_supplier_cnpj is not None else current.raw_supplier_cnpj
        ),
        supplier_legal_nature=(
            supplier_legal_nature
            if supplier_legal_nature is not None
            else current.supplier_legal_nature
        ),
        service_description=(
            service_description if service_description is not None else current.service_description
        ),
    )


def _parse_invoice_sections(text: str) -> dict[str, InvoiceDossierPatch]:
    patches: dict[str, InvoiceDossierPatch] = {}
    known_numbers: list[str] = []

    for match in _INVOICE_TABLE_ROW_RE.finditer(text):
        num = match.group("number").strip()
        known_numbers.append(num)
        comp_m, comp_y = match.group("comp").split("/")
        period = ReferencePeriod.model_validate(f"{comp_y}-{comp_m}")
        _merge_patch(
            patches,
            num,
            reference_period=period,
            issue_date=parse_date_text(match.group("issue")),
            attestation_date=parse_date_text(match.group("att")),
            inss_withheld=parse_decimal(match.group("inss"), "inss_withheld"),
            iss_withheld=parse_decimal(match.group("iss"), "iss_withheld"),
            net_total=parse_decimal(match.group("net"), "net_total"),
        )

    # Quadro de descrições ("Descrição de serviços informada nas notas")
    desc_header = re.search(
        r"(?i)Descri[çc][ãa]o\s+de\s+servi[çc]os\s+informada\s+nas\s+notas",
        text,
    )
    if desc_header is not None:
        end_slice = re.search(
            r"(?i)Dados\s+do\s+emitente|^\s*\d+\.\s+Ordens\s+de\s+pagamento",
            text[desc_header.end() :],
            re.MULTILINE,
        )
        desc_block = (
            text[desc_header.end() : desc_header.end() + end_slice.start()]
            if end_slice is not None
            else text[desc_header.end() : desc_header.end() + 1500]
        )
        # Casa padrão "<números> <descrição>" ou linha com números seguido da descrição
        for row_m in re.finditer(
            r"(?m)^\s*(?P<nums>\d{2,10}(?:\s+a\s+\d{2,10})?(?:\s*,\s*\d{2,10})*)\s+"
            r"(?P<desc>Servi[çc]os[^\n]+)$",
            desc_block,
        ):
            nums = _expand_invoice_numbers_token(row_m.group("nums"), known_numbers)
            desc = row_m.group("desc").strip()
            for num in nums:
                _merge_patch(patches, num, service_description=desc)
        # Caso especial com quebra de linha entre lista de NFs / NF única e descrição (ex.: 1051)
        special_1051 = re.search(
            r"(?is)(Servi[çc]os\s+de[^\n]*compet[êe]ncia\s+09/2025[^\n]*)\r?\n\s*(?P<num>\d{2,10})"
            r"\s*\r?\n\s*(?P<cont>acrescido[^\n]+)",
            desc_block,
        )
        if special_1051 is not None:
            desc_joined = f"{special_1051.group(1).strip()} {special_1051.group('cont').strip()}"
            _merge_patch(
                patches,
                special_1051.group("num"),
                service_description=desc_joined,
            )

    # Quadro de emitentes ("Dados do emitente conforme cabeçalho de cada NF")
    issuer_header = re.search(
        r"(?i)Dados\s+do\s+emitente\s+conforme\s+cabe[çc]alho",
        text,
    )
    if issuer_header is not None:
        end_slice = re.search(
            r"(?im)^\s*\d+\.\s+Ordens\s+de\s+pagamento|P[áa]g\.\s*\d+",
            text[issuer_header.end() :],
        )
        issuer_block = (
            text[issuer_header.end() : issuer_header.end() + end_slice.start()]
            if end_slice is not None
            else text[issuer_header.end() : issuer_header.end() + 1500]
        )
        for row_m in re.finditer(
            r"(?im)^\s*(?P<nums>\d{2,10}(?:\s*,\s*\d{2,10})*)\s+"
            r"(?P<name>[^\n]+?)\s+"
            r"(?P<cnpj>\d{2}\.\d{3}\.\d{3}/\d{4}-[0-9?X*]{2})"
            r"(?:\s+(?P<nat>EIRELI|LTDA|ME|EPP|Sociedade[^\n]*))?\s*$",
            issuer_block,
        ):
            nums = _expand_invoice_numbers_token(row_m.group("nums"), known_numbers)
            name = row_m.group("name").strip()
            cnpj = row_m.group("cnpj").strip()
            nat = (row_m.group("nat") or "").strip() or None
            for num in nums:
                _merge_patch(
                    patches,
                    num,
                    supplier=name,
                    raw_supplier_cnpj=cnpj,
                    supplier_legal_nature=nat,
                )

    # Cópias resumidas da NF (ex.: NF 1049 e NF 1051)
    for copy_m in _NF_COPY_BLOCK_RE.finditer(text):
        num = copy_m.group("number").strip()
        body = copy_m.group("body")
        prest_m = _PRESTADOR_LINE_RE.search(body)
        disc_m = _DISCRIMINACAO_RE.search(body)
        _merge_patch(
            patches,
            num,
            supplier=prest_m.group("name").strip() if prest_m is not None else None,
            raw_supplier_cnpj=prest_m.group("cnpj").strip() if prest_m is not None else None,
            service_description=(
                " ".join(disc_m.group("desc").split()) if disc_m is not None else None
            ),
        )

    return patches


def _parse_public_ob_rows(
    text: str,
    *,
    contract_id: str,
    source_document_id: str,
    patches: dict[str, InvoiceDossierPatch] | None,
    seen_ops: set[str],
) -> list[PaymentOrder]:
    lines = text.splitlines()
    orders: list[PaymentOrder] = []
    cnpj_re = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-[0-9?X*]{2}")
    bank_ag_re = re.compile(r"(\d{3,4})\s*/\s*([0-9A-Za-z-]+)\s*/")
    acc_re = re.compile(r"\b(\d{4,}-\d)\b")
    for idx, line in enumerate(lines):
        m = _PUBLIC_OB_ROW_RE.search(line)
        if m is None:
            continue
        op_num = m.group("op").strip()
        if op_num in seen_ops:
            continue
        seen_ops.add(op_num)
        pay_dt = parse_date_text(m.group("pay_date"))
        att_dt = parse_date_text(m.group("att_date"))
        nf_num = m.group("nf").strip()
        amt = parse_decimal(m.group("amt"), "paid_amount")
        if patches is not None:
            _merge_patch(patches, nf_num, attestation_date=att_dt)

        # Janela local [idx-2 : idx+3] sem invadir outra linha OB adjacente.
        lo = max(0, idx - 2)
        hi = min(len(lines), idx + 3)
        window_lines: list[str] = []
        for j in range(lo, hi):
            if j != idx and _PUBLIC_OB_ROW_RE.search(lines[j]):
                if j < idx:
                    window_lines.clear()
                    continue
                break
            window_lines.append(lines[j])
        window_txt = "\n".join(window_lines)

        cnpj_m = cnpj_re.search(window_txt)
        raw_cnpj = cnpj_m.group(0) if cnpj_m is not None else None
        valid_cnpj = normalize_cnpj(raw_cnpj) if raw_cnpj and is_valid_cnpj(raw_cnpj) else None
        bank_m = bank_ag_re.search(window_txt)
        bank_code = bank_m.group(1) if bank_m is not None else None
        bank_agency = bank_m.group(2) if bank_m is not None else None
        acc_m = acc_re.search(window_txt)
        bank_account = acc_m.group(1) if acc_m is not None else None

        benef_parts: list[str] = []
        inline_b = (m.group("inline_benef") or "").strip()
        # Coleta texto de favorecido das linhas anteriores/seguintes dentro do bloco
        for j in range(idx - 1, lo - 1, -1):
            if _PUBLIC_OB_ROW_RE.search(lines[j]):
                break
            cleaned = bank_ag_re.sub("", lines[j])
            cleaned = cnpj_re.sub("", cleaned).strip(" /")
            if cleaned and not any(
                hdr in cleaned.casefold()
                for hdr in ("favorecido", "bancário", "liquidação", "extração")
            ):
                benef_parts.insert(0, cleaned)
        if inline_b:
            benef_parts.append(inline_b)
        benef_name = " ".join(benef_parts).strip() or "Beneficiário na OB"

        orders.append(
            PaymentOrder(
                id=f"op-{source_document_id}-{_slug(op_num)}",
                contract_id=contract_id,
                source_document_id=source_document_id,
                op_number=op_num,
                payment_date=pay_dt,
                invoice_number=nf_num,
                beneficiary_name=benef_name,
                beneficiary_cnpj=valid_cnpj,
                raw_beneficiary_cnpj=raw_cnpj,
                bank_code=bank_code,
                bank_agency=bank_agency,
                bank_account=bank_account,
                paid_amount=amt,
            )
        )
    return orders


def _parse_payment_orders(
    text: str,
    *,
    contract_id: str,
    source_document_id: str,
    patches: dict[str, InvoiceDossierPatch] | None = None,
) -> tuple[PaymentOrder, ...]:
    staff_roles: dict[str, str] = {}
    for staff_m in _STAFF_ROLE_RE.finditer(text):
        staff_roles[staff_m.group(1).strip()] = staff_m.group(3).strip()

    explicit_limits: dict[str, tuple[str, Decimal]] = {}
    default_limit: tuple[str, Decimal] | None = None
    for app_m in _OP_APPROVAL_ROW_RE.finditer(text):
        target = app_m.group("op_or_all").strip()
        role = app_m.group("role").strip()
        limit_val = parse_decimal(app_m.group("limit"), "approver_limit")
        if target.casefold().startswith("demais"):
            default_limit = (role, limit_val)
        else:
            explicit_limits[target] = (role, limit_val)

    orders: list[PaymentOrder] = []
    seen_ops: set[str] = set()
    for op_m in _OP_BLOCK_RE.finditer(text):
        op_num = op_m.group("op").strip()
        if op_num in seen_ops:
            continue
        seen_ops.add(op_num)
        raw_cnpj = op_m.group("cnpj").strip()
        valid_cnpj = normalize_cnpj(raw_cnpj) if is_valid_cnpj(raw_cnpj) else None
        approver_id = op_m.group("app").strip()
        role_and_limit = explicit_limits.get(op_num) or default_limit
        approver_role = (
            role_and_limit[0] if role_and_limit is not None else staff_roles.get(approver_id)
        )
        approver_limit = role_and_limit[1] if role_and_limit is not None else None
        orders.append(
            PaymentOrder(
                id=f"op-{source_document_id}-{_slug(op_num)}",
                contract_id=contract_id,
                source_document_id=source_document_id,
                op_number=op_num,
                payment_date=parse_date_text(op_m.group("date")),
                invoice_number=op_m.group("nf").strip(),
                beneficiary_name=op_m.group("benef").strip(),
                beneficiary_cnpj=valid_cnpj,
                raw_beneficiary_cnpj=raw_cnpj,
                bank_code=op_m.group("bank").strip(),
                bank_agency=op_m.group("ag").strip(),
                bank_account=op_m.group("acc").strip(),
                paid_amount=parse_decimal(op_m.group("amt"), "paid_amount"),
                requester_id=op_m.group("req").strip(),
                approver_id=approver_id,
                approver_role=approver_role,
                approver_limit=approver_limit,
            )
        )
    orders.extend(
        _parse_public_ob_rows(
            text,
            contract_id=contract_id,
            source_document_id=source_document_id,
            patches=patches,
            seen_ops=seen_ops,
        )
    )
    return tuple(orders)


def _parse_expected_glosas(text: str) -> dict[str, Decimal]:
    sec_m = _GLOSA_SECTION_RE.search(text)
    if sec_m is None:
        return {}
    glosas: dict[str, Decimal] = {}
    for row_m in _GLOSA_ROW_RE.finditer(sec_m.group(0)):
        mm, yyyy = row_m.group("comp").split("/")
        val = parse_decimal(row_m.group("glosa"), "glosa")
        if val > Decimal("0.00"):
            glosas[f"{yyyy}-{mm}"] = val
    return glosas


def _classify_certificate_label(label: str) -> CertificateType:
    low = label.casefold()
    if "fgts" in low or "crf" in low:
        return CertificateType.CRF_FGTS
    if "cndt" in low or "trabalhist" in low:
        return CertificateType.CNDT
    if "municipal" in low or "prefeitura" in low:
        return CertificateType.CND_MUNICIPAL
    if "estadual" in low or "sefaz" in low:
        return CertificateType.CND_ESTADUAL
    return CertificateType.CND_FEDERAL


def _parse_certificates(
    text: str,
    *,
    contract_id: str,
    source_document_id: str,
) -> tuple[TaxCertificate, ...]:
    section_m = re.search(
        r"(?i)(?:Certid[õo]es\s+de\s+regularidade|REGULARIDADE\s+FISCAL\s+E\s+GARANTIA)",
        text,
    )
    target_text = text[section_m.start() :] if section_m is not None else text
    end_m = re.search(r"(?im)^\s*\d+\.\s+Resumo\s+financeiro", target_text)
    if end_m is not None:
        target_text = target_text[: end_m.start()]

    certs: list[TaxCertificate] = []
    seen_keys: set[tuple[CertificateType, date]] = set()
    for cert_m in _CERT_ROW_RE.finditer(target_text):
        label = cert_m.group("label").strip()
        cert_type = _classify_certificate_label(label)
        issue_dt = parse_date_text(cert_m.group("issue"))
        expiry_dt = parse_date_text(cert_m.group("expiry"))
        if (cert_type, expiry_dt) in seen_keys:
            continue
        seen_keys.add((cert_type, expiry_dt))
        status_txt = (cert_m.group("status") or "").strip() or None
        certs.append(
            TaxCertificate(
                id=f"cert-{source_document_id}-{cert_type.value.lower()}-{expiry_dt.isoformat()}",
                contract_id=contract_id,
                source_document_id=source_document_id,
                certificate_type=cert_type,
                issue_date=issue_dt,
                expiry_date=expiry_dt,
                raw_status=None if status_txt == "-" else status_txt,
            )
        )
    guar_m = _INSURANCE_GUARANTEE_RE.search(target_text)
    if guar_m is not None:
        issue_dt = parse_date_text(guar_m.group("start"))
        expiry_dt = parse_date_text(guar_m.group("end"))
        if (CertificateType.CONTRACT_GUARANTEE, expiry_dt) not in seen_keys:
            seen_keys.add((CertificateType.CONTRACT_GUARANTEE, expiry_dt))
            renewal_txt = (guar_m.group("renewal") or "").strip() or None
            certs.append(
                TaxCertificate(
                    id=f"cert-{source_document_id}-guarantee-{expiry_dt.isoformat()}",
                    contract_id=contract_id,
                    source_document_id=source_document_id,
                    certificate_type=CertificateType.CONTRACT_GUARANTEE,
                    issue_date=issue_dt,
                    expiry_date=expiry_dt,
                    raw_status=renewal_txt,
                )
            )
    return tuple(certs)


def _parse_financial_summary(
    text: str,
    *,
    contract_id: str,
    source_document_id: str,
) -> DossierFinancialSummary | None:
    if "Resumo financeiro" not in text and "Total líquido pago" not in text:
        return None
    comp_m = _SUMMARY_COMP_RE.search(text)
    contr_m = _SUMMARY_CONTRACTED_RE.search(text)
    gross_m = _SUMMARY_GROSS_RE.search(text)
    inss_m = _SUMMARY_INSS_RE.search(text)
    iss_m = _SUMMARY_ISS_RE.search(text)
    net_m = _SUMMARY_NET_RE.search(text)
    if not any((contr_m, gross_m, net_m)):
        return None
    return DossierFinancialSummary(
        id=f"summary-{source_document_id}",
        contract_id=contract_id,
        source_document_id=source_document_id,
        competences_count=int(comp_m.group(1)) if comp_m is not None else None,
        reported_contracted_total=(
            parse_decimal(contr_m.group(1), "reported_contracted_total")
            if contr_m is not None
            else None
        ),
        reported_gross_total=(
            parse_decimal(gross_m.group(1), "reported_gross_total") if gross_m is not None else None
        ),
        reported_inss_total=(
            parse_decimal(inss_m.group(1), "reported_inss_total") if inss_m is not None else None
        ),
        reported_iss_total=(
            parse_decimal(iss_m.group(1), "reported_iss_total") if iss_m is not None else None
        ),
        reported_net_paid_total=(
            parse_decimal(net_m.group(1), "reported_net_paid_total") if net_m is not None else None
        ),
    )


def _extract_supplemental_rules(
    text: str,
    *,
    contract_id: str,
    source_document_id: str,
    validity: ValidityPeriod,
) -> tuple[ContractRule, ...]:
    rules: list[ContractRule] = []
    # 1. Retenções (Cláusula 5ª)
    ret_m = _CLAUSE_RETENTION_RE.search(text)
    if ret_m is not None:
        excerpt = ret_m.group(1).strip()
        page_num = _page_for_offset(text, ret_m.start(1))
        inss_pct_m = re.search(
            r"(\d+(?:,\d+)?)\s*%[^.]{0,90}(?:previdenci|inss)",
            excerpt,
            re.IGNORECASE | re.DOTALL,
        )
        if inss_pct_m is not None:
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-inss",
                    contract_id=contract_id,
                    rule_type=RuleType.INSS_RETENTION,
                    value=parse_decimal(inss_pct_m.group(1), "inss_rate"),
                    unit="%",
                    validity=validity,
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        clause="Cláusula 5ª",
                        literal_excerpt=excerpt,
                    ),
                    relation=RuleRelation.CREATED,
                )
            )
        iss_pct_m = re.search(
            r"(\d+(?:,\d+)?)\s*%[^.]{0,60}\bISS\b",
            excerpt,
            re.IGNORECASE | re.DOTALL,
        )
        if iss_pct_m is not None:
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-iss",
                    contract_id=contract_id,
                    rule_type=RuleType.ISS_RETENTION,
                    value=parse_decimal(iss_pct_m.group(1), "iss_rate"),
                    unit="%",
                    validity=validity,
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        clause="Cláusula 5ª",
                        literal_excerpt=excerpt,
                    ),
                    relation=RuleRelation.CREATED,
                )
            )

    # 2. Alçada de aprovação (Cláusula 6ª)
    app_m = _CLAUSE_APPROVAL_RE.search(text)
    if app_m is not None:
        excerpt = app_m.group(1).strip()
        page_num = _page_for_offset(text, app_m.start(1))
        val_m = re.search(r"at[ée]\s+R\$\s*([\d.,]+)", excerpt, re.IGNORECASE)
        if val_m is not None:
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-approval",
                    contract_id=contract_id,
                    rule_type=RuleType.APPROVAL_LIMIT,
                    value=parse_decimal(val_m.group(1), "approval_limit"),
                    validity=validity,
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        clause="Cláusula 6ª",
                        literal_excerpt=excerpt,
                    ),
                    relation=RuleRelation.CREATED,
                )
            )

    # 3. Prazo de pagamento (Cláusula 4ª)
    pay_m = _CLAUSE_PAYMENT_RE.search(text)
    if pay_m is not None:
        excerpt = pay_m.group(1).strip()
        page_num = _page_for_offset(text, pay_m.start(1))
        days_m = re.search(r"at[ée]\s+(\d+)\s*(?:\([^)]+\))?\s*dias", excerpt, re.IGNORECASE)
        if days_m is not None:
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-payment-terms",
                    contract_id=contract_id,
                    rule_type=RuleType.PAYMENT_TERMS,
                    quantity=Decimal(days_m.group(1)),
                    unit="dias",
                    validity=validity,
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        clause="Cláusula 4ª",
                        literal_excerpt=excerpt,
                    ),
                    relation=RuleRelation.CREATED,
                )
            )

    # 4. Reajuste / carência (Cláusula 2ª)
    adj_m = _CLAUSE_ADJUSTMENT_RE.search(text)
    if adj_m is not None:
        excerpt = adj_m.group(1).strip()
        idx_m = re.search(r"\b(IPCA(?:/IBGE)?|IGP-?M|INPC)\b", excerpt, re.IGNORECASE)
        months_m = re.search(r"(\d+)\s+meses", excerpt, re.IGNORECASE)
        if idx_m is not None:
            page_num = _page_for_offset(text, adj_m.start(1))
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-adjustment",
                    contract_id=contract_id,
                    rule_type=RuleType.ADJUSTMENT,
                    adjustment_index=idx_m.group(1).upper(),
                    periodicity_months=int(months_m.group(1)) if months_m is not None else 12,
                    validity=validity,
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        clause="Cláusula 2ª",
                        literal_excerpt=excerpt,
                    ),
                    relation=RuleRelation.CREATED,
                )
            )

    # 5. Tabelas de postos mensais (`Posto | Quantidade | Valor mensal por posto`)
    post_sec = _POSTS_TABLE_SECTION_RE.search(text)
    if post_sec is not None:
        sec_offset = post_sec.start("body")
        for row_m in _POSTS_TABLE_ROW_RE.finditer(post_sec.group("body")):
            name = " ".join(row_m.group("name").split())
            if name.casefold().startswith("total"):
                continue
            qty = parse_decimal(row_m.group("qty"), "item_qty")
            unit_price = parse_decimal(row_m.group("unit_price"), "item_unit_price")
            page_num = _page_for_offset(text, sec_offset + row_m.start())
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-item-{_slug(name)}",
                    contract_id=contract_id,
                    rule_type=RuleType.ITEM_PRICE,
                    item_name=name,
                    value=unit_price,
                    quantity=qty,
                    unit="posto",
                    quantity_scope="MONTHLY",
                    validity=validity,
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        literal_excerpt=" ".join(row_m.group(0).split()),
                    ),
                    relation=RuleRelation.CREATED,
                )
            )

    # 6. Tabelas de Ata de Registro de Preços (`Item | Descrição | Unidade | Qtd máxima | Preço`)
    ata_sec = _ATA_TABLE_SECTION_RE.search(text)
    if ata_sec is not None:
        sec_offset = ata_sec.start("body")
        for row_m in _ATA_TABLE_ROW_RE.finditer(ata_sec.group("body")):
            desc = " ".join(row_m.group("desc").split())
            unit = row_m.group("unit").strip().casefold()
            qty = parse_decimal(row_m.group("qty"), "ata_qty")
            unit_price = parse_decimal(row_m.group("price"), "ata_unit_price")
            page_num = _page_for_offset(text, sec_offset + row_m.start())
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-ata-{_slug(desc)[:32]}",
                    contract_id=contract_id,
                    rule_type=RuleType.ITEM_PRICE,
                    item_name=desc,
                    value=unit_price,
                    quantity=qty,
                    unit=unit,
                    quantity_scope="TOTAL",
                    validity=validity,
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        literal_excerpt=" ".join(row_m.group(0).split()),
                    ),
                    relation=RuleRelation.CREATED,
                )
            )

    # 7. Termos aditivos / apostilamentos com itens ou repactuação (B2 e A1)
    aditivo_qty_m = re.finditer(
        r"(?is)acresc(?:ido|ida)\s+o\s+quantitativo[^.\n]{0,80}?de\s+(?P<item>[^\n,]+?),\s*"
        r"passando\s+de\s+\d+[^.\n]{0,40}?\s+para\s+(?P<qty>\d+)[^.\n]{0,60}?"
        r"efeitos\s+a\s+partir\s+de\s+(?P<eff>\d{2}/\d{2}/\d{4})[^.\n]{0,80}?"
        r"R\$\s*(?P<price>\d{1,3}(?:\.\d{3})*,\d{2})",
        text,
    )
    for idx_aq, aq_m in enumerate(aditivo_qty_m, start=1):
        item_label = re.sub(
            r"^(?:postos?\s+de\s+)", "", aq_m.group("item").strip(), flags=re.IGNORECASE
        )
        eff_dt = parse_date_text(aq_m.group("eff"))
        page_num = _page_for_offset(text, aq_m.start())
        rules.append(
            ContractRule(
                id=f"{source_document_id}-dossier-add-qty-{idx_aq}-{_slug(item_label)[:24]}",
                contract_id=contract_id,
                rule_type=RuleType.ITEM_PRICE,
                item_name=item_label.capitalize(),
                value=parse_decimal(aq_m.group("price"), "add_unit_price"),
                quantity=parse_decimal(aq_m.group("qty"), "add_qty"),
                unit="posto",
                quantity_scope="MONTHLY",
                validity=ValidityPeriod(start=eff_dt),
                review_status=ReviewStatus.PENDING_REVIEW,
                verification_score=Decimal("1.00"),
                evidence=Evidence(
                    source_document_id=source_document_id,
                    page=page_num,
                    literal_excerpt=" ".join(aq_m.group(0).split()),
                ),
                relation=RuleRelation.ALTERED,
            )
        )

    ext_m_iter = re.finditer(
        r"(?is)prorrogada\s+a\s+vig[êe]ncia[^.\n]{0,120}?de\s+"
        r"(?P<start>\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*(?P<end>\d{2}/\d{2}/\d{4})",
        text,
    )
    for idx_ex, ex_m in enumerate(ext_m_iter, start=1):
        page_num = _page_for_offset(text, ex_m.start())
        rules.append(
            ContractRule(
                id=f"{source_document_id}-dossier-ext-{idx_ex}",
                contract_id=contract_id,
                rule_type=RuleType.EXTENSION,
                validity=ValidityPeriod(
                    start=parse_date_text(ex_m.group("start")),
                    end=parse_date_text(ex_m.group("end")),
                ),
                review_status=ReviewStatus.PENDING_REVIEW,
                verification_score=Decimal("1.00"),
                evidence=Evidence(
                    source_document_id=source_document_id,
                    page=page_num,
                    literal_excerpt=" ".join(ex_m.group(0).split()),
                ),
                relation=RuleRelation.CREATED,
            )
        )

    repac_m = re.search(
        r"(?is)repactuad[oa]s\s+os\s+pre[çc]os[^.\n]{0,140}?"
        r"efeitos\s+a\s+partir\s+de\s+(?P<eff>\d{2}/\d{2}/\d{4})\s*:\s*(?P<items>[^.]+)\.",
        text,
    )
    if repac_m is not None:
        eff_dt = parse_date_text(repac_m.group("eff"))
        page_num = _page_for_offset(text, repac_m.start())
        kept_qty: dict[str, Decimal] = {}
        qty_ctx_m = re.search(
            r"(?is)Mantido\s+o\s+quantitativo\s+de\s+(?P<q1>\d+)[^.\n]{0,40}?servente"
            r"[^.\n]{0,40}?\s+e\s+(?P<q2>\d+)[^.\n]{0,40}?encarregado",
            text[repac_m.end() : repac_m.end() + 400],
        )
        if qty_ctx_m is not None:
            kept_qty["servente"] = parse_decimal(qty_ctx_m.group("q1"), "kept_q1")
            kept_qty["encarregado"] = parse_decimal(qty_ctx_m.group("q2"), "kept_q2")
        for idx_rp, part in enumerate(repac_m.group("items").split(";"), start=1):
            pm = re.search(
                r"(?is)^\s*(?P<name>[^:,]+?)\s*,\s*R\$\s*(?P<price>\d{1,3}(?:\.\d{3})*,\d{2})",
                part,
            )
            if pm is None:
                continue
            item_nm = " ".join(pm.group("name").split())
            inferred_qty: Decimal | None = None
            for k_tok, k_qty in kept_qty.items():
                if k_tok in item_nm.casefold():
                    inferred_qty = k_qty
                    break
            rules.append(
                ContractRule(
                    id=f"{source_document_id}-dossier-repac-{idx_rp}-{_slug(item_nm)[:24]}",
                    contract_id=contract_id,
                    rule_type=RuleType.ITEM_PRICE,
                    item_name=item_nm.capitalize(),
                    value=parse_decimal(pm.group("price"), "repac_price"),
                    quantity=inferred_qty,
                    unit="posto" if inferred_qty is not None else None,
                    quantity_scope="MONTHLY" if inferred_qty is not None else None,
                    validity=ValidityPeriod(start=eff_dt),
                    review_status=ReviewStatus.PENDING_REVIEW,
                    verification_score=Decimal("1.00"),
                    evidence=Evidence(
                        source_document_id=source_document_id,
                        page=page_num,
                        literal_excerpt=" ".join(repac_m.group(0).split()),
                    ),
                    relation=RuleRelation.ALTERED,
                )
            )

    return tuple(rules)


def _extract_contract_validity_dates(text: str) -> tuple[date | None, date | None]:
    for idx, pattern in enumerate(_VALIDITY_CLAUSE_PRIORITY_PATTERNS):
        for match in pattern.finditer(text):
            if idx == len(_VALIDITY_CLAUSE_PRIORITY_PATTERNS) - 1:
                prefix = text[max(0, match.start() - 80) : match.start()]
                if _INSURANCE_CONTEXT_RE.search(prefix) is not None:
                    continue
            start_dt = parse_date_text(match.group(1))
            end_dt = parse_date_text(match.group(2))
            if start_dt <= end_dt:
                return start_dt, end_dt
    return None, None


def extract_dossier_artifacts(
    text: str,
    *,
    contract_id: str,
    source_document_id: str,
) -> DossierExtractionResult:
    """Extrai de forma determinística metadados bancários, NFs, OPs, certidões e resumos."""
    if not text.strip():
        return DossierExtractionResult(
            contract_metadata=ContractDossierMetadata(),
            invoice_patches={},
            payment_orders=(),
            tax_certificates=(),
            financial_summary=None,
            supplemental_rules=(),
        )

    bank_m = _BANK_INFO_RE.search(text)
    nat_m = _LEGAL_NATURE_RE.search(text)
    validity_start, validity_end = _extract_contract_validity_dates(text)

    contract_meta = ContractDossierMetadata(
        legal_nature=nat_m.group(1).strip() if nat_m is not None else None,
        bank_code=bank_m.group(1).strip() if bank_m is not None else None,
        bank_agency=bank_m.group(2).strip() if bank_m is not None else None,
        bank_account=bank_m.group(3).strip() if bank_m is not None else None,
        validity_start=validity_start,
        validity_end=validity_end,
    )

    invoice_patches = _parse_invoice_sections(text)
    payment_orders = _parse_payment_orders(
        text,
        contract_id=contract_id,
        source_document_id=source_document_id,
        patches=invoice_patches,
    )
    tax_certificates = _parse_certificates(
        text, contract_id=contract_id, source_document_id=source_document_id
    )
    financial_summary = _parse_financial_summary(
        text, contract_id=contract_id, source_document_id=source_document_id
    )
    expected_glosas = _parse_expected_glosas(text)
    supplemental_rules = _extract_supplemental_rules(
        text,
        contract_id=contract_id,
        source_document_id=source_document_id,
        validity=ValidityPeriod(start=validity_start, end=validity_end),
    )

    return DossierExtractionResult(
        contract_metadata=contract_meta,
        invoice_patches=invoice_patches,
        payment_orders=payment_orders,
        tax_certificates=tax_certificates,
        financial_summary=financial_summary,
        supplemental_rules=supplemental_rules,
        expected_glosas_by_period=expected_glosas,
    )


def has_structured_financial_sections(text: str) -> bool:
    """Indica se o texto contém quadros de OP, certidões, frequência ou resumo financeiro."""
    markers = (
        "ordens de pagamento",
        "ordens bancárias",
        "ordens bancarias",
        "certidões de regularidade",
        "certidoes de regularidade",
        "regularidade fiscal e garantia",
        "relatório mensal de frequência",
    )
    low = text.casefold()
    return any(marker in low for marker in markers)


def has_supplemental_contract_rules(text: str) -> bool:
    """Indica se o texto traz tabelas determinísticas de postos/itens, aditivos ou retenções."""
    if has_structured_financial_sections(text):
        return True
    return bool(
        _POSTS_TABLE_SECTION_RE.search(text)
        or _ATA_TABLE_SECTION_RE.search(text)
        or _CLAUSE_RETENTION_RE.search(text)
        or re.search(r"(?is)repactuad[oa]s\s+os\s+pre[çc]os", text)
        or re.search(r"(?is)acresc(?:ido|ida)\s+o\s+quantitativo", text)
    )
