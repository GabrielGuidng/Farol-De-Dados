"""Auditoria determinística de ordens de pagamento, certidões e reconciliação (P-05 a P-09)."""

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from backend.motor.calculator import add_money, subtract_money
from backend.motor.formatting import format_money
from backend.motor.timeline import select_applicable_rule
from backend.schemas import (
    CertificateType,
    Check,
    CheckResult,
    Contract,
    ContractRule,
    DivergenceDirection,
    DossierFinancialSummary,
    Invoice,
    PaymentOrder,
    ReferencePeriod,
    RuleType,
    StrictModel,
    TaxCertificate,
    normalize_cnpj,
)

PAYMENT_BENEFICIARY_CHECK = "payment_beneficiary"
PAYMENT_ATTESTATION_CHECK = "payment_before_attestation"
PAYMENT_SEGREGATION_CHECK = "payment_segregation"
PAYMENT_APPROVAL_CHECK = "payment_approval_limit"
PAYMENT_BUSINESS_DAY_CHECK = "payment_business_day"
PAYMENT_DUPLICATE_CHECK = "payment_duplicate"

FindingSeverityLiteral = Literal["Alta", "Média", "Baixa"]

_ZERO = Decimal("0.00")
_WEEKDAY_NAMES_PT: tuple[str, ...] = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
_CERTIFICATE_LABELS: dict[CertificateType, str] = {
    CertificateType.CND_FEDERAL: "CND Federal (RFB/PGFN)",
    CertificateType.CRF_FGTS: "CRF do FGTS (Caixa)",
    CertificateType.CNDT: "CNDT Trabalhista (TST)",
    CertificateType.CND_ESTADUAL: "CND Estadual",
    CertificateType.CND_MUNICIPAL: "CND Municipal",
    CertificateType.CONTRACT_GUARANTEE: "Seguro-garantia contratual",
}


class PaymentFinding(StrictModel):
    """Achado financeiro ou de controle interno oriundo de OP, certidão ou reconciliação."""

    code: str
    contract_id: str | None = None
    severity: FindingSeverityLiteral
    op_numbers: tuple[str, ...] = ()
    invoice_numbers: tuple[str, ...] = ()
    competence_period: ReferencePeriod | None = None
    problem: str
    risk_amount: Decimal | None = None
    suggested_action: str
    details: tuple[str, ...] = ()


class PaymentCycleAuditResult(StrictModel):
    """Resultado consolidado dos procedimentos P-05 a P-09 sobre pagamentos e certidões."""

    payment_orders_count: int = 0
    certificates_count: int = 0
    total_net_paid: Decimal = _ZERO
    checks_by_invoice_number: dict[str, tuple[Check, ...]] = {}
    findings: tuple[PaymentFinding, ...] = ()
    duplicate_op_pairs: dict[str, tuple[str, ...]] = {}


def _clean_bank_token(raw: str | None) -> str:
    if raw is None:
        return ""
    cleaned = re.sub(r"[^0-9A-Za-z]", "", raw.strip()).lstrip("0")
    return cleaned.casefold()


def _format_date_br(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _resolve_linked_invoice(
    op: PaymentOrder,
    by_id: dict[str, Invoice],
    by_number: dict[str, Invoice],
) -> Invoice | None:
    if op.invoice_id and op.invoice_id in by_id:
        return by_id[op.invoice_id]
    return by_number.get(op.invoice_number.strip().casefold())


def _op_cnpj_digits(op: PaymentOrder) -> str | None:
    if op.beneficiary_cnpj:
        return normalize_cnpj(op.beneficiary_cnpj)
    if op.raw_beneficiary_cnpj:
        digits = normalize_cnpj(op.raw_beneficiary_cnpj)
        return digits or None
    return None


def _approval_limit_for_op(
    op: PaymentOrder,
    approval_rules: Sequence[ContractRule],
    period: ReferencePeriod,
) -> Decimal | None:
    if op.approver_limit is not None:
        return op.approver_limit
    selection = select_applicable_rule(approval_rules, period)
    if selection.rule is not None and selection.rule.value is not None:
        return selection.rule.value
    for rule in approval_rules:
        if rule.value is not None:
            return rule.value
    return None


def audit_payment_cycle(
    contract: Contract | None,
    *,
    rules: Sequence[ContractRule] = (),
    invoices: Sequence[Invoice] = (),
    payment_orders: Sequence[PaymentOrder] = (),
    certificates: Sequence[TaxCertificate] = (),
    financial_summaries: Sequence[DossierFinancialSummary] = (),
) -> PaymentCycleAuditResult:
    """Executa verificações determinísticas de P-05 a P-09 sobre OPs, certidões e resumos."""
    if not payment_orders and not certificates and not financial_summaries:
        return PaymentCycleAuditResult()

    by_id = {inv.id: inv for inv in invoices}
    by_number = {inv.number.strip().casefold(): inv for inv in invoices}
    approval_rules = [rule for rule in rules if rule.rule_type is RuleType.APPROVAL_LIMIT]

    checks_map: dict[str, list[Check]] = {}
    findings: list[PaymentFinding] = []
    duplicate_op_pairs: dict[str, tuple[str, ...]] = {}

    def _add_check(invoice_number: str, chk: Check) -> None:
        checks_map.setdefault(invoice_number.strip().casefold(), []).append(chk)

    for op in payment_orders:
        linked_inv = _resolve_linked_invoice(op, by_id, by_number)
        comp = (
            linked_inv.reference_period
            if linked_inv is not None
            else ReferencePeriod(year=op.payment_date.year, month=op.payment_date.month)
        )

        # 1. Favorecido / CNPJ / Domicílio bancário (P-06)
        if contract is not None:
            op_cnpj = _op_cnpj_digits(op)
            cnpj_differs = op_cnpj is not None and op_cnpj != contract.supplier_cnpj
            bank_differs = bool(
                contract.bank_code
                and op.bank_code
                and _clean_bank_token(contract.bank_code) != _clean_bank_token(op.bank_code)
            )
            account_differs = bool(
                contract.bank_account
                and op.bank_account
                and _clean_bank_token(contract.bank_account) != _clean_bank_token(op.bank_account)
            )
            if cnpj_differs or bank_differs or account_differs:
                reasons: list[str] = []
                if cnpj_differs:
                    shown_cnpj = op.raw_beneficiary_cnpj or op_cnpj or "não informado"
                    reasons.append(
                        f"CNPJ favorecido {shown_cnpj} ({op.beneficiary_name}) difere do "
                        f"contrato ({contract.supplier_cnpj})"
                    )
                if bank_differs or account_differs:
                    reasons.append(
                        f"domicílio bancário {op.bank_code or '—'}/"
                        f"{op.bank_agency or '—'}/{op.bank_account or '—'} difere do cadastrado "
                        f"({contract.bank_code or '—'}/{contract.bank_agency or '—'}/"
                        f"{contract.bank_account or '—'})"
                    )
                detail_msg = "; ".join(reasons)
                _add_check(
                    op.invoice_number,
                    Check(
                        name=PAYMENT_BENEFICIARY_CHECK,
                        result=CheckResult.DIVERGENCE,
                        direction=DivergenceDirection.OVERCHARGE,
                        message=(
                            f"OP {op.op_number} paga em {_format_date_br(op.payment_date)} para "
                            f"terceiro/conta divergente: {detail_msg}."
                        ),
                        calculation_memory=(
                            f"ordem de pagamento = {op.op_number}",
                            f"valor líquido pago = {format_money(op.paid_amount)}",
                            f"favorecido na OP = {op.beneficiary_name}",
                        ),
                    ),
                )
                findings.append(
                    PaymentFinding(
                        code="PAYMENT_BENEFICIARY",
                        severity="Alta",
                        op_numbers=(op.op_number,),
                        invoice_numbers=(op.invoice_number,),
                        competence_period=comp,
                        problem="Pagamento efetuado a CNPJ/conta bancária de terceiro",
                        risk_amount=op.paid_amount,
                        suggested_action=(
                            "Apurar desvio de favorecido/conta bancária e bloquear novos créditos"
                        ),
                        details=(detail_msg,),
                    )
                )

        # 2. Pagamento anterior ao atesto (P-05 / Cláusula 4ª)
        if (
            linked_inv is not None
            and linked_inv.attestation_date is not None
            and op.payment_date < linked_inv.attestation_date
        ):
            att_br = _format_date_br(linked_inv.attestation_date)
            pay_br = _format_date_br(op.payment_date)
            _add_check(
                op.invoice_number,
                Check(
                    name=PAYMENT_ATTESTATION_CHECK,
                    result=CheckResult.DIVERGENCE,
                    direction=DivergenceDirection.UNVERIFIABLE,
                    message=(
                        f"OP {op.op_number} paga em {pay_br}, antes do atesto da NF "
                        f"{op.invoice_number} em {att_br} (vedado pela Cláusula 4ª)."
                    ),
                    calculation_memory=(
                        f"data do pagamento = {pay_br}",
                        f"data do atesto = {att_br}",
                    ),
                ),
            )
            findings.append(
                PaymentFinding(
                    code="PAYMENT_BEFORE_ATTESTATION",
                    severity="Alta",
                    op_numbers=(op.op_number,),
                    invoice_numbers=(op.invoice_number,),
                    competence_period=comp,
                    problem="Pagamento realizado antes do atesto da nota",
                    risk_amount=None,
                    suggested_action=(
                        "Exigir justificativa e implantar trava sistêmica de atesto prévio"
                    ),
                    details=(f"OP {op.op_number} paga em {pay_br}; atesto somente em {att_br}.",),
                )
            )

        # 3. Segregação de funções solicitante == aprovador (P-07 / Cláusula 6ª)
        req = (op.requester_id or "").strip()
        app = (op.approver_id or "").strip()
        if req and app and req.casefold() == app.casefold():
            _add_check(
                op.invoice_number,
                Check(
                    name=PAYMENT_SEGREGATION_CHECK,
                    result=CheckResult.DIVERGENCE,
                    direction=DivergenceDirection.UNVERIFIABLE,
                    message=(
                        f"OP {op.op_number} possui o mesmo usuário como solicitante e aprovador "
                        f"(matrícula {req}), violando a segregação de funções."
                    ),
                    calculation_memory=(
                        f"solicitante = {req}",
                        f"aprovador = {app}",
                    ),
                ),
            )
            findings.append(
                PaymentFinding(
                    code="PAYMENT_SEGREGATION",
                    severity="Média",
                    op_numbers=(op.op_number,),
                    invoice_numbers=(op.invoice_number,),
                    competence_period=comp,
                    problem="Solicitante do pagamento igual ao aprovador",
                    risk_amount=None,
                    suggested_action="Corrigir alçadas e matriz de perfis no módulo financeiro",
                    details=(f"OP {op.op_number}: solicitante e aprovador matrícula {req}.",),
                )
            )

        # 4. Alçada de aprovação (P-07 / Cláusula 6ª)
        limit = _approval_limit_for_op(op, approval_rules, comp)
        gross_value = linked_inv.total if linked_inv is not None else op.paid_amount
        is_director = "diretor" in (op.approver_role or "").casefold()
        if limit is not None and gross_value > limit and not is_director:
            role_txt = op.approver_role or f"matrícula {app or 'não informada'}"
            _add_check(
                op.invoice_number,
                Check(
                    name=PAYMENT_APPROVAL_CHECK,
                    result=CheckResult.DIVERGENCE,
                    direction=DivergenceDirection.UNVERIFIABLE,
                    message=(
                        f"OP {op.op_number} ({format_money(gross_value)} brutos) aprovada acima da "
                        f"alçada de {format_money(limit)} por {role_txt}; exige Diretoria."
                    ),
                    calculation_memory=(
                        f"valor bruto da NF = {format_money(gross_value)}",
                        f"limite do aprovador = {format_money(limit)}",
                        f"aprovador = {role_txt}",
                    ),
                ),
            )
            findings.append(
                PaymentFinding(
                    code="PAYMENT_APPROVAL_LIMIT",
                    severity="Média",
                    op_numbers=(op.op_number,),
                    invoice_numbers=(op.invoice_number,),
                    competence_period=comp,
                    problem="Pagamento aprovado acima da alçada permitida",
                    risk_amount=None,
                    suggested_action="Submeter o pagamento excedente à ratificação da Diretoria",
                    details=(
                        f"OP {op.op_number} com bruto {format_money(gross_value)} > teto "
                        f"{format_money(limit)} ({role_txt}).",
                    ),
                )
            )

        # 5. Liquidação em fim de semana / fora de dia útil (P-05 / Cláusula 4ª)
        weekday = op.payment_date.weekday()
        if weekday >= 5:
            day_name = _WEEKDAY_NAMES_PT[weekday]
            pay_br = _format_date_br(op.payment_date)
            _add_check(
                op.invoice_number,
                Check(
                    name=PAYMENT_BUSINESS_DAY_CHECK,
                    result=CheckResult.REVIEW,
                    direction=DivergenceDirection.UNVERIFIABLE,
                    message=(
                        f"OP {op.op_number} liquidada em {pay_br} ({day_name}), contrariando "
                        "a cláusula de pagamento exclusivamente em dias úteis."
                    ),
                    calculation_memory=(f"data do pagamento = {pay_br} ({day_name})",),
                ),
            )
            findings.append(
                PaymentFinding(
                    code="PAYMENT_NON_BUSINESS_DAY",
                    severity="Baixa",
                    op_numbers=(op.op_number,),
                    invoice_numbers=(op.invoice_number,),
                    competence_period=comp,
                    problem=f"Pagamento realizado em {day_name} (fora de dia útil)",
                    risk_amount=None,
                    suggested_action="Ajustar agenda de liquidação para dias úteis",
                    details=(f"OP {op.op_number} paga em {pay_br} ({day_name}).",),
                )
            )

    # 6. Duplicidade de OPs associadas a NFs da mesma competência e mesmo valor líquido
    by_period_amount: dict[tuple[str, Decimal], list[PaymentOrder]] = {}
    for op in payment_orders:
        linked_inv = _resolve_linked_invoice(op, by_id, by_number)
        period_key = (
            str(linked_inv.reference_period)
            if linked_inv is not None
            else f"{op.payment_date.year:04d}-{op.payment_date.month:02d}"
        )
        by_period_amount.setdefault((period_key, op.paid_amount), []).append(op)

    for (period_key, paid_amt), bucket in by_period_amount.items():
        if len(bucket) < 2:
            continue
        op_nums = tuple(item.op_number for item in bucket)
        for item in bucket:
            others = ", ".join(other.op_number for other in bucket if other.id != item.id)
            _add_check(
                item.invoice_number,
                Check(
                    name=PAYMENT_DUPLICATE_CHECK,
                    result=CheckResult.REVIEW,
                    direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
                    message=(
                        f"OP {item.op_number} ({format_money(paid_amt)}) possui outra ordem de "
                        f"pagamento na mesma competência {period_key} ({others})."
                    ),
                    calculation_memory=(f"ordens de pagamento no mês = {', '.join(op_nums)}",),
                ),
            )
            duplicate_op_pairs[item.invoice_number.strip().casefold()] = op_nums

    # 7. Certidões de regularidade vigentes na data de cada pagamento (P-08 / Cláusula 7ª)
    if certificates and payment_orders:
        certs_by_type: dict[CertificateType, list[TaxCertificate]] = {}
        for cert in certificates:
            certs_by_type.setdefault(cert.certificate_type, []).append(cert)

        for cert_type in sorted(certs_by_type.keys(), key=lambda c: c.value):
            cert_list = certs_by_type[cert_type]
            has_explicit_gap = any(
                "nao localizad" in (cert.raw_status or "").casefold()
                or "não localizad" in (cert.raw_status or "").casefold()
                for cert in cert_list
            ) or any(
                c1.expiry_date < c2.issue_date
                for c1 in cert_list
                for c2 in cert_list
                if c2.issue_date is not None
            )
            affected_ops: list[PaymentOrder] = []
            for op in payment_orders:
                valid_on_date = any(
                    (
                        (
                            cert.issue_date is None
                            or op.payment_date >= cert.issue_date
                            or not has_explicit_gap
                        )
                        and op.payment_date <= cert.expiry_date
                    )
                    for cert in cert_list
                )
                if not valid_on_date:
                    affected_ops.append(op)
            if not affected_ops:
                continue
            label = _CERTIFICATE_LABELS.get(cert_type, cert_type.value)
            latest_expiry = max(cert.expiry_date for cert in cert_list)
            first_affected = affected_ops[0]
            first_inv = _resolve_linked_invoice(first_affected, by_id, by_number)
            first_comp = (
                first_inv.reference_period
                if first_inv is not None
                else ReferencePeriod(
                    year=first_affected.payment_date.year,
                    month=first_affected.payment_date.month,
                )
            )
            findings.append(
                PaymentFinding(
                    code=f"CERTIFICATE_{cert_type.value}",
                    severity="Média",
                    op_numbers=tuple(op.op_number for op in affected_ops),
                    invoice_numbers=tuple(op.invoice_number for op in affected_ops),
                    competence_period=first_comp,
                    problem=(f"{label} vencida ou ausente em {len(affected_ops)} pagamento(s)"),
                    risk_amount=None,
                    suggested_action=(
                        "Exigir certidão negativa vigente antes da liberação de novos pagamentos"
                    ),
                    details=(
                        f"Última validade registrada: {_format_date_br(latest_expiry)}.",
                        f"OPs afetadas: {', '.join(op.op_number for op in affected_ops)}.",
                    ),
                )
            )

    # 8. Reconciliação aritmética do resumo financeiro (P-09)
    total_net_paid = (
        add_money(*(op.paid_amount for op in payment_orders)) if payment_orders else _ZERO
    )
    total_gross_billed = add_money(*(inv.total for inv in invoices)) if invoices else _ZERO

    for summary in financial_summaries:
        if summary.reported_net_paid_total is not None and payment_orders:
            net_diff = subtract_money(total_net_paid, summary.reported_net_paid_total)
            if net_diff != _ZERO:
                findings.append(
                    PaymentFinding(
                        code="SUMMARY_NET_PAID_MISMATCH",
                        severity="Baixa",
                        problem=(
                            "Resumo financeiro diverge em "
                            f"{format_money(abs(net_diff))} da soma das ordens de pagamento"
                        ),
                        risk_amount=None,
                        suggested_action=(
                            "Reconciliar o total líquido do quadro resumo com as OPs da tesouraria"
                        ),
                        details=(
                            f"Líquido informado no resumo: "
                            f"{format_money(summary.reported_net_paid_total)}",
                            f"Soma real das {len(payment_orders)} OPs: "
                            f"{format_money(total_net_paid)}",
                        ),
                    )
                )
        if summary.reported_gross_total is not None and invoices:
            gross_diff = subtract_money(total_gross_billed, summary.reported_gross_total)
            if gross_diff != _ZERO:
                findings.append(
                    PaymentFinding(
                        code="SUMMARY_GROSS_MISMATCH",
                        severity="Baixa",
                        problem=(
                            "Resumo financeiro diverge em "
                            f"{format_money(abs(gross_diff))} da soma bruta das notas"
                        ),
                        risk_amount=None,
                        suggested_action="Reconciliar o total bruto informado com as NFs emitidas",
                        details=(
                            f"Bruto informado no resumo: "
                            f"{format_money(summary.reported_gross_total)}",
                            f"Soma real das {len(invoices)} NFs: "
                            f"{format_money(total_gross_billed)}",
                        ),
                    )
                )

    frozen_checks = {key: tuple(val) for key, val in checks_map.items()}
    cid = contract.id if contract is not None else None
    scoped_findings = tuple(
        f.model_copy(update={"contract_id": cid})
        if f.contract_id is None and cid is not None
        else f
        for f in findings
    )
    return PaymentCycleAuditResult(
        payment_orders_count=len(payment_orders),
        certificates_count=len(certificates),
        total_net_paid=total_net_paid,
        checks_by_invoice_number=frozen_checks,
        findings=scoped_findings,
        duplicate_op_pairs=duplicate_op_pairs,
    )
