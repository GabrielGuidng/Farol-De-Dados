"""Verificações determinísticas de cadastro, retenções tributárias e justificativa de reajuste."""

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from backend.motor.adjustment import add_months
from backend.motor.calculator import round_money, subtract_money
from backend.motor.formatting import describe_rule_source, format_money, format_percent
from backend.motor.results import RuleSelection, SelectionStatus
from backend.schemas import (
    Amendment,
    AppliedRule,
    Check,
    CheckResult,
    Contract,
    ContractRule,
    DivergenceDirection,
    Invoice,
    RuleType,
)

SUPPLIER_IDENTITY_CHECK = "supplier_identity"
INSS_RETENTION_CHECK = "inss_retention"
ISS_RETENTION_CHECK = "iss_retention"
UNAPPROVED_ADJUSTMENT_CHECK = "unapproved_adjustment"

_STOP_WORDS = frozenset({"de", "da", "do", "das", "dos", "e", "em"})
_PLACEHOLDER_SUPPLIER_NAMES = frozenset(
    {
        "fornecedor identificado no documento",
        "fornecedor cnpj nao informado no arquivo",
        "fornecedor",
        "contratada",
        "contratado",
        "empresa contratada",
    }
)
_ABBREV_CANONICAL: dict[str, str] = {
    "empreend": "empreendimentos",
    "emp": "empreendimentos",
    "serv": "servicos",
    "com": "comercio",
    "coml": "comercial",
    "ind": "industria",
    "adm": "administracao",
    "cons": "conservacao",
    "log": "logistica",
    "supr": "suprimentos",
    "imp": "importacao",
    "exp": "exportacao",
}
_LEGAL_SUFFIX_MAP: dict[str, str] = {
    "ltda": "LTDA",
    "limitada": "LTDA",
    "eireli": "EIRELI",
    "me": "ME",
    "epp": "EPP",
    "ei": "EI",
    "sa": "SA",
    "slu": "SLU",
}
_ADJUSTMENT_CLAIM_RE = re.compile(
    r"\b(?:reajuste|repactuac|majoracao|correcao\s+monetaria|acrescimo\s+de\s+reajuste)\b",
    re.IGNORECASE,
)


def _strip_accents_lower(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.casefold()).strip()


def _extract_legal_form(name: str, explicit_nature: str | None) -> str | None:
    if explicit_nature:
        norm_nat = _strip_accents_lower(explicit_nature)
        if "limitada" in norm_nat or "ltda" in norm_nat:
            return "LTDA"
        if "eireli" in norm_nat:
            return "EIRELI"
        if "anonima" in norm_nat or "s a" in norm_nat:
            return "SA"
        if "microempresa" in norm_nat or re.search(r"\bme\b", norm_nat):
            return "ME"
        if "pequeno porte" in norm_nat or "epp" in norm_nat:
            return "EPP"
    tokens = _strip_accents_lower(name).split()
    for token in reversed(tokens):
        mapped = _LEGAL_SUFFIX_MAP.get(token)
        if mapped is not None:
            return mapped
    return None


def _core_name_tokens(name: str) -> tuple[str, ...]:
    tokens = _strip_accents_lower(name).split()
    cleaned: list[str] = []
    for token in tokens:
        if token in _STOP_WORDS or token in _LEGAL_SUFFIX_MAP:
            continue
        cleaned.append(token)
    return tuple(cleaned)


def _tokens_compatible(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if left == right:
        return True
    if len(left) != len(right):
        return False
    for a, b in zip(left, right, strict=True):
        ca = _ABBREV_CANONICAL.get(a, a)
        cb = _ABBREV_CANONICAL.get(b, b)
        if ca == cb:
            continue
        # Aceita abreviações comuns (ex.: "serv" para "servicos"), não erros de grafia.
        shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
        if len(shorter) >= 4 and longer.startswith(shorter):
            continue
        return False
    return True


def _is_placeholder_supplier(name: str) -> bool:
    return _strip_accents_lower(name) in _PLACEHOLDER_SUPPLIER_NAMES


def check_supplier_identity(contract: Contract, invoice: Invoice) -> Check | None:
    """
    Compara razão social e natureza jurídica da cobrança com o cadastro do contrato (P-03).
    Devolve None quando compatível ou quando o nome no contrato é marcador genérico
    ("Não li" != "Diverge"), evitando falso positivo em lote.
    """
    if _is_placeholder_supplier(contract.supplier):
        return None
    contract_form = _extract_legal_form(contract.supplier, contract.legal_nature)
    invoice_form = _extract_legal_form(invoice.supplier, invoice.supplier_legal_nature)
    contract_core = _core_name_tokens(contract.supplier)
    invoice_core = _core_name_tokens(invoice.supplier)

    form_differs = (
        contract_form is not None and invoice_form is not None and contract_form != invoice_form
    )
    name_differs = bool(contract_core and invoice_core) and not _tokens_compatible(
        contract_core, invoice_core
    )

    if not form_differs and not name_differs:
        return None

    reasons: list[str] = []
    if form_differs:
        reasons.append(f"natureza jurídica {invoice_form} vs. {contract_form} no contrato")
    if name_differs:
        reasons.append(f"razão social '{invoice.supplier}' vs. '{contract.supplier}'")
    memory = [
        f"razão social no contrato = {contract.supplier}",
        f"razão social na cobrança = {invoice.supplier}",
    ]
    if contract.legal_nature or invoice.supplier_legal_nature:
        memory.append(
            "natureza jurídica = "
            f"{invoice.supplier_legal_nature or invoice_form or 'não informada'} "
            f"(esperada: {contract.legal_nature or contract_form or 'não informada'})"
        )
    return Check(
        name=SUPPLIER_IDENTITY_CHECK,
        result=CheckResult.REVIEW,
        direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
        message=(
            "Divergência cadastral do emitente: "
            + "; ".join(reasons)
            + ". Conferir alteração contratual e cadastro na Receita Federal."
        ),
        calculation_memory=tuple(memory),
    )


def _check_single_retention(
    *,
    check_name: str,
    tax_label: str,
    expected_rule_type: RuleType,
    withheld: Decimal | None,
    gross_total: Decimal,
    selection: RuleSelection,
    tolerance: Decimal,
) -> tuple[Check | None, AppliedRule | None]:
    if withheld is None or gross_total <= Decimal("0"):
        return None, None
    if selection.status is not SelectionStatus.SELECTED or selection.rule is None:
        return None, None
    rule = selection.rule
    if rule.rule_type is not expected_rule_type or rule.value is None:
        return None, None

    rate_pct = rule.value
    expected = round_money(gross_total * rate_pct / Decimal("100"))
    diff = subtract_money(withheld, expected)
    applied = AppliedRule(rule_id=rule.id, version=rule.version)
    actual_pct = round_money(withheld * Decimal("100") / gross_total)
    memory = [
        f"valor bruto da nota = {format_money(gross_total)}",
        f"alíquota contratual de {tax_label} = {format_percent(rate_pct)}",
        f"retenção esperada = {format_money(expected)}",
        f"retenção destacada = {format_money(withheld)} ({format_percent(actual_pct)})",
        f"diferença na retenção = retido − esperado = {format_money(diff)}",
    ]
    source = f"Fonte da regra: {describe_rule_source(rule)}."
    trusted = rule.review_status.is_trusted

    if abs(diff) <= tolerance:
        if trusted:
            return (
                Check(
                    name=check_name,
                    result=CheckResult.OK,
                    message=(
                        f"Retenção de {tax_label} ({format_money(withheld)}, "
                        f"{format_percent(rate_pct)}) confere com o contrato. {source}"
                    ),
                    calculation_memory=tuple(memory),
                    rule_id=rule.id,
                ),
                applied,
            )
        return (
            Check(
                name=check_name,
                result=CheckResult.REVIEW,
                direction=DivergenceDirection.UNVERIFIABLE,
                message=(
                    f"Retenção de {tax_label} ({format_money(withheld)}) confere com a regra, "
                    f"mas a regra ainda não foi revisada por uma pessoa. {source}"
                ),
                calculation_memory=tuple(memory),
                rule_id=rule.id,
            ),
            applied,
        )

    shortfall_note = (
        f"retenção a menor de {format_money(abs(diff))}, elevando indevidamente o valor líquido"
        if diff < Decimal("0")
        else f"retenção a maior de {format_money(abs(diff))}"
    )
    direction = (
        DivergenceDirection.UNDERCHARGE if diff < Decimal("0") else DivergenceDirection.OVERCHARGE
    )
    if trusted:
        return (
            Check(
                name=check_name,
                result=CheckResult.DIVERGENCE,
                direction=direction,
                message=(
                    f"Divergência na retenção de {tax_label}: destacado {format_money(withheld)} "
                    f"({format_percent(actual_pct)}) contra {format_money(expected)} "
                    f"({format_percent(rate_pct)}) exigidos — {shortfall_note}. {source}"
                ),
                calculation_memory=tuple(memory),
                rule_id=rule.id,
            ),
            applied,
        )
    return (
        Check(
            name=check_name,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
            message=(
                f"Possível divergência na retenção de {tax_label} (regra não revisada): "
                f"destacado {format_money(withheld)} contra {format_money(expected)} — "
                f"{shortfall_note}. {source}"
            ),
            calculation_memory=tuple(memory),
            rule_id=rule.id,
        ),
        applied,
    )


def check_tax_retentions(
    invoice: Invoice,
    *,
    inss_selection: RuleSelection,
    iss_selection: RuleSelection,
    tolerance: Decimal,
) -> tuple[list[Check], list[AppliedRule]]:
    """Confere retenções de INSS e ISS destacadas na nota contra as alíquotas contratuais."""
    checks: list[Check] = []
    applied: list[AppliedRule] = []
    for check_name, label, rtype, withheld, sel in (
        (
            INSS_RETENTION_CHECK,
            "INSS",
            RuleType.INSS_RETENTION,
            invoice.inss_withheld,
            inss_selection,
        ),
        (
            ISS_RETENTION_CHECK,
            "ISS",
            RuleType.ISS_RETENTION,
            invoice.iss_withheld,
            iss_selection,
        ),
    ):
        chk, app = _check_single_retention(
            check_name=check_name,
            tax_label=label,
            expected_rule_type=rtype,
            withheld=withheld,
            gross_total=invoice.total,
            selection=sel,
            tolerance=tolerance,
        )
        if chk is not None:
            checks.append(chk)
        if app is not None:
            applied.append(app)
    return checks, applied


def check_unapproved_adjustment_claim(
    contract: Contract,
    invoice: Invoice,
    adjustment_rules: Sequence[ContractRule],
    amendments: Sequence[Amendment],
) -> Check | None:
    """
    Detecta descrição de nota alegando reajuste/majoração sem termo aditivo aplicável
    ou antes do decurso do prazo mínimo de carência contratual (P-01 / Cláusula 2ª e 8ª).
    """
    texts = [invoice.service_description or "", *(item.description for item in invoice.items)]
    combined = " ".join(part for part in texts if part.strip())
    if not combined:
        return None
    normalized = _strip_accents_lower(combined)
    if _ADJUSTMENT_CLAIM_RE.search(normalized) is None:
        return None

    has_price_amendment = any(
        amend.effective_date <= invoice.reference_period.last_day for amend in amendments
    )
    if has_price_amendment:
        return None

    periodicity_months = 12
    for rule in adjustment_rules:
        if rule.periodicity_months is not None:
            periodicity_months = rule.periodicity_months
            break

    start_date = contract.validity.start
    grace_detail = ""
    if start_date is not None:
        first_allowed = add_months(start_date, periodicity_months)
        if invoice.reference_period.last_day < first_allowed:
            grace_detail = (
                f" Além disso, o contrato proíbe reajuste nos primeiros {periodicity_months} meses "
                f"(vigência desde {start_date.strftime('%d/%m/%Y')}; carência até "
                f"{first_allowed.strftime('%d/%m/%Y')})."
            )

    return Check(
        name=UNAPPROVED_ADJUSTMENT_CHECK,
        result=CheckResult.DIVERGENCE,
        direction=DivergenceDirection.OVERCHARGE,
        message=(
            "A descrição da nota menciona reajuste/majoração de valores, mas não existe "
            "termo aditivo formalizado vigente para a competência." + grace_detail
        ),
        calculation_memory=(
            f"descrição informada = {combined.strip()}",
            "aditivos aplicáveis na competência = 0",
        ),
    )
