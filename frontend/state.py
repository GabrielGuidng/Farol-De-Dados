"""Gerenciamento de caminho do banco, sessão Streamlit e cliente Gemini."""

import os
import sqlite3
from pathlib import Path

import streamlit as st

from backend.agentes import GeminiLLMClient, LLMClient, LLMExecutionError
from backend.persistencia import connect, init_db

VIEW_DASHBOARD = "Painel e Relatório"
VIEW_CONTRACTS = "Contratos e Documentos"
VIEW_RULES = "Regras e Vigência"
VIEW_BILLING = "Cobranças e Auditoria"
VIEW_HISTORY = "Histórico e Dados"

NAV_VIEWS: tuple[str, ...] = (
    VIEW_DASHBOARD,
    VIEW_CONTRACTS,
    VIEW_RULES,
    VIEW_BILLING,
    VIEW_HISTORY,
)

DEFAULT_DB_FILE = Path("farol_dados.db")
DEFAULT_COMPETENCE = "2026-04"


def get_db_path() -> Path:
    """Resolve o caminho do SQLite (priorizando `FAROL_DB_PATH`) e garante o esquema."""
    configured = os.environ.get("FAROL_DB_PATH")
    db_path = Path(configured) if configured else DEFAULT_DB_FILE
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        init_db(db_path).close()
    else:
        # Garante que todas as tabelas e triggers estejam criadas caso o arquivo esteja vazio.
        init_db(db_path).close()
    return db_path


def open_connection() -> sqlite3.Connection:
    """Abre conexão SQLite configurada para a interface."""
    return connect(get_db_path())


def build_gemini_client() -> tuple[LLMClient | None, str | None]:
    """Instancia o cliente Gemini a partir de `.env` sem expor segredos em tela."""
    try:
        client = GeminiLLMClient.from_env()
    except LLMExecutionError as exc:
        return None, (
            "Não foi possível inicializar a IA com as variáveis atuais de `.env`: "
            f"{exc}. Configure `GEMINI_API_KEY` ou carregue o cenário sintético de demonstração."
        )
    except OSError as exc:
        return None, f"Falha ao ler configuração de IA no ambiente: {exc}."
    return client, None


def ensure_session_defaults() -> None:
    """Inicializa chaves padrão de navegação sem sobrescrever escolhas do usuário."""
    st.session_state.setdefault("theme", "dark")
    if st.session_state.get("active_view") not in NAV_VIEWS:
        st.session_state["active_view"] = VIEW_CONTRACTS
    st.session_state.setdefault("selected_contract_id", None)
    st.session_state.setdefault("selected_competence", DEFAULT_COMPETENCE)
    st.session_state.setdefault("reviewer_name", "auditor.local")
