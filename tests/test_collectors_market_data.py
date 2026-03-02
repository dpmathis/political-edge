"""Tests for collectors.market_data module."""

import sqlite3
from unittest.mock import patch

import numpy as np
import pandas as pd

from collectors.market_data import _get_watchlist_tickers, _get_latest_date, collect


class TestGetWatchlistTickers:
    """Tests for _get_watchlist_tickers."""

    def test_returns_active_tickers(self, db_path):
        conn = sqlite3.connect(db_path)
        tickers = _get_watchlist_tickers(conn)
        conn.close()
        assert sorted(tickers) == ["LMT", "PFE"]

    def test_empty_watchlist(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM watchlist")
        conn.commit()
        tickers = _get_watchlist_tickers(conn)
        conn.close()
        assert tickers == []


class TestGetLatestDate:
    """Tests for _get_latest_date."""

    def test_returns_max_date_for_ticker(self, db_path):
        conn = sqlite3.connect(db_path)
        result = _get_latest_date(conn, "SPY")
        conn.close()
        assert result is not None
        assert isinstance(result, str)
        # Should be a valid date string in YYYY-MM-DD format
        assert len(result) == 10
        assert result[4] == "-" and result[7] == "-"

    def test_returns_none_for_unknown(self, db_path):
        conn = sqlite3.connect(db_path)
        result = _get_latest_date(conn, "ZZZZZ")
        conn.close()
        assert result is None


class TestCollect:
    """Tests for the collect function."""

    @patch("collectors.market_data.yf.download")
    @patch("collectors.market_data.DB_PATH")
    def test_collect_inserts_rows(self, mock_db_path, mock_download, db_path):
        mock_db_path.__str__ = lambda self: db_path
        # Patch DB_PATH so sqlite3.connect(DB_PATH) uses our temp db
        with patch("collectors.market_data.DB_PATH", db_path):
            dates = pd.date_range("2026-01-02", periods=3, freq="B")
            cols = pd.MultiIndex.from_product(
                [["LMT", "PFE"], ["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
            )
            data = pd.DataFrame(
                np.random.rand(3, 12) * 100, index=dates, columns=cols
            )
            data[("LMT", "Volume")] = [1000000, 2000000, 3000000]
            data[("PFE", "Volume")] = [500000, 600000, 700000]
            mock_download.return_value = data

            result = collect(start_date="2026-01-02", end_date="2026-01-07")

            assert result > 0
            mock_download.assert_called_once()

            # Verify rows were actually inserted into market_data
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT COUNT(*) FROM market_data WHERE ticker IN ('LMT', 'PFE') AND date >= '2026-01-02'"
            ).fetchone()[0]
            conn.close()
            assert rows == 6  # 3 dates x 2 tickers

    @patch("collectors.market_data.yf.download")
    def test_collect_empty_download(self, mock_download, db_path):
        with patch("collectors.market_data.DB_PATH", db_path):
            mock_download.return_value = pd.DataFrame()
            result = collect(start_date="2026-01-02", end_date="2026-01-07")
            assert result == 0

    @patch("collectors.market_data.yf.download")
    def test_no_tickers_returns_zero(self, mock_download, db_path):
        # Clear watchlist so there are no active tickers
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM watchlist")
        conn.commit()
        conn.close()

        with patch("collectors.market_data.DB_PATH", db_path):
            result = collect(start_date="2026-01-02", end_date="2026-01-07")
            assert result == 0
            mock_download.assert_not_called()
