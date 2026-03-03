"""Tests for collectors.congress module."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import requests

from collectors.congress import (
    _build_source_id,
    _fetch_json,
    _insert_events,
    _is_market_relevant,
    _latest_action_looks_relevant,
)


# -- _is_market_relevant -----------------------------------------------------


class TestIsMarketRelevant:
    """Tests for the _is_market_relevant function."""

    def test_became_law_is_relevant(self):
        action = {"actionCode": "becameLaw", "type": "BecameLaw"}
        relevant, meta = _is_market_relevant(action)

        assert relevant is True
        assert meta is not None
        assert meta["event_type"] == "bill_signed"
        assert meta["impact_base"] == 5

    def test_passed_house_is_relevant(self):
        action = {"actionCode": "passedHouse", "type": "Floor"}
        relevant, meta = _is_market_relevant(action)

        assert relevant is True
        assert meta is not None
        assert meta["event_type"] == "bill_passed_chamber"
        assert meta["impact_base"] == 4

    def test_introduced_not_relevant(self):
        action = {"actionCode": "Intro-H", "type": "IntroReferral"}
        relevant, meta = _is_market_relevant(action)

        assert relevant is False
        assert meta is None

    def test_type_field_fallback(self):
        """When actionCode is absent, type field should be checked."""
        action = {"type": "becameLaw"}
        relevant, meta = _is_market_relevant(action)

        assert relevant is True
        assert meta is not None
        assert meta["event_type"] == "bill_signed"
        assert meta["impact_base"] == 5


# -- _latest_action_looks_relevant -------------------------------------------


class TestLatestActionLooksRelevant:
    """Tests for the _latest_action_looks_relevant pre-filter."""

    def test_became_law_matches(self):
        assert _latest_action_looks_relevant("Became Public Law No: 118-999.") is True

    def test_passed_house_matches(self):
        assert _latest_action_looks_relevant("Passed House by voice vote.") is True

    def test_passed_senate_matches(self):
        assert _latest_action_looks_relevant("Passed Senate with amendments.") is True

    def test_reported_by_matches(self):
        assert _latest_action_looks_relevant("Reported by the Committee on Finance.") is True

    def test_hearing_held_matches(self):
        assert _latest_action_looks_relevant("Hearing held before Subcommittee.") is True

    def test_introduced_does_not_match(self):
        assert _latest_action_looks_relevant("Introduced in House") is False

    def test_referred_does_not_match(self):
        assert _latest_action_looks_relevant("Referred to the Committee on Ways and Means.") is False

    def test_empty_string(self):
        assert _latest_action_looks_relevant("") is False


# -- _build_source_id --------------------------------------------------------


class TestBuildSourceId:
    """Tests for the _build_source_id function."""

    def test_format(self):
        result = _build_source_id(118, "hr", "1234", "becameLaw", "2025-11-15")
        assert result == "congress-118-hr1234-becameLaw-2025-11-15"


# -- _insert_events ----------------------------------------------------------


class TestInsertEvents:
    """Tests for the _insert_events function."""

    def _make_event(self, source_id="congress-test-001"):
        return {
            "source": "congress",
            "source_id": source_id,
            "event_type": "bill_signed",
            "title": "Test Bill Title",
            "summary": "A bill was signed into law.",
            "agency": "Committee on Finance",
            "publication_date": "2025-11-15",
            "url": "https://www.congress.gov/bill/118th-congress/hr/1234",
            "raw_json": json.dumps({"bill": {}, "action": {}}),
        }

    def test_insert_new_events(self, db_path):
        conn = sqlite3.connect(db_path)
        events = [
            self._make_event("congress-new-001"),
            self._make_event("congress-new-002"),
        ]
        count = _insert_events(conn, events)

        assert count == 2

        rows = conn.execute(
            "SELECT source_id FROM regulatory_events "
            "WHERE source_id IN ('congress-new-001', 'congress-new-002')"
        ).fetchall()
        assert len(rows) == 2
        conn.close()

    def test_duplicate_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        event = self._make_event("congress-dup-001")

        # First insert
        count1 = _insert_events(conn, [event])
        assert count1 == 1

        # Second insert with same source_id
        count2 = _insert_events(conn, [event])
        assert count2 == 0

        # Only one row exists
        rows = conn.execute(
            "SELECT COUNT(*) FROM regulatory_events WHERE source_id = 'congress-dup-001'"
        ).fetchone()
        assert rows[0] == 1
        conn.close()


# -- _fetch_json --------------------------------------------------------------


class TestFetchJson:
    """Tests for the _fetch_json function."""

    @patch("collectors.congress.time.sleep")
    @patch("collectors.congress.requests.get")
    def test_successful_fetch(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"bills": [{"number": "1"}]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = _fetch_json(
            "https://api.congress.gov/v3/bill",
            "test-api-key",
            {"limit": 10},
        )

        assert result == {"bills": [{"number": "1"}]}
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[1]["params"]["api_key"] == "test-api-key"
        assert call_args[1]["params"]["format"] == "json"
        assert call_args[1]["params"]["limit"] == 10

    @patch("collectors.congress.time.sleep")
    @patch("collectors.congress.requests.get")
    def test_connection_error_returns_none(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = _fetch_json(
            "https://api.congress.gov/v3/bill",
            "test-api-key",
        )

        assert result is None
        assert mock_get.call_count == 3  # 3 retry attempts


# -- collect ------------------------------------------------------------------


class TestCollect:
    """Tests for the collect function."""

    @patch("collectors.congress.get_api_key")
    def test_no_api_key_returns_zero(self, mock_get_api_key):
        mock_get_api_key.return_value = None

        from collectors.congress import collect

        result = collect(start_date="2025-11-01", end_date="2025-11-15")

        assert result == 0
        mock_get_api_key.assert_called_once_with("congress_gov")

    @patch("collectors.congress.time.sleep")
    @patch("collectors.congress.requests.get")
    @patch("collectors.congress.get_api_key")
    def test_collect_with_mocked_api(self, mock_get_api_key, mock_get, mock_sleep, db_path):
        mock_get_api_key.return_value = "fake-api-key"

        # Mock API responses: bills list, then actions for the one bill
        bills_response = MagicMock()
        bills_response.status_code = 200
        bills_response.raise_for_status = MagicMock()
        bills_response.json.return_value = {
            "bills": [
                {
                    "congress": 118,
                    "type": "HR",
                    "number": "9999",
                    "title": "Test Infrastructure Act",
                    "url": "https://api.congress.gov/v3/bill/118/hr/9999",
                    "latestAction": {
                        "actionDate": "2025-11-10",
                        "text": "Became Public Law No: 118-999.",
                    },
                }
            ]
        }

        actions_response = MagicMock()
        actions_response.status_code = 200
        actions_response.raise_for_status = MagicMock()
        actions_response.json.return_value = {
            "actions": [
                {
                    "actionCode": "becameLaw",
                    "actionDate": "2025-11-10",
                    "type": "BecameLaw",
                    "text": "Became Public Law No: 118-999.",
                    "committee": {"name": ""},
                },
            ]
        }

        # First call is bills list, second is actions for that bill
        mock_get.side_effect = [bills_response, actions_response]

        from collectors.congress import collect

        with patch("collectors.congress.DB_PATH", db_path):
            # Patch the analysis imports that fire when total_inserted > 0
            with patch.dict("sys.modules", {
                "analysis": MagicMock(),
                "analysis.sector_mapper": MagicMock(tag_all_untagged=MagicMock(return_value=0)),
                "analysis.impact_scorer": MagicMock(score_all_unscored=MagicMock(return_value=0)),
            }):
                result = collect(
                    start_date="2025-11-01",
                    end_date="2025-11-15",
                    max_pages=1,
                )

        assert result >= 1

        # Verify the event was actually written to the database
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT source_id, event_type, title FROM regulatory_events "
            "WHERE source = 'congress'"
        ).fetchall()
        assert len(rows) >= 1

        row = rows[0]
        assert "congress-118-hr9999-becameLaw-2025-11-10" == row[0]
        assert row[1] == "bill_signed"
        assert row[2] == "Test Infrastructure Act"
        conn.close()
