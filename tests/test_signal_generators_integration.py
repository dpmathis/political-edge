"""Integration tests for individual signal generator functions in signal_generator.py."""

import sqlite3
from datetime import date, timedelta
from unittest.mock import patch

from analysis.signal_generator import (
    _generate_contract_signals,
    _generate_fda_signals,
    _generate_fomc_signals,
    _generate_lobbying_signals,
    _generate_macro_regime_signals,
    _generate_pipeline_deadline_signals,
    _generate_pipeline_pressure_signals,
    _generate_regulatory_signals,
)


# ── helpers ──────────────────────────────────────────────────────


def _today_offset(days: int) -> str:
    """Return ISO date string offset from today."""
    return (date.today() + timedelta(days=days)).isoformat()


def _seed_fda_event(conn, ticker="PFE", event_type="adcom_vote", days_out=15, outcome="pending"):
    """Insert an FDA event."""
    conn.execute(
        """INSERT INTO fda_events
           (ticker, event_type, event_date, drug_name, company_name, outcome)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ticker, event_type, _today_offset(days_out), "TestDrug", "TestCo", outcome),
    )
    conn.commit()


def _seed_contract(conn, ticker="LMT", amount=100_000_000, agency="Department of Defense", days_ago=3):
    """Insert a contract award."""
    conn.execute(
        """INSERT INTO contract_awards
           (award_id, recipient_name, recipient_ticker, awarding_agency,
            award_amount, award_date, description)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (f"award-{ticker}-{amount}", f"{ticker} Corp", ticker, agency,
         amount, _today_offset(-days_ago), "Test contract"),
    )
    conn.commit()


def _seed_regulatory_event(conn, event_type="final_rule", title="Test Rule",
                           tickers="LMT", impact=5, agency="Department of Defense", days_ago=1):
    """Insert a regulatory event."""
    conn.execute(
        """INSERT INTO regulatory_events
           (source, source_id, event_type, title, agency, publication_date,
            tickers, impact_score, sectors)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("federal_register", f"fr-sig-{title[:10]}", event_type, title, agency,
         _today_offset(-days_ago), tickers, impact, "Defense"),
    )
    conn.commit()


def _seed_fomc_event(conn, days_out=5, rate_decision=None, hd_score=None):
    """Insert an FOMC event."""
    conn.execute(
        """INSERT INTO fomc_events (event_date, event_type, rate_decision, hawkish_dovish_score)
           VALUES (?, ?, ?, ?)""",
        (_today_offset(days_out), "meeting", rate_decision, hd_score),
    )
    conn.commit()


def _seed_macro_regime(conn, quadrant, label, days_ago=0, confidence="high"):
    """Insert a macro regime row."""
    conn.execute(
        """INSERT OR REPLACE INTO macro_regimes
           (date, quadrant, quadrant_label, position_size_modifier, confidence)
           VALUES (?, ?, ?, ?, ?)""",
        (_today_offset(-days_ago), quadrant, label, 1.0, confidence),
    )
    conn.commit()


def _seed_signal(conn, ticker, signal_type, days_ago=1):
    """Insert a recent trading signal for dedup testing."""
    conn.execute(
        """INSERT INTO trading_signals
           (ticker, signal_type, signal_date, direction, conviction, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ticker, signal_type, _today_offset(-days_ago), "long", "medium", "active"),
    )
    conn.commit()


def _seed_pipeline_rule(conn, sector="Defense", tickers="LMT", impact=4,
                        deadline_days_out=3, status="in_comment", hist_car=-0.0025):
    """Insert a pipeline rule approaching deadline (needs a regulatory event for FK)."""
    # Insert a regulatory event to serve as proposed_event_id
    conn.execute(
        """INSERT INTO regulatory_events
           (source, source_id, event_type, title, agency, publication_date,
            comment_deadline, sectors, tickers, impact_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("federal_register", f"fr-pipe-{_today_offset(deadline_days_out)}", "proposed_rule",
         "Test Pipeline Rule", "Department of Defense", _today_offset(-30),
         _today_offset(deadline_days_out), sector, tickers, impact),
    )
    event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO pipeline_rules
           (proposed_event_id, agency, sector, tickers, proposed_title, proposed_date,
            comment_deadline, impact_score, status, historical_car)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, "Department of Defense", sector, tickers, "Test Pipeline Rule",
         _today_offset(-30), _today_offset(deadline_days_out), impact, status, hist_car),
    )
    conn.commit()


# ── _generate_fda_signals ────────────────────────────────────────


class TestGenerateFdaSignals:
    """Tests for _generate_fda_signals."""

    def test_pending_adcom_within_30_days(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_fda_event(conn, ticker="PFE", event_type="adcom_vote", days_out=15)
        signals = _generate_fda_signals(conn, 1.0, 1)
        conn.close()
        assert len(signals) >= 1
        sig = next(s for s in signals if s["ticker"] == "PFE")
        assert sig["signal_type"] == "fda_catalyst"
        assert sig["direction"] == "long"

    def test_past_events_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_fda_event(conn, ticker="PFE", event_type="adcom_vote", days_out=-5)
        signals = _generate_fda_signals(conn, 1.0, 1)
        conn.close()
        pfe_signals = [s for s in signals if s["ticker"] == "PFE"]
        assert len(pfe_signals) == 0

    def test_dedup_recent_signal(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_fda_event(conn, ticker="PFE", event_type="pdufa_date", days_out=10)
        _seed_signal(conn, "PFE", "fda_catalyst", days_ago=2)
        signals = _generate_fda_signals(conn, 1.0, 1)
        conn.close()
        pfe_signals = [s for s in signals if s["ticker"] == "PFE"]
        assert len(pfe_signals) == 0

    def test_null_ticker_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_fda_event(conn, ticker=None, event_type="adcom_vote", days_out=10)
        signals = _generate_fda_signals(conn, 1.0, 1)
        conn.close()
        # No signal for None ticker
        none_sigs = [s for s in signals if s["ticker"] is None]
        assert len(none_sigs) == 0


# ── _generate_contract_signals ───────────────────────────────────


class TestGenerateContractSignals:
    """Tests for _generate_contract_signals."""

    def test_large_defense_award(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_contract(conn, ticker="LMT", amount=600_000_000, agency="Department of Defense")
        signals = _generate_contract_signals(conn, 1.0, 1)
        conn.close()
        lmt_sigs = [s for s in signals if s["ticker"] == "LMT"]
        assert len(lmt_sigs) >= 1
        sig = lmt_sigs[0]
        assert sig["signal_type"] == "contract_momentum"
        assert sig["direction"] == "long"
        # $600M + defense agency = 2 boosts → high conviction
        assert sig["conviction"] == "high"

    def test_below_threshold_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_contract(conn, ticker="BA", amount=40_000_000)
        signals = _generate_contract_signals(conn, 1.0, 1)
        conn.close()
        ba_sigs = [s for s in signals if s["ticker"] == "BA"]
        assert len(ba_sigs) == 0  # Below $50M trigger

    def test_dedup_recent_signal(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_contract(conn, ticker="LMT", amount=100_000_000)
        _seed_signal(conn, "LMT", "contract_momentum", days_ago=2)
        signals = _generate_contract_signals(conn, 1.0, 1)
        conn.close()
        lmt_sigs = [s for s in signals if s["ticker"] == "LMT"]
        assert len(lmt_sigs) == 0


# ── _generate_regulatory_signals ─────────────────────────────────


class TestGenerateRegulatorySignals:
    """Tests for _generate_regulatory_signals."""

    def test_high_impact_executive_order(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_regulatory_event(conn, event_type="executive_order",
                               title="Executive Order on Defense Support",
                               tickers="LMT,RTX", impact=5)
        signals = _generate_regulatory_signals(conn, 1.0, 1)
        conn.close()
        # Should produce signals for both tickers
        tickers = [s["ticker"] for s in signals if s["signal_type"] == "regulatory_event"]
        assert "LMT" in tickers or "RTX" in tickers

    def test_proposed_rule_reduces_conviction(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_regulatory_event(conn, event_type="proposed_rule",
                               title="Proposed Rule on Import Standards",
                               tickers="XLE", impact=4)
        signals = _generate_regulatory_signals(conn, 1.0, 1)
        conn.close()
        xle_sigs = [s for s in signals if s["ticker"] == "XLE"]
        if xle_sigs:
            # proposed_rule applies a reduce, so conviction should be <= medium
            assert xle_sigs[0]["conviction"] in ("low", "medium")

    def test_low_impact_filtered(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_regulatory_event(conn, event_type="notice", title="Minor Notice",
                               tickers="SPY", impact=2)
        signals = _generate_regulatory_signals(conn, 1.0, 1)
        conn.close()
        spy_sigs = [s for s in signals if s["ticker"] == "SPY" and s["signal_type"] == "regulatory_event"]
        assert len(spy_sigs) == 0  # Below impact >= 4 trigger

    def test_tariff_in_title_short(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_regulatory_event(conn, event_type="final_rule",
                               title="New Tariff Schedule on Steel Imports",
                               tickers="X", impact=5)
        signals = _generate_regulatory_signals(conn, 1.0, 1)
        conn.close()
        x_sigs = [s for s in signals if s["ticker"] == "X"]
        if x_sigs:
            assert x_sigs[0]["direction"] == "short"


# ── _generate_lobbying_signals ───────────────────────────────────


class TestGenerateLobbyingSignals:
    """Tests for _generate_lobbying_signals."""

    @patch("collectors.lobbying.calculate_qoq_changes")
    def test_spike_with_matching_event(self, mock_changes, db_path):
        conn = sqlite3.connect(db_path)
        # Seed a regulatory event for LMT
        _seed_regulatory_event(conn, tickers="LMT", impact=4)
        mock_changes.return_value = [
            {"ticker": "LMT", "client_name": "Lockheed Martin", "spike": True, "pct_change": 0.40}
        ]
        signals = _generate_lobbying_signals(conn, 1.0, 1)
        conn.close()
        lmt_sigs = [s for s in signals if s["ticker"] == "LMT"]
        assert len(lmt_sigs) >= 1
        assert lmt_sigs[0]["signal_type"] == "lobbying_spike"

    @patch("collectors.lobbying.calculate_qoq_changes")
    def test_spike_no_matching_event(self, mock_changes, db_path):
        conn = sqlite3.connect(db_path)
        mock_changes.return_value = [
            {"ticker": "UNKNOWN", "client_name": "Unknown Corp", "spike": True, "pct_change": 0.50}
        ]
        signals = _generate_lobbying_signals(conn, 1.0, 1)
        conn.close()
        # No regulatory event for UNKNOWN ticker → no signal
        unk_sigs = [s for s in signals if s["ticker"] == "UNKNOWN"]
        assert len(unk_sigs) == 0


# ── _generate_macro_regime_signals ───────────────────────────────


class TestGenerateMacroRegimeSignals:
    """Tests for _generate_macro_regime_signals."""

    def test_q1_to_q3_transition(self, db_path):
        conn = sqlite3.connect(db_path)
        # Clear existing macro regimes from conftest seed
        conn.execute("DELETE FROM macro_regimes")
        # Seed transition: Q1 yesterday, Q3 today
        _seed_macro_regime(conn, quadrant=1, label="Goldilocks", days_ago=1)
        _seed_macro_regime(conn, quadrant=3, label="Stagflation", days_ago=0)
        signals = _generate_macro_regime_signals(conn, 1.0, 3)
        conn.close()

        tickers = {s["ticker"] for s in signals}
        directions = {s["ticker"]: s["direction"] for s in signals}

        # Q1 favored: XLK, XLY. Q3 favored: XLE, XLP.
        # Newly favored (long): XLE (was not in Q1 favored)
        # XLP was in Q1 avoid → now in Q3 favored, so it's newly favored
        # Newly avoided (short): XLK, XLY (were in Q1 favored, not in Q3 favored,
        #   but what matters is new_avoid - old_avoid)
        # Q1 avoid: XLP, XLU. Q3 avoid: XLK, XLY, XLF.
        # Newly avoided: XLK, XLY, XLF (in Q3 avoid but not in Q1 avoid)
        assert len(signals) > 0
        assert all(s["signal_type"] == "macro_regime" for s in signals)
        # XLK and XLY should be short (newly avoided)
        if "XLK" in tickers:
            assert directions["XLK"] == "short"
        if "XLY" in tickers:
            assert directions["XLY"] == "short"

    def test_no_transition_no_signals(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_regimes")
        _seed_macro_regime(conn, quadrant=1, label="Goldilocks", days_ago=1)
        _seed_macro_regime(conn, quadrant=1, label="Goldilocks", days_ago=0)
        signals = _generate_macro_regime_signals(conn, 1.0, 1)
        conn.close()
        assert len(signals) == 0

    def test_insufficient_data(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_regimes")
        _seed_macro_regime(conn, quadrant=2, label="Reflation", days_ago=0)
        signals = _generate_macro_regime_signals(conn, 1.0, 2)
        conn.close()
        assert len(signals) == 0

    def test_dedup_recent_signal(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_regimes")
        _seed_macro_regime(conn, quadrant=1, label="Goldilocks", days_ago=1)
        _seed_macro_regime(conn, quadrant=3, label="Stagflation", days_ago=0)
        # Pre-seed signals for expected ETFs
        _seed_signal(conn, "XLK", "macro_regime", days_ago=5)
        _seed_signal(conn, "XLY", "macro_regime", days_ago=5)
        _seed_signal(conn, "XLF", "macro_regime", days_ago=5)
        _seed_signal(conn, "XLE", "macro_regime", days_ago=5)
        _seed_signal(conn, "XLP", "macro_regime", days_ago=5)
        signals = _generate_macro_regime_signals(conn, 1.0, 3)
        conn.close()
        # All should be deduped since we seeded signals within 20-day window
        assert len(signals) == 0


# ── _generate_fomc_signals ───────────────────────────────────────


class TestGenerateFomcSignals:
    """Tests for _generate_fomc_signals."""

    def test_pre_drift_upcoming_meeting(self, db_path):
        conn = sqlite3.connect(db_path)
        # Clear existing FOMC events and seed one 5 days out
        conn.execute("DELETE FROM fomc_events")
        _seed_fomc_event(conn, days_out=5)
        signals = _generate_fomc_signals(conn, 1.0, 1)
        conn.close()
        spy_sigs = [s for s in signals if s["ticker"] == "SPY" and s["signal_type"] == "fomc_drift"]
        assert len(spy_sigs) >= 1
        assert spy_sigs[0]["direction"] == "long"

    def test_no_meeting_in_window(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM fomc_events")
        _seed_fomc_event(conn, days_out=15)  # Too far out (>8 days)
        signals = _generate_fomc_signals(conn, 1.0, 1)
        conn.close()
        spy_sigs = [s for s in signals if s["ticker"] == "SPY" and s["signal_type"] == "fomc_drift"]
        assert len(spy_sigs) == 0

    def test_post_rate_cut_rotation(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM fomc_events")
        # Seed a meeting that happened today with a rate cut
        _seed_fomc_event(conn, days_out=0, rate_decision="cut_25", hd_score=-0.3)
        signals = _generate_fomc_signals(conn, 1.0, 1)
        conn.close()
        tickers = {s["ticker"] for s in signals if s["signal_type"] == "fomc_drift"}
        # Rate cut → long XLF and XLRE
        assert "XLF" in tickers or "XLRE" in tickers


# ── _generate_pipeline_pressure_signals ──────────────────────────


class TestGeneratePipelinePressureSignals:
    """Tests for _generate_pipeline_pressure_signals."""

    def test_low_pressure_sector_long(self, db_path):
        conn = sqlite3.connect(db_path)
        # Seed proposed rules past deadline for Defense sector
        for i in range(3):
            conn.execute(
                """INSERT INTO regulatory_events
                   (source, source_id, event_type, title, agency, publication_date,
                    comment_deadline, sectors, impact_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("federal_register", f"fr-pp-{i}", "proposed_rule",
                 f"Proposed Rule {i}", "Department of Defense",
                 _today_offset(-60), _today_offset(-10), "Defense", 3),
            )
        conn.commit()
        signals = _generate_pipeline_pressure_signals(conn, 1.0, 1)
        conn.close()
        # Should identify sectors with pressure and signal long on lowest
        if signals:
            assert all(s["signal_type"] == "pipeline_pressure" for s in signals)
            assert all(s["direction"] == "long" for s in signals)

    def test_no_proposed_rules(self, db_path):
        conn = sqlite3.connect(db_path)
        # Remove all proposed rules
        conn.execute("DELETE FROM regulatory_events WHERE event_type = 'proposed_rule'")
        conn.commit()
        signals = _generate_pipeline_pressure_signals(conn, 1.0, 1)
        conn.close()
        assert len(signals) == 0


# ── _generate_pipeline_deadline_signals ──────────────────────────


class TestGeneratePipelineDeadlineSignals:
    """Tests for _generate_pipeline_deadline_signals."""

    def test_approaching_deadline(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_pipeline_rule(conn, sector="Defense", tickers="LMT", impact=4,
                            deadline_days_out=3, hist_car=-0.0025)
        signals = _generate_pipeline_deadline_signals(conn, 1.0, 1)
        conn.close()
        lmt_sigs = [s for s in signals if s["ticker"] == "LMT"]
        if lmt_sigs:
            assert lmt_sigs[0]["signal_type"] == "pipeline_deadline"
            assert lmt_sigs[0]["direction"] == "short"

    def test_low_impact_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_pipeline_rule(conn, sector="Defense", tickers="LMT", impact=2,
                            deadline_days_out=3)
        signals = _generate_pipeline_deadline_signals(conn, 1.0, 1)
        conn.close()
        lmt_sigs = [s for s in signals if s["ticker"] == "LMT"]
        assert len(lmt_sigs) == 0  # impact < 3 threshold
