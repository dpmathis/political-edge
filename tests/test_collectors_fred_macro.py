import sqlite3
from unittest.mock import patch

import pandas as pd

from collectors.fred_macro import _get_fred_client, _insert_observations, calculate_roc, collect


class TestGetFredClient:
    @patch("collectors.fred_macro.get_api_key")
    def test_returns_fred_when_key_exists(self, mock_key):
        mock_key.return_value = "test-key"
        with patch("fredapi.Fred") as MockFred:
            client = _get_fred_client()
            MockFred.assert_called_once_with(api_key="test-key")
            assert client is not None

    @patch("collectors.fred_macro.get_api_key")
    def test_returns_none_when_no_key(self, mock_key):
        mock_key.return_value = None
        assert _get_fred_client() is None


class TestInsertObservations:
    def test_insert_valid_observations(self, db_path):
        conn = sqlite3.connect(db_path)
        dates = pd.date_range("2026-01-01", periods=3, freq="MS")
        obs = pd.Series([100.0, 101.5, 103.2], index=dates)
        count = _insert_observations(conn, "TEST_SERIES", obs)
        assert count == 3
        rows = conn.execute("SELECT * FROM macro_indicators WHERE series_id='TEST_SERIES'").fetchall()
        assert len(rows) == 3
        conn.close()

    def test_skips_missing_values(self, db_path):
        conn = sqlite3.connect(db_path)
        dates = pd.date_range("2026-01-01", periods=3, freq="MS")
        # FRED uses "." for missing values; _insert_observations checks str(value) == "."
        obs = pd.Series([100.0, ".", 103.2], index=dates, dtype=object)
        count = _insert_observations(conn, "SKIP_SERIES", obs)
        assert count == 2  # skipped "."
        conn.close()

    def test_duplicate_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        dates = pd.date_range("2026-01-01", periods=2, freq="MS")
        obs = pd.Series([100.0, 101.5], index=dates)
        _insert_observations(conn, "DUP_SERIES", obs)
        count2 = _insert_observations(conn, "DUP_SERIES", obs)
        assert count2 == 0
        conn.close()


class TestCalculateRoc:
    def test_calculates_roc(self, db_path):
        conn = sqlite3.connect(db_path)
        # Seed 15 months of data
        for i in range(15):
            d = f"2025-{(i % 12) + 1:02d}-01"
            if i >= 12:
                d = f"2026-{(i - 12) + 1:02d}-01"
            conn.execute(
                "INSERT INTO macro_indicators (series_id, date, value) VALUES (?,?,?)",
                ("ROC_TEST", d, 100 + i),
            )
        conn.commit()
        calculate_roc(conn, "ROC_TEST")
        # Check that some RoC values were set
        row = conn.execute(
            "SELECT rate_of_change_6m FROM macro_indicators WHERE series_id='ROC_TEST' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        assert row[0] is not None
        conn.close()

    def test_skips_short_series(self, db_path):
        conn = sqlite3.connect(db_path)
        for i in range(5):
            conn.execute(
                "INSERT INTO macro_indicators (series_id, date, value) VALUES (?,?,?)",
                ("SHORT", f"2026-0{i + 1}-01", 100 + i),
            )
        conn.commit()
        calculate_roc(conn, "SHORT")  # Should return without doing anything
        row = conn.execute(
            "SELECT rate_of_change_6m FROM macro_indicators WHERE series_id='SHORT' LIMIT 1"
        ).fetchone()
        assert row[0] is None  # Not calculated
        conn.close()


class TestCollect:
    @patch("collectors.fred_macro.get_api_key")
    def test_no_api_key_returns_zero(self, mock_key):
        mock_key.return_value = None
        assert collect() == 0
