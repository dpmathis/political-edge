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
    """Auto-initialize DB: decompress seed if available, else create empty.

    Uses seed file size as a version marker to detect when a new seed
    has been deployed and the DB needs to be refreshed.
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    marker_path = DB_PATH + ".seed_version"

    need_decompress = False

    if os.path.exists(_SEED_DB_GZ):
        current_version = str(os.path.getsize(_SEED_DB_GZ))
        stored_version = ""

        if os.path.exists(marker_path):
            try:
                stored_version = open(marker_path).read().strip()
            except Exception:
                stored_version = ""

        if not os.path.exists(DB_PATH):
            need_decompress = True
        elif current_version != stored_version:
            need_decompress = True

        if need_decompress:
            try:
                with gzip.open(_SEED_DB_GZ, "rb") as f_in:
                    with open(DB_PATH, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                # Run migration for any new tables/columns
                from scripts.migrate_phase2 import main as migrate_main
                migrate_main()
                # Write marker so we don't decompress again until seed changes
                try:
                    with open(marker_path, "w") as f:
                        f.write(current_version)
                except Exception:
                    pass  # Marker write failure is non-fatal
                return
            except Exception as e:
                st.error(f"Failed to decompress seed database: {e}")

    if not os.path.exists(DB_PATH):
        # No seed and no DB — create empty
        try:
            from scripts.setup_db import main as setup_main
            setup_main()
        except Exception:
            pass
        try:
            from scripts.migrate_phase2 import main as migrate_main
            migrate_main()
        except Exception:
            pass
        return

    # DB exists — ensure schema is up to date
    try:
        conn = sqlite3.connect(DB_PATH)
        tables = set(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall())
        conn.close()
        required = {"fda_events", "trading_signals", "prediction_markets",
                     "data_collection_log", "event_studies"}
        if not required.issubset(tables):
            from scripts.migrate_phase2 import main as migrate_main
            migrate_main()
    except Exception:
        pass


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
stats = {}
for label, table in [
    ("Regulatory Events", "regulatory_events"),
    ("FDA Events", "fda_events"),
    ("Lobbying Filings", "lobbying_filings"),
    ("Congress Trades", "congress_trades"),
    ("Trading Signals", "trading_signals"),
    ("Market Data", "market_data"),
]:
    try:
        stats[label] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        stats[label] = 0
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
