"""Macro Regime Classifier.

Implements a Hedgeye-style four-quadrant growth/inflation model.
Used as a position-sizing overlay, not a standalone signal.
"""

import logging
import sqlite3
from datetime import date

import pandas as pd

from config import DB_PATH

logger = logging.getLogger(__name__)

QUADRANTS = {
    1: {
        "label": "Goldilocks",
        "description": "Growth accelerating, Inflation decelerating",
        "equity_bias": "long",
        "position_modifier": 1.2,
        "favored_sectors": ["XLK", "XLY"],
        "avoid_sectors": ["XLP", "XLU"],
    },
    2: {
        "label": "Reflation",
        "description": "Growth accelerating, Inflation accelerating",
        "equity_bias": "long_cautious",
        "position_modifier": 1.0,
        "favored_sectors": ["XLE", "XLB", "XLF"],
        "avoid_sectors": ["XLU"],
    },
    3: {
        "label": "Stagflation",
        "description": "Growth decelerating, Inflation accelerating",
        "equity_bias": "defensive",
        "position_modifier": 0.6,
        "favored_sectors": ["XLE", "XLP"],
        "avoid_sectors": ["XLK", "XLY", "XLF"],
    },
    4: {
        "label": "Deflation",
        "description": "Growth decelerating, Inflation decelerating",
        "equity_bias": "short_or_cash",
        "position_modifier": 0.4,
        "favored_sectors": ["XLU", "XLP"],
        "avoid_sectors": ["XLE", "XLI", "XLB"],
    },
}


def classify_current_regime(conn: sqlite3.Connection | None = None) -> dict | None:
    """Classify the current macro regime based on growth and inflation RoC.

    Returns dict with quadrant, label, confidence, position_modifier, etc.
    Returns None if insufficient data.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        # Get latest growth RoC (use GDPC1 or fallback to INDPRO)
        growth_row = conn.execute(
            """SELECT date, rate_of_change_6m FROM macro_indicators
               WHERE series_id = 'GDPC1' AND rate_of_change_6m IS NOT NULL
               ORDER BY date DESC LIMIT 2"""
        ).fetchall()

        if len(growth_row) < 2:
            # Fallback to Industrial Production (more frequent)
            growth_row = conn.execute(
                """SELECT date, rate_of_change_6m FROM macro_indicators
                   WHERE series_id = 'INDPRO' AND rate_of_change_6m IS NOT NULL
                   ORDER BY date DESC LIMIT 2"""
            ).fetchall()

        # Get latest inflation RoC
        inflation_row = conn.execute(
            """SELECT date, rate_of_change_6m FROM macro_indicators
               WHERE series_id = 'CPIAUCSL' AND rate_of_change_6m IS NOT NULL
               ORDER BY date DESC LIMIT 2"""
        ).fetchall()

        if len(growth_row) < 2 or len(inflation_row) < 2:
            logger.warning("Insufficient data for regime classification")
            return None

        current_growth_roc = growth_row[0][1]
        prior_growth_roc = growth_row[1][1]
        current_inflation_roc = inflation_row[0][1]
        prior_inflation_roc = inflation_row[1][1]

        # Determine direction
        growth_delta = current_growth_roc - prior_growth_roc
        inflation_delta = current_inflation_roc - prior_inflation_roc

        growth_accelerating = growth_delta > 0
        inflation_accelerating = inflation_delta > 0

        # Assign quadrant
        if growth_accelerating and not inflation_accelerating:
            quadrant = 1  # Goldilocks
        elif growth_accelerating and inflation_accelerating:
            quadrant = 2  # Reflation
        elif not growth_accelerating and inflation_accelerating:
            quadrant = 3  # Stagflation
        else:
            quadrant = 4  # Deflation

        # Determine confidence
        growth_clear = abs(growth_delta) > 0.005  # >0.5% divergence
        inflation_clear = abs(inflation_delta) > 0.005

        if growth_clear and inflation_clear:
            confidence = "high"
        elif growth_clear or inflation_clear:
            confidence = "medium"
        else:
            confidence = "low"

        # Get supplementary data
        yield_curve = conn.execute(
            "SELECT value FROM macro_indicators WHERE series_id = 'T10Y2Y' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        vix = conn.execute(
            "SELECT value FROM macro_indicators WHERE series_id = 'VIXCLS' ORDER BY date DESC LIMIT 1"
        ).fetchone()

        regime_data = QUADRANTS[quadrant]
        result = {
            "quadrant": quadrant,
            "label": regime_data["label"],
            "description": regime_data["description"],
            "confidence": confidence,
            "position_modifier": regime_data["position_modifier"],
            "equity_bias": regime_data["equity_bias"],
            "favored_sectors": regime_data["favored_sectors"],
            "avoid_sectors": regime_data["avoid_sectors"],
            "growth_roc": current_growth_roc,
            "inflation_roc": current_inflation_roc,
            "yield_curve_spread": yield_curve[0] if yield_curve else None,
            "vix": vix[0] if vix else None,
        }

        # Save to macro_regimes table
        today = date.today().isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO macro_regimes
               (date, growth_roc, inflation_roc, quadrant, quadrant_label,
                yield_curve_spread, vix, confidence, position_size_modifier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                today,
                current_growth_roc,
                current_inflation_roc,
                quadrant,
                regime_data["label"],
                result["yield_curve_spread"],
                result["vix"],
                confidence,
                regime_data["position_modifier"],
            ),
        )
        conn.commit()

        logger.info(
            "Macro regime: Q%d %s (confidence: %s, modifier: %.1fx)",
            quadrant, regime_data["label"], confidence, regime_data["position_modifier"],
        )
        return result

    finally:
        if close_conn:
            conn.close()


def get_regime_history(start_date: str | None = None, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Get historical regime classifications."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        query = "SELECT * FROM macro_regimes"
        params = ()
        if start_date:
            query += " WHERE date >= ?"
            params = (start_date,)
        query += " ORDER BY date"

        return pd.read_sql_query(query, conn, params=params)
    finally:
        if close_conn:
            conn.close()
