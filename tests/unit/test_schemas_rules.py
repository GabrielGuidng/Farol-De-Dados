"""Testes de ContractRule e Amendment: S-06 e S-08 (1.6)."""

import copy

import pytest
from pydantic import ValidationError

from backend.schemas import Amendment, ContractRule, ReviewStatus, RuleRelation


def test_valid_rule_defaults_to_pending_review(rule_payload: dict) -> None:
    rule = ContractRule.model_validate(rule_payload)
    assert rule.review_status is ReviewStatus.PENDING_REVIEW
    assert rule.relation is RuleRelation.CREATED
    assert rule.validity.start is None


def test_s06_price_rule_without_value_fails(rule_payload: dict) -> None:
    del rule_payload["value"]
    with pytest.raises(ValidationError):
        ContractRule.model_validate(rule_payload)


def test_overage_price_requires_value(rule_payload: dict) -> None:
    rule_payload.update(rule_type="OVERAGE_PRICE", value=None)
    with pytest.raises(ValidationError):
        ContractRule.model_validate(rule_payload)


def test_franchise_requires_quantity(rule_payload: dict) -> None:
    rule_payload.update(rule_type="FRANCHISE", value=None)
    with pytest.raises(ValidationError):
        ContractRule.model_validate(rule_payload)
    rule_payload.update(quantity="6", unit="hora")
    assert ContractRule.model_validate(rule_payload).quantity == 6


def test_adjustment_requires_index(rule_payload: dict) -> None:
    rule_payload.update(rule_type="ADJUSTMENT", value=None)
    with pytest.raises(ValidationError):
        ContractRule.model_validate(rule_payload)
    rule_payload.update(adjustment_index="IPCA", periodicity_months=12)
    assert ContractRule.model_validate(rule_payload).adjustment_index == "IPCA"


def test_extension_requires_new_end(rule_payload: dict) -> None:
    rule_payload.update(rule_type="EXTENSION", value=None)
    with pytest.raises(ValidationError):
        ContractRule.model_validate(rule_payload)
    rule_payload["validity"] = {"end": "2027-12-31"}
    assert ContractRule.model_validate(rule_payload).validity.end is not None


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("relation", "SUBSTITUTED"),
        ("review_status", "UNKNOWN"),
        ("rule_type", "DISCOUNT"),
    ],
)
def test_s08_invalid_enum_in_rule_fails(rule_payload: dict, field: str, invalid: str) -> None:
    rule_payload[field] = invalid
    with pytest.raises(ValidationError):
        ContractRule.model_validate(rule_payload)


def test_rule_rejects_float_value_and_extra_field(rule_payload: dict) -> None:
    with pytest.raises(ValidationError):
        ContractRule.model_validate({**rule_payload, "value": 10500.0})
    with pytest.raises(ValidationError):
        ContractRule.model_validate({**rule_payload, "unexpected": 1})


def test_rule_rejects_verification_score_out_of_range(rule_payload: dict) -> None:
    with pytest.raises(ValidationError):
        ContractRule.model_validate({**rule_payload, "verification_score": "1.5"})


def _amendment_payload(rule_payload: dict, relation: str) -> dict:
    return {
        "id": "amend-1",
        "affected_rule_id": "rule-1",
        "new_rule": copy.deepcopy(rule_payload) if relation == "ALTERED" else None,
        "source_document_id": "doc-2",
        "effective_date": "2026-10-01",
        "relation": relation,
    }


def test_altered_amendment_requires_new_rule(rule_payload: dict) -> None:
    assert Amendment.model_validate(_amendment_payload(rule_payload, "ALTERED")).new_rule
    payload = {**_amendment_payload(rule_payload, "ALTERED"), "new_rule": None}
    with pytest.raises(ValidationError):
        Amendment.model_validate(payload)


def test_revoked_amendment_has_no_new_rule(rule_payload: dict) -> None:
    assert Amendment.model_validate(_amendment_payload(rule_payload, "REVOKED")).new_rule is None
    payload = {**_amendment_payload(rule_payload, "REVOKED"), "new_rule": rule_payload}
    with pytest.raises(ValidationError):
        Amendment.model_validate(payload)


def test_amendment_cannot_be_created_relation(rule_payload: dict) -> None:
    payload = {**_amendment_payload(rule_payload, "ALTERED"), "relation": "CREATED"}
    with pytest.raises(ValidationError):
        Amendment.model_validate(payload)


def test_s06_rule_without_evidence_fails(rule_payload: dict) -> None:
    del rule_payload["evidence"]
    with pytest.raises(ValidationError):
        ContractRule.model_validate(rule_payload)
