"""Reusable event display components with context-aware interpretations."""

import sqlite3

import streamlit as st

from dashboard.components.color_system import (
    DIRECTION_COLORS,
    IMPACT_SEVERITY,
    render_direction_badge,
    render_impact_indicator,
    render_impact_dot,
)

# Color coding for impact scores (kept for backward compatibility)
IMPACT_COLORS = {
    1: "gray",
    2: "blue",
    3: "orange",
    4: "red",
    5: "red",
}

EVENT_TYPE_LABELS = {
    "final_rule": "Final Rule",
    "proposed_rule": "Proposed Rule",
    "executive_order": "Executive Order",
    "notice": "Notice",
    "bill_signed": "Bill Signed",
    "bill_passed_chamber": "Passed Chamber",
    "bill_passed_committee": "Passed Committee",
    "bill_introduced": "Bill Introduced",
    "hearing_scheduled": "Hearing Scheduled",
    "comment_period_open": "Comment Period Open",
    "comment_period_close": "Comment Period Close",
}

# Plain-English descriptions of what event types mean for trading
EVENT_TYPE_IMPLICATIONS = {
    "final_rule": "Regulatory clarity — uncertainty resolves, stocks often re-price.",
    "proposed_rule": "New regulatory uncertainty — markets may price in risk.",
    "executive_order": "Direct presidential action — can move sectors immediately.",
    "notice": "Informational — usually low impact unless it signals policy shift.",
    "bill_signed": "Law enacted — certainty for affected industries.",
    "bill_passed_chamber": "Advancing legislation — increases probability of becoming law.",
    "bill_passed_committee": "Early-stage progress — watch for momentum.",
    "bill_introduced": "Early signal — low immediate impact, sets direction.",
    "hearing_scheduled": "Congressional attention on a sector — watch for follow-up action.",
    "comment_period_open": "Regulation in progress — outcome still uncertain.",
    "comment_period_close": "Comment window closing — final rule likely within months.",
}

TRADE_ACTION_OPTIONS = ["none", "watch", "long", "short", "close"]


def render_impact_badge(score: int) -> str:
    """Return a colored impact score badge (Streamlit markdown format)."""
    if score >= 4:
        return f":red[**{score}/5**]"
    elif score >= 3:
        return f":orange[**{score}/5**]"
    elif score >= 2:
        return f":blue[{score}/5]"
    return f"{score}/5"


def format_event_type(event_type: str) -> str:
    return EVENT_TYPE_LABELS.get(event_type, event_type.replace("_", " ").title())


def render_so_what(event: dict, conn: sqlite3.Connection = None) -> str:
    """Generate a one-line plain-English 'So what?' interpretation for an event.

    Combines:
    - Event type implications (proposed_rule = uncertainty, final_rule = clarity)
    - Historical performance from event studies (if available)
    - Current macro regime context

    Args:
        event: Dict with keys: event_type, agency, tickers, impact_score, title
        conn: Optional DB connection for historical lookups.

    Returns:
        Plain-English interpretation string.
    """
    event_type = event.get("event_type", "")
    agency = event.get("agency", "")
    tickers = event.get("tickers", "")
    impact_score = event.get("impact_score", 0) or 0
    title = event.get("title", "")

    parts = []

    # 1. Event type implication
    implication = EVENT_TYPE_IMPLICATIONS.get(event_type, "")
    if implication:
        parts.append(implication)

    # 2. Historical performance from event studies (if DB available)
    if conn is not None:
        try:
            # Look for event studies matching this agency or event type
            study_row = conn.execute(
                """SELECT mean_car, win_rate, num_events, p_value
                   FROM event_studies
                   WHERE study_name LIKE ? OR study_name LIKE ?
                   ORDER BY created_at DESC LIMIT 1""",
                (f"%{event_type}%", f"%{agency[:20]}%" if agency else "%nothing%"),
            ).fetchone()
            if study_row and study_row[0] is not None:
                car, win, n, p = study_row
                parts.append(
                    f"Historical: {car:+.1%} avg return, {win:.0%} win rate (N={n})"
                )
        except Exception:
            pass

    # 3. Macro regime context
    if conn is not None:
        try:
            regime_row = conn.execute(
                "SELECT quadrant, quadrant_label FROM macro_regimes ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if regime_row:
                from analysis.macro_regime import QUADRANTS
                q = regime_row[0]
                label = regime_row[1]
                info = QUADRANTS.get(q, {})
                bias = info.get("equity_bias", "neutral").replace("_", " ")
                modifier = info.get("position_modifier", 1.0)
                if modifier < 0.8:
                    parts.append(f"Macro caution: {label} regime suggests reduced positions ({modifier:.1f}x).")
                elif modifier > 1.0:
                    parts.append(f"Macro tailwind: {label} regime supports larger positions ({modifier:.1f}x).")
        except Exception:
            pass

    # 4. Affected tickers
    if tickers:
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
        if ticker_list:
            parts.append(f"Tickers: {', '.join(ticker_list[:5])}")

    return " ".join(parts) if parts else "Monitor for developments."


def render_event_with_context(event: dict, conn: sqlite3.Connection = None):
    """Render an event card with impact indicator, so-what, and ticker chips.

    Uses HTML for rich formatting inside st.markdown().

    Args:
        event: Dict with keys: event_type, title, agency, impact_score,
               tickers, publication_date, source
        conn: Optional DB connection for context lookups.
    """
    impact_score = event.get("impact_score", 0) or 0
    event_type = format_event_type(event.get("event_type", ""))
    title = event.get("title", "")
    agency = event.get("agency", "") or event.get("source", "")
    pub_date = event.get("publication_date", "")
    tickers = event.get("tickers", "")

    severity = IMPACT_SEVERITY.get(impact_score, IMPACT_SEVERITY[1])
    color = severity["color"]
    bg = severity["bg"]
    label = severity["label"]

    # Build ticker chips
    ticker_html = ""
    if tickers:
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
        chips = " ".join(
            f'<span style="background:#e0f2fe; color:#0369a1; padding:2px 8px; '
            f'border-radius:4px; font-size:11px; font-weight:600;">{t}</span>'
            for t in ticker_list[:5]
        )
        ticker_html = f'<div style="margin-top:6px;">{chips}</div>'

    # So what interpretation
    so_what = render_so_what(event, conn)
    so_what_html = ""
    if so_what and impact_score >= 3:
        so_what_html = (
            f'<div style="margin-top:6px; font-size:12px; color:#475569; '
            f'font-style:italic; line-height:1.4;">'
            f'So what? {so_what}'
            f'</div>'
        )

    # Render card based on severity
    if impact_score >= 5:
        # Critical — prominent banner
        card_html = f"""
        <div style="border:1px solid {color}44; border-left:4px solid {color};
                    background:{bg}; border-radius:8px; padding:14px; margin-bottom:10px;">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                <span style="font-weight:700; color:{color};">Impact {impact_score} — {label}</span>
                <span style="color:#64748b; font-size:12px;">| {event_type}</span>
                <span style="color:#94a3b8; font-size:12px;">({agency[:30]})</span>
            </div>
            <div style="font-size:14px; font-weight:500; color:#1e293b;">{title[:150]}</div>
            {so_what_html}
            {ticker_html}
            <div style="font-size:11px; color:#94a3b8; margin-top:6px;">{pub_date}</div>
        </div>
        """
    elif impact_score >= 4:
        # High — compact card
        card_html = f"""
        <div style="border:1px solid {color}33; border-left:3px solid {color};
                    border-radius:6px; padding:10px; margin-bottom:8px;">
            <div style="display:flex; align-items:center; gap:6px; margin-bottom:3px;">
                <span style="font-weight:600; color:{color}; font-size:13px;">Impact {impact_score}</span>
                <span style="color:#64748b; font-size:12px;">| {event_type} ({agency[:25]})</span>
            </div>
            <div style="font-size:13px; color:#334155;">{title[:120]}</div>
            {so_what_html}
            {ticker_html}
        </div>
        """
    else:
        # Moderate/Low — minimal
        card_html = f"""
        <div style="padding:6px 0; border-bottom:1px solid #f1f5f9; margin-bottom:4px;">
            <span style="color:{color}; font-weight:600; font-size:12px;">Impact {impact_score}</span>
            <span style="color:#64748b; font-size:12px;"> | {event_type}</span>
            <span style="color:#334155; font-size:13px;"> — {title[:100]}</span>
        </div>
        """

    st.markdown(card_html, unsafe_allow_html=True)
