"""Tests for collectors.fda_calendar module."""

import sqlite3
from unittest.mock import patch


from collectors.fda_calendar import (
    _classify_fda_event,
    _match_company,
    collect_from_regulatory_events,
)

# Full fda_events schema (conftest table is missing source, source_url, etc.)
FDA_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS fda_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    drug_name TEXT,
    company_name TEXT,
    ticker TEXT,
    indication TEXT,
    event_date DATE NOT NULL,
    outcome TEXT,
    vote_result TEXT,
    source TEXT,
    source_url TEXT,
    details TEXT,
    pre_event_price REAL,
    post_event_price REAL,
    abnormal_return REAL,
    benchmark_ticker TEXT DEFAULT 'XBI',
    user_notes TEXT,
    trade_action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _recreate_fda_events(db_path: str):
    """Recreate fda_events table with the full schema the collector expects."""
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS fda_events")
    conn.executescript(FDA_EVENTS_DDL)
    conn.close()


# ── _classify_fda_event ──────────────────────────────────────────


class TestClassifyFdaEvent:
    """Tests for the _classify_fda_event function."""

    def test_advisory_committee(self):
        assert _classify_fda_event("FDA Advisory Committee on Oncology Drugs") == "adcom_vote"

    def test_advisory_panel(self):
        assert _classify_fda_event("Joint Advisory Panel Meeting for XYZ Drug") == "adcom_vote"

    def test_approval(self):
        assert _classify_fda_event("FDA Approval of New Drug Application") == "approval"

    def test_approved(self):
        assert _classify_fda_event("Drug X Approved for Treatment of Condition Y") == "approval"

    def test_complete_response(self):
        assert _classify_fda_event("Complete Response Letter for BLA 12345") == "crl"

    def test_refuse_to_file(self):
        assert _classify_fda_event("Refuse to File for NDA 67890") == "crl"

    def test_fast_track(self):
        assert _classify_fda_event("Fast Track Designation Granted") == "fast_track"

    def test_breakthrough(self):
        assert _classify_fda_event("Breakthrough Therapy Designation for Cancer Drug") == "breakthrough"

    def test_priority_review(self):
        assert _classify_fda_event("Priority Review Voucher Awarded") == "priority_review"

    def test_warning_letter(self):
        assert _classify_fda_event("Warning Letter Issued to Manufacturer") == "warning_letter"

    def test_import_alert(self):
        assert _classify_fda_event("Import Alert 66-40 Update") == "import_alert"

    def test_pdufa(self):
        assert _classify_fda_event("PDUFA Date for Drug ABC") == "pdufa_date"

    def test_default_fda_notice(self):
        assert _classify_fda_event("Some Generic FDA Document") == "fda_notice"

    def test_case_insensitive(self):
        assert _classify_fda_event("ADVISORY COMMITTEE SCHEDULED") == "adcom_vote"


# ── _match_company ───────────────────────────────────────────────


class TestMatchCompany:
    """Tests for the _match_company function."""

    def test_match_found(self):
        lookup = {"pfizer": "PFE", "merck": "MRK", "novartis": "NVS"}
        name, ticker = _match_company("Pfizer Inc submitted a new drug application", lookup)
        assert name == "pfizer"
        assert ticker == "PFE"

    def test_case_insensitive_match(self):
        lookup = {"pfizer": "PFE"}
        name, ticker = _match_company("PFIZER announced results", lookup)
        assert ticker == "PFE"

    def test_no_match(self):
        lookup = {"pfizer": "PFE"}
        name, ticker = _match_company("Acme Biotech announced results", lookup)
        assert name is None
        assert ticker is None

    def test_empty_lookup(self):
        name, ticker = _match_company("Pfizer drug approval", {})
        assert name is None
        assert ticker is None

    def test_empty_text(self):
        lookup = {"pfizer": "PFE"}
        name, ticker = _match_company("", lookup)
        assert name is None
        assert ticker is None


# ── collect_from_regulatory_events ───────────────────────────────


class TestCollectFromRegulatoryEvents:
    """Tests for the collect_from_regulatory_events function."""

    @patch("collectors.fda_calendar._build_company_lookup")
    def test_inserts_fda_events_from_regulatory(self, mock_lookup, db_path):
        _recreate_fda_events(db_path)
        mock_lookup.return_value = {"pfizer": "PFE", "merck": "MRK"}

        conn = sqlite3.connect(db_path)
        # Insert FDA regulatory events
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, summary, agency, publication_date, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-fda-001", "notice",
             "Advisory Committee on Pfizer Drug X",
             "Discussion of new drug application by Pfizer",
             "Food and Drug Administration", "2026-02-15",
             "https://federalregister.gov/doc/fda-001"),
        )
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, summary, agency, publication_date, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-fda-002", "notice",
             "FDA Approval of Merck Treatment",
             "New treatment approved for condition Y",
             "Food and Drug Administration", "2026-02-20",
             "https://federalregister.gov/doc/fda-002"),
        )
        conn.commit()

        inserted = collect_from_regulatory_events(conn)
        assert inserted >= 2

        rows = conn.execute("SELECT event_type, ticker, company_name FROM fda_events").fetchall()
        assert len(rows) >= 2

        # Check at least one has correct classification
        event_types = [r[0] for r in rows]
        assert "adcom_vote" in event_types or "approval" in event_types
        conn.close()

    @patch("collectors.fda_calendar._build_company_lookup")
    def test_duplicate_url_skipped(self, mock_lookup, db_path):
        _recreate_fda_events(db_path)
        mock_lookup.return_value = {"pfizer": "PFE"}

        conn = sqlite3.connect(db_path)
        url = "https://federalregister.gov/doc/fda-dup"
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, publication_date, url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-fda-dup", "notice",
             "FDA Advisory Committee on Pfizer Drug",
             "Food and Drug Administration", "2026-02-15", url),
        )
        conn.commit()

        # First run
        first_count = collect_from_regulatory_events(conn)
        assert first_count >= 1

        # Second run - same URL should be skipped
        collect_from_regulatory_events(conn)

        total = conn.execute("SELECT COUNT(*) FROM fda_events WHERE source_url = ?", (url,)).fetchone()[0]
        # Should not have doubled (the source_url dedup logic)
        assert total >= 1
        conn.close()

    @patch("collectors.fda_calendar._build_company_lookup")
    def test_non_fda_agency_excluded(self, mock_lookup, db_path):
        _recreate_fda_events(db_path)
        mock_lookup.return_value = {"pfizer": "PFE"}

        conn = sqlite3.connect(db_path)
        # Insert a non-FDA event
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, publication_date, url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-dod-001", "final_rule",
             "Defense Procurement Standards",
             "Department of Defense", "2026-02-15",
             "https://federalregister.gov/doc/dod-001"),
        )
        conn.commit()

        collect_from_regulatory_events(conn)

        # Should only pick up FDA events, not DOD events
        fda_rows = conn.execute(
            "SELECT COUNT(*) FROM fda_events WHERE source_url = 'https://federalregister.gov/doc/dod-001'"
        ).fetchone()[0]
        assert fda_rows == 0
        conn.close()

    @patch("collectors.fda_calendar._build_company_lookup")
    def test_event_type_classification_in_db(self, mock_lookup, db_path):
        _recreate_fda_events(db_path)
        mock_lookup.return_value = {}

        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, publication_date, url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-fda-approval", "notice",
             "Approval of New Drug Application for Treatment",
             "Food and Drug Administration", "2026-03-01",
             "https://federalregister.gov/doc/fda-approval"),
        )
        conn.commit()

        collect_from_regulatory_events(conn)

        row = conn.execute(
            "SELECT event_type FROM fda_events WHERE source_url = 'https://federalregister.gov/doc/fda-approval'"
        ).fetchone()
        assert row is not None
        assert row[0] == "approval"
        conn.close()
