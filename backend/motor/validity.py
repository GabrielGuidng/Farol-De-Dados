"""Vigência do contrato e prorrogação: a competência está dentro do período contratado?"""

from collections.abc import Sequence
from datetime import date

from backend.motor.results import ContractValidityResult, Reason, ReasonCode, ValidityStatus
from backend.schemas import Contract, ContractRule, ReferencePeriod, ReviewStatus, RuleType


def _extension_rules(contract: Contract, extensions: Sequence[ContractRule]) -> list[ContractRule]:
    for rule in extensions:
        if rule.rule_type is not RuleType.EXTENSION:
            raise ValueError(f"a regra {rule.id} não é uma prorrogação (EXTENSION)")
        if rule.contract_id != contract.id:
            raise ValueError(f"a regra {rule.id} pertence a outro contrato")
    return [rule for rule in extensions if rule.review_status is not ReviewStatus.REJECTED]


def effective_contract_end(contract: Contract, extensions: Sequence[ContractRule]) -> date | None:
    """
    Recebe o contrato e as prorrogações; devolve o fim efetivo (None = vigência aberta).
    Só prorrogação CONFIRMED ou CORRECTED estende o contrato; a pendente não conta.
    """
    original = contract.validity.end
    if original is None:
        return None
    ends = [
        rule.validity.end
        for rule in _extension_rules(contract, extensions)
        if rule.review_status.is_trusted and rule.validity.end is not None
    ]
    return max([original, *ends])


def check_contract_validity(
    contract: Contract, extensions: Sequence[ContractRule], reference_period: ReferencePeriod
) -> ContractValidityResult:
    """
    Recebe contrato, prorrogações e competência; devolve se o mês está dentro da vigência.
    Fora da vigência é NEEDS_REVIEW (não DIVERGENCE): a prorrogação pode não ter sido enviada.
    """
    period = reference_period
    end = effective_contract_end(contract, extensions)
    start = contract.validity.start
    reasons: list[Reason] = []
    if start is not None and period.last_day < start:
        reasons.append(
            Reason(
                code=ReasonCode.OUTSIDE_CONTRACT_VALIDITY,
                message=f"A competência {period} é anterior ao início da vigência do contrato.",
            )
        )
    elif end is not None and period.first_day > end:
        reasons.append(
            Reason(
                code=ReasonCode.OUTSIDE_CONTRACT_VALIDITY,
                message=(
                    f"A competência {period} está fora da vigência do contrato, que termina em "
                    f"{end.isoformat()}. Pode existir uma prorrogação que não foi enviada."
                ),
            )
        )
        pending = [
            rule
            for rule in _extension_rules(contract, extensions)
            if not rule.review_status.is_trusted
        ]
        if pending:
            reasons.append(
                Reason(
                    code=ReasonCode.PENDING_EXTENSION,
                    message="Há prorrogação não revisada por uma pessoa; ela não foi considerada.",
                    rule_ids=tuple(rule.id for rule in pending),
                )
            )
    elif (start is not None and period.first_day < start) or (
        end is not None and period.last_day > end
    ):
        reasons.append(
            Reason(
                code=ReasonCode.MID_PERIOD_CHANGE,
                message=f"A vigência do contrato começa ou termina dentro da competência {period}.",
            )
        )
    status = ValidityStatus.NEEDS_REVIEW if reasons else ValidityStatus.WITHIN
    return ContractValidityResult(status=status, effective_end=end, reasons=tuple(reasons))
