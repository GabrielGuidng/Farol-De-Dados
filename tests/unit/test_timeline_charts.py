"""Testes unitários dos gráficos em linha da linha do tempo (Painel e Vigência)."""

from datetime import date
from decimal import Decimal

from backend.schemas import (
    Contract,
    ContractRule,
    ContractStatus,
    Document,
    DocumentType,
    Evidence,
    ReadQuality,
    ReferencePeriod,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from frontend.ui.executive_report import CompetenceSemaphoreRow
from frontend.ui.tokens import CHART_COLORS, THEMES
from frontend.views.dashboard_view import _build_competence_chart
from frontend.views.rules_vigencia_view import _build_vigencia_timeline_chart


def test_competence_timeline_line_chart_builds_in_dark_and_light_themes() -> None:
    rows = (
        CompetenceSemaphoreRow(
            period_iso="2025-01",
            competence_pt="01/2025",
            invoices_count=1,
            expected_total=Decimal("78390.00"),
            charged_total=Decimal("78390.00"),
            difference_total=Decimal("0.00"),
            tone="ok",
            icon="✓",
            status_label="Conforme",
        ),
        CompetenceSemaphoreRow(
            period_iso="2025-06",
            competence_pt="06/2025",
            invoices_count=1,
            expected_total=Decimal("90230.00"),
            charged_total=Decimal("96150.00"),
            difference_total=Decimal("5920.00"),
            tone="bad",
            icon="▲",
            status_label="Divergência",
        ),
    )
    for theme in ("dark", "light"):
        spec = _build_competence_chart(rows, theme).to_dict()
        assert spec["config"]["background"] == THEMES[theme]["bg-surface"]
        layers = spec["layer"]
        assert len(layers) == 2
        assert layers[0]["mark"]["type"] == "line"
        assert layers[1]["mark"]["type"] == "circle"
        assert layers[1]["mark"]["color"] == THEMES[theme]["bad-tx"]


def test_vigencia_timeline_line_chart_shows_step_evolution_and_milestones() -> None:
    contract = Contract(
        id="018/2024",
        company="FMS",
        supplier="LIMPAR MAIS",
        supplier_cnpj="24681035000100",
        subject="Limpeza",
        validity=ValidityPeriod(start=date(2025, 1, 2), end=date(2025, 12, 31)),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    docs = {
        "doc-1": Document(
            id="doc-1",
            contract_id="018/2024",
            document_type=DocumentType.CONTRACT,
            file_name="B1_contrato.pdf",
            received_at="2025-01-02T10:00:00+00:00",
            file_hash="a" * 64,
            read_quality=ReadQuality.OK,
            reviewed=True,
            extracted_text="Texto",
        ),
        "doc-2": Document(
            id="doc-2",
            contract_id="018/2024",
            document_type=DocumentType.AMENDMENT,
            file_name="B2_aditivo.pdf",
            received_at="2025-04-01T10:00:00+00:00",
            file_hash="b" * 64,
            read_quality=ReadQuality.OK,
            reviewed=True,
            extracted_text="Texto",
        ),
    }
    rules = (
        ContractRule(
            id="r-1",
            contract_id="018/2024",
            rule_type=RuleType.ITEM_PRICE,
            item_name="Servente",
            value=Decimal("5920.00"),
            quantity=Decimal("12"),
            unit="posto",
            quantity_scope="MONTHLY",
            validity=ValidityPeriod(start=date(2025, 1, 2), end=date(2025, 3, 31)),
            review_status=ReviewStatus.CONFIRMED,
            evidence=Evidence(source_document_id="doc-1", page=1, literal_excerpt="12 postos"),
            version=1,
        ),
        ContractRule(
            id="r-2",
            contract_id="018/2024",
            rule_type=RuleType.ITEM_PRICE,
            item_name="Servente",
            value=Decimal("5920.00"),
            quantity=Decimal("14"),
            unit="posto",
            quantity_scope="MONTHLY",
            validity=ValidityPeriod(start=date(2025, 4, 1)),
            review_status=ReviewStatus.CONFIRMED,
            evidence=Evidence(source_document_id="doc-2", page=1, literal_excerpt="14 postos"),
            relation=RuleRelation.ALTERED,
            version=2,
        ),
    )
    for theme in ("dark", "light"):
        chart = _build_vigencia_timeline_chart(
            contract,
            rules,
            ReferencePeriod(year=2025, month=6),
            docs,
            theme,
        )
        assert chart is not None
        spec = chart.to_dict()
        assert spec["config"]["background"] == THEMES[theme]["bg-surface"]
        layers = spec["layer"]
        assert layers[0]["mark"]["type"] == "line"
        assert layers[0]["mark"]["interpolate"] == "step-after"
        assert layers[0]["mark"]["color"] == CHART_COLORS[theme][0]
        assert any(layer["mark"]["type"] == "rule" for layer in layers)
        assert any(layer["mark"]["type"] == "circle" for layer in layers)
