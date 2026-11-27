"""Etapa 7.6 — Histórico de decisões humanas, carga sintética e exclusão total de dados."""

import csv
import io
import sqlite3

import streamlit as st

from backend.api import (
    fetch_contracts,
    fetch_documents,
    fetch_invoices,
    fetch_reviews,
    reset_all_data,
    seed_synthetic_demo,
)
from backend.schemas import HumanReview, ReviewDecision, ReviewTargetType
from frontend.state import get_db_path
from frontend.ui.components import (
    KpiItem,
    render_badge,
    render_banner,
    render_empty_state,
    render_html_table,
    render_kpi_row,
    render_page_header,
)
from frontend.ui.formatting import (
    AUDIT_DECISION_LABELS,
    REVIEW_DECISION_LABELS,
    REVIEW_TARGET_LABELS,
    BadgeToneLiteral,
    format_datetime_br,
)

_FILTER_OPTIONS: tuple[str, ...] = ("Todos", "Regras", "Documentos", "Auditorias")
_FILTER_TO_TARGET: dict[str, ReviewTargetType] = {
    "Regras": ReviewTargetType.RULE,
    "Documentos": ReviewTargetType.DOCUMENT,
    "Auditorias": ReviewTargetType.AUDIT_RESULT,
}

_TRANSIENT_KEYS: tuple[str, ...] = (
    "last_upload_batch",
    "last_doc_result",
    "audit_reports_meta",
    "billing_import_warnings",
    "focused_invoice_select",
)


def _decision_label(review: HumanReview) -> str:
    if review.target_type is ReviewTargetType.AUDIT_RESULT:
        return AUDIT_DECISION_LABELS[review.decision]
    return REVIEW_DECISION_LABELS[review.decision]


def _decision_badge(review: HumanReview) -> str:
    label = _decision_label(review)
    if review.decision is ReviewDecision.CONFIRM:
        tone: BadgeToneLiteral = "ok"
        icon = "✓"
    elif review.decision is ReviewDecision.CORRECT:
        tone = "ai"
        icon = "✦"
    else:
        tone = "bad"
        icon = "▲"
    return render_badge(tone, icon, label)


def _reviews_to_csv(reviews: list[HumanReview]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "data_hora",
            "revisor",
            "tipo_alvo",
            "identificador",
            "decisao",
            "status_anterior",
            "status_posterior",
            "justificativa",
        ]
    )
    for rev in reviews:
        writer.writerow(
            [
                rev.reviewed_at.isoformat(),
                rev.user,
                REVIEW_TARGET_LABELS.get(rev.target_type, rev.target_type.value),
                rev.target_id,
                _decision_label(rev),
                rev.previous_status.value,
                rev.new_status.value,
                rev.justification,
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def render_history_data_view(conn: sqlite3.Connection) -> None:
    """Renderiza histórico cronológico de decisões, carga sintética e exclusão total de dados."""
    render_page_header(
        "Histórico e Dados",
        (
            "Consulte o registro completo de decisões humanas, carregue dados sintéticos "
            "para demonstração ou limpe o banco local."
        ),
        eyebrow="Etapa 6 • Rastreabilidade & Governança de Dados",
    )

    flash = st.session_state.pop("history_flash", None)
    if isinstance(flash, tuple) and len(flash) == 3:
        render_banner(flash[0], flash[1], flash[2])

    all_reviews = list(fetch_reviews(conn))
    rules_reviews = sum(1 for r in all_reviews if r.target_type is ReviewTargetType.RULE)
    docs_reviews = sum(1 for r in all_reviews if r.target_type is ReviewTargetType.DOCUMENT)
    audit_reviews = sum(1 for r in all_reviews if r.target_type is ReviewTargetType.AUDIT_RESULT)

    render_kpi_row(
        [
            KpiItem("Decisões registradas", str(len(all_reviews))),
            KpiItem("Regras revisadas", str(rules_reviews)),
            KpiItem("Documentos revisados", str(docs_reviews)),
            KpiItem("Auditorias decididas", str(audit_reviews)),
        ]
    )

    st.subheader("Registro de decisões humanas")
    selected_filter_raw = st.segmented_control(
        "Filtrar por tipo de alvo",
        options=list(_FILTER_OPTIONS),
        default="Todos",
        key="history_target_filter",
    )
    selected_filter = (
        selected_filter_raw
        if isinstance(selected_filter_raw, str) and selected_filter_raw in _FILTER_OPTIONS
        else "Todos"
    )
    target_filter = _FILTER_TO_TARGET.get(selected_filter)
    filtered_reviews = [
        rev
        for rev in reversed(all_reviews)
        if target_filter is None or rev.target_type is target_filter
    ]

    if not filtered_reviews:
        render_empty_state(
            "Nenhuma decisão registrada neste filtro",
            "As aprovações, correções, ressalvas e rejeições humanas aparecerão aqui em ordem "
            "cronológica.",
            next_action="Revise regras, aditivos ou auditorias — ou carregue o cenário sintético.",
        )
    else:
        rows: list[list[str]] = []
        for rev in filtered_reviews:
            rows.append(
                [
                    format_datetime_br(rev.reviewed_at),
                    rev.user,
                    REVIEW_TARGET_LABELS.get(rev.target_type, rev.target_type.value),
                    rev.target_id,
                    _decision_badge(rev),
                    f"{rev.previous_status.value} → {rev.new_status.value}",
                    rev.justification,
                ]
            )
        render_html_table(
            [
                "Data / Hora",
                "Pessoa revisora",
                "Tipo de alvo",
                "Identificador",
                "Decisão",
                "Transição de status",
                "Justificativa",
            ],
            rows,
            html_cols={4},
        )
        st.download_button(
            "Exportar histórico de decisões (.csv)",
            data=_reviews_to_csv(filtered_reviews),
            file_name="historico_decisoes_farol.csv",
            mime="text/csv",
            type="secondary",
            key="download_reviews_csv_btn",
        )

    st.divider()
    st.subheader("Governança do banco local")
    col_seed, col_reset = st.columns(2, gap="large")

    with col_seed:
        st.markdown("#### Cenário sintético de demonstração")
        st.caption(
            "Carrega offline o contrato CT-2026-001 completo (contrato-base PDF, 2 aditivos "
            "revisados e cobranças de `data/sintetico/`) sem usar cota de API."
        )
        if st.button(
            "Carregar cenário sintético de demonstração",
            type="secondary",
            key="seed_demo_btn",
        ):
            try:
                seeded = seed_synthetic_demo(conn)
            except (FileNotFoundError, ValueError) as exc:
                render_banner("ai", "Carga sintética não aplicada", str(exc))
            else:
                st.session_state["selected_contract_id"] = "CT-2026-001"
                st.session_state["history_flash"] = (
                    "ok",
                    "Cenário sintético carregado",
                    (
                        f"Contrato {seeded[0]} e {max(len(seeded) - 1, 0)} cobrança(s) prontos "
                        "para explorar nas telas."
                    ),
                )
                st.rerun()

    with col_reset:
        st.markdown("#### Apagar todos os dados")
        contracts_count = len(fetch_contracts(conn))
        docs_count = len(fetch_documents(conn))
        invoices_count = len(fetch_invoices(conn))
        st.caption(
            f"Base atual: {contracts_count} contrato(s), {docs_count} documento(s), "
            f"{invoices_count} cobrança(s) e {len(all_reviews)} decisão(ões). "
            "Esta ação recria o arquivo SQLite do zero."
        )

        if st.session_state.pop("_reset_full_confirm_flag", False):
            st.session_state["confirm_full_reset"] = False
        confirm_reset = st.checkbox(
            "Confirmo que desejo apagar permanentemente todos os dados",
            key="confirm_full_reset",
        )
        if st.button(
            "Apagar todos os dados",
            type="secondary",
            key="reset_all_data_btn",
        ):
            if not confirm_reset:
                render_banner(
                    "bad",
                    "Confirmação obrigatória",
                    "Marque a caixa de confirmação antes de apagar todos os dados do sistema.",
                )
            else:
                conn.close()
                reset_all_data(get_db_path())
                st.session_state["selected_contract_id"] = None
                for key in _TRANSIENT_KEYS:
                    st.session_state.pop(key, None)
                st.session_state["_reset_full_confirm_flag"] = True
                st.session_state["history_flash"] = (
                    "ok",
                    "Dados apagados",
                    (
                        "Todos os contratos, documentos, regras, cobranças, auditorias "
                        "e histórico foram removidos."
                    ),
                )
                st.rerun()
