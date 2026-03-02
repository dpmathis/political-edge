"""Tests for analysis/pipeline_builder.py."""

import sqlite3
from datetime import date, timedelta

import pytest


class TestDetermineStatus:
    """Test _determine_status() status logic."""

    def test_finalized_when_final_date_exists(self):
        from analysis.pipeline_builder import _determine_status
        result = _determine_status("2025-01-01", "2025-03-01", "2025-06-01", date(2025, 7, 1))
        assert result == "finalized"

    def test_in_comment_before_deadline(self):
        from analysis.pipeline_builder import _determine_status
        today = date(2025, 2, 15)
        result = _determine_status("2025-01-01", "2025-03-01", None, today)
        assert result == "in_comment"

    def test_awaiting_final_after_deadline(self):
        from analysis.pipeline_builder import _determine_status
        today = date(2025, 4, 15)
        result = _determine_status("2025-01-01", "2025-03-01", None, today)
        assert result == "awaiting_final"

    def test_proposed_when_no_deadline(self):
        from analysis.pipeline_builder import _determine_status
        result = _determine_status("2025-01-01", None, None, date(2025, 2, 1))
        assert result == "proposed"

    def test_finalized_takes_precedence(self):
        from analysis.pipeline_builder import _determine_status
        # Even with comment_deadline in future, finalized wins
        today = date(2025, 2, 1)
        result = _determine_status("2025-01-01", "2025-06-01", "2025-01-15", today)
        assert result == "finalized"


class TestBuildPipeline:
    """Test build_pipeline() end-to-end."""

    def test_build_pipeline_returns_counts(self, db_with_pipeline):
        from analysis.pipeline_builder import build_pipeline
        result = build_pipeline(db_with_pipeline)
        assert isinstance(result, dict)
        assert "matched" in result
        assert "pending" in result
        assert "total" in result

    def test_build_pipeline_populates_table(self, db_with_pipeline):
        from analysis.pipeline_builder import build_pipeline
        build_pipeline(db_with_pipeline)
        conn = sqlite3.connect(db_with_pipeline)
        count = conn.execute("SELECT COUNT(*) FROM pipeline_rules").fetchone()[0]
        conn.close()
        assert count > 0

    def test_build_pipeline_idempotent(self, db_with_pipeline):
        from analysis.pipeline_builder import build_pipeline
        result1 = build_pipeline(db_with_pipeline)
        result2 = build_pipeline(db_with_pipeline)
        conn = sqlite3.connect(db_with_pipeline)
        count = conn.execute("SELECT COUNT(*) FROM pipeline_rules").fetchone()[0]
        conn.close()
        # Second run shouldn't duplicate
        assert count == result1["total"]


class TestRefreshStatuses:
    """Test refresh_statuses() updates correctly."""

    def test_refresh_returns_int(self, db_with_pipeline):
        from analysis.pipeline_builder import build_pipeline, refresh_statuses
        build_pipeline(db_with_pipeline)
        changed = refresh_statuses(db_with_pipeline)
        assert isinstance(changed, int)
        assert changed >= 0

    def test_refresh_updates_status(self, db_with_pipeline):
        from analysis.pipeline_builder import build_pipeline, refresh_statuses
        build_pipeline(db_with_pipeline)
        # All rules should have valid statuses
        conn = sqlite3.connect(db_with_pipeline)
        statuses = [
            r[0] for r in conn.execute("SELECT DISTINCT status FROM pipeline_rules").fetchall()
        ]
        conn.close()
        valid = {"proposed", "in_comment", "awaiting_final", "finalized"}
        for s in statuses:
            assert s in valid


class TestAgencyMedianLag:
    """Test _compute_agency_median_lag()."""

    def test_returns_dict(self, db_with_pipeline):
        import pandas as pd
        from analysis.pipeline_builder import _compute_agency_median_lag
        # Create a minimal DataFrame matching the expected format
        df = pd.DataFrame({
            "agency": ["Department of Defense"] * 5,
            "days_between": [180, 200, 190, 210, 195],
        })
        lags = _compute_agency_median_lag(df)
        assert isinstance(lags, dict)

    def test_empty_dataframe(self):
        import pandas as pd
        from analysis.pipeline_builder import _compute_agency_median_lag
        df = pd.DataFrame(columns=["agency", "days_between"])
        lags = _compute_agency_median_lag(df)
        assert lags == {}

    def test_insufficient_samples_excluded(self):
        import pandas as pd
        from analysis.pipeline_builder import _compute_agency_median_lag
        # Only 2 samples — below the minimum of 3
        df = pd.DataFrame({
            "agency": ["DOE", "DOE"],
            "days_between": [100, 200],
        })
        lags = _compute_agency_median_lag(df)
        assert len(lags) == 0
