"""Watchlist — Ticker Deep Dive with Confluence Scoring.

Shows confluence score grid for all tickers, then deep-dive with
thesis narrative and accordion evidence panels for the selected ticker.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import DB_PATH
from analysis.confluence import compute_confluence
from dashboard.components.color_system import (
    DIRECTION_COLORS,
    render_direction_badge,
    render_impact_dot,
)
from dashboard.components.confluence_card import render_confluence_card
from dashboard.components.event_card import render_event_with_context, render_so_what
from dashboard.components.glossary import (
    inject_tooltip_css,
    render_metric_with_tooltip,
    tooltip,
)
from dashboard.components.price_chart import render_price_chart


def _generate_thesis(ticker: str, conf: dict, company_info, conn: sqlite3.Connection):
    """Generate and render a template-based thesis narrative."""
    direction = conf.get("direction", "neutral")
    score = conf.get("score", 0)
    strength = conf.get("strength", "weak")
    factors = conf.get("factors", [])

    dir_color = DIRECTION_COLORS.get(direction, DIRECTION_COLORS["neutral"])
    company = company_info.get("company_name", ticker)

    # Build narrative from contributing factors
    supporting = [f for f in factors if f["contributing"]]
    missing = [f for f in factors if not f["contributing"]]

    if not supporting:
        narrative = f"{company} has weak confluence — insufficient evidence across data sources for a directional thesis."
    else:
        if strength == "strong":
            narrative = f"{company} has **strong multi-factor support** ({score}/7 confluence). "
        elif strength == "moderate":
            narrative = f"{company} has **moderate support** ({score}/7 confluence). "
        else:
            narrative = f"{company} has limited evidence ({score}/7 confluence). "

        details = []
        for f in supporting:
            details.append(f"{f['source']}: {f['signal']}")
        narrative += " ".join(details) + ". "

        if missing:
            gap_names = [f["source"] for f in missing[:3]]
            narrative += f"Gaps: {', '.join(gap_names)}."

    st.markdown(
        f"""
        <div style="background:{dir_color}08; border:1px solid {dir_color}33;
                    border-radius:8px; padding:16px; margin-bottom:16px;">
            <div style="font-size:16px; font-weight:bold; color:{dir_color}; margin-bottom:8px;">
                {ticker} Thesis: {direction.upper()} ({score}/7 confluence)
            </div>
            <div style="font-size:14px; color:#334155; line-height:1.6;">
                {narrative}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.title("Watchlist")
st.caption("Confluence-driven ticker deep dive across all data sources")
inject_tooltip_css()

from dashboard.components.freshness import render_freshness
render_freshness("market_data", "date", "Market Data")

conn = sqlite3.connect(DB_PATH)


@st.cache_data(ttl=300)
def _load_watchlist():
    c = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT ticker, company_name, sector, subsector, active FROM watchlist ORDER BY sector, ticker",
            c,
        )
    except Exception:
        df = pd.DataFrame()
    c.close()
    return df


@st.cache_data(ttl=300)
def _compute_all_confluence(tickers: tuple) -> dict:
    """Compute confluence for all tickers (cached)."""
    c = sqlite3.connect(DB_PATH)
    results = {}
    for t in tickers:
        try:
            results[t] = compute_confluence(t, c)
        except Exception:
            results[t] = {"ticker": t, "score": 0, "direction": "neutral", "factors": [], "strength": "weak"}
    c.close()
    return results


# ── Load Watchlist ────────────────────────────────────────────────────
watchlist_df = _load_watchlist()

if watchlist_df.empty:
    st.warning("No tickers in watchlist. Run setup to populate.")
    conn.close()
    st.stop()

active_df = watchlist_df[watchlist_df["active"] == 1]
if active_df.empty:
    st.warning("No active tickers in watchlist.")
    conn.close()
    st.stop()

# ── Section A: Confluence Score Grid ──────────────────────────────────
st.subheader("Confluence Scores")
st.caption(
    "How many independent data sources align on each ticker. "
    "Score of 4+ = strong confluence."
)

# Compute confluence for all active tickers
all_tickers = tuple(active_df["ticker"].tolist())
confluence_data = _compute_all_confluence(all_tickers)

# Sort by score descending
sorted_tickers = sorted(all_tickers, key=lambda t: confluence_data.get(t, {}).get("score", 0), reverse=True)

# Render grid (3 columns)
grid_cols = st.columns(3)
for i, ticker in enumerate(sorted_tickers):
    with grid_cols[i % 3]:
        row = active_df[active_df["ticker"] == ticker].iloc[0]
        render_confluence_card(
            confluence_data.get(ticker, {"ticker": ticker, "score": 0, "direction": "neutral", "factors": [], "strength": "weak"}),
            company_name=row.get("company_name", ""),
            sector=row.get("sector", ""),
        )

# ── Section B: Ticker Selector ────────────────────────────────────────
st.markdown("---")

# Pre-select highest confluence ticker
default_idx = 0
if sorted_tickers:
    default_ticker = sorted_tickers[0]
    ticker_list = active_df["ticker"].tolist()
    if default_ticker in ticker_list:
        default_idx = ticker_list.index(default_ticker)

selected_ticker = st.selectbox(
    "Select a ticker for deep dive",
    active_df["ticker"].tolist(),
    index=default_idx,
    key="watchlist_ticker",
)

if selected_ticker:
    company_info = active_df[active_df["ticker"] == selected_ticker].iloc[0]
    conf = confluence_data.get(selected_ticker, {})

    st.subheader(f"{selected_ticker} — {company_info['company_name']}")
    st.caption(f"{company_info['sector']} / {company_info['subsector']}")

    # ── Thesis Narrative ──────────────────────────────────────────────
    _generate_thesis(selected_ticker, conf, company_info, conn)

    end_date = date.today().isoformat()
    start_180 = (date.today() - timedelta(days=180)).isoformat()
    start_365 = (date.today() - timedelta(days=365)).isoformat()

    # ── Price & Technicals ────────────────────────────────────────────
    st.markdown("---")
    chart_ctrl = st.columns([1, 1, 1, 1])
    with chart_ctrl[0]:
        show_mas = st.checkbox("Moving Averages", value=True, key="wl_mas")
    with chart_ctrl[1]:
        show_volume = st.checkbox("Volume", value=True, key="wl_vol")
    with chart_ctrl[2]:
        benchmark = st.selectbox(
            "Benchmark",
            ["None", "SPY", "XBI", "XLF", "XLK", "XLE", "XLI"],
            key="wl_bench",
        )
    with chart_ctrl[3]:
        event_windows = st.checkbox("Event Windows", value=False, key="wl_ew")

    render_price_chart(
        selected_ticker, start_365, end_date,
        show_mas=show_mas,
        show_volume=show_volume,
        benchmark=benchmark if benchmark != "None" else None,
        event_windows=event_windows,
    )

    # ── Evidence Sections (Accordion) ─────────────────────────────────

    # 1. Regulatory Events
    try:
        reg_events = pd.read_sql_query(
            """SELECT publication_date, source, event_type, title, impact_score, agency, tickers
               FROM regulatory_events
               WHERE tickers LIKE ?
                 AND publication_date >= ?
               ORDER BY publication_date DESC
               LIMIT 10""",
            conn,
            params=(f"%{selected_ticker}%", start_180),
        )
    except Exception:
        reg_events = pd.DataFrame()

    reg_count = len(reg_events)
    high_impact_count = len(reg_events[reg_events["impact_score"] >= 4]) if not reg_events.empty else 0
    with st.expander(f"Regulatory Events ({reg_count} events, {high_impact_count} high-impact)"):
        if not reg_events.empty:
            for _, evt in reg_events.iterrows():
                render_event_with_context(evt.to_dict(), conn)
        else:
            st.info("No regulatory events for this ticker in the last 180 days.")

    # 2. FDA Events
    try:
        fda_events = pd.read_sql_query(
            """SELECT event_date, event_type, drug_name, outcome, abnormal_return
               FROM fda_events
               WHERE ticker = ?
                 AND event_date >= ?
               ORDER BY event_date DESC
               LIMIT 10""",
            conn,
            params=(selected_ticker, start_180),
        )
    except Exception:
        fda_events = pd.DataFrame()

    with st.expander(f"FDA Events ({len(fda_events)})"):
        if not fda_events.empty:
            display_fda = fda_events.copy()
            display_fda.columns = ["Date", "Event Type", "Drug", "Outcome", "AR"]
            display_fda["AR"] = display_fda["AR"].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
            st.dataframe(display_fda, use_container_width=True, hide_index=True)
        else:
            st.info("No FDA events for this ticker in the last 180 days.")

    # 3. Lobbying Activity
    try:
        lobbying_df = pd.read_sql_query(
            """SELECT filing_year, filing_period, SUM(amount) as total_amount,
                      GROUP_CONCAT(DISTINCT specific_issues, ' | ') as issues
               FROM lobbying_filings
               WHERE client_ticker = ?
               GROUP BY filing_year, filing_period
               ORDER BY filing_year DESC, filing_period DESC
               LIMIT 4""",
            conn,
            params=(selected_ticker,),
        )
    except Exception:
        lobbying_df = pd.DataFrame()

    with st.expander(f"Lobbying Activity ({len(lobbying_df)} periods)"):
        if not lobbying_df.empty:
            lobbying_df["Period"] = lobbying_df["filing_year"].astype(str) + " " + lobbying_df["filing_period"].fillna("")
            lobbying_df["Amount"] = lobbying_df["total_amount"].apply(
                lambda x: f"${x:,.0f}" if pd.notna(x) else ""
            )
            # Calculate QoQ change
            amounts = lobbying_df["total_amount"].tolist()
            qoq = [""]
            for i in range(1, len(amounts)):
                if pd.notna(amounts[i]) and pd.notna(amounts[i - 1]) and amounts[i - 1] > 0:
                    change = (amounts[i] - amounts[i - 1]) / amounts[i - 1]
                    qoq.append(f"{change:+.1%}")
                else:
                    qoq.append("")
            lobbying_df["QoQ Change"] = qoq
            lobbying_df["Issues"] = lobbying_df["issues"].apply(
                lambda x: x[:150] + "..." if isinstance(x, str) and len(x) > 150 else x
            )

            # Interpretation
            if len(amounts) >= 2 and amounts[0] and amounts[1] and amounts[1] > 0:
                qoq_pct = (amounts[0] - amounts[1]) / amounts[1]
                if qoq_pct > 0.25:
                    st.markdown(
                        f"**Lobbying spend surged {qoq_pct:+.0%} QoQ** — "
                        f"this often signals the company expects imminent regulation."
                    )
                elif qoq_pct < -0.25:
                    st.markdown(f"Lobbying spend declined {qoq_pct:+.0%} QoQ — reduced engagement.")

            st.dataframe(
                lobbying_df[["Period", "Amount", "QoQ Change", "Issues"]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No lobbying filings for this ticker.")

    # 4. Congressional Trades
    try:
        trades_df = pd.read_sql_query(
            """SELECT trade_date, politician, party, trade_type, amount_range
               FROM congress_trades
               WHERE ticker = ?
                 AND trade_date >= ?
               ORDER BY trade_date DESC
               LIMIT 10""",
            conn,
            params=(selected_ticker, start_180),
        )
    except Exception:
        trades_df = pd.DataFrame()

    with st.expander(f"Congressional Trades ({len(trades_df)})"):
        if not trades_df.empty:
            trades_df.columns = ["Date", "Politician", "Party", "Type", "Amount Range"]
            # Add party context color
            st.dataframe(trades_df, use_container_width=True, hide_index=True)
        else:
            st.info("No congressional trades for this ticker in the last 180 days.")

    # 5. Trading Signals
    try:
        signals_df = pd.read_sql_query(
            """SELECT signal_date, signal_type, direction, conviction, status, pnl_percent
               FROM trading_signals
               WHERE ticker = ?
               ORDER BY signal_date DESC
               LIMIT 10""",
            conn,
            params=(selected_ticker,),
        )
    except Exception:
        signals_df = pd.DataFrame()

    with st.expander(f"Trading Signals ({len(signals_df)})"):
        if not signals_df.empty:
            signals_df.columns = ["Date", "Signal Type", "Direction", "Conviction", "Status", "PnL %"]
            signals_df["PnL %"] = signals_df["PnL %"].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
            st.dataframe(signals_df, use_container_width=True, hide_index=True)
        else:
            st.info("No trading signals for this ticker yet.")

    # 6. Prediction Markets
    try:
        pred_df = pd.read_sql_query(
            """SELECT question_text, current_price, volume, resolution_date
               FROM prediction_markets
               WHERE related_ticker = ?
               ORDER BY volume DESC
               LIMIT 5""",
            conn,
            params=(selected_ticker,),
        )
    except Exception:
        pred_df = pd.DataFrame()

    if not pred_df.empty:
        with st.expander(f"Prediction Markets ({len(pred_df)} contracts)"):
            for _, row in pred_df.iterrows():
                prob = row["current_price"]
                st.markdown(
                    f"- **{prob:.1%}** — {row['question_text'][:80]} "
                    f"(${row['volume']:,.0f} volume)"
                )

conn.close()
