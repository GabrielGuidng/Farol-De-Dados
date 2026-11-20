"""Testes de Contract e Document (1.5)."""

import pytest
from pydantic import ValidationError

from backend.schemas import Contract, Document


def test_contract_is_valid_and_normalizes_cnpj(contract_payload: dict) -> None:
    contract = Contract.model_validate(contract_payload)
    assert contract.supplier_cnpj == "11222333000181"
    assert contract.validity.end is None


def test_contract_rejects_extra_field(contract_payload: dict) -> None:
    contract_payload["unexpected"] = 1
    with pytest.raises(ValidationError):
        Contract.model_validate(contract_payload)


def test_contract_rejects_negative_tolerance_and_float(contract_payload: dict) -> None:
    for invalid in ("-1.00", 0.5):
        with pytest.raises(ValidationError):
            Contract.model_validate({**contract_payload, "comparison_tolerance": invalid})


def test_contract_rejects_invalid_cnpj(contract_payload: dict) -> None:
    contract_payload["supplier_cnpj"] = "11.222.333/0001-82"
    with pytest.raises(ValidationError):
        Contract.model_validate(contract_payload)


def test_document_is_valid_and_not_reviewed_by_default(document_payload: dict) -> None:
    document = Document.model_validate(document_payload)
    assert document.reviewed is False


def test_document_rejects_extra_field(document_payload: dict) -> None:
    document_payload["unexpected"] = 1
    with pytest.raises(ValidationError):
        Document.model_validate(document_payload)


def test_document_rejects_invalid_hash(document_payload: dict) -> None:
    document_payload["file_hash"] = "not-a-hash"
    with pytest.raises(ValidationError):
        Document.model_validate(document_payload)


def test_document_without_text_must_be_flagged_no_text(document_payload: dict) -> None:
    document_payload["extracted_text"] = ""
    with pytest.raises(ValidationError):
        Document.model_validate(document_payload)
    document_payload["read_quality"] = "NO_TEXT"
    assert Document.model_validate(document_payload).read_quality == "NO_TEXT"
