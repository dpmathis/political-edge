"""Tests for analysis/eo_classifier.py — classify_eo() and keyword constants."""

from analysis.eo_classifier import classify_eo


def test_tariff_classification():
    result = classify_eo("Imposing Tariffs on Imports from China")
    assert result["topic"] == "tariff_trade"
    assert result["is_tradeable"] is True


def test_defense_classification():
    result = classify_eo("Strengthening the National Defense Industrial Base")
    assert result["topic"] == "defense"


def test_energy_classification():
    result = classify_eo("Promoting Energy Independence and Domestic Production")
    assert result["topic"] == "energy"


def test_unknown_not_tradeable():
    result = classify_eo("Establishing a Federal Advisory Council on Something")
    assert result["topic"] == "other"
    assert result["is_tradeable"] is False


def test_tariff_direction_imposition():
    result = classify_eo("Imposing Additional Tariffs on Steel Imports")
    assert result["topic"] == "tariff_trade"
    assert result["tariff_direction"] == "imposition"


def test_tariff_direction_relief():
    result = classify_eo("Suspending Tariffs on Canadian Lumber Imports")
    assert result["topic"] == "tariff_trade"
    assert result["tariff_direction"] == "relief"
