"""Ponto de entrada único da interface Streamlit do Farol de Dados."""

import sys
from contextlib import closing
from pathlib import Path

# Garante que `backend` e `frontend` sejam importáveis com `streamlit run`.
_ROOT_DIR = Path(__file__).resolve().parents[1]
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

import streamlit as st

from backend.api import fetch_contracts, fetch_invoices, fetch_rules
from frontend.state import (
    NAV_VIEWS,
    VIEW_BILLING,
    VIEW_CONTRACTS,
    VIEW_DASHBOARD,
    VIEW_HISTORY,
    VIEW_RULES,
    ensure_session_defaults,
    open_connection,
)
from frontend.ui.theme import inject_theme
from frontend.views.billing_audit_view import render_billing_audit_view
from frontend.views.contracts_view import render_contracts_view
from frontend.views.dashboard_view import render_dashboard_view
from frontend.views.history_data_view import render_history_data_view
from frontend.views.rules_vigencia_view import render_rules_vigencia_view


def _on_nav_change() -> None:
    chosen = st.session_state.get("nav_view_radio")
    if isinstance(chosen, str) and chosen in NAV_VIEWS:
        st.session_state["active_view"] = chosen


def _on_contract_change() -> None:
    chosen = st.session_state.get("sidebar_contract_select")
    if isinstance(chosen, str):
        st.session_state["selected_contract_id"] = chosen


def _render_sidebar(
    contract_ids: tuple[str, ...],
    contract_labels: dict[str, str],
    pending_rules_count: int,
    invoices_count: int,
) -> str:
    """Renderiza navegação principal, seletor de contrato ativo e estatísticas."""

    def _format_contract_option(contract_id: str) -> str:
        return contract_labels.get(contract_id, contract_id)

    with st.sidebar:
        st.html(
            '<div class="sidebar-brand">'
            '<div class="sidebar-brand-title">Farol de Dados</div>'
            '<div class="sidebar-brand-sub">Auditoria Contratual</div>'
            "</div>"
        )

        current_view_raw = st.session_state.get("active_view", VIEW_CONTRACTS)
        current_view = (
            current_view_raw
            if isinstance(current_view_raw, str) and current_view_raw in NAV_VIEWS
            else VIEW_CONTRACTS
        )
        st.session_state["nav_view_radio"] = current_view
        chosen_view = st.radio(
            "Navegação",
            options=list(NAV_VIEWS),
            key="nav_view_radio",
            on_change=_on_nav_change,
        )
        active_view = (
            chosen_view
            if isinstance(chosen_view, str) and chosen_view in NAV_VIEWS
            else current_view
        )
        st.session_state["active_view"] = active_view

        st.divider()

        if contract_ids:
            current_contract_raw = st.session_state.get("selected_contract_id")
            current_contract = (
                current_contract_raw
                if isinstance(current_contract_raw, str) and current_contract_raw in contract_ids
                else contract_ids[0]
            )
            st.session_state["selected_contract_id"] = current_contract
            st.session_state["sidebar_contract_select"] = current_contract
            selected_contract = st.selectbox(
                "Contrato ativo",
                options=list(contract_ids),
                format_func=_format_contract_option,
                key="sidebar_contract_select",
                on_change=_on_contract_change,
            )
            st.session_state["selected_contract_id"] = (
                selected_contract
                if isinstance(selected_contract, str) and selected_contract in contract_ids
                else current_contract
            )
        else:
            st.session_state["selected_contract_id"] = None
            st.session_state.pop("sidebar_contract_select", None)
            st.caption("Nenhum contrato cadastrado ainda.")

        reviewer_default = str(st.session_state.get("reviewer_name", "auditor.local"))
        reviewer = st.text_input(
            "Auditor(a) responsável",
            value=reviewer_default,
            key="sidebar_reviewer_input",
        )
        st.session_state["reviewer_name"] = (reviewer or "").strip() or "auditor.local"

        st.html(
            '<div class="sidebar-stats">'
            f"<div>Contratos: <strong>{len(contract_ids)}</strong></div>"
            f"<div>Regras pendentes: <strong>{pending_rules_count}</strong></div>"
            f"<div>Cobranças: <strong>{invoices_count}</strong></div>"
            "</div>"
        )
    return active_view


def main() -> None:
    """Configura a página, injeta o tema ativo e direciona para a vista selecionada."""
    st.set_page_config(
        page_title="Farol de Dados — Auditoria Contratual",
        layout="wide",
    )
    ensure_session_defaults()
    _ = inject_theme()

    with closing(open_connection()) as conn:
        contracts = fetch_contracts(conn)
        contract_ids = tuple(c.id for c in contracts)
        all_rules = fetch_rules(conn)
        pending_count = sum(1 for r in all_rules if not r.review_status.is_trusted)
        invoices_count = len(fetch_invoices(conn))

        active_view = _render_sidebar(
            contract_ids,
            {c.id: f"{c.id} — {c.supplier}" for c in contracts},
            pending_count,
            invoices_count,
        )

        if active_view == VIEW_DASHBOARD:
            render_dashboard_view(conn)
        elif active_view == VIEW_CONTRACTS:
            render_contracts_view(conn)
        elif active_view == VIEW_RULES:
            render_rules_vigencia_view(conn)
        elif active_view == VIEW_BILLING:
            render_billing_audit_view(conn)
        elif active_view == VIEW_HISTORY:
            render_history_data_view(conn)


if __name__ == "__main__":
    main()
