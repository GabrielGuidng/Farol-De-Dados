"""Fonte única dos tokens cromáticos (Escuro padrão e Claro) do Farol de Dados."""

from typing import Literal

ThemeName = Literal["dark", "light"]

# Cores proibidas segundo .regras/frontend_style.md §3.7 (neon, roxos, rosados e preto puro).
FORBIDDEN_HEX: tuple[str, ...] = (
    "#0033FF",
    "#0600AB",
    "#1B2CC1",
    "#2038B6",
    "#53159B",
    "#977DFF",
    "#FFCCF2",
    "#000000",
)

THEMES: dict[ThemeName, dict[str, str]] = {
    "dark": {
        "bg-canvas": "#0B0D12",
        "bg-surface": "#12151C",
        "bg-subtle": "#1B1F2A",
        "bg-sidebar": "#07080B",
        "bg-sidebar-hover": "#1B1F2A",
        "text-primary": "#EEF0F7",
        "text-secondary": "#A0A6BB",
        "text-muted": "#7B819B",
        "text-on-dark": "#EEF0F7",
        "text-on-dark-muted": "#A0A6BB",
        "primary": "#23518C",
        "primary-hover": "#2C65AE",
        "primary-pressed": "#1B3F6E",
        "on-primary": "#FFFFFF",
        "link": "#ABD2FA",
        "focus-ring": "rgba(171,210,250,.40)",
        "border-focus": "#7FA8E0",
        "accent-blue": "#1F64BF",
        "gold": "#C2A267",
        "gold-text": "#D8BF8A",
        "gold-bg": "#1C1812",
        "gold-border": "#4A3E26",
        "border": "#2A2F40",
        "border-sidebar": "rgba(194,162,103,.30)",
        "sidebar-gradient": "linear-gradient(180deg,#07080B 0%,#0F1430 100%)",
        "canvas-gradient": (
            "radial-gradient(ellipse 90% 55% at 50% 100%, rgba(35,81,140,.28) 0%, "
            "rgba(20,26,64,.55) 48%, rgba(11,13,18,0) 85%), "
            "linear-gradient(180deg, #0B0D12 0%, #0B0D12 38%, #0F1430 74%, #141A40 100%)"
        ),
        "ok-bg": "#0F2418",
        "ok-bd": "#1F5A38",
        "ok-tx": "#86EFAC",
        "bad-bg": "#2A1214",
        "bad-bd": "#7F1D1D",
        "bad-tx": "#FCA5A5",
        "info-bg": "#0F1D33",
        "info-bd": "#1E3A66",
        "info-tx": "#ABD2FA",
        "chart-1": "#7FA8E0",
        "chart-2": "#1F64BF",
        "chart-3": "#A0A6BB",
        "chart-4": "#ABD2FA",
    },
    "light": {
        "bg-canvas": "#F7F8FC",
        "bg-surface": "#FFFFFF",
        "bg-subtle": "#EEF0F7",
        "bg-sidebar": "#0D0F15",
        "bg-sidebar-hover": "#1B1F2A",
        "text-primary": "#141A40",
        "text-secondary": "#404259",
        "text-muted": "#6B6F94",
        "text-on-dark": "#EEF0F7",
        "text-on-dark-muted": "#A0A6BB",
        "primary": "#23518C",
        "primary-hover": "#1B3F6E",
        "primary-pressed": "#141A40",
        "on-primary": "#FFFFFF",
        "link": "#23518C",
        "focus-ring": "rgba(35,81,140,.35)",
        "border-focus": "#A2BDD2",
        "accent-blue": "#1F64BF",
        "gold": "#A58F69",
        "gold-text": "#735114",
        "gold-bg": "#F9F6F0",
        "gold-border": "#E2D8C5",
        "border": "#D8D9EA",
        "border-sidebar": "rgba(165,143,105,.35)",
        "sidebar-gradient": "linear-gradient(180deg,#0D0F15 0%,#141A40 100%)",
        "canvas-gradient": (
            "radial-gradient(ellipse 90% 55% at 50% 100%, rgba(255,255,255,.85) 0%, "
            "rgba(162,189,210,.18) 55%, rgba(238,240,247,0) 85%), "
            "linear-gradient(180deg, #EEF0F7 0%, #F7F8FC 52%, #FFFFFF 100%)"
        ),
        "ok-bg": "#ECFDF3",
        "ok-bd": "#BBF7D0",
        "ok-tx": "#14532D",
        "bad-bg": "#FEF2F2",
        "bad-bd": "#FECACA",
        "bad-tx": "#991B1B",
        "info-bg": "#EFF6FF",
        "info-bd": "#BFDBFE",
        "info-tx": "#23518C",
        "chart-1": "#23518C",
        "chart-2": "#1F64BF",
        "chart-3": "#4F5173",
        "chart-4": "#A2BDD2",
    },
}

CHART_COLORS: dict[ThemeName, tuple[str, str, str, str]] = {
    "dark": (
        THEMES["dark"]["chart-1"],
        THEMES["dark"]["chart-2"],
        THEMES["dark"]["chart-3"],
        THEMES["dark"]["chart-4"],
    ),
    "light": (
        THEMES["light"]["chart-1"],
        THEMES["light"]["chart-2"],
        THEMES["light"]["chart-3"],
        THEMES["light"]["chart-4"],
    ),
}
