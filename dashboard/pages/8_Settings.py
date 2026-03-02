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
    ("Congress Trades", "collectors.congress_trades", "collect", {}),
    ("FRED Macro Data", "collectors.fred_macro", "collect", {}),
    ("FOMC Events", "collectors.fomc", "collect", {}),
    ("Prediction Markets", "collectors.polymarket", "collect", {}),
]


def _run_collection(steps, progress_bar, status_text):
    """Run a list of collection steps with progress tracking."""
    results = {}
    total = len(steps) + 2  # +1 for regime classification, +1 for signal generation

    for i, (label, module_path, func_name, kwargs) in enumerate(steps):
        pct = i / total
        progress_bar.progress(pct, text=f"({i+1}/{total}) {label}...")
        status_text.write(f"**{label}...**")

        try:
            mod = importlib.import_module(module_path)
            func = getattr(mod, func_name)
            result = func(**kwargs)
            status_text.write(f"  :white_check_mark: {label}: **{result}**")
            results[label] = result
        except Exception as e:
            status_text.write(f"  :x: {label}: {e}")
            results[label] = None

    # Macro regime classification
    step_num = len(steps) + 1
    progress_bar.progress((step_num - 1) / total, text=f"({step_num}/{total}) Classifying regime...")
    status_text.write("**Classifying macro regime...**")
    try:
        from analysis.macro_regime import classify_current_regime
        regime = classify_current_regime()
        if regime:
            status_text.write(
                f"  :white_check_mark: Regime: **Q{regime['quadrant']} {regime['label']}** "
                f"({regime['confidence']} confidence)"
            )
        else:
            status_text.write("  :warning: Insufficient data for regime classification")
    except Exception as e:
        status_text.write(f"  :x: Regime: {e}")

    # Signal generation
    step_num = len(steps) + 2
    progress_bar.progress((step_num - 1) / total, text=f"({step_num}/{total}) Generating signals...")
    status_text.write("**Generating trading signals...**")
    try:
        from analysis.signal_generator import generate_signals
        new_signals = generate_signals()
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
    {"Source": "Polymarket", "API": "gamma-api.polymarket.com", "Key Required": "No", "Table": "prediction_markets", "Frequency": "Daily"},
]

st.dataframe(pd.DataFrame(sources), use_container_width=True, hide_index=True)
