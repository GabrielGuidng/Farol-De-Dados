"""Linha do tempo das regras: aplica aditivos e seleciona a regra vigente de uma competência."""

import re
import unicodedata
from collections.abc import Sequence
from datetime import date, timedelta

from backend.motor.results import Reason, ReasonCode, RuleSelection, SelectionStatus, TimelineResult
from backend.schemas import (
    Amendment,
    ContractRule,
    ReferencePeriod,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)

_ONE_DAY = timedelta(days=1)
_ITEM_STOP_WORDS = frozenset({"de", "da", "do", "dos", "das", "para", "com", "posto", "postos"})


def normalize_item_key(name: str | None) -> str:
    """Normaliza nome de item/posto para parear regras e itens sem sensibilidade a acento/caixa."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    stripped_code = re.sub(r"^\s*\d{1,6}\s*[-–.]?\s*", "", ascii_only)
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", stripped_code) if tok not in _ITEM_STOP_WORDS]
    return " ".join(tokens)


def _reason(code: ReasonCode, message: str, *rule_ids: str) -> Reason:
    return Reason(code=code, message=message, rule_ids=rule_ids)


def _unique_amendments(amendments: Sequence[Amendment], issues: list[Reason]) -> list[Amendment]:
    """Ordena por data de efeito; descarta (com aviso) aditivos conflitantes na mesma data."""
    ordered = sorted(amendments, key=lambda item: (item.effective_date, item.id))
    groups: dict[tuple[str, date], list[Amendment]] = {}
    for amendment in ordered:
        groups.setdefault((amendment.affected_rule_id, amendment.effective_date), []).append(
            amendment
        )
    unique: list[Amendment] = []
    for (rule_id, effective), group in groups.items():
        if any(item != group[0] for item in group):
            ids = ", ".join(item.id for item in group)
            issues.append(
                _reason(
                    ReasonCode.AMENDMENT_CONFLICT,
                    f"Aditivos conflitantes ({ids}) alteram a regra {rule_id} na mesma data "
                    f"({effective.isoformat()}); nenhum foi aplicado.",
                    rule_id,
                )
            )
        else:
            unique.append(group[0])
    return sorted(unique, key=lambda item: (item.effective_date, item.id))


def _apply_one(
    current: dict[str, ContractRule], amendment: Amendment, issues: list[Reason]
) -> None:
    """Aplica um aditivo em `current`; se não for possível, registra o problema e não altera."""
    affected = current.get(amendment.affected_rule_id)
    if affected is None:
        issues.append(
            _reason(
                ReasonCode.UNKNOWN_AFFECTED_RULE,
                f"O aditivo {amendment.id} afeta a regra {amendment.affected_rule_id}, "
                "que não existe.",
            )
        )
        return
    effective = amendment.effective_date
    new_rule = amendment.new_rule
    if new_rule is not None and new_rule.rule_type is not affected.rule_type:
        issues.append(
            _reason(
                ReasonCode.AMENDMENT_TYPE_MISMATCH,
                f"O aditivo {amendment.id} troca uma regra do tipo {affected.rule_type} "
                f"por uma do tipo {new_rule.rule_type}; não foi aplicado.",
                affected.id,
            )
        )
        return
    start = affected.validity.start
    if start is not None and effective <= start:
        issues.append(
            _reason(
                ReasonCode.INVALID_EFFECTIVE_DATE,
                f"O aditivo {amendment.id} tem efeito em {effective.isoformat()}, que não é "
                f"posterior ao início da regra {affected.id}; não foi aplicado.",
                affected.id,
            )
        )
        return
    if new_rule is not None:
        if new_rule.id in current:
            issues.append(
                _reason(
                    ReasonCode.DUPLICATE_RULE_ID,
                    f"A nova regra {new_rule.id} do aditivo {amendment.id} repete um "
                    "identificador existente; não foi aplicado.",
                    new_rule.id,
                )
            )
            return
        new_end = new_rule.validity.end
        if new_end is not None and new_end < effective:
            issues.append(
                _reason(
                    ReasonCode.INVALID_EFFECTIVE_DATE,
                    f"A nova regra {new_rule.id} termina antes da data de efeito do aditivo "
                    f"{amendment.id}; não foi aplicado.",
                    new_rule.id,
                )
            )
            return
        if new_rule.validity.start is not None and new_rule.validity.start != effective:
            issues.append(
                _reason(
                    ReasonCode.START_DATE_MISMATCH,
                    f"A nova regra {new_rule.id} declara início em "
                    f"{new_rule.validity.start.isoformat()}, mas o aditivo {amendment.id} tem "
                    f"efeito em {effective.isoformat()}; foi usada a data de efeito.",
                    new_rule.id,
                )
            )

    day_before = effective - _ONE_DAY
    old_end = affected.validity.end
    closed_end = day_before if old_end is None else min(old_end, day_before)
    revoked = amendment.relation is RuleRelation.REVOKED
    current[affected.id] = affected.model_copy(
        update={
            "validity": ValidityPeriod(start=start, end=closed_end),
            "relation": RuleRelation.REVOKED if revoked else affected.relation,
        }
    )
    if new_rule is not None:
        if new_rule.rule_type is RuleType.ITEM_PRICE and affected.rule_type is RuleType.ITEM_PRICE:
            inherited_updates: dict[str, object] = {
                "validity": ValidityPeriod(start=effective, end=new_rule.validity.end),
                "relation": RuleRelation.ALTERED,
                "version": affected.version + 1,
            }
            if new_rule.quantity is None and affected.quantity is not None:
                inherited_updates["quantity"] = affected.quantity
            if new_rule.unit is None and affected.unit is not None:
                inherited_updates["unit"] = affected.unit
            if new_rule.quantity_scope is None and affected.quantity_scope is not None:
                inherited_updates["quantity_scope"] = affected.quantity_scope
            current[new_rule.id] = new_rule.model_copy(update=inherited_updates)
        else:
            current[new_rule.id] = new_rule.model_copy(
                update={
                    "validity": ValidityPeriod(start=effective, end=new_rule.validity.end),
                    "relation": RuleRelation.ALTERED,
                    "version": affected.version + 1,
                }
            )


def apply_amendments(
    rules: Sequence[ContractRule], amendments: Sequence[Amendment]
) -> TimelineResult:
    """
    Recebe as regras e os aditivos; devolve todas as versões com a vigência reconstruída.
    O aditivo encerra só a regra que afeta, no dia anterior à data de efeito; o que não puder
    ser aplicado vira `issues` (nunca exceção).
    """
    issues: list[Reason] = []
    current: dict[str, ContractRule] = {}
    for rule in rules:
        if rule.id in current:
            issues.append(
                _reason(
                    ReasonCode.DUPLICATE_RULE_ID,
                    f"A regra {rule.id} aparece mais de uma vez; a repetição foi ignorada.",
                    rule.id,
                )
            )
            continue
        current[rule.id] = rule
    for amendment in _unique_amendments(amendments, issues):
        _apply_one(current, amendment, issues)
    return TimelineResult(rules=tuple(current.values()), issues=tuple(issues))


def _terms(rule: ContractRule) -> tuple[object, ...]:
    return (
        rule.value,
        rule.quantity,
        rule.unit,
        normalize_item_key(rule.item_name),
        rule.quantity_scope,
        rule.adjustment_index,
        rule.periodicity_months,
    )


def _clipped(rule: ContractRule, period: ReferencePeriod) -> tuple[date, date]:
    start = rule.validity.start
    end = rule.validity.end
    first = period.first_day if start is None else max(start, period.first_day)
    last = period.last_day if end is None else min(end, period.last_day)
    return first, last


def select_applicable_rule(
    rules: Sequence[ContractRule], reference_period: ReferencePeriod
) -> RuleSelection:
    """
    Recebe as regras de um tipo e a competência; devolve a regra cuja vigência cobre o mês inteiro.
    Conflito, mudança no meio da competência ou início desconhecido: NEEDS_REVIEW (não levanta).
    Regras REJECTED são ignoradas; tipos misturados são erro de uso (ValueError).
    """
    if len({rule.rule_type for rule in rules}) > 1:
        raise ValueError("select_applicable_rule exige regras de um único tipo")
    period = reference_period
    candidates: list[ContractRule] = []
    for rule in rules:
        start, end = rule.validity.start, rule.validity.end
        if rule.review_status is ReviewStatus.REJECTED:
            continue
        if end is not None and end < period.first_day:
            continue
        if start is not None and start > period.last_day:
            continue
        candidates.append(rule)
    if not candidates:
        return RuleSelection(
            status=SelectionStatus.NO_RULE,
            reasons=(
                _reason(
                    ReasonCode.NO_APPLICABLE_RULE,
                    f"Nenhuma regra vigente para a competência {period}.",
                ),
            ),
        )

    reasons: list[Reason] = []
    conflicting: list[str] = []
    for index, left in enumerate(candidates):
        left_start, left_end = _clipped(left, period)
        for right in candidates[index + 1 :]:
            if (
                left.rule_type is RuleType.ITEM_PRICE
                and right.rule_type is RuleType.ITEM_PRICE
                and normalize_item_key(left.item_name) != normalize_item_key(right.item_name)
            ):
                continue
            right_start, right_end = _clipped(right, period)
            overlap = max(left_start, right_start) <= min(left_end, right_end)
            if overlap and _terms(left) != _terms(right):
                conflicting.extend(rule_id for rule_id in (left.id, right.id))
    if conflicting:
        ids = tuple(dict.fromkeys(conflicting))
        reasons.append(
            _reason(
                ReasonCode.CONFLICT,
                f"Regras com valores diferentes e vigências sobrepostas na competência {period}, "
                f"sem vínculo de substituição: {', '.join(ids)}.",
                *ids,
            )
        )
    partial = [
        rule
        for rule in candidates
        if rule.validity.start is not None
        and not (
            rule.validity.start <= period.first_day
            and (rule.validity.end is None or rule.validity.end >= period.last_day)
        )
    ]
    if partial:
        ids = tuple(rule.id for rule in partial)
        reasons.append(
            _reason(
                ReasonCode.MID_PERIOD_CHANGE,
                f"A vigência de uma regra começa ou termina dentro da competência {period} "
                "e não há pró-rata definido.",
                *ids,
            )
        )
    unknown_start = [rule for rule in candidates if rule.validity.start is None]
    if unknown_start:
        ids = tuple(rule.id for rule in unknown_start)
        reasons.append(
            _reason(
                ReasonCode.MISSING_START_DATE,
                "A regra não informa a data de início da vigência; o sistema não presume uma data.",
                *ids,
            )
        )
    if reasons:
        return RuleSelection(status=SelectionStatus.NEEDS_REVIEW, reasons=tuple(reasons))
    return RuleSelection(
        status=SelectionStatus.SELECTED, rule=min(candidates, key=lambda rule: rule.id)
    )


def select_applicable_item_rules(
    rules: Sequence[ContractRule], reference_period: ReferencePeriod
) -> dict[str, RuleSelection]:
    """Seleciona, por `normalize_item_key(item_name)`, a regra ITEM_PRICE vigente no período."""
    grouped: dict[str, list[ContractRule]] = {}
    for rule in rules:
        if rule.rule_type is not RuleType.ITEM_PRICE or rule.review_status is ReviewStatus.REJECTED:
            continue
        key = normalize_item_key(rule.item_name)
        if not key:
            continue
        grouped.setdefault(key, []).append(rule)
    return {key: select_applicable_rule(group, reference_period) for key, group in grouped.items()}
