"""Shared test fixtures for Political Edge test suite."""

import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS regulatory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    agency TEXT,
    publication_date DATE,
    effective_date DATE,
    comment_deadline DATE,
    url TEXT,
    raw_json TEXT,
    sectors TEXT,
    tickers TEXT,
    impact_score INTEGER DEFAULT 0,
    user_notes TEXT,
    trade_action TEXT DEFAULT 'none',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adj_close REAL,
    volume INTEGER,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS event_studies (
    study_id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_name TEXT NOT NULL,
    hypothesis TEXT,
    benchmark TEXT,
    window_pre INTEGER,
    window_post INTEGER,
    num_events INTEGER,
    mean_car REAL,
    median_car REAL,
    t_statistic REAL,
    p_value REAL,
    sharpe_ratio REAL,
    win_rate REAL,
    results_json TEXT,
    parameters_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_study_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id INTEGER,
    event_date DATE,
    ticker TEXT,
    event_description TEXT,
    car_pre REAL,
    car_post REAL,
    car_full REAL,
    abnormal_returns_json TEXT,
    benchmark_returns_json TEXT
);

CREATE TABLE IF NOT EXISTS fomc_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date DATE NOT NULL,
    event_type TEXT,
    rate_decision TEXT,
    hawkish_dovish_score REAL,
    statement_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS macro_regimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE UNIQUE NOT NULL,
    growth_roc REAL,
    inflation_roc REAL,
    quadrant INTEGER,
    quadrant_label TEXT,
    yield_curve_spread REAL,
    vix REAL,
    confidence TEXT,
    position_size_modifier REAL
);

CREATE TABLE IF NOT EXISTS macro_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL,
    date DATE NOT NULL,
    value REAL,
    rate_of_change_6m REAL,
    UNIQUE(series_id, date)
);

CREATE TABLE IF NOT EXISTS trading_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date DATE,
    ticker TEXT,
    signal_type TEXT,
    direction TEXT,
    conviction TEXT,
    source_event_id INTEGER,
    source_table TEXT,
    rationale TEXT,
    macro_regime_at_signal INTEGER,
    position_size_modifier REAL,
    status TEXT DEFAULT 'pending',
    stop_loss_price REAL,
    take_profit_price REAL,
    suggested_position_size REAL,
    time_horizon_days INTEGER,
    expected_car REAL,
    historical_win_rate REAL,
    historical_p_value REAL,
    historical_n_events INTEGER,
    prediction_market_prob REAL,
    entry_price REAL,
    entry_date DATE,
    exit_price REAL,
    exit_date DATE,
    pnl_percent REAL,
    pnl_dollars REAL,
    holding_days INTEGER,
    user_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fda_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date DATE,
    event_type TEXT,
    ticker TEXT,
    drug_name TEXT,
    company_name TEXT,
    details TEXT,
    outcome TEXT,
    abnormal_return REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prediction_markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT,
    category TEXT,
    related_ticker TEXT,
    current_price REAL,
    volume REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT UNIQUE NOT NULL,
    company_name TEXT,
    sector TEXT,
    active BOOLEAN DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_market_ticker ON market_data(ticker, date);
CREATE INDEX IF NOT EXISTS idx_events_date ON regulatory_events(publication_date DESC);
"""


def _generate_market_data(ticker: str, start_date: date, days: int, base_price: float = 100.0):
    """Generate synthetic market data with small random-walk returns."""
    import random
    random.seed(42 + hash(ticker))
    rows = []
    price = base_price
    current = start_date
    for _ in range(days):
        # Skip weekends
        while current.weekday() >= 5:
            current += timedelta(days=1)
        ret = random.gauss(0.0003, 0.015)  # ~0.03% daily drift, 1.5% vol
        price *= (1 + ret)
        rows.append((
            ticker,
            current.isoformat(),
            round(price * 0.998, 2),  # open
            round(price * 1.005, 2),  # high
            round(price * 0.995, 2),  # low
            round(price, 2),          # close
            round(price, 2),          # adj_close
            random.randint(1_000_000, 50_000_000),  # volume
        ))
        current += timedelta(days=1)
    return rows


@pytest.fixture
def db_path():
    """Create a temporary SQLite DB with full schema and sample data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)

    # Seed market data: SPY and LMT for 60 trading days
    start = date(2025, 10, 1)
    for ticker, base in [("SPY", 450.0), ("LMT", 480.0), ("XLI", 110.0)]:
        rows = _generate_market_data(ticker, start, 60, base)
        conn.executemany(
            "INSERT INTO market_data (ticker, date, open, high, low, close, adj_close, volume) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )

    # Seed regulatory events
    events = [
        ("federal_register", f"fr-test-{i}", "final_rule",
         f"Test Rule {i} on Defense Procurement",
         "Department of Defense", (start + timedelta(days=i * 3)).isoformat(),
         "Defense", "LMT", 4)
        for i in range(10)
    ]
    conn.executemany(
        """INSERT INTO regulatory_events
           (source, source_id, event_type, title, agency, publication_date, sectors, tickers, impact_score)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        events,
    )

    # Seed FOMC events
    fomc_dates = [
        ((start + timedelta(days=20)).isoformat(), "meeting"),
        ((start + timedelta(days=50)).isoformat(), "meeting"),
    ]
    conn.executemany(
        "INSERT INTO fomc_events (event_date, event_type) VALUES (?,?)",
        fomc_dates,
    )

    # Seed macro regimes
    for i in range(60):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        quadrant = 1 if i < 30 else 2
        label = "Goldilocks" if quadrant == 1 else "Reflation"
        conn.execute(
            """INSERT OR IGNORE INTO macro_regimes
               (date, quadrant, quadrant_label, position_size_modifier, confidence)
               VALUES (?,?,?,?,?)""",
            (d.isoformat(), quadrant, label, 1.2 if quadrant == 1 else 1.0, "high"),
        )

    conn.commit()
    conn.close()

    yield path

    os.unlink(path)
