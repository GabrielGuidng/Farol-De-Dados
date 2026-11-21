"""Auditor: junta todas as verificações em um AuditResult com classificação e memória de cálculo.

A classificação principal é a mais grave (MISSING_DOCUMENTATION > DIVERGENCE > NEEDS_REVIEW >
NO_DIVERGENCE), mas todos os alertas continuam listados em `checks`.

Verificações de "porta" (vínculo com contrato, competência inferida, aditivos não revisados, regras
sem início, aditivos não aplicáveis, competência fora da vigência): se alguma não passar, o motor
NÃO compara valores, para nunca apontar divergência contra uma regra possivelmente errada.
"""

from collections.abc import Sequence
from decimal import Decimal

from backend.motor.adjustment import check_periodic_adjustment
from backend.motor.calculator import add_money
from backend.motor.checks import check_monthly_price, check_overage
from backend.motor.consolidation import (
    check_amendment_review,
    check_competence_inferred,
    check_contract_link,
    consolidate_rules,
)
from backend.motor.duplicates import check_duplicates
from backend.motor.items import (
    check_item_classification,
    check_items_total,
    classify_items,
)
from backend.motor.results import CheckOutcome, RuleSelection, SelectionStatus, ValidityStatus
from backend.motor.timeline import apply_amendments, select_applicable_rule
from backend.motor.validity import check_contract_validity
from backend.schemas import (
    Amendment,
    AppliedRule,
    AuditClassification,
    AuditResult,
    Check,
    CheckResult,
    Contract,
    ContractRule,
    DivergenceDirection,
    Document,
    Invoice,
    RuleType,
    most_severe,
)

CONTRACT_VALIDITY_CHECK = "contract_validity"
TIMELINE_CHECK = "rule_timeline"


def _of_type(rules: Sequence[ContractRule], rule_type: RuleType) -> list[ContractRule]:
    return [rule for rule in rules if rule.rule_type is rule_type]


def _applied(selection: RuleSelection) -> list[AppliedRule]:
    if selection.status is not SelectionStatus.SELECTED or selection.rule is None:
        return []
    return [AppliedRule(rule_id=selection.rule.id, version=selection.rule.version)]


def _build_result(
    invoice: Invoice,
    checks: list[Check],
    applied: list[AppliedRule],
    outcomes: list[CheckOutcome],
) -> AuditResult:
    classification = (
        most_severe(check.result.classification for check in checks)
        if checks
        else AuditClassification.NO_DIVERGENCE
    )
    price_base = next((o.base for o in outcomes if o.check.name == "monthly_price"), None)
    flagged = [
        outcome.difference
        for outcome in outcomes
        if outcome.difference is not None
        and (
            outcome.check.result is CheckResult.DIVERGENCE
            or outcome.direction is DivergenceDirection.POSSIBLE_DIVERGENCE
        )
    ]
    difference: Decimal | None = None
    if outcomes:
        difference = add_money(*flagged) if flagged else Decimal("0.00")
    unique_rules = list({(rule.rule_id, rule.version): rule for rule in applied}.values())
    return AuditResult(
        id=f"audit-{invoice.id}",
        invoice_id=invoice.id,
        classification=classification,
        checks=tuple(checks),
        applied_rules=tuple(unique_rules),
        reference_period=invoice.reference_period,
        base=price_base,
        difference=difference,
        total=invoice.total,
    )


def audit_invoice(
    invoice: Invoice,
    *,
    contract: Contract | None,
    rules: Sequence[ContractRule],
    amendments: Sequence[Amendment] = (),
    documents: Sequence[Document] = (),
    other_invoices: Sequence[Invoice] = (),
) -> AuditResult:
    """
    Audita uma cobrança contra o contrato, as regras, os aditivos e os documentos recebidos.
    `other_invoices` serve à detecção de duplicidade. Nunca levanta exceção de negócio.
    """
    period = invoice.reference_period
    checks: list[Check] = []
    applied: list[AppliedRule] = []
    outcomes: list[CheckOutcome] = []
    duplicate = check_duplicates([invoice, *other_invoices]).get(invoice.id)

    link = check_contract_link(contract, invoice)
    checks.append(link)
    if contract is None or link.result is CheckResult.PENDING:
        if duplicate is not None:
            checks.append(duplicate)
        return _build_result(invoice, checks, applied, outcomes)

    gates: list[Check] = [
        check_competence_inferred(invoice),
        check_amendment_review(amendments, documents, period),
    ]
    consolidation = consolidate_rules(rules)
    gates.append(consolidation.check)
    timeline = apply_amendments(consolidation.rules, amendments)
    if not timeline.complete:
        gates.append(
            Check(
                name=TIMELINE_CHECK,
                result=CheckResult.REVIEW,
                direction=DivergenceDirection.UNVERIFIABLE,
                message="Aditivo não aplicado: " + " ".join(i.message for i in timeline.issues),
                calculation_memory=tuple(issue.message for issue in timeline.issues),
            )
        )
    validity = check_contract_validity(
        contract, _of_type(timeline.rules, RuleType.EXTENSION), period
    )
    if validity.status is ValidityStatus.WITHIN:
        gates.append(
            Check(
                name=CONTRACT_VALIDITY_CHECK,
                result=CheckResult.OK,
                message="A competência está dentro da vigência do contrato.",
            )
        )
    else:
        gates.append(
            Check(
                name=CONTRACT_VALIDITY_CHECK,
                result=CheckResult.REVIEW,
                direction=DivergenceDirection.UNVERIFIABLE,
                message=" ".join(reason.message for reason in validity.reasons),
            )
        )
    checks.extend(gates)
    if any(gate.result is not CheckResult.OK for gate in gates):
        if duplicate is not None:
            checks.append(duplicate)
        return _build_result(invoice, checks, applied, outcomes)

    tolerance = contract.comparison_tolerance
    items = classify_items(invoice.items)
    checks.append(check_item_classification(items))
    checks.append(check_items_total(invoice.items, invoice.total, tolerance))

    price_selection = select_applicable_rule(
        _of_type(timeline.rules, RuleType.MONTHLY_PRICE), period
    )
    if items.base_unambiguous:
        outcomes.append(
            check_monthly_price(items.base_items[0].total_value, price_selection, tolerance)
        )
        applied.extend(_applied(price_selection))

    if items.overage_items:
        franchise_selection = select_applicable_rule(
            _of_type(timeline.rules, RuleType.FRANCHISE), period
        )
        overage_selection = select_applicable_rule(
            _of_type(timeline.rules, RuleType.OVERAGE_PRICE), period
        )
        outcomes.extend(
            check_overage(
                items.overage_hours,
                items.overage_amount,
                invoice.measured_hours,
                franchise_selection,
                overage_selection,
                tolerance,
            )
        )
        applied.extend(_applied(franchise_selection))
        applied.extend(_applied(overage_selection))
    checks.extend(outcome.check for outcome in outcomes)

    adjustment_rules = _of_type(timeline.rules, RuleType.ADJUSTMENT)
    adjustment = check_periodic_adjustment(adjustment_rules, price_selection, period)
    if adjustment is not None:
        checks.append(adjustment)
        applied.extend(
            AppliedRule(rule_id=rule.id, version=rule.version)
            for rule in adjustment_rules
            if rule.id == adjustment.rule_id
        )
    if duplicate is not None:
        checks.append(duplicate)
    return _build_result(invoice, checks, applied, outcomes)


def audit_invoices(
    invoices: Sequence[Invoice],
    *,
    contract: Contract | None,
    rules: Sequence[ContractRule],
    amendments: Sequence[Amendment] = (),
    documents: Sequence[Document] = (),
) -> tuple[AuditResult, ...]:
    """Audita várias cobranças do mesmo contrato; cada uma vê as demais para a duplicidade."""
    return tuple(
        audit_invoice(
            invoice,
            contract=contract,
            rules=rules,
            amendments=amendments,
            documents=documents,
            other_invoices=[other for other in invoices if other.id != invoice.id],
        )
        for invoice in invoices
    )
