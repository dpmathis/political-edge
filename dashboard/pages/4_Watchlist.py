"""Watchlist — Combined per-ticker view across all data sources."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import DB_PATH
from dashboard.components.price_chart import render_price_chart

st.title("Watchlist")
st.caption("Combined per-ticker view across all data sources")

from dashboard.components.freshness import render_freshness
render_freshness("market_data", "date", "Market Data")

conn = sqlite3.connect(DB_PATH)


@st.cache_data(ttl=300)
def _load_watchlist():
    c = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT ticker, company_name, sector, subsector, active FROM watchlist ORDER BY sector, ticker",
        c,
    )
    c.close()
    return df


# --- WATCHLIST TABLE ---
watchlist_df = _load_watchlist()

if watchlist_df.empty:
    st.warning("No tickers in watchlist. Run setup to populate.")
    conn.close()
    st.stop()

# Show watchlist summary
st.subheader("Active Tickers")
active_df = watchlist_df[watchlist_df["active"] == 1]
st.dataframe(
    active_df[["ticker", "company_name", "sector", "subsector"]],
    column_config={
        "ticker": "Ticker",
        "company_name": "Company",
        "sector": "Sector",
        "subsector": "Subsector",
    },
    use_container_width=True,
    hide_index=True,
)

# --- TICKER SELECTOR ---
st.markdown("---")
selected_ticker = st.selectbox(
    "Select a ticker for combined view",
    active_df["ticker"].tolist(),
    key="watchlist_ticker",
)

if selected_ticker:
    company_info = active_df[active_df["ticker"] == selected_ticker].iloc[0]
    st.subheader(f"{selected_ticker} — {company_info['company_name']}")
    st.caption(f"{company_info['sector']} / {company_info['subsector']}")

    end_date = date.today().isoformat()
    start_180 = (date.today() - timedelta(days=180)).isoformat()
    start_365 = (date.today() - timedelta(days=365)).isoformat()

    # --- 1. PRICE CHART ---
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

    # --- 2. REGULATORY EVENTS ---
    st.markdown("---")
    st.subheader("Regulatory Events")
    reg_events = pd.read_sql_query(
        """SELECT publication_date, source, event_type, title, impact_score, agency
           FROM regulatory_events
           WHERE tickers LIKE ?
             AND publication_date >= ?
           ORDER BY publication_date DESC
           LIMIT 10""",
        conn,
        params=(f"%{selected_ticker}%", start_180),
    )
    if not reg_events.empty:
        reg_events.columns = ["Date", "Source", "Type", "Title", "Impact", "Agency"]
        reg_events["Title"] = reg_events["Title"].apply(
            lambda x: x[:100] + "..." if isinstance(x, str) and len(x) > 100 else x
        )
        st.dataframe(reg_events, use_container_width=True, hide_index=True)
    else:
        st.info("No regulatory events for this ticker in the last 180 days.")

    # --- 3. FDA EVENTS ---
    st.markdown("---")
    st.subheader("FDA Events")
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
    if not fda_events.empty:
        fda_events.columns = ["Date", "Event Type", "Drug", "Outcome", "AR"]
        fda_events["AR"] = fda_events["AR"].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
        st.dataframe(fda_events, use_container_width=True, hide_index=True)
    else:
        st.info("No FDA events for this ticker in the last 180 days.")

    # --- 4. LOBBYING ACTIVITY ---
    st.markdown("---")
    st.subheader("Lobbying Activity")
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
        # Reverse since data is desc
        lobbying_df["QoQ Change"] = qoq

        lobbying_df["Issues"] = lobbying_df["issues"].apply(
            lambda x: x[:150] + "..." if isinstance(x, str) and len(x) > 150 else x
        )
        st.dataframe(
            lobbying_df[["Period", "Amount", "QoQ Change", "Issues"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No lobbying filings for this ticker.")

    # --- 5. CONGRESSIONAL TRADES ---
    st.markdown("---")
    st.subheader("Congressional Trades")
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
    if not trades_df.empty:
        trades_df.columns = ["Date", "Politician", "Party", "Type", "Amount Range"]
        st.dataframe(trades_df, use_container_width=True, hide_index=True)
    else:
        st.info("No congressional trades for this ticker in the last 180 days.")

    # --- 6. ACTIVE SIGNALS (placeholder for Phase 5) ---
    st.markdown("---")
    st.subheader("Trading Signals")
    signals_df = pd.read_sql_query(
        """SELECT signal_date, signal_type, direction, conviction, status, pnl_percent
           FROM trading_signals
           WHERE ticker = ?
           ORDER BY signal_date DESC
           LIMIT 10""",
        conn,
        params=(selected_ticker,),
    )
    if not signals_df.empty:
        signals_df.columns = ["Date", "Signal Type", "Direction", "Conviction", "Status", "PnL %"]
        signals_df["PnL %"] = signals_df["PnL %"].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
        st.dataframe(signals_df, use_container_width=True, hide_index=True)
    else:
        st.info("No trading signals yet. Signals will appear after Phase 5 is built.")

conn.close()
