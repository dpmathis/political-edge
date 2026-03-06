"""Freshness indicator component for Streamlit pages."""

import sqlite3
from datetime import date, datetime

import streamlit as st

from config import DB_PATH


def render_freshness(table: str, date_column: str, label: str | None = None, conn: sqlite3.Connection | None = None):
    """Display a colored freshness indicator with relative time.

    Shows a colored dot emoji + relative time label:
    - Green: updated today or yesterday
    - Yellow: updated 2-3 days ago
    - Red: updated >3 days ago (stale)
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        row = conn.execute(f"SELECT MAX({date_column}) FROM {table}").fetchone()
        latest = row[0] if row else None

        if not latest:
            st.caption(f"{label or table}: No data")
            return

        try:
            latest_date = datetime.strptime(str(latest)[:10], "%Y-%m-%d").date()
            days_old = (date.today() - latest_date).days
        except (ValueError, TypeError):
            st.caption(f"{label or table}: Latest: {latest}")
            return

        display_label = label or table.replace("_", " ").title()

        if days_old == 0:
            dot, relative = "🟢", "updated today"
        elif days_old == 1:
            dot, relative = "🟢", "updated yesterday"
        elif days_old <= 3:
            dot, relative = "🟡", f"updated {days_old} days ago"
        elif days_old <= 7:
            dot, relative = "🔴", f"updated {days_old} days ago"
        else:
            dot, relative = "🔴", f"stale — last updated {days_old} days ago"

        st.caption(f"{dot} {display_label} — {relative} ({latest_date.strftime('%b %d')})")

    except Exception:
        st.caption(f"{label or table}: Unable to check freshness")
    finally:
        if close_conn:
            conn.close()
