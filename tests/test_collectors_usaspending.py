"""Tests for collectors.usaspending module."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import requests

from collectors.usaspending import (
    _build_ticker_lookup,
    _fetch_page,
    _format_place_of_performance,
    _insert_awards,
    _match_recipient_ticker,
    _parse_award,
)


# ── _match_recipient_ticker ──────────────────────────────────────


class TestMatchRecipientTicker:
    """Tests for the _match_recipient_ticker function."""

    def test_exact_match(self):
        lookup = {"LOCKHEED MARTIN": "LMT", "BOEING": "BA"}
        assert _match_recipient_ticker("LOCKHEED MARTIN", lookup) == "LMT"

    def test_substring_match(self):
        lookup = {"LOCKHEED MARTIN": "LMT", "BOEING": "BA"}
        assert _match_recipient_ticker("LOCKHEED MARTIN CORPORATION", lookup) == "LMT"

    def test_case_insensitive(self):
        lookup = {"LOCKHEED MARTIN": "LMT"}
        assert _match_recipient_ticker("Lockheed Martin Corp", lookup) == "LMT"

    def test_unknown_returns_none(self):
        lookup = {"LOCKHEED MARTIN": "LMT"}
        assert _match_recipient_ticker("Acme Widgets Inc", lookup) is None

    def test_empty_lookup(self):
        assert _match_recipient_ticker("Boeing Company", {}) is None

    def test_empty_recipient(self):
        lookup = {"LOCKHEED MARTIN": "LMT"}
        assert _match_recipient_ticker("", lookup) is None


# ── _build_ticker_lookup ─────────────────────────────────────────


class TestBuildTickerLookup:
    """Tests for the _build_ticker_lookup function."""

    def test_builds_from_company_contractor_map(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO company_contractor_map (ticker, contractor_name) VALUES (?, ?)",
            ("LMT", "Lockheed Martin"),
        )
        conn.execute(
            "INSERT INTO company_contractor_map (ticker, contractor_name) VALUES (?, ?)",
            ("BA", "Boeing Company"),
        )
        conn.commit()

        lookup = _build_ticker_lookup(conn)
        conn.close()

        assert lookup["LOCKHEED MARTIN"] == "LMT"
        assert lookup["BOEING COMPANY"] == "BA"

    def test_empty_table_returns_empty_dict(self, db_path):
        conn = sqlite3.connect(db_path)
        lookup = _build_ticker_lookup(conn)
        conn.close()
        assert lookup == {}


# ── _parse_award ─────────────────────────────────────────────────


class TestParseAward:
    """Tests for the _parse_award function."""

    def _make_award(self, **overrides) -> dict:
        award = {
            "generated_internal_id": "CONT_IDV_ABC123",
            "Recipient Name": "LOCKHEED MARTIN CORPORATION",
            "Award Amount": 50_000_000,
            "Awarding Agency": "Department of Defense",
            "Start Date": "2026-02-01",
            "Description": "Fighter jet maintenance contract",
            "NAICS Code": "336411",
            "Place of Performance State Code": "TX",
            "Place of Performance Country Code": "USA",
            "Contract Award Type": "D",
        }
        award.update(overrides)
        return award

    def test_valid_award_parsing(self):
        lookup = {"LOCKHEED MARTIN": "LMT"}
        award = self._make_award()
        result = _parse_award(award, lookup)

        assert result is not None
        assert result["award_id"] == "CONT_IDV_ABC123"
        assert result["recipient_name"] == "LOCKHEED MARTIN CORPORATION"
        assert result["recipient_ticker"] == "LMT"
        assert result["awarding_agency"] == "Department of Defense"
        assert result["award_amount"] == 50_000_000
        assert result["award_date"] == "2026-02-01"
        assert result["description"] == "Fighter jet maintenance contract"
        assert result["naics_code"] == "336411"
        assert result["place_of_performance"] == "TX, USA"
        assert result["contract_type"] == "D"
        assert "CONT_IDV_ABC123" in result["url"]
        assert "raw_json" in result

    def test_missing_award_id_returns_none(self):
        award = self._make_award(generated_internal_id=None)
        assert _parse_award(award, {}) is None

    def test_missing_recipient_name_returns_none(self):
        award = self._make_award(**{"Recipient Name": ""})
        assert _parse_award(award, {}) is None

    def test_no_matching_ticker(self):
        lookup = {"BOEING": "BA"}
        award = self._make_award()
        result = _parse_award(award, lookup)
        assert result is not None
        assert result["recipient_ticker"] is None

    def test_raw_json_is_valid(self):
        result = _parse_award(self._make_award(), {})
        parsed_json = json.loads(result["raw_json"])
        assert parsed_json["generated_internal_id"] == "CONT_IDV_ABC123"


# ── _format_place_of_performance ─────────────────────────────────


class TestFormatPlaceOfPerformance:
    """Tests for the _format_place_of_performance function."""

    def test_both_state_and_country(self):
        assert _format_place_of_performance("CA", "USA") == "CA, USA"

    def test_only_state(self):
        assert _format_place_of_performance("TX", None) == "TX"

    def test_only_country(self):
        assert _format_place_of_performance(None, "DEU") == "DEU"

    def test_neither(self):
        assert _format_place_of_performance(None, None) is None


# ── _fetch_page ──────────────────────────────────────────────────


class TestFetchPage:
    """Tests for the _fetch_page function."""

    @patch("collectors.usaspending.time.sleep")
    @patch("collectors.usaspending.requests.post")
    def test_successful_fetch(self, mock_post, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [{"Award ID": "123"}]}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = _fetch_page(1, "2026-01-01", "2026-01-31")
        assert result == {"results": [{"Award ID": "123"}]}
        mock_post.assert_called_once()

    @patch("collectors.usaspending.time.sleep")
    @patch("collectors.usaspending.requests.post")
    def test_429_retries(self, mock_post, mock_sleep):
        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"results": []}
        resp_ok.raise_for_status = MagicMock()

        mock_post.side_effect = [resp_429, resp_ok]

        result = _fetch_page(1, "2026-01-01", "2026-01-31")
        assert result == {"results": []}
        assert mock_post.call_count == 2
        assert mock_sleep.call_count >= 1

    @patch("collectors.usaspending.time.sleep")
    @patch("collectors.usaspending.requests.post")
    def test_request_exception_after_retries(self, mock_post, mock_sleep):
        mock_post.side_effect = requests.exceptions.ConnectionError("Failed")

        result = _fetch_page(1, "2026-01-01", "2026-01-31")
        assert result is None
        assert mock_post.call_count == 3


# ── _insert_awards + duplicate detection ─────────────────────────


class TestInsertAwards:
    """Tests for the _insert_awards function."""

    def _make_award_row(self, award_id="award-001"):
        return {
            "award_id": award_id,
            "recipient_name": "Test Corp",
            "recipient_ticker": "TST",
            "awarding_agency": "DOD",
            "award_amount": 10_000_000,
            "award_date": "2026-02-01",
            "description": "Test contract",
            "naics_code": "336411",
            "place_of_performance": "TX, USA",
            "contract_type": "D",
            "url": "https://usaspending.gov/award/award-001",
            "raw_json": "{}",
        }

    def test_insert_new_awards(self, db_path):
        conn = sqlite3.connect(db_path)
        awards = [self._make_award_row("award-001"), self._make_award_row("award-002")]
        count = _insert_awards(conn, awards)

        assert count == 2
        rows = conn.execute(
            "SELECT award_id FROM contract_awards WHERE award_id IN ('award-001', 'award-002')"
        ).fetchall()
        assert len(rows) == 2
        conn.close()

    def test_duplicate_award_id_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        award = self._make_award_row("award-dup")
        _insert_awards(conn, [award])

        # Insert same award_id again
        count = _insert_awards(conn, [award])
        assert count == 0

        rows = conn.execute(
            "SELECT COUNT(*) FROM contract_awards WHERE award_id = 'award-dup'"
        ).fetchone()
        assert rows[0] == 1
        conn.close()

    def test_mixed_new_and_duplicate(self, db_path):
        conn = sqlite3.connect(db_path)
        _insert_awards(conn, [self._make_award_row("award-existing")])

        awards = [self._make_award_row("award-existing"), self._make_award_row("award-new")]
        count = _insert_awards(conn, awards)

        # Only the new one should be inserted
        assert count == 1
        total = conn.execute("SELECT COUNT(*) FROM contract_awards").fetchone()[0]
        assert total == 2
        conn.close()
