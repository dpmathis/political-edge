"""Confluence Card — compact widget showing multi-source convergence for a ticker."""

import streamlit as st

from dashboard.components.color_system import (
    DIRECTION_COLORS,
    render_direction_badge,
)


def render_confluence_card(confluence_data: dict, company_name: str = "", sector: str = ""):
    """Render a compact confluence card for the ticker grid.

    Args:
        confluence_data: Dict from analysis.confluence.compute_confluence().
        company_name: Company name for display.
        sector: Sector for display.
    """
    ticker = confluence_data.get("ticker", "???")
    score = confluence_data.get("score", 0)
    direction = confluence_data.get("direction", "neutral")
    strength = confluence_data.get("strength", "weak")
    factors = confluence_data.get("factors", [])

    dir_color = DIRECTION_COLORS.get(direction, DIRECTION_COLORS["neutral"])
    dir_badge = render_direction_badge(direction)

    # Score bar (out of 7)
    filled = min(score, 7)
    bar_filled = "█" * filled
    bar_empty = "░" * (7 - filled)

    # Strength color
    strength_colors = {"strong": "#22c55e", "moderate": "#f59e0b", "weak": "#94a3b8"}
    s_color = strength_colors.get(strength, "#94a3b8")

    # Factor checklist
    check_items = []
    for f in factors:
        if f["contributing"]:
            check_items.append(
                f'<div style="font-size:11px; color:#22c55e; margin:1px 0;">✓ {f["source"]}</div>'
            )
        else:
            check_items.append(
                f'<div style="font-size:11px; color:#cbd5e1; margin:1px 0;">✗ {f["source"]}</div>'
            )

    card_html = f"""
    <div style="border:1px solid {dir_color}33; border-radius:10px; padding:14px;
                background:white; min-height:180px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <span style="font-size:18px; font-weight:bold;">{ticker}</span>
            {dir_badge}
        </div>
        <div style="font-size:12px; color:#64748b; margin-bottom:8px;">
            {company_name[:30] if company_name else ""}{f" | {sector}" if sector else ""}
        </div>
        <div style="margin-bottom:8px;">
            <div style="font-size:11px; color:#94a3b8; margin-bottom:2px;">CONFLUENCE</div>
            <span style="font-family:monospace; letter-spacing:2px;">
                <span style="color:{s_color};">{bar_filled}</span><span style="color:#e2e8f0;">{bar_empty}</span>
            </span>
            <span style="font-weight:600; color:{s_color}; font-size:13px; margin-left:6px;">{score}/7</span>
        </div>
        <div>
            {"".join(check_items)}
        </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)
