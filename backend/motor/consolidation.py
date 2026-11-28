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
    calculate_cnpj_check_digits,
    normalize_cnpj,
)

AMENDMENT_REVIEW_CHECK = "amendments_reviewed"
RULE_START_CHECK = "rules_start_date"
COMPETENCE_CHECK = "competence_inferred"
CONTRACT_LINK_CHECK = "contract_link"
_PROVISIONAL_CNPJ = "00000000000191"


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


def _format_cnpj_digits(digits: str) -> str:
    if len(digits) != 14:
        return digits
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def _check_raw_cnpj_validity(invoice: Invoice) -> str | None:
    raw = (invoice.raw_supplier_cnpj or "").strip()
    if not raw:
        return None
    digits = normalize_cnpj(raw)
    if len(digits) != 14 or len(set(digits)) <= 1:
        return f"O CNPJ informado na cobrança ({raw}) está incompleto ou inválido."
    expected_dv = calculate_cnpj_check_digits(digits[:12])
    if digits[12:] != expected_dv:
        return (
            f"O CNPJ do fornecedor ({raw}) possui dígito verificador inválido "
            f"(esperado final -{expected_dv})."
        )
    return None


def _describe_cnpj_mismatch(contract: Contract, invoice: Invoice) -> str:
    raw_err = _check_raw_cnpj_validity(invoice)
    if raw_err is not None:
        if contract.supplier_cnpj == _PROVISIONAL_CNPJ:
            return raw_err
        return raw_err.rstrip(".") + " e difere do CNPJ do contrato."
    if invoice.supplier_cnpj == _PROVISIONAL_CNPJ:
        return (
            "O CNPJ do fornecedor na cobrança não foi identificado ou é inválido e "
            "difere do CNPJ do contrato."
        )
    inv_fmt = _format_cnpj_digits(invoice.supplier_cnpj)
    contract_fmt = _format_cnpj_digits(contract.supplier_cnpj)
    if invoice.supplier_cnpj[:8] == contract.supplier_cnpj[:8]:
        return (
            f"O CNPJ do emitente ({inv_fmt}) pertence a filial da contratada "
            f"(mesma raiz, filial /{invoice.supplier_cnpj[8:12]} vs. "
            f"/{contract.supplier_cnpj[8:12]} no contrato)."
        )
    return (
        f"O CNPJ do emitente ({inv_fmt}) pertence a outro fornecedor diferente "
        f"do contrato ({contract_fmt})."
    )


def check_contract_link(contract: Contract | None, invoice: Invoice) -> Check:
    """
    Cobrança sem vínculo seguro com o contrato (contrato ausente, id ou CNPJ divergentes) ou sem
    valor (total zero e sem itens) é pendência documental (MISSING_DOCUMENTATION).
    Quando o contrato tem CNPJ provisório (não lido no instrumento), ainda valida o dígito da NF,
    mas não reprova em cascata notas com CNPJ válido.
    """
    problem: str | None = None
    raw_text = (invoice.raw_supplier_cnpj or "").strip()
    is_masked_on_provisional = (
        contract is not None
        and contract.supplier_cnpj == _PROVISIONAL_CNPJ
        and invoice.supplier_cnpj == _PROVISIONAL_CNPJ
        and bool(raw_text)
        and any(ch in "?*xX" for ch in raw_text)
    )
    if contract is None or invoice.contract_id is None:
        problem = "A cobrança não tem vínculo com um contrato."
    elif invoice.contract_id != contract.id:
        problem = (
            f"A cobrança aponta o contrato {invoice.contract_id}, "
            f"mas o contrato informado é {contract.id}."
        )
    elif (
        not is_masked_on_provisional and (raw_err := _check_raw_cnpj_validity(invoice)) is not None
    ):
        problem = (
            raw_err
            if contract.supplier_cnpj == _PROVISIONAL_CNPJ
            else raw_err.rstrip(".") + " e difere do CNPJ do contrato."
        )
    elif (
        contract.supplier_cnpj != _PROVISIONAL_CNPJ
        and invoice.supplier_cnpj != contract.supplier_cnpj
    ):
        problem = _describe_cnpj_mismatch(contract, invoice)
    elif invoice.total == 0 and not invoice.items:
        problem = "A cobrança não informa total nem itens."
    if problem is None:
        if contract is not None and contract.supplier_cnpj == _PROVISIONAL_CNPJ:
            return Check(
                name=CONTRACT_LINK_CHECK,
                result=CheckResult.OK,
                message=(
                    "Vínculo por contrato ativo (CNPJ da contratada não foi lido no "
                    "contrato — conferência de CNPJ contra o contrato omitida)."
                ),
            )
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
