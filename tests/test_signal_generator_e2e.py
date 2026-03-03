"""End-to-end integration tests for analysis/signal_generator.py.

Validates the full pipeline: seeded DB → generate_signals() → signals in trading_signals.
"""

import sqlite3
from datetime import date, timedelta
from unittest.mock import patch


def _seed_e2e_data(db_path):
    """Seed the test DB with data that should trigger multiple signal types."""
    conn = sqlite3.connect(db_path)
    today = date.today()

    # Clear any signals from prior tests
    conn.execute("DELETE FROM trading_signals")

    # 1. FDA event: pending PDUFA date within 30 days for PFE
    conn.execute("DELETE FROM fda_events")
    pdufa_date = (today + timedelta(days=15)).isoformat()
    conn.execute(
        """INSERT INTO fda_events (event_date, event_type, ticker, drug_name, company_name, outcome)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (pdufa_date, "pdufa_date", "PFE", "TestDrug", "Pfizer", "pending"),
    )

    # 2. Contract award: large defense award for LMT within 7 days
    conn.execute("DELETE FROM contract_awards")
    award_date = (today - timedelta(days=2)).isoformat()
    conn.execute(
        """INSERT INTO contract_awards (award_id, recipient_name, recipient_ticker, awarding_agency,
           award_amount, award_date, description)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("award-e2e-1", "Lockheed Martin", "LMT", "Department of Defense",
         200_000_000, award_date, "F-35 support contract"),
    )

    # 3. Regulatory event: high-impact EO with tickers within 3 days
    conn.execute(
        """INSERT INTO regulatory_events (source, source_id, event_type, title, agency,
           publication_date, tickers, impact_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("federal_register", "fr-e2e-eo-1", "executive_order",
         "Executive Order on Defense Procurement Reform",
         "Executive Office of the President",
         (today - timedelta(days=1)).isoformat(), "LMT,RTX", 5),
    )

    # 4. Macro regimes: transition from Q1 to Q3
    conn.execute("DELETE FROM macro_regimes")
    conn.execute(
        """INSERT INTO macro_regimes (date, quadrant, quadrant_label, position_size_modifier, confidence)
           VALUES (?, ?, ?, ?, ?)""",
        ((today - timedelta(days=2)).isoformat(), 1, "Goldilocks", 1.2, "high"),
    )
    conn.execute(
        """INSERT INTO macro_regimes (date, quadrant, quadrant_label, position_size_modifier, confidence)
           VALUES (?, ?, ?, ?, ?)""",
        ((today - timedelta(days=1)).isoformat(), 3, "Stagflation", 0.7, "high"),
    )

    # 5. FOMC event: upcoming meeting 5 days from now (within 3-8 day window)
    conn.execute("DELETE FROM fomc_events")
    fomc_date = (today + timedelta(days=5)).isoformat()
    conn.execute(
        """INSERT INTO fomc_events (event_date, event_type, title)
           VALUES (?, ?, ?)""",
        (fomc_date, "meeting", f"FOMC Meeting — {fomc_date}"),
    )

    # 6. Market data: ensure SPY, LMT, PFE have recent prices
    conn.execute("DELETE FROM market_data")
    for ticker, base in [("SPY", 450.0), ("LMT", 480.0), ("PFE", 28.0)]:
        for i in range(10):
            d = (today - timedelta(days=9 - i))
            if d.weekday() >= 5:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO market_data (ticker, date, close) VALUES (?, ?, ?)",
                (ticker, d.isoformat(), base + i * 0.5),
            )

    conn.commit()
    conn.close()


def _mock_eo_classifier(title):
    """Stub EO classifier that returns a tradeable classification."""
    return {
        "is_tradeable": True,
        "topic": "defense_spending",
        "tickers": ["LMT", "RTX"],
        "direction": "long",
        "expected_car": 0.012,
        "confidence": "high",
        "sample_size": 15,
    }


def _mock_detect_shocks(lookback_weeks=1, conn=None):
    """Stub reg_shock_detector that returns one shock."""
    return [{
        "agency": "Defense Department, Defense Acquisition Regulations System",
        "week_start": date.today().isoformat(),
        "count": 8,
        "z_score": 2.5,
        "tickers": ["LMT", "GD"],
        "direction": "long",
        "expected_car": 0.015,
        "confidence": "high",
        "hold_days": 5,
    }]


class TestSignalGeneratorE2E:
    """End-to-end tests for generate_signals()."""

    def _run_generate(self, db_path):
        """Run generate_signals with all external dependencies mocked."""
        from analysis.signal_generator import generate_signals

        with (
            patch("analysis.signal_generator.DB_PATH", db_path),
            patch("analysis.eo_classifier.classify_eo", side_effect=_mock_eo_classifier),
            patch("analysis.reg_shock_detector.detect_shocks", side_effect=_mock_detect_shocks),
            patch("collectors.lobbying.calculate_qoq_changes", return_value=[]),
        ):
            return generate_signals()

    def test_generate_signals_produces_output(self, db_path):
        """Seeded data should trigger at least one signal."""
        _seed_e2e_data(db_path)
        signals = self._run_generate(db_path)
        assert len(signals) >= 1

        # Verify they were written to DB
        conn = sqlite3.connect(db_path)
        db_count = conn.execute("SELECT COUNT(*) FROM trading_signals").fetchone()[0]
        conn.close()
        assert db_count == len(signals)

    def test_signals_have_required_fields(self, db_path):
        """Each signal must have core fields."""
        _seed_e2e_data(db_path)
        signals = self._run_generate(db_path)
        assert len(signals) >= 1

        required = {"ticker", "signal_type", "direction", "conviction"}
        for sig in signals:
            for field in required:
                assert field in sig, f"Missing field '{field}' in signal: {sig}"
                assert sig[field] is not None, f"Null field '{field}' in signal: {sig}"

    def test_macro_modifier_applied(self, db_path):
        """Signals should reflect the macro regime modifier (Q3 → 0.7)."""
        _seed_e2e_data(db_path)
        signals = self._run_generate(db_path)
        assert len(signals) >= 1

        # The most recent macro regime is Q3 with modifier 0.7
        for sig in signals:
            assert sig.get("position_size_modifier") is not None
            # Should be 0.7 (from Stagflation regime)
            assert sig["position_size_modifier"] == 0.7

    def test_duplicate_signals_not_created(self, db_path):
        """Running generate_signals twice should not double-insert."""
        _seed_e2e_data(db_path)

        first_run = self._run_generate(db_path)
        first_count = len(first_run)
        assert first_count >= 1

        second_run = self._run_generate(db_path)
        # All signals from first run should be deduplicated
        assert len(second_run) == 0
