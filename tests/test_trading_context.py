"""Tests for analysis/trading_context.py — historical performance lookup."""

import sqlite3

from analysis.trading_context import (
    get_historical_performance,
    get_time_horizon,
)


# ── get_historical_performance ───────────────────────────────────


class TestGetHistoricalPerformance:
    """Tests for get_historical_performance."""

    def test_with_matching_study(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO event_studies
               (study_name, mean_car, win_rate, p_value, num_events)
               VALUES (?, ?, ?, ?, ?)""",
            ("fda_adcom", 0.032, 0.67, 0.04, 24),
        )
        conn.commit()
        result = get_historical_performance("fda_catalyst", conn)
        conn.close()
        assert result is not None
        assert abs(result["mean_car"] - 0.032) < 1e-6
        assert abs(result["win_rate"] - 0.67) < 1e-6
        assert abs(result["p_value"] - 0.04) < 1e-6
        assert result["n_events"] == 24

    def test_no_matching_study(self, db_path):
        conn = sqlite3.connect(db_path)
        result = get_historical_performance("lobbying_spike", conn)
        conn.close()
        # SIGNAL_STUDY_MAP maps lobbying_spike → None, so returns None
        assert result is None

    def test_eo_type_uses_classifier_constants(self, db_path):
        conn = sqlite3.connect(db_path)
        result = get_historical_performance("eo_defense", conn)
        conn.close()
        assert result is not None
        assert abs(result["mean_car"] - 0.0074) < 1e-6
        assert result["n_events"] == 158

    def test_reg_shock_uses_detector_constants(self, db_path):
        conn = sqlite3.connect(db_path)
        result = get_historical_performance("reg_shock", conn)
        conn.close()
        assert result is not None
        assert result["mean_car"] is not None
        assert result["n_events"] is not None


# ── get_time_horizon ─────────────────────────────────────────────


class TestGetTimeHorizon:
    """Tests for get_time_horizon."""

    def test_known_types(self):
        assert get_time_horizon("fda_catalyst") == 15
        assert get_time_horizon("fomc_drift") == 5
        assert get_time_horizon("reg_shock") == 5

    def test_eo_prefix_returns_3(self):
        assert get_time_horizon("eo_tariff_trade") == 3
        assert get_time_horizon("eo_defense") == 3
        assert get_time_horizon("eo_sanctions") == 3
