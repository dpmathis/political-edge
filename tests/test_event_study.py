"""Tests for analysis/event_study.py — EventStudy class and EventStudyResults."""

import sqlite3

import pytest

from analysis.event_study import EventStudy, EventStudyResults


def test_dedup_events(db_path):
    events = [
        {"date": "2025-10-10", "ticker": "LMT", "label": "first"},
        {"date": "2025-10-10", "ticker": "LMT", "label": "duplicate"},
        {"date": "2025-10-15", "ticker": "LMT", "label": "second"},
    ]
    es = EventStudy(db_path=db_path)
    result = es.run(events, study_name="dedup_test", window_pre=1, window_post=3)
    assert result.num_events <= 2  # at most 2 unique (date, ticker) pairs


def test_market_adjusted_car(db_path):
    events = [{"date": "2025-10-10", "ticker": "LMT", "label": "test"}]
    es = EventStudy(db_path=db_path)
    result = es.run(events, study_name="single_event", window_pre=1, window_post=3)
    assert result.num_events == 1
    assert isinstance(result.mean_car, float)
    assert isinstance(result.p_value, float)


def test_empty_events(db_path):
    es = EventStudy(db_path=db_path)
    result = es.run([], study_name="empty_test")
    assert result.num_events == 0
    assert isinstance(result, EventStudyResults)
    assert result.p_value == 1.0


def test_save_to_db(db_path):
    events = [{"date": "2025-10-10", "ticker": "LMT", "label": "save_test"}]
    es = EventStudy(db_path=db_path)
    result = es.run(events, study_name="save_study", window_pre=1, window_post=3)
    study_id = result.save_to_db(db_path)
    assert study_id is not None

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT study_name FROM event_studies WHERE study_id = ?", (study_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "save_study"


def test_is_significant():
    sig_result = EventStudyResults(
        study_name="sig", hypothesis="", method="market_adjusted",
        benchmark="SPY", window_pre=5, window_post=10, num_events=10,
        mean_car=0.01, median_car=0.01, t_statistic=2.5, p_value=0.03,
        win_rate=0.7, sharpe_ratio=1.0,
    )
    assert sig_result.is_significant() is True

    insig_result = EventStudyResults(
        study_name="insig", hypothesis="", method="market_adjusted",
        benchmark="SPY", window_pre=5, window_post=10, num_events=10,
        mean_car=0.005, median_car=0.004, t_statistic=1.2, p_value=0.10,
        win_rate=0.55, sharpe_ratio=0.5,
    )
    assert insig_result.is_significant() is False
