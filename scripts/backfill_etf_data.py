#!/usr/bin/env python3
"""Backfill full market data history for sector ETFs and SPY.

Downloads daily OHLCV from 2017-01-01 to present via yfinance.
Deletes existing rows for each ticker and reinserts clean data.

Usage:
    python scripts/backfill_etf_data.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from datetime import datetime

import yfinance as yf

from config import DB_PATH

ETFS = ["SPY", "XLI", "XLB", "XLK", "XLE", "XLF", "XLP"]
START = "2017-01-01"
END = datetime.now().strftime("%Y-%m-%d")


def main():
    conn = sqlite3.connect(DB_PATH)

    for ticker in ETFS:
        existing = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM market_data WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        print(f"{ticker}: existing {existing[2]} rows from {existing[0]} to {existing[1]}")

        print(f"  Downloading {ticker} from {START} to {END}...")
        df = yf.download(ticker, start=START, end=END, auto_adjust=False, progress=False)

        if df.empty:
            print(f"  WARNING: No data returned for {ticker}")
            continue

        # Handle multi-level columns from yfinance
        if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
            df = df.droplevel("Ticker", axis=1)

        df = df.reset_index()

        # Delete existing and reinsert
        conn.execute("DELETE FROM market_data WHERE ticker = ?", (ticker,))

        for _, row in df.iterrows():
            trade_date = row["Date"].strftime("%Y-%m-%d")
            try:
                conn.execute(
                    """INSERT INTO market_data (ticker, date, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ticker,
                        trade_date,
                        float(row["Open"]),
                        float(row["High"]),
                        float(row["Low"]),
                        float(row["Close"]),
                        int(row["Volume"]),
                    ),
                )
            except Exception as e:
                print(f"  Error inserting {ticker} {trade_date}: {e}")

        conn.commit()
        new_count = conn.execute(
            "SELECT COUNT(*) FROM market_data WHERE ticker = ?", (ticker,)
        ).fetchone()[0]
        print(f"  -> Backfilled to {new_count} rows")

    # Summary
    print("\n--- Summary ---")
    rows = conn.execute(
        """SELECT ticker, COUNT(*), MIN(date), MAX(date)
           FROM market_data
           WHERE ticker IN ('SPY','XLI','XLB','XLK','XLE','XLF','XLP')
           GROUP BY ticker"""
    ).fetchall()
    for ticker, count, min_date, max_date in rows:
        print(f"  {ticker}: {count} rows from {min_date} to {max_date}")

    conn.close()


if __name__ == "__main__":
    main()
