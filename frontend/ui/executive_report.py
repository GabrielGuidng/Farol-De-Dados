"""Motor do Relatório Executivo de Auditoria e exportadores (HTML/PDF, CSV, XLSX)."""

import csv
import html
import io
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from backend.motor import (
    HOURLY_LIMIT_CHECK,
    HOURS_WITHOUT_OS_CHECK,
    INSS_RETENTION_CHECK,
    ISS_RETENTION_CHECK,
    ITEM_QUANTITY_CUMULATIVE_CHECK,
    ITEM_QUANTITY_MONTHLY_CHECK,
    ITEM_UNIT_PRICE_CHECK,
    ITEMS_ARITHMETIC_CHECK,
    MISSING_GLOSA_CHECK,
    SUPPLIER_IDENTITY_CHECK,
    UNAPPROVED_ADJUSTMENT_CHECK,
    UNREGISTERED_ITEM_CHECK,
    WRITTEN_AMOUNT_CHECK,
    PaymentCycleAuditResult,
)
from backend.motor.duplicates import DUPLICATE_CHECK
from backend.persistencia import StoredAudit
from backend.schemas import (
    AuditClassification,
    Check,
    CheckResult,
    Contract,
    ContractRule,
    DivergenceDirection,
    Invoice,
    ReviewStatus,
    RuleType,
    is_valid_cnpj,
    most_severe,
)
from frontend.ui.components import describe_rule_value
from frontend.ui.formatting import (
    AUDIT_CLASSIFICATION_BADGES,
    AUDIT_REVIEW_STATUS_BADGES,
    CHECK_RESULT_BADGES,
    INVOICE_SOURCE_LABELS,
    RULE_TYPE_LABELS,
    BadgeToneLiteral,
    clean_technical_ids_in_message,
    format_brl,
    format_competence_pt,
    format_competence_short,
    format_date_br,
    format_signed_brl,
    humanize_check_name,
    natural_invoice_sort_key,
    natural_number_sort_tuple,
    parse_decimal_input,
    period_sort_tuple,
)
from frontend.ui.tokens import THEMES

FindingSeverity = Literal["Alta", "Média", "Baixa"]

_ZERO = Decimal("0.00")
_PROVISIONAL_CNPJ = "00000000000191"
_SEVERITY_RANK: dict[FindingSeverity, int] = {"Alta": 0, "Média": 1, "Baixa": 2}
_CHECK_PRIORITY: dict[str, int] = {
    "contract_link": 0,
    "contract_validity": 1,
    ITEMS_ARITHMETIC_CHECK: 2,
    MISSING_GLOSA_CHECK: 2,
    UNREGISTERED_ITEM_CHECK: 2,
    ITEM_UNIT_PRICE_CHECK: 3,
    ITEM_QUANTITY_MONTHLY_CHECK: 3,
    ITEM_QUANTITY_CUMULATIVE_CHECK: 3,
    "monthly_price": 3,
    "overage_price": 4,
    HOURLY_LIMIT_CHECK: 5,
    UNAPPROVED_ADJUSTMENT_CHECK: 6,
    INSS_RETENTION_CHECK: 7,
    ISS_RETENTION_CHECK: 8,
    "overage_quantity": 9,
    HOURS_WITHOUT_OS_CHECK: 10,
    WRITTEN_AMOUNT_CHECK: 11,
    "item_classification": 12,
    "items_total": 13,
    "rule_timeline": 14,
    "amendments_reviewed": 15,
    "rules_start_date": 16,
    "periodic_adjustment": 17,
    SUPPLIER_IDENTITY_CHECK: 18,
    "competence_inferred": 19,
    DUPLICATE_CHECK: 20,
}
_PLACEHOLDER_SUPPLIER_NAMES = frozenset(
    {
        "fornecedor identificado no documento",
        "fornecedor (cnpj não informado no arquivo)",
        "empresa contratante (importação direta)",
        "fornecedor",
        "contratada",
    }
)
_SAFETY_LOCK_CADASTRO_PROBLEMS = frozenset(
    {
        "CNPJ de fornecedor diferente do contrato",
        "Razão social / natureza jurídica diverge do contrato",
        "Emitida por filial (CNPJ diferente da matriz)",
    }
)


@dataclass(frozen=True)
class ReadingCoverageItem:
    """Indicador compacto do que foi lido com êxito no contrato antes da auditoria."""

    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class AuditFinding:
    """Linha acionável na Tabela de Achados, ordenada por gravidade e competência."""

    severity: FindingSeverity
    invoice_numbers: tuple[str, ...]
    invoice_ids: tuple[str, ...]
    competence: str
    sort_period: tuple[int, int]
    sort_number: tuple[int, int, str]
    problem: str
    amount_display: str
    risk_amount: Decimal | None
    suggested_action: str

    @property
    def invoices_label(self) -> str:
        """Formata lista de notas afetadas (`1046 e 1047`, até 6 listadas ou resumo)."""
        if not self.invoice_numbers:
            return "—"
        if len(self.invoice_numbers) == 1:
            return self.invoice_numbers[0]
        if len(self.invoice_numbers) == 2:
            return f"{self.invoice_numbers[0]} e {self.invoice_numbers[1]}"
        if len(self.invoice_numbers) <= 6:
            return f"{', '.join(self.invoice_numbers[:-1])} e {self.invoice_numbers[-1]}"
        head = ", ".join(self.invoice_numbers[:3])
        return f"{len(self.invoice_numbers)} NFs ({head}, …, {self.invoice_numbers[-1]})"


@dataclass(frozen=True)
class CompetenceSemaphoreRow:
    """Resumo mensal cronológico com semáforo acessível."""

    period_iso: str
    competence_pt: str
    invoices_count: int
    expected_total: Decimal | None
    charged_total: Decimal
    difference_total: Decimal | None
    tone: BadgeToneLiteral
    icon: str
    status_label: str


@dataclass(frozen=True)
class AppendixCheckItem:
    """Verificação detalhada exibida no apêndice técnico das notas com achado."""

    check_label: str
    status_label: str
    tone: BadgeToneLiteral
    message: str
    memory_lines: tuple[str, ...]


@dataclass(frozen=True)
class AppendixClauseQuote:
    """Citação literal da regra/cláusula envolvida na nota com achado."""

    rule_label: str
    location_label: str
    literal_excerpt: str


@dataclass(frozen=True)
class AppendixEntry:
    """Bloco técnico completo (apenas para notas que tiveram achado)."""

    invoice_id: str
    invoice_number: str
    competence_pt: str
    supplier: str
    classification_label: str
    tone: BadgeToneLiteral
    expected_display: str
    charged_display: str
    difference_display: str
    checks: tuple[AppendixCheckItem, ...]
    clauses: tuple[AppendixClauseQuote, ...]
    severity_label: FindingSeverity | None = None


@dataclass(frozen=True)
class ContractSectionReport:
    """Sub-relatório escopado de um contrato dentro de um lote multicontrato."""

    contract: Contract
    summary: "ExecutiveReportSummary"


@dataclass(frozen=True)
class ExecutiveReportSummary:
    """Pacote completo e imutável do Relatório Executivo de Auditoria."""

    total_invoices: int
    findings_count: int
    action_invoices_count: int
    clean_invoices_count: int
    clean_invoice_numbers: tuple[str, ...]
    exposure_total: Decimal
    charged_total: Decimal
    expected_total: Decimal | None
    verdict_tone: BadgeToneLiteral
    verdict_headline: str
    verdict_lines: tuple[str, ...]
    findings: tuple[AuditFinding, ...]
    competence_rows: tuple[CompetenceSemaphoreRow, ...]
    clean_summary_line: str
    appendix_entries: tuple[AppendixEntry, ...]
    reading_coverage: tuple[ReadingCoverageItem, ...] = ()
    reading_warnings: tuple[str, ...] = ()
    contract_sections: tuple[ContractSectionReport, ...] = ()


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _format_rule_location_short(rule: ContractRule, doc_names: dict[str, str] | None) -> str:
    doc_name = (
        doc_names.get(rule.evidence.source_document_id, rule.evidence.source_document_id)
        if doc_names
        else rule.evidence.source_document_id
    )
    parts: list[str] = [doc_name]
    if rule.evidence.clause:
        parts.append(rule.evidence.clause)
    if rule.evidence.page is not None:
        parts.append(f"pág. {rule.evidence.page}")
    elif rule.evidence.paragraph:
        parts.append(f"parág. {rule.evidence.paragraph}")
    return " • ".join(parts)


def _group_duplicate_components(
    audited_items: Sequence[tuple[Invoice, StoredAudit]],
) -> list[list[tuple[Invoice, StoredAudit]]]:
    """Agrupa cobranças com alerta `duplicate_invoice` via componentes conexos."""
    dup_items: list[tuple[Invoice, StoredAudit]] = []
    for pair in audited_items:
        has_dup = any(
            chk.name == DUPLICATE_CHECK and chk.result is not CheckResult.OK
            for chk in pair[1].result.checks
        )
        if has_dup:
            dup_items.append(pair)

    if not dup_items:
        return []

    by_id = {inv.id: (inv, aud) for inv, aud in dup_items}
    adj: dict[str, set[str]] = {inv.id: set() for inv, _ in dup_items}

    ids = list(by_id.keys())
    for i, left_id in enumerate(ids):
        left_inv, left_aud = by_id[left_id]
        left_dup_text = "\n".join(
            line
            for chk in left_aud.result.checks
            if chk.name == DUPLICATE_CHECK
            for line in (chk.message, *chk.calculation_memory)
        )
        for right_id in ids[i + 1 :]:
            right_inv, right_aud = by_id[right_id]
            right_dup_text = "\n".join(
                line
                for chk in right_aud.result.checks
                if chk.name == DUPLICATE_CHECK
                for line in (chk.message, *chk.calculation_memory)
            )
            linked_by_id = right_id in left_dup_text or left_id in right_dup_text
            same_contract = (
                left_inv.contract_id is not None and left_inv.contract_id == right_inv.contract_id
            )
            same_number = left_inv.number.strip().casefold() == right_inv.number.strip().casefold()
            same_period_val = (
                left_inv.reference_period == right_inv.reference_period
                and left_inv.total == right_inv.total
            )
            if linked_by_id or (same_contract and (same_number or same_period_val)):
                adj[left_id].add(right_id)
                adj[right_id].add(left_id)

    visited: set[str] = set()
    components: list[list[tuple[Invoice, StoredAudit]]] = []
    for root_id in ids:
        if root_id in visited:
            continue
        stack = [root_id]
        comp_ids: list[str] = []
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            comp_ids.append(cur)
            stack.extend(sorted(adj[cur] - visited))
        comp = sorted(
            (by_id[cid] for cid in comp_ids), key=lambda p: natural_invoice_sort_key(p[0])
        )
        components.append(comp)
    return components


def _extract_retention_shortfall(chk: Check) -> Decimal | None:
    if chk.direction is not DivergenceDirection.UNDERCHARGE:
        return None
    for line in reversed(chk.calculation_memory):
        if "diferença na retenção" in line.casefold() and "=" in line:
            tail = line.rsplit("=", 1)[-1].strip()
            try:
                val = parse_decimal_input(tail)
            except (InvalidOperation, ValueError):
                continue
            return abs(val) if val != _ZERO else None
    return None


def _extract_memory_money(chk: Check, prefix: str) -> Decimal | None:
    prefix_low = prefix.casefold()
    for line in reversed(chk.calculation_memory):
        if prefix_low in line.casefold() and "=" in line:
            tail = line.rsplit("=", 1)[-1].strip()
            try:
                val = parse_decimal_input(tail)
            except (InvalidOperation, ValueError):
                continue
            return abs(val) if val != _ZERO else None
    return None


def _extract_signed_memory_money(chk: Check, prefix: str) -> Decimal | None:
    prefix_low = prefix.casefold()
    for line in reversed(chk.calculation_memory):
        if prefix_low in line.casefold() and "=" in line:
            tail = line.rsplit("=", 1)[-1].strip()
            try:
                val = parse_decimal_input(tail)
            except (InvalidOperation, ValueError):
                continue
            return val if val != _ZERO else None
    return None


def _build_reading_diagnostics(
    contracts: Sequence[Contract],
    rules: Sequence[ContractRule],
) -> tuple[tuple[ReadingCoverageItem, ...], tuple[str, ...]]:
    """Resume o que foi lido do contrato e gera um único aviso por falha de leitura."""
    if not contracts and not rules:
        return (), ()

    active_rules = [r for r in rules if r.review_status is not ReviewStatus.REJECTED]
    coverage: list[ReadingCoverageItem] = []
    warnings: list[str] = []

    if contracts:
        has_real_cnpj = all(
            c.supplier_cnpj != _PROVISIONAL_CNPJ and is_valid_cnpj(c.supplier_cnpj)
            for c in contracts
        )
        has_real_name = all(
            c.supplier.strip().casefold() not in _PLACEHOLDER_SUPPLIER_NAMES for c in contracts
        )
        if has_real_cnpj and has_real_name:
            sup_ok = True
            sup_detail = "Lido"
        elif has_real_cnpj:
            sup_ok = False
            sup_detail = "Parcial"
            warnings.append(
                "Razão social do fornecedor não identificada no contrato (apenas CNPJ lido)."
            )
        else:
            sup_ok = False
            sup_detail = "Não lido"
            warnings.append(
                "Não foi possível ler o CNPJ da contratada no contrato — "
                "a conferência de CNPJ contra o contrato foi omitida."
            )
        coverage.append(
            ReadingCoverageItem(
                label="Fornecedor / CNPJ",
                ok=sup_ok,
                detail=sup_detail,
            )
        )

        has_validity = all(
            c.validity.start is not None or c.validity.end is not None for c in contracts
        )
        coverage.append(
            ReadingCoverageItem(
                label="Vigência do contrato",
                ok=has_validity,
                detail="Lida" if has_validity else "Não lida",
            )
        )
        if not has_validity:
            warnings.append(
                "Não foi possível ler o período de vigência no contrato — "
                "a conferência de prazo contratual foi omitida."
            )

    has_price = any(
        r.rule_type
        in (RuleType.MONTHLY_PRICE, RuleType.ITEM_PRICE, RuleType.OVERAGE_PRICE, RuleType.FRANCHISE)
        for r in active_rules
    )
    coverage.append(
        ReadingCoverageItem(
            label="Preço / Itens",
            ok=has_price,
            detail="Lido" if has_price else "Não lido",
        )
    )
    if not has_price:
        warnings.append(
            "Nenhuma regra de preço mensal foi extraída do contrato — "
            "a comparação de preço unitário/mensal foi omitida."
        )

    has_retentions = any(
        r.rule_type in (RuleType.INSS_RETENTION, RuleType.ISS_RETENTION) for r in active_rules
    )
    coverage.append(
        ReadingCoverageItem(
            label="Retenções (INSS/ISS)",
            ok=has_retentions,
            detail="Lidas" if has_retentions else "Não lidas",
        )
    )

    has_adjustment = any(r.rule_type is RuleType.ADJUSTMENT for r in active_rules)
    coverage.append(
        ReadingCoverageItem(
            label="Reajuste contratual",
            ok=has_adjustment,
            detail="Lido" if has_adjustment else "Não lido",
        )
    )

    return tuple(coverage), tuple(warnings)


def _group_identical_findings(findings: Sequence[AuditFinding]) -> list[AuditFinding]:
    """Consolida achados idênticos sem valor em risco numa única linha com lista de notas."""
    passthrough: list[AuditFinding] = []
    buckets: dict[tuple[FindingSeverity, str, str], list[AuditFinding]] = {}
    for f in findings:
        if f.risk_amount is not None:
            passthrough.append(f)
            continue
        key = (f.severity, f.problem, f.suggested_action)
        buckets.setdefault(key, []).append(f)

    grouped: list[AuditFinding] = list(passthrough)
    for bucket in buckets.values():
        if len(bucket) == 1:
            grouped.append(bucket[0])
            continue
        pairs: dict[str, tuple[str, str]] = {}
        seen_ids: list[str] = []
        comps: set[str] = set()
        for item in bucket:
            comps.add(item.competence)
            for num, iid in zip(item.invoice_numbers, item.invoice_ids, strict=False):
                if iid not in pairs:
                    pairs[iid] = (num, iid)
                    seen_ids.append(iid)
            for num in item.invoice_numbers:
                if not item.invoice_ids and num not in pairs:
                    pairs[num] = (num, "")
        sorted_nums = sorted(
            dict.fromkeys(num for item in bucket for num in item.invoice_numbers),
            key=natural_number_sort_tuple,
        )
        ordered_ids = tuple(
            dict.fromkeys(iid for item in bucket for iid in item.invoice_ids if iid)
        )
        comp_label = next(iter(comps)) if len(comps) == 1 else "Várias competências"
        first = min(bucket, key=lambda it: (it.sort_period, it.sort_number))
        grouped.append(
            AuditFinding(
                severity=first.severity,
                invoice_numbers=tuple(sorted_nums),
                invoice_ids=ordered_ids,
                competence=comp_label,
                sort_period=first.sort_period,
                sort_number=natural_number_sort_tuple(sorted_nums[0])
                if sorted_nums
                else first.sort_number,
                problem=first.problem,
                amount_display=first.amount_display,
                risk_amount=None,
                suggested_action=first.suggested_action,
            )
        )
    return grouped


def _compute_exposure_total(
    findings: Sequence[AuditFinding],
    invoice_totals_by_id: dict[str, Decimal],
) -> Decimal:
    """Calcula exposição financeira sem duplicar valor quando 1 nota tem 2 bloqueios integrais."""
    per_invoice_risk: dict[str, Decimal] = {}
    other_risk = _ZERO
    for f in findings:
        if f.risk_amount is None or f.risk_amount <= _ZERO:
            continue
        if len(f.invoice_ids) == 1:
            iid = f.invoice_ids[0]
            cap = invoice_totals_by_id.get(iid)
            accumulated = per_invoice_risk.get(iid, _ZERO) + f.risk_amount
            per_invoice_risk[iid] = min(cap, accumulated) if cap is not None else accumulated
        else:
            other_risk += f.risk_amount
    return sum(per_invoice_risk.values(), _ZERO) + other_risk


def _classify_non_duplicate_issues(
    invoice: Invoice,
    stored: StoredAudit,
    *,
    doc_names: dict[str, str] | None,
) -> list[AuditFinding]:
    """Traduz divergências e pendências da NF (inclusive múltiplas na mesma nota) em achados."""
    res = stored.result
    if res.classification is AuditClassification.NO_DIVERGENCE:
        return []

    non_dup_issues = [
        chk
        for chk in res.checks
        if chk.result is not CheckResult.OK and chk.name != DUPLICATE_CHECK
    ]
    comp_pt = format_competence_pt(res.reference_period)
    sort_p = period_sort_tuple(res.reference_period)
    sort_n = natural_number_sort_tuple(invoice.number)
    findings_out: list[AuditFinding] = []
    handled_names: set[str] = set()

    # 1. Cobrança sem contrato vinculado (caso órfão integral)
    if (
        invoice.contract_id is None
        and res.classification is AuditClassification.MISSING_DOCUMENTATION
    ):
        return [
            AuditFinding(
                severity="Alta",
                invoice_numbers=(invoice.number,),
                invoice_ids=(invoice.id,),
                competence=comp_pt,
                sort_period=sort_p,
                sort_number=sort_n,
                problem="Cobrança sem contrato vinculado",
                amount_display=format_brl(invoice.total),
                risk_amount=invoice.total,
                suggested_action="Vincular contrato ou bloquear pagamento",
            )
        ]

    # 2. Preço mensal / excedente acima ou abaixo do contrato
    has_monthly_or_overage_issue = any(
        chk.name in {"monthly_price", "overage_price"}
        and chk.result in (CheckResult.DIVERGENCE, CheckResult.REVIEW)
        for chk in non_dup_issues
    )
    has_granular_item_issue = any(
        chk.name
        in {
            UNREGISTERED_ITEM_CHECK,
            ITEM_UNIT_PRICE_CHECK,
            ITEM_QUANTITY_MONTHLY_CHECK,
            ITEM_QUANTITY_CUMULATIVE_CHECK,
            MISSING_GLOSA_CHECK,
            "items_total",
        }
        for chk in non_dup_issues
    )
    if (
        res.difference is not None
        and res.difference > _ZERO
        and (has_monthly_or_overage_issue or not has_granular_item_issue)
    ):
        is_confirmed_rule = res.classification is AuditClassification.DIVERGENCE
        has_unapproved_adj = any(chk.name == UNAPPROVED_ADJUSTMENT_CHECK for chk in non_dup_issues)
        problem = (
            "Cobrado acima do contrato (reajuste sem aditivo)"
            if (is_confirmed_rule and has_unapproved_adj)
            else (
                "Cobrado acima do contrato"
                if is_confirmed_rule
                else "Possível cobrança acima do contrato"
            )
        )
        findings_out.append(
            AuditFinding(
                severity="Alta" if is_confirmed_rule else "Média",
                invoice_numbers=(invoice.number,),
                invoice_ids=(invoice.id,),
                competence=comp_pt,
                sort_period=sort_p,
                sort_number=sort_n,
                problem=problem,
                amount_display=format_signed_brl(res.difference),
                risk_amount=res.difference,
                suggested_action=(
                    "Exigir aditivo ou estorno"
                    if is_confirmed_rule
                    else "Confirmar regra no contrato e exigir estorno"
                ),
            )
        )
        handled_names.update({"monthly_price", "overage_price", UNAPPROVED_ADJUSTMENT_CHECK})
    elif (
        res.difference is not None
        and res.difference < _ZERO
        and (has_monthly_or_overage_issue or not has_granular_item_issue)
    ):
        findings_out.append(
            AuditFinding(
                severity="Baixa",
                invoice_numbers=(invoice.number,),
                invoice_ids=(invoice.id,),
                competence=comp_pt,
                sort_period=sort_p,
                sort_number=sort_n,
                problem="Cobrado abaixo do contrato",
                amount_display=format_signed_brl(res.difference),
                risk_amount=None,
                suggested_action="Conferir desconto ou faturamento parcial",
            )
        )
        handled_names.update({"monthly_price", "overage_price"})

    # 2b. Checks granulares de ITEM_PRICE e Glosas de frequência
    for chk in non_dup_issues:
        if chk.name == UNREGISTERED_ITEM_CHECK:
            handled_names.add(UNREGISTERED_ITEM_CHECK)
            risk_val = _extract_memory_money(chk, "valor cobrado sem previsão")
            findings_out.append(
                AuditFinding(
                    severity="Alta" if chk.result is CheckResult.DIVERGENCE else "Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Cobrança de item não previsto no contrato/Ata",
                    amount_display=format_signed_brl(risk_val) if risk_val is not None else "n/a",
                    risk_amount=risk_val,
                    suggested_action="Glosar item não constante do contrato/Ata",
                )
            )
        elif chk.name == ITEM_UNIT_PRICE_CHECK:
            handled_names.add(ITEM_UNIT_PRICE_CHECK)
            unit_diff = _extract_signed_memory_money(chk, "diferença acumulada no preço unitário")
            if unit_diff is None:
                unit_diff = res.difference
            risk_val = unit_diff if (unit_diff is not None and unit_diff > _ZERO) else None
            is_premature = "antes do início da vigência" in chk.message.casefold()
            findings_out.append(
                AuditFinding(
                    severity="Alta" if chk.result is CheckResult.DIVERGENCE else "Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem=(
                        "Repactuação/aditivo aplicado antes do início da vigência"
                        if is_premature
                        else "Preço unitário acima do registrado no contrato/Ata"
                    ),
                    amount_display=format_signed_brl(unit_diff) if unit_diff is not None else "n/a",
                    risk_amount=risk_val,
                    suggested_action=(
                        "Estornar aplicação antecipada até a data de efeito"
                        if is_premature
                        else "Glosar diferença unitária e cobrar preço contratado"
                    ),
                )
            )
        elif chk.name == ITEM_QUANTITY_MONTHLY_CHECK:
            handled_names.add(ITEM_QUANTITY_MONTHLY_CHECK)
            excess_val = _extract_memory_money(chk, "excesso")
            findings_out.append(
                AuditFinding(
                    severity="Alta" if chk.result is CheckResult.DIVERGENCE else "Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Quantidade mensal cobrada acima da contratada",
                    amount_display=(
                        format_signed_brl(excess_val) if excess_val is not None else "n/a"
                    ),
                    risk_amount=excess_val,
                    suggested_action="Glosar postos/unidades excedentes ao contratado no mês",
                )
            )
        elif chk.name == ITEM_QUANTITY_CUMULATIVE_CHECK:
            handled_names.add(ITEM_QUANTITY_CUMULATIVE_CHECK)
            excess_val = _extract_memory_money(chk, "excesso acumulado")
            findings_out.append(
                AuditFinding(
                    severity="Alta" if chk.result is CheckResult.DIVERGENCE else "Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Saldo acumulado do item acima do teto da Ata",
                    amount_display=(
                        format_signed_brl(excess_val) if excess_val is not None else "n/a"
                    ),
                    risk_amount=excess_val,
                    suggested_action="Glosar quantidade além do limite global da Ata",
                )
            )
        elif chk.name == MISSING_GLOSA_CHECK:
            handled_names.add(MISSING_GLOSA_CHECK)
            glosa_val = _extract_memory_money(chk, "glosa faltante")
            findings_out.append(
                AuditFinding(
                    severity="Alta",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Glosa de frequência não descontada na nota",
                    amount_display=(
                        format_signed_brl(glosa_val) if glosa_val is not None else "n/a"
                    ),
                    risk_amount=glosa_val,
                    suggested_action="Efetuar glosa de frequência antes do pagamento",
                )
            )

    # 3. Pendência de CNPJ / vínculo documental (separando dígito inválido, filial e terceiro)
    for chk in non_dup_issues:
        if chk.name != "contract_link":
            continue
        handled_names.add("contract_link")
        msg_low = chk.message.casefold()
        if "dígito verificador inválido" in msg_low or "incompleto ou inválido" in msg_low:
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="CNPJ com dígito verificador inválido",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Pedir NF corrigida ou carta de correção",
                )
            )
        elif "filial" in msg_low:
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Emitida por filial (CNPJ diferente da matriz)",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Confirmar se o contrato autoriza faturamento por filial",
                )
            )
        elif "outro fornecedor" in msg_low:
            findings_out.append(
                AuditFinding(
                    severity="Alta",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="CNPJ de fornecedor diferente do contrato",
                    amount_display=format_brl(invoice.total),
                    risk_amount=invoice.total,
                    suggested_action="Bloquear pagamento e apurar faturamento por terceiro",
                )
            )
        elif "cnpj" in msg_low:
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="CNPJ inválido",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Pedir NF corrigida",
                )
            )
        else:
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Documentação incompleta",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Solicitar documentação complementar",
                )
            )

    # 3b. Vigência do contrato: separa mudança no meio da competência (Média, n/a) de fora (Alta)
    for chk in non_dup_issues:
        if chk.name != "contract_validity":
            continue
        handled_names.add("contract_validity")
        mem_txt = " ".join(chk.calculation_memory).casefold()
        msg_low = chk.message.casefold()
        is_outside = (
            "codigo = outside_contract_validity" in mem_txt
            or "fora da vigência" in msg_low
            or "após o fim da vigência" in msg_low
            or "anterior ao início da vigência" in msg_low
        )
        if is_outside:
            findings_out.append(
                AuditFinding(
                    severity="Alta",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem=(
                        "Emissão após o fim da vigência do contrato"
                        if "após o fim da vigência" in msg_low
                        else "Competência fora da vigência do contrato"
                    ),
                    amount_display=format_brl(invoice.total),
                    risk_amount=invoice.total,
                    suggested_action="Verificar aditivo de prorrogação ou bloquear pagamento",
                )
            )
        else:
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Vigência inicia ou termina no meio da competência",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Conferir proporcionalidade (pró-rata) na competência",
                )
            )

    # 4. Retenções tributárias (INSS / ISS)
    for chk in non_dup_issues:
        if chk.name not in (INSS_RETENTION_CHECK, ISS_RETENTION_CHECK):
            continue
        handled_names.add(chk.name)
        tax_label = "INSS" if chk.name == INSS_RETENTION_CHECK else "ISS"
        shortfall = _extract_retention_shortfall(chk)
        if shortfall is not None:
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem=f"Retenção de {tax_label} abaixo do contrato",
                    amount_display=format_signed_brl(shortfall),
                    risk_amount=shortfall,
                    suggested_action=(
                        f"Recolher/glosar diferença de {tax_label} no próximo pagamento"
                    ),
                )
            )
        else:
            findings_out.append(
                AuditFinding(
                    severity="Baixa",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem=f"Retenção de {tax_label} exige revisão",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action=f"Confirmar alíquota contratual de {tax_label}",
                )
            )

    # 4b. Cálculo item a item (qtd × unitário), limite de horas, horas sem OS e valor por extenso
    for chk in non_dup_issues:
        if chk.name == ITEMS_ARITHMETIC_CHECK:
            handled_names.add(ITEMS_ARITHMETIC_CHECK)
            diff_val = _extract_memory_money(chk, "diferença acumulada nos itens")
            findings_out.append(
                AuditFinding(
                    severity="Alta",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Erro de cálculo no item (qtd × unitário)",
                    amount_display=format_brl(diff_val) if diff_val is not None else "n/a",
                    risk_amount=diff_val,
                    suggested_action="Solicitar correção da NF ou glosar diferença",
                )
            )
        elif chk.name == HOURLY_LIMIT_CHECK:
            handled_names.add(HOURLY_LIMIT_CHECK)
            excess_val = _extract_memory_money(chk, "valor excedente")
            findings_out.append(
                AuditFinding(
                    severity="Alta",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Horas cobradas acima do limite contratual",
                    amount_display=format_brl(excess_val) if excess_val is not None else "n/a",
                    risk_amount=excess_val,
                    suggested_action="Glosar horas excedentes ao teto contratual",
                )
            )
        elif chk.name == HOURS_WITHOUT_OS_CHECK:
            handled_names.add(HOURS_WITHOUT_OS_CHECK)
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Horas cobradas sem Ordem de Serviço (OS)",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Exigir OS ou boletim de medição antes de pagar",
                )
            )
        elif chk.name == "items_total":
            signed_diff = _extract_signed_memory_money(chk, "diferença = total")
            if signed_diff is not None and signed_diff > _ZERO:
                handled_names.add("items_total")
                findings_out.append(
                    AuditFinding(
                        severity="Alta",
                        invoice_numbers=(invoice.number,),
                        invoice_ids=(invoice.id,),
                        competence=comp_pt,
                        sort_period=sort_p,
                        sort_number=sort_n,
                        problem="Total da nota maior que a soma dos itens",
                        amount_display=format_signed_brl(signed_diff),
                        risk_amount=signed_diff,
                        suggested_action="Solicitar correção da NF ou glosar diferença",
                    )
                )
        elif chk.name == WRITTEN_AMOUNT_CHECK:
            handled_names.add(WRITTEN_AMOUNT_CHECK)
            findings_out.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=(invoice.number,),
                    invoice_ids=(invoice.id,),
                    competence=comp_pt,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem="Valor por extenso diverge do valor numérico",
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Confirmar valor devido e pedir carta de correção",
                )
            )

    # 5. Divergência de razão social / natureza jurídica (P-03)
    for chk in non_dup_issues:
        if chk.name != SUPPLIER_IDENTITY_CHECK:
            continue
        handled_names.add(SUPPLIER_IDENTITY_CHECK)
        findings_out.append(
            AuditFinding(
                severity="Baixa",
                invoice_numbers=(invoice.number,),
                invoice_ids=(invoice.id,),
                competence=comp_pt,
                sort_period=sort_p,
                sort_number=sort_n,
                problem="Razão social / natureza jurídica diverge do contrato",
                amount_display="n/a",
                risk_amount=None,
                suggested_action="Conferir alteração contratual e cadastro na Receita Federal",
            )
        )

    # 6. Demais checks relevantes ainda não cobertos acima
    remaining = [chk for chk in non_dup_issues if chk.name not in handled_names]
    if findings_out or not remaining:
        return findings_out

    ordered_checks = sorted(
        remaining,
        key=lambda chk: (
            0 if chk.result in (CheckResult.PENDING, CheckResult.DIVERGENCE) else 1,
            _CHECK_PRIORITY.get(chk.name, 99),
        ),
    )
    primary: Check = ordered_checks[0]
    cleaned_msg = clean_technical_ids_in_message(primary.message, doc_names)

    if primary.name == "contract_validity":
        problem = "Competência fora da vigência do contrato"
        action = "Verificar aditivo de prorrogação ou bloquear pagamento"
        sev: FindingSeverity = "Alta"
    elif primary.name == "overage_quantity":
        problem = "Excedente sem medição comprovada"
        action = "Exigir boletim de medição"
        sev = "Média"
    elif primary.name in {"item_classification", "items_total"}:
        problem = "Itens cobrados exigem conferência"
        action = "Conferir discriminação na nota"
        sev = "Média"
    elif primary.name in {"amendments_reviewed", "rule_timeline", "rules_start_date"}:
        problem = "Regra ou aditivo pendente de revisão"
        action = "Revisar regra em Regras e Vigência"
        sev = "Média"
    elif primary.name == "competence_inferred":
        problem = "Competência inferida pela emissão"
        action = "Confirmar mês de referência"
        sev = "Baixa"
    elif primary.name == "periodic_adjustment":
        problem = "Reajuste contratual a verificar"
        action = "Conferir aplicação do índice"
        sev = "Baixa"
    elif primary.name == UNAPPROVED_ADJUSTMENT_CHECK:
        problem = "Descrição alega reajuste sem aditivo"
        action = "Exigir aditivo formal ou estorno"
        sev = "Alta"
    elif "pendente" in cleaned_msg.casefold() or "revisada" in cleaned_msg.casefold():
        problem = "Regra contratual aguardando confirmação"
        action = "Confirmar regra em Regras e Vigência"
        sev = "Média"
    else:
        problem = f"{humanize_check_name(primary.name)} exige verificação"
        action = "Analisar memória de cálculo"
        sev = "Média"

    return [
        AuditFinding(
            severity=sev,
            invoice_numbers=(invoice.number,),
            invoice_ids=(invoice.id,),
            competence=comp_pt,
            sort_period=sort_p,
            sort_number=sort_n,
            problem=problem,
            amount_display="n/a",
            risk_amount=None,
            suggested_action=action,
        )
    ]


def _semaphore_for_classification(
    worst: AuditClassification,
) -> tuple[BadgeToneLiteral, str, str]:
    if worst is AuditClassification.NO_DIVERGENCE:
        return ("ok", "✓", "Conforme")
    if worst is AuditClassification.NEEDS_REVIEW:
        return ("ai", "✦", "Atenção")
    return ("bad", "▲", "Divergência")


def _apply_safety_lock_per_contract(
    findings: list[AuditFinding],
    ordered_items: Sequence[tuple[Invoice, StoredAudit]],
) -> tuple[list[AuditFinding], list[str]]:
    """
    Trava de Segurança: se >= 3 notas de um mesmo contrato tiverem 100% de incidência de
    um mesmo achado cadastral, consolida numa linha única sem somar o bruto como exposição.
    """
    inv_ids_by_contract: dict[str, set[str]] = {}
    inv_nums_by_id: dict[str, str] = {}
    inv_contract_by_id: dict[str, str] = {}
    for inv, _ in ordered_items:
        cid = inv.contract_id or "__unlinked__"
        inv_ids_by_contract.setdefault(cid, set()).add(inv.id)
        inv_nums_by_id[inv.id] = inv.number
        inv_contract_by_id[inv.id] = cid

    extra_warnings: list[str] = []
    locked_findings: list[AuditFinding] = list(findings)

    for cid, cid_inv_ids in inv_ids_by_contract.items():
        if cid == "__unlinked__" or len(cid_inv_ids) < 3:
            continue
        for problem_label in sorted(_SAFETY_LOCK_CADASTRO_PROBLEMS):
            matched_indices: list[int] = []
            affected_ids: set[str] = set()
            for idx, f in enumerate(locked_findings):
                if f.problem != problem_label:
                    continue
                if f.invoice_ids and all(iid in cid_inv_ids for iid in f.invoice_ids):
                    matched_indices.append(idx)
                    affected_ids.update(f.invoice_ids)
            if affected_ids != cid_inv_ids or not matched_indices:
                continue
            sample = locked_findings[matched_indices[0]]
            remove_set = set(matched_indices)
            locked_findings = [f for i, f in enumerate(locked_findings) if i not in remove_set]
            sorted_nums = tuple(
                sorted(
                    (inv_nums_by_id[iid] for iid in cid_inv_ids if iid in inv_nums_by_id),
                    key=natural_number_sort_tuple,
                )
            )
            sorted_ids = tuple(sorted(cid_inv_ids))
            locked_findings.append(
                AuditFinding(
                    severity="Média",
                    invoice_numbers=sorted_nums,
                    invoice_ids=sorted_ids,
                    competence="Todo o período",
                    sort_period=sample.sort_period,
                    sort_number=natural_number_sort_tuple(sorted_nums[0])
                    if sorted_nums
                    else sample.sort_number,
                    problem=(
                        f"Possível falha na leitura cadastral do contrato {cid} "
                        "(100% das notas divergiram)"
                    ),
                    amount_display="n/a",
                    risk_amount=None,
                    suggested_action="Conferir CNPJ e razão social cadastrados no contrato",
                )
            )
            extra_warnings.append(
                f"Trava de segurança ({cid}): todas as {len(cid_inv_ids)} notas acusaram "
                f"'{problem_label}' — provável erro de leitura do contrato; exposição zerada "
                "para evitar falso positivo em cascata."
            )

    return locked_findings, extra_warnings


def _filter_payment_audit_for_contract(
    payment_audit: PaymentCycleAuditResult | None,
    contract_id: str,
    inv_number_keys: set[str],
) -> PaymentCycleAuditResult | None:
    if payment_audit is None:
        return None
    scoped_findings = tuple(
        pf
        for pf in payment_audit.findings
        if pf.contract_id == contract_id
        or (
            pf.contract_id is None
            and any(num.strip().casefold() in inv_number_keys for num in pf.invoice_numbers)
        )
    )
    scoped_checks = {
        k: v for k, v in payment_audit.checks_by_invoice_number.items() if k in inv_number_keys
    }
    scoped_dups = {
        k: v for k, v in payment_audit.duplicate_op_pairs.items() if k in inv_number_keys
    }
    return PaymentCycleAuditResult(
        payment_orders_count=(
            payment_audit.payment_orders_count if scoped_findings or scoped_checks else 0
        ),
        certificates_count=payment_audit.certificates_count if scoped_findings else 0,
        total_net_paid=payment_audit.total_net_paid,
        checks_by_invoice_number=scoped_checks,
        findings=scoped_findings,
        duplicate_op_pairs=scoped_dups,
    )


def build_executive_report(
    audited_items: Sequence[tuple[Invoice, StoredAudit]],
    *,
    doc_names: dict[str, str] | None = None,
    payment_audit: PaymentCycleAuditResult | None = None,
    contracts: Sequence[Contract] = (),
    rules: Sequence[ContractRule] = (),
    _allow_sections: bool = True,
) -> ExecutiveReportSummary:
    """Constrói o Relatório Executivo estruturado por gravidade, competência e exposição."""
    ordered_items = sorted(audited_items, key=lambda pair: natural_invoice_sort_key(pair[0]))
    inv_by_number = {inv.number.strip().casefold(): inv for inv, _ in ordered_items}
    inv_totals_by_id = {inv.id: inv.total for inv, _ in ordered_items}
    op_checks_by_nf = payment_audit.checks_by_invoice_number if payment_audit is not None else {}

    contract_sections: list[ContractSectionReport] = []
    if _allow_sections and len(contracts) > 1:
        present_cids = {inv.contract_id for inv, _ in ordered_items if inv.contract_id is not None}
        if len(present_cids) > 1:
            contract_by_id = {c.id: c for c in contracts}
            for cid in sorted(cid for cid in present_cids if cid in contract_by_id):
                sec_items = [pair for pair in ordered_items if pair[0].contract_id == cid]
                sec_nf_keys = {inv.number.strip().casefold() for inv, _ in sec_items}
                sec_rules = [r for r in rules if r.contract_id == cid]
                sec_payment = _filter_payment_audit_for_contract(payment_audit, cid, sec_nf_keys)
                sec_summary = build_executive_report(
                    sec_items,
                    doc_names=doc_names,
                    payment_audit=sec_payment,
                    contracts=(contract_by_id[cid],),
                    rules=sec_rules,
                    _allow_sections=False,
                )
                contract_sections.append(
                    ContractSectionReport(contract=contract_by_id[cid], summary=sec_summary)
                )

    findings: list[AuditFinding] = []
    for comp in _group_duplicate_components(ordered_items):
        first_inv = comp[0][0]
        if len(comp) >= 2:
            totals_sorted = sorted((inv.total for inv, _ in comp))
            dup_risk = sum(totals_sorted[:-1], _ZERO)
            amt_disp = format_brl(dup_risk)
        else:
            dup_risk = None
            amt_disp = "n/a"

        findings.append(
            AuditFinding(
                severity="Média",
                invoice_numbers=tuple(inv.number for inv, _ in comp),
                invoice_ids=tuple(inv.id for inv, _ in comp),
                competence=format_competence_pt(first_inv.reference_period),
                sort_period=period_sort_tuple(first_inv.reference_period),
                sort_number=natural_number_sort_tuple(first_inv.number),
                problem="Possível duplicidade",
                amount_display=amt_disp,
                risk_amount=dup_risk,
                suggested_action="Confirmar e bloquear pagamento",
            )
        )

    for inv, stored in ordered_items:
        findings.extend(_classify_non_duplicate_issues(inv, stored, doc_names=doc_names))

    payment_risk_total = _ZERO
    if payment_audit is not None:
        for pf in payment_audit.findings:
            inv_ids = tuple(
                inv_by_number[num.strip().casefold()].id
                for num in pf.invoice_numbers
                if num.strip().casefold() in inv_by_number
            )
            if pf.competence_period is not None:
                comp_str = format_competence_pt(pf.competence_period)
                sort_p = period_sort_tuple(pf.competence_period)
            else:
                comp_str = "Período"
                sort_p = (9999, 99)
            sort_n = (
                natural_number_sort_tuple(pf.invoice_numbers[0])
                if pf.invoice_numbers
                else (9, 0, pf.code)
            )
            if pf.risk_amount is not None:
                payment_risk_total += pf.risk_amount
                amt_display = format_brl(pf.risk_amount)
            else:
                amt_display = "n/a"
            findings.append(
                AuditFinding(
                    severity=pf.severity,
                    invoice_numbers=pf.invoice_numbers,
                    invoice_ids=inv_ids,
                    competence=comp_str,
                    sort_period=sort_p,
                    sort_number=sort_n,
                    problem=pf.problem,
                    amount_display=amt_display,
                    risk_amount=pf.risk_amount,
                    suggested_action=pf.suggested_action,
                )
            )

    findings, safety_warnings = _apply_safety_lock_per_contract(findings, ordered_items)
    findings = _group_identical_findings(findings)
    findings.sort(
        key=lambda f: (
            _SEVERITY_RANK[f.severity],
            f.sort_period[0],
            f.sort_period[1],
            f.sort_number,
            f.invoices_label,
        )
    )

    max_sev_by_inv: dict[str, FindingSeverity] = {}
    for f in findings:
        for iid in f.invoice_ids:
            cur = max_sev_by_inv.get(iid)
            if cur is None or _SEVERITY_RANK[f.severity] < _SEVERITY_RANK[cur]:
                max_sev_by_inv[iid] = f.severity

    op_flagged_numbers = {
        nf_key
        for nf_key, chk_list in op_checks_by_nf.items()
        if any(chk.result is not CheckResult.OK for chk in chk_list)
    }

    clean_items = [
        (inv, stored)
        for inv, stored in ordered_items
        if stored.result.classification is AuditClassification.NO_DIVERGENCE
    ]
    flagged_items = [
        (inv, stored)
        for inv, stored in ordered_items
        if stored.result.classification is not AuditClassification.NO_DIVERGENCE
        or inv.number.strip().casefold() in op_flagged_numbers
    ]

    charged_total = sum((stored.result.total for _, stored in ordered_items), _ZERO)
    exposure_total = _compute_exposure_total(findings, inv_totals_by_id)
    reading_coverage, base_warnings = _build_reading_diagnostics(contracts, rules)
    reading_warnings = tuple(dict.fromkeys([*base_warnings, *safety_warnings]))

    # Quadro cronológico por competência (uma única base vigente por contrato na competência;
    # evita comparar valor vigente parcial quando há mais de um contrato no mesmo mês).
    by_period: dict[str, list[tuple[Invoice, StoredAudit]]] = {}
    for pair in ordered_items:
        by_period.setdefault(str(pair[1].result.reference_period), []).append(pair)

    competence_rows: list[CompetenceSemaphoreRow] = []
    for period_iso in sorted(by_period.keys(), key=period_sort_tuple):
        bucket = by_period[period_iso]
        bucket_cids = {inv.contract_id or f"__unlinked_{inv.id}" for inv, _ in bucket}
        dedup_base_by_contract: dict[str, Decimal] = {}
        for c_key in bucket_cids:
            c_bases = [
                st.result.base
                for inv, st in bucket
                if (inv.contract_id or f"__unlinked_{inv.id}") == c_key
                and st.result.base is not None
            ]
            if not c_bases:
                continue
            if len(c_bases) == 1 or len(set(c_bases)) == 1:
                dedup_base_by_contract[c_key] = c_bases[0]
            else:
                dedup_base_by_contract[c_key] = sum(c_bases, _ZERO)

        all_contracts_have_base = len(dedup_base_by_contract) == len(bucket_cids)
        period_expected: Decimal | None = (
            sum(dedup_base_by_contract.values(), _ZERO)
            if dedup_base_by_contract and (len(bucket_cids) == 1 or all_contracts_have_base)
            else None
        )
        period_charged = sum((st.result.total for _, st in bucket), _ZERO)
        period_diff: Decimal | None = (
            period_charged - period_expected if period_expected is not None else None
        )
        classifications_in_month = [st.result.classification for _, st in bucket]
        if any(inv.number.strip().casefold() in op_flagged_numbers for inv, _ in bucket):
            classifications_in_month.append(AuditClassification.DIVERGENCE)
        if period_diff is not None and period_diff > _ZERO:
            classifications_in_month.append(AuditClassification.DIVERGENCE)
        worst = most_severe(classifications_in_month)
        tone, icon, status_label = _semaphore_for_classification(worst)
        competence_rows.append(
            CompetenceSemaphoreRow(
                period_iso=period_iso,
                competence_pt=format_competence_pt(period_iso),
                invoices_count=len(bucket),
                expected_total=period_expected,
                charged_total=period_charged,
                difference_total=period_diff,
                tone=tone,
                icon=icon,
                status_label=status_label,
            )
        )

    expected_total: Decimal | None = (
        sum(
            (row.expected_total for row in competence_rows if row.expected_total is not None),
            _ZERO,
        )
        if any(row.expected_total is not None for row in competence_rows)
        else None
    )

    clean_numbers = tuple(inv.number for inv, _ in clean_items)
    if not clean_numbers:
        clean_summary_line = "Nenhuma nota concluída sem ressalvas neste escopo."
    elif len(clean_numbers) == 1:
        clean_summary_line = (
            "1 nota conferida em vínculo, preço, vigência e soma dos itens, sem divergências "
            f"({clean_numbers[0]})."
        )
    else:
        clean_summary_line = (
            f"{len(clean_numbers)} notas conferidas em vínculo, preço, vigência e soma dos itens, "
            f"sem divergências ({', '.join(clean_numbers)})."
        )

    # Apêndice técnico exclusivamente para notas com achado (fiscal, tributário ou na OP vinculada)
    finding_order_by_inv: dict[str, int] = {}
    for idx, f in enumerate(findings):
        for iid in f.invoice_ids:
            finding_order_by_inv.setdefault(iid, idx)

    flagged_sorted = sorted(
        flagged_items,
        key=lambda p: (
            finding_order_by_inv.get(p[0].id, 9999),
            natural_invoice_sort_key(p[0]),
        ),
    )
    appendix_entries: list[AppendixEntry] = []
    for inv, stored in flagged_sorted:
        res = stored.result
        op_checks = op_checks_by_nf.get(inv.number.strip().casefold(), ())
        combined_cls = most_severe(
            [
                res.classification,
                *(
                    chk.result.classification
                    for chk in op_checks
                    if chk.result is not CheckResult.OK
                ),
            ]
        )
        tone, _, cls_label = AUDIT_CLASSIFICATION_BADGES[combined_cls]
        aligned_sev = max_sev_by_inv.get(inv.id)
        if aligned_sev is not None:
            tone = "bad" if aligned_sev == "Alta" else ("ai" if aligned_sev == "Média" else "info")
        check_items: list[AppendixCheckItem] = []
        for chk in (*res.checks, *op_checks):
            if chk.result is CheckResult.OK and chk.name != "monthly_price":
                continue
            if (
                chk.result is CheckResult.OK
                and res.classification is not AuditClassification.MISSING_DOCUMENTATION
            ):
                continue
            chk_tone, _, chk_status = CHECK_RESULT_BADGES[chk.result]
            check_items.append(
                AppendixCheckItem(
                    check_label=humanize_check_name(chk.name),
                    status_label=chk_status,
                    tone=chk_tone,
                    message=clean_technical_ids_in_message(chk.message, doc_names),
                    memory_lines=tuple(
                        clean_technical_ids_in_message(line, doc_names)
                        for line in chk.calculation_memory
                    ),
                )
            )
        clauses: list[AppendixClauseQuote] = []
        seen_rules: set[str] = set()
        applied_ids = {ar.rule_id for ar in res.applied_rules}
        ordered_rules = sorted(
            stored.rules_snapshot,
            key=lambda r: (0 if r.id in applied_ids else 1, r.version),
        )
        for rule in ordered_rules:
            if rule.id in seen_rules:
                continue
            seen_rules.add(rule.id)
            clauses.append(
                AppendixClauseQuote(
                    rule_label=f"{RULE_TYPE_LABELS[rule.rule_type]} ({describe_rule_value(rule)})",
                    location_label=_format_rule_location_short(rule, doc_names),
                    literal_excerpt=rule.evidence.literal_excerpt,
                )
            )
        appendix_entries.append(
            AppendixEntry(
                invoice_id=inv.id,
                invoice_number=inv.number,
                competence_pt=format_competence_pt(res.reference_period),
                supplier=inv.supplier,
                classification_label=cls_label,
                tone=tone,
                severity_label=aligned_sev,
                expected_display=format_brl(res.base),
                charged_display=format_brl(res.total),
                difference_display=format_signed_brl(res.difference),
                checks=tuple(check_items),
                clauses=tuple(clauses),
            )
        )

    total_count = len(ordered_items)
    findings_count = len(findings)
    action_invoices_count = len(flagged_items)
    clean_count = len(clean_items)

    if findings_count == 0:
        verdict_tone: BadgeToneLiteral = "ok"
        verdict_headline = (
            f"Aprovado — {total_count} nota(s) auditada(s), nenhuma ação exigida, "
            "exposição estimada de R$ 0,00."
        )
        verdict_lines = (
            f"Total cobrado conferido: {format_brl(charged_total)}.",
            "Todas as verificações de preço, vigência, duplicidade e soma dos itens passaram.",
            "Pronto para encaminhamento financeiro padrão.",
        )
    else:
        alta_count = sum(1 for f in findings if f.severity == "Alta")
        media_count = sum(1 for f in findings if f.severity == "Média")
        baixa_count = sum(1 for f in findings if f.severity == "Baixa")
        verdict_tone = "bad" if alta_count > 0 else "ai"
        nota_word = "nota auditada" if total_count == 1 else "notas auditadas"
        acao_nota_word = "nota exige ação" if action_invoices_count == 1 else "notas exigem ação"
        achado_word = "achado" if findings_count == 1 else "achados"
        verdict_headline = (
            f"{total_count} {nota_word}, {action_invoices_count} {acao_nota_word} "
            f"({findings_count} {achado_word}), exposição estimada de "
            f"{format_brl(exposure_total)}."
        )
        sev_parts: list[str] = []
        if alta_count:
            sev_parts.append(f"{alta_count} de gravidade Alta")
        if media_count:
            sev_parts.append(f"{media_count} de gravidade Média")
        if baixa_count:
            sev_parts.append(f"{baixa_count} de gravidade Baixa")
        retention_risk = sum(
            (
                f.risk_amount
                for f in findings
                if f.risk_amount is not None and "retenção" in f.problem.casefold()
            ),
            _ZERO,
        )
        if expected_total is not None and charged_total > expected_total:
            billing_excess = charged_total - expected_total
            totals_line = (
                f"Faturado {format_brl(charged_total)} contra {format_brl(expected_total)} "
                f"vigente ({format_signed_brl(billing_excess)}); "
                f"retenções/pagamentos em risco: {format_brl(payment_risk_total + retention_risk)}."
            )
        else:
            totals_line = f"Total cobrado no escopo: {format_brl(charged_total)}."
        verdict_lines = (
            f"Prioridades identificadas: {', '.join(sev_parts)}.",
            (
                f"Notas com achado: {action_invoices_count} de {total_count} "
                f"({clean_count} sem divergências de faturamento)."
                if payment_audit
                and (payment_audit.payment_orders_count or payment_audit.certificates_count)
                else f"Notas com achado: {action_invoices_count} de {total_count} "
                f"({clean_count} sem divergências)."
            ),
            totals_line,
            "Recomendação: tratar os achados abaixo antes de liberar o pagamento.",
        )

    return ExecutiveReportSummary(
        total_invoices=total_count,
        findings_count=findings_count,
        action_invoices_count=action_invoices_count,
        clean_invoices_count=clean_count,
        clean_invoice_numbers=clean_numbers,
        exposure_total=exposure_total,
        charged_total=charged_total,
        expected_total=expected_total,
        verdict_tone=verdict_tone,
        verdict_headline=verdict_headline,
        verdict_lines=verdict_lines,
        findings=tuple(findings),
        competence_rows=tuple(competence_rows),
        clean_summary_line=clean_summary_line,
        appendix_entries=tuple(appendix_entries),
        reading_coverage=reading_coverage,
        reading_warnings=reading_warnings,
        contract_sections=tuple(contract_sections),
    )


def _tone_palette(tone: BadgeToneLiteral, palette: dict[str, str]) -> tuple[str, str, str]:
    if tone == "ok":
        return palette["ok-bg"], palette["ok-bd"], palette["ok-tx"]
    if tone == "bad":
        return palette["bad-bg"], palette["bad-bd"], palette["bad-tx"]
    if tone == "ai":
        return palette["gold-bg"], palette["gold-border"], palette["gold-text"]
    return palette["info-bg"], palette["info-bd"], palette["info-tx"]


def _severity_badge_html(severity: FindingSeverity, palette: dict[str, str]) -> str:
    tone: BadgeToneLiteral = (
        "bad" if severity == "Alta" else ("ai" if severity == "Média" else "info")
    )
    bg, bd, tx = _tone_palette(tone, palette)
    icon = "▲" if severity == "Alta" else ("✦" if severity == "Média" else "●")
    return (
        f'<span class="pill" style="background:{bg};border-color:{bd};color:{tx};">'
        f"{_esc(icon)} {_esc(severity)}</span>"
    )


def build_printable_report_html(
    summary: ExecutiveReportSummary,
    *,
    scope_label: str,
    generated_at_br: str,
    auto_print: bool = False,
) -> str:
    """Gera documento HTML5 autossuficiente estilizado para impressão e Save as PDF."""
    palette = THEMES["light"]
    v_bg, v_bd, v_tx = _tone_palette(summary.verdict_tone, palette)

    findings_rows_html: list[str] = []
    if not summary.findings:
        findings_rows_html.append(
            '<tr><td colspan="6" style="text-align:center;">'
            "Nenhum achado ou pendência encontrado neste escopo.</td></tr>"
        )
    else:
        for f in summary.findings:
            findings_rows_html.append(
                "<tr>"
                f"<td>{_severity_badge_html(f.severity, palette)}</td>"
                f'<td class="mono">{_esc(f.invoices_label)}</td>'
                f'<td class="mono">{_esc(f.competence)}</td>'
                f"<td>{_esc(f.problem)}</td>"
                f'<td class="mono num">{_esc(f.amount_display)}</td>'
                f"<td><strong>{_esc(f.suggested_action)}</strong></td>"
                "</tr>"
            )

    comp_rows_html: list[str] = []
    for row in summary.competence_rows:
        bg, bd, tx = _tone_palette(row.tone, palette)
        status_pill = (
            f'<span class="pill" style="background:{bg};border-color:{bd};color:{tx};">'
            f"{_esc(row.icon)} {_esc(row.status_label)}</span>"
        )
        comp_rows_html.append(
            "<tr>"
            f'<td class="mono">{_esc(row.competence_pt)}</td>'
            f'<td class="mono num">{row.invoices_count}</td>'
            f'<td class="mono num">{_esc(format_brl(row.expected_total))}</td>'
            f'<td class="mono num">{_esc(format_brl(row.charged_total))}</td>'
            f'<td class="mono num">{_esc(format_signed_brl(row.difference_total))}</td>'
            f"<td>{status_pill}</td>"
            "</tr>"
        )

    appendix_blocks_html: list[str] = []
    for entry in summary.appendix_entries:
        bg, bd, tx = _tone_palette(entry.tone, palette)
        checks_html = "".join(
            '<div class="chk-item">'
            f"<div><strong>{_esc(chk.check_label)} ({_esc(chk.status_label)}):</strong> "
            f"{_esc(chk.message)}</div>"
            + (
                f'<pre class="mem">{_esc(chr(10).join(chk.memory_lines))}</pre>'
                if chk.memory_lines
                else ""
            )
            + "</div>"
            for chk in entry.checks
        )
        clauses_html = "".join(
            '<div class="clause">'
            f'<div class="clause-meta">{_esc(cl.rule_label)} — {_esc(cl.location_label)}</div>'
            f'<div class="clause-txt">"{_esc(cl.literal_excerpt)}"</div>'
            "</div>"
            for cl in entry.clauses
        )
        appendix_blocks_html.append(
            '<div class="app-card">'
            '<div class="app-head">'
            f"<div><strong>Nota {_esc(entry.invoice_number)}</strong> • "
            f"{_esc(entry.competence_pt)} • {_esc(entry.supplier)}</div>"
            f'<span class="pill" style="background:{bg};border-color:{bd};color:{tx};">'
            f"{_esc(entry.severity_label + ' • ') if entry.severity_label else ''}"
            f"{_esc(entry.classification_label)}</span>"
            "</div>"
            '<div class="app-nums">'
            f"Vigente: <strong>{_esc(entry.expected_display)}</strong> • "
            f"Cobrado: <strong>{_esc(entry.charged_display)}</strong> • "
            f"Diferença: <strong>{_esc(entry.difference_display)}</strong>"
            "</div>"
            f"{checks_html}"
            f"{clauses_html}"
            "</div>"
        )

    appendix_section = (
        ""
        if not appendix_blocks_html
        else (
            '<div class="page-break"></div>'
            "<h2>Apêndice Técnico — Apenas Notas com Achado</h2>" + "".join(appendix_blocks_html)
        )
    )

    verdict_items = "".join(f"<li>{_esc(line)}</li>" for line in summary.verdict_lines[:4])
    coverage_pills_html = "".join(
        (
            '<span class="pill" style="'
            f"background:{(_tone_palette('ok' if item.ok else 'ai', palette))[0]};"
            f"border-color:{(_tone_palette('ok' if item.ok else 'ai', palette))[1]};"
            f"color:{(_tone_palette('ok' if item.ok else 'ai', palette))[2]};"
            'margin:2px 4px 2px 0;">'
            f"{'✓' if item.ok else '▲'} {_esc(item.label)}: {_esc(item.detail)}</span>"
        )
        for item in summary.reading_coverage
    )
    coverage_warns_html = "".join(
        f'<div style="margin-top:4px;font-size:11.5px;color:{palette["gold-text"]};">'
        f"▲ {_esc(warn)}</div>"
        for warn in summary.reading_warnings
    )
    coverage_section_html = (
        ""
        if not summary.reading_coverage and not summary.reading_warnings
        else (
            '<div style="border:1px solid '
            f'{palette["border"]};border-radius:4px;padding:8px 12px;margin-bottom:12px;">'
            '<div style="font-size:10.5px;font-weight:700;text-transform:uppercase;'
            f'color:{palette["text-secondary"]};margin-bottom:4px;">'
            "Cobertura da Leitura do Contrato</div>"
            f"<div>{coverage_pills_html}</div>"
            f"{coverage_warns_html}"
            "</div>"
        )
    )
    sections_html = ""
    if len(summary.contract_sections) > 1:
        parts: list[str] = []
        for sec in summary.contract_sections:
            sec_s = sec.summary
            sec_comp = "".join(
                "<tr>"
                f'<td class="mono">{_esc(r.competence_pt)}</td>'
                f'<td class="mono num">{r.invoices_count}</td>'
                f'<td class="mono num">{_esc(format_brl(r.expected_total))}</td>'
                f'<td class="mono num">{_esc(format_brl(r.charged_total))}</td>'
                f'<td class="mono num">{_esc(format_signed_brl(r.difference_total))}</td>'
                f"<td>{_esc(r.status_label)}</td>"
                "</tr>"
                for r in sec_s.competence_rows
            )
            parts.append(
                f"<h2>Contrato {_esc(sec.contract.id)} — {_esc(sec.contract.supplier)} "
                f"({_esc(sec.contract.supplier_cnpj)})</h2>"
                f"<p><strong>{_esc(sec_s.verdict_headline)}</strong></p>"
                '<table><thead><tr><th>Competência</th><th class="num">Notas</th>'
                '<th class="num">Valor vigente</th><th class="num">Valor cobrado</th>'
                '<th class="num">Diferença</th><th>Situação</th></tr></thead>'
                f"<tbody>{sec_comp}</tbody></table>"
                f'<div class="clean-box">✓ {_esc(sec_s.clean_summary_line)}</div>'
            )
        sections_html = "".join(parts)

    print_script = (
        '<script>window.addEventListener("load", function () {'
        "setTimeout(function () { window.print(); }, 250);"
        "});</script>"
        if auto_print
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<title>Relatório Executivo de Auditoria — Farol de Dados</title>
<style>
  @page {{ size: A4; margin: 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", Arial, sans-serif;
    color: {palette["text-primary"]};
    background: {palette["bg-surface"]};
    margin: 0;
    padding: 20px;
    font-size: 12.5px;
    line-height: 1.45;
  }}
  .doc-head {{
    border-bottom: 2px solid {palette["gold"]};
    padding-bottom: 10px;
    margin-bottom: 14px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
  }}
  .eyebrow {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: {palette["gold-text"]};
  }}
  h1 {{ font-size: 19px; margin: 2px 0 2px 0; color: {palette["text-primary"]}; }}
  h2 {{
    font-size: 14px;
    margin: 16px 0 8px 0;
    color: {palette["text-primary"]};
    border-bottom: 1px solid {palette["border"]};
    padding-bottom: 4px;
  }}
  .meta-right {{ font-size: 11px; color: {palette["text-secondary"]}; text-align: right; }}
  .verdict {{
    background: {v_bg};
    border: 1px solid {v_bd};
    border-left: 4px solid {v_tx};
    border-radius: 4px;
    padding: 12px 14px;
    margin-bottom: 14px;
  }}
  .verdict-title {{ font-size: 15px; font-weight: 700; color: {v_tx}; margin-bottom: 6px; }}
  .verdict ul {{ margin: 0; padding-left: 18px; color: {palette["text-primary"]}; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 12px; }}
  th {{
    background: {palette["bg-subtle"]};
    color: {palette["text-secondary"]};
    text-align: left;
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 8px 10px;
    border-bottom: 1px solid {palette["border"]};
  }}
  td {{ padding: 8px 10px; border-bottom: 1px solid {palette["border"]}; vertical-align: top; }}
  th.num, td.num {{ text-align: right; }}
  .mono {{ font-family: ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums; }}
  .pill {{
    display: inline-block;
    padding: 2px 8px;
    border: 1px solid;
    border-radius: 4px;
    font-size: 10.5px;
    font-weight: 700;
    white-space: nowrap;
  }}
  .clean-box {{
    background: {palette["ok-bg"]};
    border: 1px solid {palette["ok-bd"]};
    color: {palette["ok-tx"]};
    border-radius: 4px;
    padding: 10px 12px;
    font-weight: 600;
    margin-top: 8px;
  }}
  .app-card {{
    border: 1px solid {palette["border"]};
    border-radius: 4px;
    padding: 10px 12px;
    margin-bottom: 10px;
    page-break-inside: avoid;
  }}
  .app-head {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }}
  .app-nums {{ font-size: 12px; color: {palette["text-secondary"]}; margin-bottom: 8px; }}
  .chk-item {{ margin-bottom: 6px; }}
  .mem {{
    background: {palette["bg-subtle"]};
    border-left: 3px solid {palette["primary"]};
    padding: 6px 8px;
    font-family: ui-monospace, Consolas, monospace;
    font-size: 11px;
    white-space: pre-wrap;
    margin: 4px 0 0 0;
  }}
  .clause {{
    background: {palette["gold-bg"]};
    border-left: 3px solid {palette["gold"]};
    padding: 6px 8px;
    margin-top: 6px;
    font-size: 11.5px;
  }}
  .clause-meta {{ font-weight: 700; color: {palette["gold-text"]}; margin-bottom: 2px; }}
  .gov-note {{
    margin-top: 14px;
    font-size: 11px;
    color: {palette["text-secondary"]};
    border-top: 1px solid {palette["border"]};
    padding-top: 8px;
  }}
  .page-break {{ page-break-before: always; }}
  @media print {{ body {{ padding: 0; }} }}
</style>
</head>
<body>
  <div class="doc-head">
    <div>
      <div class="eyebrow">Farol de Dados • Auditoria Contratual</div>
      <h1>Relatório Executivo de Auditoria</h1>
      <div>Escopo: <strong>{_esc(scope_label)}</strong></div>
    </div>
    <div class="meta-right">
      <div>Emitido em: {_esc(generated_at_br)}</div>
      <div>Motor determinístico em Decimal</div>
    </div>
  </div>

  <div class="verdict">
    <div class="verdict-title">{_esc(summary.verdict_headline)}</div>
    <ul>{verdict_items}</ul>
  </div>

  {coverage_section_html}

  <h2>Achados Priorizados por Gravidade</h2>
  <table>
    <thead>
      <tr>
        <th>Gravidade</th>
        <th>Nota</th>
        <th>Competência</th>
        <th>Problema</th>
        <th class="num">Valor</th>
        <th>Ação sugerida</th>
      </tr>
    </thead>
    <tbody>{"".join(findings_rows_html)}</tbody>
  </table>

  <h2>Quadro por Competência</h2>
  <table>
    <thead>
      <tr>
        <th>Competência</th>
        <th class="num">Notas</th>
        <th class="num">Valor vigente</th>
        <th class="num">Valor cobrado</th>
        <th class="num">Diferença</th>
        <th>Situação</th>
      </tr>
    </thead>
    <tbody>{"".join(comp_rows_html)}</tbody>
  </table>

  <div class="clean-box">✓ {_esc(summary.clean_summary_line)}</div>

  {sections_html}

  {appendix_section}

  <div class="gov-note">
    <strong>Nota de governança:</strong> este relatório apresenta evidências objetivas para decisão
    humana; não declara fraude e não bloqueia pagamentos automaticamente.
  </div>
  {print_script}
</body>
</html>"""


def build_single_invoice_printable_html(
    invoice: Invoice,
    stored: StoredAudit | None,
    *,
    doc_names: dict[str, str] | None = None,
    decision_label: str | None = None,
    auto_print: bool = False,
) -> str:
    """Gera ficha imprimível de 1 cobrança para o botão `Imprimir` na tela 7.4."""
    palette = THEMES["light"]
    comp_short = format_competence_short(invoice.reference_period)
    if stored is None:
        return (
            "<html><body style='font-family:sans-serif;padding:24px;'>"
            f"<h2>Cobrança nº {_esc(invoice.number)} • {_esc(comp_short)}</h2>"
            f"<p>Fornecedor: {_esc(invoice.supplier)} • "
            f"Total: {_esc(format_brl(invoice.total))}</p>"
            "<p>Cobrança ainda não auditada.</p></body></html>"
        )

    res = stored.result
    tone, icon, cls_label = AUDIT_CLASSIFICATION_BADGES[res.classification]
    bg, bd, tx = _tone_palette(tone, palette)
    pct_str = ""
    if res.base is not None and res.base > _ZERO and res.difference is not None:
        pct = (res.difference / res.base * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        pct_str = f" ({pct:+.2f}%)"

    checks_html = "".join(
        "<div style='margin-bottom:8px;'>"
        f"<strong>{_esc(humanize_check_name(chk.name))}:</strong> "
        f"{_esc(clean_technical_ids_in_message(chk.message, doc_names))}"
        + (
            "<pre style='background:"
            + palette["bg-subtle"]
            + ";border-left:3px solid "
            + palette["primary"]
            + ";padding:8px;font-size:11.5px;white-space:pre-wrap;'>"
            + _esc(
                "\n".join(
                    clean_technical_ids_in_message(line, doc_names)
                    for line in chk.calculation_memory
                )
            )
            + "</pre>"
            if chk.calculation_memory
            else ""
        )
        + "</div>"
        for chk in res.checks
    )
    rules_html = "".join(
        "<li style='margin-bottom:6px;'>"
        f"<strong>{_esc(RULE_TYPE_LABELS[rule.rule_type])} "
        f"({_esc(describe_rule_value(rule))})</strong>"
        f" — {_esc(_format_rule_location_short(rule, doc_names))}<br/>"
        f'<em>"{_esc(rule.evidence.literal_excerpt)}"</em>'
        "</li>"
        for rule in stored.rules_snapshot
    )
    print_script = (
        '<script>window.addEventListener("load", function () {'
        "setTimeout(function () { window.print(); }, 250);"
        "});</script>"
        if auto_print
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<title>Auditoria Nota {_esc(invoice.number)} — Farol de Dados</title>
<style>
  @page {{ size: A4; margin: 14mm; }}
  body {{
    font-family: -apple-system, "Segoe UI", Arial, sans-serif;
    color: {palette["text-primary"]};
    background: {palette["bg-surface"]};
    margin: 0;
    padding: 20px;
    font-size: 12.5px;
    line-height: 1.45;
  }}
  .head {{
    display:flex; justify-content:space-between; align-items:flex-start;
    border-bottom: 2px solid {palette["gold"]}; padding-bottom: 10px; margin-bottom: 12px;
  }}
  .grid {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:8px; margin-bottom:14px; }}
  .cell {{ border:1px solid {palette["border"]}; padding:8px 10px; border-radius:4px; }}
  .lbl {{
    font-size:10px; text-transform:uppercase; color:{palette["text-secondary"]}; font-weight:700;
  }}
  .val {{ font-family: ui-monospace, Consolas, monospace; font-size:14px; font-weight:700; }}
  .pill {{
    display:inline-block; padding:3px 9px; border-radius:4px; border:1px solid {bd};
    background:{bg}; color:{tx}; font-weight:700; font-size:11px;
  }}
</style>
</head>
<body>
  <div class="head">
    <div>
      <h2 style="margin:0;">Cobrança nº {_esc(invoice.number)} •
        {_esc(comp_short)} • {_esc(format_brl(invoice.total))}</h2>
      <div>{_esc(invoice.supplier)} • Contrato {_esc(invoice.contract_id or "Sem vínculo")} •
        {_esc(INVOICE_SOURCE_LABELS[invoice.source])}</div>
    </div>
    <span class="pill">{_esc(icon)} {_esc(cls_label)}</span>
  </div>
  <div class="grid">
    <div class="cell"><div class="lbl">Valor de referência</div>
      <div class="val">{_esc(format_brl(res.base))}</div></div>
    <div class="cell"><div class="lbl">Total da cobrança</div>
      <div class="val">{_esc(format_brl(res.total))}</div></div>
    <div class="cell"><div class="lbl">Diferença</div>
      <div class="val">{_esc(format_signed_brl(res.difference))}{_esc(pct_str)}</div></div>
    <div class="cell"><div class="lbl">Decisão humana</div>
      <div class="val">{_esc(decision_label or "Aguardando")}</div></div>
  </div>
  <h3>Verificações e memória de cálculo</h3>
  {checks_html}
  <h3>Evidências e regras aplicadas</h3>
  <ul>{rules_html or "<li>Nenhuma regra aplicada.</li>"}</ul>
  <p style="font-size:11px;color:{palette["text-secondary"]};
    border-top:1px solid {palette["border"]};padding-top:8px;">
    Nota de governança: o resultado apresenta evidências para decisão humana;
    não declara fraude e não bloqueia pagamentos automaticamente.
  </p>
  {print_script}
</body>
</html>"""


def build_executive_csv_bytes(summary: ExecutiveReportSummary) -> bytes:
    """Exporta achados e resumo por competência em CSV UTF-8 com BOM (`;` para Excel pt-BR)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["VEREDITO EXECUTIVO", summary.verdict_headline])
    writer.writerow(["Notas auditadas", summary.total_invoices])
    writer.writerow(["Achados que exigem ação", summary.findings_count])
    writer.writerow(["Exposição estimada", format_brl(summary.exposure_total)])
    writer.writerow(["Total cobrado", format_brl(summary.charged_total)])
    writer.writerow([])

    writer.writerow(["TABELA DE ACHADOS POR GRAVIDADE"])
    writer.writerow(["Gravidade", "Nota", "Competência", "Problema", "Valor", "Ação sugerida"])
    for f in summary.findings:
        writer.writerow(
            [
                f.severity,
                f.invoices_label,
                f.competence,
                f.problem,
                f.amount_display,
                f.suggested_action,
            ]
        )
    writer.writerow([])

    writer.writerow(["QUADRO POR COMPETÊNCIA"])
    writer.writerow(
        ["Competência", "Notas", "Valor vigente", "Valor cobrado", "Diferença", "Situação"]
    )
    for row in summary.competence_rows:
        writer.writerow(
            [
                row.competence_pt,
                row.invoices_count,
                format_brl(row.expected_total),
                format_brl(row.charged_total),
                format_signed_brl(row.difference_total),
                row.status_label,
            ]
        )
    writer.writerow([])
    writer.writerow(["NOTAS SEM DIVERGÊNCIA", summary.clean_summary_line])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def build_invoices_csv_bytes(
    items: Sequence[tuple[Invoice, StoredAudit | None, ReviewStatus]],
) -> bytes:
    """Exporta cobranças filtradas na tela 7.4 em CSV UTF-8 com BOM."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            "Nota",
            "Competência",
            "Emissão",
            "Fornecedor",
            "CNPJ",
            "Contrato",
            "Valor vigente",
            "Total cobrado",
            "Diferença",
            "Classificação",
            "Decisão humana",
        ]
    )
    for inv, stored, rev_status in items:
        if stored is not None:
            res = stored.result
            expected_txt = format_brl(res.base)
            diff_txt = format_signed_brl(res.difference)
            cls_label = AUDIT_CLASSIFICATION_BADGES[res.classification][2]
        else:
            expected_txt = "—"
            diff_txt = "—"
            cls_label = "Não auditada"
        rev_label = AUDIT_REVIEW_STATUS_BADGES[rev_status][2]
        writer.writerow(
            [
                inv.number,
                format_competence_pt(inv.reference_period),
                format_date_br(inv.issue_date),
                inv.supplier,
                inv.supplier_cnpj,
                inv.contract_id or "Sem vínculo",
                expected_txt,
                format_brl(inv.total),
                diff_txt,
                cls_label,
                rev_label,
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _xlsx_fill(hex_color: str) -> PatternFill:
    return PatternFill(
        start_color=hex_color.lstrip("#"), end_color=hex_color.lstrip("#"), fill_type="solid"
    )


def _xlsx_font(hex_color: str, *, bold: bool = False) -> Font:
    return Font(name="Calibri", size=11, bold=bold, color=hex_color.lstrip("#"))


def build_executive_xlsx_bytes(
    summary: ExecutiveReportSummary,
    audited_items: Sequence[tuple[Invoice, StoredAudit]],
) -> bytes:
    """Gera planilha Excel `.xlsx` com abas `Achados`, `Por Competência` e `Cobranças`."""
    palette = THEMES["light"]
    wb = Workbook()
    thin_side = Side(style="thin", color=palette["border"].lstrip("#"))
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    header_fill = _xlsx_fill(palette["primary"])
    header_font = _xlsx_font(palette["on-primary"], bold=True)
    money_fmt = 'R$ #,##0.00;[Red]-R$ #,##0.00;"R$ 0,00"'

    def _style_headers(ws: Worksheet, headers: Sequence[str], row_idx: int = 1) -> None:
        for col_idx, label in enumerate(headers, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=label)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="left", vertical="center")
            cell.border = cell_border

    def _auto_fit(ws: Worksheet) -> None:
        for col_idx, col in enumerate(ws.columns, start=1):
            max_len = 10
            col_letter = get_column_letter(col_idx)
            for cell in col:
                val_len = len(str(cell.value or ""))
                if val_len > max_len:
                    max_len = min(val_len, 48)
            ws.column_dimensions[col_letter].width = max_len + 4

    # Aba 1: Achados (pronta para filtrar e ordenar)
    ws_findings = wb.active
    if ws_findings is None:
        ws_findings = wb.create_sheet("Achados")
    ws_findings.title = "Achados"
    ws_findings.append(["Veredito:", summary.verdict_headline])
    ws_findings.cell(row=1, column=2).font = _xlsx_font(palette["text-primary"], bold=True)
    ws_findings.append(["Exposição estimada (R$):", float(summary.exposure_total)])
    ws_findings.cell(row=2, column=2).number_format = money_fmt
    ws_findings.cell(row=2, column=2).font = _xlsx_font(palette["bad-tx"], bold=True)
    ws_findings.append(["Notas sem divergência:", summary.clean_summary_line])
    ws_findings.append([])

    _style_headers(
        ws_findings,
        [
            "Gravidade",
            "Nota",
            "Competência",
            "Problema",
            "Valor",
            "Exposição (R$)",
            "Ação sugerida",
        ],
        row_idx=5,
    )
    for r_offset, f in enumerate(summary.findings, start=6):
        ws_findings.cell(row=r_offset, column=1, value=f.severity)
        ws_findings.cell(row=r_offset, column=2, value=f.invoices_label)
        ws_findings.cell(row=r_offset, column=3, value=f.competence)
        ws_findings.cell(row=r_offset, column=4, value=f.problem)
        ws_findings.cell(row=r_offset, column=5, value=f.amount_display)
        exp_cell = ws_findings.cell(
            row=r_offset,
            column=6,
            value=float(f.risk_amount) if f.risk_amount is not None else None,
        )
        if f.risk_amount is not None:
            exp_cell.number_format = money_fmt
        ws_findings.cell(row=r_offset, column=7, value=f.suggested_action)

        sev_tone: BadgeToneLiteral = (
            "bad" if f.severity == "Alta" else ("ai" if f.severity == "Média" else "info")
        )
        s_bg, _, s_tx = _tone_palette(sev_tone, palette)
        sev_cell = ws_findings.cell(row=r_offset, column=1)
        sev_cell.fill = _xlsx_fill(s_bg)
        sev_cell.font = _xlsx_font(s_tx, bold=True)
        for col_i in range(1, 8):
            ws_findings.cell(row=r_offset, column=col_i).border = cell_border
    ws_findings.auto_filter.ref = f"A5:G{max(5, 5 + len(summary.findings))}"
    _auto_fit(ws_findings)

    # Aba 2: Por Competência
    ws_comp = wb.create_sheet("Por Competência")
    _style_headers(
        ws_comp,
        [
            "Competência",
            "Notas",
            "Valor vigente (R$)",
            "Valor cobrado (R$)",
            "Diferença (R$)",
            "Situação",
        ],
    )
    for r_idx, row in enumerate(summary.competence_rows, start=2):
        ws_comp.cell(row=r_idx, column=1, value=row.competence_pt)
        ws_comp.cell(row=r_idx, column=2, value=row.invoices_count)
        c_exp = ws_comp.cell(
            row=r_idx,
            column=3,
            value=float(row.expected_total) if row.expected_total is not None else None,
        )
        if row.expected_total is not None:
            c_exp.number_format = money_fmt
        c_chg = ws_comp.cell(row=r_idx, column=4, value=float(row.charged_total))
        c_chg.number_format = money_fmt
        c_dif = ws_comp.cell(
            row=r_idx,
            column=5,
            value=float(row.difference_total) if row.difference_total is not None else None,
        )
        if row.difference_total is not None:
            c_dif.number_format = money_fmt
        st_cell = ws_comp.cell(row=r_idx, column=6, value=row.status_label)
        bg, _, tx = _tone_palette(row.tone, palette)
        st_cell.fill = _xlsx_fill(bg)
        st_cell.font = _xlsx_font(tx, bold=True)
        for col_i in range(1, 7):
            ws_comp.cell(row=r_idx, column=col_i).border = cell_border
    _auto_fit(ws_comp)

    # Aba 3: Cobranças completas
    ws_inv = wb.create_sheet("Cobranças")
    _style_headers(
        ws_inv,
        [
            "Competência",
            "Nota",
            "Fornecedor",
            "Contrato",
            "Valor vigente (R$)",
            "Total cobrado (R$)",
            "Diferença (R$)",
            "Situação",
        ],
    )
    ordered_all = sorted(audited_items, key=lambda pair: natural_invoice_sort_key(pair[0]))
    for r_idx, (inv, stored) in enumerate(ordered_all, start=2):
        res = stored.result
        tone, _, cls_label = AUDIT_CLASSIFICATION_BADGES[res.classification]
        ws_inv.cell(row=r_idx, column=1, value=format_competence_pt(res.reference_period))
        ws_inv.cell(row=r_idx, column=2, value=inv.number)
        ws_inv.cell(row=r_idx, column=3, value=inv.supplier)
        ws_inv.cell(row=r_idx, column=4, value=inv.contract_id or "Sem vínculo")
        v_exp = ws_inv.cell(
            row=r_idx, column=5, value=float(res.base) if res.base is not None else None
        )
        if res.base is not None:
            v_exp.number_format = money_fmt
        v_tot = ws_inv.cell(row=r_idx, column=6, value=float(res.total))
        v_tot.number_format = money_fmt
        v_dif = ws_inv.cell(
            row=r_idx,
            column=7,
            value=float(res.difference) if res.difference is not None else None,
        )
        if res.difference is not None:
            v_dif.number_format = money_fmt
        st_c = ws_inv.cell(row=r_idx, column=8, value=cls_label)
        bg, _, tx = _tone_palette(tone, palette)
        st_c.fill = _xlsx_fill(bg)
        st_c.font = _xlsx_font(tx, bold=True)
        for col_i in range(1, 9):
            ws_inv.cell(row=r_idx, column=col_i).border = cell_border
    ws_inv.auto_filter.ref = f"A1:H{max(1, 1 + len(ordered_all))}"
    _auto_fit(ws_inv)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
