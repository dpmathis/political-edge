"""Tests for analysis/alert_engine.py."""

import sqlite3
from datetime import date, timedelta
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


class TestCheckHighConvictionSignals:
    """Test _check_high_conviction_signals() detection."""

    def test_returns_signals_when_present(self, db_path):
        from analysis.alert_engine import _check_high_conviction_signals
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM trading_signals")
        now = date.today().isoformat()
        for i in range(3):
            conn.execute(
                """INSERT INTO trading_signals
                   (signal_date, ticker, signal_type, direction, conviction, status, rationale, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (now, f"TK{i}", "regulatory_event", "long", "high", "pending", f"Reason {i}"),
            )
        conn.commit()

        has_alert, body = _check_high_conviction_signals(conn)
        conn.close()
        assert has_alert is True
        assert "High-Conviction" in body
        assert "TK0" in body

    def test_returns_empty_when_no_signals(self, db_path):
        from analysis.alert_engine import _check_high_conviction_signals
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM trading_signals")
        conn.commit()

        has_alert, body = _check_high_conviction_signals(conn)
        conn.close()
        assert has_alert is False
        assert body == ""

    def test_ignores_non_pending_signals(self, db_path):
        from analysis.alert_engine import _check_high_conviction_signals
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM trading_signals")
        conn.execute(
            """INSERT INTO trading_signals
               (signal_date, ticker, signal_type, direction, conviction, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (date.today().isoformat(), "LMT", "fda_catalyst", "long", "high", "active"),
        )
        conn.commit()

        has_alert, body = _check_high_conviction_signals(conn)
        conn.close()
        assert has_alert is False

    def test_ignores_non_high_conviction(self, db_path):
        from analysis.alert_engine import _check_high_conviction_signals
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM trading_signals")
        conn.execute(
            """INSERT INTO trading_signals
               (signal_date, ticker, signal_type, direction, conviction, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (date.today().isoformat(), "LMT", "fda_catalyst", "long", "medium", "pending"),
        )
        conn.commit()

        has_alert, body = _check_high_conviction_signals(conn)
        conn.close()
        assert has_alert is False

    def test_truncates_at_10(self, db_path):
        from analysis.alert_engine import _check_high_conviction_signals
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM trading_signals")
        now = date.today().isoformat()
        for i in range(15):
            conn.execute(
                """INSERT INTO trading_signals
                   (signal_date, ticker, signal_type, direction, conviction, status, rationale, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (now, f"TK{i}", "regulatory_event", "long", "high", "pending", f"Reason {i}"),
            )
        conn.commit()

        has_alert, body = _check_high_conviction_signals(conn)
        conn.close()
        assert has_alert is True
        assert "5 more signals" in body


class TestCheckPipelineDeadlines:
    """Test _check_pipeline_deadlines() detection."""

    def test_approaching_deadline_detected(self, db_path):
        from analysis.alert_engine import _check_pipeline_deadlines
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM pipeline_rules")
        deadline = (date.today() + timedelta(days=3)).isoformat()
        conn.execute(
            """INSERT INTO pipeline_rules
               (proposed_event_id, agency, proposed_title, comment_deadline, tickers, impact_score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (1, "EPA", "Clean Air Standards Update", deadline, "XOM", 4, "proposed"),
        )
        conn.commit()

        has_alert, body = _check_pipeline_deadlines(conn)
        conn.close()
        assert has_alert is True
        assert "Clean Air Standards Update" in body
        assert "EPA" in body

    def test_no_approaching_deadline(self, db_path):
        from analysis.alert_engine import _check_pipeline_deadlines
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM pipeline_rules")
        # Deadline far in the future
        deadline = (date.today() + timedelta(days=30)).isoformat()
        conn.execute(
            """INSERT INTO pipeline_rules
               (proposed_event_id, agency, proposed_title, comment_deadline, tickers, impact_score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (1, "EPA", "Future rule", deadline, "XOM", 4, "proposed"),
        )
        conn.commit()

        has_alert, body = _check_pipeline_deadlines(conn)
        conn.close()
        assert has_alert is False

    def test_low_impact_ignored(self, db_path):
        from analysis.alert_engine import _check_pipeline_deadlines
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM pipeline_rules")
        deadline = (date.today() + timedelta(days=3)).isoformat()
        conn.execute(
            """INSERT INTO pipeline_rules
               (proposed_event_id, agency, proposed_title, comment_deadline, tickers, impact_score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (1, "EPA", "Low impact rule", deadline, "XOM", 2, "proposed"),
        )
        conn.commit()

        has_alert, body = _check_pipeline_deadlines(conn)
        conn.close()
        assert has_alert is False

    def test_formats_null_tickers_as_na(self, db_path):
        from analysis.alert_engine import _check_pipeline_deadlines
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM pipeline_rules")
        deadline = (date.today() + timedelta(days=3)).isoformat()
        conn.execute(
            """INSERT INTO pipeline_rules
               (proposed_event_id, agency, proposed_title, comment_deadline, tickers, impact_score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (1, "EPA", "No tickers rule", deadline, None, 4, "proposed"),
        )
        conn.commit()

        has_alert, body = _check_pipeline_deadlines(conn)
        conn.close()
        assert has_alert is True
        assert "N/A" in body


class TestCheckDataStaleness:
    """Test _check_data_staleness() detection."""

    def test_all_fresh_returns_empty(self, db_path):
        from analysis.alert_engine import _check_data_staleness
        conn = sqlite3.connect(db_path)
        # Seed fresh data in all checked tables
        today = date.today().isoformat()
        conn.execute("DELETE FROM macro_indicators")
        conn.execute(
            "INSERT INTO macro_indicators (series_id, date, value) VALUES (?, ?, ?)",
            ("GDP", today, 1.0),
        )
        conn.execute("DELETE FROM trading_signals")
        conn.execute(
            """INSERT INTO trading_signals (signal_date, ticker, signal_type, direction, conviction, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (today, "SPY", "test", "long", "medium", "pending"),
        )
        # regulatory_events and market_data already have data but may be old
        conn.execute("DELETE FROM regulatory_events")
        conn.execute(
            """INSERT INTO regulatory_events (source, source_id, event_type, title, publication_date)
               VALUES (?, ?, ?, ?, ?)""",
            ("test", "stale-test-1", "final_rule", "Fresh event", today),
        )
        conn.execute("DELETE FROM market_data")
        conn.execute(
            "INSERT INTO market_data (ticker, date, close) VALUES (?, ?, ?)",
            ("SPY", today, 450.0),
        )
        conn.commit()

        has_alert, body = _check_data_staleness(conn)
        conn.close()
        assert has_alert is False

    def test_stale_table_detected(self, db_path):
        from analysis.alert_engine import _check_data_staleness
        conn = sqlite3.connect(db_path)
        # Make market_data very old
        conn.execute("DELETE FROM market_data")
        old_date = (date.today() - timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO market_data (ticker, date, close) VALUES (?, ?, ?)",
            ("SPY", old_date, 450.0),
        )
        conn.commit()

        has_alert, body = _check_data_staleness(conn)
        conn.close()
        assert has_alert is True
        assert "market_data" in body
        assert "days old" in body

    def test_empty_table_no_error(self, db_path):
        from analysis.alert_engine import _check_data_staleness
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_indicators")
        conn.commit()

        # Should not raise — handles empty tables gracefully
        has_alert, body = _check_data_staleness(conn)
        conn.close()
        assert isinstance(has_alert, bool)


class TestCheckLobbyingSpikes:
    """Test _check_lobbying_spikes() detection."""

    @patch("collectors.lobbying.calculate_qoq_changes")
    def test_spikes_detected(self, mock_qoq):
        from analysis.alert_engine import _check_lobbying_spikes
        mock_qoq.return_value = [
            {
                "ticker": "LMT",
                "client_name": "Lockheed Martin",
                "current_amount": 5_000_000,
                "prior_amount": 3_000_000,
                "pct_change": 0.67,
                "filing_year": 2025,
                "filing_period": "Q4",
                "spike": True,
            },
        ]
        # conn is passed but calculate_qoq_changes is mocked
        import sqlite3 as _sq
        conn = _sq.connect(":memory:")
        has_alert, body = _check_lobbying_spikes(conn)
        conn.close()
        assert has_alert is True
        assert "LMT" in body
        assert "Lockheed Martin" in body
        assert "$5,000,000" in body

    @patch("collectors.lobbying.calculate_qoq_changes")
    def test_no_spikes_returns_empty(self, mock_qoq):
        from analysis.alert_engine import _check_lobbying_spikes
        mock_qoq.return_value = [
            {
                "ticker": "LMT",
                "client_name": "Lockheed Martin",
                "current_amount": 3_100_000,
                "prior_amount": 3_000_000,
                "pct_change": 0.03,
                "filing_year": 2025,
                "filing_period": "Q4",
                "spike": False,
            },
        ]
        import sqlite3 as _sq
        conn = _sq.connect(":memory:")
        has_alert, body = _check_lobbying_spikes(conn)
        conn.close()
        assert has_alert is False
        assert body == ""
