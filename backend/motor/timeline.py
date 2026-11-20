"""Linha do tempo das regras: aplica aditivos e seleciona a regra vigente de uma competência."""

from collections.abc import Sequence
from datetime import date, timedelta

from backend.motor.results import Reason, ReasonCode, TimelineResult
from backend.schemas import (
    Amendment,
    ContractRule,
    RuleRelation,
    ValidityPeriod,
)

_ONE_DAY = timedelta(days=1)


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
