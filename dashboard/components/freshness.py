"""Freshness indicator component for Streamlit pages."""

import sqlite3
from datetime import date, datetime

import streamlit as st

from config import DB_PATH


def render_freshness(table: str, date_column: str, label: str | None = None, conn: sqlite3.Connection | None = None):
    """Display a colored freshness indicator showing the most recent data date.

    Green: data is from today or yesterday
    Yellow: data is 2-3 days old
    Red: data is >3 days old
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

        if days_old <= 1:
            color = "green"
        elif days_old <= 3:
            color = "orange"
        else:
            color = "red"

        display_label = label or table.replace("_", " ").title()
        st.caption(f":{color}[{display_label} data through {latest}]")

    except Exception:
        st.caption(f"{label or table}: Unable to check freshness")
    finally:
        if close_conn:
            conn.close()
