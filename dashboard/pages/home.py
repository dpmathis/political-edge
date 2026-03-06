"""Home — Guided Dashboard Landing Page."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3

import pandas as pd
import streamlit as st

from config import DB_PATH
from dashboard.components.color_system import REGIME_COLORS, hex_to_rgba
from dashboard.components.glossary import inject_tooltip_css, render_glossary_term
from dashboard.components.signal_card import render_signal_card

inject_tooltip_css()

# ── Value Proposition Banner ──────────────────────────────────────────
st.markdown(
    """
    <div style="background:linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
                border-radius:16px; padding:32px 36px; margin-bottom:24px;
                border:1px solid #334155;">
        <h1 style="color:#f1f5f9; font-size:28px; margin:0 0 8px 0;">
            Track How Washington Moves Markets
        </h1>
        <p style="color:#94a3b8; font-size:16px; margin:0; line-height:1.5;">
            Political Edge monitors 12 government data sources and translates
            regulatory events into actionable trading signals.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

conn = sqlite3.connect(DB_PATH)

# ── Market Conditions Card ────────────────────────────────────────────
try:
    regime_row = conn.execute(
        """SELECT quadrant, quadrant_label, confidence, position_size_modifier
           FROM macro_regimes ORDER BY date DESC LIMIT 1"""
    ).fetchone()
except Exception:
    regime_row = None

_REGIME_OUTLOOK = {
    1: ("bullish", "growth is accelerating while inflation cools — historically good for tech and consumer discretionary stocks"),
    2: ("cautiously bullish", "both growth and inflation are accelerating — favor energy, materials, and financials"),
    3: ("defensive", "growth is slowing while inflation rises — lean toward energy and consumer staples"),
    4: ("risk-off", "both growth and inflation are decelerating — favor utilities and defensive sectors"),
}

if regime_row:
    quadrant, label, confidence, modifier = regime_row
    color = REGIME_COLORS.get(quadrant, "#94a3b8")
    outlook, narrative = _REGIME_OUTLOOK.get(quadrant, ("neutral", "macro conditions are mixed"))

    st.markdown(
        f"""
        <div style="background:{hex_to_rgba(color, 0.08)}; border:2px solid {color};
                    border-radius:12px; padding:20px 24px; margin-bottom:20px;">
            <div style="font-size:13px; text-transform:uppercase; letter-spacing:1px;
                        color:{color}; font-weight:600; margin-bottom:8px;">
                Market Conditions
            </div>
            <div style="font-size:17px; line-height:1.6;">
                The macro environment is <b style="color:{color};">{outlook}</b> — {narrative}.
            </div>
            <div style="font-size:13px; color:#64748b; margin-top:8px;">
                {render_glossary_term("Macro Regime", f"Regime: Q{quadrant} {label}")} &middot;
                Confidence: {(confidence or 'N/A').upper()} &middot;
                {render_glossary_term("Position Size Modifier", f"Position sizing: {modifier:.1f}x normal")}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ── What's Happening Now — Top Signals ────────────────────────────────
st.subheader("What's Happening Now")

try:
    signals = pd.read_sql_query(
        """SELECT signal_date, ticker, signal_type, direction, conviction, rationale,
                  position_size_modifier, status, entry_price, stop_loss_price,
                  take_profit_price, time_horizon_days, expected_car,
                  historical_win_rate, historical_p_value, historical_n_events
           FROM trading_signals
           WHERE status IN ('pending', 'active')
           ORDER BY
               CASE conviction WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
               signal_date DESC
           LIMIT 5""",
        conn,
    )
except Exception:
    signals = pd.DataFrame()

if not signals.empty:
    for _, sig in signals.iterrows():
        render_signal_card(sig.to_dict(), show_evidence=False, conn=conn)

    try:
        total_active = conn.execute(
            "SELECT COUNT(*) FROM trading_signals WHERE status IN ('pending', 'active')"
        ).fetchone()[0]
        if total_active > 5:
            st.caption(f"Showing top 5 of {total_active} active signals.")
    except Exception:
        pass
else:
    st.info(
        "No active trading signals yet. Go to **Settings** to run data collection, "
        "then generate signals from the **Signals** page."
    )

# ── Quick Links ───────────────────────────────────────────────────────
st.markdown("---")

from pathlib import Path

_PAGES = Path(__file__).parent

link_cols = st.columns(3)
with link_cols[0]:
    st.page_link(str(_PAGES / "0_Today.py"), label="Today's Briefing", icon="📊")
    st.caption("Full daily briefing with catalysts, events, and macro context.")
with link_cols[1]:
    st.page_link(str(_PAGES / "4_Watchlist.py"), label="Watchlist", icon="👁️")
    st.caption("Deep-dive on your tracked tickers with confluence scoring.")
with link_cols[2]:
    st.page_link(str(_PAGES / "6_Signals.py"), label="All Signals", icon="⚡")
    st.caption("Complete list of trading signals with performance analytics.")

conn.close()
