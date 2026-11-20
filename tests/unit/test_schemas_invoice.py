"""Testes de Invoice e BillingItem (1.7)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.schemas import BillingItem, Invoice


def test_valid_invoice(invoice_payload: dict) -> None:
    invoice = Invoice.model_validate(invoice_payload)
    assert invoice.reference_period.last_day.isoformat() == "2026-10-31"
    assert invoice.competence_inferred is False
    assert invoice.items == ()


def test_invoice_without_contract_is_allowed(invoice_payload: dict) -> None:
    del invoice_payload["contract_id"]
    assert Invoice.model_validate(invoice_payload).contract_id is None


def test_negative_total_fails(invoice_payload: dict) -> None:
    invoice_payload["total"] = "-1.00"
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload)


@pytest.mark.parametrize("invalid", ["2026-13", "10/2026", "2026-1"])
def test_invalid_reference_period_fails(invoice_payload: dict, invalid: str) -> None:
    invoice_payload["reference_period"] = invalid
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload)


def test_invoice_rejects_float_total_and_extra_field(invoice_payload: dict) -> None:
    with pytest.raises(ValidationError):
        Invoice.model_validate({**invoice_payload, "total": 10800.0})
    with pytest.raises(ValidationError):
        Invoice.model_validate({**invoice_payload, "unexpected": 1})


def test_billing_item_keeps_unit_value_precision() -> None:
    item = BillingItem.model_validate(
        {
            "description": "Hora adicional de suporte",
            "quantity": "8",
            "unit": "hora",
            "unit_value": "150.1234567890",
            "total_value": "1200.99",
        }
    )
    assert item.unit_value == Decimal("150.1234567890")


def test_invoice_converts_items_to_immutable_tuple(invoice_payload: dict) -> None:
    item = {
        "description": "Mensalidade",
        "quantity": "1",
        "unit_value": "10800",
        "total_value": "10800",
    }
    invoice = Invoice.model_validate({**invoice_payload, "items": [item]})
    assert isinstance(invoice.items, tuple)
    assert len(invoice.items) == 1
