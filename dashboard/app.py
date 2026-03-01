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

st.markdown("""
**RegWatch** — Track regulatory events and map them to market-tradeable signals.

Use the sidebar to navigate between pages and apply filters.

---

### Quick Start
1. Run `python scripts/run_collectors.py` to fetch the latest data
2. Navigate to **RegWatch** to review regulatory events
3. Use filters to narrow by sector, impact score, or event type
4. Click on events to see details and add your analysis
""")

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
