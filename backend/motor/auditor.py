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
from backend.motor.compliance import (
    check_supplier_identity,
    check_tax_retentions,
    check_unapproved_adjustment_claim,
)
from backend.motor.consolidation import (
    check_amendment_review,
    check_competence_inferred,
    check_contract_link,
    consolidate_rules,
)
from backend.motor.duplicates import check_duplicates
from backend.motor.extenso import check_written_amount
from backend.motor.item_prices import (
    audit_item_prices,
    check_missing_glosa,
    has_matching_item_rules,
)
from backend.motor.items import (
    ITEM_CLASSIFICATION_CHECK,
    check_hourly_items,
    check_item_classification,
    check_items_arithmetic,
    check_items_total,
    classify_items,
    select_fixed_base_item,
)
from backend.motor.results import CheckOutcome, RuleSelection, SelectionStatus, ValidityStatus
from backend.motor.timeline import (
    apply_amendments,
    select_applicable_item_rules,
    select_applicable_rule,
)
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
    ReferencePeriod,
    ReviewStatus,
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
    *,
    item_base: Decimal | None = None,
) -> AuditResult:
    classification = (
        most_severe(check.result.classification for check in checks)
        if checks
        else AuditClassification.NO_DIVERGENCE
    )
    price_base = next((o.base for o in outcomes if o.check.name == "monthly_price"), None)
    if price_base is None and item_base is not None:
        price_base = item_base
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
    elif price_base is not None:
        difference = Decimal("0.00")
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


def _select_retention_rule(
    timeline_rules: Sequence[ContractRule],
    raw_rules: Sequence[ContractRule],
    rule_type: RuleType,
    period: ReferencePeriod,
) -> RuleSelection:
    sel = select_applicable_rule(_of_type(timeline_rules, rule_type), period)
    if sel.status is not SelectionStatus.NO_RULE:
        return sel
    fallback = [
        r
        for r in raw_rules
        if r.rule_type is rule_type and r.review_status is not ReviewStatus.REJECTED
    ]
    if not fallback:
        return sel
    return select_applicable_rule(fallback, period)


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
    Portões de segurança bloqueiam apenas a comparação de preço contratual/franquia;
    verificações independentes (retenções, soma, cálculo, glosa, cadastro, AF) sempre rodam.
    """
    period = invoice.reference_period
    checks: list[Check] = []
    applied: list[AppliedRule] = []
    outcomes: list[CheckOutcome] = []
    duplicate = check_duplicates([invoice, *other_invoices]).get(invoice.id)

    link = check_contract_link(contract, invoice)
    checks.append(link)
    hard_unlinked = (
        contract is None
        or invoice.contract_id is None
        or invoice.contract_id != contract.id
        or (invoice.total == 0 and not invoice.items)
    )
    if contract is None or hard_unlinked:
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
        contract,
        _of_type(timeline.rules, RuleType.EXTENSION),
        period,
        issue_date=invoice.issue_date,
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
        reason_codes = tuple(f"codigo = {reason.code.value}" for reason in validity.reasons)
        gates.append(
            Check(
                name=CONTRACT_VALIDITY_CHECK,
                result=CheckResult.REVIEW,
                direction=DivergenceDirection.UNVERIFIABLE,
                message=" ".join(reason.message for reason in validity.reasons),
                calculation_memory=reason_codes,
            )
        )
    checks.extend(gates)
    gates_ok = all(gate.result is CheckResult.OK for gate in gates)

    tolerance = contract.comparison_tolerance
    items = classify_items(invoice.items)
    all_item_rules = _of_type(timeline.rules, RuleType.ITEM_PRICE)
    price_selection = RuleSelection(status=SelectionStatus.NO_RULE)
    item_base: Decimal | None = None
    expected_gross_before_glosa: Decimal | None = None

    if gates_ok:
        use_item_price_engine = bool(all_item_rules) and (
            has_matching_item_rules(invoice.items, all_item_rules)
            or len(items.base_items) >= 2
            or not _of_type(timeline.rules, RuleType.MONTHLY_PRICE)
        )
        cls_check = check_item_classification(items)
        if (
            use_item_price_engine
            and cls_check.result is CheckResult.REVIEW
            and len(items.base_items) > 1
            and not items.ambiguous_items
        ):
            cls_check = Check(
                name=ITEM_CLASSIFICATION_CHECK,
                result=CheckResult.OK,
                message="Itens da cobrança auditados individualmente contra a tabela do contrato.",
            )
        checks.append(cls_check)
        checks.append(check_items_total(invoice.items, invoice.total, tolerance))

        if use_item_price_engine:
            active_item_selections = select_applicable_item_rules(all_item_rules, period)
            item_res = audit_item_prices(
                invoice,
                all_item_rules=all_item_rules,
                active_selections=active_item_selections,
                other_invoices=other_invoices,
                tolerance=tolerance,
                contract_supplier_cnpj=contract.supplier_cnpj,
            )
            checks.extend(item_res.checks)
            outcomes.extend(item_res.outcomes)
            applied.extend(item_res.applied_rules)
            item_base = item_res.expected_base
            expected_gross_before_glosa = item_res.expected_gross_before_glosa
        else:
            price_selection = select_applicable_rule(
                _of_type(timeline.rules, RuleType.MONTHLY_PRICE), period
            )
            if items.base_unambiguous:
                outcomes.append(
                    check_monthly_price(items.base_items[0].total_value, price_selection, tolerance)
                )
                applied.extend(_applied(price_selection))
            elif len(items.base_items) > 1:
                fixed_base = select_fixed_base_item(items.base_items)
                if fixed_base is not None and price_selection.status is SelectionStatus.SELECTED:
                    outcomes.append(
                        check_monthly_price(fixed_base.total_value, price_selection, tolerance)
                    )
                    applied.extend(_applied(price_selection))

            franchise_selection = select_applicable_rule(
                _of_type(timeline.rules, RuleType.FRANCHISE), period
            )
            overage_selection = select_applicable_rule(
                _of_type(timeline.rules, RuleType.OVERAGE_PRICE), period
            )
            if items.overage_items:
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
            else:
                hourly_checks, hourly_applied = check_hourly_items(
                    invoice,
                    franchise_selection=franchise_selection,
                    overage_selection=overage_selection,
                    tolerance=tolerance,
                )
                checks.extend(hourly_checks)
                applied.extend(hourly_applied)
            checks.extend(outcome.check for outcome in outcomes)
    else:
        # Mesmo com portão de preço, soma divergente dos itens não é silenciada.
        items_tot_chk = check_items_total(invoice.items, invoice.total, tolerance)
        if items_tot_chk.result is not CheckResult.OK and invoice.items:
            checks.append(items_tot_chk)

    # Verificações independentes da linha do tempo de preço: executam sempre.
    arith_check = check_items_arithmetic(invoice.items, tolerance)
    if arith_check is not None:
        checks.append(arith_check)

    glosa_outcome = check_missing_glosa(
        invoice,
        classification=items,
        expected_gross_before_glosa=expected_gross_before_glosa,
    )
    if glosa_outcome is not None:
        checks.append(glosa_outcome.check)
        outcomes.append(glosa_outcome)

    if gates_ok:
        extenso_check = check_written_amount(invoice, tolerance)
        if extenso_check is not None:
            checks.append(extenso_check)

        adjustment_rules = _of_type(timeline.rules, RuleType.ADJUSTMENT)
        if contract.validity.end is None or period.first_day <= contract.validity.end:
            adjustment = check_periodic_adjustment(adjustment_rules, price_selection, period)
            if adjustment is not None:
                checks.append(adjustment)
                applied.extend(
                    AppliedRule(rule_id=rule.id, version=rule.version)
                    for rule in adjustment_rules
                    if rule.id == adjustment.rule_id
                )
    else:
        adjustment_rules = _of_type(rules, RuleType.ADJUSTMENT)
        extenso_check = check_written_amount(invoice, tolerance)
        if extenso_check is not None and extenso_check.result is not CheckResult.OK:
            checks.append(extenso_check)

    identity_check = check_supplier_identity(contract, invoice)
    if identity_check is not None:
        checks.append(identity_check)

    inss_selection = _select_retention_rule(timeline.rules, rules, RuleType.INSS_RETENTION, period)
    iss_selection = _select_retention_rule(timeline.rules, rules, RuleType.ISS_RETENTION, period)
    retention_checks, retention_applied = check_tax_retentions(
        invoice,
        inss_selection=inss_selection,
        iss_selection=iss_selection,
        tolerance=tolerance,
    )
    checks.extend(retention_checks)
    applied.extend(retention_applied)

    unapproved_adj = check_unapproved_adjustment_claim(
        contract, invoice, adjustment_rules, amendments
    )
    if unapproved_adj is not None:
        checks.append(unapproved_adj)

    if duplicate is not None:
        checks.append(duplicate)
    return _build_result(invoice, checks, applied, outcomes, item_base=item_base)


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
