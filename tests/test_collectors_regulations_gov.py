"""Tests for collectors.regulations_gov module."""

import sqlite3
from unittest.mock import MagicMock, patch

from collectors.regulations_gov import (
    _fetch_documents,
    _insert_events,
    _parse_document,
)


# ── _parse_document ─────────────────────────────────────────────


class TestParseDocument:
    """Tests for the _parse_document function."""

    def _make_doc(self, **overrides):
        doc = {
            "id": "FDA-2026-N-0001-0001",
            "attributes": {
                "documentType": "Proposed Rule",
                "title": "Test Proposed Rule",
                "summary": "A summary of the rule.",
                "agencyId": "FDA",
                "postedDate": "2026-02-15T00:00:00Z",
                "commentEndDate": "2026-04-15T00:00:00Z",
            },
        }
        doc.update(overrides)
        return doc

    def test_valid_document(self):
        doc = self._make_doc()
        result = _parse_document(doc)

        assert result is not None
        assert result["source"] == "regulations_gov"
        assert result["source_id"] == "regsgov-FDA-2026-N-0001-0001"
        assert result["event_type"] == "proposed_rule"
        assert result["title"] == "Test Proposed Rule"
        assert result["summary"] == "A summary of the rule."
        assert result["agency"] == "FDA"
        assert result["publication_date"] == "2026-02-15"
        assert result["comment_deadline"] == "2026-04-15"
        assert result["url"] == "https://www.regulations.gov/document/FDA-2026-N-0001-0001"
        assert "raw_json" in result

    def test_missing_id_returns_none(self):
        doc = self._make_doc(id="")
        assert _parse_document(doc) is None

    def test_missing_attributes_returns_none(self):
        doc = self._make_doc(attributes={})
        assert _parse_document(doc) is None

    def test_date_strips_time(self):
        doc = self._make_doc()
        result = _parse_document(doc)

        assert result["publication_date"] == "2026-02-15"
        assert "T" not in result["publication_date"]
        assert result["comment_deadline"] == "2026-04-15"
        assert "T" not in result["comment_deadline"]

    def test_type_mapping(self):
        doc_proposed = self._make_doc()
        doc_proposed["attributes"]["documentType"] = "Proposed Rule"
        assert _parse_document(doc_proposed)["event_type"] == "proposed_rule"

        doc_rule = self._make_doc()
        doc_rule["attributes"]["documentType"] = "Rule"
        assert _parse_document(doc_rule)["event_type"] == "final_rule"


# ── _insert_events ───────────────────────────────────────────────


def _make_event(source_id="regsgov-test-001"):
    return {
        "source": "regulations_gov",
        "source_id": source_id,
        "event_type": "proposed_rule",
        "title": "Test Rule",
        "summary": "Summary",
        "agency": "FDA",
        "publication_date": "2026-02-15",
        "comment_deadline": "2026-04-15",
        "url": "https://www.regulations.gov/document/test",
        "raw_json": "{}",
    }


class TestInsertEvents:
    """Tests for the _insert_events function."""

    def test_insert_new(self, db_path):
        conn = sqlite3.connect(db_path)
        events = [_make_event("regsgov-new-001"), _make_event("regsgov-new-002")]
        count = _insert_events(conn, events)

        assert count == 2

        rows = conn.execute(
            "SELECT source_id FROM regulatory_events WHERE source_id IN ('regsgov-new-001', 'regsgov-new-002')"
        ).fetchall()
        assert len(rows) == 2
        conn.close()

    def test_duplicate_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        event = _make_event("regsgov-dup-001")
        _insert_events(conn, [event])

        # Insert same source_id again
        count = _insert_events(conn, [event])
        assert count == 0
        conn.close()


# ── _fetch_documents ─────────────────────────────────────────────


class TestFetchDocuments:
    """Tests for the _fetch_documents function."""

    @patch("collectors.regulations_gov.time.sleep")
    @patch("collectors.regulations_gov.requests.get")
    def test_successful_fetch(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"id": "DOC-001"}]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_documents("test-api-key", "2026-01-01")

        assert result == {"data": [{"id": "DOC-001"}]}
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["timeout"] == 30
        assert call_kwargs[1]["headers"]["X-Api-Key"] == "test-api-key"

    @patch("collectors.regulations_gov.time.sleep")
    @patch("collectors.regulations_gov.requests.get")
    def test_403_returns_none(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_get.return_value = mock_response

        result = _fetch_documents("bad-api-key", "2026-01-01")
        assert result is None

    @patch("collectors.regulations_gov.time.sleep")
    @patch("collectors.regulations_gov.requests.get")
    def test_429_retries(self, mock_get, mock_sleep):
        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"data": []}
        resp_ok.raise_for_status = MagicMock()

        mock_get.side_effect = [resp_429, resp_ok]

        result = _fetch_documents("test-api-key", "2026-01-01")
        assert result == {"data": []}
        assert mock_get.call_count == 2
        assert mock_sleep.call_count >= 1


# ── collect ──────────────────────────────────────────────────────


class TestCollect:
    """Tests for the collect function."""

    @patch("collectors.regulations_gov.get_api_key")
    def test_no_api_key_returns_zero(self, mock_get_api_key):
        from collectors.regulations_gov import collect

        mock_get_api_key.return_value = None
        result = collect()
        assert result == 0
        mock_get_api_key.assert_called_once_with("regulations_gov")
