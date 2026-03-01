"""Event Study Framework — calculates abnormal returns around dated events.

This is the analytical core of the platform. Given a list of (event_date, ticker)
pairs and a benchmark, it calculates abnormal returns, cumulative abnormal returns,
and statistical significance.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from config import DB_PATH

logger = logging.getLogger(__name__)


@dataclass
class EventStudyResults:
    """Results from an event study run."""

    study_name: str
    hypothesis: str
    method: str
    benchmark: str
    window_pre: int
    window_post: int
    num_events: int
    mean_car: float
    median_car: float
    t_statistic: float
    p_value: float
    win_rate: float
    sharpe_ratio: float
    per_event_results: list[dict] = field(default_factory=list)
    daily_avg_ar: list[float] = field(default_factory=list)
    daily_avg_car: list[float] = field(default_factory=list)

    def save_to_db(self, db_path: str | None = None) -> int:
        """Save to event_studies + event_study_results tables. Returns study_id."""
        conn = sqlite3.connect(db_path or DB_PATH)
        cursor = conn.execute(
            """INSERT INTO event_studies
               (study_name, hypothesis, benchmark, window_pre, window_post,
                num_events, mean_car, median_car, t_statistic, p_value,
                sharpe_ratio, win_rate, results_json, parameters_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.study_name,
                self.hypothesis,
                self.benchmark,
                self.window_pre,
                self.window_post,
                self.num_events,
                self.mean_car,
                self.median_car,
                self.t_statistic,
                self.p_value,
                self.sharpe_ratio,
                self.win_rate,
                json.dumps({"daily_avg_ar": self.daily_avg_ar, "daily_avg_car": self.daily_avg_car}),
                json.dumps({"method": self.method}),
            ),
        )
        study_id = cursor.lastrowid

        for result in self.per_event_results:
            conn.execute(
                """INSERT INTO event_study_results
                   (study_id, event_date, ticker, event_description,
                    car_pre, car_post, car_full,
                    abnormal_returns_json, benchmark_returns_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    study_id,
                    result["event_date"],
                    result["ticker"],
                    result.get("label", ""),
                    result.get("car_pre"),
                    result.get("car_post"),
                    result.get("car_full"),
                    json.dumps(result.get("daily_ar", [])),
                    json.dumps(result.get("benchmark_returns", [])),
                ),
            )

        conn.commit()
        conn.close()
        logger.info("Saved study '%s' (id=%d) with %d event results", self.study_name, study_id, len(self.per_event_results))
        return study_id

    def to_dataframe(self) -> pd.DataFrame:
        """Per-event results as DataFrame."""
        return pd.DataFrame(self.per_event_results)

    def summary(self) -> str:
        """Human-readable summary."""
        sig = "YES" if self.is_significant() else "NO"
        return (
            f"Study: {self.study_name}\n"
            f"Hypothesis: {self.hypothesis}\n"
            f"Method: {self.method} | Benchmark: {self.benchmark}\n"
            f"Window: [{-self.window_pre}, +{self.window_post}]\n"
            f"Events: {self.num_events}\n"
            f"Mean CAR: {self.mean_car:+.2%} | Median CAR: {self.median_car:+.2%}\n"
            f"t-stat: {self.t_statistic:.3f} | p-value: {self.p_value:.4f} | Significant: {sig}\n"
            f"Win Rate: {self.win_rate:.1%} | Sharpe: {self.sharpe_ratio:.2f}"
        )

    def is_significant(self, alpha: float = 0.05) -> bool:
        return self.p_value < alpha


class EventStudy:
    """Reusable event study framework.

    Given a list of (event_date, ticker) pairs and a benchmark, calculates
    abnormal returns, cumulative abnormal returns, and statistical significance.
    """

    def __init__(self, db_path: str | None = None, benchmark_ticker: str = "SPY"):
        self.db_path = db_path or DB_PATH
        self.benchmark_ticker = benchmark_ticker

    def run(
        self,
        events: list[dict],
        study_name: str = "unnamed_study",
        hypothesis: str = "",
        window_pre: int = 5,
        window_post: int = 10,
        estimation_window: int = 120,
        benchmark: str | None = None,
        method: str = "market_adjusted",
    ) -> EventStudyResults:
        """Run a complete event study.

        Args:
            events: List of {"date": "YYYY-MM-DD", "ticker": "XYZ", "label": "description"}
            study_name: Name for the study
            hypothesis: What we're testing
            window_pre: Trading days before event
            window_post: Trading days after event
            estimation_window: Days for expected return estimation (market_model only)
            benchmark: Override default benchmark ticker
            method: 'market_adjusted' or 'market_model'

        Returns:
            EventStudyResults with aggregate stats and per-event details.
        """
        benchmark = benchmark or self.benchmark_ticker
        logger.info(
            "Running event study '%s': %d events, window [-%d, +%d], method=%s, benchmark=%s",
            study_name, len(events), window_pre, window_post, method, benchmark,
        )

        conn = sqlite3.connect(self.db_path)
        per_event_results = []
        all_daily_ars = []  # list of lists, one per event

        for event in events:
            event_date = event["date"]
            ticker = event["ticker"]
            label = event.get("label", "")

            try:
                result = self._process_single_event(
                    conn, ticker, event_date, benchmark,
                    window_pre, window_post, estimation_window, method,
                )
                if result is None:
                    continue

                result["event_date"] = event_date
                result["ticker"] = ticker
                result["label"] = label
                per_event_results.append(result)
                all_daily_ars.append(result["daily_ar"])

            except Exception as e:
                logger.warning("Skipping event %s/%s: %s", ticker, event_date, e)
                continue

        conn.close()

        if not per_event_results:
            logger.warning("No valid events processed for study '%s'", study_name)
            return EventStudyResults(
                study_name=study_name, hypothesis=hypothesis, method=method,
                benchmark=benchmark, window_pre=window_pre, window_post=window_post,
                num_events=0, mean_car=0.0, median_car=0.0,
                t_statistic=0.0, p_value=1.0, win_rate=0.0, sharpe_ratio=0.0,
            )

        # Aggregate statistics
        cars_full = [r["car_full"] for r in per_event_results]
        significance = self._test_significance(cars_full)

        # Average daily AR/CAR across events (align by padding shorter series)
        total_days = window_pre + window_post + 1
        padded_ars = []
        for ar_series in all_daily_ars:
            if len(ar_series) >= total_days:
                padded_ars.append(ar_series[:total_days])
            else:
                padded_ars.append(ar_series + [0.0] * (total_days - len(ar_series)))

        ar_matrix = np.array(padded_ars) if padded_ars else np.zeros((1, total_days))
        daily_avg_ar = np.nanmean(ar_matrix, axis=0).tolist()
        daily_avg_car = np.cumsum(daily_avg_ar).tolist()

        return EventStudyResults(
            study_name=study_name,
            hypothesis=hypothesis,
            method=method,
            benchmark=benchmark,
            window_pre=window_pre,
            window_post=window_post,
            num_events=len(per_event_results),
            mean_car=significance["mean_car"],
            median_car=significance["median_car"],
            t_statistic=significance["t_stat"],
            p_value=significance["p_value"],
            win_rate=significance["win_rate"],
            sharpe_ratio=significance["sharpe"],
            per_event_results=per_event_results,
            daily_avg_ar=daily_avg_ar,
            daily_avg_car=daily_avg_car,
        )

    def _process_single_event(
        self,
        conn: sqlite3.Connection,
        ticker: str,
        event_date: str,
        benchmark: str,
        window_pre: int,
        window_post: int,
        estimation_window: int,
        method: str,
    ) -> dict | None:
        """Process a single event, returning its AR details or None if insufficient data."""

        # Get trading dates from market_data
        event_dt = pd.Timestamp(event_date)

        # We need data from estimation_window + window_pre before event to window_post after
        buffer_days = (estimation_window + window_pre + 30) if method == "market_model" else (window_pre + 30)
        start_dt = event_dt - pd.Timedelta(days=buffer_days * 2)  # calendar days buffer
        end_dt = event_dt + pd.Timedelta(days=window_post * 2)

        stock_prices = self._get_price_data(conn, ticker, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        bench_prices = self._get_price_data(conn, benchmark, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))

        if stock_prices.empty or bench_prices.empty:
            logger.warning("Insufficient price data for %s or %s around %s", ticker, benchmark, event_date)
            return None

        # Calculate returns
        stock_returns = stock_prices["close"].pct_change().dropna()
        bench_returns = bench_prices["close"].pct_change().dropna()

        # Align on common dates
        common_dates = stock_returns.index.intersection(bench_returns.index)
        if len(common_dates) < window_pre + window_post + 5:
            return None

        stock_returns = stock_returns.loc[common_dates]
        bench_returns = bench_returns.loc[common_dates]

        # Find event date in trading calendar (use nearest trading day on or after event)
        trading_dates = common_dates.sort_values()
        event_idx_candidates = trading_dates[trading_dates >= event_dt]
        if event_idx_candidates.empty:
            return None
        event_trading_date = event_idx_candidates[0]
        event_pos = trading_dates.get_loc(event_trading_date)

        # Define windows
        pre_start = max(0, event_pos - window_pre)
        post_end = min(len(trading_dates), event_pos + window_post + 1)

        if event_pos - pre_start < window_pre or post_end - event_pos - 1 < window_post:
            # Not enough trading days in window
            if event_pos - pre_start < 2 or post_end - event_pos - 1 < 2:
                return None

        window_dates = trading_dates[pre_start:post_end]
        window_stock = stock_returns.loc[window_dates]
        window_bench = bench_returns.loc[window_dates]

        # Calculate abnormal returns
        if method == "market_model":
            # Estimation window: before the event window
            est_end_pos = max(0, pre_start - 1)
            est_start_pos = max(0, est_end_pos - estimation_window)
            if est_end_pos - est_start_pos < 30:
                # Fall back to market-adjusted
                method = "market_adjusted"
            else:
                est_dates = trading_dates[est_start_pos:est_end_pos]
                est_stock = stock_returns.loc[est_dates]
                est_bench = bench_returns.loc[est_dates]
                alpha, beta = self._estimate_market_model(est_stock, est_bench)

        if method == "market_adjusted":
            daily_ar = (window_stock - window_bench).values.tolist()
        else:
            expected = alpha + beta * window_bench
            daily_ar = (window_stock - expected).values.tolist()

        daily_car = np.cumsum(daily_ar).tolist()

        # Split CAR into pre and post
        event_offset = event_pos - pre_start
        car_pre = sum(daily_ar[:event_offset]) if event_offset > 0 else 0.0
        car_post = sum(daily_ar[event_offset:]) if event_offset < len(daily_ar) else 0.0
        car_full = sum(daily_ar)

        return {
            "daily_ar": daily_ar,
            "daily_car": daily_car,
            "car_pre": car_pre,
            "car_post": car_post,
            "car_full": car_full,
            "benchmark_returns": window_bench.values.tolist(),
        }

    def _get_price_data(self, conn: sqlite3.Connection, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Pull price data from market_data table. Auto-fetch via yfinance if missing."""
        df = pd.read_sql_query(
            "SELECT date, close FROM market_data WHERE ticker = ? AND date >= ? AND date <= ? ORDER BY date",
            conn,
            params=(ticker, start_date, end_date),
        )

        if df.empty or len(df) < 10:
            # Try to fetch from yfinance
            logger.info("Fetching missing price data for %s via yfinance", ticker)
            try:
                import yfinance as yf
                data = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=False)
                if data.empty:
                    return pd.DataFrame()

                # Handle multi-level columns from yfinance
                if isinstance(data.columns, pd.MultiIndex):
                    data = data.droplevel("Ticker", axis=1)

                # Insert into DB for caching
                for idx, row in data.iterrows():
                    trade_date = idx.strftime("%Y-%m-%d")
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO market_data (ticker, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ticker, trade_date, float(row["Open"]), float(row["High"]),
                             float(row["Low"]), float(row["Close"]), int(row["Volume"])),
                        )
                    except Exception:
                        pass
                conn.commit()

                # Re-query
                df = pd.read_sql_query(
                    "SELECT date, close FROM market_data WHERE ticker = ? AND date >= ? AND date <= ? ORDER BY date",
                    conn,
                    params=(ticker, start_date, end_date),
                )
            except Exception as e:
                logger.error("Failed to fetch %s from yfinance: %s", ticker, e)
                return pd.DataFrame()

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

        return df

    def _estimate_market_model(self, stock_returns: pd.Series, bench_returns: pd.Series) -> tuple[float, float]:
        """Estimate alpha and beta from OLS regression."""
        X = sm.add_constant(bench_returns.values)
        y = stock_returns.values
        try:
            model = sm.OLS(y, X).fit()
            return float(model.params[0]), float(model.params[1])
        except Exception:
            return 0.0, 1.0  # fallback to market-adjusted

    def _test_significance(self, cars: list[float]) -> dict:
        """Test statistical significance of CARs."""
        cars_arr = np.array(cars)
        n = len(cars_arr)

        if n < 2:
            return {
                "mean_car": float(np.mean(cars_arr)) if n > 0 else 0.0,
                "median_car": float(np.median(cars_arr)) if n > 0 else 0.0,
                "std_car": 0.0,
                "t_stat": 0.0,
                "p_value": 1.0,
                "n_events": n,
                "win_rate": float(np.mean(cars_arr > 0)) if n > 0 else 0.0,
                "sharpe": 0.0,
            }

        mean_car = float(np.mean(cars_arr))
        std_car = float(np.std(cars_arr, ddof=1))

        t_stat, p_value = stats.ttest_1samp(cars_arr, 0.0)

        win_rate = float(np.mean(cars_arr > 0))
        sharpe = (mean_car / std_car * np.sqrt(252)) if std_car > 0 else 0.0

        return {
            "mean_car": mean_car,
            "median_car": float(np.median(cars_arr)),
            "std_car": std_car,
            "t_stat": float(t_stat),
            "p_value": float(p_value),
            "n_events": n,
            "win_rate": win_rate,
            "sharpe": float(sharpe),
        }
