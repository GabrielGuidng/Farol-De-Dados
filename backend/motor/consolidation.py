"""Consolidação e dados faltantes: o que impede o motor de concluir com segurança."""

from collections.abc import Sequence

from backend.motor.results import RuleConsolidation
from backend.schemas import (
    Amendment,
    Check,
    CheckResult,
    Contract,
    ContractRule,
    DivergenceDirection,
    Document,
    Invoice,
    ReferencePeriod,
)

AMENDMENT_REVIEW_CHECK = "amendments_reviewed"
RULE_START_CHECK = "rules_start_date"
COMPETENCE_CHECK = "competence_inferred"
CONTRACT_LINK_CHECK = "contract_link"


def consolidate_rules(rules: Sequence[ContractRule]) -> RuleConsolidation:
    """
    Recebe as regras; devolve as que têm início de vigência e um alerta sobre as que não têm.
    Regra sem início fica fora da consolidação (o sistema não presume a data).
    """
    kept = tuple(rule for rule in rules if rule.validity.start is not None)
    dropped = [rule for rule in rules if rule.validity.start is None]
    if not dropped:
        check = Check(
            name=RULE_START_CHECK,
            result=CheckResult.OK,
            message="Todas as regras informam o início da vigência.",
        )
    else:
        ids = ", ".join(rule.id for rule in dropped)
        check = Check(
            name=RULE_START_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message=(
                f"Regra(s) sem início de vigência ({ids}) ficaram fora da consolidação; "
                "o sistema não presume a data."
            ),
            calculation_memory=tuple(f"regra {rule.id} sem data de início" for rule in dropped),
        )
    return RuleConsolidation(rules=kept, check=check)


def check_amendment_review(
    amendments: Sequence[Amendment],
    documents: Sequence[Document],
    reference_period: ReferencePeriod,
) -> Check:
    """
    Recebe aditivos, documentos recebidos e a competência; só aditivos em vigor até o fim da
    competência contam. Documento do aditivo com reviewed=False deixa a consolidação incompleta.
    """
    by_id = {document.id: document for document in documents}
    relevant = [item for item in amendments if item.effective_date <= reference_period.last_day]
    pending: list[str] = []
    for amendment in relevant:
        document = by_id.get(amendment.source_document_id)
        if document is None:
            pending.append(
                f"aditivo {amendment.id}: documento {amendment.source_document_id} não encontrado"
            )
        elif not document.reviewed:
            pending.append(
                f"aditivo {amendment.id}: documento {amendment.source_document_id} não revisado"
            )
    if pending:
        return Check(
            name=AMENDMENT_REVIEW_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message=(
                "Consolidação incompleta: há aditivo em vigor ainda não revisado por uma pessoa "
                "(" + "; ".join(pending) + ")."
            ),
            calculation_memory=tuple(pending),
        )
    return Check(
        name=AMENDMENT_REVIEW_CHECK,
        result=CheckResult.OK,
        message="Todos os aditivos em vigor até a competência foram revisados.",
    )


def check_competence_inferred(invoice: Invoice) -> Check:
    """Competência inferida (ex.: pela data de emissão) gera revisão; informada no documento, OK."""
    if invoice.competence_inferred:
        return Check(
            name=COMPETENCE_CHECK,
            result=CheckResult.REVIEW,
            direction=DivergenceDirection.UNVERIFIABLE,
            message=(
                f"A competência {invoice.reference_period} foi inferida (não consta no documento); "
                "confirme-a antes de comparar com as regras."
            ),
        )
    return Check(
        name=COMPETENCE_CHECK,
        result=CheckResult.OK,
        message="A competência foi informada pelo documento.",
    )


def check_contract_link(contract: Contract | None, invoice: Invoice) -> Check:
    """
    Cobrança sem vínculo seguro com o contrato (contrato ausente, id ou CNPJ divergentes) ou sem
    valor (total zero e sem itens) é pendência documental (MISSING_DOCUMENTATION).
    """
    problem: str | None = None
    if contract is None or invoice.contract_id is None:
        problem = "A cobrança não tem vínculo com um contrato."
    elif invoice.contract_id != contract.id:
        problem = (
            f"A cobrança aponta o contrato {invoice.contract_id}, "
            f"mas o contrato informado é {contract.id}."
        )
    elif invoice.supplier_cnpj != contract.supplier_cnpj:
        problem = "O CNPJ do fornecedor da cobrança difere do CNPJ do contrato."
    elif invoice.total == 0 and not invoice.items:
        problem = "A cobrança não informa total nem itens."
    if problem is None:
        return Check(
            name=CONTRACT_LINK_CHECK,
            result=CheckResult.OK,
            message="A cobrança está vinculada com segurança ao contrato.",
        )
    return Check(
        name=CONTRACT_LINK_CHECK,
        result=CheckResult.PENDING,
        direction=DivergenceDirection.UNVERIFIABLE,
        message=f"Pendência documental: {problem}",
    )
