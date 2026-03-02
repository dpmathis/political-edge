"""Today — Your Daily Trading Briefing.

Narrative-first landing page: briefing banner, signal cards,
macro context, upcoming catalysts, and high-impact events.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import DB_PATH
from dashboard.components.briefing import render_briefing
from dashboard.components.color_system import (
    DIRECTION_COLORS,
    REGIME_COLORS,
    hex_to_rgba,
    render_direction_badge,
)
from dashboard.components.event_card import render_event_with_context
from dashboard.components.glossary import (
    inject_tooltip_css,
    render_glossary_term,
    render_metric_with_tooltip,
    tooltip,
)
from dashboard.components.signal_card import render_signal_card

st.title("Today")
st.caption("What should you trade today?")
inject_tooltip_css()

conn = sqlite3.connect(DB_PATH)
today = date.today().isoformat()

# ── Section A: Daily Briefing Banner ──────────────────────────────────
render_briefing(conn)

# ── Section B: Macro Regime Context Card ──────────────────────────────

try:
    regime_row = conn.execute(
        """SELECT quadrant, quadrant_label, growth_roc, inflation_roc, vix,
                  yield_curve_spread, confidence, position_size_modifier, date
           FROM macro_regimes ORDER BY date DESC LIMIT 1"""
    ).fetchone()
except Exception:
    regime_row = None

if regime_row:
    quadrant, label, growth, inflation, vix, yc, confidence, modifier, regime_date = regime_row

    from analysis.macro_regime import QUADRANTS
    regime_info = QUADRANTS.get(quadrant, {})
    color = REGIME_COLORS.get(quadrant, "#94a3b8")

    # Regime card with "what this means" narrative
    favored = regime_info.get("favored_sectors", [])
    avoid = regime_info.get("avoid_sectors", [])
    bias = regime_info.get("equity_bias", "neutral").replace("_", " ").title()

    # Map ETF tickers to plain-English sector names
    etf_names = {
        "XLK": "Technology", "XLY": "Consumer Discretionary", "XLE": "Energy",
        "XLB": "Materials", "XLF": "Financials", "XLI": "Industrials",
        "XLP": "Consumer Staples", "XLU": "Utilities", "XLV": "Healthcare",
    }
    favored_names = [f"{etf_names.get(s, s)} ({s})" for s in favored]
    avoid_names = [f"{etf_names.get(s, s)} ({s})" for s in avoid]

    st.markdown(
        f"""
        <div style="background:{hex_to_rgba(color, 0.08)}; border:2px solid {color};
                    border-radius:12px; padding:20px; margin-bottom:16px;">
            <div style="display:flex; align-items:center; gap:16px; margin-bottom:12px;">
                <div style="font-size:48px; font-weight:bold; color:{color};">Q{quadrant}</div>
                <div>
                    <div style="font-size:24px; font-weight:bold;">{label}</div>
                    <div style="font-size:14px; color:#64748b;">
                        Growth {'accelerating' if quadrant in (1, 2) else 'decelerating'},
                        Inflation {'accelerating' if quadrant in (2, 3) else 'decelerating'}
                    </div>
                </div>
            </div>
            <div style="font-size:14px; color:#334155; line-height:1.6; margin-bottom:12px;">
                <b>What this means:</b> {bias} bias — lean into
                {', '.join(favored_names) if favored_names else 'N/A'}.
                Reduce exposure to {', '.join(avoid_names) if avoid_names else 'N/A'}.
                Position sizes scaled to <b>{modifier:.1f}x</b> normal.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Key metrics row with tooltips
    metric_cols = st.columns(4)
    with metric_cols[0]:
        render_metric_with_tooltip("Confidence", (confidence or "N/A").upper(), "Conviction")
    with metric_cols[1]:
        render_metric_with_tooltip("Position Modifier", f"{modifier:.1f}x", "Position Size Modifier")
    with metric_cols[2]:
        render_metric_with_tooltip("VIX", f"{vix:.1f}" if vix else "N/A", "VIX")
    with metric_cols[3]:
        render_metric_with_tooltip("10Y-2Y Spread", f"{yc:.2f}%" if yc else "N/A", "Yield Curve Spread")

    # Regime transition (if previous regime exists)
    try:
        prev_regime = conn.execute(
            """SELECT quadrant, quadrant_label, date FROM macro_regimes
               ORDER BY date DESC LIMIT 1 OFFSET 1"""
        ).fetchone()
        if prev_regime and prev_regime[0] != quadrant:
            st.caption(
                f"Shifted from Q{prev_regime[0]} {prev_regime[1]} → Q{quadrant} {label} "
                f"(since {regime_date})"
            )
    except Exception:
        pass

else:
    st.info("No macro regime data. Run FRED collector and classify regime from Settings.")

# ── Section C: Active Signal Cards ────────────────────────────────────
st.markdown("---")
st.subheader("Active Signals")

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
        render_signal_card(sig.to_dict(), show_evidence=True, conn=conn)

    # Show remaining signals count
    try:
        total_active = conn.execute(
            "SELECT COUNT(*) FROM trading_signals WHERE status IN ('pending', 'active')"
        ).fetchone()[0]
        if total_active > 5:
            st.caption(f"Showing top 5 of {total_active} active signals. See the Signals page for all.")
    except Exception:
        pass
else:
    st.info("No active signals. Run signal generation from the Settings page.")

# ── Section D: Upcoming Catalysts (next 7 days) ──────────────────────
st.markdown("---")
st.subheader("Upcoming Catalysts (Next 7 Days)")

next_week = (date.today() + timedelta(days=7)).isoformat()

catalyst_cols = st.columns(3)

# FDA catalysts
with catalyst_cols[0]:
    st.markdown(f"**{render_glossary_term('PDUFA Date', 'FDA Events')}**", unsafe_allow_html=True)
    try:
        fda_upcoming = pd.read_sql_query(
            """SELECT event_date, event_type, drug_name, company_name, ticker
               FROM fda_events
               WHERE event_date >= ? AND event_date <= ?
                 AND event_type IN ('adcom_vote', 'pdufa_date', 'approval')
               ORDER BY event_date
               LIMIT 5""",
            conn,
            params=(today, next_week),
        )
    except Exception:
        fda_upcoming = pd.DataFrame()
    if not fda_upcoming.empty:
        for _, row in fda_upcoming.iterrows():
            drug = row["drug_name"] or "N/A"
            ticker = f" ({row['ticker']})" if row["ticker"] else ""
            days_until = (date.fromisoformat(row["event_date"]) - date.today()).days
            st.markdown(
                f"- **{row['event_date']}** ({days_until}d) — "
                f"{row['event_type'].replace('_', ' ')}: {drug}{ticker}"
            )
    else:
        st.caption("No FDA catalysts this week.")

# FOMC events
with catalyst_cols[1]:
    st.markdown(f"**{render_glossary_term('FOMC Drift', 'FOMC Events')}**", unsafe_allow_html=True)
    try:
        fomc_upcoming = pd.read_sql_query(
            """SELECT event_date, event_type, title, rate_decision
               FROM fomc_events
               WHERE event_date >= ? AND event_date <= ?
               ORDER BY event_date
               LIMIT 5""",
            conn,
            params=(today, next_week),
        )
    except Exception:
        fomc_upcoming = pd.DataFrame()
    if not fomc_upcoming.empty:
        for _, row in fomc_upcoming.iterrows():
            title = row["title"] or row["event_type"]
            days_until = (date.fromisoformat(row["event_date"]) - date.today()).days
            st.markdown(f"- **{row['event_date']}** ({days_until}d) — {title[:60]}")
    else:
        st.caption("No FOMC events this week.")

# Comment deadlines
with catalyst_cols[2]:
    st.markdown("**Regulatory Deadlines**")
    try:
        deadlines = pd.read_sql_query(
            """SELECT publication_date, title, agency
               FROM regulatory_events
               WHERE event_type IN ('proposed_rule', 'notice')
                 AND impact_score >= 3
                 AND publication_date >= ? AND publication_date <= ?
               ORDER BY publication_date
               LIMIT 5""",
            conn,
            params=(today, next_week),
        )
    except Exception:
        deadlines = pd.DataFrame()
    if not deadlines.empty:
        for _, row in deadlines.iterrows():
            st.markdown(f"- **{row['publication_date']}** {row['title'][:60]}")
    else:
        st.caption("No comment deadlines this week.")

# ── Section E: Prediction Market Sentiment ────────────────────────────
st.markdown("---")
st.subheader("Prediction Market Sentiment")

try:
    pred_markets = pd.read_sql_query(
        """SELECT question_text, current_price, volume, category, resolution_date, related_ticker
           FROM prediction_markets
           WHERE current_price IS NOT NULL
           ORDER BY volume DESC
           LIMIT 12""",
        conn,
    )
except Exception:
    pred_markets = pd.DataFrame()

if not pred_markets.empty:
    # FOMC rate probabilities with plain-English labels
    fomc_markets = pred_markets[pred_markets["category"] == "fomc"]
    rate_markets = fomc_markets[fomc_markets["question_text"].str.contains("interest rate", case=False, na=False)]

    if not rate_markets.empty:
        st.markdown("**FOMC Rate Decision Probabilities**")
        rate_cols = st.columns(min(len(rate_markets), 4))
        for i, (_, row) in enumerate(rate_markets.head(4).iterrows()):
            with rate_cols[i]:
                q = row["question_text"]
                prob = row["current_price"]
                if "no change" in q.lower():
                    label = "Hold"
                    if prob > 0.8:
                        context = "Almost certain — limited alpha from rate move"
                    elif prob > 0.6:
                        context = "Likely — some uncertainty remains"
                    else:
                        context = "Uncertain — watch for surprises"
                elif "decrease" in q.lower() and "50" in q:
                    label = "Cut 50bp"
                    context = "Aggressive easing signal" if prob > 0.3 else "Unlikely scenario"
                elif "decrease" in q.lower() and "25" in q:
                    label = "Cut 25bp"
                    context = "Moderate easing expected" if prob > 0.3 else "Low probability"
                elif "increase" in q.lower():
                    label = "Hike 25bp"
                    context = "Tightening signal" if prob > 0.3 else "Very unlikely"
                else:
                    label = q[:20]
                    context = ""
                st.metric(label, f"{prob:.1%}")
                if context:
                    st.caption(context)

    # Other notable markets
    other_markets = pred_markets[~pred_markets.index.isin(rate_markets.index)].head(6)
    if not other_markets.empty:
        st.markdown("**Other Notable Markets**")
        for _, row in other_markets.iterrows():
            ticker_str = f" [{row['related_ticker']}]" if row["related_ticker"] else ""
            st.markdown(
                f"- **{row['current_price']:.1%}** — {row['question_text'][:80]}{ticker_str} "
                f"(${row['volume']:,.0f} volume)"
            )
else:
    st.info("No prediction market data. Run the Polymarket collector from Settings.")

# ── Section F: Recent High-Impact Events ──────────────────────────────
st.markdown("---")
st.subheader("Recent High-Impact Events (Last 48 Hours)")

two_days_ago = (date.today() - timedelta(days=2)).isoformat()

try:
    recent_events = pd.read_sql_query(
        """SELECT publication_date, source, event_type, title, impact_score, tickers, agency
           FROM regulatory_events
           WHERE impact_score >= 3
             AND publication_date >= ?
           ORDER BY impact_score DESC, publication_date DESC
           LIMIT 10""",
        conn,
        params=(two_days_ago,),
    )
except Exception:
    recent_events = pd.DataFrame()

if not recent_events.empty:
    # Severity-tiered rendering
    high_impact = recent_events[recent_events["impact_score"] >= 4]
    moderate_impact = recent_events[recent_events["impact_score"] < 4]

    for _, evt in high_impact.iterrows():
        render_event_with_context(evt.to_dict(), conn)

    if not moderate_impact.empty:
        with st.expander(f"Moderate impact events ({len(moderate_impact)})"):
            for _, evt in moderate_impact.iterrows():
                render_event_with_context(evt.to_dict(), conn)
else:
    st.info("No high-impact events in the last 48 hours.")

# ── Section G: EO Signals + Data Summary (Progressive Disclosure) ────
with st.expander("Executive Order Signals"):
    try:
        eo_signals = pd.read_sql_query(
            """SELECT signal_date, ticker, signal_type, direction, conviction, rationale
               FROM trading_signals
               WHERE signal_type LIKE 'eo_%' OR signal_type = 'reg_shock'
               ORDER BY signal_date DESC
               LIMIT 5""",
            conn,
        )
    except Exception:
        eo_signals = pd.DataFrame()

    if not eo_signals.empty:
        st.dataframe(
            eo_signals.rename(columns={
                "signal_date": "Date",
                "ticker": "Ticker",
                "signal_type": "Type",
                "direction": "Direction",
                "conviction": "Conviction",
                "rationale": "Rationale",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No EO-based signals yet.")

with st.expander("Data Summary"):
    stats = {}
    for label, table in [
        ("Regulatory Events", "regulatory_events"),
        ("FDA Events", "fda_events"),
        ("Trading Signals", "trading_signals"),
        ("Congress Trades", "congress_trades"),
        ("Lobbying Filings", "lobbying_filings"),
        ("Market Data", "market_data"),
    ]:
        try:
            stats[label] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            stats[label] = 0

    if stats:
        stat_cols = st.columns(len(stats))
        for i, (label, value) in enumerate(stats.items()):
            with stat_cols[i]:
                st.metric(label, f"{value:,}")

conn.close()
