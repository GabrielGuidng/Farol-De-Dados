"""Testes unitários do Relatório Executivo de Auditoria e exportadores (HTML, CSV, XLSX)."""

import io
from datetime import UTC, date, datetime
from decimal import Decimal

from openpyxl import load_workbook

from backend.motor import PaymentCycleAuditResult, PaymentFinding
from backend.motor.duplicates import DUPLICATE_CHECK
from backend.persistencia import StoredAudit
from backend.schemas import (
    AppliedRule,
    AuditClassification,
    AuditResult,
    Contract,
    ContractRule,
    ContractStatus,
    DivergenceDirection,
    Evidence,
    Invoice,
    InvoiceSource,
    ReferencePeriod,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from backend.schemas.audit import Check
from backend.schemas.enums import CheckResult
from frontend.ui.executive_report import (
    build_executive_csv_bytes,
    build_executive_report,
    build_executive_xlsx_bytes,
    build_printable_report_html,
)

_TS = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)
_FORBIDDEN_CODES: tuple[str, ...] = (
    "NO_DIVERGENCE",
    "NEEDS_REVIEW",
    "MISSING_DOCUMENTATION",
    "POSSIBLE_DIVERGENCE",
    "contract_link",
    "audit-pdf-doc-",
    "pdf-doc-ac46cb9ddb",
)


def _rule_clausula_2() -> ContractRule:
    return ContractRule(
        id="rule-cl2",
        contract_id="2025/0187",
        rule_type=RuleType.MONTHLY_PRICE,
        value=Decimal("48500.00"),
        validity=ValidityPeriod(start=date(2025, 1, 1), end=date(2025, 12, 31)),
        review_status=ReviewStatus.CONFIRMED,
        verification_score=Decimal("0.98"),
        relation=RuleRelation.CREATED,
        version=1,
        evidence=Evidence(
            source_document_id="doc-contrato-2025",
            page=2,
            clause="Cláusula 2ª",
            literal_excerpt="Valor mensal fixo de R$ 48.500,00 para 2025.",
        ),
    )


def _make_audited(
    *,
    inv_id: str,
    number: str,
    month: int,
    total: Decimal,
    classification: AuditClassification,
    difference: Decimal | None,
    base: Decimal | None,
    checks: tuple[Check, ...],
    supplier_cnpj: str = "28.741.963/0001-65",
    contract_id: str | None = "2025/0187",
) -> tuple[Invoice, StoredAudit]:
    period = ReferencePeriod(year=2025, month=month)
    invoice = Invoice(
        id=inv_id,
        contract_id=contract_id,
        supplier="Norte Sul Facilities e Serviços Ltda",
        supplier_cnpj=supplier_cnpj,
        number=number,
        issue_date=date(2025, month, 28),
        reference_period=period,
        total=total,
        source=InvoiceSource.PDF,
    )
    rule = _rule_clausula_2()
    applied = (AppliedRule(rule_id=rule.id, version=rule.version),) if base is not None else ()
    stored = StoredAudit(
        result=AuditResult(
            id=f"audit-{inv_id}",
            invoice_id=inv_id,
            classification=classification,
            checks=checks,
            applied_rules=applied,
            reference_period=period,
            base=base,
            difference=difference,
            total=total,
        ),
        rules_snapshot=(rule,) if base is not None else (),
    )
    return invoice, stored


def _ok_checks() -> tuple[Check, ...]:
    return (
        Check(name="contract_link", result=CheckResult.OK, message="Vínculo OK."),
        Check(name="monthly_price", result=CheckResult.OK, message="Preço OK."),
        Check(name="contract_validity", result=CheckResult.OK, message="Vigência OK."),
    )


def test_executive_report_11_invoices_deduplicates_calculates_exposure_and_exports() -> None:
    # IDs propositalmente fora de ordem alfabética (como ocorria no SQLite antes da correção).
    audited_items = [
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb",
            number="1031",
            month=3,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-10",
            number="1058",
            month=11,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-11",
            number="1062",
            month=12,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-2",
            number="1036",
            month=4,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-3",
            number="1040",
            month=5,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-4",
            number="1044",
            month=6,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-5",
            number="1046",
            month=7,
            total=Decimal("48500.00"),
            classification=AuditClassification.NEEDS_REVIEW,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name=DUPLICATE_CHECK,
                    result=CheckResult.REVIEW,
                    direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
                    message="Possível duplicidade: cobrança 1047 (pdf-doc-ac46cb9ddb-6).",
                    calculation_memory=(
                        "cobrança 1047 (pdf-doc-ac46cb9ddb-6): mesma competência (2025-07)",
                    ),
                ),
            ),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-6",
            number="1047",
            month=7,
            total=Decimal("48500.00"),
            classification=AuditClassification.NEEDS_REVIEW,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name=DUPLICATE_CHECK,
                    result=CheckResult.REVIEW,
                    direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
                    message="Possível duplicidade: cobrança 1046 (pdf-doc-ac46cb9ddb-5).",
                    calculation_memory=(
                        "cobrança 1046 (pdf-doc-ac46cb9ddb-5): mesma competência (2025-07)",
                    ),
                ),
            ),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-7",
            number="1049",
            month=8,
            total=Decimal("48500.00"),
            classification=AuditClassification.MISSING_DOCUMENTATION,
            difference=None,
            base=None,
            supplier_cnpj="00.000.000/0001-91",
            checks=(
                Check(
                    name="contract_link",
                    result=CheckResult.PENDING,
                    message=(
                        "Pendência documental: O CNPJ do fornecedor da cobrança difere do "
                        "CNPJ do contrato."
                    ),
                ),
            ),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-8",
            number="1051",
            month=9,
            total=Decimal("52300.00"),
            classification=AuditClassification.DIVERGENCE,
            difference=Decimal("3800.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name="monthly_price",
                    result=CheckResult.DIVERGENCE,
                    direction=DivergenceDirection.OVERCHARGE,
                    message="Cobrado R$ 3.800,00 acima do contrato.",
                    calculation_memory=("Diferença = R$ 52.300,00 - R$ 48.500,00 = +R$ 3.800,00",),
                    rule_id="rule-cl2",
                ),
            ),
        ),
        _make_audited(
            inv_id="pdf-doc-ac46cb9ddb-9",
            number="1055",
            month=10,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
    ]

    summary = build_executive_report(
        audited_items,
        doc_names={"doc-contrato-2025": "contrato_norte_sul.pdf"},
    )

    assert summary.total_invoices == 11
    assert summary.findings_count == 3
    assert summary.action_invoices_count == 4
    assert summary.clean_invoices_count == 7
    assert summary.exposure_total == Decimal("52300.00")
    assert "11 notas auditadas, 4 notas exigem ação (3 achados)" in summary.verdict_headline
    assert "R$ 52.300,00" in summary.verdict_headline

    assert [
        (f.severity, f.invoices_label, f.competence, f.amount_display) for f in summary.findings
    ] == [
        ("Alta", "1051", "09/2025", "+R$ 3.800,00"),
        ("Média", "1046 e 1047", "07/2025", "R$ 48.500,00"),
        ("Média", "1049", "08/2025", "n/a"),
    ]
    assert summary.findings[0].suggested_action == "Exigir aditivo ou estorno"
    assert summary.findings[1].suggested_action == "Confirmar e bloquear pagamento"
    assert summary.findings[2].suggested_action == "Pedir NF corrigida"

    assert summary.clean_invoice_numbers == ("1031", "1036", "1040", "1044", "1055", "1058", "1062")
    assert "7 notas conferidas" in summary.clean_summary_line
    assert [row.competence_pt for row in summary.competence_rows] == [
        "03/2025",
        "04/2025",
        "05/2025",
        "06/2025",
        "07/2025",
        "08/2025",
        "09/2025",
        "10/2025",
        "11/2025",
        "12/2025",
    ]
    assert [entry.invoice_number for entry in summary.appendix_entries] == [
        "1051",
        "1046",
        "1047",
        "1049",
    ]

    html_out = build_printable_report_html(
        summary,
        scope_label="2025/0187 — Norte Sul Facilities",
        generated_at_br="27/11/2026 16:50",
        auto_print=True,
    )
    assert "window.print()" in html_out
    assert "Exposição estimada" not in html_out or "52.300,00" in html_out
    assert "1046 e 1047" in html_out
    assert "contrato_norte_sul.pdf" in html_out

    csv_text = build_executive_csv_bytes(summary).decode("utf-8-sig")
    assert "Alta;1051;09/2025;Cobrado acima do contrato;+R$ 3.800,00" in csv_text
    assert "Média;1046 e 1047;07/2025;Possível duplicidade;R$ 48.500,00" in csv_text

    xlsx_bytes = build_executive_xlsx_bytes(summary, audited_items)
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    assert wb.sheetnames == ["Achados", "Por Competência", "Cobranças"]
    ws_achados = wb["Achados"]
    assert ws_achados["B2"].value == 52300.0
    assert ws_achados["B6"].value == "1051"
    assert ws_achados["B7"].value == "1046 e 1047"

    xlsx_text = " ".join(
        str(cell.value)
        for sheet in wb.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    )
    for blob in (html_out, csv_text, xlsx_text):
        for forbidden in _FORBIDDEN_CODES:
            assert forbidden not in blob


def test_executive_report_counts_orphan_invoice_risk_and_handles_all_clean() -> None:
    orphan = _make_audited(
        inv_id="inv-orphan-1",
        number="9901",
        month=5,
        total=Decimal("9000.00"),
        classification=AuditClassification.MISSING_DOCUMENTATION,
        difference=None,
        base=None,
        contract_id=None,
        checks=(
            Check(
                name="contract_link",
                result=CheckResult.PENDING,
                message="Cobrança sem contrato vinculado.",
            ),
        ),
    )
    summary_orphan = build_executive_report([orphan])
    assert summary_orphan.exposure_total == Decimal("9000.00")
    assert summary_orphan.findings[0].severity == "Alta"

    clean = _make_audited(
        inv_id="inv-clean-1",
        number="1001",
        month=5,
        total=Decimal("10000.00"),
        classification=AuditClassification.NO_DIVERGENCE,
        difference=Decimal("0.00"),
        base=Decimal("10000.00"),
        checks=_ok_checks(),
    )
    summary_clean = build_executive_report([clean])
    assert summary_clean.findings_count == 0
    assert summary_clean.exposure_total == Decimal("0.00")
    assert summary_clean.verdict_tone == "ok"
    assert summary_clean.appendix_entries == ()


def test_executive_report_uses_single_base_per_competence_and_direct_payment_exposure() -> None:
    audited_items = [
        _make_audited(
            inv_id="i-03",
            number="1031",
            month=3,
            total=Decimal("48500.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="i-07a",
            number="1046",
            month=7,
            total=Decimal("48500.00"),
            classification=AuditClassification.NEEDS_REVIEW,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name=DUPLICATE_CHECK,
                    result=CheckResult.REVIEW,
                    direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
                    message="Possível duplicidade: cobrança 1047 (i-07b).",
                    calculation_memory=("cobrança 1047 (i-07b): mesma competência",),
                ),
            ),
        ),
        _make_audited(
            inv_id="i-07b",
            number="1047",
            month=7,
            total=Decimal("48500.00"),
            classification=AuditClassification.NEEDS_REVIEW,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name=DUPLICATE_CHECK,
                    result=CheckResult.REVIEW,
                    direction=DivergenceDirection.POSSIBLE_DIVERGENCE,
                    message="Possível duplicidade: cobrança 1046 (i-07a).",
                    calculation_memory=("cobrança 1046 (i-07a): mesma competência",),
                ),
            ),
        ),
        _make_audited(
            inv_id="i-08",
            number="1049",
            month=8,
            total=Decimal("48500.00"),
            classification=AuditClassification.MISSING_DOCUMENTATION,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name="contract_link",
                    result=CheckResult.PENDING,
                    message="O CNPJ (28.741.963/0001-68) possui dígito verificador inválido.",
                ),
                Check(
                    name="monthly_price",
                    result=CheckResult.OK,
                    message="Valor cobrado de R$ 48.500,00 confere com a regra vigente.",
                ),
            ),
        ),
        _make_audited(
            inv_id="i-09",
            number="1051",
            month=9,
            total=Decimal("52300.00"),
            classification=AuditClassification.DIVERGENCE,
            difference=Decimal("3800.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name="monthly_price",
                    result=CheckResult.DIVERGENCE,
                    direction=DivergenceDirection.OVERCHARGE,
                    message="Divergência no preço mensal: +R$ 3.800,00.",
                ),
                Check(
                    name="unapproved_adjustment",
                    result=CheckResult.DIVERGENCE,
                    direction=DivergenceDirection.OVERCHARGE,
                    message="Reajuste sem aditivo.",
                ),
            ),
        ),
        _make_audited(
            inv_id="i-10",
            number="1055",
            month=10,
            total=Decimal("48500.00"),
            classification=AuditClassification.DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("48500.00"),
            checks=(
                Check(
                    name="inss_retention",
                    result=CheckResult.DIVERGENCE,
                    direction=DivergenceDirection.UNDERCHARGE,
                    message="INSS retido a menor.",
                    calculation_memory=("diferença na retenção = retido − esperado = -R$ 485,00",),
                ),
            ),
        ),
    ]
    payment_audit = PaymentCycleAuditResult(
        payment_orders_count=6,
        certificates_count=2,
        total_net_paid=Decimal("250000.00"),
        findings=(
            PaymentFinding(
                code="PAYMENT_BENEFICIARY",
                severity="Alta",
                op_numbers=("2025/0690",),
                invoice_numbers=("1031",),
                competence_period=ReferencePeriod(year=2025, month=3),
                problem="Pagamento efetuado a CNPJ/conta bancária de terceiro",
                risk_amount=Decimal("42195.00"),
                suggested_action="Apurar desvio de favorecido",
            ),
        ),
    )
    rep = build_executive_report(audited_items, payment_audit=payment_audit)
    july = next(r for r in rep.competence_rows if r.period_iso == "2025-07")
    assert july.expected_total == Decimal("48500.00")
    assert july.charged_total == Decimal("97000.00")
    assert july.difference_total == Decimal("48500.00")

    august = next(r for r in rep.competence_rows if r.period_iso == "2025-08")
    assert august.expected_total == Decimal("48500.00")
    assert august.difference_total == Decimal("0.00")

    # 5 competências × 48.500 = 242.500 vigente; 294.800 cobrado (+52.300)
    assert rep.expected_total == Decimal("242500.00")
    assert rep.charged_total == Decimal("294800.00")
    # Exposição direta = 3.800 + 48.500 + 485 + 42.195 = 94.980
    assert rep.exposure_total == Decimal("94980.00")


def test_executive_report_groups_findings_separates_cnpjs_and_counts_out_of_validity_exposure() -> (
    None
):
    contract = Contract(
        id="CT-2025-880",
        company="Órgão Contratante",
        supplier="Fornecedor Alfa Ltda",
        supplier_cnpj="00000000000191",
        subject="Sustentação de sistemas",
        validity=ValidityPeriod(),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    items = [
        _make_audited(
            inv_id="inv-8801",
            number="8801",
            month=3,
            total=Decimal("10000.00"),
            classification=AuditClassification.NO_DIVERGENCE,
            difference=Decimal("0.00"),
            base=Decimal("10000.00"),
            contract_id=contract.id,
            checks=_ok_checks(),
        ),
        _make_audited(
            inv_id="inv-8815",
            number="8815",
            month=4,
            total=Decimal("10000.00"),
            classification=AuditClassification.MISSING_DOCUMENTATION,
            difference=None,
            base=None,
            contract_id=contract.id,
            checks=(
                Check(
                    name="contract_link",
                    result=CheckResult.PENDING,
                    message=(
                        "Pendência documental: O CNPJ do fornecedor (28.741.963/0001-99) "
                        "possui dígito verificador inválido (esperado final -65)."
                    ),
                ),
            ),
        ),
        _make_audited(
            inv_id="inv-8830",
            number="8830",
            month=5,
            total=Decimal("10000.00"),
            classification=AuditClassification.MISSING_DOCUMENTATION,
            difference=None,
            base=None,
            contract_id=contract.id,
            checks=(
                Check(
                    name="contract_link",
                    result=CheckResult.PENDING,
                    message=(
                        "Pendência documental: O CNPJ do emitente (28.741.963/0002-46) "
                        "pertence a filial da contratada (mesma raiz, filial /0002 vs. /0001)."
                    ),
                ),
            ),
        ),
        _make_audited(
            inv_id="inv-8831",
            number="8831",
            month=5,
            total=Decimal("10000.00"),
            classification=AuditClassification.MISSING_DOCUMENTATION,
            difference=None,
            base=None,
            contract_id=contract.id,
            checks=(
                Check(
                    name="contract_link",
                    result=CheckResult.PENDING,
                    message=(
                        "Pendência documental: O CNPJ do emitente (28.741.963/0002-46) "
                        "pertence a filial da contratada (mesma raiz, filial /0002 vs. /0001)."
                    ),
                ),
            ),
        ),
        _make_audited(
            inv_id="inv-8890",
            number="8890",
            month=6,
            total=Decimal("31096.00"),
            classification=AuditClassification.MISSING_DOCUMENTATION,
            difference=None,
            base=None,
            contract_id=contract.id,
            checks=(
                Check(
                    name="contract_link",
                    result=CheckResult.PENDING,
                    message=(
                        "Pendência documental: O CNPJ do emitente (33.000.167/0001-01) "
                        "pertence a outro fornecedor diferente do contrato (28.741.963/0001-65)."
                    ),
                ),
            ),
        ),
        _make_audited(
            inv_id="inv-8895",
            number="8895",
            month=7,
            total=Decimal("31096.00"),
            classification=AuditClassification.NEEDS_REVIEW,
            difference=None,
            base=None,
            contract_id=contract.id,
            checks=(
                Check(
                    name="contract_validity",
                    result=CheckResult.REVIEW,
                    message="Competência fora da vigência do contrato.",
                ),
            ),
        ),
    ]

    rep = build_executive_report(items, contracts=[contract], rules=[])
    assert rep.total_invoices == 6
    assert rep.clean_invoices_count == 1
    assert rep.action_invoices_count == 5
    # 8830 e 8831 agrupadas em 1 achado de filial -> 4 achados no total.
    assert rep.findings_count == 4
    assert "6 notas auditadas, 5 notas exigem ação (4 achados)" in rep.verdict_headline
    # Exposição integral de 8890 (31.096) + 8895 (31.096) = 62.192,00
    assert rep.exposure_total == Decimal("62192.00")

    problems = [f.problem for f in rep.findings]
    assert "CNPJ de fornecedor diferente do contrato" in problems
    assert "Competência fora da vigência do contrato" in problems
    assert "CNPJ com dígito verificador inválido" in problems
    assert "Emitida por filial (CNPJ diferente da matriz)" in problems
    filial_finding = next(f for f in rep.findings if "filial" in f.problem)
    assert filial_finding.invoices_label == "8830 e 8831"

    assert len(rep.reading_coverage) == 5
    assert any("CNPJ da contratada" in w for w in rep.reading_warnings)
    assert any("período de vigência" in w for w in rep.reading_warnings)
    assert any("regra de preço mensal" in w for w in rep.reading_warnings)

    html_out = build_printable_report_html(
        rep,
        scope_label="CT-2025-880",
        generated_at_br="27/11/2026 22:00",
    )
    assert "Cobertura da Leitura do Contrato" in html_out
