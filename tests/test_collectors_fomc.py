"""Tests for collectors.fomc module."""

from unittest.mock import MagicMock, patch

import requests

from collectors.fomc import _scrape_fomc_calendar, _scrape_statement, score_hawkish_dovish


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
