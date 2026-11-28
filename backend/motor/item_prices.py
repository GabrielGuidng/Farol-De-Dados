"""Auditoria determinística por item/posto, saldo acumulado de Ata e glosas (100% Decimal)."""

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from backend.motor.calculator import add_money, multiply_money, round_money, subtract_money
from backend.motor.duplicates import extract_af_tokens
from backend.motor.formatting import describe_rule_source, format_money, format_quantity
from backend.motor.items import ItemClassification, classify_items
from backend.motor.results import CheckOutcome, RuleSelection, SelectionStatus
from backend.motor.timeline import normalize_item_key
from backend.schemas import (
    AppliedRule,
    BillingItem,
    Check,
    CheckResult,
    ContractRule,
    DivergenceDirection,
    Invoice,
    StrictModel,
)

UNREGISTERED_ITEM_CHECK = "unregistered_item"
ITEM_UNIT_PRICE_CHECK = "item_unit_price"
ITEM_QUANTITY_MONTHLY_CHECK = "item_quantity_monthly"
ITEM_QUANTITY_CUMULATIVE_CHECK = "item_quantity_cumulative"
MISSING_GLOSA_CHECK = "missing_glosa"

_ZERO = Decimal("0.00")
_GLOSA_TOLERANCE = Decimal("0.01")


class ItemPricesAuditResult(StrictModel):
    """Resultado completo da verificação por itens, quantidades, saldos e glosas."""

    checks: tuple[Check, ...] = ()
    outcomes: tuple[CheckOutcome, ...] = ()
    applied_rules: tuple[AppliedRule, ...] = ()
    expected_base: Decimal | None = None
    expected_gross_before_glosa: Decimal | None = None


def match_invoice_item_to_rule_key(description: str, item_keys: Iterable[str]) -> str | None:
    """Pareia a descrição de um item cobrado a uma chave normalizada de regra `ITEM_PRICE`."""
    norm = normalize_item_key(description)
    if not norm:
        return None
    keys_list = [k for k in item_keys if k]
    if norm in keys_list:
        return norm

    # Substring direta apenas quando ambos os termos têm identidade substancial.
    substring_hits = [
        key
        for key in keys_list
        if len(key) >= 6 and (key in norm or (len(norm) >= 6 and norm in key))
    ]
    if substring_hits:
        return max(substring_hits, key=len)

    norm_tokens = set(norm.split())
    best_key: str | None = None
    best_score = 0
    for key in keys_list:
        key_tokens = key.split()
        if not key_tokens:
            continue
        # Exige presença integral dos tokens da regra e do primeiro substantivo identificador.
        if key_tokens[0] in norm_tokens and all(tok in norm_tokens for tok in key_tokens):
            score = len(key_tokens) * 10 + len(key)
            if score > best_score:
                best_score = score
                best_key = key
    if best_key is not None:
        return best_key

    # Fallback controlado: interseção de tokens nominais fortes (sem float por M-16).
    norm_sig = {tok for tok in norm.split() if len(tok) >= 2 and not tok.isdigit()}
    ranked: list[tuple[int, str]] = []
    for key in keys_list:
        key_sig = [tok for tok in key.split() if len(tok) >= 2 and not tok.isdigit()]
        if len(key_sig) < 2 or not norm_sig:
            continue
        overlap = sum(1 for tok in key_sig if tok in norm_sig)
        min_len = min(len(key_sig), len(norm_sig))
        if overlap >= 2 and overlap * 5 >= min_len * 3:
            ranked.append((overlap * 100 - len(key_sig), key))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def _order_key(invoice: Invoice) -> tuple[object, str, str]:
    return (invoice.issue_date, invoice.number.strip().casefold(), invoice.id)


def _prior_invoices_for_cumulative(
    invoice: Invoice,
    other_invoices: Sequence[Invoice],
    *,
    contract_supplier_cnpj: str | None,
) -> list[Invoice]:
    """Filtra notas anteriores válidas do mesmo contrato (sem duplicidade de AF/terceiro)."""
    current_key = _order_key(invoice)
    current_afs = set(extract_af_tokens(invoice))
    seen_afs: set[str] = set()
    eligible: list[Invoice] = []
    for cand in sorted(other_invoices, key=_order_key):
        if cand.contract_id != invoice.contract_id:
            continue
        if (
            contract_supplier_cnpj is not None
            and contract_supplier_cnpj != "00000000000191"
            and cand.supplier_cnpj != contract_supplier_cnpj
        ):
            continue
        if _order_key(cand) >= current_key:
            continue
        cand_afs = set(extract_af_tokens(cand))
        if current_afs and (cand_afs & current_afs):
            continue
        if cand_afs and (cand_afs & seen_afs):
            continue
        seen_afs.update(cand_afs)
        eligible.append(cand)
    return eligible


def _future_matching_rule(
    item_key: str,
    charged_unit: Decimal,
    invoice: Invoice,
    all_item_rules: Sequence[ContractRule],
) -> ContractRule | None:
    charged_rounded = round_money(charged_unit)
    for rule in all_item_rules:
        if normalize_item_key(rule.item_name) != item_key:
            continue
        if (
            rule.validity.start is not None
            and rule.validity.start > invoice.reference_period.last_day
        ):
            if rule.value is not None and round_money(rule.value) == charged_rounded:
                return rule
    return None


def _compute_expected_monthly_gross(
    active_selections: Mapping[str, RuleSelection],
) -> Decimal | None:
    rules = [
        sel.rule
        for sel in active_selections.values()
        if sel.status is SelectionStatus.SELECTED and sel.rule is not None
    ]
    if not rules:
        return None
    if not all(
        r.quantity_scope in (None, "MONTHLY") and r.quantity is not None and r.value is not None
        for r in rules
    ):
        return None
    terms = [
        multiply_money(r.quantity, r.value)
        for r in rules
        if r.quantity is not None and r.value is not None
    ]
    return add_money(*terms) if terms else None


def check_missing_glosa(
    invoice: Invoice,
    *,
    classification: ItemClassification | None = None,
    expected_gross_before_glosa: Decimal | None = None,
) -> CheckOutcome | None:
    """Verifica se a glosa apurada pela fiscalização foi devidamente descontada na nota."""
    if invoice.expected_glosa is None or invoice.expected_glosa <= _ZERO:
        return None

    cls = classification if classification is not None else classify_items(invoice.items)
    discount_sum = (
        add_money(*(it.total_value for it in cls.discount_items)) if cls.discount_items else _ZERO
    )
    implicit_discount = _ZERO
    if expected_gross_before_glosa is not None and invoice.total < expected_gross_before_glosa:
        implicit_discount = subtract_money(expected_gross_before_glosa, invoice.total)
    applied_glosa = max(discount_sum, implicit_discount)
    missing = subtract_money(invoice.expected_glosa, applied_glosa)

    memory = (
        f"glosa devida (frequência) = {format_money(invoice.expected_glosa)}",
        f"desconto aplicado na nota = {format_money(applied_glosa)}",
        f"glosa faltante = {format_money(missing)}",
    )
    if missing > _GLOSA_TOLERANCE:
        return CheckOutcome(
            check=Check(
                name=MISSING_GLOSA_CHECK,
                result=CheckResult.DIVERGENCE,
                direction=DivergenceDirection.OVERCHARGE,
                message=(
                    f"Glosa de {format_money(invoice.expected_glosa)} apurada no relatório de "
                    f"frequência não foi descontada na nota (desconto aplicado: "
                    f"{format_money(applied_glosa)}; cobrança a maior de {format_money(missing)})."
                ),
                calculation_memory=memory,
            ),
            direction=DivergenceDirection.OVERCHARGE,
            base=None,
            difference=missing,
        )

    return CheckOutcome(
        check=Check(
            name=MISSING_GLOSA_CHECK,
            result=CheckResult.OK,
            message=(
                f"Glosa de {format_money(invoice.expected_glosa)} devidamente abatida na cobrança."
            ),
            calculation_memory=memory,
        ),
        difference=_ZERO,
    )


def audit_item_prices(
    invoice: Invoice,
    *,
    all_item_rules: Sequence[ContractRule],
    active_selections: Mapping[str, RuleSelection],
    other_invoices: Sequence[Invoice],
    tolerance: Decimal,
    contract_supplier_cnpj: str | None = None,
) -> ItemPricesAuditResult:
    """
    Audita cobranças item a item contra regras `ITEM_PRICE` do contrato ou Ata:
    - itens não previstos (`unregistered_item`);
    - preço unitário divergente e repactuação/aditivo antecipado (`item_unit_price`);
    - quantidade mensal excedente (`item_quantity_monthly`);
    - saldo acumulado de Ata excedido (`item_quantity_cumulative`).
    """
    if not all_item_rules:
        return ItemPricesAuditResult()

    cls = classify_items(invoice.items)
    billable_items = [*cls.base_items, *cls.overage_items, *cls.ambiguous_items]
    all_keys = {
        normalize_item_key(rule.item_name)
        for rule in all_item_rules
        if normalize_item_key(rule.item_name)
    }

    checks: list[Check] = []
    outcomes: list[CheckOutcome] = []
    applied_rules: list[AppliedRule] = []
    matched_qty_by_key: dict[str, Decimal] = {}
    matched_items_by_key: dict[str, list[BillingItem]] = {}
    unregistered_items: list[BillingItem] = []

    for item in billable_items:
        matched_key = match_invoice_item_to_rule_key(item.description, all_keys)
        if matched_key is None:
            unregistered_items.append(item)
            continue
        matched_items_by_key.setdefault(matched_key, []).append(item)
        matched_qty_by_key[matched_key] = matched_qty_by_key.get(matched_key, _ZERO) + item.quantity

    # 1. Itens não registrados no contrato / Ata
    if unregistered_items:
        unreg_total = add_money(*(it.total_value for it in unregistered_items))
        unreg_desc = "; ".join(
            f"{it.description} ({format_quantity(it.quantity)} × {format_money(it.unit_value)} = "
            f"{format_money(it.total_value)})"
            for it in unregistered_items
        )
        outcome = CheckOutcome(
            check=Check(
                name=UNREGISTERED_ITEM_CHECK,
                result=CheckResult.DIVERGENCE,
                direction=DivergenceDirection.OVERCHARGE,
                message=(
                    f"Item não constante do contrato/Ata cobrado na nota ({unreg_desc}), "
                    f"gerando cobrança indevida de {format_money(unreg_total)}."
                ),
                calculation_memory=(
                    f"itens não previstos = {unreg_desc}",
                    f"valor cobrado sem previsão = {format_money(unreg_total)}",
                ),
            ),
            direction=DivergenceDirection.OVERCHARGE,
            base=_ZERO,
            difference=unreg_total,
        )
        checks.append(outcome.check)
        outcomes.append(outcome)

    # 2. Preço unitário, quantidade mensal e saldo acumulado
    unit_details: list[str] = []
    unit_memory: list[str] = []
    unit_diff_terms: list[Decimal] = []
    unit_trusted = True
    unit_premature_dates: list[str] = []
    unit_rule_ids: list[str] = []

    prior_cache = _prior_invoices_for_cumulative(
        invoice,
        other_invoices,
        contract_supplier_cnpj=contract_supplier_cnpj,
    )

    delivery_expected_terms: list[Decimal] = []

    for key, key_items in matched_items_by_key.items():
        selection = active_selections.get(key)
        if (
            selection is None
            or selection.status is not SelectionStatus.SELECTED
            or selection.rule is None
        ):
            # Pode haver regra apenas para outra vigência (ex.: item ainda não vigente).
            if selection is not None and selection.reasons:
                msg = " ".join(r.message for r in selection.reasons)
                chk = Check(
                    name=ITEM_UNIT_PRICE_CHECK,
                    result=CheckResult.REVIEW,
                    direction=DivergenceDirection.UNVERIFIABLE,
                    message=f"Não foi possível selecionar regra vigente para '{key}': {msg}",
                )
                checks.append(chk)
                outcomes.append(CheckOutcome(check=chk, direction=DivergenceDirection.UNVERIFIABLE))
            continue

        rule = selection.rule
        applied_rules.append(AppliedRule(rule_id=rule.id, version=rule.version))
        expected_unit = rule.value
        if expected_unit is None:
            continue
        item_label = rule.item_name or key_items[0].description

        # Compara preço unitário por item cobrado
        for item in key_items:
            unit_diff = subtract_money(item.unit_value, expected_unit)
            if abs(unit_diff) > tolerance:
                line_excess = multiply_money(item.quantity, unit_diff)
                unit_diff_terms.append(line_excess)
                unit_trusted = unit_trusted and rule.review_status.is_trusted
                unit_rule_ids.append(rule.id)
                future_rule = (
                    _future_matching_rule(key, item.unit_value, invoice, all_item_rules)
                    if unit_diff > _ZERO
                    else None
                )
                if future_rule is not None and future_rule.validity.start is not None:
                    eff_br = future_rule.validity.start.strftime("%d/%m/%Y")
                    unit_premature_dates.append(eff_br)
                    unit_details.append(
                        f"{item_label}: cobrado {format_money(item.unit_value)} (repactuação/"
                        f"aditivo com vigência somente em {eff_br}) vs. vigente "
                        f"{format_money(expected_unit)} em {format_quantity(item.quantity)} unid. "
                        f"({format_money(line_excess)})"
                    )
                else:
                    unit_details.append(
                        f"{item_label}: unitário cobrado {format_money(item.unit_value)} vs. "
                        f"contratado {format_money(expected_unit)} × "
                        f"{format_quantity(item.quantity)} ({format_money(line_excess)})"
                    )
                unit_memory.append(
                    f"{item_label}: ({format_money(item.unit_value)} − "
                    f"{format_money(expected_unit)}) × {format_quantity(item.quantity)} = "
                    f"{format_money(line_excess)}"
                )

        billed_qty = matched_qty_by_key[key]
        allowed_qty_for_delivery = billed_qty

        # Limite mensal por quantidade de postos/unidades
        if rule.quantity is not None and rule.quantity_scope in (None, "MONTHLY"):
            if billed_qty > rule.quantity:
                excess_qty = billed_qty - rule.quantity
                allowed_qty_for_delivery = rule.quantity
                qty_excess_amt = multiply_money(excess_qty, expected_unit)
                trusted = rule.review_status.is_trusted
                source = f"Fonte da regra: {describe_rule_source(rule)}."
                outcome = CheckOutcome(
                    check=Check(
                        name=ITEM_QUANTITY_MONTHLY_CHECK,
                        result=CheckResult.DIVERGENCE if trusted else CheckResult.REVIEW,
                        direction=(
                            DivergenceDirection.OVERCHARGE
                            if trusted
                            else DivergenceDirection.POSSIBLE_DIVERGENCE
                        ),
                        message=(
                            f"Quantidade mensal cobrada para {item_label} "
                            f"({format_quantity(billed_qty)}) excede o limite contratado de "
                            f"{format_quantity(rule.quantity)} em {format_quantity(excess_qty)} "
                            f"({format_money(qty_excess_amt)}). {source}"
                        ),
                        calculation_memory=(
                            f"item = {item_label}",
                            f"quantidade contratada/mês = {format_quantity(rule.quantity)}",
                            f"quantidade cobrada = {format_quantity(billed_qty)}",
                            f"excesso = {format_quantity(excess_qty)} × "
                            f"{format_money(expected_unit)} = {format_money(qty_excess_amt)}",
                        ),
                        rule_id=rule.id,
                    ),
                    direction=(
                        DivergenceDirection.OVERCHARGE
                        if trusted
                        else DivergenceDirection.POSSIBLE_DIVERGENCE
                    ),
                    difference=qty_excess_amt,
                )
                checks.append(outcome.check)
                outcomes.append(outcome)

        # Limite global acumulado em Ata (quantity_scope == "TOTAL")
        elif rule.quantity is not None and rule.quantity_scope == "TOTAL":
            prior_qty = _ZERO
            for prev in prior_cache:
                prev_cls = classify_items(prev.items)
                prev_items = (
                    *prev_cls.base_items,
                    *prev_cls.overage_items,
                    *prev_cls.ambiguous_items,
                )
                for prev_item in prev_items:
                    if match_invoice_item_to_rule_key(prev_item.description, (key,)) == key:
                        prior_qty += prev_item.quantity
            cum_after = prior_qty + billed_qty
            cap = rule.quantity
            if cum_after > cap:
                already_used_cap = max(cap, prior_qty)
                excess_qty = cum_after - already_used_cap
                allowed_qty_for_delivery = max(_ZERO, billed_qty - excess_qty)
                cum_excess_amt = multiply_money(excess_qty, expected_unit)
                trusted = rule.review_status.is_trusted
                source = f"Fonte da regra: {describe_rule_source(rule)}."
                outcome = CheckOutcome(
                    check=Check(
                        name=ITEM_QUANTITY_CUMULATIVE_CHECK,
                        result=CheckResult.DIVERGENCE if trusted else CheckResult.REVIEW,
                        direction=(
                            DivergenceDirection.OVERCHARGE
                            if trusted
                            else DivergenceDirection.POSSIBLE_DIVERGENCE
                        ),
                        message=(
                            f"Saldo acumulado de {item_label} atingiu {format_quantity(cum_after)} "
                            f"(anterior {format_quantity(prior_qty)} + nota "
                            f"{format_quantity(billed_qty)}), superando o teto da Ata de "
                            f"{format_quantity(cap)} em {format_quantity(excess_qty)} "
                            f"({format_money(cum_excess_amt)}). {source}"
                        ),
                        calculation_memory=(
                            f"item = {item_label}",
                            f"teto registrado na Ata = {format_quantity(cap)}",
                            f"consumido em notas anteriores = {format_quantity(prior_qty)}",
                            f"cobrado nesta nota = {format_quantity(billed_qty)}",
                            f"excesso acumulado = {format_quantity(excess_qty)} × "
                            f"{format_money(expected_unit)} = {format_money(cum_excess_amt)}",
                        ),
                        rule_id=rule.id,
                    ),
                    direction=(
                        DivergenceDirection.OVERCHARGE
                        if trusted
                        else DivergenceDirection.POSSIBLE_DIVERGENCE
                    ),
                    difference=cum_excess_amt,
                )
                checks.append(outcome.check)
                outcomes.append(outcome)

        delivery_expected_terms.append(multiply_money(allowed_qty_for_delivery, expected_unit))

    if unit_diff_terms:
        total_unit_diff = add_money(*unit_diff_terms)
        if abs(total_unit_diff) > tolerance:
            direction = (
                DivergenceDirection.OVERCHARGE
                if total_unit_diff > _ZERO
                else DivergenceDirection.UNDERCHARGE
            )
            if not unit_trusted:
                chk_res = CheckResult.REVIEW
                chk_dir = DivergenceDirection.POSSIBLE_DIVERGENCE
            else:
                chk_res = CheckResult.DIVERGENCE
                chk_dir = direction
            premature_prefix = (
                f"Aplicação de repactuação/aditivo antes do início da vigência em "
                f"{', '.join(dict.fromkeys(unit_premature_dates))}: "
                if unit_premature_dates
                else "Divergência no preço unitário dos itens: "
            )
            unit_outcome = CheckOutcome(
                check=Check(
                    name=ITEM_UNIT_PRICE_CHECK,
                    result=chk_res,
                    direction=chk_dir,
                    message=premature_prefix + "; ".join(unit_details) + ".",
                    calculation_memory=(
                        *unit_memory,
                        f"diferença acumulada no preço unitário = {format_money(total_unit_diff)}",
                    ),
                    rule_id=unit_rule_ids[0] if unit_rule_ids else None,
                ),
                direction=chk_dir,
                difference=total_unit_diff,
            )
            checks.append(unit_outcome.check)
            outcomes.append(unit_outcome)

    monthly_gross = _compute_expected_monthly_gross(active_selections)
    if monthly_gross is not None:
        expected_base = (
            subtract_money(monthly_gross, invoice.expected_glosa)
            if invoice.expected_glosa is not None and invoice.expected_glosa > _ZERO
            else monthly_gross
        )
    elif delivery_expected_terms:
        expected_base = add_money(*delivery_expected_terms)
    else:
        expected_base = None

    # Se nenhum alerta foi gerado para itens pareados, registra conferência positiva.
    checked_item_names = {
        ITEM_UNIT_PRICE_CHECK,
        ITEM_QUANTITY_MONTHLY_CHECK,
        ITEM_QUANTITY_CUMULATIVE_CHECK,
    }
    if matched_items_by_key and not any(c.name in checked_item_names for c in checks):
        checks.append(
            Check(
                name=ITEM_UNIT_PRICE_CHECK,
                result=CheckResult.OK,
                message="Preços unitários e quantidades conferem com o contrato.",
                calculation_memory=(
                    (f"base esperada pelos itens = {format_money(expected_base)}",)
                    if expected_base is not None
                    else ()
                ),
            )
        )

    return ItemPricesAuditResult(
        checks=tuple(checks),
        outcomes=tuple(outcomes),
        applied_rules=tuple(applied_rules),
        expected_base=expected_base,
        expected_gross_before_glosa=monthly_gross,
    )


def has_matching_item_rules(
    items: Sequence[BillingItem],
    item_rules: Sequence[ContractRule],
) -> bool:
    """Indica se ao menos um item faturável da nota corresponde a regra `ITEM_PRICE`."""
    if not item_rules or not items:
        return False
    keys = {normalize_item_key(rule.item_name) for rule in item_rules if rule.item_name}
    if not keys:
        return False
    return any(match_invoice_item_to_rule_key(item.description, keys) is not None for item in items)


__all__ = [
    "ITEM_QUANTITY_CUMULATIVE_CHECK",
    "ITEM_QUANTITY_MONTHLY_CHECK",
    "ITEM_UNIT_PRICE_CHECK",
    "MISSING_GLOSA_CHECK",
    "UNREGISTERED_ITEM_CHECK",
    "ItemPricesAuditResult",
    "audit_item_prices",
    "check_missing_glosa",
    "has_matching_item_rules",
    "match_invoice_item_to_rule_key",
]
