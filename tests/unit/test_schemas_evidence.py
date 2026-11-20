"""Testes de evidencia documental: S-06 e S-09."""

import pytest
from pydantic import ValidationError

from backend.schemas import Evidence


def test_evidence_with_page_is_valid(evidence_payload: dict) -> None:
    evidence = Evidence.model_validate(evidence_payload)
    assert evidence.page == 3
    assert evidence.without_page is False


def test_s09_docx_with_clause_and_paragraph_is_valid_and_marked_without_page() -> None:
    evidence = Evidence(
        source_document_id="doc-2",
        clause="5.2",
        paragraph="1",
        literal_excerpt="Valor de R$ 10.500,00.",
    )
    assert evidence.page is None
    assert evidence.without_page is True


@pytest.mark.parametrize(
    "locator",
    [{}, {"clause": "5.2"}, {"paragraph": "1"}, {"page": 0}, {"page": -1}],
)
def test_s09_evidence_without_valid_locator_fails(locator: dict) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            {"source_document_id": "doc-2", "literal_excerpt": "Trecho.", **locator}
        )


@pytest.mark.parametrize("excerpt", ["", "   "])
def test_s06_evidence_without_literal_excerpt_fails(evidence_payload: dict, excerpt: str) -> None:
    evidence_payload["literal_excerpt"] = excerpt
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence_payload)


def test_s06_evidence_without_document_fails(evidence_payload: dict) -> None:
    del evidence_payload["source_document_id"]
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence_payload)
