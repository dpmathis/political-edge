"""Tests for collectors.congress_trades module."""

import sqlite3
from unittest.mock import MagicMock, patch

import requests
from bs4 import BeautifulSoup

from collectors.congress_trades import (
    _extract_ticker,
    _fetch_page,
    _insert_trades,
    _parse_date_cell,
    _parse_trades_table,
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


# ── _parse_trades_table ─────────────────────────────────────────

_VALID_ROW_HTML = """
<html><body><table><tbody><tr>
  <td><a href="/politician/john-smith">John Smith</a>|Democrat|Senate|CA</td>
  <td><a href="/issuer/aapl">Apple Inc</a>|AAPL:US</td>
  <td>15 Feb 2026</td>
  <td>10 Feb 2026</td>
  <td>Spouse</td>
  <td>Equities</td>
  <td>buy</td>
  <td>1K–15K</td>
</tr></tbody></table></body></html>
"""


class TestParseTradesTable:
    """Tests for the _parse_trades_table function."""

    def test_valid_row_parsed(self):
        soup = BeautifulSoup(_VALID_ROW_HTML, "lxml")
        trades = _parse_trades_table(soup)
        assert len(trades) == 1
        assert trades[0]["politician"] == "John Smith"
        assert trades[0]["ticker"] == "AAPL"
        assert trades[0]["trade_type"] == "buy"
        assert trades[0]["party"] == "D"
        assert trades[0]["chamber"] == "Senate"

    def test_missing_columns_skipped(self):
        html = "<html><body><table><tbody><tr><td>Only one</td><td>Two</td></tr></tbody></table></body></html>"
        soup = BeautifulSoup(html, "lxml")
        trades = _parse_trades_table(soup)
        assert trades == []

    def test_empty_table(self):
        html = "<html><body><table><tbody></tbody></table></body></html>"
        soup = BeautifulSoup(html, "lxml")
        trades = _parse_trades_table(soup)
        assert trades == []

    def test_no_table_at_all(self):
        html = "<html><body><p>No table here</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        trades = _parse_trades_table(soup)
        assert trades == []

    def test_multiple_rows(self):
        row = """<tr>
          <td><a href="/p/a">Alice</a>|Republican|House|TX</td>
          <td><a href="/i/msft">Microsoft</a>|MSFT:US</td>
          <td>20 Feb 2026</td><td>18 Feb 2026</td>
          <td>Self</td><td>Equities</td><td>sell</td><td>50K–100K</td>
        </tr>"""
        html = f"<html><body><table><tbody>{row}{row}</tbody></table></body></html>"
        soup = BeautifulSoup(html, "lxml")
        trades = _parse_trades_table(soup)
        assert len(trades) == 2
        assert all(t["politician"] == "Alice" for t in trades)


# ── _insert_trades ──────────────────────────────────────────────


class TestInsertTrades:
    """Tests for the _insert_trades function."""

    def test_new_trades_inserted(self, db_path):
        conn = sqlite3.connect(db_path)
        trades = [
            {
                "politician": "Jane Doe",
                "party": "D",
                "chamber": "Senate",
                "ticker": "GOOGL",
                "trade_type": "buy",
                "amount_range": "$1K – $15K",
                "trade_date": "2026-02-20",
                "disclosure_date": "2026-02-25",
                "asset_description": "Alphabet Inc",
            },
        ]
        inserted = _insert_trades(conn, trades)
        assert inserted == 1

        row = conn.execute(
            "SELECT politician, ticker FROM congress_trades WHERE ticker = 'GOOGL'"
        ).fetchone()
        assert row is not None
        assert row[0] == "Jane Doe"
        conn.close()

    def test_duplicate_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        trade = {
            "politician": "John Doe",
            "party": "R",
            "chamber": "House",
            "ticker": "MSFT",
            "trade_type": "buy",
            "amount_range": "$1K – $15K",
            "trade_date": "2026-02-20",
            "disclosure_date": "2026-02-25",
            "asset_description": "Microsoft",
        }
        # Insert first time
        inserted1 = _insert_trades(conn, [trade])
        assert inserted1 == 1

        # Insert again — should be duplicate
        inserted2 = _insert_trades(conn, [trade])
        assert inserted2 == 0
        conn.close()

    def test_missing_required_field_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        trades = [
            {
                "politician": "Jane Doe",
                "party": "D",
                "chamber": "Senate",
                "ticker": None,  # Missing ticker
                "trade_type": "buy",
                "amount_range": None,
                "trade_date": "2026-02-20",
                "disclosure_date": None,
                "asset_description": None,
            },
        ]
        inserted = _insert_trades(conn, trades)
        assert inserted == 0
        conn.close()


# ── collect ──────────────────────────────────────────────────────


class TestCongressTradesCollect:
    """Tests for the collect() main function."""

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades._fetch_page")
    def test_single_page_collection(self, mock_fetch, mock_sleep, db_path):
        from collectors.congress_trades import collect
        soup = BeautifulSoup(_VALID_ROW_HTML, "lxml")
        # First page returns data, second returns None (stop)
        mock_fetch.side_effect = [soup, None]

        with patch("collectors.congress_trades.DB_PATH", db_path):
            result = collect()

        assert result >= 1

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades._fetch_page")
    def test_pagination_stops_on_empty(self, mock_fetch, mock_sleep, db_path):
        from collectors.congress_trades import collect
        # Empty table — no trades found
        empty_soup = BeautifulSoup("<html><body><table><tbody></tbody></table></body></html>", "lxml")
        mock_fetch.return_value = empty_soup

        with patch("collectors.congress_trades.DB_PATH", db_path):
            result = collect()

        assert result == 0
        # Should stop after first empty page
        assert mock_fetch.call_count == 1

    @patch("collectors.congress_trades.time.sleep")
    @patch("collectors.congress_trades._fetch_page", return_value=None)
    def test_fetch_failure_page1(self, mock_fetch, mock_sleep, db_path):
        from collectors.congress_trades import collect
        with patch("collectors.congress_trades.DB_PATH", db_path):
            result = collect()
        assert result == 0
