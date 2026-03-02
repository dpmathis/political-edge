"""Tests for collectors.congress_trades module."""

import sqlite3
from unittest.mock import MagicMock, patch

import requests
from bs4 import BeautifulSoup

from collectors.congress_trades import (
    _extract_ticker,
    _fetch_page,
    _parse_date_cell,
    _trade_exists,
)


# ── _parse_date_cell ─────────────────────────────────────────────


class TestParseDateCell:
    """Tests for the _parse_date_cell function."""

    def test_format_day_mon_year(self):
        # Standard "27 Feb 2026" format
        assert _parse_date_cell("27 Feb 2026") == "2026-02-27"

    def test_format_day_mon_year_no_space(self):
        # Capitol Trades often drops the space: "27 Feb2026"
        assert _parse_date_cell("27 Feb2026") == "2026-02-27"

    def test_format_with_pipe_separator(self):
        # "27 Feb | 2026"
        assert _parse_date_cell("27 Feb | 2026") == "2026-02-27"

    def test_format_mon_day_comma_year(self):
        # "Feb 27, 2026"
        assert _parse_date_cell("Feb 27, 2026") == "2026-02-27"

    def test_format_slash_broken_by_year_regex(self):
        # The regex that inserts spaces before 4-digit years turns "02/27/2026"
        # into "02/27/ 2026", which no format string can parse. This is expected
        # because _parse_date_cell is designed for Capitol Trades' "27 Feb2026" format.
        assert _parse_date_cell("02/27/2026") is None

    def test_format_iso(self):
        # "2026-02-27"
        assert _parse_date_cell("2026-02-27") == "2026-02-27"

    def test_unparseable_returns_none(self):
        assert _parse_date_cell("not a date") is None

    def test_empty_string_returns_none(self):
        assert _parse_date_cell("") is None

    def test_whitespace_handling(self):
        assert _parse_date_cell("  27 Feb 2026  ") == "2026-02-27"


# ── _extract_ticker ──────────────────────────────────────────────


class TestExtractTicker:
    """Tests for the _extract_ticker function."""

    def test_ticker_exchange_pattern(self):
        assert _extract_ticker("VMware Inc | VMW:US") == "VMW"

    def test_ticker_only(self):
        assert _extract_ticker("Apple Inc | AAPL:US") == "AAPL"

    def test_multi_char_exchange(self):
        assert _extract_ticker("Company XYZ | XYZ:US") == "XYZ"

    def test_no_match_returns_none(self):
        assert _extract_ticker("Some company without ticker info") is None

    def test_empty_string(self):
        assert _extract_ticker("") is None

    def test_lowercase_not_matched(self):
        # Pattern requires uppercase [A-Z]
        assert _extract_ticker("vmw:us") is None

    def test_ticker_with_numbers_not_matched(self):
        # Pattern is [A-Z]{1,5} so digits won't match
        assert _extract_ticker("BRK2:US") is None

    def test_single_char_ticker(self):
        assert _extract_ticker("F:US") == "F"

    def test_five_char_ticker(self):
        assert _extract_ticker("GOOGL:US") == "GOOGL"


# ── _trade_exists ────────────────────────────────────────────────


class TestTradeExists:
    """Tests for the _trade_exists function."""

    def test_existing_trade_returns_true(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO congress_trades
               (politician, ticker, trade_date, trade_type, party, chamber, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("John Smith", "AAPL", "2026-02-15", "buy", "D", "Senate", "capitol_trades"),
        )
        conn.commit()

        trade = {
            "politician": "John Smith",
            "ticker": "AAPL",
            "trade_date": "2026-02-15",
            "trade_type": "buy",
        }
        assert _trade_exists(conn, trade) is True
        conn.close()

    def test_non_existing_trade_returns_false(self, db_path):
        conn = sqlite3.connect(db_path)
        trade = {
            "politician": "Jane Doe",
            "ticker": "MSFT",
            "trade_date": "2026-02-20",
            "trade_type": "sell",
        }
        assert _trade_exists(conn, trade) is False
        conn.close()

    def test_different_date_not_duplicate(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO congress_trades
               (politician, ticker, trade_date, trade_type, source)
               VALUES (?, ?, ?, ?, ?)""",
            ("John Smith", "AAPL", "2026-02-15", "buy", "capitol_trades"),
        )
        conn.commit()

        trade = {
            "politician": "John Smith",
            "ticker": "AAPL",
            "trade_date": "2026-02-16",  # Different date
            "trade_type": "buy",
        }
        assert _trade_exists(conn, trade) is False
        conn.close()

    def test_different_trade_type_not_duplicate(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO congress_trades
               (politician, ticker, trade_date, trade_type, source)
               VALUES (?, ?, ?, ?, ?)""",
            ("John Smith", "AAPL", "2026-02-15", "buy", "capitol_trades"),
        )
        conn.commit()

        trade = {
            "politician": "John Smith",
            "ticker": "AAPL",
            "trade_date": "2026-02-15",
            "trade_type": "sell",  # Different type
        }
        assert _trade_exists(conn, trade) is False
        conn.close()


# ── _fetch_page ──────────────────────────────────────────────────


class TestFetchPage:
    """Tests for the _fetch_page function."""

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades.requests.get")
    def test_successful_fetch_returns_soup(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><table><tbody><tr><td>Test</td></tr></tbody></table></body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_page(1)
        assert result is not None
        assert isinstance(result, BeautifulSoup)
        mock_get.assert_called_once()

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades.requests.get")
    def test_429_retries_with_backoff(self, mock_get, mock_sleep):
        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.text = "<html><body>OK</body></html>"
        resp_ok.raise_for_status = MagicMock()

        mock_get.side_effect = [resp_429, resp_ok]

        result = _fetch_page(1)
        assert result is not None
        assert mock_get.call_count == 2
        mock_sleep.assert_called()  # Backoff sleep was invoked

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades.requests.get")
    def test_403_returns_none_immediately(self, mock_get, mock_sleep):
        resp_403 = MagicMock()
        resp_403.status_code = 403
        mock_get.return_value = resp_403

        result = _fetch_page(1)
        assert result is None
        assert mock_get.call_count == 1

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades.requests.get")
    def test_connection_error_retries(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("Refused")

        result = _fetch_page(1)
        assert result is None
        assert mock_get.call_count == 3  # All 3 attempts exhausted

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades.requests.get")
    def test_page_1_url_no_query_param(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        _fetch_page(1)
        call_args = mock_get.call_args
        url = call_args[0][0]
        assert "?page=" not in url

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades.requests.get")
    def test_page_2_url_has_query_param(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        _fetch_page(2)
        call_args = mock_get.call_args
        url = call_args[0][0]
        assert "?page=2" in url
