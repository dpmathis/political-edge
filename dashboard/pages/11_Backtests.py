"""Backtests -- Run and visualize hypothesis backtests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from config import DB_PATH
from dashboard.components.glossary import inject_tooltip_css

st.title("Backtests")
st.caption("Run event study backtests and compare hypothesis performance")
inject_tooltip_css()

# ── BacktestRunner import (may fail if dependencies are missing) ──

try:
    from analysis.backtest_runner import BacktestRunner
    _RUNNER_AVAILABLE = True
except Exception as _import_err:
    _RUNNER_AVAILABLE = False
    _RUNNER_IMPORT_ERROR = str(_import_err)

try:
    from dashboard.components.research_charts import render_kpi_row, render_car_timeline
    _CHARTS_AVAILABLE = True
except Exception:
    _CHARTS_AVAILABLE = False


# ── Helpers ───────────────────────────────────────────────────────


@st.cache_data(ttl=300)
def _load_saved_results() -> pd.DataFrame:
    """Load saved backtest results from the event_studies table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            """SELECT study_name, hypothesis, num_events, mean_car, median_car,
                      t_statistic, p_value, sharpe_ratio, win_rate, created_at
               FROM event_studies
               ORDER BY created_at DESC""",
            conn,
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


def _result_to_series(result) -> pd.Series:
    """Convert an EventStudyResults dataclass to a pd.Series for chart components."""
    d = {
        "study_name": result.study_name,
        "hypothesis": result.hypothesis,
        "method": result.method,
        "benchmark": result.benchmark,
        "window_pre": result.window_pre,
        "window_post": result.window_post,
        "num_events": result.num_events,
        "mean_car": result.mean_car,
        "median_car": result.median_car,
        "t_statistic": result.t_statistic,
        "p_value": result.p_value,
        "win_rate": result.win_rate,
        "sharpe_ratio": result.sharpe_ratio,
    }
    # Include results_json for the CAR timeline chart
    import json
    d["results_json"] = json.dumps({
        "daily_avg_ar": getattr(result, "daily_avg_ar", []),
        "daily_avg_car": getattr(result, "daily_avg_car", []),
    })
    return pd.Series(d)


# ── Tabs ──────────────────────────────────────────────────────────

tab_run, tab_saved = st.tabs(["Run Backtests", "Saved Results"])


# ── Tab 1: Run Backtests ─────────────────────────────────────────

with tab_run:
    if not _RUNNER_AVAILABLE:
        st.error(
            f"BacktestRunner could not be imported: {_RUNNER_IMPORT_ERROR}. "
            "Ensure all analysis dependencies are installed."
        )
        st.stop()

    runner = BacktestRunner(db_path=DB_PATH)
    available_studies = runner.list_studies()

    selected_studies = st.multiselect(
        "Select studies to run",
        options=available_studies,
        default=[],
        help="Choose one or more hypothesis backtests to execute",
    )

    run_button = st.button("Run Selected", type="primary", disabled=len(selected_studies) == 0)

    if run_button and selected_studies:
        progress_bar = st.progress(0)
        results_container = st.container()

        for i, name in enumerate(selected_studies):
            progress_bar.progress((i) / len(selected_studies))

            try:
                with st.spinner(f"Running {name}..."):
                    result = runner.run_study(name)
            except Exception as e:
                st.error(f"Study '{name}' failed: {e}")
                continue

            # Save result to DB
            try:
                result.save_to_db(DB_PATH)
            except Exception as e:
                st.warning(f"Could not save '{name}' to database: {e}")

            # Display result in an expander
            mean_car = result.mean_car
            p_value = result.p_value
            with results_container.expander(
                f"{name}: CAR={mean_car:+.2%}, p={p_value:.3f}",
                expanded=True,
            ):
                study_series = _result_to_series(result)

                # KPI row
                if _CHARTS_AVAILABLE:
                    render_kpi_row(study_series)
                else:
                    cols = st.columns(4)
                    with cols[0]:
                        st.metric("N Events", int(result.num_events))
                    with cols[1]:
                        st.metric("Mean CAR", f"{mean_car:+.2%}")
                    with cols[2]:
                        sig = "Significant" if p_value < 0.05 else "Not Significant"
                        st.metric("p-value", f"{p_value:.4f}", delta=sig)
                    with cols[3]:
                        st.metric("Win Rate", f"{result.win_rate:.0%}")

                # CAR timeline if per-event data exists
                has_daily = (
                    hasattr(result, "daily_avg_car")
                    and result.daily_avg_car
                    and len(result.daily_avg_car) > 0
                )
                if has_daily and _CHARTS_AVAILABLE:
                    render_car_timeline(study_series)

                # Additional stats
                st.markdown(
                    f"**Hypothesis:** {result.hypothesis}  \n"
                    f"**Method:** {result.method} | **Benchmark:** {result.benchmark} | "
                    f"**Window:** [-{result.window_pre}, +{result.window_post}] | "
                    f"**Sharpe:** {result.sharpe_ratio:.2f}"
                )

            progress_bar.progress((i + 1) / len(selected_studies))

        progress_bar.progress(1.0)
        st.success(f"Completed {len(selected_studies)} backtest(s).")
        # Clear cached saved results so the Saved Results tab picks up new data
        _load_saved_results.clear()


# ── Tab 2: Saved Results ─────────────────────────────────────────

with tab_saved:
    saved_df = _load_saved_results()

    if saved_df.empty:
        st.info(
            "No saved backtest results. Run backtests from the 'Run Backtests' tab "
            "or via the Settings page."
        )
    else:
        # Group by study_name, keep only latest run per study
        latest = saved_df.drop_duplicates(subset=["study_name"], keep="first").copy()

        st.subheader("Comparison Table")
        st.caption("Latest run per study, sorted by most recent")

        # Build display table
        display = latest[
            ["study_name", "hypothesis", "num_events", "mean_car", "median_car",
             "p_value", "sharpe_ratio", "win_rate"]
        ].copy()

        display["mean_car"] = display["mean_car"].apply(
            lambda x: f"{x:.2%}" if pd.notna(x) else "N/A"
        )
        display["median_car"] = display["median_car"].apply(
            lambda x: f"{x:.2%}" if pd.notna(x) else "N/A"
        )
        display["win_rate"] = display["win_rate"].apply(
            lambda x: f"{x:.0%}" if pd.notna(x) else "N/A"
        )
        display["p_value"] = display["p_value"].apply(
            lambda x: f"{x:.3f}" if pd.notna(x) else "N/A"
        )
        display["sharpe_ratio"] = display["sharpe_ratio"].apply(
            lambda x: f"{x:.2f}" if pd.notna(x) else "N/A"
        )
        display["Significant?"] = latest["p_value"].apply(
            lambda x: "Yes" if pd.notna(x) and x < 0.05 else "No"
        )

        display.columns = [
            "Study", "Hypothesis", "N Events", "Mean CAR", "Median CAR",
            "p-value", "Sharpe", "Win Rate", "Significant?",
        ]

        st.dataframe(display, use_container_width=True, hide_index=True)

        # Bar chart: mean CAR by study name
        st.subheader("Mean CAR by Study")

        chart_data = latest[["study_name", "mean_car", "p_value"]].copy()
        chart_data["mean_car_pct"] = chart_data["mean_car"] * 100
        chart_data["significant"] = chart_data["p_value"].apply(
            lambda x: "p < 0.05" if pd.notna(x) and x < 0.05 else "p >= 0.05"
        )

        fig = px.bar(
            chart_data,
            x="study_name",
            y="mean_car_pct",
            color="significant",
            color_discrete_map={"p < 0.05": "#22c55e", "p >= 0.05": "#9ca3af"},
            hover_data=["p_value"],
            title="Mean CAR by Study (green = statistically significant)",
            labels={"mean_car_pct": "Mean CAR (%)", "study_name": "Study"},
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig.update_layout(
            height=450,
            margin=dict(l=40, r=20, t=50, b=100),
            xaxis_tickangle=-45,
            legend_title_text="",
        )
        st.plotly_chart(fig, use_container_width=True)
