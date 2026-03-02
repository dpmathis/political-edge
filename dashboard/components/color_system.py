"""Centralized color system and render helpers for consistent UI.

Establishes a strict color language across all pages:
- Green = Long / Bullish / Positive
- Red = Short / Bearish / Negative
- Amber = Watch / Caution / Low confidence
- Blue = Informational / Neutral
- Purple = Macro regime / Contextual overlay
"""

import streamlit as st

# ── Direction Colors ──────────────────────────────────────────────────
DIRECTION_COLORS = {
    "long": "#22c55e",
    "short": "#ef4444",
    "watch": "#f59e0b",
    "neutral": "#3b82f6",
    "none": "#94a3b8",
}

DIRECTION_LABELS = {
    "long": "LONG",
    "short": "SHORT",
    "watch": "WATCH",
    "neutral": "NEUTRAL",
    "none": "",
}

# ── Impact Severity (1-5) ────────────────────────────────────────────
IMPACT_SEVERITY = {
    5: {"color": "#ef4444", "bg": "rgba(239,68,68,0.12)", "label": "Critical", "desc": "Likely to move prices"},
    4: {"color": "#f97316", "bg": "rgba(249,115,22,0.12)", "label": "High", "desc": "Worth monitoring"},
    3: {"color": "#eab308", "bg": "rgba(234,179,8,0.12)", "label": "Moderate", "desc": "Background noise unless in your sector"},
    2: {"color": "#94a3b8", "bg": "rgba(148,163,184,0.12)", "label": "Low", "desc": "Informational only"},
    1: {"color": "#94a3b8", "bg": "rgba(148,163,184,0.12)", "label": "Low", "desc": "Informational only"},
}

# ── Conviction Colors ─────────────────────────────────────────────────
CONVICTION_COLORS = {
    "high": "#22c55e",
    "medium": "#f59e0b",
    "low": "#94a3b8",
}

CONVICTION_BARS = {
    "high": ("████", "░", 4),
    "medium": ("███", "░░", 3),
    "low": ("██", "░░░", 2),
}

# ── Macro Regime Colors ───────────────────────────────────────────────
REGIME_COLORS = {
    1: "#22c55e",  # Goldilocks — green
    2: "#f59e0b",  # Reflation — amber
    3: "#f97316",  # Stagflation — orange
    4: "#ef4444",  # Deflation — red
}


# ── Render Helpers ────────────────────────────────────────────────────

def render_direction_badge(direction: str) -> str:
    """Return an HTML badge for a trade direction (long/short/watch)."""
    direction = (direction or "none").lower()
    color = DIRECTION_COLORS.get(direction, DIRECTION_COLORS["none"])
    label = DIRECTION_LABELS.get(direction, direction.upper())
    return (
        f'<span style="background:{color}; color:white; padding:2px 10px; '
        f'border-radius:4px; font-weight:bold; font-size:13px;">{label}</span>'
    )


def render_impact_indicator(score: int) -> str:
    """Return an HTML impact indicator with colored dot, label, and description."""
    info = IMPACT_SEVERITY.get(score, IMPACT_SEVERITY[1])
    color = info["color"]
    return (
        f'<span style="display:inline-flex; align-items:center; gap:6px;">'
        f'<span style="width:10px; height:10px; border-radius:50%; background:{color}; display:inline-block;"></span>'
        f'<span style="font-weight:600; color:{color};">{info["label"]}</span>'
        f'<span style="color:#64748b; font-size:12px;">— {info["desc"]}</span>'
        f'</span>'
    )


def render_impact_dot(score: int) -> str:
    """Return a compact colored dot + score for table use."""
    info = IMPACT_SEVERITY.get(score, IMPACT_SEVERITY[1])
    color = info["color"]
    return (
        f'<span style="display:inline-flex; align-items:center; gap:4px;">'
        f'<span style="width:8px; height:8px; border-radius:50%; background:{color}; display:inline-block;"></span>'
        f'<span style="font-weight:600; color:{color};">{score}/5</span>'
        f'</span>'
    )


def render_conviction_bar(level: str) -> str:
    """Return an HTML conviction bar (████░ HIGH)."""
    level = (level or "low").lower()
    color = CONVICTION_COLORS.get(level, CONVICTION_COLORS["low"])
    filled, empty, _ = CONVICTION_BARS.get(level, CONVICTION_BARS["low"])
    label = level.upper()
    return (
        f'<span style="font-family:monospace; letter-spacing:2px;">'
        f'<span style="color:{color};">{filled}</span>'
        f'<span style="color:#e2e8f0;">{empty}</span>'
        f'</span> '
        f'<span style="font-weight:600; color:{color}; font-size:12px;">{label}</span>'
    )


def render_conviction_bar_simple(level: str) -> str:
    """Return a compact conviction indicator for inline use."""
    level = (level or "low").lower()
    color = CONVICTION_COLORS.get(level, CONVICTION_COLORS["low"])
    return (
        f'<span style="background:{color}22; color:{color}; padding:2px 8px; '
        f'border-radius:4px; font-weight:600; font-size:12px;">{level.upper()}</span>'
    )


def hex_to_rgba(hex_color: str, alpha: float = 0.13) -> str:
    """Convert hex color to rgba string for Plotly compatibility."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"
