"""Testes do Agente Tradutor: cenários T-01, T-02 e T-03 (Etapa 5.6)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.agentes import (
    FakeLLMClient,
    ItemRuleLinkCandidate,
    LinkIssueCode,
    TradutorExtractionPayload,
    TradutorInput,
    TranslationStatus,
    run_tradutor_agent,
)
from backend.schemas import BillingItem, RuleType
from tests.fixtures.scenario import base_rules, make_rule


def _tradutor_input(items: tuple[BillingItem, ...], rules=None) -> TradutorInput:
    return TradutorInput(
        document_id="inv-104",
        items=items,
        active_rules=tuple(base_rules() if rules is None else rules),
    )


def _item(description: str, amount: str = "10500.00") -> BillingItem:
    return BillingItem(
        description=description,
        quantity=Decimal("1"),
        unit="mês",
        unit_value=Decimal(amount),
        total_value=Decimal(amount),
    )


def test_t01_link_without_justifying_excerpt_is_invalid_in_schema() -> None:
    with pytest.raises(ValidationError):
        ItemRuleLinkCandidate(
            item_index=0,
            rule_id="price-base",
            item_excerpt="",
            rule_excerpt="O valor mensal é de R$ 10.000,00.",
            similarity_score=Decimal("0.95"),
        )
    with pytest.raises(ValidationError):
        ItemRuleLinkCandidate(
            item_index=0,
            rule_id="price-base",
            item_excerpt="Mensalidade de suporte",
            rule_excerpt="   ",
            similarity_score=Decimal("0.95"),
        )
    with pytest.raises(ValidationError):
        ItemRuleLinkCandidate(
            item_index=0,
            rule_id="price-base",
            item_excerpt="Mensalidade de suporte",
            rule_excerpt="O valor mensal é de R$ 10.000,00.",
            similarity_score=0.95,  # type: ignore[arg-type]
        )


def test_tradutor_valid_link_never_exposes_calculations_and_marks_linked() -> None:
    items = (
        _item("Mensalidade de suporte técnico"),
        BillingItem(
            description="Horas excedentes de suporte",
            quantity=Decimal("8"),
            unit="hora",
            unit_value=Decimal("150.00"),
            total_value=Decimal("1200.00"),
        ),
    )
    payload = _tradutor_input(items)
    llm_output = TradutorExtractionPayload(
        links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="price-base",
                item_excerpt="Mensalidade de suporte técnico",
                rule_excerpt="O valor mensal é de R$ 10.000,00.",
                similarity_score=Decimal("0.94"),
            ),
            ItemRuleLinkCandidate(
                item_index=1,
                rule_id="overage",
                item_excerpt="Horas excedentes de suporte",
                rule_excerpt="Hora excedente a R$ 150,00.",
                similarity_score=Decimal("0.92"),
            ),
        )
    )
    client = FakeLLMClient([llm_output])

    result = run_tradutor_agent(payload, client)

    assert result.status is TranslationStatus.LINKED
    assert result.ready_for_calculation is True
    assert result.warnings == ()
    assert len(result.links) == 2
    assert result.links[0].rule_type is RuleType.MONTHLY_PRICE
    assert result.links[1].rule_type is RuleType.OVERAGE_PRICE


def test_t02_link_below_threshold_or_ambiguous_goes_to_needs_review() -> None:
    items = (_item("Serviço técnico eventual"),)
    payload = _tradutor_input(items)

    below_threshold = TradutorExtractionPayload(
        links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="price-base",
                item_excerpt="Serviço técnico eventual",
                rule_excerpt="O valor mensal é de R$ 10.000,00.",
                similarity_score=Decimal("0.72"),
            ),
        )
    )
    res_low = run_tradutor_agent(payload, FakeLLMClient([below_threshold]))
    assert res_low.status is TranslationStatus.NEEDS_REVIEW
    assert res_low.ready_for_calculation is False
    assert LinkIssueCode.BELOW_SIMILARITY_THRESHOLD in res_low.links[0].issues

    # Limiar ajustável: o mesmo 0.72 passa se o limiar for configurado para 0.70.
    res_custom = run_tradutor_agent(
        payload,
        FakeLLMClient([below_threshold]),
        similarity_threshold=Decimal("0.70"),
    )
    assert res_custom.status is TranslationStatus.LINKED
    assert res_custom.ready_for_calculation is True

    ambiguous = TradutorExtractionPayload(
        links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="price-base",
                item_excerpt="Serviço técnico eventual",
                rule_excerpt="O valor mensal é de R$ 10.000,00.",
                similarity_score=Decimal("0.91"),
                ambiguous=True,
            ),
        )
    )
    res_amb = run_tradutor_agent(payload, FakeLLMClient([ambiguous]))
    assert res_amb.status is TranslationStatus.NEEDS_REVIEW
    assert res_amb.ready_for_calculation is False
    assert LinkIssueCode.AMBIGUOUS_LINK in res_amb.links[0].issues


def test_t03_two_candidate_items_for_same_rule_go_to_needs_review() -> None:
    items = (
        _item("Mensalidade principal", "10000.00"),
        _item("Taxa adicional de suporte", "800.00"),
    )
    rule = make_rule("price-base", value="10000.00", start=date(2026, 1, 1), excerpt="Mensalidade.")
    payload = _tradutor_input(items, rules=(rule,))
    two_for_one = TradutorExtractionPayload(
        links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="price-base",
                item_excerpt="Mensalidade principal",
                rule_excerpt="Mensalidade.",
                similarity_score=Decimal("0.96"),
            ),
            ItemRuleLinkCandidate(
                item_index=1,
                rule_id="price-base",
                item_excerpt="Taxa adicional de suporte",
                rule_excerpt="Mensalidade.",
                similarity_score=Decimal("0.88"),
            ),
        )
    )
    result = run_tradutor_agent(payload, FakeLLMClient([two_for_one]))

    assert result.status is TranslationStatus.NEEDS_REVIEW
    assert result.ready_for_calculation is False
    assert all(LinkIssueCode.DUPLICATE_RULE_CANDIDATES in link.issues for link in result.links)


def test_tradutor_unanchored_excerpt_and_invalid_schema_require_review() -> None:
    payload = _tradutor_input((_item("Mensalidade"),))
    bad_excerpt = TradutorExtractionPayload(
        links=(
            ItemRuleLinkCandidate(
                item_index=0,
                rule_id="price-base",
                item_excerpt="Texto inexistente no item",
                rule_excerpt="O valor mensal é de R$ 10.000,00.",
                similarity_score=Decimal("0.95"),
            ),
        )
    )
    res = run_tradutor_agent(payload, FakeLLMClient([bad_excerpt]))
    assert res.status is TranslationStatus.NEEDS_REVIEW
    assert LinkIssueCode.EXCERPT_NOT_ANCHORED in res.links[0].issues

    res_schema = run_tradutor_agent(
        payload, FakeLLMClient(["não é json", '{"links": "errado"}', '{"links": [{}]}'])
    )
    assert res_schema.schema_valid is False
    assert res_schema.status is TranslationStatus.NEEDS_REVIEW
    assert res_schema.ready_for_calculation is False
