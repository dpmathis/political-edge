"""Tests for analysis/macro_regime.py."""

import sqlite3
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from analysis.macro_regime import QUADRANTS, classify_current_regime


def _seed_growth_inflation(
    conn: sqlite3.Connection,
    growth_current: float,
    growth_prior: float,
    inflation_current: float,
    inflation_prior: float,
    series_id_growth: str = "GDPC1",
    yield_curve: float | None = None,
    vix: float | None = None,
):
    """Seed macro_indicators with growth and inflation data for classification.

    Two rows per series (current and prior) are needed for delta calculation.
    The function orders by date DESC LIMIT 2, so current should have the later date.
    """
    today = date.today()
    prior_date = (today - timedelta(days=90)).isoformat()
    current_date = today.isoformat()

    # Growth series (GDPC1 or INDPRO)
    conn.execute(
        """INSERT OR REPLACE INTO macro_indicators (series_id, date, value, rate_of_change_6m)
           VALUES (?, ?, ?, ?)""",
        (series_id_growth, prior_date, 100.0, growth_prior),
    )
    conn.execute(
        """INSERT OR REPLACE INTO macro_indicators (series_id, date, value, rate_of_change_6m)
           VALUES (?, ?, ?, ?)""",
        (series_id_growth, current_date, 101.0, growth_current),
    )

    # Inflation series (CPIAUCSL)
    conn.execute(
        """INSERT OR REPLACE INTO macro_indicators (series_id, date, value, rate_of_change_6m)
           VALUES (?, ?, ?, ?)""",
        ("CPIAUCSL", prior_date, 300.0, inflation_prior),
    )
    conn.execute(
        """INSERT OR REPLACE INTO macro_indicators (series_id, date, value, rate_of_change_6m)
           VALUES (?, ?, ?, ?)""",
        ("CPIAUCSL", current_date, 301.0, inflation_current),
    )

    # Optional supplementary data
    if yield_curve is not None:
        conn.execute(
            """INSERT OR REPLACE INTO macro_indicators (series_id, date, value)
               VALUES (?, ?, ?)""",
            ("T10Y2Y", current_date, yield_curve),
        )
    if vix is not None:
        conn.execute(
            """INSERT OR REPLACE INTO macro_indicators (series_id, date, value)
               VALUES (?, ?, ?)""",
            ("VIXCLS", current_date, vix),
        )

    conn.commit()


class TestQuadrantsDict:
    """Verify QUADRANTS constant has required structure."""

    def test_all_four_quadrants_exist(self):
        assert set(QUADRANTS.keys()) == {1, 2, 3, 4}

    @pytest.mark.parametrize("quadrant", [1, 2, 3, 4])
    def test_quadrant_has_required_keys(self, quadrant):
        required_keys = {
            "label",
            "description",
            "equity_bias",
            "position_modifier",
            "favored_sectors",
            "avoid_sectors",
        }
        assert required_keys.issubset(set(QUADRANTS[quadrant].keys()))

    def test_quadrant_labels(self):
        assert QUADRANTS[1]["label"] == "Goldilocks"
        assert QUADRANTS[2]["label"] == "Reflation"
        assert QUADRANTS[3]["label"] == "Stagflation"
        assert QUADRANTS[4]["label"] == "Deflation"

    @pytest.mark.parametrize("quadrant", [1, 2, 3, 4])
    def test_position_modifier_is_positive(self, quadrant):
        assert QUADRANTS[quadrant]["position_modifier"] > 0

    @pytest.mark.parametrize("quadrant", [1, 2, 3, 4])
    def test_favored_and_avoid_are_lists(self, quadrant):
        assert isinstance(QUADRANTS[quadrant]["favored_sectors"], list)
        assert isinstance(QUADRANTS[quadrant]["avoid_sectors"], list)


class TestClassifyCurrentRegime:
    """Test classify_current_regime() quadrant assignment."""

    def test_q1_goldilocks_growth_up_inflation_down(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # Growth accelerating (current > prior), inflation decelerating (current < prior)
        _seed_growth_inflation(conn, growth_current=0.04, growth_prior=0.02,
                               inflation_current=0.02, inflation_prior=0.04)

        result = classify_current_regime(conn)
        conn.close()

        assert result is not None
        assert result["quadrant"] == 1
        assert result["label"] == "Goldilocks"
        assert result["position_modifier"] == 1.2

    def test_q2_reflation_growth_up_inflation_up(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # Both accelerating
        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.05, inflation_prior=0.02)

        result = classify_current_regime(conn)
        conn.close()

        assert result is not None
        assert result["quadrant"] == 2
        assert result["label"] == "Reflation"
        assert result["position_modifier"] == 1.0

    def test_q3_stagflation_growth_down_inflation_up(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # Growth decelerating, inflation accelerating
        _seed_growth_inflation(conn, growth_current=0.01, growth_prior=0.04,
                               inflation_current=0.06, inflation_prior=0.02)

        result = classify_current_regime(conn)
        conn.close()

        assert result is not None
        assert result["quadrant"] == 3
        assert result["label"] == "Stagflation"
        assert result["position_modifier"] == 0.6

    def test_q4_deflation_growth_down_inflation_down(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # Both decelerating
        _seed_growth_inflation(conn, growth_current=0.01, growth_prior=0.04,
                               inflation_current=0.01, inflation_prior=0.04)

        result = classify_current_regime(conn)
        conn.close()

        assert result is not None
        assert result["quadrant"] == 4
        assert result["label"] == "Deflation"
        assert result["position_modifier"] == 0.4

    def test_returns_none_with_no_data(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        result = classify_current_regime(conn)
        conn.close()

        assert result is None

    def test_returns_none_with_only_one_growth_row(self, db_path):
        """Need at least 2 rows to compute delta — 1 row is insufficient."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        today = date.today().isoformat()
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("GDPC1", today, 100.0, 0.03),
        )
        # Inflation has 2 rows
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("CPIAUCSL", "2025-06-01", 300.0, 0.02),
        )
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("CPIAUCSL", today, 301.0, 0.03),
        )
        conn.commit()

        result = classify_current_regime(conn)
        conn.close()

        assert result is None

    def test_returns_none_with_only_one_inflation_row(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        today = date.today().isoformat()
        prior = (date.today() - timedelta(days=90)).isoformat()

        # Growth has 2 rows
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("GDPC1", prior, 100.0, 0.02),
        )
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("GDPC1", today, 101.0, 0.04),
        )
        # Inflation has only 1 row
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("CPIAUCSL", today, 300.0, 0.03),
        )
        conn.commit()

        result = classify_current_regime(conn)
        conn.close()

        assert result is None

    def test_falls_back_to_indpro_when_gdpc1_missing(self, db_path):
        """If GDPC1 has <2 rows, should fall back to INDPRO."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        today = date.today().isoformat()
        prior = (date.today() - timedelta(days=90)).isoformat()

        # No GDPC1 data; seed INDPRO instead (growth accelerating)
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("INDPRO", prior, 100.0, 0.02),
        )
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("INDPRO", today, 102.0, 0.05),
        )

        # Inflation decelerating => should be Q1 Goldilocks
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("CPIAUCSL", prior, 300.0, 0.04),
        )
        conn.execute(
            """INSERT INTO macro_indicators (series_id, date, value, rate_of_change_6m)
               VALUES (?, ?, ?, ?)""",
            ("CPIAUCSL", today, 301.0, 0.02),
        )
        conn.commit()

        result = classify_current_regime(conn)
        conn.close()

        assert result is not None
        assert result["quadrant"] == 1
        assert result["label"] == "Goldilocks"


class TestClassifyConfidence:
    """Test confidence level assignment."""

    def test_high_confidence_when_both_deltas_clear(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # Large deltas (>0.005 for both)
        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.01,
                               inflation_current=0.01, inflation_prior=0.05)

        result = classify_current_regime(conn)
        conn.close()

        assert result["confidence"] == "high"

    def test_medium_confidence_when_one_delta_clear(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # Growth delta is large (0.04), inflation delta is tiny (0.001)
        _seed_growth_inflation(conn, growth_current=0.06, growth_prior=0.02,
                               inflation_current=0.031, inflation_prior=0.030)

        result = classify_current_regime(conn)
        conn.close()

        assert result["confidence"] == "medium"

    def test_low_confidence_when_both_deltas_small(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # Both deltas very small (<=0.005)
        _seed_growth_inflation(conn, growth_current=0.031, growth_prior=0.030,
                               inflation_current=0.030, inflation_prior=0.031)

        result = classify_current_regime(conn)
        conn.close()

        assert result["confidence"] == "low"


class TestClassifySupplementaryData:
    """Test yield curve and VIX supplementary data in result."""

    def test_includes_yield_curve_and_vix(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.01, inflation_prior=0.03,
                               yield_curve=1.5, vix=18.5)

        result = classify_current_regime(conn)
        conn.close()

        assert result["yield_curve_spread"] == 1.5
        assert result["vix"] == 18.5

    def test_yield_curve_and_vix_are_none_when_missing(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # No T10Y2Y or VIXCLS data seeded
        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.01, inflation_prior=0.03)

        result = classify_current_regime(conn)
        conn.close()

        assert result["yield_curve_spread"] is None
        assert result["vix"] is None


class TestClassifyResultKeys:
    """Test that the result dict contains all expected keys."""

    def test_result_has_all_expected_keys(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.01, inflation_prior=0.03)

        result = classify_current_regime(conn)
        conn.close()

        expected_keys = {
            "quadrant", "label", "description", "confidence",
            "position_modifier", "equity_bias", "favored_sectors",
            "avoid_sectors", "growth_roc", "inflation_roc",
            "yield_curve_spread", "vix",
        }
        assert expected_keys == set(result.keys())

    def test_growth_roc_and_inflation_roc_populated(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.03, inflation_prior=0.01)

        result = classify_current_regime(conn)
        conn.close()

        assert result["growth_roc"] == 0.05
        assert result["inflation_roc"] == 0.03


class TestClassifySavesToDb:
    """Test that classify_current_regime saves result to macro_regimes table."""

    def test_inserts_into_macro_regimes(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.01, inflation_prior=0.03)

        result = classify_current_regime(conn)

        today = date.today().isoformat()
        row = conn.execute(
            "SELECT quadrant, quadrant_label, confidence, position_size_modifier FROM macro_regimes WHERE date = ?",
            (today,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == result["quadrant"]
        assert row[1] == result["label"]
        assert row[2] == result["confidence"]
        assert row[3] == result["position_modifier"]

    def test_replaces_existing_regime_for_same_date(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        # First classification: Q1
        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.01, inflation_prior=0.03)
        result1 = classify_current_regime(conn)
        assert result1["quadrant"] == 1

        # Clear indicators and reclassify as Q3
        conn.execute("DELETE FROM macro_indicators")
        conn.commit()
        _seed_growth_inflation(conn, growth_current=0.01, growth_prior=0.05,
                               inflation_current=0.05, inflation_prior=0.01)
        result2 = classify_current_regime(conn)
        assert result2["quadrant"] == 3

        # Only one row for today
        today = date.today().isoformat()
        count = conn.execute(
            "SELECT COUNT(*) FROM macro_regimes WHERE date = ?", (today,)
        ).fetchone()[0]
        conn.close()

        assert count == 1


class TestClassifyDbPathFallback:
    """Test DB_PATH fallback when no conn is provided."""

    def test_uses_db_path_when_conn_is_none(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()

        _seed_growth_inflation(conn, growth_current=0.05, growth_prior=0.02,
                               inflation_current=0.01, inflation_prior=0.03)
        conn.close()

        with patch("analysis.macro_regime.DB_PATH", db_path):
            result = classify_current_regime()  # No conn argument

        assert result is not None
        assert result["quadrant"] == 1
        assert result["label"] == "Goldilocks"
