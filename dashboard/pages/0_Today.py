"""Today — Unified trading intelligence landing page.

Shows macro regime, top signals, upcoming catalysts, recent high-impact events,
and active EO signals in a single actionable view.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH

st.title("Today")
st.caption("What should you trade today?")

conn = sqlite3.connect(DB_PATH)
today = date.today().isoformat()

# ── Macro Regime Card ────────────────────────────────────────────────
st.markdown("---")

try:
    regime_row = conn.execute(
        """SELECT quadrant, quadrant_label, growth_roc, inflation_roc, vix,
                  yield_curve_spread, confidence, position_size_modifier
           FROM macro_regimes ORDER BY date DESC LIMIT 1"""
    ).fetchone()
except Exception:
    regime_row = None

if regime_row:
    quadrant, label, growth, inflation, vix, yc, confidence, modifier = regime_row

    from analysis.macro_regime import QUADRANTS
    regime_info = QUADRANTS.get(quadrant, {})

    regime_cols = st.columns([1, 1, 1, 1])
    with regime_cols[0]:
        st.metric("Macro Regime", f"Q{quadrant} {label}")
    with regime_cols[1]:
        st.metric("Confidence", confidence.upper() if confidence else "N/A")
    with regime_cols[2]:
        st.metric("Position Modifier", f"{modifier:.1f}x")
    with regime_cols[3]:
        st.metric("VIX", f"{vix:.1f}" if vix else "N/A")

    detail_cols = st.columns(2)
    with detail_cols[0]:
        favored = regime_info.get("favored_sectors", [])
        st.markdown(f"**Favored Sectors:** {', '.join(favored)}" if favored else "")
    with detail_cols[1]:
        avoid = regime_info.get("avoid_sectors", [])
        st.markdown(f"**Avoid Sectors:** {', '.join(avoid)}" if avoid else "")
else:
    st.info("No macro regime data. Run FRED collector and classify regime.")

# ── Active Signals ────────────────────────────────────────────────
st.markdown("---")
st.subheader("Active Signals")

try:
    signals = pd.read_sql_query(
        """SELECT signal_date, ticker, signal_type, direction, conviction, rationale,
                  position_size_modifier, status
           FROM trading_signals
           WHERE status IN ('pending', 'active')
           ORDER BY
               CASE conviction WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
               signal_date DESC
           LIMIT 10""",
        conn,
    )
except Exception:
    signals = pd.DataFrame()

if not signals.empty:
    for _, sig in signals.iterrows():
        dir_label = "LONG" if sig["direction"] == "long" else "SHORT" if sig["direction"] == "short" else "WATCH"

        sig_cols = st.columns([1, 1, 1, 4])
        with sig_cols[0]:
            st.markdown(f"**{sig['ticker']}** {dir_label}")
        with sig_cols[1]:
            st.markdown(f"{sig['conviction'].upper()} | {sig['signal_type']}")
        with sig_cols[2]:
            st.markdown(f"{sig['signal_date']}")
        with sig_cols[3]:
            st.markdown(f"{sig['rationale'][:120] if sig['rationale'] else ''}")
else:
    st.info("No active signals. Run signal generation from the Settings page.")

# ── Upcoming Catalysts (next 7 days) ─────────────────────────────
st.markdown("---")
st.subheader("Upcoming Catalysts (Next 7 Days)")

next_week = (date.today() + timedelta(days=7)).isoformat()

catalyst_cols = st.columns(3)

# FDA catalysts
with catalyst_cols[0]:
    st.markdown("**FDA Events**")
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
            st.markdown(f"- **{row['event_date']}** {row['event_type']}: {drug}{ticker}")
    else:
        st.caption("No FDA catalysts this week.")

# FOMC events
with catalyst_cols[1]:
    st.markdown("**FOMC Events**")
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
            st.markdown(f"- **{row['event_date']}** {title[:60]}")
    else:
        st.caption("No FOMC events this week.")

# Comment deadlines (from regulatory events)
with catalyst_cols[2]:
    st.markdown("**Comment Deadlines**")
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

# ── Recent High-Impact Events (last 48 hours) ─────────────────────
st.markdown("---")
st.subheader("Recent High-Impact Events (Last 48 Hours)")

two_days_ago = (date.today() - timedelta(days=2)).isoformat()

try:
    recent_events = pd.read_sql_query(
        """SELECT publication_date, source, event_type, title, impact_score, tickers, agency
           FROM regulatory_events
           WHERE impact_score >= 4
             AND publication_date >= ?
           ORDER BY impact_score DESC, publication_date DESC
           LIMIT 10""",
        conn,
        params=(two_days_ago,),
    )
except Exception:
    recent_events = pd.DataFrame()

if not recent_events.empty:
    for _, evt in recent_events.iterrows():
        impact_color = "red" if evt["impact_score"] >= 5 else "orange"
        tickers = f" | {evt['tickers']}" if evt["tickers"] else ""
        st.markdown(
            f"- :{impact_color}[Impact {evt['impact_score']}] **{evt['event_type']}** "
            f"({evt['agency'][:30] if evt['agency'] else evt['source']}){tickers} — "
            f"{evt['title'][:100]}"
        )
else:
    st.info("No high-impact events in the last 48 hours.")

# ── Prediction Market Sentiment ────────────────────────────────
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
    # Show FOMC rate probabilities first if available
    fomc_markets = pred_markets[pred_markets["category"] == "fomc"]
    rate_markets = fomc_markets[fomc_markets["question_text"].str.contains("interest rate", case=False, na=False)]

    if not rate_markets.empty:
        st.markdown("**FOMC Rate Decision Probabilities**")
        rate_cols = st.columns(min(len(rate_markets), 4))
        for i, (_, row) in enumerate(rate_markets.head(4).iterrows()):
            with rate_cols[i]:
                # Extract short label from question
                q = row["question_text"]
                if "no change" in q.lower():
                    label = "Hold"
                elif "decrease" in q.lower() and "50" in q:
                    label = "Cut 50bp"
                elif "decrease" in q.lower() and "25" in q:
                    label = "Cut 25bp"
                elif "increase" in q.lower():
                    label = "Hike 25bp"
                else:
                    label = q[:20]
                st.metric(label, f"{row['current_price']:.1%}")

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

# ── EO Signals Summary ─────────────────────────────────────────
st.markdown("---")
st.subheader("Executive Order Signals")

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
    st.caption("No EO-based signals yet. Signals are generated during data collection.")

# ── Quick Stats ─────────────────────────────────────────────────
st.markdown("---")
st.subheader("Data Summary")

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
