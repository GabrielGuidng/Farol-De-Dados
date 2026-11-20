"""Testes de Check, AuditResult e HumanReview (1.8)."""

import pytest
from pydantic import ValidationError

from backend.schemas import AuditResult, Check, HumanReview, ReviewStatus


def _check_payload() -> dict:
    return {
        "name": "price_vs_rule",
        "result": "DIVERGENCE",
        "message": "Cobrado R$ 10.800,00; regra vigente R$ 10.500,00.",
        "calculation_memory": ["base = 10500.00", "total = 10800.00", "diferenca = 300.00"],
        "rule_id": "rule-1",
    }


def _result_payload() -> dict:
    return {
        "id": "res-1",
        "invoice_id": "inv-1",
        "classification": "DIVERGENCE",
        "checks": [_check_payload()],
        "applied_rules": [{"rule_id": "rule-1", "version": 2}],
        "reference_period": "2026-10",
        "base": "10500.00",
        "difference": "300.00",
        "total": "10800.00",
    }


def _review_payload() -> dict:
    return {
        "target_type": "RULE",
        "target_id": "rule-1",
        "user": "auditora.simulada",
        "decision": "CONFIRM",
        "justification": "Trecho conferido no documento.",
        "previous_status": "PENDING_REVIEW",
        "new_status": "CONFIRMED",
        "reviewed_at": "2026-11-20T01:00:00",
    }


def test_valid_check() -> None:
    check = Check.model_validate(_check_payload())
    assert isinstance(check.calculation_memory, tuple)
    assert len(check.calculation_memory) == 3


def test_valid_audit_result_and_negative_difference() -> None:
    result = AuditResult.model_validate({**_result_payload(), "difference": "-300.00"})
    assert result.applied_rules[0].version == 2
    assert result.difference is not None and result.difference < 0


def test_audit_result_without_base_and_difference() -> None:
    payload = {**_result_payload(), "classification": "NEEDS_REVIEW", "base": None}
    payload["difference"] = None
    assert AuditResult.model_validate(payload).base is None


def test_audit_result_rejects_float_negative_total_and_extra_field() -> None:
    for override in ({"total": 10800.0}, {"total": "-1"}, {"unexpected": 1}):
        with pytest.raises(ValidationError):
            AuditResult.model_validate({**_result_payload(), **override})


def test_audit_result_rejects_invalid_classification() -> None:
    with pytest.raises(ValidationError):
        AuditResult.model_validate({**_result_payload(), "classification": "MAYBE"})


def test_applied_rule_version_must_be_positive() -> None:
    payload = {**_result_payload(), "applied_rules": [{"rule_id": "rule-1", "version": 0}]}
    with pytest.raises(ValidationError):
        AuditResult.model_validate(payload)


def test_valid_human_review() -> None:
    review = HumanReview.model_validate(_review_payload())
    assert review.new_status is ReviewStatus.CONFIRMED


def test_human_review_requires_justification_and_matching_status() -> None:
    with pytest.raises(ValidationError):
        HumanReview.model_validate({**_review_payload(), "justification": " "})
    with pytest.raises(ValidationError):
        HumanReview.model_validate({**_review_payload(), "new_status": "REJECTED"})


def test_human_review_rejects_invalid_enums_and_extra_field() -> None:
    for override in ({"target_type": "INVOICE"}, {"decision": "MAYBE"}, {"unexpected": 1}):
        with pytest.raises(ValidationError):
            HumanReview.model_validate({**_review_payload(), **override})
