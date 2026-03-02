"""Executive Order Tracker — Topic-classified EOs with evidence-based trading signals."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from analysis.eo_classifier import (
    TOPIC_CONFIDENCE,
    TOPIC_EXPECTED_CAR,
    TOPIC_SAMPLE_SIZE,
    TOPIC_TICKERS,
    classify_eo,
)
from config import DB_PATH
from dashboard.components.glossary import inject_tooltip_css, render_glossary_term

from dashboard.components.freshness import render_freshness

st.set_page_config(page_title="EO Tracker", layout="wide")
st.title("Executive Order Tracker")
st.caption("Real-time topic classification with evidence-based trading signals")
inject_tooltip_css()
render_freshness("regulatory_events", "publication_date", "EO Events")

conn = sqlite3.connect(DB_PATH)

# ---- Load and classify EOs ----
try:
    eos = pd.read_sql_query(
    """SELECT id, publication_date, title, url
       FROM regulatory_events
       WHERE event_type = 'executive_order'
       ORDER BY publication_date DESC""",
    conn,
    )
except Exception:
    eos = pd.DataFrame()

if eos.empty:
    st.info("No executive orders found. Run collectors to populate.")
    conn.close()
    st.stop()

# Classify each EO
classifications = eos["title"].apply(classify_eo).apply(pd.Series)
eos = pd.concat([eos, classifications], axis=1)

# ---- KPI Row ----
kpi_cols = st.columns(4)

tradeable = eos[eos["is_tradeable"]]
recent_7d = eos[pd.to_datetime(eos["publication_date"]) >= pd.Timestamp.now() - pd.Timedelta(days=7)]
recent_tradeable = recent_7d[recent_7d["is_tradeable"]]

with kpi_cols[0]:
    st.metric("Total EOs", len(eos))
with kpi_cols[1]:
    st.metric("Tradeable Signals", len(tradeable), f"+{len(recent_tradeable)} this week")
with kpi_cols[2]:
    st.metric("Last 7 Days", len(recent_7d))
with kpi_cols[3]:
    top_signal = (
        recent_tradeable.iloc[0]["topic"].replace("_", "/").title()
        if len(recent_tradeable) > 0
        else "None"
    )
    st.metric("Top Signal", top_signal)

# ---- Active Signals ----
if len(recent_tradeable) > 0:
    st.markdown("---")
    st.subheader("Active Signals")
    for _, row in recent_tradeable.head(5).iterrows():
        direction_label = "LONG" if row["direction"] == "long" else "SHORT" if row["direction"] == "short" else "WATCH"
        tickers_str = ", ".join(row["tickers"]) if row["tickers"] else "N/A"

        with st.container():
            sig_cols = st.columns([1, 1, 1, 4])
            with sig_cols[0]:
                st.markdown(f"**{row['publication_date']}**")
            with sig_cols[1]:
                st.markdown(f"**{direction_label}** {tickers_str}")
            with sig_cols[2]:
                st.markdown(f"**{row['confidence'].upper()}** | CAR: {row['expected_car']:+.2%}")
            with sig_cols[3]:
                st.markdown(f"{row['title'][:120]}")

# ---- Evidence Summary + Topic Distribution ----
st.markdown("---")
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("EO Topic Distribution")
    topic_counts = eos["topic"].value_counts().reset_index()
    topic_counts.columns = ["topic", "count"]
    topic_counts = topic_counts[topic_counts["topic"] != "other"]

    if not topic_counts.empty:
        fig = px.bar(
            topic_counts,
            x="topic",
            y="count",
            color="count",
            color_continuous_scale="Blues",
            labels={"topic": "Topic", "count": "Count"},
        )
        fig.update_layout(showlegend=False, height=350, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No classified EOs found.")

with col_right:
    st.subheader("Signal Evidence Summary")
    evidence_data = []
    for topic, car in TOPIC_EXPECTED_CAR.items():
        confidence = TOPIC_CONFIDENCE.get(topic, "low")
        n = TOPIC_SAMPLE_SIZE.get(topic, 0)
        is_tradeable = confidence in ("high", "medium")
        # Evidence strength indicator
        if isinstance(n, int) and n > 30:
            strength = "Strong"
        elif isinstance(n, int) and n > 15:
            strength = "Moderate"
        else:
            strength = "Weak"
        evidence_data.append({
            "Topic": topic.replace("_", " ").title(),
            "Expected CAR": f"{car:+.2%}",
            "Confidence": confidence.upper(),
            "N": n,
            "Evidence": strength,
            "Tickers": ", ".join(TOPIC_TICKERS.get(topic, [])),
            "Tradeable": "Yes" if is_tradeable else "No",
        })
    st.dataframe(pd.DataFrame(evidence_data), use_container_width=True, hide_index=True)
    st.caption(
        f"{render_glossary_term('CAR', 'CAR = Cumulative Abnormal Return')} | "
        f"Strong evidence: N>30, Moderate: N>15, Weak: N<15"
    )

# ---- Regulatory Shock Alerts ----
st.markdown("---")
st.subheader("Regulatory Intensity Shocks")

try:
    from analysis.reg_shock_detector import detect_shocks
    shocks = detect_shocks(lookback_weeks=4, conn=conn)
    if shocks:
        for shock in shocks:
            direction_label = "LONG" if shock["direction"] == "long" else "SHORT"
            st.warning(
                f"**{shock['agency'][:60]}** | Week of {shock['week_start']} | "
                f"Count: {shock['count']} (z={shock['z_score']:.1f}) | "
                f"{direction_label} {', '.join(shock['tickers'])} | "
                f"Expected CAR: {shock['expected_car']:+.2%}"
            )
    else:
        st.info("No regulatory shocks detected in the last 4 weeks.")
except Exception as e:
    st.error(f"Shock detection error: {e}")

# ---- EO Timeline Table ----
st.markdown("---")

topic_options = [t for t in eos["topic"].unique() if t != "other"]
filter_cols = st.columns([2, 1])
with filter_cols[0]:
    topic_filter = st.multiselect("Filter by topic", options=topic_options, default=topic_options, key="eo_topic_filter")
with filter_cols[1]:
    tradeable_only = st.checkbox("Tradeable only", value=False, key="eo_tradeable_only")

filtered = eos.copy()
if topic_filter:
    filtered = filtered[filtered["topic"].isin(topic_filter)]
else:
    filtered = filtered[filtered["topic"] != "other"]
if tradeable_only:
    filtered = filtered[filtered["is_tradeable"]]

with st.expander(f"Executive Order Timeline ({len(filtered)} orders)"):
    display_df = filtered[["publication_date", "topic", "direction", "confidence", "title"]].copy()
    display_df["topic"] = display_df["topic"].str.replace("_", " ").str.title()
    display_df["confidence"] = display_df["confidence"].fillna("").str.upper()
    display_df["direction"] = display_df["direction"].fillna("")

    st.dataframe(
        display_df,
        column_config={
            "publication_date": "Date",
            "topic": "Topic",
            "direction": "Direction",
            "confidence": "Confidence",
            "title": "Title",
        },
        use_container_width=True,
        hide_index=True,
        height=400,
    )

# ---- EO Detail ----
if not filtered.empty:
    st.markdown("---")
    st.subheader("EO Signal Detail")

    selected_title = st.selectbox(
        "Select an Executive Order",
        filtered["title"].head(20).tolist(),
        format_func=lambda x: x[:100],
        key="eo_detail_select",
    )

    if selected_title:
        eo_row = filtered[filtered["title"] == selected_title].iloc[0]
        detail_cols = st.columns(3)
        with detail_cols[0]:
            st.markdown(f"**Published:** {eo_row['publication_date']}")
            st.markdown(f"**Topic:** {eo_row['topic'].replace('_', ' ').title()}")
        with detail_cols[1]:
            if eo_row["direction"]:
                dir_label = "LONG" if eo_row["direction"] == "long" else "SHORT" if eo_row["direction"] == "short" else "N/A"
                st.markdown(f"**Direction:** {dir_label}")
            if eo_row["confidence"]:
                st.markdown(f"**Confidence:** {eo_row['confidence'].upper()}")
        with detail_cols[2]:
            if eo_row["expected_car"]:
                st.markdown(f"**Expected CAR:** {eo_row['expected_car']:+.2%}")
            if eo_row["tickers"]:
                st.markdown(f"**Tickers:** {', '.join(eo_row['tickers'])}")
        if eo_row.get("tariff_direction"):
            st.markdown(f"**Tariff Direction:** {eo_row['tariff_direction'].title()}")
        if eo_row.get("url"):
            st.markdown(f"[View on Federal Register]({eo_row['url']})")

conn.close()
