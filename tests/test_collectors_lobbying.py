"""Tests for collectors.lobbying module."""

import sqlite3

import pytest

from collectors.lobbying import (
    _match_client_ticker,
    _parse_filing,
    _insert_filings,
    calculate_qoq_changes,
)


# -- TestMatchClientTicker ----------------------------------------------------


class TestMatchClientTicker:
    """Tests for the _match_client_ticker function."""

    def test_exact_match(self):
        lookup = {"lockheed martin": "LMT"}
        assert _match_client_ticker("Lockheed Martin", lookup) == "LMT"

    def test_substring_match(self):
        lookup = {"lockheed martin": "LMT"}
        assert _match_client_ticker("Lockheed Martin Corporation", lookup) == "LMT"

    def test_short_name_exact_only(self):
        lookup = {"meta": "META"}
        assert _match_client_ticker("METALS INC", lookup) is None
        assert _match_client_ticker("Meta", lookup) == "META"

    def test_no_match(self):
        lookup = {"lockheed martin": "LMT"}
        assert _match_client_ticker("Unknown Corp", lookup) is None


# -- TestParseFiling ----------------------------------------------------------


class TestParseFiling:
    """Tests for the _parse_filing function."""

    def test_valid_filing(self):
        filing = {
            "filing_uuid": "abc-123",
            "registrant": {"name": "Lobby Firm LLC"},
            "client": {"name": "Lockheed Martin"},
            "income": "$50,000",
            "filing_year": 2026,
            "filing_period": "Q1",
            "lobbying_activities": [
                {
                    "specific_issues": "Defense spending",
                    "government_entities": [{"name": "DOD"}],
                    "lobbyists": [{"name": "John Smith"}],
                }
            ],
        }
        lookup = {"lockheed martin": "LMT"}
        result = _parse_filing(filing, lookup)

        assert result is not None
        assert result["filing_id"] == "abc-123"
        assert result["registrant_name"] == "Lobby Firm LLC"
        assert result["client_name"] == "Lockheed Martin"
        assert result["client_ticker"] == "LMT"
        assert result["amount"] == 50000.0
        assert result["filing_year"] == 2026
        assert result["filing_period"] == "Q1"
        assert "Defense spending" in result["specific_issues"]
        assert "DOD" in result["government_entities"]
        assert "John Smith" in result["lobbyists"]
        assert result["url"] == "https://lda.gov/filings/abc-123"

    def test_missing_uuid_returns_none(self):
        filing = {
            "registrant": {"name": "Firm"},
            "client": {"name": "Client"},
        }
        assert _parse_filing(filing, {}) is None

    def test_missing_client_returns_none(self):
        filing = {
            "filing_uuid": "abc-456",
            "registrant": {"name": "Firm"},
            "client": {"name": ""},
        }
        assert _parse_filing(filing, {}) is None


# -- TestInsertFilings --------------------------------------------------------


class TestInsertFilings:
    """Tests for the _insert_filings function."""

    def test_insert_new(self, db_path):
        conn = sqlite3.connect(db_path)
        filings = [
            {
                "filing_id": "uuid-1",
                "registrant_name": "Lobby Firm",
                "client_name": "Lockheed Martin",
                "client_ticker": "LMT",
                "amount": 50000.0,
                "filing_year": 2026,
                "filing_period": "Q1",
                "specific_issues": "Defense spending",
                "government_entities": "DOD",
                "lobbyists": "John Smith",
                "url": "https://lda.gov/filings/uuid-1",
                "raw_json": "{}",
            },
            {
                "filing_id": "uuid-2",
                "registrant_name": "Lobby Firm",
                "client_name": "Pfizer",
                "client_ticker": "PFE",
                "amount": 30000.0,
                "filing_year": 2026,
                "filing_period": "Q1",
                "specific_issues": "Drug pricing",
                "government_entities": "HHS",
                "lobbyists": "Jane Doe",
                "url": "https://lda.gov/filings/uuid-2",
                "raw_json": "{}",
            },
        ]
        count = _insert_filings(conn, filings)
        assert count == 2

        rows = conn.execute("SELECT COUNT(*) FROM lobbying_filings").fetchone()[0]
        assert rows == 2
        conn.close()

    def test_duplicate_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        filing = {
            "filing_id": "uuid-dup",
            "registrant_name": "Lobby Firm",
            "client_name": "Lockheed Martin",
            "client_ticker": "LMT",
            "amount": 50000.0,
            "filing_year": 2026,
            "filing_period": "Q1",
            "specific_issues": "Defense spending",
            "government_entities": "DOD",
            "lobbyists": "John Smith",
            "url": "https://lda.gov/filings/uuid-dup",
            "raw_json": "{}",
        }
        _insert_filings(conn, [filing])

        # Insert the same filing again
        count = _insert_filings(conn, [filing])
        assert count == 0

        rows = conn.execute("SELECT COUNT(*) FROM lobbying_filings").fetchone()[0]
        assert rows == 1
        conn.close()


# -- TestCalculateQoQChanges -------------------------------------------------


class TestCalculateQoQChanges:
    """Tests for the calculate_qoq_changes function."""

    def test_calculates_pct_change(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO lobbying_filings (filing_id, registrant_name, client_name, client_ticker, amount, filing_year, filing_period) VALUES (?,?,?,?,?,?,?)",
            ("qoq-1", "Firm", "Client", "LMT", 100000, 2025, "Q1"),
        )
        conn.execute(
            "INSERT INTO lobbying_filings (filing_id, registrant_name, client_name, client_ticker, amount, filing_year, filing_period) VALUES (?,?,?,?,?,?,?)",
            ("qoq-2", "Firm", "Client", "LMT", 150000, 2025, "Q2"),
        )
        conn.commit()

        changes = calculate_qoq_changes(conn)
        assert len(changes) == 1
        assert changes[0]["ticker"] == "LMT"
        assert changes[0]["current_amount"] == 150000
        assert changes[0]["prior_amount"] == 100000
        assert changes[0]["pct_change"] == pytest.approx(0.5)
        conn.close()

    def test_flags_spike(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO lobbying_filings (filing_id, registrant_name, client_name, client_ticker, amount, filing_year, filing_period) VALUES (?,?,?,?,?,?,?)",
            ("spike-1", "Firm", "Client", "LMT", 100000, 2025, "Q1"),
        )
        conn.execute(
            "INSERT INTO lobbying_filings (filing_id, registrant_name, client_name, client_ticker, amount, filing_year, filing_period) VALUES (?,?,?,?,?,?,?)",
            ("spike-2", "Firm", "Client", "LMT", 200000, 2025, "Q2"),
        )
        conn.commit()

        changes = calculate_qoq_changes(conn)
        assert len(changes) == 1
        assert changes[0]["spike"] is True
        assert changes[0]["pct_change"] == pytest.approx(1.0)
        conn.close()
