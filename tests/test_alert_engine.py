"""Tests for analysis/alert_engine.py."""

import sqlite3
from unittest.mock import patch



class TestGetAlertConfig:
    """Test _get_alert_config() credential checking."""

    def test_returns_none_without_smtp_user(self):
        from analysis.alert_engine import _get_alert_config
        with patch("analysis.alert_engine.load_config", return_value={"alerts": {"email": "a@b.com", "smtp_password": "x"}}):
            assert _get_alert_config() is None

    def test_returns_none_without_email(self):
        from analysis.alert_engine import _get_alert_config
        with patch("analysis.alert_engine.load_config", return_value={"alerts": {"smtp_user": "u", "smtp_password": "p"}}):
            assert _get_alert_config() is None

    def test_returns_config_when_complete(self):
        from analysis.alert_engine import _get_alert_config
        alerts = {"email": "a@b.com", "smtp_user": "u", "smtp_password": "p"}
        with patch("analysis.alert_engine.load_config", return_value={"alerts": alerts}):
            result = _get_alert_config()
            assert result is not None
            assert result["email"] == "a@b.com"

    def test_returns_none_without_alerts_section(self):
        from analysis.alert_engine import _get_alert_config
        with patch("analysis.alert_engine.load_config", return_value={}):
            assert _get_alert_config() is None


class TestFormatEventBody:
    """Test _format_event_body() output formatting."""

    def test_format_basic(self):
        from analysis.alert_engine import _format_event_body
        rows = [("val1", "val2", None)]
        columns = ["col1", "col2", "col3"]
        body = _format_event_body("Test Rule", rows, columns)
        assert "Test Rule" in body
        assert "col1: val1" in body
        assert "col2: val2" in body
        # None values should be skipped
        assert "col3" not in body

    def test_format_limits_to_10(self):
        from analysis.alert_engine import _format_event_body
        rows = [(f"val{i}",) for i in range(15)]
        columns = ["col"]
        body = _format_event_body("Test", rows, columns)
        assert "5 more events" in body

    def test_format_includes_dashboard_link(self):
        from analysis.alert_engine import _format_event_body
        body = _format_event_body("Test", [("x",)], ["c"])
        assert "political-edge" in body


class TestCheckRegimeChange:
    """Test _check_regime_change() detection."""

    def test_detects_change(self, db_path):
        from analysis.alert_engine import _check_regime_change
        conn = sqlite3.connect(db_path)
        # Clear existing and add two different regimes
        conn.execute("DELETE FROM macro_regimes")
        conn.execute(
            "INSERT INTO macro_regimes (date, quadrant, quadrant_label) VALUES (?, ?, ?)",
            ("2025-11-01", 1, "Goldilocks"),
        )
        conn.execute(
            "INSERT INTO macro_regimes (date, quadrant, quadrant_label) VALUES (?, ?, ?)",
            ("2025-11-02", 3, "Stagflation"),
        )
        conn.commit()
        has_alert, body = _check_regime_change(conn)
        conn.close()
        assert has_alert is True
        assert "Stagflation" in body

    def test_no_change(self, db_path):
        from analysis.alert_engine import _check_regime_change
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_regimes")
        conn.execute(
            "INSERT INTO macro_regimes (date, quadrant, quadrant_label) VALUES (?, ?, ?)",
            ("2025-11-01", 1, "Goldilocks"),
        )
        conn.execute(
            "INSERT INTO macro_regimes (date, quadrant, quadrant_label) VALUES (?, ?, ?)",
            ("2025-11-02", 1, "Goldilocks"),
        )
        conn.commit()
        has_alert, body = _check_regime_change(conn)
        conn.close()
        assert has_alert is False

    def test_insufficient_data(self, db_path):
        from analysis.alert_engine import _check_regime_change
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()
        has_alert, body = _check_regime_change(conn)
        conn.close()
        assert has_alert is False


class TestEvaluateAndSend:
    """Test evaluate_and_send() integration."""

    def test_returns_zero_when_unconfigured(self):
        from analysis.alert_engine import evaluate_and_send
        with patch("analysis.alert_engine._get_alert_config", return_value=None):
            assert evaluate_and_send() == 0

    def test_returns_zero_with_no_rules(self):
        from analysis.alert_engine import evaluate_and_send
        config = {"email": "a@b.com", "smtp_user": "u", "smtp_password": "p", "rules": []}
        with patch("analysis.alert_engine._get_alert_config", return_value=config):
            assert evaluate_and_send() == 0

    @patch("analysis.alert_engine._send_email")
    def test_sends_for_matching_events(self, mock_send, db_path):
        from analysis.alert_engine import evaluate_and_send
        config = {
            "email": "a@b.com",
            "smtp_user": "u",
            "smtp_password": "p",
            "smtp_server": "smtp.test.com",
            "smtp_port": 587,
            "rules": [
                {
                    "name": "High-Impact Test",
                    "table": "regulatory_events",
                    "condition": "impact_score >= 4",
                    "lookback_hours": 999999,
                }
            ],
        }
        with patch("analysis.alert_engine._get_alert_config", return_value=config):
            with patch("analysis.alert_engine.DB_PATH", db_path):
                result = evaluate_and_send()
        assert result == 1
        mock_send.assert_called_once()
