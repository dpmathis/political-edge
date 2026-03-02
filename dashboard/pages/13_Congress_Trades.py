"""Congress Trades — Congressional stock trading disclosures with party and ticker analysis."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from config import DB_PATH
from dashboard.components.glossary import inject_tooltip_css

st.set_page_config(page_title="Congress Trades", layout="wide")
st.title("Congress Trades")
st.caption("Congressional stock trading disclosures with party, ticker, and disclosure delay analysis")
inject_tooltip_css()

from dashboard.components.freshness import render_freshness

render_freshness("congress_trades", "trade_date")

PARTY_COLORS = {"D": "#2563eb", "R": "#dc2626", "I": "#6b7280"}


# --- Cached Query Functions ---


@st.cache_data(ttl=300)
def _load_summary():
    """Return total trades, unique politicians, buy count, sell count, avg disclosure delay."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """SELECT COUNT(*) as total,
                      COUNT(DISTINCT politician) as unique_politicians,
                      COUNT(CASE WHEN LOWER(trade_type) = 'purchase' THEN 1 END) as buys,
                      COUNT(CASE WHEN LOWER(trade_type) = 'sale' THEN 1 END) as sells,
                      AVG(JULIANDAY(disclosure_date) - JULIANDAY(trade_date)) as avg_delay
               FROM congress_trades"""
        ).fetchone()
    except Exception:
        row = None
    conn.close()
    return row


@st.cache_data(ttl=300)
def _load_recent_trades(days=30):
    conn = sqlite3.connect(DB_PATH)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        df = pd.read_sql_query(
            """SELECT politician, party, chamber, ticker, trade_type,
                      amount_range, trade_date, disclosure_date, asset_description
               FROM congress_trades
               WHERE trade_date >= ?
               ORDER BY disclosure_date DESC
               LIMIT 50""",
            conn,
            params=(cutoff,),
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_top_politicians(limit=10):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT politician, party, COUNT(*) as trade_count
               FROM congress_trades
               GROUP BY politician, party
               ORDER BY trade_count DESC
               LIMIT ?""",
            conn,
            params=(limit,),
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_trades_over_time(days=90):
    conn = sqlite3.connect(DB_PATH)
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        df = pd.read_sql_query(
            """SELECT trade_date, COUNT(*) as trade_count
               FROM congress_trades
               WHERE trade_date >= ?
               GROUP BY trade_date
               ORDER BY trade_date""",
            conn,
            params=(cutoff,),
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_party_breakdown():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT party, COUNT(*) as trade_count
               FROM congress_trades
               WHERE party IS NOT NULL
               GROUP BY party
               ORDER BY trade_count DESC""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_chamber_breakdown():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT chamber, COUNT(*) as trade_count
               FROM congress_trades
               WHERE chamber IS NOT NULL
               GROUP BY chamber
               ORDER BY trade_count DESC""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_top_tickers(limit=10):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT ticker, COUNT(*) as trade_count
               FROM congress_trades
               GROUP BY ticker
               ORDER BY trade_count DESC
               LIMIT ?""",
            conn,
            params=(limit,),
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_watchlist_trades():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT ct.politician, ct.party, ct.ticker, w.company_name, w.sector,
                      ct.trade_type, ct.amount_range, ct.trade_date, ct.disclosure_date
               FROM congress_trades ct
               JOIN watchlist w ON ct.ticker = w.ticker
               ORDER BY ct.trade_date DESC
               LIMIT 50""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_filtered_trades(parties, trade_types, date_start, date_end):
    conn = sqlite3.connect(DB_PATH)
    try:
        conditions = ["1=1"]
        params = []
        if parties:
            placeholders = ",".join("?" for _ in parties)
            conditions.append(f"party IN ({placeholders})")
            params.extend(parties)
        if trade_types:
            placeholders = ",".join("?" for _ in trade_types)
            conditions.append(f"LOWER(trade_type) IN ({placeholders})")
            params.extend([t.lower() for t in trade_types])
        if date_start:
            conditions.append("trade_date >= ?")
            params.append(date_start.isoformat())
        if date_end:
            conditions.append("trade_date <= ?")
            params.append(date_end.isoformat())
        where = " AND ".join(conditions)
        df = pd.read_sql_query(
            f"""SELECT politician, party, chamber, ticker, trade_type,
                       amount_range, trade_date, disclosure_date, asset_description
                FROM congress_trades
                WHERE {where}
                ORDER BY trade_date DESC
                LIMIT 200""",
            conn,
            params=params,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


# --- Empty State Check ---

summary = _load_summary()

if summary is None or summary[0] == 0:
    st.info("No congressional trade data yet. Run the congress trades collector.")
    st.stop()

total_trades, unique_politicians, buy_count, sell_count, avg_delay = summary

# --- KPI Row ---

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric("Total Trades", f"{total_trades:,}")
with kpi_cols[1]:
    st.metric("Unique Politicians", f"{unique_politicians:,}")
with kpi_cols[2]:
    st.metric("Buy / Sell Split", f"{buy_count:,} buys / {sell_count:,} sells")
with kpi_cols[3]:
    delay_str = f"{avg_delay:.0f} days" if avg_delay and not pd.isna(avg_delay) else "N/A"
    st.metric("Avg Disclosure Delay", delay_str)

# --- Recent Trades Table ---
st.markdown("---")
st.subheader("Recent Trades (Last 30 Days)")

recent_df = _load_recent_trades(days=30)

if not recent_df.empty:
    display_df = recent_df.copy()
    display_df["asset_description"] = display_df["asset_description"].apply(
        lambda x: x[:80] + "..." if isinstance(x, str) and len(x) > 80 else x
    )
    st.dataframe(
        display_df,
        column_config={
            "politician": "Politician",
            "party": "Party",
            "chamber": "Chamber",
            "ticker": "Ticker",
            "trade_type": "Type",
            "amount_range": "Amount Range",
            "trade_date": "Trade Date",
            "disclosure_date": "Disclosed",
            "asset_description": "Asset",
        },
        use_container_width=True,
        hide_index=True,
        height=400,
    )
else:
    st.info("No trades in the last 30 days.")

# --- Top Politicians & Trades Over Time ---
st.markdown("---")

col_politicians, col_time = st.columns(2)

with col_politicians:
    st.subheader("Top 10 Politicians by Trade Count")
    top_pol_df = _load_top_politicians(limit=10)
    if not top_pol_df.empty:
        fig = px.bar(
            top_pol_df,
            x="trade_count",
            y="politician",
            orientation="h",
            color="party",
            color_discrete_map=PARTY_COLORS,
        )
        fig.update_layout(
            height=400,
            margin=dict(l=0, r=0, t=30, b=0),
            xaxis_title="Number of Trades",
            yaxis_title="",
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No politician data available.")

with col_time:
    st.subheader("Trades Over Time (90 Days)")
    time_df = _load_trades_over_time(days=90)
    if not time_df.empty:
        fig = px.bar(
            time_df,
            x="trade_date",
            y="trade_count",
            color="trade_count",
            color_continuous_scale="Blues",
        )
        fig.update_layout(
            height=400,
            margin=dict(l=0, r=0, t=30, b=0),
            xaxis_title="Date",
            yaxis_title="Number of Trades",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No trade data in the last 90 days.")

# --- Party Breakdown ---
st.markdown("---")

col_party, col_chamber = st.columns(2)

with col_party:
    st.subheader("Trades by Party")
    party_df = _load_party_breakdown()
    if not party_df.empty:
        fig = px.pie(
            party_df,
            values="trade_count",
            names="party",
            color="party",
            color_discrete_map=PARTY_COLORS,
        )
        fig.update_layout(
            height=350,
            margin=dict(l=0, r=0, t=30, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No party data available.")

with col_chamber:
    st.subheader("Trades by Chamber")
    chamber_df = _load_chamber_breakdown()
    if not chamber_df.empty:
        fig = px.bar(
            chamber_df,
            x="chamber",
            y="trade_count",
            color="trade_count",
            color_continuous_scale="Blues",
        )
        fig.update_layout(
            height=350,
            margin=dict(l=0, r=0, t=30, b=0),
            xaxis_title="",
            yaxis_title="Number of Trades",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No chamber data available.")

# --- Ticker Analysis ---
st.markdown("---")
st.subheader("Top 10 Most-Traded Tickers")

ticker_df = _load_top_tickers(limit=10)

if not ticker_df.empty:
    fig = px.bar(
        ticker_df,
        x="trade_count",
        y="ticker",
        orientation="h",
        color="trade_count",
        color_continuous_scale="Blues",
    )
    fig.update_layout(
        height=400,
        margin=dict(l=0, r=0, t=30, b=0),
        xaxis_title="Number of Trades",
        yaxis_title="",
        yaxis=dict(autorange="reversed"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No ticker data available.")

# --- Watchlist-Linked Trades ---
st.markdown("---")

watchlist_df = _load_watchlist_trades()

if not watchlist_df.empty:
    with st.expander(f"Watchlist-Linked Trades ({len(watchlist_df)})"):
        st.dataframe(
            watchlist_df,
            column_config={
                "politician": "Politician",
                "party": "Party",
                "ticker": "Ticker",
                "company_name": "Company",
                "sector": "Sector",
                "trade_type": "Type",
                "amount_range": "Amount Range",
                "trade_date": "Trade Date",
                "disclosure_date": "Disclosed",
            },
            use_container_width=True,
            hide_index=True,
            height=400,
        )
else:
    st.info("No watchlist-linked congressional trades found.")

# --- Sidebar Filters ---
st.sidebar.markdown("### Filters")

filter_parties = st.sidebar.multiselect(
    "Party",
    options=["D", "R", "I"],
    default=[],
    key="congress_party_filter",
)

filter_trade_types = st.sidebar.multiselect(
    "Trade Type",
    options=["purchase", "sale"],
    default=[],
    key="congress_trade_type_filter",
)

filter_date_start = st.sidebar.date_input(
    "Start Date",
    value=date.today() - timedelta(days=365),
    key="congress_date_start",
)

filter_date_end = st.sidebar.date_input(
    "End Date",
    value=date.today(),
    key="congress_date_end",
)

if filter_parties or filter_trade_types:
    st.markdown("---")
    st.subheader("Filtered Trades")
    filtered_df = _load_filtered_trades(
        tuple(filter_parties) if filter_parties else (),
        tuple(filter_trade_types) if filter_trade_types else (),
        filter_date_start,
        filter_date_end,
    )
    if not filtered_df.empty:
        display_filtered = filtered_df.copy()
        display_filtered["asset_description"] = display_filtered["asset_description"].apply(
            lambda x: x[:80] + "..." if isinstance(x, str) and len(x) > 80 else x
        )
        st.dataframe(
            display_filtered,
            column_config={
                "politician": "Politician",
                "party": "Party",
                "chamber": "Chamber",
                "ticker": "Ticker",
                "trade_type": "Type",
                "amount_range": "Amount Range",
                "trade_date": "Trade Date",
                "disclosure_date": "Disclosed",
                "asset_description": "Asset",
            },
            use_container_width=True,
            hide_index=True,
            height=400,
        )
    else:
        st.info("No trades match the selected filters.")
