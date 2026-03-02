"""Tests for analysis/confluence.py — multi-source convergence scoring engine."""

import sqlite3
from datetime import date, timedelta


from analysis.confluence import compute_confluence


def _today_offset(days: int) -> str:
    """Return ISO date string offset from today."""
    return (date.today() + timedelta(days=days)).isoformat()


def _find_factor(factors: list[dict], source: str) -> dict | None:
    """Find a factor by source name in the factors list."""
    for f in factors:
        if f["source"] == source:
            return f
    return None


def _seed_watchlist(conn, ticker, sector):
    """Insert a watchlist entry (ignores if exists)."""
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (ticker, company_name, sector) VALUES (?, ?, ?)",
        (ticker, f"{ticker} Corp", sector),
    )
    conn.commit()


def _seed_regime(conn, quadrant, label, days_ago=0):
    """Insert a macro regime row."""
    conn.execute(
        """INSERT OR REPLACE INTO macro_regimes
           (date, quadrant, quadrant_label, position_size_modifier, confidence)
           VALUES (?, ?, ?, ?, ?)""",
        (_today_offset(-days_ago), quadrant, label, 1.0, "high"),
    )
    conn.commit()


class TestComputeConfluence:
    """Integration tests for compute_confluence."""

    def test_empty_db_returns_neutral(self, db_path):
        conn = sqlite3.connect(db_path)
        # Clear everything except schema; add a bare watchlist entry
        conn.execute("DELETE FROM macro_regimes")
        conn.execute("DELETE FROM trading_signals")
        conn.execute("DELETE FROM regulatory_events")
        conn.commit()
        _seed_watchlist(conn, "TEST", "Technology")

        result = compute_confluence("TEST", conn)
        conn.close()

        assert result["ticker"] == "TEST"
        assert result["direction"] == "neutral"
        assert result["score"] == 0
        assert len(result["factors"]) == 7
        assert all(not f["contributing"] for f in result["factors"])

    def test_macro_regime_favors_sector(self, db_path):
        """Goldilocks (Q1) favors XLK → Technology sector should be contributing/long."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_regimes")
        _seed_watchlist(conn, "GOOGL", "Technology")
        _seed_regime(conn, quadrant=1, label="Goldilocks")

        result = compute_confluence("GOOGL", conn)
        conn.close()

        macro = _find_factor(result["factors"], "Macro Regime")
        assert macro is not None
        assert macro["contributing"] is True
        assert macro["direction"] == "long"

    def test_macro_regime_avoids_sector(self, db_path):
        """Stagflation (Q3) avoids XLK → Technology sector should be contributing/short."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM macro_regimes")
        _seed_watchlist(conn, "GOOGL", "Technology")
        _seed_regime(conn, quadrant=3, label="Stagflation")

        result = compute_confluence("GOOGL", conn)
        conn.close()

        macro = _find_factor(result["factors"], "Macro Regime")
        assert macro is not None
        assert macro["contributing"] is True
        assert macro["direction"] == "short"

    def test_active_signal_contributes(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_watchlist(conn, "LMT", "Defense")
        conn.execute("DELETE FROM trading_signals")
        conn.execute(
            """INSERT INTO trading_signals
               (ticker, signal_type, signal_date, direction, conviction, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("LMT", "regulatory_event", _today_offset(0), "long", "high", "active"),
        )
        conn.commit()

        result = compute_confluence("LMT", conn)
        conn.close()

        sig_factor = _find_factor(result["factors"], "Trading Signal")
        assert sig_factor is not None
        assert sig_factor["contributing"] is True
        assert sig_factor["direction"] == "long"
        # High conviction adds weight 2
        assert result["directional_score"] >= 2

    def test_lobbying_spike_contributes(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_watchlist(conn, "LMT", "Defense")
        # Two quarters: Q1=$1M, Q2=$1.5M → 50% QoQ increase (>15% threshold)
        conn.execute(
            """INSERT INTO lobbying_filings
               (filing_id, registrant_name, client_name, client_ticker,
                amount, filing_year, filing_period)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("lob-1", "LobbyFirm", "Lockheed", "LMT", 1_500_000, 2025, "Q2"),
        )
        conn.execute(
            """INSERT INTO lobbying_filings
               (filing_id, registrant_name, client_name, client_ticker,
                amount, filing_year, filing_period)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("lob-2", "LobbyFirm", "Lockheed", "LMT", 1_000_000, 2025, "Q1"),
        )
        conn.commit()

        result = compute_confluence("LMT", conn)
        conn.close()

        lobby = _find_factor(result["factors"], "Lobbying Spend")
        assert lobby is not None
        assert lobby["contributing"] is True
        assert lobby["direction"] == "long"

    def test_regulatory_event_contributes(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_watchlist(conn, "LMT", "Defense")
        conn.execute("DELETE FROM regulatory_events")
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency,
                publication_date, tickers, impact_score, sectors)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("federal_register", "fr-conf-1", "final_rule",
             "Defense Procurement Modernization", "Department of Defense",
             _today_offset(-2), "LMT,RTX", 5, "Defense"),
        )
        conn.commit()

        result = compute_confluence("LMT", conn)
        conn.close()

        reg = _find_factor(result["factors"], "Regulatory Event")
        assert reg is not None
        assert reg["contributing"] is True

    def test_congress_buys_contribute(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_watchlist(conn, "LMT", "Defense")
        for i in range(2):
            conn.execute(
                """INSERT INTO congress_trades
                   (politician, ticker, trade_type, trade_date)
                   VALUES (?, ?, ?, ?)""",
                (f"Senator {i}", "LMT", "purchase", _today_offset(-10 - i)),
            )
        conn.commit()

        result = compute_confluence("LMT", conn)
        conn.close()

        cong = _find_factor(result["factors"], "Congress Trades")
        assert cong is not None
        assert cong["contributing"] is True
        assert cong["direction"] == "long"

    def test_congress_sells_dominate(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_watchlist(conn, "LMT", "Defense")
        for i in range(3):
            conn.execute(
                """INSERT INTO congress_trades
                   (politician, ticker, trade_type, trade_date)
                   VALUES (?, ?, ?, ?)""",
                (f"Senator {i}", "LMT", "sale", _today_offset(-5 - i)),
            )
        conn.commit()

        result = compute_confluence("LMT", conn)
        conn.close()

        cong = _find_factor(result["factors"], "Congress Trades")
        assert cong is not None
        assert cong["contributing"] is True
        assert cong["direction"] == "short"

    def test_prediction_market_high_prob(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_watchlist(conn, "LMT", "Defense")
        conn.execute(
            """INSERT INTO prediction_markets
               (contract_id, platform, question_text, category, related_ticker, current_price, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("test-defense-1", "polymarket", "Will defense spending increase?", "policy", "LMT", 0.75, 50000),
        )
        conn.commit()

        result = compute_confluence("LMT", conn)
        conn.close()

        pred = _find_factor(result["factors"], "Prediction Market")
        assert pred is not None
        assert pred["contributing"] is True

    def test_fda_catalyst_upcoming(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_watchlist(conn, "PFE", "Healthcare")
        conn.execute(
            """INSERT INTO fda_events
               (ticker, event_type, event_date, drug_name, company_name)
               VALUES (?, ?, ?, ?, ?)""",
            ("PFE", "pdufa_date", _today_offset(15), "TestDrug", "Pfizer"),
        )
        conn.commit()

        result = compute_confluence("PFE", conn)
        conn.close()

        fda = _find_factor(result["factors"], "FDA Catalyst")
        assert fda is not None
        assert fda["contributing"] is True
        assert fda["direction"] == "long"
