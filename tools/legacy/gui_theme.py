"""LEGACY QUARANTINED: non-shipping theme wrapper."""

from __future__ import annotations

from ui.theme import build_palette, build_stylesheet
from ui.theme_tokens import DEFAULT_THEME, ThemeTokens

TOKENS = DEFAULT_THEME.colors

__all__ = ["TOKENS", "ThemeTokens", "build_palette", "build_stylesheet"]

