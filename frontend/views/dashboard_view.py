"""Etapa 7.5 — Painel executivo, gráficos cronológicos e Relatório Executivo de Auditoria."""

import sqlite3
from datetime import datetime
from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from backend.api import (
    audit_stored_invoice,
    fetch_audits,
    fetch_contract,
    fetch_documents,
    fetch_invoices,
    fetch_reviews,
    fetch_rules,
)
from backend.persistencia import StoredAudit
from backend.schemas import (
    CLASSIFICATION_SEVERITY_ORDER,
    AuditClassification,
    ContractRule,
    Invoice,
    ReviewStatus,
    ReviewTargetType,
)
from frontend.ui.components import (
    KpiItem,
    escape_text,
    render_badge,
    render_banner,
    render_empty_state,
    render_html_table,
    render_kpi_row,
    render_page_header,
)
from frontend.ui.executive_report import (
    CompetenceSemaphoreRow,
    ExecutiveReportSummary,
    FindingSeverity,
    build_executive_csv_bytes,
    build_executive_report,
    build_executive_xlsx_bytes,
    build_printable_report_html,
)
from frontend.ui.formatting import (
    AUDIT_CLASSIFICATION_BADGES,
    REVIEW_STATUS_BADGES,
    format_brl,
    format_datetime_br,
    format_signed_brl,
)
from frontend.ui.tokens import CHART_COLORS, THEMES, ThemeName

_ZERO = Decimal("0.00")


def _active_theme_name() -> ThemeName:
    raw = st.session_state.get("theme", "dark")
    return "light" if raw == "light" else "dark"


def _apply_chart_theme(chart: alt.Chart, palette: dict[str, str]) -> alt.Chart:
    return (
        chart.properties(usermeta={"embedOptions": {"actions": False}})
        .configure(background=palette["bg-surface"])
        .configure_view(stroke=palette["border"], strokeWidth=1)
        .configure_axis(
            labelColor=palette["text-secondary"],
            titleColor=palette["text-secondary"],
            gridColor=palette["border"],
            domainColor=palette["border"],
            tickColor=palette["border"],
        )
        .configure_legend(
            labelColor=palette["text-secondary"],
            titleColor=palette["text-primary"],
        )
        .configure_title(color=palette["text-primary"])
    )


def _build_classification_chart(
    counts: dict[AuditClassification, int],
    theme: ThemeName,
) -> alt.Chart:
    palette = THEMES[theme]
    series_colors = CHART_COLORS[theme]
    color_by_cls: dict[AuditClassification, str] = {
        AuditClassification.MISSING_DOCUMENTATION: series_colors[1],
        AuditClassification.DIVERGENCE: palette["bad-tx"],
        AuditClassification.NEEDS_REVIEW: series_colors[2],
        AuditClassification.NO_DIVERGENCE: palette["ok-tx"],
    }
    labels = [AUDIT_CLASSIFICATION_BADGES[cls][2] for cls in CLASSIFICATION_SEVERITY_ORDER]
    colors = [color_by_cls[cls] for cls in CLASSIFICATION_SEVERITY_ORDER]
    df = pd.DataFrame(
        {
            "classificacao": labels,
            "quantidade": [counts.get(cls, 0) for cls in CLASSIFICATION_SEVERITY_ORDER],
        }
    )
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("quantidade:Q", title="Notas", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("classificacao:N", sort=labels, title=None),
            color=alt.Color(
                "classificacao:N",
                legend=None,
                scale=alt.Scale(domain=labels, range=colors),
            ),
            tooltip=[
                alt.Tooltip("classificacao:N", title="Classificação"),
                alt.Tooltip("quantidade:Q", title="Notas"),
            ],
        )
        .properties(title="Contagem por classificação", height=230)
    )
    return _apply_chart_theme(chart, palette)


def _build_competence_chart(
    rows: tuple[CompetenceSemaphoreRow, ...],
    theme: ThemeName,
) -> alt.Chart:
    palette = THEMES[theme]
    series_colors = CHART_COLORS[theme]
    ordered_periods = [row.competence_pt for row in rows]
    records: list[dict[str, object]] = []
    for row in rows:
        diff_txt = format_signed_brl(row.difference_total)
        exp_val = row.expected_total if row.expected_total is not None else _ZERO
        records.append(
            {
                "competencia": row.competence_pt,
                "serie": "Valor vigente",
                "valor": float(exp_val),
                "valor_brl": format_brl(row.expected_total),
                "diferenca_brl": diff_txt,
            }
        )
        records.append(
            {
                "competencia": row.competence_pt,
                "serie": "Valor cobrado",
                "valor": float(row.charged_total),
                "valor_brl": format_brl(row.charged_total),
                "diferenca_brl": diff_txt,
            }
        )

    df = pd.DataFrame(records)
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("competencia:N", sort=ordered_periods, title="Competência"),
            xOffset="serie:N",
            y=alt.Y("valor:Q", title="Valor (R$)"),
            color=alt.Color(
                "serie:N",
                title="Série",
                scale=alt.Scale(
                    domain=["Valor vigente", "Valor cobrado"],
                    range=[series_colors[0], series_colors[1]],
                ),
            ),
            tooltip=[
                alt.Tooltip("competencia:N", title="Competência"),
                alt.Tooltip("serie:N", title="Série"),
                alt.Tooltip("valor_brl:N", title="Valor"),
                alt.Tooltip("diferenca_brl:N", title="Diferença no mês"),
            ],
        )
        .properties(title="Valor vigente × Valor cobrado por competência", height=230)
    )
    return _apply_chart_theme(chart, palette)


def _build_rules_overview_chart(rules: list[ContractRule], theme: ThemeName) -> alt.Chart:
    palette = THEMES[theme]
    series_colors = CHART_COLORS[theme]
    ordered_statuses = (
        ReviewStatus.CONFIRMED,
        ReviewStatus.CORRECTED,
        ReviewStatus.PENDING_REVIEW,
        ReviewStatus.INCONCLUSIVE,
        ReviewStatus.REJECTED,
    )
    color_by_status: dict[ReviewStatus, str] = {
        ReviewStatus.CONFIRMED: palette["ok-tx"],
        ReviewStatus.CORRECTED: series_colors[0],
        ReviewStatus.PENDING_REVIEW: series_colors[1],
        ReviewStatus.INCONCLUSIVE: series_colors[2],
        ReviewStatus.REJECTED: palette["bad-tx"],
    }
    counts: dict[ReviewStatus, int] = {status: 0 for status in ordered_statuses}
    for rule in rules:
        counts[rule.review_status] = counts.get(rule.review_status, 0) + 1

    labels = [REVIEW_STATUS_BADGES[status][2] for status in ordered_statuses]
    colors = [color_by_status[status] for status in ordered_statuses]
    df = pd.DataFrame(
        {
            "status": labels,
            "quantidade": [counts[status] for status in ordered_statuses],
        }
    )
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("quantidade:Q", title="Regras", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("status:N", sort=labels, title=None),
            color=alt.Color(
                "status:N",
                legend=None,
                scale=alt.Scale(domain=labels, range=colors),
            ),
            tooltip=[
                alt.Tooltip("status:N", title="Status da regra"),
                alt.Tooltip("quantidade:Q", title="Regras"),
            ],
        )
        .properties(title="Regras contratuais extraídas por status de revisão", height=220)
    )
    return _apply_chart_theme(chart, palette)


def _severity_badge_html(severity: FindingSeverity) -> str:
    if severity == "Alta":
        return render_badge("bad", "▲", "Alta")
    if severity == "Média":
        return render_badge("ai", "✦", "Média")
    return render_badge("info", "●", "Baixa")


def _render_executive_report_section(
    summary: ExecutiveReportSummary,
    *,
    audited_items: list[tuple[Invoice, StoredAudit]],
    scope_label: str,
) -> None:
    st.subheader("Relatório Executivo de Auditoria")

    generated_at_br = format_datetime_br(datetime.now())
    html_download = build_printable_report_html(
        summary,
        scope_label=scope_label,
        generated_at_br=generated_at_br,
        auto_print=False,
    )
    xlsx_bytes = build_executive_xlsx_bytes(summary, audited_items)
    csv_bytes = build_executive_csv_bytes(summary)

    e1, e2, e3, e4 = st.columns(4, gap="small")
    with e1:
        if st.button(
            "Imprimir / Salvar PDF",
            type="primary",
            key="print_exec_report_btn",
            width="stretch",
        ):
            st.session_state["dashboard_print_html"] = build_printable_report_html(
                summary,
                scope_label=scope_label,
                generated_at_br=generated_at_br,
                auto_print=True,
            )
    with e2:
        st.download_button(
            "Baixar HTML imprimível",
            data=html_download,
            file_name="relatorio_executivo_farol.html",
            mime="text/html",
            key="download_exec_html_btn",
            width="stretch",
        )
    with e3:
        st.download_button(
            "Exportar Excel (.xlsx)",
            data=xlsx_bytes,
            file_name="relatorio_executivo_farol.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_exec_xlsx_btn",
            width="stretch",
        )
    with e4:
        st.download_button(
            "Exportar CSV (.csv)",
            data=csv_bytes,
            file_name="achados_auditoria_farol.csv",
            mime="text/csv",
            key="download_exec_csv_btn",
            width="stretch",
        )

    print_payload = st.session_state.pop("dashboard_print_html", None)
    if isinstance(print_payload, str) and print_payload:
        st.iframe(
            print_payload,
            height=1,
            alt="Janela de impressão do Relatório Executivo de Auditoria",
        )
        st.caption(
            "Diálogo de impressão aberto. Se o navegador bloquear a janela, use o botão "
            "'Baixar HTML imprimível' ao lado e salve como PDF com Ctrl+P."
        )

    # 1. Topo em até 5 linhas: veredito, quantas exigem ação e exposição estimada
    verdict_items = "".join(f"<li>{escape_text(line)}</li>" for line in summary.verdict_lines[:4])
    st.html(
        f'<div class="verdict-box {summary.verdict_tone}">'
        f'<div class="verdict-headline">{escape_text(summary.verdict_headline)}</div>'
        f'<ul class="verdict-list">{verdict_items}</ul>'
        "</div>"
    )

    # 2. Tabela de Achados por Gravidade
    st.markdown("#### Achados priorizados por gravidade")
    if not summary.findings:
        render_banner(
            "ok",
            "Sem achados pendentes",
            "Todas as notas deste escopo passaram nas verificações sem divergência.",
        )
    else:
        finding_rows = [
            [
                _severity_badge_html(f.severity),
                f.invoices_label,
                f.competence,
                f.problem,
                f.amount_display,
                f"<strong>{escape_text(f.suggested_action)}</strong>",
            ]
            for f in summary.findings
        ]
        render_html_table(
            ["Gravidade", "Nota", "Competência", "Problema", "Valor", "Ação sugerida"],
            finding_rows,
            numeric_cols={4},
            html_cols={0, 5},
        )

    # 3. Quadro por Competência com semáforo acessível
    st.markdown("#### Quadro por competência")
    comp_table_rows = [
        [
            row.competence_pt,
            str(row.invoices_count),
            format_brl(row.expected_total),
            format_brl(row.charged_total),
            format_signed_brl(row.difference_total),
            render_badge(row.tone, row.icon, row.status_label),
        ]
        for row in summary.competence_rows
    ]
    render_html_table(
        ["Competência", "Notas", "Valor vigente", "Valor cobrado", "Diferença", "Situação"],
        comp_table_rows,
        numeric_cols={1, 2, 3, 4},
        html_cols={5},
    )

    # 4. Notas sem problema em uma única linha
    render_banner("ok", "Notas sem divergência", summary.clean_summary_line)

    # 5. Apêndice Técnico exclusivamente para notas com achado
    if summary.appendix_entries:
        with st.expander(
            f"Apêndice técnico — apenas notas com achado ({len(summary.appendix_entries)})",
            expanded=False,
        ):
            for entry in summary.appendix_entries:
                badge = render_badge(entry.tone, "•", entry.classification_label)
                checks_html = "".join(
                    '<div style="margin-top:6px;">'
                    f"<strong>{escape_text(chk.check_label)}:</strong> "
                    f"{escape_text(chk.message)}"
                    + (
                        '<div class="calc-memory">'
                        f"{escape_text(chr(10).join(chk.memory_lines))}</div>"
                        if chk.memory_lines
                        else ""
                    )
                    + "</div>"
                    for chk in entry.checks
                )
                clauses_html = "".join(
                    '<div class="audit-evidence-panel">'
                    f'<div class="audit-evidence-title">{escape_text(cl.rule_label)} — '
                    f"{escape_text(cl.location_label)}</div>"
                    f"<div><em>“{escape_text(cl.literal_excerpt)}”</em></div>"
                    "</div>"
                    for cl in entry.clauses
                )
                st.html(
                    '<div class="deterministic-box">'
                    '<div class="box-meta-row">'
                    f"<strong>Nota {escape_text(entry.invoice_number)} • "
                    f"{escape_text(entry.competence_pt)} • {escape_text(entry.supplier)}</strong>"
                    f"<span>{badge}</span>"
                    "</div>"
                    f'<div class="compact-rule-sub">Vigente: {escape_text(entry.expected_display)}'
                    f" • Cobrado: {escape_text(entry.charged_display)} • "
                    f"Diferença: {escape_text(entry.difference_display)}</div>"
                    f"{checks_html}"
                    f"{clauses_html}"
                    "</div>"
                )


def render_dashboard_view(conn: sqlite3.Connection) -> None:
    """Renderiza painel executivo, gráficos cronológicos e Relatório Executivo (7.5)."""
    render_page_header(
        "Painel e Relatório",
        (
            "Veredito imediato, exposição financeira em risco, quadro por competência e "
            "exportação executiva para PDF, Excel e CSV."
        ),
        eyebrow="Etapa 5 • Painel Executivo & Relatório",
    )

    flash = st.session_state.pop("dashboard_flash", None)
    if isinstance(flash, tuple) and len(flash) == 3:
        render_banner(flash[0], flash[1], flash[2])

    all_invoices = list(fetch_invoices(conn))
    selected_id: str | None = st.session_state.get("selected_contract_id")
    active_contract = fetch_contract(conn, selected_id) if selected_id else None

    c_scope, c_recalc = st.columns([2.2, 1.0], gap="small", vertical_alignment="bottom")
    with c_scope:
        scope_options = ["Todos os contratos", "Contrato ativo"]
        scope_raw = st.segmented_control(
            "Escopo do painel",
            options=scope_options,
            default="Todos os contratos",
            key="dashboard_scope_ctrl",
        )
        scope = (
            scope_raw
            if isinstance(scope_raw, str) and scope_raw in scope_options
            else "Todos os contratos"
        )

    if scope == "Contrato ativo" and active_contract is not None:
        active_invoices = [inv for inv in all_invoices if inv.contract_id == active_contract.id]
        if active_invoices:
            scoped_invoices = active_invoices
            scope_label = f"{active_contract.id} — {active_contract.supplier}"
        else:
            if all_invoices:
                render_banner(
                    "info",
                    "Contrato ativo sem cobranças vinculadas",
                    (
                        f"O contrato {active_contract.id} ainda não possui cobranças vinculadas; "
                        "exibindo todas as cobranças disponíveis no sistema."
                    ),
                )
            scoped_invoices = all_invoices
            scope_label = "Todos os contratos"
    else:
        if scope == "Contrato ativo" and active_contract is None:
            st.caption("Nenhum contrato ativo selecionado; exibindo todos os contratos.")
        scoped_invoices = all_invoices
        scope_label = "Todos os contratos"

    theme = _active_theme_name()
    if not scoped_invoices:
        all_rules = list(fetch_rules(conn))
        if all_rules:
            render_banner(
                "ai",
                "Falta enviar cobrança / nota fiscal para auditoria",
                (
                    f"Há {len(all_rules)} regra(s) contratual(is) registrada(s), mas nenhuma "
                    "nota fiscal, planilha, fatura PDF/DOCX ou dossiê enviado ainda."
                ),
            )
            st.altair_chart(
                _build_rules_overview_chart(all_rules, theme),
                theme=None,
                width="stretch",
                alt="Contagem de regras contratuais extraídas por status de revisão",
                key="chart_rules_overview",
            )
        else:
            render_empty_state(
                "Nenhuma cobrança registrada neste escopo",
                "Importe notas fiscais, planilhas ou dossiês para gerar o painel e o relatório.",
                next_action="Envie arquivos em Contratos e Documentos ou Cobranças e Auditoria.",
            )
        return

    audited_items: list[tuple[Invoice, StoredAudit]] = []
    unaudited: list[Invoice] = []
    for inv in scoped_invoices:
        audits = fetch_audits(conn, inv.id)
        if audits:
            audited_items.append((inv, audits[-1]))
        else:
            unaudited.append(inv)

    reports_meta: dict[str, dict[str, object]] = dict(
        st.session_state.get("audit_reports_meta", {})
    )
    if unaudited:
        with st.spinner("Auditando cobranças pendentes no motor determinístico…"):
            for pending_inv in unaudited:
                outcome = audit_stored_invoice(conn, pending_inv.id)
                audited_items.append((pending_inv, outcome.stored_audit))
                reports_meta[outcome.stored_audit.result.id] = {
                    "final_text": outcome.report.final_text,
                    "rejected_llm_text": outcome.report.rejected_llm_text,
                    "used_llm_text": outcome.report.used_llm_text,
                    "awaiting_link_review": outcome.awaiting_link_review,
                    "warnings": list(outcome.warnings),
                }
        st.session_state["audit_reports_meta"] = reports_meta

    with c_recalc:
        if st.button(
            "Recalcular escopo",
            type="secondary",
            key="dashboard_audit_pending_btn",
            width="stretch",
        ):
            executed = 0
            with st.spinner("Recalculando auditorias no motor determinístico…"):
                for target_inv in scoped_invoices:
                    existing_audits = fetch_audits(conn, target_inv.id)
                    has_human_decision = any(
                        bool(
                            fetch_reviews(
                                conn,
                                target_type=ReviewTargetType.AUDIT_RESULT,
                                target_id=aud.result.id,
                            )
                        )
                        for aud in existing_audits
                    )
                    if has_human_decision:
                        continue
                    outcome = audit_stored_invoice(conn, target_inv.id)
                    executed += 1
                    reports_meta[outcome.stored_audit.result.id] = {
                        "final_text": outcome.report.final_text,
                        "rejected_llm_text": outcome.report.rejected_llm_text,
                        "used_llm_text": outcome.report.used_llm_text,
                        "awaiting_link_review": outcome.awaiting_link_review,
                        "warnings": list(outcome.warnings),
                    }
            st.session_state["audit_reports_meta"] = reports_meta
            st.session_state["dashboard_flash"] = (
                "ok",
                "Auditoria atualizada",
                f"{executed} cobrança(s) recalculada(s) e atualizada(s) no painel.",
            )
            st.rerun()

    doc_names = {doc.id: doc.file_name for doc in fetch_documents(conn)}
    summary = build_executive_report(audited_items, doc_names=doc_names)

    counts: dict[AuditClassification, int] = {cls: 0 for cls in CLASSIFICATION_SEVERITY_ORDER}
    for _, stored in audited_items:
        counts[stored.result.classification] = counts.get(stored.result.classification, 0) + 1

    render_kpi_row(
        [
            KpiItem(
                "Notas auditadas",
                f"{summary.total_invoices}",
                hint=f"{summary.clean_invoices_count} sem divergência",
            ),
            KpiItem(
                "Exigem ação",
                str(summary.findings_count),
                hint=f"{summary.action_invoices_count} nota(s) envolvida(s)",
            ),
            KpiItem(
                "Exposição estimada",
                format_brl(summary.exposure_total),
                hint="Sobrecustos, duplicidades e notas sem vínculo",
            ),
            KpiItem(
                "Total cobrado",
                format_brl(summary.charged_total),
                hint=(
                    f"Vigente esperado: {format_brl(summary.expected_total)}"
                    if summary.expected_total is not None
                    else "Base vigente não apurada"
                ),
            ),
        ]
    )

    col_chart_cls, col_chart_comp = st.columns(2, gap="large")
    with col_chart_cls:
        st.altair_chart(
            _build_classification_chart(counts, theme),
            theme=None,
            width="stretch",
            alt="Contagem de notas por classificação de auditoria",
            key="chart_classifications",
        )
    with col_chart_comp:
        st.altair_chart(
            _build_competence_chart(summary.competence_rows, theme),
            theme=None,
            width="stretch",
            alt="Comparativo entre valor vigente e valor cobrado por competência",
            key="chart_competence_values",
        )

    st.divider()
    _render_executive_report_section(
        summary,
        audited_items=audited_items,
        scope_label=scope_label,
    )
