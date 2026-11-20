"""Fixtures reutilizaveis de payloads validos para os testes de schemas."""

import copy

import pytest

VALID_CNPJ = "11.222.333/0001-81"


@pytest.fixture
def evidence_payload() -> dict:
    return {
        "source_document_id": "doc-1",
        "page": 3,
        "literal_excerpt": "O valor mensal passa a ser de R$ 10.500,00.",
    }


@pytest.fixture
def rule_payload(evidence_payload: dict) -> dict:
    return {
        "id": "rule-1",
        "contract_id": "contract-1",
        "rule_type": "MONTHLY_PRICE",
        "value": "10500.00",
        "evidence": copy.deepcopy(evidence_payload),
    }


@pytest.fixture
def contract_payload() -> dict:
    return {
        "id": "contract-1",
        "company": "Empresa Exemplo",
        "supplier": "Fornecedor Exemplo",
        "supplier_cnpj": VALID_CNPJ,
        "subject": "Servicos de suporte",
        "validity": {"start": "2026-01-01", "end": None},
        "comparison_tolerance": "0.00",
        "status": "ACTIVE",
    }


@pytest.fixture
def document_payload() -> dict:
    return {
        "id": "doc-1",
        "contract_id": "contract-1",
        "document_type": "CONTRACT",
        "file_name": "contrato.pdf",
        "received_at": "2026-11-01T10:00:00",
        "file_hash": "a" * 64,
        "read_quality": "OK",
        "extracted_text": "Texto do contrato.",
    }


@pytest.fixture
def invoice_payload() -> dict:
    return {
        "id": "inv-1",
        "contract_id": "contract-1",
        "supplier": "Fornecedor Exemplo",
        "supplier_cnpj": VALID_CNPJ,
        "number": "107",
        "issue_date": "2026-11-05",
        "reference_period": "2026-10",
        "total": "10800.00",
        "source": "PDF",
    }
