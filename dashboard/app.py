"""Political Edge — Main Streamlit Dashboard."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gzip
import shutil
import sqlite3
from datetime import date, timedelta

import streamlit as st

from config import DB_PATH

st.set_page_config(
    page_title="Political Edge",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Database Bootstrap ────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEED_DB_GZ = os.path.join(_PROJECT_ROOT, "data", "seed.db.gz")


def _ensure_db():
    """Auto-initialize DB: decompress seed if available, else create empty."""
    if os.path.exists(DB_PATH):
        # Ensure all tables exist
        conn = sqlite3.connect(DB_PATH)
        tables = set(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall())
        conn.close()
        if "fda_events" not in tables or "trading_signals" not in tables:
            from scripts.migrate_phase2 import main as migrate_main
            migrate_main()
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # Try to decompress seed database (ships with repo for cloud deploy)
    if os.path.exists(_SEED_DB_GZ):
        with st.spinner("Loading pre-populated database..."):
            with gzip.open(_SEED_DB_GZ, "rb") as f_in:
                with open(DB_PATH, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
        return

    # Fallback: create empty database
    with st.spinner("Initializing empty database..."):
        from scripts.setup_db import main as setup_main
        setup_main()
        from scripts.migrate_phase2 import main as migrate_main
        migrate_main()


_ensure_db()

st.title("Political Edge")
st.subheader("Political & Regulatory Trading Intelligence")

st.markdown(
    "Track regulatory events and map them to market-tradeable signals. "
    "Use the sidebar to navigate between pages."
)

# Show database stats
conn = sqlite3.connect(DB_PATH)
stats = {
    "Regulatory Events": conn.execute("SELECT COUNT(*) FROM regulatory_events").fetchone()[0],
    "FDA Events": conn.execute("SELECT COUNT(*) FROM fda_events").fetchone()[0],
    "Lobbying Filings": conn.execute("SELECT COUNT(*) FROM lobbying_filings").fetchone()[0],
    "Congress Trades": conn.execute("SELECT COUNT(*) FROM congress_trades").fetchone()[0],
    "Macro Indicators": conn.execute("SELECT COUNT(*) FROM macro_indicators").fetchone()[0],
    "FOMC Events": conn.execute("SELECT COUNT(*) FROM fomc_events").fetchone()[0],
    "Trading Signals": conn.execute("SELECT COUNT(*) FROM trading_signals").fetchone()[0],
    "Market Data": conn.execute("SELECT COUNT(*) FROM market_data").fetchone()[0],
    "Watchlist": conn.execute("SELECT COUNT(*) FROM watchlist WHERE active = 1").fetchone()[0],
}
conn.close()

stat_items = list(stats.items())
row1 = stat_items[:5]
row2 = stat_items[5:]
cols1 = st.columns(len(row1))
for i, (label, value) in enumerate(row1):
    with cols1[i]:
        st.metric(label, f"{value:,}")
if row2:
    cols2 = st.columns(len(row2))
    for i, (label, value) in enumerate(row2):
        with cols2[i]:
            st.metric(label, f"{value:,}")

total_records = sum(stats.values())
if total_records == 0:
    st.warning(
        "**Database is empty.** Click **Collect Now** below to fetch recent data, "
        "or **Run Backfill** to load historical data."
    )

# ── Collection Steps ──────────────────────────────────────────────────

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
]


def _run_collection(steps, progress_bar, status_text):
    """Run a list of collection steps with progress tracking."""
    results = {}
    total = len(steps) + 1  # +1 for regime classification

    for i, (label, module_path, func_name, kwargs) in enumerate(steps):
        pct = i / total
        progress_bar.progress(pct, text=f"({i+1}/{total}) {label}...")
        status_text.write(f"**{label}...**")

        try:
            import importlib
            mod = importlib.import_module(module_path)
            func = getattr(mod, func_name)
            result = func(**kwargs)
            status_text.write(f"  :white_check_mark: {label}: **{result}**")
            results[label] = result
        except Exception as e:
            status_text.write(f"  :x: {label}: {e}")
            results[label] = None

    # Macro regime classification
    progress_bar.progress((total - 1) / total, text=f"({total}/{total}) Classifying regime...")
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

    progress_bar.progress(1.0, text="Complete!")
    return results


# --- Data Collection Controls ---
st.markdown("---")
st.subheader("Data Collection")

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

# --- Backtesting ---
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
    recent_studies = bt_conn.execute(
        """SELECT study_name, num_events, mean_car, p_value, win_rate, created_at
           FROM event_studies ORDER BY created_at DESC LIMIT 10"""
    ).fetchall()
    bt_conn.close()

    if recent_studies:
        import pandas as pd
        bt_df = pd.DataFrame(recent_studies, columns=["Study", "Events", "Mean CAR", "p-value", "Win Rate", "Run Date"])
        bt_df["Mean CAR"] = bt_df["Mean CAR"].apply(lambda x: f"{x:+.2%}" if x else "")
        bt_df["Win Rate"] = bt_df["Win Rate"].apply(lambda x: f"{x:.1%}" if x else "")
        bt_df["p-value"] = bt_df["p-value"].apply(lambda x: f"{x:.4f}" if x else "")
        st.dataframe(bt_df, use_container_width=True, hide_index=True)
