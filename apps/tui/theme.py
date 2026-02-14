"""Hexis Textual theme — color tokens matching cli_theme.py / hexis-ui CSS."""
from __future__ import annotations

from textual.design import ColorSystem
from textual.theme import Theme

HEXIS_COLORS = {
    "accent": "#d8774f",
    "accent_strong": "#b45835",
    "teal": "#3c6f64",
    "muted": "#4e463d",
    "bg": "#1a1a1a",
    "surface": "#242424",
    "text": "#e0d8d0",
}

hexis_theme = Theme(
    name="hexis",
    primary=HEXIS_COLORS["accent"],
    secondary=HEXIS_COLORS["teal"],
    accent=HEXIS_COLORS["accent_strong"],
    background=HEXIS_COLORS["bg"],
    surface=HEXIS_COLORS["surface"],
    foreground=HEXIS_COLORS["text"],
    success="#4a9",
    warning="#da3",
    error="#c44",
    dark=True,
)
