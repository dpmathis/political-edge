"""Tests for collectors.federal_register module."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import requests

from collectors.federal_register import (
    TARIFF_TICKERS,
    _detect_tariff_sector,
    _fetch_page,
    _insert_events,
    _parse_document,
    tag_tariff_events,
)


# ── _parse_document ─────────────────────────────────────────────


class TestParseDocument:
    """Tests for the _parse_document function."""

    def _make_doc(self, **overrides) -> dict:
        """Helper to build a valid Federal Register API document dict."""
        doc = {
            "document_number": "2026-01234",
            "type": "Rule",
            "title": "Regulation on Steel Imports",
            "abstract": "Summary of the regulation.",
            "agencies": [{"name": "Department of Commerce"}],
            "publication_date": "2026-02-15",
            "effective_on": "2026-04-01",
            "comments_close_on": None,
            "html_url": "https://www.federalregister.gov/documents/2026/01234",
        }
        doc.update(overrides)
        return doc

    def test_valid_document(self):
        doc = self._make_doc()
        result = _parse_document(doc)

        assert result is not None
        assert result["source"] == "federal_register"
        assert result["source_id"] == "2026-01234"
        assert result["event_type"] == "final_rule"
        assert result["title"] == "Regulation on Steel Imports"
        assert result["summary"] == "Summary of the regulation."
        assert result["agency"] == "Department of Commerce"
        assert result["publication_date"] == "2026-02-15"
        assert result["effective_date"] == "2026-04-01"
        assert result["comment_deadline"] is None
        assert result["url"] == "https://www.federalregister.gov/documents/2026/01234"
        assert "raw_json" in result

    def test_missing_document_number_returns_none(self):
        doc = self._make_doc(document_number=None)
        assert _parse_document(doc) is None

        doc_no_key = self._make_doc()
        del doc_no_key["document_number"]
        assert _parse_document(doc_no_key) is None

    def test_type_map_rule(self):
        doc = self._make_doc(type="Rule")
        result = _parse_document(doc)
        assert result["event_type"] == "final_rule"

    def test_type_map_proposed_rule(self):
        doc = self._make_doc(type="Proposed Rule")
        result = _parse_document(doc)
        assert result["event_type"] == "proposed_rule"

    def test_type_map_presidential_document(self):
        doc = self._make_doc(type="Presidential Document")
        result = _parse_document(doc)
        assert result["event_type"] == "executive_order"

    def test_type_map_notice(self):
        doc = self._make_doc(type="Notice")
        result = _parse_document(doc)
        assert result["event_type"] == "notice"

    def test_unknown_type_falls_back_to_lowercase(self):
        doc = self._make_doc(type="Special Report")
        result = _parse_document(doc)
        assert result["event_type"] == "special_report"

    def test_multiple_agencies(self):
        doc = self._make_doc(agencies=[
            {"name": "Department of Commerce"},
            {"name": "Department of Treasury"},
        ])
        result = _parse_document(doc)
        assert result["agency"] == "Department of Commerce, Department of Treasury"

    def test_agency_with_raw_name_fallback(self):
        doc = self._make_doc(agencies=[{"raw_name": "Some Agency"}])
        result = _parse_document(doc)
        assert result["agency"] == "Some Agency"

    def test_empty_agencies(self):
        doc = self._make_doc(agencies=[])
        result = _parse_document(doc)
        assert result["agency"] == ""

    def test_raw_json_is_valid_json(self):
        doc = self._make_doc()
        result = _parse_document(doc)
        parsed_json = json.loads(result["raw_json"])
        assert parsed_json["document_number"] == "2026-01234"


# ── _fetch_page ──────────────────────────────────────────────────


class TestFetchPage:
    """Tests for the _fetch_page function."""

    @patch("collectors.federal_register.time.sleep")
    @patch("collectors.federal_register.requests.get")
    def test_successful_fetch(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [{"document_number": "1"}]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_page("RULE", page=1, start_date="2026-01-01", end_date="2026-01-31")

        assert result == {"results": [{"document_number": "1"}]}
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["timeout"] == 30

    @patch("collectors.federal_register.time.sleep")
    @patch("collectors.federal_register.requests.get")
    def test_429_retries(self, mock_get, mock_sleep):
        # First two calls: 429. Third call: success.
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp_429)

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"results": []}
        resp_ok.raise_for_status = MagicMock()

        mock_get.side_effect = [resp_429, resp_429, resp_ok]

        result = _fetch_page("RULE")
        assert result == {"results": []}
        assert mock_get.call_count == 3
        # Should have slept for exponential backoff on the two 429s
        assert mock_sleep.call_count == 2

    @patch("collectors.federal_register.time.sleep")
    @patch("collectors.federal_register.requests.get")
    def test_request_exception_retries_and_fails(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = _fetch_page("RULE")
        assert result is None
        assert mock_get.call_count == 3  # 3 attempts

    @patch("collectors.federal_register.time.sleep")
    @patch("collectors.federal_register.requests.get")
    def test_non_429_http_error_returns_none(self, mock_get, mock_sleep):
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp_500)
        mock_get.return_value = resp_500

        result = _fetch_page("RULE")
        assert result is None
        # Should NOT retry on non-429 HTTP errors
        assert mock_get.call_count == 1


# ── _insert_events ───────────────────────────────────────────────


class TestInsertEvents:
    """Tests for the _insert_events function."""

    def _make_event(self, source_id="fr-new-001"):
        return {
            "source": "federal_register",
            "source_id": source_id,
            "event_type": "final_rule",
            "title": "New Rule on Trade",
            "summary": "A summary.",
            "agency": "Department of Commerce",
            "publication_date": "2026-02-20",
            "effective_date": "2026-05-01",
            "comment_deadline": None,
            "url": "https://example.com/doc",
            "raw_json": "{}",
        }

    def test_insert_new_events(self, db_path):
        conn = sqlite3.connect(db_path)
        events = [self._make_event("fr-new-001"), self._make_event("fr-new-002")]
        count = _insert_events(conn, events)

        # Verify at least 1 inserted (INSERT OR IGNORE + total_changes logic)
        assert count >= 1

        rows = conn.execute(
            "SELECT source_id FROM regulatory_events WHERE source_id IN ('fr-new-001', 'fr-new-002')"
        ).fetchall()
        assert len(rows) == 2
        conn.close()

    def test_duplicate_source_id_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        event = self._make_event("fr-test-0")  # Already seeded in conftest
        initial_count = conn.execute("SELECT COUNT(*) FROM regulatory_events").fetchone()[0]

        _insert_events(conn, [event])

        final_count = conn.execute("SELECT COUNT(*) FROM regulatory_events").fetchone()[0]
        assert final_count == initial_count  # No new row
        conn.close()


# ── _detect_tariff_sector ────────────────────────────────────────


class TestDetectTariffSector:
    """Tests for the _detect_tariff_sector function."""

    def test_steel_keyword(self):
        tickers = _detect_tariff_sector("Section 232 Steel Tariff", "Applies to steel imports.")
        assert tickers == "X,NUE,CLF"

    def test_aluminum_keyword(self):
        tickers = _detect_tariff_sector("Aluminum Import Duties", None)
        assert tickers == "AA,CENX"

    def test_semiconductor_keyword(self):
        tickers = _detect_tariff_sector("Semiconductor Trade Restrictions", "")
        assert tickers == "NVDA,AMD,INTC"

    def test_automobile_keyword(self):
        tickers = _detect_tariff_sector("Automobile Import Tariff", "")
        assert tickers == "F,GM,TM"

    def test_unmatched_falls_back_to_default(self):
        tickers = _detect_tariff_sector("Generic Trade Measure", "No sector keywords.")
        assert tickers == TARIFF_TICKERS["default"]
        assert tickers == "SPY,EWC,EWJ"


# ── tag_tariff_events ────────────────────────────────────────────


class TestTagTariffEvents:
    """Tests for the tag_tariff_events function."""

    def test_tags_untagged_tariff_event(self, db_path):
        conn = sqlite3.connect(db_path)
        # Insert an event with "tariff" in the title and no tickers
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, publication_date, tickers)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-tariff-001", "final_rule",
             "New Tariff on Steel Imports", "USTR", "2026-02-28", None),
        )
        conn.commit()

        tagged = tag_tariff_events(conn)
        assert tagged >= 1

        row = conn.execute(
            "SELECT tickers FROM regulatory_events WHERE source_id = 'fr-tariff-001'"
        ).fetchone()
        assert row[0] is not None
        assert "X" in row[0]  # Steel tickers
        conn.close()

    def test_already_tagged_event_not_retagged(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, publication_date, tickers)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-tariff-002", "final_rule",
             "Tariff Adjustment on Aluminum", "USTR", "2026-02-28", "EXISTING"),
        )
        conn.commit()

        tag_tariff_events(conn)

        row = conn.execute(
            "SELECT tickers FROM regulatory_events WHERE source_id = 'fr-tariff-002'"
        ).fetchone()
        assert row[0] == "EXISTING"  # Not overwritten
        conn.close()

    def test_no_tariff_keywords_not_tagged(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, publication_date, tickers)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-nontariff-001", "final_rule",
             "Environmental Protection Standards", "EPA", "2026-02-28", None),
        )
        conn.commit()

        tag_tariff_events(conn)

        row = conn.execute(
            "SELECT tickers FROM regulatory_events WHERE source_id = 'fr-nontariff-001'"
        ).fetchone()
        assert row[0] is None  # Not tagged
        conn.close()
