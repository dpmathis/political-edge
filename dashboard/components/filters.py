"""Shared sidebar filter components for the Streamlit dashboard."""

import sqlite3
from datetime import date, timedelta

import streamlit as st

from config import DB_PATH


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def render_sidebar_filters() -> dict:
    """Render the shared sidebar filters and return the selected values."""
    st.sidebar.title("Filters")

    conn = get_db_connection()

    # Date range
    col1, col2 = st.sidebar.columns(2)
    with col1:
        start_date = st.date_input(
            "From",
            value=date.today() - timedelta(days=30),
            key="filter_start_date",
        )
    with col2:
        end_date = st.date_input("To", value=date.today(), key="filter_end_date")

    # Sector filter
    sectors = conn.execute(
        "SELECT DISTINCT sector FROM sector_keyword_map ORDER BY sector"
    ).fetchall()
    sector_options = [r[0] for r in sectors]
    selected_sectors = st.sidebar.multiselect(
        "Sectors", sector_options, key="filter_sectors"
    )

    # Impact score filter
    impact_range = st.sidebar.slider(
        "Impact Score", min_value=1, max_value=5, value=(1, 5), key="filter_impact"
    )

    # Event type filter
    event_types = conn.execute(
        "SELECT DISTINCT event_type FROM regulatory_events ORDER BY event_type"
    ).fetchall()
    type_options = [r[0] for r in event_types if r[0]]
    selected_types = st.sidebar.multiselect(
        "Event Types", type_options, key="filter_event_types"
    )

    # Watchlist ticker filter
    tickers = conn.execute(
        "SELECT ticker FROM watchlist WHERE active = 1 ORDER BY ticker"
    ).fetchall()
    ticker_options = [r[0] for r in tickers]
    selected_tickers = st.sidebar.multiselect(
        "Watchlist Tickers", ticker_options, key="filter_tickers"
    )

    conn.close()

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "sectors": selected_sectors,
        "impact_range": impact_range,
        "event_types": selected_types,
        "tickers": selected_tickers,
    }
