"""Aplicação dos temas Escuro (padrão) e Claro em Streamlit."""

from pathlib import Path

import streamlit as st

from frontend.ui.tokens import THEMES, ThemeName

_STYLES_PATH = Path(__file__).with_name("styles.css")


def build_theme_css(theme: ThemeName) -> str:
    """Gera o bloco `<style>` combinando as variáveis do tema e o CSS global."""
    variables = "".join(f"--{key}:{value};" for key, value in THEMES[theme].items())
    base_css = _STYLES_PATH.read_text(encoding="utf-8")
    return f"<style>:root{{{variables}}}\n{base_css}</style>"


def inject_theme() -> ThemeName:
    """Renderiza o seletor de tema na sidebar e injeta os tokens do tema ativo."""
    current: ThemeName = st.session_state.get("theme", "dark")
    if current not in THEMES:
        current = "dark"
        st.session_state["theme"] = current

    with st.sidebar:
        choice = st.radio(
            "Tema",
            ["🌙 Escuro", "☀️ Claro"],
            index=0 if current == "dark" else 1,
            horizontal=True,
            label_visibility="collapsed",
            key="theme_selector_widget",
        )
    selected: ThemeName = "dark" if choice is not None and "Escuro" in choice else "light"
    st.session_state["theme"] = selected

    st.markdown(build_theme_css(selected), unsafe_allow_html=True)
    return selected
