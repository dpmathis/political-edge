"""Tests for analysis/signal_generator.py — helper functions."""

from analysis.signal_generator import _adjust_conviction, _determine_regulatory_direction


def test_conviction_adjustment_boost():
    assert _adjust_conviction("medium", 1, 0) == "high"


def test_conviction_adjustment_reduce():
    assert _adjust_conviction("medium", 0, 1) == "low"


def test_conviction_clamp_at_high():
    assert _adjust_conviction("high", 2, 0) == "high"


def test_conviction_clamp_at_low():
    assert _adjust_conviction("low", 0, 2) == "low"


def test_direction_restrict_keyword():
    result = _determine_regulatory_direction("final_rule", "Rule to restrict exports", None, None)
    assert result == "short"


def test_direction_support_keyword():
    result = _determine_regulatory_direction("final_rule", "Subsidy for clean energy production", None, None)
    assert result == "long"


def test_direction_tariff_default():
    result = _determine_regulatory_direction("final_rule", "New tariff schedule on imports", None, None)
    assert result == "short"
