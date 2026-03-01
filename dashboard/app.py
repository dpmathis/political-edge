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
    "Market Data Points": conn.execute("SELECT COUNT(*) FROM market_data").fetchone()[0],
    "Watchlist Tickers": conn.execute("SELECT COUNT(*) FROM watchlist WHERE active = 1").fetchone()[0],
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

            status.update(label="Backfill complete!", state="complete")
        st.cache_data.clear()
        st.rerun()
