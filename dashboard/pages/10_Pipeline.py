"""Pipeline Monitor — Track the regulatory pipeline for trading alpha.

Report 3 found that proposed rules generate -0.25% CAR (p=0.016, N=2000).
This page lets retail investors monitor active proposed rules, see historical
evidence, and build what-if scenarios for portfolio impact.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH
from dashboard.components.glossary import inject_tooltip_css, tooltip
from dashboard.components.research_charts import render_study_section

from dashboard.components.freshness import render_freshness

st.title("Pipeline Monitor")
st.caption("Track proposed rules through the regulatory pipeline — where the alpha lives")
inject_tooltip_css()
render_freshness("pipeline_rules", "created_at", "Pipeline Rules")

conn = sqlite3.connect(DB_PATH)


# ── Helpers ────────────────────────────────────────────────────────


def _check_pipeline_table() -> bool:
    """Check if pipeline_rules table exists and has data."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM pipeline_rules"
        ).fetchone()
        return row[0] > 0
    except Exception:
        return False


@st.cache_data(ttl=300)
def _load_pipeline_summary() -> dict:
    """Load aggregate pipeline statistics."""
    c = sqlite3.connect(DB_PATH)
    try:
        total_active = c.execute(
            "SELECT COUNT(*) FROM pipeline_rules WHERE status != 'finalized'"
        ).fetchone()[0]

        thirty_days = (date.today() + timedelta(days=30)).isoformat()
        today_str = date.today().isoformat()
        approaching = c.execute(
            """SELECT COUNT(*) FROM pipeline_rules
               WHERE status IN ('proposed', 'in_comment')
                 AND comment_deadline IS NOT NULL
                 AND comment_deadline <= ?
                 AND comment_deadline >= ?""",
            (thirty_days, today_str),
        ).fetchone()[0]

        avg_days = c.execute(
            "SELECT AVG(days_in_pipeline) FROM pipeline_rules WHERE status != 'finalized'"
        ).fetchone()[0] or 0

        sectors = c.execute(
            "SELECT COUNT(DISTINCT sector) FROM pipeline_rules WHERE status != 'finalized' AND sector != 'Other'"
        ).fetchone()[0]

        status_counts = dict(c.execute(
            "SELECT status, COUNT(*) FROM pipeline_rules GROUP BY status"
        ).fetchall())

        heatmap = pd.read_sql_query(
            """SELECT sector, status, COUNT(*) as count, AVG(impact_score) as avg_impact
               FROM pipeline_rules
               WHERE sector != 'Other'
               GROUP BY sector, status""",
            c,
        )

        return {
            "total_active": total_active,
            "approaching_deadline": approaching,
            "avg_days": avg_days,
            "sectors_affected": sectors,
            "status_counts": status_counts,
            "heatmap_df": heatmap,
        }
    finally:
        c.close()


@st.cache_data(ttl=120)
def _load_active_rules() -> pd.DataFrame:
    """Load all non-finalized pipeline rules with regulatory event details."""
    c = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(
            """SELECT pr.id, pr.proposed_title, pr.agency, pr.sector, pr.tickers,
                      pr.proposed_date, pr.comment_deadline, pr.estimated_final_date,
                      pr.status, pr.days_in_pipeline, pr.impact_score,
                      pr.historical_car, pr.historical_n,
                      pr.proposed_event_id,
                      re.summary, re.url
               FROM pipeline_rules pr
               JOIN regulatory_events re ON pr.proposed_event_id = re.id
               WHERE pr.status != 'finalized'
               ORDER BY pr.comment_deadline ASC NULLS LAST""",
            c,
        )
    finally:
        c.close()


@st.cache_data(ttl=600)
def _load_report3_studies() -> pd.DataFrame:
    """Load Report 3 event studies."""
    c = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(
            """SELECT id, study_name, hypothesis, benchmark,
                      window_pre, window_post, num_events, mean_car,
                      median_car, t_statistic, p_value, sharpe_ratio,
                      win_rate, results_json, created_at
               FROM event_studies
               WHERE study_name LIKE 'report3_%'
               ORDER BY created_at DESC""",
            c,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


@st.cache_data(ttl=600)
def _load_sector_car_breakdown() -> pd.DataFrame:
    """Get per-event CARs grouped by sector from pipeline proposed study."""
    c = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(
            """SELECT pr.sector,
                      COUNT(*) as n_events,
                      AVG(esr.car_full) as mean_car,
                      AVG(CASE WHEN esr.car_full > 0 THEN 1.0 ELSE 0.0 END) as win_rate
               FROM event_study_results esr
               JOIN event_studies es ON esr.study_id = es.id
               JOIN pipeline_rules pr ON esr.event_date = pr.proposed_date
               WHERE es.study_name = 'report3_pipeline_proposed'
                 AND esr.car_full IS NOT NULL
                 AND pr.sector != 'Other'
               GROUP BY pr.sector
               HAVING COUNT(*) >= 10
               ORDER BY mean_car ASC""",
            c,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


@st.cache_data(ttl=600)
def _load_agency_car_breakdown() -> pd.DataFrame:
    """Get per-event CARs grouped by top agencies."""
    c = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(
            """SELECT pr.agency,
                      COUNT(*) as n_events,
                      AVG(esr.car_full) as mean_car,
                      AVG(CASE WHEN esr.car_full > 0 THEN 1.0 ELSE 0.0 END) as win_rate
               FROM event_study_results esr
               JOIN event_studies es ON esr.study_id = es.id
               JOIN pipeline_rules pr ON esr.event_date = pr.proposed_date
               WHERE es.study_name = 'report3_pipeline_proposed'
                 AND esr.car_full IS NOT NULL
               GROUP BY pr.agency
               HAVING COUNT(*) >= 20
               ORDER BY mean_car ASC""",
            c,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


@st.cache_data(ttl=600)
def _load_pipeline_pressure_timeseries() -> pd.DataFrame:
    """Build monthly pipeline pressure from pipeline_rules."""
    c = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(
            """SELECT
                 strftime('%Y-%m', proposed_date) as month,
                 sector,
                 SUM(CASE WHEN status = 'awaiting_final' THEN 1 ELSE 0 END) as pressure,
                 COUNT(*) as total
               FROM pipeline_rules
               WHERE sector != 'Other'
               GROUP BY month, sector
               ORDER BY month""",
            c,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


def _urgency_color(row: pd.Series) -> str:
    """Return urgency color based on days until comment deadline."""
    cd = row.get("comment_deadline")
    if not cd:
        return "#6b7280"  # gray for no deadline
    try:
        days_left = (date.fromisoformat(str(cd)[:10]) - date.today()).days
    except (ValueError, TypeError):
        return "#6b7280"

    if days_left <= 7:
        return "#ef4444"  # red
    elif days_left <= 30:
        return "#f59e0b"  # amber
    else:
        return "#22c55e"  # green


def _urgency_label(row: pd.Series) -> str:
    """Return urgency label."""
    cd = row.get("comment_deadline")
    if not cd:
        return "No deadline"
    try:
        days_left = (date.fromisoformat(str(cd)[:10]) - date.today()).days
    except (ValueError, TypeError):
        return "Unknown"

    if days_left < 0:
        return f"{abs(days_left)}d past"
    elif days_left == 0:
        return "TODAY"
    else:
        return f"{days_left}d left"


# ── Check prerequisites ───────────────────────────────────────────

if not _check_pipeline_table():
    st.warning("Pipeline not built yet. Click below to build it.")
    if st.button("Build Pipeline", type="primary"):
        with st.spinner("Matching proposed rules to final rules..."):
            from analysis.pipeline_builder import build_pipeline
            result = build_pipeline()
            st.success(
                f"Pipeline built: {result['matched']} matched pairs, "
                f"{result['pending']} pending rules"
            )
            st.cache_data.clear()
            st.rerun()
    conn.close()
    st.stop()


# ── Tabs ──────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs([
    "Overview",
    "Active Rules",
    "Historical Evidence",
    "Scenario Builder",
])


# ── Tab 1: Pipeline Overview ─────────────────────────────────────

with tab1:
    summary = _load_pipeline_summary()

    # KPI row
    cols = st.columns(4)
    with cols[0]:
        st.metric("Active Rules", summary["total_active"],
                   help=tooltip("Proposed rules not yet matched to a final rule"))
    with cols[1]:
        st.metric("Approaching Deadline", summary["approaching_deadline"],
                   help="Rules with comment deadline within 30 days")
    with cols[2]:
        st.metric("Avg Days in Pipeline", f"{summary['avg_days']:.0f}",
                   help="Average days since proposed rule was published")
    with cols[3]:
        st.metric("Sectors Affected", summary["sectors_affected"])

    # What the data shows callout
    st.markdown("""
<div style="background:linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
            border-radius:12px; padding:20px 24px; margin:16px 0;
            color:#e2e8f0; line-height:1.6;">
    <div style="font-weight:700; font-size:16px; margin-bottom:8px;">
        What the Data Shows
    </div>
    <div style="font-size:14px;">
        Proposed rules generate a <strong style="color:#f59e0b;">-0.25% abnormal return</strong>
        on average (p=0.016, N=2,000 events). Markets systematically underreact to
        proposed rules — by the time the final rule hits, the move has already started.
        Monitoring this pipeline gives you a head start.
    </div>
</div>
""", unsafe_allow_html=True)

    # Pipeline funnel
    status_counts = summary["status_counts"]
    funnel_stages = ["proposed", "in_comment", "awaiting_final", "finalized"]
    funnel_labels = ["Proposed", "In Comment Period", "Awaiting Final Rule", "Finalized"]
    funnel_values = [status_counts.get(s, 0) for s in funnel_stages]

    if any(funnel_values):
        fig = go.Figure(go.Funnel(
            y=funnel_labels,
            x=funnel_values,
            textinfo="value+percent initial",
            marker=dict(color=["#3b82f6", "#f59e0b", "#ef4444", "#22c55e"]),
        ))
        fig.update_layout(
            title="Pipeline Stage Distribution",
            height=300,
            margin=dict(l=20, r=20, t=40, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Sector heatmap
    heatmap_df = summary["heatmap_df"]
    if not heatmap_df.empty:
        active_heatmap = heatmap_df[heatmap_df["status"] != "finalized"]
        if not active_heatmap.empty:
            pivot = active_heatmap.pivot_table(
                index="sector", columns="status", values="count",
                fill_value=0, aggfunc="sum",
            )
            # Reorder columns
            col_order = [c for c in ["proposed", "in_comment", "awaiting_final"] if c in pivot.columns]
            if col_order:
                pivot = pivot[col_order]
                pivot.columns = [c.replace("_", " ").title() for c in pivot.columns]

                fig = px.imshow(
                    pivot,
                    labels=dict(x="Pipeline Stage", y="Sector", color="Rule Count"),
                    color_continuous_scale="YlOrRd",
                    aspect="auto",
                    title="Active Rules by Sector & Stage",
                )
                fig.update_layout(height=350, margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig, use_container_width=True)

    # Refresh button
    st.markdown("---")
    refresh_cols = st.columns([4, 1])
    with refresh_cols[1]:
        if st.button("Rebuild Pipeline"):
            with st.spinner("Rebuilding pipeline..."):
                from analysis.pipeline_builder import build_pipeline
                result = build_pipeline()
                st.success(f"Rebuilt: {result['matched']} matched, {result['pending']} pending")
                st.cache_data.clear()
                st.rerun()


# ── Tab 2: Active Rules ──────────────────────────────────────────

with tab2:
    st.subheader("Active Proposed Rules")
    st.markdown("Rules currently moving through the regulatory pipeline. Sorted by deadline urgency.")

    active_df = _load_active_rules()

    if active_df.empty:
        st.info("No active rules in pipeline. Build the pipeline first.")
    else:
        # Sidebar filters
        with st.sidebar:
            st.markdown("### Pipeline Filters")
            agencies = sorted(active_df["agency"].dropna().unique().tolist())
            sel_agencies = st.multiselect("Agency", agencies, key="pipe_agency")

            sectors = sorted(active_df["sector"].dropna().unique().tolist())
            sel_sectors = st.multiselect("Sector", sectors, key="pipe_sector")

            impact_range = st.slider(
                "Impact Score", 0, 5,
                (0, 5), key="pipe_impact",
            )

            statuses = sorted(active_df["status"].unique().tolist())
            sel_statuses = st.multiselect("Status", statuses, default=statuses, key="pipe_status")

        # Apply filters
        filtered = active_df.copy()
        if sel_agencies:
            filtered = filtered[filtered["agency"].isin(sel_agencies)]
        if sel_sectors:
            filtered = filtered[filtered["sector"].isin(sel_sectors)]
        filtered = filtered[
            (filtered["impact_score"] >= impact_range[0]) &
            (filtered["impact_score"] <= impact_range[1])
        ]
        if sel_statuses:
            filtered = filtered[filtered["status"].isin(sel_statuses)]

        st.caption(f"Showing {len(filtered)} of {len(active_df)} active rules")

        # Display rules
        for _, row in filtered.iterrows():
            urgency = _urgency_label(row)
            urgency_color = _urgency_color(row)
            impact = int(row["impact_score"])

            # Header with urgency badge
            col1, col2, col3 = st.columns([5, 1, 1])
            with col1:
                title_display = row["proposed_title"][:80] if row["proposed_title"] else "Untitled"
                st.markdown(
                    f"**{title_display}**  \n"
                    f"<small style='color:#9ca3af;'>{row['agency'][:60]}</small>",
                    unsafe_allow_html=True,
                )
            with col2:
                st.markdown(
                    f"<span style='background:{urgency_color}; color:white; "
                    f"padding:2px 8px; border-radius:4px; font-size:12px;'>"
                    f"{urgency}</span>",
                    unsafe_allow_html=True,
                )
            with col3:
                st.markdown(
                    f"<span style='color:#9ca3af; font-size:12px;'>Impact: {impact}/5</span>",
                    unsafe_allow_html=True,
                )

            with st.expander("Details", expanded=False):
                detail_cols = st.columns(4)
                with detail_cols[0]:
                    st.write(f"**Sector:** {row['sector']}")
                    st.write(f"**Tickers:** {row['tickers'] or 'N/A'}")
                with detail_cols[1]:
                    st.write(f"**Proposed:** {row['proposed_date']}")
                    st.write(f"**Comment Deadline:** {row['comment_deadline'] or 'N/A'}")
                with detail_cols[2]:
                    st.write(f"**Est. Final:** {row['estimated_final_date'] or 'N/A'}")
                    st.write(f"**Days in Pipeline:** {row['days_in_pipeline']}")
                with detail_cols[3]:
                    car = row.get("historical_car")
                    if car is not None:
                        st.write(f"**Historical CAR:** {car:+.2%}")
                    else:
                        st.write("**Historical CAR:** -0.25% (avg)")
                    st.write(f"**Status:** {row['status'].replace('_', ' ').title()}")

                summary_text = row.get("summary", "")
                if summary_text:
                    st.markdown(f"<small>{summary_text[:300]}{'...' if len(str(summary_text)) > 300 else ''}</small>",
                               unsafe_allow_html=True)

                url = row.get("url", "")
                if url:
                    st.markdown(f"[View on Federal Register]({url})")

            st.markdown("<hr style='margin:4px 0; border-color:#333;'>", unsafe_allow_html=True)


# ── Tab 3: Historical Evidence ───────────────────────────────────

with tab3:
    st.subheader("Historical Evidence: Report 3 Results")
    st.markdown(
        "Statistical evidence that the regulatory pipeline generates tradeable signals. "
        "Based on 2,000+ proposed rule events matched to market data."
    )

    studies3 = _load_report3_studies()
    if studies3.empty:
        st.info(
            "Report 3 hasn't been run yet. Go to the Research page and run Report 3, "
            "or click below."
        )
        if st.button("Run Report 3", key="run_r3_pipeline"):
            with st.spinner("Running pipeline analysis..."):
                try:
                    from analysis.research.report3_reg_pipeline import run_report
                    result = run_report(DB_PATH)
                    result.save_all_to_db(DB_PATH)
                    st.success(f"Report 3 complete: {len(result.event_studies)} sub-studies")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Report 3 failed: {e}")
    else:
        render_study_section(studies3, "pipe_report3", conn)

    # Sector breakdown
    sector_cars = _load_sector_car_breakdown()
    if not sector_cars.empty:
        st.markdown("### CAR by Sector")
        st.caption("Which sectors react most to proposed rules?")
        sector_cars["mean_car_pct"] = sector_cars["mean_car"] * 100
        fig = px.bar(
            sector_cars, x="sector", y="mean_car_pct",
            color="mean_car_pct", color_continuous_scale="RdYlGn",
            hover_data=["n_events", "win_rate"],
            labels={"mean_car_pct": "Mean CAR (%)", "sector": "Sector"},
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig.update_layout(height=350, margin=dict(l=40, r=20, t=20, b=40))
        st.plotly_chart(fig, use_container_width=True)

    # Agency breakdown
    agency_cars = _load_agency_car_breakdown()
    if not agency_cars.empty:
        st.markdown("### CAR by Agency")
        st.caption("Which agencies' proposed rules have the strongest market signal?")
        agency_cars["mean_car_pct"] = agency_cars["mean_car"] * 100
        agency_cars["agency_short"] = agency_cars["agency"].str[:40]
        fig = px.bar(
            agency_cars.head(15), x="agency_short", y="mean_car_pct",
            color="mean_car_pct", color_continuous_scale="RdYlGn",
            hover_data=["n_events", "win_rate"],
            labels={"mean_car_pct": "Mean CAR (%)", "agency_short": "Agency"},
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig.update_layout(height=400, margin=dict(l=40, r=20, t=20, b=100),
                         xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

    # Pipeline pressure time series
    pressure_df = _load_pipeline_pressure_timeseries()
    if not pressure_df.empty and pressure_df["pressure"].sum() > 0:
        st.markdown("### Pipeline Pressure Over Time")
        st.caption("Monthly count of proposed rules awaiting final resolution by sector")
        fig = px.line(
            pressure_df[pressure_df["pressure"] > 0],
            x="month", y="pressure", color="sector",
            labels={"pressure": "Unresolved Rules", "month": "Month"},
        )
        fig.update_layout(height=350, margin=dict(l=40, r=20, t=20, b=40))
        st.plotly_chart(fig, use_container_width=True)


# ── Tab 4: Scenario Builder ─────────────────────────────────────

with tab4:
    st.subheader("Scenario Builder")
    st.markdown(
        "Select pending proposed rules and model what happens if they finalize. "
        "Based on historical data from Report 3's event studies."
    )

    active_for_scenario = _load_active_rules()

    if active_for_scenario.empty:
        st.info("No active rules to build scenarios from.")
    else:
        # Sort by impact score desc for the selector
        active_for_scenario = active_for_scenario.sort_values("impact_score", ascending=False)

        # Build options
        rule_options = {}
        for _, row in active_for_scenario.iterrows():
            agency_short = str(row["agency"])[:30] if row["agency"] else "Unknown"
            title_short = str(row["proposed_title"])[:50] if row["proposed_title"] else "Untitled"
            rule_options[row["id"]] = (
                f"[Impact {row['impact_score']}] {agency_short} — {title_short}"
            )

        selected_ids = st.multiselect(
            "Select 1-5 proposed rules to analyze",
            options=list(rule_options.keys()),
            format_func=lambda x: rule_options[x],
            max_selections=5,
            key="scenario_rules",
        )

        if selected_ids:
            selected_rules = active_for_scenario[active_for_scenario["id"].isin(selected_ids)]

            # Show detail cards for each selected rule
            st.markdown("### Selected Rules")
            for _, rule in selected_rules.iterrows():
                with st.container():
                    cols = st.columns([3, 1, 1, 1])
                    with cols[0]:
                        st.markdown(f"**{rule['proposed_title'][:60]}**")
                        st.caption(f"{rule['agency'][:50]} | {rule['sector']}")
                    with cols[1]:
                        car = rule.get("historical_car")
                        car_display = f"{car:+.2%}" if car is not None else "-0.25%"
                        st.metric("Hist. CAR", car_display)
                    with cols[2]:
                        n = rule.get("historical_n")
                        st.metric("Precedents", n if n else "2,000+")
                    with cols[3]:
                        est = rule.get("estimated_final_date", "Unknown")
                        if est:
                            try:
                                days_to = (date.fromisoformat(str(est)[:10]) - date.today()).days
                                st.metric("Est. Days to Final", max(0, days_to))
                            except (ValueError, TypeError):
                                st.metric("Est. Days to Final", "N/A")
                        else:
                            st.metric("Est. Days to Final", "N/A")

            st.markdown("---")

            # Scenario toggle
            scenario = st.radio(
                "Scenario",
                ["All selected rules finalize", "All selected rules are withdrawn"],
                horizontal=True,
                key="scenario_type",
            )

            # Compute impact
            st.markdown("### Estimated Impact")

            # Gather affected tickers
            all_tickers = {}
            default_car = -0.0025  # Report 3 average

            for _, rule in selected_rules.iterrows():
                car = rule.get("historical_car") or default_car
                if scenario == "All selected rules are withdrawn":
                    car = -car  # Reversal: uncertainty premium unwinds

                tickers_str = rule.get("tickers", "")
                if tickers_str and isinstance(tickers_str, str):
                    for t in tickers_str.split(","):
                        t = t.strip()
                        if t:
                            all_tickers[t] = all_tickers.get(t, 0) + car
                else:
                    # Fall back to sector
                    sector = rule.get("sector", "")
                    if sector:
                        all_tickers[sector + " (sector)"] = all_tickers.get(sector + " (sector)", 0) + car

            total_car = sum(all_tickers.values())

            # Confidence level
            n_precedents = sum(
                (rule.get("historical_n") or 2000) for _, rule in selected_rules.iterrows()
            )
            if n_precedents >= 100:
                confidence = "High"
                conf_color = "#22c55e"
            elif n_precedents >= 30:
                confidence = "Medium"
                conf_color = "#f59e0b"
            else:
                confidence = "Low"
                conf_color = "#ef4444"

            # Impact KPIs
            impact_cols = st.columns(3)
            with impact_cols[0]:
                direction = "Bearish" if total_car < 0 else "Bullish"
                dir_color = "#ef4444" if total_car < 0 else "#22c55e"
                st.metric(
                    "Expected Total CAR",
                    f"{total_car:+.2%}",
                    delta=direction,
                    delta_color="inverse" if total_car < 0 else "normal",
                )
            with impact_cols[1]:
                st.metric("Rules in Scenario", len(selected_rules))
            with impact_cols[2]:
                st.markdown(
                    f"**Confidence:** <span style='color:{conf_color};'>{confidence}</span>",
                    unsafe_allow_html=True,
                )

            # Per-ticker impact table
            if all_tickers:
                ticker_df = pd.DataFrame([
                    {"Ticker": t, "Expected CAR (%)": v * 100}
                    for t, v in sorted(all_tickers.items(), key=lambda x: x[1])
                ])
                st.dataframe(ticker_df, use_container_width=True, hide_index=True)

            # Sector pressure change
            st.markdown("### Sector Pressure Change")
            sector_impact = {}
            for _, rule in selected_rules.iterrows():
                s = rule.get("sector", "Other")
                if s != "Other":
                    if scenario == "All selected rules finalize":
                        sector_impact[s] = sector_impact.get(s, 0) - 1  # Finalized = pressure decreases
                    else:
                        sector_impact[s] = sector_impact.get(s, 0) - 1  # Withdrawn = pressure decreases too

            if sector_impact:
                for sector, change in sorted(sector_impact.items()):
                    arrow = "decreases" if change < 0 else "increases"
                    st.markdown(f"- **{sector}**: Pipeline pressure {arrow} by {abs(change)} rule(s)")
            else:
                st.caption("No sector-specific pressure changes.")

            # Generate signals button
            st.markdown("---")
            if st.button("Generate Signals for This Scenario", type="primary", key="gen_signals"):
                sig_conn = sqlite3.connect(DB_PATH)
                today_str = date.today().isoformat()
                signal_count = 0

                from analysis.research.base import SECTOR_ETF_ONLY

                for _, rule in selected_rules.iterrows():
                    car = rule.get("historical_car") or default_car
                    if scenario == "All selected rules are withdrawn":
                        car = -car

                    direction = "short" if car < 0 else "long"
                    conviction = "medium" if confidence in ("High", "Medium") else "low"

                    tickers = []
                    tickers_str = rule.get("tickers", "")
                    if tickers_str and isinstance(tickers_str, str):
                        tickers = [t.strip() for t in tickers_str.split(",") if t.strip()]
                    if not tickers:
                        sector = rule.get("sector", "")
                        if sector in SECTOR_ETF_ONLY:
                            tickers = [SECTOR_ETF_ONLY[sector]]
                        else:
                            tickers = ["SPY"]

                    for ticker in tickers[:3]:
                        rationale = (
                            f"Pipeline scenario: {rule['proposed_title'][:60]} "
                            f"({'finalizes' if 'finalize' in scenario else 'withdrawn'}). "
                            f"Historical CAR for similar rules: {car:+.2%}."
                        )
                        try:
                            sig_conn.execute(
                                """INSERT INTO trading_signals
                                   (signal_date, ticker, signal_type, direction, conviction,
                                    source_event_id, source_table, rationale, status,
                                    expected_car, time_horizon_days)
                                   VALUES (?, ?, 'pipeline_scenario', ?, ?, ?, 'pipeline_rules',
                                           ?, 'pending', ?, 20)""",
                                (
                                    today_str, ticker, direction, conviction,
                                    int(rule["proposed_event_id"]),
                                    rationale, car,
                                ),
                            )
                            signal_count += 1
                        except Exception as e:
                            st.warning(f"Failed to create signal for {ticker}: {e}")

                sig_conn.commit()
                sig_conn.close()
                st.success(f"Created {signal_count} pipeline scenario signals. View them on the Signals page.")

conn.close()
