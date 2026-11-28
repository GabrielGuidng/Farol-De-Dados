"""Testes da verificação de valor por extenso (BRL) em Decimal puro."""

from decimal import Decimal

from backend.motor import WRITTEN_AMOUNT_CHECK, check_written_amount, parse_brl_extenso
from backend.schemas import CheckResult, DivergenceDirection
from tests.fixtures.scenario import make_invoice, monthly_item


def test_parse_brl_extenso_extracts_standard_brazilian_phrases() -> None:
    assert parse_brl_extenso(
        "Valor total: R$ 31.942,50 (trinta e um mil, novecentos e quarenta e dois reais "
        "e cinquenta centavos)"
    ) == Decimal("31942.50")
    assert parse_brl_extenso("cinquenta e dois mil e trezentos reais") == Decimal("52300.00")
    assert parse_brl_extenso("um milhão, duzentos mil reais") == Decimal("1200000.00")
    assert parse_brl_extenso("cem reais") == Decimal("100.00")
    assert parse_brl_extenso("Prestação de serviços sem menção por extenso") is None


def test_check_written_amount_flags_divergence_when_text_disagrees_with_total() -> None:
    inv = make_invoice(
        "inv-ext-1",
        "8840",
        "2026-04",
        [monthly_item("10000.00")],
        total="10000.00",
    ).model_copy(
        update={
            "service_description": (
                "Serviços mensais — valor por extenso: doze mil reais (R$ 10.000,00)."
            )
        }
    )
    chk = check_written_amount(inv, Decimal("0.00"))
    assert chk is not None
    assert chk.name == WRITTEN_AMOUNT_CHECK
    assert chk.result is CheckResult.DIVERGENCE
    assert chk.direction is DivergenceDirection.UNDERCHARGE
    assert "R$ 12.000,00" in chk.message

    matching = inv.model_copy(
        update={"service_description": "Serviços mensais — valor por extenso: dez mil reais."}
    )
    assert check_written_amount(matching, Decimal("0.00")) is None
