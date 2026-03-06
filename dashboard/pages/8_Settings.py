"""Settings & Data — Data collection, backfill, backtest controls, and data health."""

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

from config import DB_PATH
from dashboard.collection_logger import log_collection_step

st.title("Settings & Data")
st.caption("Data collection, backfill, backtesting, and data health monitoring")

# ── Data Health ──────────────────────────────────────────────────
st.subheader("Data Freshness")

conn = sqlite3.connect(DB_PATH)

freshness_queries = {
    "Regulatory Events": ("regulatory_events", "publication_date"),
    "FDA Events": ("fda_events", "event_date"),
    "Lobbying Filings": ("lobbying_filings", "filing_year"),
    "Congress Trades": ("congress_trades", "trade_date"),
    "Macro Indicators": ("macro_indicators", "date"),
    "FOMC Events": ("fomc_events", "event_date"),
    "Market Data": ("market_data", "date"),
    "Trading Signals": ("trading_signals", "signal_date"),
    "Prediction Markets": ("prediction_markets", "last_updated"),
}

fresh_cols = st.columns(4)
for i, (label, (table, col)) in enumerate(freshness_queries.items()):
    with fresh_cols[i % 4]:
        try:
            row = conn.execute(f"SELECT MAX({col}), COUNT(*) FROM {table}").fetchone()
            latest = row[0] or "No data"
            count = row[1]
            st.metric(label, f"{count:,}", f"Latest: {latest}")
        except Exception:
            st.metric(label, "Error")

conn.close()

# ── Data Collection ──────────────────────────────────────────────
st.markdown("---")
st.subheader("Data Collection")

COLLECT_STEPS = [
    ("Federal Register", "collectors.federal_register", "collect", {}),
    ("Sector Tagging", "analysis.sector_mapper", "tag_all_untagged", {}),
    ("Impact Scoring", "analysis.impact_scorer", "score_all_unscored", {}),
    ("Market Data", "collectors.market_data", "collect", {}),
    ("FDA Events", "collectors.fda_calendar", "collect_from_regulatory_events", {}),
    ("Congress.gov", "collectors.congress", "collect", {}),
    ("Regulations.gov", "collectors.regulations_gov", "collect", {}),
    ("Lobbying Filings", "collectors.lobbying", "collect", {}),
    ("USASpending", "collectors.usaspending", "collect", {}),
    ("Congress Trades", "collectors.congress_trades", "collect", {}),
    ("FRED Macro Data", "collectors.fred_macro", "collect", {}),
    ("FOMC Events", "collectors.fomc", "collect", {}),
    ("Prediction Markets", "collectors.polymarket", "collect", {}),
]


def _run_fda_enrichment():
    """Run FDA event enrichment (drug/company/ticker matching + openFDA)."""
    from scripts.enrich_fda_events import enrich_existing_records, fetch_openfda_approvals, _load_pharma_lookup
    lookup = _load_pharma_lookup()
    fda_conn = sqlite3.connect(DB_PATH)
    try:
        enriched = enrich_existing_records(fda_conn, lookup)
        new_approvals = fetch_openfda_approvals(fda_conn, lookup)
        return enriched + new_approvals
    finally:
        fda_conn.close()


def _run_collection(steps, progress_bar, status_text):
    """Run a list of collection steps with progress tracking and logging."""
    results = {}
    # +4 extra steps: FDA enrichment, pipeline builder, regime, signals
    total = len(steps) + 4

    for i, (label, module_path, func_name, kwargs) in enumerate(steps):
        pct = i / total
        progress_bar.progress(pct, text=f"({i+1}/{total}) {label}...")
        status_text.write(f"**{label}...**")

        try:
            mod = importlib.import_module(module_path)
            func = getattr(mod, func_name)
            log_conn = sqlite3.connect(DB_PATH)
            try:
                result = log_collection_step(log_conn, label, func, **kwargs)
            finally:
                log_conn.close()
            status_text.write(f"  :white_check_mark: {label}: **{result}**")
            results[label] = result
        except Exception as e:
            status_text.write(f"  :x: {label}: {e}")
            results[label] = None

    step_idx = len(steps)

    # FDA Enrichment
    step_idx += 1
    progress_bar.progress((step_idx - 1) / total, text=f"({step_idx}/{total}) FDA Enrichment...")
    status_text.write("**FDA Enrichment...**")
    try:
        log_conn = sqlite3.connect(DB_PATH)
        try:
            enrichment_result = log_collection_step(log_conn, "FDA Enrichment", _run_fda_enrichment)
        finally:
            log_conn.close()
        status_text.write(f"  :white_check_mark: FDA Enrichment: **{enrichment_result} records**")
    except Exception as e:
        status_text.write(f"  :x: FDA Enrichment: {e}")

    # Macro regime classification
    step_idx += 1
    progress_bar.progress((step_idx - 1) / total, text=f"({step_idx}/{total}) Classifying regime...")
    status_text.write("**Classifying macro regime...**")
    try:
        from analysis.macro_regime import classify_current_regime
        log_conn = sqlite3.connect(DB_PATH)
        try:
            regime = log_collection_step(log_conn, "macro_regime", classify_current_regime)
        finally:
            log_conn.close()
        if regime:
            status_text.write(
                f"  :white_check_mark: Regime: **Q{regime['quadrant']} {regime['label']}** "
                f"({regime['confidence']} confidence)"
            )
        else:
            status_text.write("  :warning: Insufficient data for regime classification")
    except Exception as e:
        status_text.write(f"  :x: Regime: {e}")

    # Pipeline builder
    step_idx += 1
    progress_bar.progress((step_idx - 1) / total, text=f"({step_idx}/{total}) Building pipeline...")
    status_text.write("**Building regulatory pipeline...**")
    try:
        from analysis.pipeline_builder import build_pipeline, refresh_statuses
        log_conn = sqlite3.connect(DB_PATH)
        try:
            pipeline_result = log_collection_step(log_conn, "pipeline_builder", build_pipeline)
        finally:
            log_conn.close()
        changed = refresh_statuses()
        status_text.write(
            f"  :white_check_mark: Pipeline: **{pipeline_result['matched']} matched, "
            f"{pipeline_result['pending']} pending, {changed} refreshed**"
        )
    except Exception as e:
        status_text.write(f"  :x: Pipeline: {e}")

    # Signal generation
    step_idx += 1
    progress_bar.progress((step_idx - 1) / total, text=f"({step_idx}/{total}) Generating signals...")
    status_text.write("**Generating trading signals...**")
    try:
        from analysis.signal_generator import generate_signals
        log_conn = sqlite3.connect(DB_PATH)
        try:
            new_signals = log_collection_step(log_conn, "signal_generator", generate_signals)
        finally:
            log_conn.close()
        status_text.write(f"  :white_check_mark: Signals: **{len(new_signals)} new**")
    except Exception as e:
        status_text.write(f"  :x: Signals: {e}")

    progress_bar.progress(1.0, text="Complete!")
    return results


col_collect, col_backfill = st.columns(2)

with col_collect:
    st.markdown("**Collect Latest** — Fetch recent data from all sources.")
    if st.button("Collect Now", type="primary"):
        progress_bar = st.progress(0, text="Starting collectors...")
        status_area = st.container()

        with status_area:
            results = _run_collection(COLLECT_STEPS, progress_bar, st)

        succeeded = sum(1 for v in results.values() if v is not None)
        failed = sum(1 for v in results.values() if v is None)
        if failed == 0:
            st.success(f"All {succeeded} collectors completed successfully!")
        else:
            st.warning(f"{succeeded} succeeded, {failed} failed")

        st.cache_data.clear()

with col_backfill:
    st.markdown("**Backfill** — Load historical data from **2023-01-01** for full regime history.")
    bf_col1, bf_col2 = st.columns(2)
    with bf_col1:
        bf_start = st.date_input("Start", value=date(2023, 1, 1), key="bf_start")
    with bf_col2:
        bf_end = st.date_input("End", value=date.today(), key="bf_end")

    if st.button("Run Backfill"):
        progress_bar = st.progress(0, text="Starting backfill...")

        backfill_steps = [
            ("Federal Register", "collectors.federal_register", "backfill",
             {"start_date": bf_start.isoformat(), "end_date": bf_end.isoformat(), "max_pages_per_type": 20}),
            ("Sector Tagging", "analysis.sector_mapper", "tag_all_untagged", {}),
            ("Impact Scoring", "analysis.impact_scorer", "score_all_unscored", {}),
            ("Market Data", "collectors.market_data", "collect",
             {"start_date": bf_start.isoformat(), "end_date": bf_end.isoformat()}),
            ("FDA Events", "collectors.fda_calendar", "collect_from_regulatory_events", {}),
            ("Congress.gov", "collectors.congress", "backfill",
             {"start_date": bf_start.isoformat(), "end_date": bf_end.isoformat()}),
            ("Regulations.gov", "collectors.regulations_gov", "backfill",
             {"start_date": bf_start.isoformat(), "end_date": bf_end.isoformat()}),
            ("Lobbying", "collectors.lobbying", "backfill",
             {"start_year": bf_start.year, "end_year": bf_end.year}),
            ("FRED Macro", "collectors.fred_macro", "backfill",
             {"since_date": bf_start.isoformat()}),
            ("FOMC Events", "collectors.fomc", "collect", {}),
            ("USASpending", "collectors.usaspending", "collect", {}),
        ]

        results = _run_collection(backfill_steps, progress_bar, st)

        succeeded = sum(1 for v in results.values() if v is not None)
        failed = sum(1 for v in results.values() if v is None)
        if failed == 0:
            st.success(f"Backfill complete! All {succeeded} steps succeeded.")
        else:
            st.warning(f"Backfill: {succeeded} succeeded, {failed} failed")

        st.cache_data.clear()

# ── Backtesting ──────────────────────────────────────────────────
st.markdown("---")
st.subheader("Backtesting")
st.markdown("Run hypothesis backtests to validate whether political signals predict returns.")

bt_col1, bt_col2 = st.columns([1, 2])
with bt_col1:
    study_options = ["All Studies", "tariff_sectors", "contract_awards", "fda_adcom", "high_impact_regulatory"]
    selected_study = st.selectbox("Study", study_options)

    if st.button("Run Backtest"):
        with st.status("Running backtests...", expanded=True) as status:
            try:
                from analysis.backtest_runner import BacktestRunner
                runner = BacktestRunner()

                if selected_study == "All Studies":
                    all_results = runner.run_all()
                    for name, result in all_results.items():
                        result.save_to_db()
                        st.write(f"  :white_check_mark: {name}: Mean CAR {result.mean_car:+.2%} (p={result.p_value:.4f})")
                else:
                    result = runner.run_study(selected_study)
                    result.save_to_db()
                    st.write(result.summary())

                status.update(label="Backtests complete!", state="complete")
            except Exception as e:
                st.write(f"  :x: Backtest failed: {e}")
                status.update(label="Backtest failed", state="error")
        st.cache_data.clear()

with bt_col2:
    bt_conn = sqlite3.connect(DB_PATH)
    try:
        recent_studies = bt_conn.execute(
            """SELECT study_name, num_events, mean_car, p_value, win_rate, created_at
               FROM event_studies ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()
    except Exception:
        recent_studies = []
    bt_conn.close()

    if recent_studies:
        bt_df = pd.DataFrame(recent_studies, columns=["Study", "Events", "Mean CAR", "p-value", "Win Rate", "Run Date"])

        # Significance stars
        def _sig_stars(p):
            if p is None:
                return ""
            if p < 0.01:
                return "***"
            if p < 0.05:
                return "**"
            if p < 0.10:
                return "*"
            return ""

        # Color-coded Mean CAR with significance
        bt_df["Mean CAR"] = bt_df.apply(
            lambda r: f"{r['Mean CAR']:+.2%} {_sig_stars(r['p-value'])}" if r["Mean CAR"] else "",
            axis=1,
        )
        bt_df["Win Rate"] = bt_df["Win Rate"].apply(lambda x: f"{x:.1%}" if x else "")
        bt_df["p-value"] = bt_df["p-value"].apply(lambda x: f"{x:.4f}" if x else "")
        st.dataframe(bt_df, use_container_width=True, hide_index=True)
        st.caption("Significance: \\* p<0.10, \\*\\* p<0.05, \\*\\*\\* p<0.01")

# ── Collection History ────────────────────────────────────────────
st.markdown("---")
st.subheader("Collection History")

hist_conn = sqlite3.connect(DB_PATH)
try:
    hist_rows = hist_conn.execute(
        """SELECT collector_name, status, records_added, errors, started_at
           FROM data_collection_log
           ORDER BY started_at DESC LIMIT 20"""
    ).fetchall()
except Exception:
    hist_rows = []
hist_conn.close()

if hist_rows:
    hist_df = pd.DataFrame(hist_rows, columns=["Collector", "Status", "Records", "Error", "Time"])
    hist_df["Status"] = hist_df["Status"].apply(
        lambda s: f"{'✅' if s == 'success' else '❌' if s == 'error' else '🔄'} {s}"
    )
    hist_df["Error"] = hist_df["Error"].apply(lambda x: (x[:80] + "...") if x and len(x) > 80 else (x or ""))
    st.dataframe(hist_df, use_container_width=True, hide_index=True)
else:
    st.info("No collection history yet. Run 'Collect Now' above to populate.")

# ── Signal & Trade Management ────────────────────────────────────
st.markdown("---")
st.subheader("Signal & Trade Management")

mgmt_col1, mgmt_col2 = st.columns(2)

with mgmt_col1:
    st.markdown("**Signal Generation**")
    if st.button("Generate Signals", type="primary", key="settings_gen_signals"):
        with st.spinner("Generating signals..."):
            try:
                from analysis.signal_generator import generate_signals
                new_signals = generate_signals()
                st.success(f"Generated {len(new_signals)} new signals")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Signal generation failed: {e}")
    if st.button("Review Active Signals", key="settings_review_signals"):
        with st.spinner("Reviewing active signals..."):
            try:
                from analysis.signal_generator import review_active_signals
                closed = review_active_signals()
                st.success(f"Closed {closed} signals (exit conditions met)")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Signal review failed: {e}")

with mgmt_col2:
    st.markdown("**Trade Reconciliation**")
    if st.button("Reconcile Trades", key="settings_reconcile"):
        with st.spinner("Reconciling with Alpaca..."):
            try:
                from execution.paper_trader import PaperTrader
                trader = PaperTrader()
                updated = trader.reconcile_trades()
                st.success(f"Reconciled {updated} trades")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Reconciliation failed: {e}")
    if st.button("Close Expired Positions", key="settings_close_expired"):
        with st.spinner("Closing expired positions..."):
            try:
                from execution.paper_trader import PaperTrader
                trader = PaperTrader()
                closed = trader.close_expired_positions()
                st.success(f"Closed {closed} expired positions")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Failed: {e}")

# ── Data Sources ─────────────────────────────────────────────────
st.markdown("---")
st.subheader("Data Sources")

sources = [
    {"Source": "Federal Register", "API": "api.federalregister.gov", "Key Required": "No", "Table": "regulatory_events", "Frequency": "Daily"},
    {"Source": "Congress.gov", "API": "api.congress.gov/v3", "Key Required": "Yes", "Table": "regulatory_events", "Frequency": "Daily"},
    {"Source": "Regulations.gov", "API": "api.regulations.gov/v4", "Key Required": "Yes", "Table": "regulatory_events", "Frequency": "Daily"},
    {"Source": "FDA Calendar", "API": "Federal Register (keyword)", "Key Required": "No", "Table": "fda_events", "Frequency": "Daily"},
    {"Source": "openFDA", "API": "api.fda.gov", "Key Required": "No", "Table": "fda_events", "Frequency": "Weekly"},
    {"Source": "Lobbying (LDA)", "API": "lda.gov/api/v1", "Key Required": "No", "Table": "lobbying_filings", "Frequency": "Quarterly"},
    {"Source": "Capitol Trades", "API": "capitoltrades.com (scrape)", "Key Required": "No", "Table": "congress_trades", "Frequency": "Daily"},
    {"Source": "FRED", "API": "api.stlouisfed.org", "Key Required": "Yes", "Table": "macro_indicators", "Frequency": "Varies"},
    {"Source": "FOMC", "API": "federalreserve.gov", "Key Required": "No", "Table": "fomc_events", "Frequency": "~8x/year"},
    {"Source": "Yahoo Finance", "API": "yfinance library", "Key Required": "No", "Table": "market_data", "Frequency": "Daily"},
    {"Source": "USASpending", "API": "api.usaspending.gov/v2", "Key Required": "No", "Table": "contract_awards", "Frequency": "Weekly"},
    {"Source": "Polymarket", "API": "gamma-api.polymarket.com", "Key Required": "No", "Table": "prediction_markets", "Frequency": "Daily"},
]

st.dataframe(pd.DataFrame(sources), use_container_width=True, hide_index=True)
