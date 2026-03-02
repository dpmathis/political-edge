"""Tests for config/__init__.py — config loading."""

from config import DB_PATH, load_fomc_dates, load_sector_mappings, load_tariff_events


def test_db_path_defined():
    assert DB_PATH
    assert DB_PATH.endswith(".db")


def test_load_tariff_events():
    events = load_tariff_events()
    assert isinstance(events, list)
    for event in events:
        assert "date" in event
        assert "description" in event


def test_load_fomc_dates():
    dates = load_fomc_dates()
    assert isinstance(dates, list)
    assert len(dates) > 0


def test_load_sector_mappings():
    mappings = load_sector_mappings()
    assert isinstance(mappings, dict)
    assert len(mappings) >= 1
