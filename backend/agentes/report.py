"""Relatório de auditoria montado por código e verificação numérica de texto de modelo."""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import ValidationError

from backend.agentes.anchoring import extract_numbers_from_text
from backend.agentes.client import LLMClient, LLMExecutionError
from backend.agentes.prompts import (
    REPORT_AGENT_PROMPT_VERSION,
    REPORT_AGENT_SYSTEM_PROMPT,
    REPORT_AGENT_USER_TEMPLATE,
)
from backend.schemas import AuditClassification, AuditResult, StrictModel
from backend.schemas.types import EntityId, NonEmptyStr

_CENT = Decimal("0.01")

_CLASSIFICATION_LABELS: dict[AuditClassification, str] = {
    AuditClassification.MISSING_DOCUMENTATION: "pendência documental",
    AuditClassification.DIVERGENCE: "divergência identificada",
    AuditClassification.NEEDS_REVIEW: "revisão humana necessária",
    AuditClassification.NO_DIVERGENCE: "sem divergência identificada",
}


class ReportExplanationPayload(StrictModel):
    """Resumo explicativo devolvido pelo modelo antes da verificação de números."""

    summary: NonEmptyStr


class NumberVerificationResult(StrictModel):
    """Resultado da conferência dos números citados no texto contra o `AuditResult`."""

    valid: bool
    cited_numbers: tuple[Decimal, ...] = ()
    unauthorized_numbers: tuple[Decimal, ...] = ()


class AuditReport(StrictModel):
    """Relatório final entregue ao usuário, com fallback seguro para o texto determinístico."""

    audit_id: EntityId
    invoice_id: EntityId
    classification: AuditClassification
    deterministic_text: NonEmptyStr
    final_text: NonEmptyStr
    used_llm_text: bool = False
    rejected_llm_text: bool = False
    unauthorized_numbers: tuple[Decimal, ...] = ()
    warnings: tuple[str, ...] = ()
    prompt_version: str | None = None
    model_name: str | None = None


def _format_money(value: Decimal) -> str:
    rounded = value.quantize(_CENT, rounding=ROUND_HALF_UP)
    body = format(abs(rounded), ",.2f").replace(",", "_").replace(".", ",").replace("_", ".")
    return f"-R$ {body}" if rounded < 0 else f"R$ {body}"


def build_deterministic_report(result: AuditResult) -> str:
    """Monta o relatório completo em português a partir do `AuditResult` do motor."""
    label = _CLASSIFICATION_LABELS[result.classification]
    lines = [
        f"Auditoria {result.id} — Cobrança {result.invoice_id} ({result.reference_period})",
        f"Classificação: {label} ({result.classification.value})",
        f"Total cobrado: {_format_money(result.total)}",
    ]
    if result.base is not None:
        lines.append(f"Base prevista: {_format_money(result.base)}")
    if result.difference is not None:
        lines.append(f"Diferença apurada: {_format_money(result.difference)}")

    if result.applied_rules:
        rules_text = ", ".join(f"{rule.rule_id} (v{rule.version})" for rule in result.applied_rules)
        lines.append(f"Regras aplicadas: {rules_text}")

    if result.checks:
        lines.append("Verificações:")
        for check in result.checks:
            direction = f" [{check.direction.value}]" if check.direction is not None else ""
            lines.append(f"- {check.name} ({check.result.value}{direction}): {check.message}")
            for step in check.calculation_memory:
                lines.append(f"  * {step}")

    return "\n".join(lines)


def allowed_numbers_from_audit(result: AuditResult) -> frozenset[Decimal]:
    """Coleta todos os números autorizados no `AuditResult` para validar textos explicativos."""
    allowed: set[Decimal] = {
        result.total,
        Decimal(result.reference_period.year),
        Decimal(result.reference_period.month),
    }
    if result.base is not None:
        allowed.add(result.base)
    if result.difference is not None:
        allowed.add(result.difference)
        allowed.add(abs(result.difference))

    allowed.update(extract_numbers_from_text(result.invoice_id))
    for applied in result.applied_rules:
        allowed.add(Decimal(applied.version))
        allowed.update(extract_numbers_from_text(applied.rule_id))

    for check in result.checks:
        allowed.update(extract_numbers_from_text(check.message))
        for step in check.calculation_memory:
            allowed.update(extract_numbers_from_text(step))
        if check.rule_id is not None:
            allowed.update(extract_numbers_from_text(check.rule_id))

    return frozenset(allowed)


def verify_report_numbers(explanatory_text: str, result: AuditResult) -> NumberVerificationResult:
    """Confere se todo número citado no texto explicativo consta no resultado do motor."""
    cited = extract_numbers_from_text(explanatory_text)
    allowed = allowed_numbers_from_audit(result)
    unauthorized = tuple(num for num in cited if num not in allowed and abs(num) not in allowed)
    return NumberVerificationResult(
        valid=not unauthorized,
        cited_numbers=cited,
        unauthorized_numbers=unauthorized,
    )


def generate_audit_report(result: AuditResult, *, client: LLMClient | None = None) -> AuditReport:
    """
    Gera o relatório a partir do `AuditResult`. Se `client` redigir texto explicativo, todo
    número citado é verificado contra o motor; se falhar, rejeita e usa o texto do código.
    """
    deterministic_text = build_deterministic_report(result)
    if client is None:
        return AuditReport(
            audit_id=result.id,
            invoice_id=result.invoice_id,
            classification=result.classification,
            deterministic_text=deterministic_text,
            final_text=deterministic_text,
        )

    user_prompt = REPORT_AGENT_USER_TEMPLATE.format(document_text=deterministic_text)
    try:
        response = client.generate(
            system_prompt=REPORT_AGENT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=ReportExplanationPayload,
        )
        parsed = ReportExplanationPayload.model_validate_json(response.content)
    except (LLMExecutionError, ValidationError, ValueError) as exc:
        return AuditReport(
            audit_id=result.id,
            invoice_id=result.invoice_id,
            classification=result.classification,
            deterministic_text=deterministic_text,
            final_text=deterministic_text,
            used_llm_text=False,
            rejected_llm_text=True,
            warnings=(
                f"Texto explicativo do modelo rejeitado por erro de geração/esquema ({exc}); "
                "exibindo relatório determinístico.",
            ),
            prompt_version=REPORT_AGENT_PROMPT_VERSION,
            model_name=client.model_name,
        )

    verification = verify_report_numbers(parsed.summary, result)
    if not verification.valid:
        bad_list = ", ".join(str(num) for num in verification.unauthorized_numbers)
        return AuditReport(
            audit_id=result.id,
            invoice_id=result.invoice_id,
            classification=result.classification,
            deterministic_text=deterministic_text,
            final_text=deterministic_text,
            used_llm_text=False,
            rejected_llm_text=True,
            unauthorized_numbers=verification.unauthorized_numbers,
            warnings=(
                f"Texto explicativo do modelo rejeitado: número(s) não constam no resultado do "
                f"motor ({bad_list}). Exibindo relatório determinístico.",
            ),
            prompt_version=REPORT_AGENT_PROMPT_VERSION,
            model_name=client.model_name,
        )

    final_text = f"{parsed.summary}\n\n{deterministic_text}"
    return AuditReport(
        audit_id=result.id,
        invoice_id=result.invoice_id,
        classification=result.classification,
        deterministic_text=deterministic_text,
        final_text=final_text,
        used_llm_text=True,
        rejected_llm_text=False,
        prompt_version=REPORT_AGENT_PROMPT_VERSION,
        model_name=client.model_name,
    )
