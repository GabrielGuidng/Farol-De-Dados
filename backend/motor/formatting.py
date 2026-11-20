"""Formatação de valores e fontes para as mensagens exibidas ao usuário (em português)."""

from decimal import Decimal

from backend.motor.calculator import round_money
from backend.schemas import ContractRule


def format_money(value: Decimal) -> str:
    """Formata como moeda brasileira: R$ 10.500,00 (sinal negativo antes do R$)."""
    rounded = round_money(value)
    body = format(abs(rounded), ",.2f").replace(",", "_").replace(".", ",").replace("_", ".")
    return f"-R$ {body}" if rounded < 0 else f"R$ {body}"


def format_percent(value: Decimal) -> str:
    """Formata percentual já arredondado a 2 casas: 2,86%."""
    return format(round_money(value), ".2f").replace(".", ",") + "%"


def format_quantity(value: Decimal) -> str:
    """Formata quantidade sem zeros à direita: 40 ou 6,5."""
    return format(value.normalize(), "f").replace(".", ",")


def describe_rule_source(rule: ContractRule) -> str:
    """Devolve a fonte da regra: documento, página (ou cláusula e parágrafo) e trecho literal."""
    evidence = rule.evidence
    if evidence.page is not None:
        place = f"página {evidence.page}"
    else:
        place = f"cláusula {evidence.clause}, parágrafo {evidence.paragraph}"
    return f"documento {evidence.source_document_id}, {place}: «{evidence.literal_excerpt}»"
