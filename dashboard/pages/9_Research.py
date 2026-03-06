"""Research Reports — Formal event studies with statistical rigor."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH
from dashboard.components.glossary import inject_tooltip_css
from dashboard.components.research_charts import (
    render_study_section,
)

from dashboard.components.freshness import render_freshness

st.title("Research Reports")
st.caption("Formal event studies validating trading signals with statistical rigor")
inject_tooltip_css()
render_freshness("event_studies", "created_at", "Event Studies")

conn = sqlite3.connect(DB_PATH)


# ── Helpers ────────────────────────────────────────────────────────


def _load_studies(prefix: str) -> pd.DataFrame:
    """Load event studies matching a name prefix."""
    try:
        return pd.read_sql_query(
            """SELECT id, study_name, hypothesis, benchmark,
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


@st.cache_data(ttl=60)
def _load_report_metadata(report_number: int) -> dict:
    """Load persisted additional_analyses + recommendations for a report."""
    try:
        meta_conn = sqlite3.connect(DB_PATH)
        row = meta_conn.execute(
            "SELECT additional_analyses_json, recommendations_json FROM research_reports WHERE report_number = ?",
            (report_number,),
        ).fetchone()
        meta_conn.close()
        if not row:
            return {}
        result = {}
        if row[0]:
            result["additional_analyses"] = json.loads(row[0])
        if row[1]:
            result["recommendations"] = json.loads(row[1])
        return result
    except Exception:
        return {}


def _render_study_section_local(studies: pd.DataFrame, prefix: str) -> None:
    """Render study section using shared components with local conn."""
    render_study_section(studies, prefix, conn)


def _render_recommendations(recommendations: list[str]) -> None:
    """Render a Key Findings expander from recommendations list."""
    if not recommendations:
        return
    with st.expander("Key Findings"):
        for rec in recommendations:
            st.markdown(f"- {rec}")


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

    _render_study_section_local(studies1, "report1")

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

    # Additional analyses from persisted report metadata
    meta1 = _load_report_metadata(1)
    if meta1.get("additional_analyses"):
        aa1 = meta1["additional_analyses"]

        # Granger Causality
        granger = aa1.get("granger_causality", {})
        if granger and not granger.get("error"):
            with st.expander("Granger Causality Tests"):
                gc_rows = []
                for agency, gc_result in granger.items():
                    if isinstance(gc_result, dict) and not gc_result.get("error"):
                        gc_rows.append({
                            "Agency": agency,
                            "Ticker Proxy": gc_result.get("ticker", "N/A"),
                            "Best Lag": gc_result.get("best_lag", "N/A"),
                            "F-stat": f"{gc_result['f_statistic']:.2f}" if "f_statistic" in gc_result else "N/A",
                            "p-value": f"{gc_result['p_value']:.4f}" if "p_value" in gc_result else "N/A",
                            "Significant": "Yes" if gc_result.get("significant") else "No",
                        })
                if gc_rows:
                    st.dataframe(pd.DataFrame(gc_rows), use_container_width=True, hide_index=True)

        # Out-of-Sample Validation
        oos = aa1.get("out_of_sample", {})
        comparison = oos.get("comparison") if isinstance(oos, dict) else None
        if comparison and isinstance(comparison, dict):
            with st.expander("Out-of-Sample Validation"):
                oos_rows = []
                for period, stats in comparison.items():
                    if isinstance(stats, dict) and "mean_car" in stats:
                        oos_rows.append({
                            "Period": period,
                            "N Events": stats.get("num_events", "N/A"),
                            "Mean CAR": f"{stats['mean_car']:+.2%}" if stats.get("mean_car") is not None else "N/A",
                            "p-value": f"{stats['p_value']:.4f}" if stats.get("p_value") is not None else "N/A",
                            "Significant": "Yes" if stats.get("significant") else "No",
                        })
                if oos_rows:
                    st.dataframe(pd.DataFrame(oos_rows), use_container_width=True, hide_index=True)

        _render_recommendations(meta1.get("recommendations", []))


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

    _render_study_section_local(studies2, "report2")

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

    # Additional analyses from persisted report metadata
    meta2 = _load_report_metadata(2)
    if meta2.get("additional_analyses"):
        aa2 = meta2["additional_analyses"]

        # Biden vs Trump comparison
        admin_comp = aa2.get("administration_comparison", {})
        if admin_comp and not admin_comp.get("error"):
            with st.expander("Biden vs Trump CAR Comparison"):
                admin_cols = st.columns(3)
                with admin_cols[0]:
                    val = admin_comp.get("mean_biden")
                    st.metric("Biden Mean CAR", f"{val:+.2%}" if val is not None else "N/A",
                              help=f"N={admin_comp.get('n_biden', 'N/A')}")
                with admin_cols[1]:
                    val = admin_comp.get("mean_trump")
                    st.metric("Trump Mean CAR", f"{val:+.2%}" if val is not None else "N/A",
                              help=f"N={admin_comp.get('n_trump', 'N/A')}")
                with admin_cols[2]:
                    p = admin_comp.get("p_value")
                    st.metric("t-test p-value", f"{p:.4f}" if p is not None else "N/A")

                comp_df = pd.DataFrame({
                    "Administration": ["Biden", "Trump"],
                    "Mean CAR (%)": [
                        (admin_comp.get("mean_biden") or 0) * 100,
                        (admin_comp.get("mean_trump") or 0) * 100,
                    ],
                })
                fig = px.bar(
                    comp_df, x="Administration", y="Mean CAR (%)",
                    color="Administration",
                    color_discrete_map={"Biden": "#3b82f6", "Trump": "#ef4444"},
                    title="Mean CAR by Administration",
                )
                fig.update_layout(height=300, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

        # Cross-Sectional Regression
        xreg = aa2.get("cross_sectional_regression", {})
        if xreg and not xreg.get("error"):
            with st.expander("Cross-Sectional Regression"):
                reg_cols = st.columns(4)
                with reg_cols[0]:
                    st.metric("R²", f"{xreg.get('r_squared', 0):.3f}")
                with reg_cols[1]:
                    st.metric("Adj R²", f"{xreg.get('adj_r_squared', 0):.3f}")
                with reg_cols[2]:
                    st.metric("N", f"{xreg.get('n_obs', 0)}")
                with reg_cols[3]:
                    st.metric("F-stat", f"{xreg.get('f_statistic', 0):.2f}")

                coeffs = xreg.get("coefficients", {})
                p_vals = xreg.get("p_values", {})
                t_stats = xreg.get("t_statistics", {})
                if coeffs:
                    coeff_rows = []
                    for var in coeffs:
                        p = p_vals.get(var)
                        stars = "***" if p and p < 0.01 else "**" if p and p < 0.05 else "*" if p and p < 0.10 else ""
                        coeff_rows.append({
                            "Variable": var,
                            "Coefficient": f"{coeffs[var]:.4f}",
                            "t-stat": f"{t_stats.get(var, 0):.2f}",
                            "p-value": f"{p:.4f}{stars}" if p is not None else "N/A",
                        })
                    st.dataframe(pd.DataFrame(coeff_rows), use_container_width=True, hide_index=True)

        _render_recommendations(meta2.get("recommendations", []))


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

    _render_study_section_local(studies3, "report3")

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

    _render_study_section_local(studies4, "report4")

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

    # Additional analyses from persisted report metadata
    meta4 = _load_report_metadata(4)
    if meta4.get("additional_analyses"):
        aa4 = meta4["additional_analyses"]

        # Escalation Decay
        decay = aa4.get("escalation_decay", {})
        if decay and not decay.get("error"):
            with st.expander("Escalation Decay Analysis"):
                decay_cols = st.columns(3)
                with decay_cols[0]:
                    st.metric("Spearman r", f"{decay.get('spearman_r', 0):.3f}")
                with decay_cols[1]:
                    st.metric("p-value", f"{decay.get('p_value', 0):.4f}")
                with decay_cols[2]:
                    st.metric("N", f"{decay.get('n', 0)}")
                if decay.get("significant"):
                    st.success("Significant escalation decay detected — later announcements in a cycle have diminishing market impact.")
                else:
                    st.info("No significant escalation decay found — market impact does not diminish within escalation cycles.")

        # Sector Dispersion
        disp = aa4.get("sector_dispersion", {})
        if disp and not disp.get("error"):
            with st.expander("Sector Dispersion on Tariff Days"):
                disp_data = pd.DataFrame({
                    "Day Type": ["Tariff Days", "Non-Tariff Days"],
                    "Mean Dispersion": [
                        disp.get("tariff_day_mean_dispersion", 0),
                        disp.get("non_tariff_day_mean_dispersion", 0),
                    ],
                })
                fig = px.bar(
                    disp_data, x="Day Type", y="Mean Dispersion",
                    color="Day Type",
                    color_discrete_map={"Tariff Days": "#ef4444", "Non-Tariff Days": "#94a3b8"},
                    title="Cross-Sector Dispersion",
                )
                fig.update_layout(height=300, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
                st.caption(
                    f"t-stat: {disp.get('t_stat', 0):.2f}, "
                    f"p-value: {disp.get('p_value', 0):.4f}, "
                    f"Tariff days N={disp.get('tariff_day_count', 0)}, "
                    f"Non-tariff days N={disp.get('non_tariff_day_count', 0)}"
                )

        # Cross-Cycle Comparison
        cross = aa4.get("cross_cycle", {})
        diff_test = cross.get("difference_test", {}) if isinstance(cross, dict) else {}
        if diff_test and not diff_test.get("error"):
            with st.expander("Cross-Cycle Comparison (2018-2019 vs 2025-2026)"):
                cc_cols = st.columns(4)
                with cc_cols[0]:
                    val = diff_test.get("mean_car_2018")
                    st.metric("2018-2019 Mean CAR", f"{val:+.2%}" if val is not None else "N/A",
                              help=f"N={diff_test.get('n_2018', 'N/A')}")
                with cc_cols[1]:
                    val = diff_test.get("mean_car_2025")
                    st.metric("2025-2026 Mean CAR", f"{val:+.2%}" if val is not None else "N/A",
                              help=f"N={diff_test.get('n_2025', 'N/A')}")
                with cc_cols[2]:
                    st.metric("t-stat", f"{diff_test.get('t_statistic', 0):.2f}")
                with cc_cols[3]:
                    st.metric("p-value", f"{diff_test.get('p_value', 0):.4f}")

        _render_recommendations(meta4.get("recommendations", []))


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

    _render_study_section_local(studies5, "report5")

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

    # Additional analyses from persisted report metadata
    meta5 = _load_report_metadata(5)
    if meta5.get("additional_analyses"):
        aa5 = meta5["additional_analyses"]

        # ANOVA
        anova = aa5.get("anova_by_regime", {})
        if anova and not anova.get("error"):
            with st.expander("ANOVA: CAR by Macro Regime"):
                anova_kpi = st.columns(2)
                with anova_kpi[0]:
                    st.metric("F-statistic", f"{anova.get('f_statistic', 0):.2f}")
                with anova_kpi[1]:
                    p = anova.get("p_value", 1)
                    stars = "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.10 else ""
                    st.metric("p-value", f"{p:.4f} {stars}")

                group_stats = anova.get("group_stats", {})
                if group_stats:
                    gs_rows = []
                    for regime, stats in group_stats.items():
                        if isinstance(stats, dict):
                            gs_rows.append({
                                "Regime": regime,
                                "N": stats.get("count", stats.get("n", "N/A")),
                                "Mean CAR": f"{stats.get('mean', 0):+.2%}" if stats.get("mean") is not None else "N/A",
                                "Std": f"{stats.get('std', 0):.4f}" if stats.get("std") is not None else "N/A",
                            })
                    if gs_rows:
                        st.dataframe(pd.DataFrame(gs_rows), use_container_width=True, hide_index=True)

                tukey = anova.get("tukey_hsd", [])
                if tukey and isinstance(tukey, list):
                    with st.expander("Tukey HSD Pairwise Comparisons", expanded=False):
                        tukey_rows = []
                        for comp in tukey:
                            if isinstance(comp, dict):
                                tukey_rows.append({
                                    "Group 1": comp.get("group1", ""),
                                    "Group 2": comp.get("group2", ""),
                                    "Mean Diff": f"{comp.get('meandiff', 0):+.4f}",
                                    "p-adj": f"{comp.get('p_adj', 0):.4f}",
                                    "Significant": "Yes" if comp.get("reject") else "No",
                                })
                        if tukey_rows:
                            st.dataframe(pd.DataFrame(tukey_rows), use_container_width=True, hide_index=True)

        # Regime Backtest
        backtest = aa5.get("regime_conditional_backtest", {})
        if backtest and not backtest.get("error"):
            ew = backtest.get("equal_weighted", {})
            rw = backtest.get("regime_weighted", {})
            if ew and rw:
                with st.expander("Regime-Conditional Backtest"):
                    bt_cols = st.columns(3)
                    with bt_cols[0]:
                        st.markdown("**Metric**")
                        st.markdown("Sharpe Ratio")
                        st.markdown("Total Return")
                        st.markdown("Max Drawdown")
                    with bt_cols[1]:
                        st.markdown("**Equal-Weighted**")
                        st.markdown(f"{ew.get('sharpe', 0):+.2f}")
                        st.markdown(f"{ew.get('total_return', 0):+.2%}")
                        st.markdown(f"{ew.get('max_drawdown', 0):.2%}")
                    with bt_cols[2]:
                        st.markdown("**Regime-Weighted**")
                        st.markdown(f"{rw.get('sharpe', 0):+.2f}")
                        st.markdown(f"{rw.get('total_return', 0):+.2%}")
                        st.markdown(f"{rw.get('max_drawdown', 0):.2%}")

                    improvement = backtest.get("improvement", {})
                    if improvement:
                        delta = improvement.get("sharpe_delta", 0)
                        if improvement.get("regime_sizing_helps"):
                            st.success(f"Regime-weighted sizing improves Sharpe by {delta:+.2f}")
                        else:
                            st.info(f"Regime-weighted sizing shows Sharpe delta of {delta:+.2f}")

                    # Equity curve chart
                    curves = backtest.get("equity_curves", {})
                    ew_curve = curves.get("equal_weighted", [])
                    rw_curve = curves.get("regime_weighted", [])
                    if ew_curve and rw_curve:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            y=ew_curve, mode="lines", name="Equal-Weighted",
                            line=dict(color="#94a3b8"),
                        ))
                        fig.add_trace(go.Scatter(
                            y=rw_curve, mode="lines", name="Regime-Weighted",
                            line=dict(color="#3b82f6"),
                        ))
                        fig.update_layout(
                            title="Equity Curves",
                            height=350,
                            yaxis_title="Portfolio Value",
                            xaxis_title="Trade #",
                            margin=dict(l=0, r=0, t=30, b=0),
                        )
                        st.plotly_chart(fig, use_container_width=True)

        # VIX Robustness
        vix = aa5.get("vix_regime_robustness", {})
        if vix and not vix.get("error"):
            with st.expander("VIX Regime Robustness Check"):
                vix_cols = st.columns(2)
                with vix_cols[0]:
                    st.metric("F-statistic", f"{vix.get('f_statistic', 0):.2f}")
                with vix_cols[1]:
                    st.metric("p-value", f"{vix.get('p_value', 0):.4f}")

                vix_groups = vix.get("group_stats", {})
                if vix_groups:
                    vg_rows = []
                    for group, stats in vix_groups.items():
                        if isinstance(stats, dict):
                            vg_rows.append({
                                "VIX Level": group,
                                "N": stats.get("count", stats.get("n", "N/A")),
                                "Mean CAR": f"{stats.get('mean', 0):+.2%}" if stats.get("mean") is not None else "N/A",
                                "Std": f"{stats.get('std', 0):.4f}" if stats.get("std") is not None else "N/A",
                            })
                    if vg_rows:
                        st.dataframe(pd.DataFrame(vg_rows), use_container_width=True, hide_index=True)

        # Yield Curve Robustness
        yc = aa5.get("yield_curve_regime_robustness", {})
        if yc and not yc.get("error"):
            with st.expander("Yield Curve Regime Robustness Check"):
                yc_cols = st.columns(2)
                with yc_cols[0]:
                    st.metric("F-statistic", f"{yc.get('f_statistic', 0):.2f}")
                with yc_cols[1]:
                    st.metric("p-value", f"{yc.get('p_value', 0):.4f}")

                yc_groups = yc.get("group_stats", {})
                if yc_groups:
                    ycg_rows = []
                    for group, stats in yc_groups.items():
                        if isinstance(stats, dict):
                            ycg_rows.append({
                                "Yield Curve": group,
                                "N": stats.get("count", stats.get("n", "N/A")),
                                "Mean CAR": f"{stats.get('mean', 0):+.2%}" if stats.get("mean") is not None else "N/A",
                                "Std": f"{stats.get('std', 0):.4f}" if stats.get("std") is not None else "N/A",
                            })
                    if ycg_rows:
                        st.dataframe(pd.DataFrame(ycg_rows), use_container_width=True, hide_index=True)

        _render_recommendations(meta5.get("recommendations", []))


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
