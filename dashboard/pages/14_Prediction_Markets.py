"""Prediction Markets — Political prediction market probabilities and trends."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from config import DB_PATH
from dashboard.components.freshness import render_freshness
from dashboard.components.glossary import inject_tooltip_css

st.set_page_config(page_title="Prediction Markets", layout="wide")
st.title("Prediction Markets")
st.caption("Political prediction market probabilities and trends")
inject_tooltip_css()
render_freshness("prediction_markets", "last_updated", "Prediction Markets")


# --- Cached Query Functions ---


@st.cache_data(ttl=300)
def _load_summary():
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """SELECT COUNT(*) as total,
                      COUNT(DISTINCT category) as categories,
                      AVG(current_price) as avg_prob,
                      SUM(volume) as total_volume
               FROM prediction_markets"""
        ).fetchone()
    except Exception:
        row = None
    conn.close()
    return row


@st.cache_data(ttl=300)
def _load_markets():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT contract_id, platform, question_text, category,
                      current_price, volume, resolution_date, related_ticker,
                      last_updated
               FROM prediction_markets
               ORDER BY volume DESC""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_category_breakdown():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT category, COUNT(*) as market_count, AVG(current_price) as avg_prob
               FROM prediction_markets
               WHERE category IS NOT NULL
               GROUP BY category
               ORDER BY market_count DESC""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=300)
def _load_fomc_markets():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT question_text, current_price, volume, resolution_date
               FROM prediction_markets
               WHERE category = 'fomc'
               ORDER BY volume DESC""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


# --- Empty State Check ---

summary = _load_summary()

if summary is None or summary[0] == 0:
    st.info("No prediction market data yet. Run the Polymarket collector from Settings.")
    st.stop()

total_markets, category_count, avg_prob, total_volume = summary

# --- KPI Row ---

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric("Total Markets", f"{total_markets:,}")
with kpi_cols[1]:
    st.metric("Active Categories", f"{category_count:,}")
with kpi_cols[2]:
    prob_str = f"{avg_prob:.0%}" if avg_prob else "N/A"
    st.metric("Avg Probability", prob_str)
with kpi_cols[3]:
    vol_str = f"${total_volume:,.0f}" if total_volume else "$0"
    st.metric("Total Volume", vol_str)


# --- Sidebar Filters ---

st.sidebar.markdown("### Filters")

all_markets = _load_markets()
available_categories = sorted(all_markets["category"].dropna().unique().tolist()) if not all_markets.empty else []

filter_categories = st.sidebar.multiselect(
    "Category",
    options=available_categories,
    default=[],
    key="pred_category_filter",
)

filter_prob_range = st.sidebar.slider(
    "Probability Range",
    min_value=0.0,
    max_value=1.0,
    value=(0.0, 1.0),
    step=0.05,
    key="pred_prob_range",
)

filter_min_volume = st.sidebar.number_input(
    "Min Volume ($)",
    min_value=0,
    value=0,
    step=1000,
    key="pred_min_volume",
)


# --- Apply Filters ---

filtered = all_markets.copy()
if filter_categories:
    filtered = filtered[filtered["category"].isin(filter_categories)]
if filter_prob_range != (0.0, 1.0):
    filtered = filtered[
        (filtered["current_price"] >= filter_prob_range[0])
        & (filtered["current_price"] <= filter_prob_range[1])
    ]
if filter_min_volume > 0:
    filtered = filtered[filtered["volume"] >= filter_min_volume]


# --- Markets Table ---

st.markdown("---")
st.subheader(f"All Markets ({len(filtered):,})")

if not filtered.empty:
    display_df = filtered.copy()
    display_df["probability"] = display_df["current_price"].apply(
        lambda x: f"{x:.0%}" if pd.notna(x) else "N/A"
    )
    display_df["volume_fmt"] = display_df["volume"].apply(
        lambda x: f"${x:,.0f}" if pd.notna(x) else "$0"
    )
    st.dataframe(
        display_df[["question_text", "category", "probability", "volume_fmt",
                     "resolution_date", "related_ticker", "platform"]],
        column_config={
            "question_text": "Question",
            "category": "Category",
            "probability": "Probability",
            "volume_fmt": "Volume",
            "resolution_date": "Resolution Date",
            "related_ticker": "Ticker",
            "platform": "Platform",
        },
        use_container_width=True,
        hide_index=True,
        height=400,
    )
else:
    st.info("No markets match the selected filters.")

# --- Category Breakdown ---

st.markdown("---")

col_cat, col_high = st.columns(2)

with col_cat:
    st.subheader("Markets by Category")
    cat_df = _load_category_breakdown()
    if not cat_df.empty:
        fig = px.bar(
            cat_df,
            x="market_count",
            y="category",
            orientation="h",
            color="avg_prob",
            color_continuous_scale="RdYlGn",
            labels={"market_count": "Number of Markets", "category": "", "avg_prob": "Avg Prob"},
        )
        fig.update_layout(
            height=400,
            margin=dict(l=0, r=0, t=30, b=0),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No category data available.")

# --- High Conviction Markets ---

with col_high:
    st.subheader("High Conviction Markets")
    st.caption("Probability > 80% or < 20%")
    if not all_markets.empty:
        high_conviction = all_markets[
            (all_markets["current_price"] > 0.8) | (all_markets["current_price"] < 0.2)
        ].copy()
        if not high_conviction.empty:
            high_conviction["probability"] = high_conviction["current_price"].apply(
                lambda x: f"{x:.0%}" if pd.notna(x) else "N/A"
            )
            st.dataframe(
                high_conviction[["question_text", "category", "probability", "related_ticker"]],
                column_config={
                    "question_text": "Question",
                    "category": "Category",
                    "probability": "Probability",
                    "related_ticker": "Ticker",
                },
                use_container_width=True,
                hide_index=True,
                height=350,
            )
        else:
            st.info("No high-conviction markets (> 80% or < 20%) at this time.")
    else:
        st.info("No market data available.")

# --- FOMC Probabilities ---

st.markdown("---")
st.subheader("FOMC Rate Probabilities")

fomc_df = _load_fomc_markets()

if not fomc_df.empty:
    fomc_display = fomc_df.copy()
    fomc_display["probability"] = fomc_display["current_price"].apply(
        lambda x: f"{x:.0%}" if pd.notna(x) else "N/A"
    )
    fomc_display["volume_fmt"] = fomc_display["volume"].apply(
        lambda x: f"${x:,.0f}" if pd.notna(x) else "$0"
    )

    col_table, col_chart = st.columns(2)

    with col_table:
        st.dataframe(
            fomc_display[["question_text", "probability", "volume_fmt", "resolution_date"]],
            column_config={
                "question_text": "Question",
                "probability": "Probability",
                "volume_fmt": "Volume",
                "resolution_date": "Resolution Date",
            },
            use_container_width=True,
            hide_index=True,
        )

    with col_chart:
        fig = px.bar(
            fomc_df,
            x="current_price",
            y="question_text",
            orientation="h",
            color="current_price",
            color_continuous_scale="RdYlGn",
            labels={"current_price": "Probability", "question_text": ""},
        )
        fig.update_layout(
            height=max(200, len(fomc_df) * 50),
            margin=dict(l=0, r=0, t=30, b=0),
            xaxis=dict(range=[0, 1], tickformat=".0%"),
            yaxis=dict(autorange="reversed"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No FOMC prediction market data. Run the Polymarket collector to fetch data.")
