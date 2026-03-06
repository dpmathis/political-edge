"""Reusable signal validation module.

Validates historical signal performance against actual market data.
Computes direction-adjusted returns and aggregates by signal type and conviction.
"""

import sqlite3

import numpy as np
import pandas as pd

from config import DB_PATH


def validate_signals(db_path: str | None = None) -> dict:
    """Validate all trading signals against market data.

    Returns dict with keys:
        results_df: DataFrame of per-signal results
        by_type: DataFrame aggregated by signal_type (N, win_rate, mean, median, std, sharpe)
        by_conviction: DataFrame aggregated by conviction (N, win_rate, mean)
        overall: dict with win_rate, mean_return, median_return, sharpe, n_evaluated, n_skipped
    Returns empty dict if no signals or no market data.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)

    query = """SELECT id, signal_date, ticker, signal_type, direction, conviction,
                      time_horizon_days, expected_car
               FROM trading_signals
               ORDER BY signal_date"""
    signals = pd.read_sql_query(query, conn)

    if signals.empty:
        conn.close()
        return {}

    results = []
    skipped = 0

    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        entry_date = sig["signal_date"]
        horizon = int(sig["time_horizon_days"]) if pd.notna(sig["time_horizon_days"]) else 10
        direction = sig["direction"]

        if direction not in ("long", "short"):
            skipped += 1
            continue

        prices = pd.read_sql_query(
            """SELECT date, close FROM market_data
               WHERE ticker = ? AND date >= ?
               ORDER BY date LIMIT ?""",
            conn,
            params=(ticker, entry_date, horizon + 1),
        )

        if len(prices) < 2:
            skipped += 1
            continue

        entry_price = prices.iloc[0]["close"]
        exit_price = prices.iloc[-1]["close"]

        if entry_price == 0 or pd.isna(entry_price):
            skipped += 1
            continue

        raw_return = (exit_price - entry_price) / entry_price
        adj_return = raw_return if direction == "long" else -raw_return

        results.append({
            "signal_type": sig["signal_type"],
            "ticker": ticker,
            "direction": direction,
            "conviction": sig["conviction"],
            "entry_date": entry_date,
            "horizon": horizon,
            "raw_return": raw_return,
            "adj_return": adj_return,
            "expected_car": sig["expected_car"],
            "win": adj_return > 0,
        })

    conn.close()

    if not results:
        return {"overall": {"n_evaluated": 0, "n_skipped": skipped}}

    df = pd.DataFrame(results)

    # Per-signal-type summary
    by_type = df.groupby("signal_type").agg(
        n_signals=("adj_return", "count"),
        win_rate=("win", "mean"),
        mean_return=("adj_return", "mean"),
        median_return=("adj_return", "median"),
        std_return=("adj_return", "std"),
    ).reset_index()
    by_type["sharpe"] = by_type["mean_return"] / by_type["std_return"].replace(0, np.nan)

    # Per-conviction summary
    by_conviction = df.groupby("conviction").agg(
        n_signals=("adj_return", "count"),
        win_rate=("win", "mean"),
        mean_return=("adj_return", "mean"),
    ).reset_index()

    # Overall
    overall_std = df["adj_return"].std()
    overall = {
        "win_rate": float(df["win"].mean()),
        "mean_return": float(df["adj_return"].mean()),
        "median_return": float(df["adj_return"].median()),
        "sharpe": float(df["adj_return"].mean() / overall_std) if overall_std > 0 else None,
        "n_evaluated": len(df),
        "n_skipped": skipped,
    }

    return {
        "results_df": df,
        "by_type": by_type,
        "by_conviction": by_conviction,
        "overall": overall,
    }
