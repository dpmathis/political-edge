"""Shared rendering functions for event study results.

Extracted from 9_Research.py for reuse across Research and Pipeline pages.
"""

import json
import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.components.glossary import tooltip


def render_kpi_row(study: pd.Series) -> None:
    """Render a 4-column KPI row for an event study."""
    cols = st.columns(4)
    with cols[0]:
        st.metric("N Events", int(study["num_events"]),
                   help=tooltip("Total event-ticker observations in this study"))
    with cols[1]:
        car_val = study["mean_car"]
        st.metric("Mean " + tooltip("CAR"),
                   f"{car_val:+.2%}" if pd.notna(car_val) else "N/A",
                   help="Average cumulative abnormal return across all events")
    with cols[2]:
        p = study["p_value"]
        sig_label = "Significant" if pd.notna(p) and p < 0.05 else "Not Significant"
        st.metric(tooltip("p-value"),
                   f"{p:.4f}" if pd.notna(p) else "N/A",
                   delta=sig_label,
                   delta_color="normal" if pd.notna(p) and p < 0.05 else "off")
    with cols[3]:
        wr = study["win_rate"]
        st.metric(tooltip("Win Rate"),
                   f"{wr:.0%}" if pd.notna(wr) else "N/A",
                   help="Fraction of events where CAR > 0")


def render_car_timeline(study: pd.Series) -> None:
    """Render daily average CAR timeline chart."""
    results_json = study.get("results_json")
    if not results_json:
        return
    try:
        data = json.loads(results_json)
    except (json.JSONDecodeError, TypeError):
        return

    daily_car = data.get("daily_avg_car", [])
    if not daily_car:
        return

    window_pre = int(study.get("window_pre", 1))
    days = list(range(-window_pre, len(daily_car) - window_pre))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=days, y=[c * 100 for c in daily_car],
        mode="lines+markers", name="Avg CAR",
        line=dict(color="#3b82f6", width=2),
        marker=dict(size=5),
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=0, line_dash="dash", line_color="red", opacity=0.5,
                  annotation_text="Event Day")
    fig.update_layout(
        title="Cumulative Abnormal Return Timeline",
        xaxis_title="Days Relative to Event",
        yaxis_title="CAR (%)",
        height=350,
        margin=dict(l=40, r=20, t=40, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_per_event_scatter(study_id: int, conn: sqlite3.Connection) -> None:
    """Render per-event CAR scatter plot."""
    try:
        events = pd.read_sql_query(
            """SELECT event_date, ticker, event_description,
                      car_pre, car_post, car_full
               FROM event_study_results
               WHERE study_id = ?
               ORDER BY event_date""",
            conn,
            params=(study_id,),
        )
    except Exception:
        return

    if events.empty:
        return

    events["car_pct"] = events["car_full"].fillna(events["car_post"]) * 100
    events = events.dropna(subset=["car_pct"])

    if events.empty:
        return

    fig = px.scatter(
        events, x="event_date", y="car_pct",
        color="ticker", hover_data=["event_description"],
        title="Per-Event CARs",
        labels={"car_pct": "CAR (%)", "event_date": "Date"},
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig.update_layout(height=350, margin=dict(l=40, r=20, t=40, b=40))
    st.plotly_chart(fig, use_container_width=True)


def render_study_detail(study: pd.Series) -> None:
    """Render statistical detail expander."""
    with st.expander("Statistical Details"):
        detail_cols = st.columns(3)
        with detail_cols[0]:
            st.write(f"**t-statistic:** {study['t_statistic']:.3f}" if pd.notna(study['t_statistic']) else "**t-statistic:** N/A")
            st.write(f"**Sharpe Ratio:** {study['sharpe_ratio']:.2f}" if pd.notna(study['sharpe_ratio']) else "**Sharpe Ratio:** N/A")
        with detail_cols[1]:
            st.write(f"**Median CAR:** {study['median_car']:+.2%}" if pd.notna(study['median_car']) else "**Median CAR:** N/A")
            st.write(f"**Benchmark:** {study['benchmark']}")
        with detail_cols[2]:
            st.write(f"**Window:** [-{study['window_pre']}, +{study['window_post']}]")
            st.write(f"**Study:** {study['study_name']}")


def render_study_section(
    studies: pd.DataFrame,
    prefix: str,
    conn: sqlite3.Connection,
) -> None:
    """Render a full study section with KPIs, charts, and details."""
    if studies.empty:
        st.info("No results yet. Run the report first to generate data.")
        return

    # Show sub-studies in selectbox if multiple
    if len(studies) > 1:
        study_names = studies["study_name"].unique().tolist()
        selected = st.selectbox("Sub-study", study_names, key=f"select_{prefix}")
        study = studies[studies["study_name"] == selected].iloc[0]
    else:
        study = studies.iloc[0]

    render_kpi_row(study)
    render_car_timeline(study)
    render_per_event_scatter(study["id"], conn)
    render_study_detail(study)
