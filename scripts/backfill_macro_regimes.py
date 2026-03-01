#!/usr/bin/env python3
"""Backfill daily macro regime classifications from 2024-01-01 to present.

Uses INDPRO (Industrial Production) as growth proxy and CPIAUCSL as inflation.
Forward-fills monthly data to daily, then classifies each business day using
the Hedgeye four-quadrant model from analysis/macro_regime.py.

Usage:
    python scripts/backfill_macro_regimes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3

import pandas as pd

from config import DB_PATH

QUADRANT_MAP = {
    (True, False): (1, "Goldilocks", 1.2),
    (True, True): (2, "Reflation", 1.0),
    (False, True): (3, "Stagflation", 0.6),
    (False, False): (4, "Deflation", 0.4),
}


def main():
    conn = sqlite3.connect(DB_PATH)

    # Load growth data (INDPRO has monthly roc_6m; GDPC1 is quarterly with no roc)
    growth = pd.read_sql_query(
        "SELECT date, rate_of_change_6m FROM macro_indicators WHERE series_id = 'INDPRO' AND rate_of_change_6m IS NOT NULL ORDER BY date",
        conn,
    )
    inflation = pd.read_sql_query(
        "SELECT date, rate_of_change_6m FROM macro_indicators WHERE series_id = 'CPIAUCSL' AND rate_of_change_6m IS NOT NULL ORDER BY date",
        conn,
    )
    vix = pd.read_sql_query(
        "SELECT date, value FROM macro_indicators WHERE series_id = 'VIXCLS' ORDER BY date",
        conn,
    )
    t10y2y = pd.read_sql_query(
        "SELECT date, value FROM macro_indicators WHERE series_id = 'T10Y2Y' ORDER BY date",
        conn,
    )

    print(f"Growth (INDPRO): {len(growth)} monthly observations")
    print(f"Inflation (CPIAUCSL): {len(inflation)} monthly observations")
    print(f"VIX: {len(vix)} daily observations")
    print(f"T10Y2Y: {len(t10y2y)} daily observations")

    if growth.empty or inflation.empty:
        print("ERROR: Insufficient macro data. Run FRED collector first.")
        conn.close()
        return

    # Convert to datetime index
    growth["date"] = pd.to_datetime(growth["date"])
    inflation["date"] = pd.to_datetime(inflation["date"])
    vix["date"] = pd.to_datetime(vix["date"])
    t10y2y["date"] = pd.to_datetime(t10y2y["date"])

    # Create daily business day range
    start = max(growth["date"].min(), inflation["date"].min(), pd.Timestamp("2024-01-01"))
    end = pd.Timestamp("2026-02-28")
    dates = pd.bdate_range(start, end)

    print(f"\nBackfilling {len(dates)} business days from {start.date()} to {end.date()}")

    # Forward-fill monthly data to daily
    growth_daily = growth.set_index("date")["rate_of_change_6m"].reindex(dates, method="ffill")
    inflation_daily = inflation.set_index("date")["rate_of_change_6m"].reindex(dates, method="ffill")
    vix_daily = vix.set_index("date")["value"].reindex(dates, method="ffill")
    t10y2y_daily = t10y2y.set_index("date")["value"].reindex(dates, method="ffill")

    # Clear existing regimes
    conn.execute("DELETE FROM macro_regimes")

    inserted = 0
    for d in dates:
        g = growth_daily.get(d)
        i = inflation_daily.get(d)

        if pd.isna(g) or pd.isna(i):
            continue

        growth_val = float(g)
        inflation_val = float(i)

        # Classify using rate of change direction
        # growth_roc > 0 means growth accelerating, inflation_roc > 0 means inflation accelerating
        growth_accel = growth_val > 0
        inflation_accel = inflation_val > 0

        quadrant, label, modifier = QUADRANT_MAP[(growth_accel, inflation_accel)]

        vix_val = vix_daily.get(d)
        t10y2y_val = t10y2y_daily.get(d)

        conn.execute(
            """INSERT INTO macro_regimes
               (date, growth_roc, inflation_roc, quadrant, quadrant_label,
                yield_curve_spread, vix, confidence, position_size_modifier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d.strftime("%Y-%m-%d"),
                growth_val,
                inflation_val,
                quadrant,
                label,
                float(t10y2y_val) if pd.notna(t10y2y_val) else None,
                float(vix_val) if pd.notna(vix_val) else None,
                "medium",
                modifier,
            ),
        )
        inserted += 1

    conn.commit()

    # Summary
    print(f"\nInserted {inserted} regime days")
    rows = conn.execute(
        "SELECT quadrant_label, COUNT(*), MIN(date), MAX(date) FROM macro_regimes GROUP BY quadrant_label"
    ).fetchall()
    for label, count, min_date, max_date in rows:
        print(f"  {label}: {count} days ({min_date} to {max_date})")

    conn.close()


if __name__ == "__main__":
    main()
