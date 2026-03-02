"""Tests for analysis.reg_shock_detector module."""

import sqlite3
from datetime import date, timedelta
from unittest.mock import patch

from analysis.reg_shock_detector import (
    AGENCY_TICKERS,
    Z_THRESHOLD,
    detect_shocks,
)

# Target agency for seeding test data
_DEFENSE_AGENCY = "Defense Department, Defense Acquisition Regulations System"


def _seed_weekly_events(conn, agency, weeks, events_per_week, start_date=None):
    """Seed regulatory_events with a fixed number of events per week for an agency."""
    if start_date is None:
        start_date = date(2025, 1, 6)  # Monday
    for w in range(weeks):
        pub_date = (start_date + timedelta(weeks=w)).isoformat()
        for _ in range(events_per_week):
            conn.execute(
                """INSERT INTO regulatory_events
                   (source, source_id, event_type, title, agency, publication_date, impact_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "federal_register",
                    f"fr-shock-{agency[:10]}-w{w}-{_}",
                    "final_rule",
                    f"Test rule week {w}",
                    agency,
                    pub_date,
                    4,
                ),
            )
    conn.commit()


class TestDetectShocks:
    """Tests for the detect_shocks function."""

    def test_empty_table_returns_empty_list(self, db_path):
        """No high-impact events at all → empty list."""
        conn = sqlite3.connect(db_path)
        # Clear existing seed data so only low-impact events remain
        conn.execute("DELETE FROM regulatory_events WHERE impact_score >= 4")
        conn.commit()

        shocks = detect_shocks(lookback_weeks=1, conn=conn)
        assert shocks == []
        conn.close()

    def test_insufficient_weeks_skipped(self, db_path):
        """Fewer than MIN_WEEKS of data → no shocks detected."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM regulatory_events")
        conn.commit()

        # Seed only 5 weeks (less than MIN_WEEKS=10)
        _seed_weekly_events(conn, _DEFENSE_AGENCY, weeks=5, events_per_week=2)

        shocks = detect_shocks(lookback_weeks=1, conn=conn)
        assert shocks == []
        conn.close()

    def test_detects_spike_above_threshold(self, db_path):
        """A spike in the final week should be detected as a shock."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM regulatory_events")
        conn.commit()

        # Seed 14 weeks of steady low activity (1 event/week)
        _seed_weekly_events(conn, _DEFENSE_AGENCY, weeks=14, events_per_week=1)

        # Add a big spike in week 15 (10 events — well above 1.5 std dev of 1/week)
        spike_date = (date(2025, 1, 6) + timedelta(weeks=14)).isoformat()
        for i in range(10):
            conn.execute(
                """INSERT INTO regulatory_events
                   (source, source_id, event_type, title, agency, publication_date, impact_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "federal_register",
                    f"fr-spike-{i}",
                    "final_rule",
                    "Spike event",
                    _DEFENSE_AGENCY,
                    spike_date,
                    4,
                ),
            )
        conn.commit()

        shocks = detect_shocks(lookback_weeks=1, conn=conn)
        assert len(shocks) >= 1

        shock = shocks[0]
        assert shock["agency"] == _DEFENSE_AGENCY
        assert shock["z_score"] > Z_THRESHOLD
        assert shock["count"] == 10
        conn.close()

    def test_no_spike_returns_empty(self, db_path):
        """Steady activity with no spike → no shocks."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM regulatory_events")
        conn.commit()

        # Seed 14 weeks of perfectly steady activity (3 events/week)
        _seed_weekly_events(conn, _DEFENSE_AGENCY, weeks=14, events_per_week=3)

        shocks = detect_shocks(lookback_weeks=1, conn=conn)
        assert shocks == []
        conn.close()

    def test_partial_agency_match(self, db_path):
        """Agency name partial match (first segment before comma) should work."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM regulatory_events")
        conn.commit()

        # Use a partial name that doesn't exactly match but starts with "Defense Department"
        partial_name = "Defense Department, Some Other Division"

        # Seed 14 weeks of low activity with partial name
        _seed_weekly_events(conn, partial_name, weeks=14, events_per_week=1)

        # Add spike
        spike_date = (date(2025, 1, 6) + timedelta(weeks=14)).isoformat()
        for i in range(10):
            conn.execute(
                """INSERT INTO regulatory_events
                   (source, source_id, event_type, title, agency, publication_date, impact_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "federal_register",
                    f"fr-partial-spike-{i}",
                    "final_rule",
                    "Partial match spike",
                    partial_name,
                    spike_date,
                    4,
                ),
            )
        conn.commit()

        shocks = detect_shocks(lookback_weeks=1, conn=conn)
        # Should detect via partial match ("Defense Department" prefix)
        assert len(shocks) >= 1
        conn.close()

    def test_shock_metadata_fields(self, db_path):
        """Verify returned shock dict has all expected metadata fields."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM regulatory_events")
        conn.commit()

        _seed_weekly_events(conn, _DEFENSE_AGENCY, weeks=14, events_per_week=1)

        spike_date = (date(2025, 1, 6) + timedelta(weeks=14)).isoformat()
        for i in range(10):
            conn.execute(
                """INSERT INTO regulatory_events
                   (source, source_id, event_type, title, agency, publication_date, impact_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "federal_register",
                    f"fr-meta-{i}",
                    "final_rule",
                    "Meta test",
                    _DEFENSE_AGENCY,
                    spike_date,
                    4,
                ),
            )
        conn.commit()

        shocks = detect_shocks(lookback_weeks=1, conn=conn)
        assert len(shocks) >= 1

        shock = shocks[0]
        expected_keys = {
            "agency", "week_start", "count", "z_score",
            "tickers", "direction", "expected_car", "confidence", "hold_days",
        }
        assert set(shock.keys()) == expected_keys

        # Verify metadata matches AGENCY_TICKERS for defense
        meta = AGENCY_TICKERS[_DEFENSE_AGENCY]
        assert shock["tickers"] == meta["tickers"]
        assert shock["direction"] == meta["direction"]
        assert shock["expected_car"] == meta["expected_car"]
        assert shock["confidence"] == meta["confidence"]
        assert shock["hold_days"] == meta["hold_days"]
        conn.close()

    def test_uses_db_path_fallback_when_no_conn(self, db_path):
        """When conn=None, detect_shocks opens its own connection via DB_PATH."""
        with patch("analysis.reg_shock_detector.DB_PATH", db_path):
            # Should not raise — opens connection from DB_PATH
            shocks = detect_shocks(lookback_weeks=1)
            assert isinstance(shocks, list)
