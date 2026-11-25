"""Testes do relatório com números verificados: cenário R-01 (Etapa 5.7)."""

from decimal import Decimal

from backend.agentes import (
    FakeLLMClient,
    ReportExplanationPayload,
    build_deterministic_report,
    generate_audit_report,
    verify_report_numbers,
)
from backend.schemas import (
    AppliedRule,
    AuditClassification,
    AuditResult,
    Check,
    CheckResult,
    DivergenceDirection,
    ReferencePeriod,
)


def _case_a_audit_result() -> AuditResult:
    return AuditResult(
        id="audit-inv-104",
        invoice_id="inv-104",
        classification=AuditClassification.DIVERGENCE,
        checks=(
            Check(
                name="monthly_price",
                result=CheckResult.DIVERGENCE,
                direction=DivergenceDirection.OVERCHARGE,
                message=(
                    "Divergência no preço mensal: cobrado R$ 10.800,00, regra vigente "
                    "R$ 10.500,00; diferença de R$ 300,00 a mais (2,86%)."
                ),
                calculation_memory=(
                    "valor cobrado = R$ 10.800,00",
                    "valor da regra vigente = R$ 10.500,00",
                    "diferença = cobrado − regra = R$ 300,00 (2,86%)",
                ),
                rule_id="price-add02",
            ),
        ),
        applied_rules=(AppliedRule(rule_id="price-add02", version=3),),
        reference_period=ReferencePeriod(year=2026, month=10),
        base=Decimal("10500.00"),
        difference=Decimal("300.00"),
        total=Decimal("10800.00"),
    )


def test_deterministic_report_without_llm_contains_exact_numbers() -> None:
    result = _case_a_audit_result()
    report = generate_audit_report(result)

    assert report.used_llm_text is False
    assert report.rejected_llm_text is False
    assert "divergência identificada" in report.final_text
    assert "R$ 10.800,00" in report.final_text
    assert "R$ 10.500,00" in report.final_text
    assert "R$ 300,00" in report.final_text
    assert report.final_text == build_deterministic_report(result)


def test_r01_rejects_llm_text_citing_number_absent_from_motor_result() -> None:
    result = _case_a_audit_result()
    hallucinated = ReportExplanationPayload(
        summary=(
            "A cobrança 104 apresentou divergência de R$ 450,00 (4,29%) sobre a base "
            "de R$ 10.500,00."
        )
    )
    report = generate_audit_report(result, client=FakeLLMClient([hallucinated]))

    assert report.rejected_llm_text is True
    assert report.used_llm_text is False
    assert Decimal("450.00") in report.unauthorized_numbers
    assert Decimal("4.29") in report.unauthorized_numbers
    assert report.final_text == report.deterministic_text
    assert "450,00" not in report.final_text
    assert report.warnings


def test_r01_accepts_llm_text_when_all_numbers_exist_in_motor_result() -> None:
    result = _case_a_audit_result()
    grounded = ReportExplanationPayload(
        summary=(
            "Na competência 10/2026, a cobrança 104 totalizou R$ 10.800,00 frente à base "
            "de R$ 10.500,00 (diferença de R$ 300,00, equivalente a 2,86%)."
        )
    )
    verification = verify_report_numbers(grounded.summary, result)
    assert verification.valid is True
    assert verification.unauthorized_numbers == ()

    report = generate_audit_report(result, client=FakeLLMClient([grounded]))
    assert report.used_llm_text is True
    assert report.rejected_llm_text is False
    assert report.final_text.startswith(grounded.summary)
    assert report.deterministic_text in report.final_text
