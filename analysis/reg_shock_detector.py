"""Detect abnormal surges in regulatory activity by agency.

Research findings:
- DoD Acquisition Regulations System: +1.77% CAR (p=0.002) when weekly activity spikes
- CMS (Medicare/Medicaid): -6.57% CAR (p=0.011) when weekly activity spikes
- RTX: +2.58% CAR, 100% win rate across 8 defense regulatory shocks

Uses a rolling z-score to detect when an agency's weekly output exceeds
its historical norm by more than 1.5 standard deviations.
"""

import logging
import sqlite3

import numpy as np
import pandas as pd

from config import DB_PATH

logger = logging.getLogger(__name__)

AGENCY_TICKERS = {
    "Defense Department, Defense Acquisition Regulations System": {
        "tickers": ["LMT", "RTX", "GD", "NOC", "BA"],
        "direction": "long",
        "expected_car": 0.0177,
        "confidence": "high",
        "hold_days": 5,
    },
    "Health and Human Services Department, Centers for Medicare & Medicaid Services": {
        "tickers": ["UNH", "HUM"],
        "direction": "short",
        "expected_car": -0.0657,
        "confidence": "high",
        "hold_days": 5,
    },
}

Z_THRESHOLD = 1.5
ROLLING_WINDOW = 8
MIN_WEEKS = 10


def detect_shocks(lookback_weeks: int = 1, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Detect regulatory intensity shocks in the most recent N weeks.

    Args:
        lookback_weeks: How many recent weeks to check for shocks.
        conn: Optional DB connection. Creates one if not provided.

    Returns:
        List of shock dicts with: agency, week_start, count, z_score, signal metadata
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        reg_df = pd.read_sql_query(
            """SELECT publication_date, agency, impact_score
               FROM regulatory_events
               WHERE impact_score >= 4
               ORDER BY publication_date""",
            conn,
        )

        if reg_df.empty:
            return []

        reg_df["date"] = pd.to_datetime(reg_df["publication_date"])
        reg_df["week_start"] = reg_df["date"] - pd.to_timedelta(reg_df["date"].dt.weekday, unit="D")

        shocks = []
        for agency, meta in AGENCY_TICKERS.items():
            agency_df = reg_df[reg_df["agency"] == agency]
            weekly = agency_df.groupby("week_start").size().reset_index(name="count")

            if len(weekly) < MIN_WEEKS:
                # Try partial agency name match
                agency_df = reg_df[reg_df["agency"].str.contains(agency.split(",")[0], case=False, na=False)]
                weekly = agency_df.groupby("week_start").size().reset_index(name="count")
                if len(weekly) < MIN_WEEKS:
                    logger.debug("Skipping %s: only %d weeks of data", agency[:40], len(weekly))
                    continue

            all_weeks = pd.date_range(weekly["week_start"].min(), weekly["week_start"].max(), freq="W-MON")
            weekly = weekly.set_index("week_start").reindex(all_weeks, fill_value=0).reset_index()
            weekly.columns = ["week_start", "count"]
            weekly = weekly.sort_values("week_start")

            weekly["rm"] = weekly["count"].rolling(ROLLING_WINDOW, min_periods=4).mean()
            weekly["rs"] = weekly["count"].rolling(ROLLING_WINDOW, min_periods=4).std()
            weekly["z"] = (weekly["count"] - weekly["rm"]) / weekly["rs"].replace(0, np.nan)

            recent = weekly.tail(lookback_weeks)
            for _, row in recent.iterrows():
                if pd.notna(row["z"]) and row["z"] > Z_THRESHOLD:
                    shocks.append({
                        "agency": agency,
                        "week_start": row["week_start"].strftime("%Y-%m-%d"),
                        "count": int(row["count"]),
                        "z_score": round(float(row["z"]), 2),
                        "tickers": meta["tickers"],
                        "direction": meta["direction"],
                        "expected_car": meta["expected_car"],
                        "confidence": meta["confidence"],
                        "hold_days": meta["hold_days"],
                    })

        return shocks
    finally:
        if close_conn:
            conn.close()
