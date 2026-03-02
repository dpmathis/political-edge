"""Government Contracts — Federal contract awards from USASpending with agency and watchlist analysis."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from config import DB_PATH

from dashboard.components.freshness import render_freshness

st.set_page_config(page_title="Government Contracts", layout="wide")
st.title("Government Contracts")
st.caption("Federal contract awards from USASpending with agency and watchlist analysis")
render_freshness("contract_awards", "award_date", "Contract Awards")


# --- Helpers ---

def format_currency(value):
    """Format a dollar value as $X.XB, $X.XM, or $X.XK."""
    if value is None or pd.isna(value):
        return "$0"
    abs_val = abs(value)
    if abs_val >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.1f}B"
    elif abs_val >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    elif abs_val >= 1_000:
        return f"${value / 1_000:,.1f}K"
    else:
        return f"${value:,.0f}"


@st.cache_data(ttl=300)
def load_summary_metrics():
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(award_amount) as total_value,
                      COUNT(CASE WHEN recipient_ticker IS NOT NULL THEN 1 END) as watchlist_linked,
                      AVG(award_amount) as avg_size
               FROM contract_awards"""
        ).fetchone()
    except Exception:
        row = None
    conn.close()
    return row


@st.cache_data(ttl=300)
def load_recent_top_awards():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT award_date, recipient_name, recipient_ticker, awarding_agency,
                      award_amount, description
               FROM contract_awards
               WHERE award_date >= date('now', '-30 days')
               ORDER BY award_amount DESC
               LIMIT 20""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_agency_spending():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT awarding_agency, SUM(award_amount) as total
               FROM contract_awards
               GROUP BY awarding_agency
               ORDER BY total DESC
               LIMIT 15""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_awards_over_time():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT award_date, COUNT(*) as count, SUM(award_amount) as total
               FROM contract_awards
               WHERE award_date >= date('now', '-90 days')
               GROUP BY award_date
               ORDER BY award_date""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_watchlist_awards():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT ca.award_date, ca.recipient_name, ca.recipient_ticker,
                      ca.awarding_agency, ca.award_amount, ca.description
               FROM contract_awards ca
               WHERE ca.recipient_ticker IS NOT NULL
               ORDER BY ca.award_date DESC
               LIMIT 50""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_agencies():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT DISTINCT awarding_agency
               FROM contract_awards
               WHERE awarding_agency IS NOT NULL
               ORDER BY awarding_agency""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_awards_by_agency(agency):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT award_date, recipient_name, recipient_ticker,
                      award_amount, description
               FROM contract_awards
               WHERE awarding_agency = ?
               ORDER BY award_date DESC
               LIMIT 50""",
            conn,
            params=(agency,),
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


# --- Row 1: Summary Metrics ---

metrics = load_summary_metrics()

if metrics is None or metrics[0] == 0:
    st.info("No contract award data yet. Run the USASpending collector.")
    st.stop()

total_awards, total_value, watchlist_linked, avg_size = metrics

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric("Total Awards", f"{total_awards:,}")
with kpi_cols[1]:
    st.metric("Total Value", format_currency(total_value))
with kpi_cols[2]:
    st.metric("Watchlist-Linked", f"{watchlist_linked:,}")
with kpi_cols[3]:
    st.metric("Average Award Size", format_currency(avg_size))

# --- Row 2: Recent Top Awards ---
st.markdown("---")
st.subheader("Recent Top Awards (Last 30 Days)")

recent_df = load_recent_top_awards()

if not recent_df.empty:
    display_df = recent_df.copy()
    display_df["award_amount"] = display_df["award_amount"].apply(
        lambda x: f"${x:,.0f}" if pd.notna(x) else ""
    )
    display_df["description"] = display_df["description"].apply(
        lambda x: x[:80] + "..." if isinstance(x, str) and len(x) > 80 else x
    )
    st.dataframe(
        display_df,
        column_config={
            "award_date": "Date",
            "recipient_name": "Recipient",
            "recipient_ticker": "Ticker",
            "awarding_agency": "Agency",
            "award_amount": "Amount",
            "description": "Description",
        },
        use_container_width=True,
        hide_index=True,
        height=400,
    )
else:
    st.info("No awards in the last 30 days.")

# --- Row 3: Charts ---
st.markdown("---")

col_agency, col_time = st.columns(2)

with col_agency:
    st.subheader("Agency Spending")
    agency_df = load_agency_spending()
    if not agency_df.empty:
        fig = px.bar(
            agency_df,
            x="total",
            y="awarding_agency",
            orientation="h",
            color="total",
            color_continuous_scale="Blues",
        )
        fig.update_layout(
            height=400,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Total Award Value ($)",
            yaxis_title="",
            yaxis=dict(autorange="reversed"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No agency spending data available.")

with col_time:
    st.subheader("Awards Over Time (90 Days)")
    time_df = load_awards_over_time()
    if not time_df.empty:
        fig = px.bar(
            time_df,
            x="award_date",
            y="total",
            text="count",
            color="total",
            color_continuous_scale="Blues",
        )
        fig.update_layout(
            height=400,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Date",
            yaxis_title="Total Award Value ($)",
            showlegend=False,
        )
        fig.update_traces(texttemplate="%{text} awards", textposition="outside")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No award data in the last 90 days.")

# --- Row 4: Watchlist-Linked Awards ---
st.markdown("---")

watchlist_df = load_watchlist_awards()

if not watchlist_df.empty:
    with st.expander(f"Watchlist-Linked Awards ({len(watchlist_df)})"):
        display_wl = watchlist_df.copy()
        display_wl["award_amount"] = display_wl["award_amount"].apply(
            lambda x: f"${x:,.0f}" if pd.notna(x) else ""
        )
        display_wl["description"] = display_wl["description"].apply(
            lambda x: x[:80] + "..." if isinstance(x, str) and len(x) > 80 else x
        )
        st.dataframe(
            display_wl,
            column_config={
                "award_date": "Date",
                "recipient_name": "Recipient",
                "recipient_ticker": "Ticker",
                "awarding_agency": "Agency",
                "award_amount": "Amount",
                "description": "Description",
            },
            use_container_width=True,
            hide_index=True,
            height=400,
        )
else:
    st.info("No watchlist-linked contract awards found.")

# --- Row 5: Agency Filter ---
st.markdown("---")
st.subheader("Filter by Agency")

agencies_df = load_agencies()

if not agencies_df.empty:
    selected_agency = st.selectbox(
        "Select an agency",
        agencies_df["awarding_agency"].tolist(),
        key="contract_agency_filter",
    )

    if selected_agency:
        filtered_df = load_awards_by_agency(selected_agency)
        if not filtered_df.empty:
            display_filtered = filtered_df.copy()
            display_filtered["award_amount"] = display_filtered["award_amount"].apply(
                lambda x: f"${x:,.0f}" if pd.notna(x) else ""
            )
            display_filtered["description"] = display_filtered["description"].apply(
                lambda x: x[:80] + "..." if isinstance(x, str) and len(x) > 80 else x
            )
            st.dataframe(
                display_filtered,
                column_config={
                    "award_date": "Date",
                    "recipient_name": "Recipient",
                    "recipient_ticker": "Ticker",
                    "award_amount": "Amount",
                    "description": "Description",
                },
                use_container_width=True,
                hide_index=True,
                height=400,
            )
        else:
            st.info(f"No awards found for {selected_agency}.")
else:
    st.info("No agencies found in contract data.")
