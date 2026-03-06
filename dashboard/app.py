"""Political Edge — Main Streamlit Entrypoint."""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gzip
import shutil
import sqlite3

import streamlit as st

from config import DB_PATH
from dashboard.components.responsive import inject_responsive_css

st.set_page_config(
    page_title="Political Edge",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_responsive_css()

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
                     "data_collection_log", "event_studies", "pipeline_rules",
                     "user_preferences"}
        if not required.issubset(tables):
            from scripts.migrate_phase2 import main as migrate_main
            migrate_main()
    except Exception:
        pass


_ensure_db()

# Show DB health in sidebar for debugging
try:
    _dbconn = sqlite3.connect(DB_PATH)
    _tables = [r[0] for r in _dbconn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    _dbconn.close()
    if len(_tables) < 10:
        st.sidebar.warning(f"DB has only {len(_tables)} tables — data may be incomplete.")
except Exception as _e:
    st.sidebar.error(f"DB error: {_e}")

# ── Navigation ────────────────────────────────────────────────────────
_PAGES = Path(__file__).parent / "pages"

pages = {
    "": [
        st.Page(str(_PAGES / "home.py"), title="Home", icon="🏠", default=True),
    ],
    "Daily Briefing": [
        st.Page(str(_PAGES / "0_Today.py"), title="Today", icon="📊"),
    ],
    "Market Intelligence": [
        st.Page(str(_PAGES / "4_Watchlist.py"), title="Watchlist", icon="👁️"),
        st.Page(str(_PAGES / "6_Signals.py"), title="Signals", icon="⚡"),
        st.Page(str(_PAGES / "5_Macro.py"), title="Macro & Fed", icon="🏦"),
    ],
    "Data Feeds": [
        st.Page(str(_PAGES / "1_RegWatch.py"), title="RegWatch", icon="📋"),
        st.Page(str(_PAGES / "2_FDA_Catalysts.py"), title="FDA Catalysts", icon="💊"),
        st.Page(str(_PAGES / "3_Lobbying.py"), title="Lobbying", icon="🏢"),
        st.Page(str(_PAGES / "13_Congress_Trades.py"), title="Congress Trades", icon="🏛️"),
        st.Page(str(_PAGES / "12_Contracts.py"), title="Contracts", icon="📑"),
        st.Page(str(_PAGES / "14_Prediction_Markets.py"), title="Prediction Markets", icon="🎯"),
        st.Page(str(_PAGES / "7_EO_Tracker.py"), title="EO Tracker", icon="📜"),
        st.Page(str(_PAGES / "10_Pipeline.py"), title="Pipeline", icon="🔄"),
    ],
    "Advanced": [
        st.Page(str(_PAGES / "9_Research.py"), title="Research", icon="🔬"),
        st.Page(str(_PAGES / "11_Backtests.py"), title="Backtests", icon="📈"),
        st.Page(str(_PAGES / "8_Settings.py"), title="Settings", icon="⚙️"),
    ],
}

pg = st.navigation(pages)
pg.run()
