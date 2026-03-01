"""Political Edge — Main Streamlit Dashboard."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def _ensure_db():
    """Auto-initialize DB if it doesn't exist (for cloud deployment)."""
    if not os.path.exists(DB_PATH):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with st.spinner("Initializing database for first time..."):
            from scripts.setup_db import main as setup_main
            setup_main()
            from scripts.migrate_phase2 import main as migrate_main
            migrate_main()
    else:
        conn = sqlite3.connect(DB_PATH)
        tables = set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        conn.close()
        if "fda_events" not in tables or "trading_signals" not in tables:
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
    "Market Data Points": conn.execute("SELECT COUNT(*) FROM market_data").fetchone()[0],
    "Watchlist Tickers": conn.execute("SELECT COUNT(*) FROM watchlist WHERE active = 1").fetchone()[0],
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
        "or **Run Backfill** to load historical data. The first collection takes 2-3 minutes."
    )


# ── Helper: run a step safely ─────────────────────────────────────────
def _run_step(label: str, func, *args, **kwargs):
    """Run a collection step with error handling. Returns result or None."""
    try:
        st.write(f"**{label}...**")
        result = func(*args, **kwargs)
        st.write(f"  ✓ {label}: done ({result})")
        return result
    except Exception as e:
        st.write(f"  ✗ {label}: failed — {e}")
        return None


# --- Data Collection Controls ---
st.markdown("---")
st.subheader("Data Collection")

col_collect, col_backfill = st.columns(2)

with col_collect:
    st.markdown("**Collect Latest** — Fetch recent data from all sources.")
    if st.button("Collect Now", type="primary"):
        with st.status("Running collectors — this may take a few minutes...", expanded=True) as status:
            from collectors import federal_register, market_data, fda_calendar
            from collectors import congress, regulations_gov, lobbying, congress_trades
            from collectors import fred_macro, fomc
            from analysis import sector_mapper, impact_scorer

            results = {}

            results["Federal Register"] = _run_step(
                "Federal Register events", federal_register.collect
            )

            results["Sector tagging"] = _run_step(
                "Sector tagging", sector_mapper.tag_all_untagged
            )

            results["Impact scoring"] = _run_step(
                "Impact scoring", impact_scorer.score_all_unscored
            )

            results["Market data"] = _run_step(
                "Market data", market_data.collect
            )

            results["FDA events"] = _run_step(
                "FDA events", fda_calendar.collect_from_regulatory_events
            )

            results["Congress.gov"] = _run_step(
                "Congress.gov", congress.collect
            )

            results["Regulations.gov"] = _run_step(
                "Regulations.gov", regulations_gov.collect
            )

            results["Lobbying filings"] = _run_step(
                "Lobbying filings", lobbying.collect
            )

            results["Congressional trades"] = _run_step(
                "Congressional trades", congress_trades.collect
            )

            results["FRED macro data"] = _run_step(
                "FRED macro data", fred_macro.collect
            )

            results["FOMC events"] = _run_step(
                "FOMC events", fomc.collect
            )

            # Macro regime classification
            try:
                st.write("**Classifying macro regime...**")
                from analysis.macro_regime import classify_current_regime
                regime = classify_current_regime()
                if regime:
                    st.write(f"  ✓ Regime: Q{regime['quadrant']} {regime['label']} ({regime['confidence']} confidence)")
                else:
                    st.write("  ⚠ Insufficient data — run Backfill from 2023 to populate")
            except Exception as e:
                st.write(f"  ✗ Regime classification failed: {e}")

            # Summary
            succeeded = sum(1 for v in results.values() if v is not None)
            failed = sum(1 for v in results.values() if v is None)
            summary = f"Done — {succeeded} collectors succeeded"
            if failed:
                summary += f", {failed} failed"

            st.markdown("---")
            st.markdown(f"### {summary}")
            status.update(label=summary, state="complete" if failed == 0 else "error")

        st.cache_data.clear()
        st.rerun()

with col_backfill:
    st.markdown("**Backfill** — Load historical data. Set start to **2023-01-01** for full macro regime history.")
    bf_col1, bf_col2 = st.columns(2)
    with bf_col1:
        bf_start = st.date_input("Start", value=date(2023, 1, 1), key="bf_start")
    with bf_col2:
        bf_end = st.date_input("End", value=date.today(), key="bf_end")

    if st.button("Run Backfill"):
        with st.status("Running backfill — this will take several minutes...", expanded=True) as status:
            results = {}

            # Federal Register in chunks
            try:
                from collectors import federal_register
                st.write("**Federal Register (chunked)...**")

                total_events = 0
                current = bf_start
                chunk_days = 90
                while current < bf_end:
                    chunk_end = min(current + timedelta(days=chunk_days), bf_end)
                    st.write(f"  Chunk: {current} to {chunk_end}...")
                    try:
                        new = federal_register.backfill(
                            current.isoformat(), chunk_end.isoformat(), max_pages_per_type=50
                        )
                        total_events += new
                    except Exception as e:
                        st.write(f"    ✗ Chunk failed: {e}")
                    current = chunk_end + timedelta(days=1)

                st.write(f"  ✓ Federal Register: {total_events} events")
                results["Federal Register"] = total_events
            except Exception as e:
                st.write(f"  ✗ Federal Register failed: {e}")
                results["Federal Register"] = None

            from analysis import sector_mapper, impact_scorer

            results["Sector tagging"] = _run_step(
                "Sector tagging", sector_mapper.tag_all_untagged
            )

            results["Impact scoring"] = _run_step(
                "Impact scoring", impact_scorer.score_all_unscored
            )

            from collectors import market_data
            results["Market data"] = _run_step(
                "Market data", market_data.collect,
                start_date=bf_start.isoformat(), end_date=bf_end.isoformat()
            )

            from collectors import fda_calendar
            results["FDA events"] = _run_step(
                "FDA events", fda_calendar.collect_from_regulatory_events
            )

            from collectors import congress
            results["Congress.gov"] = _run_step(
                "Congress.gov backfill", congress.backfill,
                bf_start.isoformat(), bf_end.isoformat()
            )

            from collectors import regulations_gov
            results["Regulations.gov"] = _run_step(
                "Regulations.gov backfill", regulations_gov.backfill,
                bf_start.isoformat(), bf_end.isoformat()
            )

            from collectors import lobbying
            results["Lobbying"] = _run_step(
                "Lobbying backfill", lobbying.backfill,
                start_year=bf_start.year, end_year=bf_end.year
            )

            from collectors import fred_macro
            results["FRED macro"] = _run_step(
                "FRED macro backfill", fred_macro.backfill,
                bf_start.isoformat()
            )

            from collectors import fomc
            results["FOMC events"] = _run_step(
                "FOMC events", fomc.collect
            )

            # Macro regime classification
            try:
                st.write("**Classifying macro regime...**")
                from analysis.macro_regime import classify_current_regime
                regime = classify_current_regime()
                if regime:
                    st.write(f"  ✓ Regime: Q{regime['quadrant']} {regime['label']} ({regime['confidence']} confidence)")
                else:
                    st.write("  ⚠ Insufficient data for regime classification")
            except Exception as e:
                st.write(f"  ✗ Regime classification failed: {e}")

            # Summary
            succeeded = sum(1 for v in results.values() if v is not None)
            failed = sum(1 for v in results.values() if v is None)
            summary = f"Backfill done — {succeeded} steps succeeded"
            if failed:
                summary += f", {failed} failed"

            st.markdown("---")
            st.markdown(f"### {summary}")
            status.update(label=summary, state="complete" if failed == 0 else "error")

        st.cache_data.clear()
        st.rerun()

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
                        st.write(f"  ✓ {name}: Mean CAR {result.mean_car:+.2%} (p={result.p_value:.4f})")
                else:
                    result = runner.run_study(selected_study)
                    result.save_to_db()
                    st.write(result.summary())

                status.update(label="Backtests complete!", state="complete")
            except Exception as e:
                st.write(f"  ✗ Backtest failed: {e}")
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
