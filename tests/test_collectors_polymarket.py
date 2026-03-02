"""Tests for collectors.polymarket module."""

import sqlite3
from unittest.mock import patch

import pytest

from collectors.polymarket import (
    MIN_VOLUME,
    _categorize_market,
    _find_related_ticker,
    collect,
    get_fomc_probabilities,
)

# ── _categorize_market ───────────────────────────────────────────


class TestCategorizeMarket:
    """Tests for the _categorize_market function."""

    def test_fed_keyword_maps_to_fomc(self):
        assert _categorize_market("Will the Fed raise interest rates?") == "fomc"

    def test_fomc_keyword(self):
        assert _categorize_market("FOMC meeting outcome December 2026") == "fomc"

    def test_rate_cut_keyword(self):
        assert _categorize_market("Will there be a rate cut in March?") == "fomc"

    def test_tariff_keyword(self):
        assert _categorize_market("Will new tariff on China be imposed?") == "tariff"

    def test_trade_war_keyword(self):
        assert _categorize_market("US-China trade war escalation?") == "tariff"

    def test_fda_keyword(self):
        assert _categorize_market("FDA drug approval for XYZ drug?") == "fda"

    def test_shutdown_maps_to_fiscal(self):
        assert _categorize_market("Government shutdown before March?") == "fiscal"

    def test_antitrust_keyword(self):
        assert _categorize_market("FTC blocks merger of Big Corp?") == "antitrust"

    def test_election_keyword(self):
        assert _categorize_market("Who will win the presidential election?") == "election"

    def test_irrelevant_returns_none(self):
        assert _categorize_market("Will it snow in Miami this year?") is None

    def test_empty_string(self):
        assert _categorize_market("") is None

    def test_case_insensitive(self):
        assert _categorize_market("THE FED WILL CUT RATES") == "fomc"


# ── _find_related_ticker ────────────────────────────────────────


class TestFindRelatedTicker:
    """Tests for the _find_related_ticker function."""

    def test_fed_maps_to_spy(self):
        assert _find_related_ticker("Fed interest rate decision") == "SPY"

    def test_interest_rate_maps_to_tlt(self):
        # "interest rate" comes after "fed" in QUESTION_TICKER_MAP iteration,
        # but "fed" also appears here, so it matches "fed" first -> SPY
        result = _find_related_ticker("Interest rate forecast for bonds")
        assert result == "TLT"

    def test_tariff_maps_to_spy(self):
        assert _find_related_ticker("New tariff on imports") == "SPY"

    def test_fda_maps_to_xbi(self):
        assert _find_related_ticker("FDA approves new cancer drug") == "XBI"

    def test_shutdown_maps_to_spy(self):
        assert _find_related_ticker("Government shutdown probability") == "SPY"

    def test_recession_maps_to_spy(self):
        assert _find_related_ticker("US recession probability 2026") == "SPY"

    def test_unmatched_returns_none(self):
        assert _find_related_ticker("Random question about weather") is None

    def test_empty_string(self):
        assert _find_related_ticker("") is None


# ── collect ──────────────────────────────────────────────────────


class TestCollect:
    """Tests for the collect function."""

    def _make_market(self, question, volume, condition_id="cond-1", prices="[0.65, 0.35]"):
        return {
            "question": question,
            "volumeNum": volume,
            "outcomePrices": prices,
            "conditionId": condition_id,
            "endDateIso": "2026-06-01",
            "id": 12345,
        }

    @patch("collectors.polymarket.DB_PATH")
    @patch("collectors.polymarket._fetch_markets")
    def test_collect_upserts_relevant_markets(self, mock_fetch, mock_db_path, db_path):

        mock_db_path.__str__ = lambda s: db_path
        # Patch DB_PATH to our temp db
        with patch("collectors.polymarket.DB_PATH", db_path):
            mock_fetch.return_value = [
                self._make_market("Will the Fed raise rates?", 50000, "cond-fed-1"),
                self._make_market("New tariff on steel imports?", 25000, "cond-tariff-1"),
            ]
            count = collect(max_pages=1)

        assert count == 2
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT contract_id, category, current_price FROM prediction_markets").fetchall()
        conn.close()
        assert len(rows) == 2

        # Check data
        by_id = {r[0]: r for r in rows}
        assert by_id["cond-fed-1"][1] == "fomc"
        assert by_id["cond-tariff-1"][1] == "tariff"
        assert by_id["cond-fed-1"][2] == pytest.approx(0.65)

    @patch("collectors.polymarket._fetch_markets")
    def test_collect_skips_below_min_volume(self, mock_fetch, db_path):

        with patch("collectors.polymarket.DB_PATH", db_path):
            mock_fetch.return_value = [
                self._make_market("Fed rate decision?", MIN_VOLUME - 1, "cond-low-vol"),
            ]
            count = collect(max_pages=1)

        assert count == 0
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT COUNT(*) FROM prediction_markets").fetchone()
        conn.close()
        assert rows[0] == 0

    @patch("collectors.polymarket._fetch_markets")
    def test_collect_skips_irrelevant_category(self, mock_fetch, db_path):

        with patch("collectors.polymarket.DB_PATH", db_path):
            mock_fetch.return_value = [
                self._make_market("Will it rain tomorrow?", 100000, "cond-weather"),
            ]
            count = collect(max_pages=1)

        assert count == 0

    @patch("collectors.polymarket._fetch_markets")
    def test_collect_upsert_updates_existing(self, mock_fetch, db_path):

        with patch("collectors.polymarket.DB_PATH", db_path):
            # First run
            mock_fetch.return_value = [
                self._make_market("Fed rate cut?", 50000, "cond-upsert-1", "[0.60, 0.40]"),
            ]
            collect(max_pages=1)

            # Second run with updated price
            mock_fetch.return_value = [
                self._make_market("Fed rate cut?", 55000, "cond-upsert-1", "[0.75, 0.25]"),
            ]
            collect(max_pages=1)

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT current_price, volume FROM prediction_markets WHERE contract_id = 'cond-upsert-1'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(0.75)
        assert rows[0][1] == 55000


# ── get_fomc_probabilities ───────────────────────────────────────


class TestGetFomcProbabilities:
    """Tests for the get_fomc_probabilities function."""

    def test_returns_fomc_prob_dict(self, db_path):

        conn = sqlite3.connect(db_path)
        # Seed FOMC prediction market rows
        fomc_rows = [
            ("fomc-1", "polymarket", "Will the Fed interest rate remain no change?", 0.55, 80000, "fomc", "SPY"),
            ("fomc-2", "polymarket", "Will the Fed interest rate decrease by 25 bps?", 0.30, 60000, "fomc", "TLT"),
            ("fomc-3", "polymarket", "Will the Fed interest rate increase by 25 bps?", 0.10, 40000, "fomc", "TLT"),
        ]
        for row in fomc_rows:
            conn.execute(
                """INSERT INTO prediction_markets
                   (contract_id, platform, question_text, current_price, volume, category, related_ticker)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
        conn.commit()

        probs = get_fomc_probabilities(conn)
        conn.close()

        assert "no_change" in probs
        assert probs["no_change"] == pytest.approx(0.55)
        assert "cut_25" in probs
        assert probs["cut_25"] == pytest.approx(0.30)
        assert "hike_25" in probs
        assert probs["hike_25"] == pytest.approx(0.10)

    def test_empty_table_returns_empty_dict(self, db_path):

        conn = sqlite3.connect(db_path)
        probs = get_fomc_probabilities(conn)
        conn.close()
        assert probs == {}

    def test_non_fomc_rows_excluded(self, db_path):

        conn = sqlite3.connect(db_path)
        # Insert a non-FOMC row that mentions "Fed" but is categorized as "tariff"
        conn.execute(
            """INSERT INTO prediction_markets
               (contract_id, platform, question_text, current_price, volume, category, related_ticker)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("tariff-1", "polymarket", "Fed tariff response?", 0.50, 50000, "tariff", "SPY"),
        )
        conn.commit()
        probs = get_fomc_probabilities(conn)
        conn.close()
        assert probs == {}
