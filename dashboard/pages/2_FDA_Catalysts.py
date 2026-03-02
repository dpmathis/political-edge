"""FDA Catalysts — FDA advisory committee votes, PDUFA dates, and event study results."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import sqlite3
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH
from dashboard.components.glossary import inject_tooltip_css, render_metric_with_tooltip, render_glossary_term, tooltip

st.title("FDA Catalysts")
st.caption("FDA advisory committee votes, approvals, and event study results")
inject_tooltip_css()

from dashboard.components.freshness import render_freshness
render_freshness("fda_events", "event_date", "FDA Events")

conn = sqlite3.connect(DB_PATH)

# --- KPI ROW ---
try:
    total_fda = conn.execute("SELECT COUNT(*) FROM fda_events").fetchone()[0]
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM fda_events WHERE outcome = 'pending' AND event_date >= date('now')"
    ).fetchone()[0]
    with_ticker = conn.execute("SELECT COUNT(*) FROM fda_events WHERE ticker IS NOT NULL").fetchone()[0]
except Exception:
    total_fda = 0
    upcoming = 0
    with_ticker = 0

# Get event study stats if available
try:
    study_row = conn.execute(
        "SELECT mean_car, win_rate, num_events FROM event_studies WHERE study_name = 'fda_adcom' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
except Exception:
    study_row = None

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric("Total FDA Events", f"{total_fda:,}")
with kpi_cols[1]:
    st.metric("Upcoming (Pending)", upcoming)
with kpi_cols[2]:
    st.metric("Mapped to Tickers", with_ticker)
with kpi_cols[3]:
    if study_row:
        st.metric("AdCom Study CAR", f"{study_row[0]:+.2%}", help=tooltip("CAR"))
    else:
        st.metric("AdCom Study", "Not run yet")

# --- EVENT TYPE BREAKDOWN ---
st.markdown("---")

try:
    type_df = pd.read_sql_query(
        "SELECT event_type, COUNT(*) as count FROM fda_events GROUP BY event_type ORDER BY count DESC",
        conn,
    )
except Exception:
    type_df = pd.DataFrame()

if not type_df.empty:
    import plotly.express as px

    col_chart, col_upcoming = st.columns([1, 1])

    with col_chart:
        st.subheader("Events by Type")
        fig = px.bar(type_df, x="event_type", y="count", color="count", color_continuous_scale="Blues")
        fig.update_layout(height=300, margin=dict(l=40, r=40, t=10, b=40))
        st.plotly_chart(fig, use_container_width=True)

    with col_upcoming:
        st.subheader("Upcoming FDA Events")
        try:
            upcoming_df = pd.read_sql_query(
                """SELECT event_date, event_type, drug_name, company_name, ticker, details
                   FROM fda_events
                   WHERE outcome = 'pending' AND event_date >= date('now')
                   ORDER BY event_date
                   LIMIT 20""",
                conn,
            )
        except Exception:
            upcoming_df = pd.DataFrame()
        if upcoming_df.empty:
            st.info("No pending FDA events found.")
        else:
            st.dataframe(upcoming_df, use_container_width=True, height=300)

# --- EVENT STUDY RESULTS ---
st.markdown("---")
st.subheader("Event Study Results")

try:
    studies = pd.read_sql_query(
        """SELECT id, study_name, hypothesis, num_events, mean_car, median_car,
                  t_statistic, p_value, win_rate, sharpe_ratio, results_json, created_at
           FROM event_studies
           ORDER BY created_at DESC""",
        conn,
    )
except Exception:
    studies = pd.DataFrame()

if studies.empty:
    st.info("No event studies have been run yet. Use `python scripts/run_backtests.py` to generate results.")
else:
    # Summary table
    display_studies = studies[["study_name", "num_events", "mean_car", "median_car", "t_statistic", "p_value", "win_rate", "sharpe_ratio"]].copy()
    display_studies.columns = ["Study", "Events", "Mean CAR", "Median CAR", "t-stat", "p-value", "Win Rate", "Sharpe"]
    display_studies["Mean CAR"] = display_studies["Mean CAR"].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
    display_studies["Median CAR"] = display_studies["Median CAR"].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
    display_studies["Win Rate"] = display_studies["Win Rate"].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "")
    st.dataframe(display_studies, use_container_width=True)

    # Trader's Summary for the selected study
    if study_row:
        car, win_rate, n_events = study_row
        st.markdown(
            f"**Trader's Summary:** When FDA advisory committees vote favorably, "
            f"the stock moves **{car:+.1%}** on average over the event window. "
            f"This has worked **{win_rate:.0%}** of the time across {n_events} events. "
            + ("The statistical evidence is strong." if n_events > 20 else "Sample size is limited — interpret with caution.")
        )

    # CAR curve chart for selected study
    selected_study_idx = st.selectbox(
        "Select study for CAR curve",
        range(len(studies)),
        format_func=lambda i: f"{studies.iloc[i]['study_name']} ({studies.iloc[i]['num_events']} events)",
    )
    study = studies.iloc[selected_study_idx]

    if study["results_json"]:
        try:
            results_data = json.loads(study["results_json"])
            daily_avg_car = results_data.get("daily_avg_car", [])
            daily_avg_ar = results_data.get("daily_avg_ar", [])

            if daily_avg_car:
                window_pre = study.get("window_pre", 5) if "window_pre" in study.index else 5
                days = list(range(-window_pre, len(daily_avg_car) - window_pre))
                if len(days) != len(daily_avg_car):
                    days = list(range(len(daily_avg_car)))

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=days, y=[x * 100 for x in daily_avg_car],
                    mode="lines+markers", name="Avg CAR",
                    line=dict(color="#2563eb", width=2),
                ))
                fig.add_vline(x=0, line_dash="dash", line_color="gray", annotation_text="Event Day")
                fig.add_hline(y=0, line_dash="dot", line_color="gray")
                fig.update_layout(
                    title=f"Average Cumulative Abnormal Returns — {study['study_name']}",
                    xaxis_title="Days Relative to Event",
                    yaxis_title="CAR (%)",
                    height=400,
                )
                st.plotly_chart(fig, use_container_width=True)
        except (json.JSONDecodeError, KeyError):
            pass

    # Per-event results
    try:
        per_event = pd.read_sql_query(
            """SELECT event_date, ticker, event_description, car_pre, car_post, car_full
               FROM event_study_results WHERE study_id = ? ORDER BY event_date""",
            conn,
            params=(int(study["id"]),),
        )
    except Exception:
        per_event = pd.DataFrame()
    if not per_event.empty:
        with st.expander(f"Per-Event Results ({len(per_event)})"):
            display_per = per_event.copy()
            for col in ["car_pre", "car_post", "car_full"]:
                display_per[col] = display_per[col].apply(lambda x: f"{x:+.2%}" if pd.notna(x) else "")
            display_per.columns = ["Date", "Ticker", "Description", "CAR Pre", "CAR Post", "CAR Full"]
            st.dataframe(display_per, use_container_width=True)

# --- ALL FDA EVENTS TABLE ---
st.markdown("---")

try:
    fda_df = pd.read_sql_query(
        """SELECT event_date, event_type, drug_name, company_name, ticker,
                  outcome, details, abnormal_return
           FROM fda_events
           ORDER BY event_date DESC
           LIMIT 200""",
        conn,
    )
except Exception:
    fda_df = pd.DataFrame()

conn.close()

if fda_df.empty:
    st.info("No FDA events found. Run collectors to populate.")
else:
    with st.expander(f"All FDA Events ({len(fda_df)})"):
        # Filters
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            type_filter = st.multiselect("Event Type", fda_df["event_type"].unique().tolist())
        with col_f2:
            ticker_filter = st.multiselect("Ticker", sorted(fda_df["ticker"].dropna().unique().tolist()))

        if type_filter:
            fda_df = fda_df[fda_df["event_type"].isin(type_filter)]
        if ticker_filter:
            fda_df = fda_df[fda_df["ticker"].isin(ticker_filter)]

        st.dataframe(fda_df, use_container_width=True, height=400)
