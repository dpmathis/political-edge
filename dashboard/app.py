"""Political Edge — Main Streamlit Dashboard."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gzip
import shutil
import sqlite3

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
        required = {"fda_events", "trading_signals", "prediction_markets", "data_collection_log"}
        if not required.issubset(tables):
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

st.markdown("""
**Pages:**
- **Today** — Actionable trading view: signals, catalysts, regime
- **RegWatch** — Regulatory events from Federal Register, Congress, Regulations.gov
- **FDA Catalysts** — Drug approvals, AdCom votes, PDUFA dates
- **Lobbying** — Lobbying disclosure filings and QoQ spending analysis
- **Watchlist** — Combined view of all data for tracked tickers
- **Macro & Fed** — Hedgeye-style regime classifier and FOMC tracker
- **Signals** — Trading signal generation and paper trade execution
- **EO Tracker** — Executive order topic classification with evidence-based signals
- **Settings** — Data collection, backfill, backtesting, and data health
""")

# Show database stats
conn = sqlite3.connect(DB_PATH)
stats = {
    "Regulatory Events": conn.execute("SELECT COUNT(*) FROM regulatory_events").fetchone()[0],
    "FDA Events": conn.execute("SELECT COUNT(*) FROM fda_events").fetchone()[0],
    "Lobbying Filings": conn.execute("SELECT COUNT(*) FROM lobbying_filings").fetchone()[0],
    "Congress Trades": conn.execute("SELECT COUNT(*) FROM congress_trades").fetchone()[0],
    "Trading Signals": conn.execute("SELECT COUNT(*) FROM trading_signals").fetchone()[0],
    "Market Data": conn.execute("SELECT COUNT(*) FROM market_data").fetchone()[0],
}
conn.close()

stat_cols = st.columns(len(stats))
for i, (label, value) in enumerate(stats.items()):
    with stat_cols[i]:
        st.metric(label, f"{value:,}")

total_records = sum(stats.values())
if total_records == 0:
    st.warning(
        "**Database is empty.** Go to the **Settings** page to fetch data."
    )
