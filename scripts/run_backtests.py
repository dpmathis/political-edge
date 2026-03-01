#!/usr/bin/env python3
"""Run hypothesis backtests.

Usage:
    python scripts/run_backtests.py              # Run all
    python scripts/run_backtests.py --study fda_adcom  # Run specific study
    python scripts/run_backtests.py --list        # List available studies
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.backtest_runner import BacktestRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("run_backtests")


def main():
    parser = argparse.ArgumentParser(description="Run hypothesis backtests")
    parser.add_argument("--study", type=str, help="Run a specific study by name")
    parser.add_argument("--list", action="store_true", help="List available studies")
    parser.add_argument("--save", action="store_true", default=True, help="Save results to database (default: true)")
    args = parser.parse_args()

    runner = BacktestRunner()

    if args.list:
        print("Available studies:")
        for name in runner.list_studies():
            print(f"  - {name}")
        return

    if args.study:
        results = runner.run_study(args.study)
        print("\n" + "=" * 60)
        print(results.summary())
        print("=" * 60)
        if args.save:
            study_id = results.save_to_db()
            print(f"\nSaved to database (study_id={study_id})")
    else:
        all_results = runner.run_all()
        for name, results in all_results.items():
            print("\n" + "=" * 60)
            print(results.summary())
            print("=" * 60)
            if args.save:
                study_id = results.save_to_db()
                print(f"Saved to database (study_id={study_id})")


if __name__ == "__main__":
    main()
