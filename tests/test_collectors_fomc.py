"""Tests for collectors.fomc module."""

import sqlite3
from unittest.mock import MagicMock, patch

import requests

from collectors.fomc import (
    _extract_rate_decision,
    _get_spy_returns,
    _scrape_fomc_calendar,
    _scrape_statement,
    score_hawkish_dovish,
)


class TestScrapeCalendarRetry:
    """Tests for _scrape_fomc_calendar retry logic."""

    @patch("collectors.fomc.time.sleep")
    @patch("collectors.fomc.requests.get")
    def test_retries_on_connection_error(self, mock_get, mock_sleep):
        # First two calls fail, third succeeds with valid HTML
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("Failed"),
            requests.exceptions.ConnectionError("Failed"),
            MagicMock(status_code=200, text="<html><body></body></html>",
                     raise_for_status=MagicMock()),
        ]
        result = _scrape_fomc_calendar()
        assert isinstance(result, list)
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("collectors.fomc.time.sleep")
    @patch("collectors.fomc.requests.get")
    def test_returns_empty_list_on_exhausted_retries(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("Failed")
        result = _scrape_fomc_calendar()
        assert result == []
        assert mock_get.call_count == 3


class TestScrapeStatementRetry:
    """Tests for _scrape_statement retry logic."""

    @patch("collectors.fomc.time.sleep")
    @patch("collectors.fomc.requests.get")
    def test_retries_on_connection_error(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("Failed"),
            MagicMock(status_code=200, text="<html><div id='article'>Statement text here.</div></html>",
                     raise_for_status=MagicMock()),
        ]
        result = _scrape_statement("https://example.com/statement")
        assert result is not None
        assert mock_get.call_count == 2

    @patch("collectors.fomc.time.sleep")
    @patch("collectors.fomc.requests.get")
    def test_returns_none_on_exhausted_retries(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("Failed")
        result = _scrape_statement("https://example.com/statement")
        assert result is None
        assert mock_get.call_count == 3


class TestScoreHawkishDovish:
    """Tests for score_hawkish_dovish scoring."""

    def test_hawkish_text_positive_score(self):
        text = "inflation remains elevated, tightening policy is needed for price stability"
        score = score_hawkish_dovish(text)
        assert score > 0

    def test_dovish_text_negative_score(self):
        text = "accommodation remains supportive, patient approach with gradual easing"
        score = score_hawkish_dovish(text)
        assert score < 0

    def test_neutral_text_zero_score(self):
        score = score_hawkish_dovish("The committee met today.")
        assert score == 0.0

    def test_mixed_text_balanced(self):
        text = "inflation elevated but patient accommodation needed"
        score = score_hawkish_dovish(text)
        # Both hawkish and dovish words present
        assert -1 <= score <= 1


class TestExtractRateDecision:
    """Tests for _extract_rate_decision extraction logic."""

    def test_hike_with_basis_points(self):
        text = "The Committee decided to increase the target range by 50 basis points."
        result = _extract_rate_decision(text)
        assert result == "hike_50"

    def test_hike_without_bp(self):
        text = "decided to increase the target range for the federal funds rate."
        result = _extract_rate_decision(text)
        assert result == "hike_25"

    def test_cut_with_basis_points(self):
        text = "decided to decrease the target range by 25 basis points."
        result = _extract_rate_decision(text)
        assert result == "cut_25"

    def test_hold_with_maintain(self):
        text = "decided to maintain the target range for the federal funds rate."
        result = _extract_rate_decision(text)
        assert result == "hold"

    def test_hold_with_unchanged(self):
        text = "The federal funds rate remained unchanged at the current level."
        result = _extract_rate_decision(text)
        assert result == "hold"

    def test_lower_keyword_triggers_cut(self):
        text = "decided to lower the target range for the federal funds rate."
        result = _extract_rate_decision(text)
        assert result == "cut_25"

    def test_neutral_text_returns_none(self):
        text = "The committee met today and discussed various economic conditions."
        result = _extract_rate_decision(text)
        assert result is None


class TestGetSpyReturns:
    """Tests for _get_spy_returns SPY return calculation."""

    def test_normal_returns(self, db_path):
        conn = sqlite3.connect(db_path)
        # SPY data is already seeded in conftest starting from 2025-10-01
        # Find a date with data around it
        row = conn.execute(
            "SELECT date FROM market_data WHERE ticker = 'SPY' ORDER BY date LIMIT 1 OFFSET 3"
        ).fetchone()
        event_date = row[0]

        day_ret, two_day_ret = _get_spy_returns(conn, event_date)
        conn.close()
        # Should have returns since there's data around this date
        assert day_ret is not None
        assert isinstance(day_ret, float)

    def test_insufficient_rows(self, db_path):
        conn = sqlite3.connect(db_path)
        # Use a date far outside the data range
        day_ret, two_day_ret = _get_spy_returns(conn, "2020-01-01")
        conn.close()
        assert day_ret is None
        assert two_day_ret is None

    def test_event_date_not_trading_day(self, db_path):
        conn = sqlite3.connect(db_path)
        # Use a date that is a Saturday (not in market_data)
        day_ret, two_day_ret = _get_spy_returns(conn, "2025-10-04")  # Saturday
        conn.close()
        # day_return for a non-trading day should be None (date not in day_returns dict)
        assert day_ret is None


class TestFomcCollect:
    """Tests for the collect() main function."""

    @patch("collectors.fomc._scrape_fomc_calendar", return_value=[])
    @patch("collectors.fomc.load_fomc_dates")
    @patch("collectors.fomc.DB_PATH")
    def test_new_events_inserted(self, mock_db, mock_dates, mock_scrape, db_path):
        from collectors.fomc import collect
        mock_db.__str__ = lambda s: db_path
        # Patch DB_PATH to the string value
        with patch("collectors.fomc.DB_PATH", db_path):
            # Clear existing fomc_events
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM fomc_events")
            conn.commit()
            conn.close()

            mock_dates.return_value = [
                {"date": "2025-12-15", "type": "meeting"},
                {"date": "2025-12-16", "type": "meeting"},
            ]

            result = collect()
            assert result == 2

            # Verify insertion
            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT event_date FROM fomc_events ORDER BY event_date").fetchall()
            conn.close()
            assert len(rows) == 2

    @patch("collectors.fomc._scrape_fomc_calendar", return_value=[])
    @patch("collectors.fomc.load_fomc_dates")
    def test_duplicate_skipped(self, mock_dates, mock_scrape, db_path):
        from collectors.fomc import collect
        with patch("collectors.fomc.DB_PATH", db_path):
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM fomc_events")
            conn.commit()
            conn.close()

            mock_dates.return_value = [{"date": "2025-12-15", "type": "meeting"}]

            # First collect
            result1 = collect()
            assert result1 == 1

            # Second collect — same date should be skipped
            result2 = collect()
            assert result2 == 0
