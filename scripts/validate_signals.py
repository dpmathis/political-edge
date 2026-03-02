#!/usr/bin/env python3
"""Validate historical signal performance against actual market data.

For each signal in trading_signals, computes the direction-adjusted return
over the signal's time horizon using market_data, then summarizes by signal type.

Usage:
    python scripts/validate_signals.py
    python scripts/validate_signals.py --ticker SPY
    python scripts/validate_signals.py --type reg_shock
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3

import numpy as np
import pandas as pd

from config import DB_PATH


def main():
    parser = argparse.ArgumentParser(description="Validate signal performance")
    parser.add_argument("--ticker", type=str, help="Filter by ticker")
    parser.add_argument("--type", type=str, help="Filter by signal type")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    query = """SELECT id, signal_date, ticker, signal_type, direction, conviction,
                      time_horizon_days, expected_car
               FROM trading_signals
               ORDER BY signal_date"""
    signals = pd.read_sql_query(query, conn)

    if signals.empty:
        print("No signals found in trading_signals table.")
        conn.close()
        return

    if args.ticker:
        signals = signals[signals["ticker"] == args.ticker.upper()]
    if args.type:
        signals = signals[signals["signal_type"] == args.type]

    if signals.empty:
        print("No signals match the filter criteria.")
        conn.close()
        return

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
        print(f"No signals with matching market data. ({skipped} skipped)")
        return

    df = pd.DataFrame(results)

    # Per-signal-type summary
    summary = df.groupby("signal_type").agg(
        n_signals=("adj_return", "count"),
        win_rate=("win", "mean"),
        mean_return=("adj_return", "mean"),
        median_return=("adj_return", "median"),
        std_return=("adj_return", "std"),
    ).reset_index()

    summary["sharpe"] = summary["mean_return"] / summary["std_return"].replace(0, np.nan)

    print("\n" + "=" * 80)
    print("SIGNAL VALIDATION REPORT")
    print("=" * 80)
    print(f"\nTotal signals evaluated: {len(df)} ({skipped} skipped — no market data or watch direction)")
    print(f"\n{'Signal Type':<25} {'N':>5} {'Win%':>7} {'Mean':>8} {'Median':>8} {'Std':>8} {'Sharpe':>8}")
    print("-" * 80)

    for _, row in summary.iterrows():
        print(
            f"{row['signal_type']:<25} {int(row['n_signals']):>5} "
            f"{row['win_rate']:>6.1%} {row['mean_return']:>+7.2%} "
            f"{row['median_return']:>+7.2%} {row['std_return']:>7.2%} "
            f"{row['sharpe']:>+7.2f}" if pd.notna(row['sharpe']) else
            f"{row['signal_type']:<25} {int(row['n_signals']):>5} "
            f"{row['win_rate']:>6.1%} {row['mean_return']:>+7.2%} "
            f"{row['median_return']:>+7.2%} {row['std_return']:>7.2%} "
            f"{'N/A':>8}"
        )

    print("-" * 80)
    print(
        f"{'OVERALL':<25} {len(df):>5} "
        f"{df['win'].mean():>6.1%} {df['adj_return'].mean():>+7.2%} "
        f"{df['adj_return'].median():>+7.2%} {df['adj_return'].std():>7.2%} "
        f"{df['adj_return'].mean() / df['adj_return'].std():>+7.2f}"
        if df['adj_return'].std() > 0 else ""
    )

    # Per-conviction breakdown
    if len(df) >= 10:
        print("\n\nBy Conviction Level:")
        conv_summary = df.groupby("conviction").agg(
            n=("adj_return", "count"),
            win_rate=("win", "mean"),
            mean_return=("adj_return", "mean"),
        ).reset_index()
        for _, row in conv_summary.iterrows():
            print(f"  {row['conviction']:<10} N={int(row['n']):>4}  Win={row['win_rate']:.1%}  Mean={row['mean_return']:+.2%}")

    print()


if __name__ == "__main__":
    main()
