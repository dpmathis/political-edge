"""Macro & Fed Dashboard — Regime classifier, key indicators, FOMC tracker."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH

st.title("Macro & Fed Dashboard")
st.caption("Hedgeye-style regime classifier, key economic indicators, and FOMC tracker")

QUADRANT_COLORS = {1: "#2ecc71", 2: "#f1c40f", 3: "#e67e22", 4: "#e74c3c"}
QUADRANT_LABELS = {1: "Goldilocks", 2: "Reflation", 3: "Stagflation", 4: "Deflation"}


@st.cache_data(ttl=300)
def load_regime_data():
    conn = sqlite3.connect(DB_PATH)
    regimes = pd.read_sql_query(
        "SELECT * FROM macro_regimes ORDER BY date DESC", conn
    )
    conn.close()
    return regimes


@st.cache_data(ttl=300)
def load_macro_indicators(series_id: str, limit: int = 60):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT date, value, rate_of_change_3m, rate_of_change_6m, rate_of_change_12m
           FROM macro_indicators WHERE series_id = ?
           ORDER BY date DESC LIMIT ?""",
        conn,
        params=(series_id, limit),
    )
    conn.close()
    if not df.empty:
        df = df.sort_values("date")
    return df


@st.cache_data(ttl=300)
def load_fomc_events():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM fomc_events ORDER BY event_date DESC", conn
    )
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_yield_curve_data():
    """Load latest yield data for 2Y, 10Y rates."""
    conn = sqlite3.connect(DB_PATH)
    data = {}
    for series_id, label in [("DGS2", "2Y"), ("DGS10", "10Y"), ("DFF", "Fed Funds")]:
        row = conn.execute(
            "SELECT date, value FROM macro_indicators WHERE series_id = ? ORDER BY date DESC LIMIT 1",
            (series_id,),
        ).fetchone()
        if row:
            data[label] = {"date": row[0], "value": row[1]}
    conn.close()
    return data


# ── Row 1: Current Regime ─────────────────────────────────────────────
regimes = load_regime_data()

if regimes.empty:
    st.warning("No macro regime data available. Configure a FRED API key and run the FRED collector to populate.")
    st.stop()

current = regimes.iloc[0]
q = int(current["quadrant"])
color = QUADRANT_COLORS.get(q, "#95a5a6")
confidence = current.get("confidence", "unknown")
modifier = current.get("position_size_modifier", 1.0)

st.markdown(
    f"""
    <div style="background:{color}22; border:2px solid {color}; border-radius:12px; padding:24px; margin-bottom:20px;">
        <div style="display:flex; align-items:center; gap:20px;">
            <div style="font-size:64px; font-weight:bold; color:{color};">Q{q}</div>
            <div>
                <div style="font-size:28px; font-weight:bold;">{QUADRANT_LABELS.get(q, 'Unknown')}</div>
                <div style="font-size:16px; color:#666;">
                    Growth {'accelerating' if q in (1,2) else 'decelerating'},
                    Inflation {'accelerating' if q in (2,3) else 'decelerating'}
                </div>
                <div style="margin-top:8px;">
                    <span style="background:{color}44; padding:4px 10px; border-radius:6px; margin-right:8px;">
                        Confidence: <b>{confidence}</b>
                    </span>
                    <span style="background:{color}44; padding:4px 10px; border-radius:6px;">
                        Position Modifier: <b>{modifier:.1f}x</b>
                    </span>
                </div>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Favored / Avoid sectors
from analysis.macro_regime import QUADRANTS

regime_info = QUADRANTS.get(q, {})
col_fav, col_avoid, col_bias = st.columns(3)
with col_fav:
    sectors = ", ".join(regime_info.get("favored_sectors", []))
    st.metric("Favored Sectors", sectors or "N/A")
with col_avoid:
    sectors = ", ".join(regime_info.get("avoid_sectors", []))
    st.metric("Avoid Sectors", sectors or "N/A")
with col_bias:
    st.metric("Equity Bias", regime_info.get("equity_bias", "N/A").replace("_", " ").title())

# ── Row 2: Key Indicators ─────────────────────────────────────────────
st.markdown("---")
st.subheader("Key Indicators")

indicator_cols = st.columns(4)

indicators = [
    ("GDPC1", "GDP Growth RoC", "rate_of_change_6m", "{:+.1%}"),
    ("CPIAUCSL", "CPI Inflation RoC", "rate_of_change_6m", "{:+.1%}"),
    ("T10Y2Y", "10Y-2Y Spread", "value", "{:.2f}%"),
    ("VIXCLS", "VIX", "value", "{:.1f}"),
]

for i, (series_id, label, col_name, fmt) in enumerate(indicators):
    with indicator_cols[i]:
        df = load_macro_indicators(series_id, limit=60)
        if df.empty:
            st.metric(label, "No data")
            continue

        current_val = df.iloc[-1][col_name]
        if current_val is not None:
            try:
                display_val = fmt.format(float(current_val))
            except (ValueError, TypeError):
                display_val = str(current_val)
        else:
            display_val = "N/A"

        # Calculate delta
        delta = None
        if len(df) >= 2 and col_name in df.columns:
            prev_val = df.iloc[-2][col_name]
            if current_val is not None and prev_val is not None:
                delta = float(current_val) - float(prev_val)

        st.metric(label, display_val, delta=f"{delta:+.3f}" if delta is not None else None)

        # Sparkline
        if len(df) >= 3 and col_name in df.columns:
            spark_df = df[["date", col_name]].dropna()
            if not spark_df.empty:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=spark_df["date"],
                    y=spark_df[col_name],
                    mode="lines",
                    line=dict(color=color, width=2),
                    fill="tozeroy",
                    fillcolor=f"{color}22",
                ))
                fig.update_layout(
                    height=80, margin=dict(l=0, r=0, t=0, b=0),
                    xaxis=dict(visible=False), yaxis=dict(visible=False),
                    showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True, key=f"spark_{series_id}")

# ── Row 3: Regime History + Yield Curve ────────────────────────────────
st.markdown("---")
st.subheader("Macro Regime History")

col_regime, col_yield = st.columns(2)

with col_regime:
    if len(regimes) >= 2:
        regime_plot = regimes.sort_values("date").copy()
        regime_plot["color"] = regime_plot["quadrant"].map(QUADRANT_COLORS)
        regime_plot["label"] = regime_plot["quadrant"].apply(lambda x: f"Q{x} {QUADRANT_LABELS.get(x, '')}")

        fig = go.Figure()
        for q_val in sorted(regime_plot["quadrant"].unique()):
            mask = regime_plot["quadrant"] == q_val
            subset = regime_plot[mask]
            fig.add_trace(go.Bar(
                x=subset["date"],
                y=[1] * len(subset),
                name=f"Q{q_val} {QUADRANT_LABELS.get(q_val, '')}",
                marker_color=QUADRANT_COLORS.get(q_val, "#999"),
                hovertemplate="Date: %{x}<br>Regime: Q" + str(q_val) + "<extra></extra>",
            ))

        fig.update_layout(
            barmode="stack",
            height=300,
            xaxis_title="Date",
            yaxis=dict(visible=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=0, r=0, t=30, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Regime history requires at least 2 data points.")

with col_yield:
    yield_data = load_yield_curve_data()
    if yield_data:
        st.markdown("**Current Rates**")
        rate_cols = st.columns(len(yield_data))
        for i, (label, info) in enumerate(yield_data.items()):
            with rate_cols[i]:
                st.metric(label, f"{info['value']:.2f}%")
                st.caption(f"As of {info['date']}")

        # Show 10Y-2Y spread history
        spread_df = load_macro_indicators("T10Y2Y", limit=120)
        if not spread_df.empty:
            fig = px.line(spread_df, x="date", y="value", title="10Y-2Y Yield Spread")
            fig.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="Inverted")
            fig.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0), yaxis_title="Spread (%)")
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No yield curve data. Run the FRED collector to populate.")

# ── Row 4: FOMC Section ───────────────────────────────────────────────
st.markdown("---")
st.subheader("FOMC Tracker")

fomc_df = load_fomc_events()

# Next FOMC meeting countdown
today_str = date.today().isoformat()
if not fomc_df.empty:
    future_meetings = fomc_df[fomc_df["event_date"] >= today_str].sort_values("event_date")
    past_meetings = fomc_df[fomc_df["event_date"] < today_str].sort_values("event_date", ascending=False)
else:
    future_meetings = pd.DataFrame()
    past_meetings = pd.DataFrame()

fomc_col1, fomc_col2 = st.columns([1, 2])

with fomc_col1:
    if not future_meetings.empty:
        next_meeting = future_meetings.iloc[0]
        next_date = datetime.strptime(next_meeting["event_date"], "%Y-%m-%d").date()
        days_until = (next_date - date.today()).days
        st.metric("Next FOMC Meeting", next_meeting["event_date"], delta=f"{days_until} days away")
    else:
        st.info("No upcoming FOMC meetings in the calendar.")

    # Latest rate decision
    if not past_meetings.empty:
        latest = past_meetings.iloc[0]
        decision = latest.get("rate_decision", "N/A") or "N/A"
        score = latest.get("hawkish_dovish_score")
        st.metric("Latest Rate Decision", decision.replace("_", " ").title())
        if score is not None:
            tone = "Hawkish" if score > 0 else "Dovish" if score < 0 else "Neutral"
            st.metric("Statement Tone", f"{tone} ({score:+.2f})")

with fomc_col2:
    # Hawkish/Dovish score trend
    if not past_meetings.empty:
        scored = past_meetings[past_meetings["hawkish_dovish_score"].notna()].head(12).sort_values("event_date")
        if not scored.empty:
            fig = go.Figure()
            colors = ["#e74c3c" if s > 0 else "#2ecc71" for s in scored["hawkish_dovish_score"]]
            fig.add_trace(go.Bar(
                x=scored["event_date"],
                y=scored["hawkish_dovish_score"],
                marker_color=colors,
                hovertemplate="Date: %{x}<br>Score: %{y:.2f}<extra></extra>",
            ))
            fig.add_hline(y=0, line_dash="dash", line_color="gray")
            fig.update_layout(
                title="Hawkish/Dovish Score Trend",
                height=250,
                xaxis_title="Meeting Date",
                yaxis_title="Score (-1 dovish to +1 hawkish)",
                margin=dict(l=0, r=0, t=30, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)

# FOMC Events Table
if not past_meetings.empty:
    st.markdown("**Recent FOMC Events**")
    display_cols = ["event_date", "event_type", "rate_decision", "hawkish_dovish_score",
                    "spx_return_day", "spx_return_2day"]
    available = [c for c in display_cols if c in past_meetings.columns]
    table_df = past_meetings[available].head(10).copy()

    # Format columns
    for col in ["spx_return_day", "spx_return_2day"]:
        if col in table_df.columns:
            table_df[col] = table_df[col].apply(
                lambda x: f"{x:+.2%}" if pd.notna(x) else ""
            )
    if "hawkish_dovish_score" in table_df.columns:
        table_df["hawkish_dovish_score"] = table_df["hawkish_dovish_score"].apply(
            lambda x: f"{x:+.2f}" if pd.notna(x) else ""
        )

    rename = {
        "event_date": "Date",
        "event_type": "Type",
        "rate_decision": "Decision",
        "hawkish_dovish_score": "H/D Score",
        "spx_return_day": "SPX Day Return",
        "spx_return_2day": "SPX 2-Day Return",
    }
    table_df = table_df.rename(columns=rename)
    st.dataframe(table_df, use_container_width=True, hide_index=True)

# Statement diff for latest meeting
if not past_meetings.empty:
    latest = past_meetings.iloc[0]
    diff_text = latest.get("previous_statement_diff")
    if diff_text and str(diff_text).strip():
        with st.expander("Latest Statement Diff (vs. previous meeting)"):
            st.code(diff_text, language="diff")
