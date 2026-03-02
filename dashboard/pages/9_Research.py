"""Research Reports — Formal event studies with statistical rigor."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH
from dashboard.components.glossary import inject_tooltip_css, tooltip

st.title("Research Reports")
st.caption("Formal event studies validating trading signals with statistical rigor")
inject_tooltip_css()

conn = sqlite3.connect(DB_PATH)


# ── Helpers ────────────────────────────────────────────────────────


def _load_studies(prefix: str) -> pd.DataFrame:
    """Load event studies matching a name prefix."""
    try:
        return pd.read_sql_query(
            """SELECT study_id, study_name, hypothesis, benchmark,
                      window_pre, window_post, num_events, mean_car,
                      median_car, t_statistic, p_value, sharpe_ratio,
                      win_rate, results_json, created_at
               FROM event_studies
               WHERE study_name LIKE ?
               ORDER BY created_at DESC""",
            conn,
            params=(f"{prefix}%",),
        )
    except Exception:
        return pd.DataFrame()


def _load_per_event(study_id: int) -> pd.DataFrame:
    """Load per-event results for a study."""
    try:
        return pd.read_sql_query(
            """SELECT event_date, ticker, event_description,
                      car_pre, car_post, car_full
               FROM event_study_results
               WHERE study_id = ?
               ORDER BY event_date""",
            conn,
            params=(study_id,),
        )
    except Exception:
        return pd.DataFrame()


def _render_kpi_row(study: pd.Series) -> None:
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


def _render_car_timeline(study: pd.Series) -> None:
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


def _render_per_event_scatter(study_id: int) -> None:
    """Render per-event CAR scatter plot."""
    events = _load_per_event(study_id)
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


def _render_study_detail(study: pd.Series) -> None:
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


def _render_study_section(studies: pd.DataFrame, prefix: str) -> None:
    """Render a full study section with KPIs, charts, and details."""
    if studies.empty:
        st.info("No results yet. Click 'Run Report' to generate.")
        return

    # Show sub-studies in selectbox if multiple
    if len(studies) > 1:
        study_names = studies["study_name"].unique().tolist()
        selected = st.selectbox("Sub-study", study_names, key=f"select_{prefix}")
        study = studies[studies["study_name"] == selected].iloc[0]
    else:
        study = studies.iloc[0]

    _render_kpi_row(study)
    _render_car_timeline(study)
    _render_per_event_scatter(study["study_id"])
    _render_study_detail(study)


# ── Tabs ───────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "1. Reg Shocks",
    "2. EO Impact",
    "3. Pipeline",
    "4. Tariff Asymmetry",
    "5. Macro Regime",
])


# ── Tab 1: Regulatory Intensity Shocks ─────────────────────────────

with tab1:
    st.subheader("Report 1: Regulatory Intensity Shocks & Sector Volatility")
    st.markdown(
        "Tests whether abnormal surges in agency-level regulatory activity "
        "predict sector volatility. Expands the 2-agency detector to all agencies."
    )

    studies1 = _load_studies("report1_")

    if st.button("Run Report 1", key="run_report1"):
        with st.spinner("Running regulatory shock analysis..."):
            try:
                from analysis.research.report1_reg_shocks import run_report
                result = run_report(DB_PATH)
                result.save_all_to_db(DB_PATH)
                st.success(f"Report 1 complete: {len(result.event_studies)} sub-studies")
                st.rerun()
            except Exception as e:
                st.error(f"Report 1 failed: {e}")

    _render_study_section(studies1, "report1")

    # Agency heatmap
    if not studies1.empty:
        agency_studies = studies1[studies1["study_name"].str.contains("report1_")]
        if len(agency_studies) > 1:
            with st.expander("Agency CAR Comparison"):
                chart_data = agency_studies[["study_name", "mean_car", "num_events", "p_value"]].copy()
                chart_data["mean_car_pct"] = chart_data["mean_car"] * 100
                chart_data["agency"] = chart_data["study_name"].str.replace("report1_", "").str.replace("_", " ").str.title()
                fig = px.bar(
                    chart_data, x="agency", y="mean_car_pct",
                    color="p_value", color_continuous_scale="RdYlGn_r",
                    hover_data=["num_events", "p_value"],
                    title="Mean CAR by Agency (color = p-value)",
                    labels={"mean_car_pct": "Mean CAR (%)", "agency": "Agency"},
                )
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)


# ── Tab 2: Executive Order Market Impact ───────────────────────────

with tab2:
    st.subheader("Report 2: Executive Order Market Impact")
    st.markdown(
        "Topic-conditional event study on 827 EOs. Tests whether market impact "
        "varies by topic (tariff, defense, energy, etc.) and administration."
    )

    studies2 = _load_studies("report2_")

    if st.button("Run Report 2", key="run_report2"):
        with st.spinner("Running EO impact analysis..."):
            try:
                from analysis.research.report2_eo_impact import run_report
                result = run_report(DB_PATH)
                result.save_all_to_db(DB_PATH)
                st.success(f"Report 2 complete: {len(result.event_studies)} sub-studies")
                st.rerun()
            except Exception as e:
                st.error(f"Report 2 failed: {e}")

    _render_study_section(studies2, "report2")

    # Topic comparison
    if not studies2.empty:
        topic_studies = studies2[studies2["study_name"].str.contains("topic_")]
        if len(topic_studies) > 1:
            with st.expander("Topic CAR Comparison"):
                chart_data = topic_studies[["study_name", "mean_car", "num_events"]].copy()
                chart_data["mean_car_pct"] = chart_data["mean_car"] * 100
                chart_data["topic"] = chart_data["study_name"].str.extract(r"topic_(\w+)")
                fig = px.bar(
                    chart_data, x="topic", y="mean_car_pct",
                    color="mean_car_pct", color_continuous_scale="RdYlGn",
                    hover_data=["num_events"],
                    title="Mean CAR by EO Topic",
                    labels={"mean_car_pct": "Mean CAR (%)", "topic": "Topic"},
                )
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)


# ── Tab 3: Regulatory Pipeline ─────────────────────────────────────

with tab3:
    st.subheader("Report 3: Regulatory Pipeline as Sector Rotation Signal")
    st.markdown(
        "Tests whether the pipeline of proposed rules (without final resolution) "
        "predicts sector rotation. Compares CAR at three stages: proposed rule, "
        "comment deadline, and final rule."
    )

    studies3 = _load_studies("report3_")

    if st.button("Run Report 3", key="run_report3"):
        with st.spinner("Running pipeline analysis..."):
            try:
                from analysis.research.report3_reg_pipeline import run_report
                result = run_report(DB_PATH)
                result.save_all_to_db(DB_PATH)
                st.success(f"Report 3 complete: {len(result.event_studies)} sub-studies")
                st.rerun()
            except Exception as e:
                st.error(f"Report 3 failed: {e}")

    _render_study_section(studies3, "report3")

    # Three-stage comparison
    if not studies3.empty:
        stage_names = ["proposed_rule", "comment_deadline", "final_rule"]
        stage_studies = studies3[studies3["study_name"].str.contains("|".join(stage_names))]
        if len(stage_studies) >= 2:
            with st.expander("Three-Stage CAR Comparison"):
                chart_data = stage_studies[["study_name", "mean_car", "num_events", "p_value"]].copy()
                chart_data["mean_car_pct"] = chart_data["mean_car"] * 100
                chart_data["stage"] = chart_data["study_name"].str.extract(r"(proposed_rule|comment_deadline|final_rule)")
                fig = px.bar(
                    chart_data, x="stage", y="mean_car_pct",
                    color="stage",
                    hover_data=["num_events", "p_value"],
                    title="Market Reaction at Each Pipeline Stage",
                    labels={"mean_car_pct": "Mean CAR (%)", "stage": "Pipeline Stage"},
                )
                fig.update_layout(height=400, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)


# ── Tab 4: Tariff Asymmetry ────────────────────────────────────────

with tab4:
    st.subheader("Report 4: Tariff Announcement Asymmetry")
    st.markdown(
        "Tests whether tariff imposition announcements cause larger market moves "
        "than tariff relief, and whether the market habituates over escalation cycles."
    )

    studies4 = _load_studies("report4_")

    if st.button("Run Report 4", key="run_report4"):
        with st.spinner("Running tariff asymmetry analysis..."):
            try:
                from analysis.research.report4_tariff_asymmetry import run_report
                result = run_report(DB_PATH)
                result.save_all_to_db(DB_PATH)
                st.success(f"Report 4 complete: {len(result.event_studies)} sub-studies")
                st.rerun()
            except Exception as e:
                st.error(f"Report 4 failed: {e}")

    _render_study_section(studies4, "report4")

    # Imposition vs Relief comparison
    if not studies4.empty:
        imp_study = studies4[studies4["study_name"].str.contains("imposition")]
        rel_study = studies4[studies4["study_name"].str.contains("relief")]
        if not imp_study.empty and not rel_study.empty:
            with st.expander("Imposition vs Relief Comparison"):
                comp_data = pd.DataFrame({
                    "Direction": ["Imposition", "Relief"],
                    "Mean CAR (%)": [
                        imp_study.iloc[0]["mean_car"] * 100,
                        rel_study.iloc[0]["mean_car"] * 100,
                    ],
                    "N Events": [
                        int(imp_study.iloc[0]["num_events"]),
                        int(rel_study.iloc[0]["num_events"]),
                    ],
                    "p-value": [
                        imp_study.iloc[0]["p_value"],
                        rel_study.iloc[0]["p_value"],
                    ],
                })
                st.dataframe(comp_data, use_container_width=True, hide_index=True)

                fig = px.bar(
                    comp_data, x="Direction", y="Mean CAR (%)",
                    color="Direction",
                    color_discrete_map={"Imposition": "#ef4444", "Relief": "#22c55e"},
                    title="Tariff Imposition vs Relief: Mean CAR",
                )
                fig.update_layout(height=350, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)


# ── Tab 5: Macro Regime-Conditional ────────────────────────────────

with tab5:
    st.subheader("Report 5: Macro Regime-Conditional Signal Returns")
    st.markdown(
        "Meta-study that tests whether signal returns from Reports 1-4 vary by "
        "macro regime quadrant (Goldilocks, Reflation, Stagflation, Deflation). "
        "If so, regime-conditional position sizing is justified."
    )

    studies5 = _load_studies("report5_")

    if st.button("Run Report 5", key="run_report5"):
        with st.spinner("Running macro regime meta-study (requires Reports 1-4)..."):
            try:
                from analysis.research.report5_macro_conditional import run_report
                result = run_report(db_path=DB_PATH)
                result.save_all_to_db(DB_PATH)
                st.success(f"Report 5 complete: {len(result.event_studies)} sub-studies")

                # Show recommendations
                if result.recommendations:
                    st.markdown("**Key Findings:**")
                    for rec in result.recommendations:
                        st.markdown(f"- {rec}")
                st.rerun()
            except Exception as e:
                st.error(f"Report 5 failed: {e}")

    _render_study_section(studies5, "report5")

    # Regime box plots
    if not studies5.empty:
        regime_studies = studies5[studies5["study_name"].str.contains("regime_q")]
        if len(regime_studies) >= 2:
            with st.expander("CAR by Macro Regime"):
                chart_data = regime_studies[["study_name", "mean_car", "num_events", "p_value"]].copy()
                chart_data["mean_car_pct"] = chart_data["mean_car"] * 100
                chart_data["regime"] = chart_data["study_name"].str.extract(r"regime_q\d+_(\w+)")
                fig = px.bar(
                    chart_data, x="regime", y="mean_car_pct",
                    color="regime",
                    hover_data=["num_events", "p_value"],
                    title="Mean Signal CAR by Macro Regime",
                    labels={"mean_car_pct": "Mean CAR (%)", "regime": "Regime"},
                    color_discrete_sequence=["#22c55e", "#3b82f6", "#f59e0b", "#ef4444"],
                )
                fig.update_layout(height=400, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)


# ── Run All Button ─────────────────────────────────────────────────

st.markdown("---")
run_all_cols = st.columns([3, 1])
with run_all_cols[1]:
    if st.button("Run All Reports", type="primary"):
        with st.spinner("Running all 5 research reports..."):
            from analysis.research.report1_reg_shocks import run_report as run1
            from analysis.research.report2_eo_impact import run_report as run2
            from analysis.research.report3_reg_pipeline import run_report as run3
            from analysis.research.report4_tariff_asymmetry import run_report as run4
            from analysis.research.report5_macro_conditional import run_report as run5

            results = {}
            for name, func in [("1", run1), ("2", run2), ("3", run3), ("4", run4)]:
                try:
                    r = func(DB_PATH)
                    r.save_all_to_db(DB_PATH)
                    results[name] = r
                    st.toast(f"Report {name} complete")
                except Exception as e:
                    st.error(f"Report {name} failed: {e}")

            # Report 5 uses results from 1-4
            try:
                r5 = run5(
                    report1=results.get("1"),
                    report2=results.get("2"),
                    report3=results.get("3"),
                    report4=results.get("4"),
                    db_path=DB_PATH,
                )
                r5.save_all_to_db(DB_PATH)
                st.toast("Report 5 complete")
            except Exception as e:
                st.error(f"Report 5 failed: {e}")

            st.success("All reports complete!")
            st.rerun()

conn.close()
