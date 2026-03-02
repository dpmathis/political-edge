"""FRED Macro Data Collector.

Fetches economic indicators from the Federal Reserve Economic Data (FRED) API.
Calculates rate-of-change columns for growth/inflation regime classification.

Usage:
    from collectors import fred_macro
    fred_macro.collect()
    fred_macro.backfill("2018-01-01")
"""

import logging
import sqlite3
import time
from datetime import date, timedelta

from config import DB_PATH, get_api_key

logger = logging.getLogger(__name__)

FRED_SERIES = {
    # Growth indicators
    "GDPC1": {"name": "Real GDP", "frequency": "quarterly", "category": "growth"},
    "INDPRO": {"name": "Industrial Production", "frequency": "monthly", "category": "growth"},
    "PAYEMS": {"name": "Nonfarm Payrolls", "frequency": "monthly", "category": "growth"},
    "UNRATE": {"name": "Unemployment Rate", "frequency": "monthly", "category": "growth"},
    "ICSA": {"name": "Initial Jobless Claims", "frequency": "weekly", "category": "growth"},
    "RSXFS": {"name": "Retail Sales ex Food/Auto", "frequency": "monthly", "category": "growth"},
    # Inflation indicators
    "CPIAUCSL": {"name": "CPI All Urban", "frequency": "monthly", "category": "inflation"},
    "CPILFESL": {"name": "Core CPI (ex food/energy)", "frequency": "monthly", "category": "inflation"},
    "PCEPI": {"name": "PCE Price Index", "frequency": "monthly", "category": "inflation"},
    "T5YIE": {"name": "5Y Breakeven Inflation", "frequency": "daily", "category": "inflation"},
    # Interest rates & yield curve
    "DFF": {"name": "Fed Funds Rate", "frequency": "daily", "category": "rates"},
    "DGS2": {"name": "2Y Treasury Yield", "frequency": "daily", "category": "rates"},
    "DGS10": {"name": "10Y Treasury Yield", "frequency": "daily", "category": "rates"},
    "T10Y2Y": {"name": "10Y-2Y Spread", "frequency": "daily", "category": "rates"},
    "T10Y3M": {"name": "10Y-3M Spread", "frequency": "daily", "category": "rates"},
    # Financial conditions
    "VIXCLS": {"name": "VIX", "frequency": "daily", "category": "conditions"},
    "BAMLH0A0HYM2": {"name": "HY OAS Spread", "frequency": "daily", "category": "conditions"},
}

# Periods for rate-of-change calculation (in months)
ROC_PERIODS = {
    "rate_of_change_3m": 3,
    "rate_of_change_6m": 6,
    "rate_of_change_12m": 12,
}


def _get_fred_client():
    """Create a FRED API client."""
    api_key = get_api_key("fred_api_key")
    if not api_key:
        return None
    from fredapi import Fred
    return Fred(api_key=api_key)


def _insert_observations(conn: sqlite3.Connection, series_id: str, observations) -> int:
    """Insert FRED observations into macro_indicators."""
    inserted = 0
    for obs_date, value in observations.items():
        if value is None or str(value) == "." or str(value) == "":
            continue
        try:
            val = float(value)
        except (ValueError, TypeError):
            continue

        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO macro_indicators (series_id, date, value)
                   VALUES (?, ?, ?)""",
                (series_id, str(obs_date.date()) if hasattr(obs_date, 'date') else str(obs_date), val),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.Error as e:
            logger.error("DB error inserting %s %s: %s", series_id, obs_date, e)
    conn.commit()
    return inserted


def calculate_roc(conn: sqlite3.Connection, series_id: str):
    """Calculate rate-of-change columns for a series."""
    rows = conn.execute(
        "SELECT id, date, value FROM macro_indicators WHERE series_id = ? ORDER BY date",
        (series_id,),
    ).fetchall()

    if len(rows) < 13:  # Need at least 12 months of history
        return

    # Build date→value lookup
    import pandas as pd
    dates = [r[1] for r in rows]
    values = [r[2] for r in rows]
    _ = [r[0] for r in rows]  # ids available if needed

    for i, (row_id, row_date, row_value) in enumerate(rows):
        if row_value is None or row_value == 0:
            continue

        updates = {}
        for col_name, months in ROC_PERIODS.items():
            # Find the observation ~N months ago
            target_idx = None
            for j in range(i - 1, -1, -1):
                # Approximate month difference
                date_diff = pd.Timestamp(row_date) - pd.Timestamp(dates[j])
                if date_diff.days >= months * 28:
                    target_idx = j
                    break

            if target_idx is not None and values[target_idx] and values[target_idx] != 0:
                roc = (row_value - values[target_idx]) / abs(values[target_idx])
                # Annualize for sub-annual periods
                if months < 12:
                    roc = roc * (12 / months)
                updates[col_name] = roc

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            conn.execute(
                f"UPDATE macro_indicators SET {set_clause} WHERE id = ?",
                (*updates.values(), row_id),
            )

    conn.commit()


def collect(since_date: str | None = None) -> int:
    """Fetch latest FRED data for all tracked series.

    Args:
        since_date: YYYY-MM-DD start date (default: 90 days ago)

    Returns:
        Total observations inserted.
    """
    fred = _get_fred_client()
    if not fred:
        logger.info("FRED API key not configured, skipping")
        return 0

    if not since_date:
        since_date = (date.today() - timedelta(days=90)).isoformat()

    logger.info("FRED collector: fetching since %s", since_date)
    conn = sqlite3.connect(DB_PATH)
    total_inserted = 0

    for series_id, meta in FRED_SERIES.items():
        try:
            data = fred.get_series(series_id, observation_start=since_date)
            if data is not None and not data.empty:
                inserted = _insert_observations(conn, series_id, data)
                total_inserted += inserted
                logger.info("  %s (%s): %d new observations", series_id, meta["name"], inserted)
            time.sleep(0.5)
        except Exception as e:
            logger.error("Error fetching %s: %s", series_id, e)

    # Calculate rate-of-change for key series
    logger.info("Calculating rate-of-change...")
    for series_id in ["GDPC1", "CPIAUCSL", "CPILFESL", "INDPRO", "PAYEMS", "PCEPI"]:
        try:
            calculate_roc(conn, series_id)
        except Exception as e:
            logger.error("Error calculating RoC for %s: %s", series_id, e)

    conn.close()
    logger.info("FRED collector done: %d new observations", total_inserted)
    return total_inserted


def backfill(since_date: str = "2018-01-01") -> int:
    """Backfill historical FRED data."""
    logger.info("Backfilling FRED data since %s", since_date)
    return collect(since_date=since_date)
