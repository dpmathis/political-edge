"""Tests for analysis/impact_scorer.py."""

import sqlite3

import pytest

from analysis.impact_scorer import (
    BOOST_KEYWORDS,
    REDUCE_KEYWORDS,
    score_all_unscored,
    score_event,
)


class TestScoreEventBaseScores:
    """Test base scoring by event_type."""

    def test_executive_order_gets_highest_score(self):
        score = score_event("executive_order", "Some Order", "", "")
        assert score == 5

    def test_bill_signed_gets_highest_score(self):
        score = score_event("bill_signed", "A bill", "", "")
        assert score == 5

    def test_final_rule_scores_4(self):
        score = score_event("final_rule", "Rule on Import Standards", "", "")
        assert score == 4

    def test_bill_passed_chamber_scores_4(self):
        score = score_event("bill_passed_chamber", "Bill passed", "", "")
        assert score == 4

    def test_proposed_rule_scores_3(self):
        score = score_event("proposed_rule", "Proposed Rule", "", "")
        assert score == 3

    def test_bill_passed_committee_scores_3(self):
        score = score_event("bill_passed_committee", "Committee", "", "")
        assert score == 3

    def test_hearing_scheduled_scores_2(self):
        score = score_event("hearing_scheduled", "Hearing", "", "")
        assert score == 2

    def test_comment_period_open_scores_2(self):
        score = score_event("comment_period_open", "Comment open", "", "")
        assert score == 2

    def test_comment_period_close_scores_2(self):
        score = score_event("comment_period_close", "Comment close", "", "")
        assert score == 2

    def test_bill_introduced_scores_1(self):
        score = score_event("bill_introduced", "New bill", "", "")
        assert score == 1

    def test_notice_scores_1(self):
        score = score_event("notice", "A notice", "", "")
        assert score == 1

    def test_unknown_type_defaults_to_1(self):
        score = score_event("unknown_type_xyz", "Something", "", "")
        assert score == 1


class TestScoreEventKeywordBoost:
    """Test keyword boost logic."""

    @pytest.mark.parametrize("keyword", BOOST_KEYWORDS)
    def test_boost_keyword_adds_one(self, keyword):
        # Use proposed_rule (base=3) so boost brings it to 4
        score = score_event("proposed_rule", f"Title with {keyword} in it", "", "")
        assert score == 4

    def test_boost_is_case_insensitive(self):
        score = score_event("proposed_rule", "EMERGENCY action required", "", "")
        assert score == 4

    def test_only_one_boost_even_with_multiple_keywords(self):
        title = "Emergency executive order effective immediately"
        # bill_introduced base=1, one boost => 2 (not 4)
        score = score_event("bill_introduced", title, "", "")
        assert score == 2


class TestScoreEventKeywordReduce:
    """Test keyword reduction logic."""

    @pytest.mark.parametrize("keyword", REDUCE_KEYWORDS)
    def test_reduce_keyword_subtracts_one(self, keyword):
        # Use final_rule (base=4) so reduction brings it to 3
        score = score_event("final_rule", f"Title with {keyword} update", "", "")
        assert score == 3

    def test_reduce_is_case_insensitive(self):
        score = score_event("final_rule", "TECHNICAL CORRECTION to form", "", "")
        assert score == 3

    def test_only_one_reduce_even_with_multiple_keywords(self):
        title = "Administrative nomenclature typographical clerical update"
        # final_rule base=4, one reduce => 3
        score = score_event("final_rule", title, "", "")
        assert score == 3


class TestScoreEventMultipleTickers:
    """Test ticker count boost."""

    def test_three_or_more_tickers_boosts_score(self):
        # proposed_rule base=3, ticker boost => 4
        score = score_event("proposed_rule", "Some rule", "AAPL,MSFT,GOOG", "")
        assert score == 4

    def test_two_tickers_no_boost(self):
        score = score_event("proposed_rule", "Some rule", "AAPL,MSFT", "")
        assert score == 3

    def test_empty_tickers_no_boost(self):
        score = score_event("proposed_rule", "Some rule", "", "")
        assert score == 3

    def test_none_tickers_no_boost(self):
        score = score_event("proposed_rule", "Some rule", None, "")
        assert score == 3

    def test_tickers_with_whitespace_parsed_correctly(self):
        score = score_event("proposed_rule", "Some rule", " AAPL , MSFT , GOOG ", "")
        assert score == 4


class TestScoreEventClamping:
    """Test score is clamped between 1 and 5."""

    def test_score_does_not_exceed_5(self):
        # executive_order base=5 + boost keyword + 3 tickers = 7, clamped to 5
        score = score_event(
            "executive_order",
            "Emergency national security order",
            "AAPL,MSFT,GOOG",
            "",
        )
        assert score == 5

    def test_score_does_not_go_below_1(self):
        # notice base=1 + reduce keyword = 0, clamped to 1
        score = score_event("notice", "Technical correction to nomenclature", "", "")
        assert score == 1

    def test_unknown_type_with_reduce_still_clamps_to_1(self):
        score = score_event("unknown", "Administrative cleanup", "", "")
        assert score == 1


class TestScoreEventNoneTitle:
    """Test handling of None title."""

    def test_none_title_uses_empty_string(self):
        # Should not raise, and should get base score
        score = score_event("final_rule", None, "", "")
        assert score == 4


class TestScoreAllUnscored:
    """Test score_all_unscored() database integration."""

    def test_scores_unscored_events(self, db_path):
        conn = sqlite3.connect(db_path)

        # Insert unscored events (impact_score=0)
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, impact_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test", "unscored-1", "executive_order", "Emergency Order", "DOD", 0),
        )
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, impact_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test", "unscored-2", "notice", "Minor Notice", "DOC", 0),
        )
        conn.commit()

        count = score_all_unscored(conn)
        assert count == 2

        # Verify scores were updated
        row1 = conn.execute(
            "SELECT impact_score FROM regulatory_events WHERE source_id = 'unscored-1'"
        ).fetchone()
        assert row1[0] >= 4  # executive_order base=5 + emergency boost, capped at 5

        row2 = conn.execute(
            "SELECT impact_score FROM regulatory_events WHERE source_id = 'unscored-2'"
        ).fetchone()
        assert row2[0] == 1  # notice base=1, no boost

        conn.close()

    def test_does_not_touch_already_scored_events(self, db_path):
        conn = sqlite3.connect(db_path)

        # The conftest seeds events with impact_score=4
        pre_scored = conn.execute(
            "SELECT id, impact_score FROM regulatory_events WHERE impact_score != 0"
        ).fetchall()
        assert len(pre_scored) > 0

        score_all_unscored(conn)

        # Verify pre-scored events unchanged
        for event_id, original_score in pre_scored:
            row = conn.execute(
                "SELECT impact_score FROM regulatory_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            assert row[0] == original_score

        conn.close()

    def test_returns_zero_when_nothing_to_score(self, db_path):
        conn = sqlite3.connect(db_path)
        # All conftest events have impact_score=4, none with 0
        count = score_all_unscored(conn)
        assert count == 0
        conn.close()

    def test_uses_db_path_when_no_conn_provided(self, db_path):
        """Verify score_all_unscored falls back to DB_PATH when conn is None."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, agency, impact_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test", "unscored-fallback", "final_rule", "Test Rule", "EPA", 0),
        )
        conn.commit()
        conn.close()

        from unittest.mock import patch

        with patch("analysis.impact_scorer.DB_PATH", db_path):
            count = score_all_unscored()  # No conn argument
        assert count == 1

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT impact_score FROM regulatory_events WHERE source_id = 'unscored-fallback'"
        ).fetchone()
        assert row[0] == 4  # final_rule base score
        conn.close()
