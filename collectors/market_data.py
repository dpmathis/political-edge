"""Market data collector using yfinance.

Pulls daily OHLCV data for all active watchlist tickers.
Incremental — only fetches dates not already in the database.
"""

import logging
import sqlite3
from datetime import date, timedelta

import yfinance as yf
import pandas as pd

from config import DB_PATH

logger = logging.getLogger(__name__)


def _get_watchlist_tickers(conn: sqlite3.Connection) -> list[str]:
    """Get all active tickers from the watchlist."""
    rows = conn.execute(
        "SELECT ticker FROM watchlist WHERE active = 1"
    ).fetchall()
    return [r[0] for r in rows]


def _get_latest_date(conn: sqlite3.Connection, ticker: str) -> str | None:
    """Get the most recent date we have data for a ticker."""
    row = conn.execute(
        "SELECT MAX(date) FROM market_data WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row[0] if row and row[0] else None


def collect(start_date: str | None = None, end_date: str | None = None) -> int:
    """Collect market data for all watchlist tickers.

    Args:
        start_date: YYYY-MM-DD (default: 1 year ago or day after latest data)
        end_date: YYYY-MM-DD (default: today)

    Returns:
        Total rows inserted.
    """
    if not end_date:
        end_date = date.today().isoformat()

    conn = sqlite3.connect(DB_PATH)
    tickers = _get_watchlist_tickers(conn)
    if not tickers:
        logger.warning("No active tickers in watchlist")
        conn.close()
        return 0

    logger.info("Collecting market data for %d tickers", len(tickers))
    total_inserted = 0

    # Download all tickers at once for efficiency
    ticker_str = " ".join(tickers)

    # Determine start date per ticker (incremental)
    effective_start = start_date
    if not effective_start:
        # Find the earliest "latest date" across all tickers, or default to 1 year
        latest_dates = []
        for t in tickers:
            ld = _get_latest_date(conn, t)
            if ld:
                latest_dates.append(ld)

        if latest_dates:
            # Start from the day after the oldest "latest date"
            min_latest = min(latest_dates)
            effective_start = (
                date.fromisoformat(min_latest) + timedelta(days=1)
            ).isoformat()
        else:
            effective_start = (date.today() - timedelta(days=365)).isoformat()

    logger.info("Fetching %s to %s for: %s", effective_start, end_date, ticker_str)

    try:
        data = yf.download(
            ticker_str,
            start=effective_start,
            end=end_date,
            group_by="ticker",
            auto_adjust=False,
            progress=False,
        )
    except Exception as e:
        logger.error("yfinance download failed: %s", e)
        conn.close()
        return 0

    if data.empty:
        logger.info("No new market data available")
        conn.close()
        return 0

    # Parse the multi-level columns and insert per ticker
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                ticker_data = data
            else:
                ticker_data = data[ticker]

            if ticker_data.empty:
                continue

            for idx, row in ticker_data.iterrows():
                trade_date = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO market_data
                           (ticker, date, open, high, low, close, adj_close, volume)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ticker,
                            trade_date,
                            float(row.get("Open", 0)) if pd.notna(row.get("Open")) else None,
                            float(row.get("High", 0)) if pd.notna(row.get("High")) else None,
                            float(row.get("Low", 0)) if pd.notna(row.get("Low")) else None,
                            float(row.get("Close", 0)) if pd.notna(row.get("Close")) else None,
                            float(row.get("Adj Close", row.get("Close", 0))) if pd.notna(row.get("Close")) else None,
                            int(row.get("Volume", 0)) if pd.notna(row.get("Volume")) else None,
                        ),
                    )
                    total_inserted += 1
                except sqlite3.Error as e:
                    logger.error("DB error for %s %s: %s", ticker, trade_date, e)

        except KeyError:
            logger.warning("No data for ticker %s", ticker)
            continue

    conn.commit()
    conn.close()
    logger.info("Market data collector done: %d rows inserted", total_inserted)
    return total_inserted
