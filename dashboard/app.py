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


def _seed_version() -> str:
    """Return a version string for the current seed.db.gz based on file size."""
    if os.path.exists(_SEED_DB_GZ):
        return str(os.path.getsize(_SEED_DB_GZ))
    return ""


def _ensure_db():
    """Auto-initialize DB: decompress seed if available, else create empty.

    Tracks the seed version via a marker file so the DB is re-decompressed
    whenever seed.db.gz changes (new deploy with updated data).
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    marker_path = DB_PATH + ".seed_version"

    if os.path.exists(_SEED_DB_GZ):
        current_version = _seed_version()
        stored_version = ""
        if os.path.exists(marker_path):
            stored_version = open(marker_path).read().strip()

        if current_version != stored_version or not os.path.exists(DB_PATH):
            # Seed changed or DB missing — re-decompress
            with gzip.open(_SEED_DB_GZ, "rb") as f_in:
                with open(DB_PATH, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            # Run migration for any new tables/columns
            from scripts.migrate_phase2 import main as migrate_main
            migrate_main()
            # Write marker
            with open(marker_path, "w") as f:
                f.write(current_version)
            return

    if os.path.exists(DB_PATH):
        # DB exists and seed hasn't changed — ensure schema is up to date
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

    # Fallback: create empty database
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
