"""Political Edge — Main Streamlit Dashboard."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        with st.spinner("Initializing database..."):
            from scripts.setup_db import main as setup_main
            setup_main()


_ensure_db()

st.title("Political Edge")
st.subheader("Political & Regulatory Trading Intelligence")

st.markdown(
    "**RegWatch** — Track regulatory events and map them to market-tradeable signals. "
    "Use the sidebar to navigate between pages and apply filters."
)

# Show database stats
import sqlite3

conn = sqlite3.connect(DB_PATH)
stats = {
    "Regulatory Events": conn.execute("SELECT COUNT(*) FROM regulatory_events").fetchone()[0],
    "FDA Events": conn.execute("SELECT COUNT(*) FROM fda_events").fetchone()[0],
    "Market Data Points": conn.execute("SELECT COUNT(*) FROM market_data").fetchone()[0],
    "Watchlist Tickers": conn.execute("SELECT COUNT(*) FROM watchlist WHERE active = 1").fetchone()[0],
    "Event Studies": conn.execute("SELECT COUNT(*) FROM event_studies").fetchone()[0],
}
conn.close()

cols = st.columns(len(stats))
for i, (label, value) in enumerate(stats.items()):
    with cols[i]:
        st.metric(label, f"{value:,}")

# --- Data Collection Controls ---
st.markdown("---")
st.subheader("Data Collection")

col_collect, col_backfill = st.columns(2)

with col_collect:
    st.markdown("**Collect Latest** — Fetch the last 7 days of Federal Register data and current market prices.")
    if st.button("Collect Now", type="primary"):
        with st.status("Running collectors...", expanded=True) as status:
            from collectors import federal_register, market_data
            from analysis import sector_mapper, impact_scorer

            st.write("Fetching Federal Register events...")
            new_events = federal_register.collect()
            st.write(f"  {new_events} new events")

            st.write("Tagging sectors...")
            tagged = sector_mapper.tag_all_untagged()
            st.write(f"  {tagged} events tagged")

            st.write("Scoring impact...")
            scored = impact_scorer.score_all_unscored()
            st.write(f"  {scored} events scored")

            st.write("Fetching market data...")
            rows = market_data.collect()
            st.write(f"  {rows} rows inserted")

            st.write("Extracting FDA events...")
            from collectors import fda_calendar
            fda_count = fda_calendar.collect_from_regulatory_events()
            st.write(f"  {fda_count} FDA events extracted")

            status.update(label="Collection complete!", state="complete")
        st.cache_data.clear()
        st.rerun()

with col_backfill:
    st.markdown("**Backfill** — Load historical data from a custom date range.")
    bf_col1, bf_col2 = st.columns(2)
    with bf_col1:
        from datetime import date, timedelta
        bf_start = st.date_input("Start", value=date(2024, 1, 1), key="bf_start")
    with bf_col2:
        bf_end = st.date_input("End", value=date.today(), key="bf_end")

    if st.button("Run Backfill"):
        with st.status("Running backfill...", expanded=True) as status:
            from collectors import federal_register, market_data
            from analysis import sector_mapper, impact_scorer
            from datetime import timedelta as td

            start = bf_start
            end = bf_end
            chunk_days = 90
            total_events = 0
            current = start

            while current < end:
                chunk_end = min(current + td(days=chunk_days), end)
                st.write(f"Federal Register: {current} to {chunk_end}...")
                new = federal_register.backfill(
                    current.isoformat(), chunk_end.isoformat(), max_pages_per_type=50
                )
                total_events += new
                current = chunk_end + td(days=1)

            st.write(f"  {total_events} total new events")

            st.write("Tagging sectors...")
            tagged = sector_mapper.tag_all_untagged()
            st.write(f"  {tagged} events tagged")

            st.write("Scoring impact...")
            scored = impact_scorer.score_all_unscored()
            st.write(f"  {scored} events scored")

            st.write("Fetching market data...")
            rows = market_data.collect(
                start_date=bf_start.isoformat(), end_date=bf_end.isoformat()
            )
            st.write(f"  {rows} rows inserted")

            st.write("Extracting FDA events...")
            from collectors import fda_calendar
            fda_count = fda_calendar.collect_from_regulatory_events()
            st.write(f"  {fda_count} FDA events extracted")

            status.update(label="Backfill complete!", state="complete")
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
            from analysis.backtest_runner import BacktestRunner
            runner = BacktestRunner()

            if selected_study == "All Studies":
                all_results = runner.run_all()
                for name, result in all_results.items():
                    result.save_to_db()
                    st.write(f"{name}: Mean CAR {result.mean_car:+.2%} (p={result.p_value:.4f})")
            else:
                result = runner.run_study(selected_study)
                result.save_to_db()
                st.write(result.summary())

            status.update(label="Backtests complete!", state="complete")
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
