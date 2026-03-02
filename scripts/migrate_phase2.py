#!/usr/bin/env python3
"""Add Phase 2-5 tables to existing database. Safe to run multiple times."""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

MIGRATION_SQL = """
-- Phase 2: FDA Events
CREATE TABLE IF NOT EXISTS fda_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    drug_name TEXT,
    company_name TEXT,
    ticker TEXT,
    indication TEXT,
    event_date DATE NOT NULL,
    outcome TEXT,
    vote_result TEXT,
    source TEXT,
    source_url TEXT,
    details TEXT,
    pre_event_price REAL,
    post_event_price REAL,
    abnormal_return REAL,
    benchmark_ticker TEXT DEFAULT 'XBI',
    user_notes TEXT,
    trade_action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fda_date ON fda_events(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_fda_ticker ON fda_events(ticker);
CREATE INDEX IF NOT EXISTS idx_fda_type ON fda_events(event_type);

-- Phase 2: Event Studies
CREATE TABLE IF NOT EXISTS event_studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_name TEXT NOT NULL,
    hypothesis TEXT,
    event_source TEXT,
    benchmark TEXT,
    window_pre INTEGER DEFAULT 5,
    window_post INTEGER DEFAULT 10,
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
    study_id INTEGER NOT NULL,
    event_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    event_description TEXT,
    car_pre REAL,
    car_post REAL,
    car_full REAL,
    abnormal_returns_json TEXT,
    benchmark_returns_json TEXT,
    FOREIGN KEY (study_id) REFERENCES event_studies(id)
);
CREATE INDEX IF NOT EXISTS idx_esr_study ON event_study_results(study_id);

-- Phase 4: Macro (create empty for future)
CREATE TABLE IF NOT EXISTS macro_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL,
    date DATE NOT NULL,
    value REAL NOT NULL,
    rate_of_change_3m REAL,
    rate_of_change_6m REAL,
    rate_of_change_12m REAL,
    UNIQUE(series_id, date)
);
CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_indicators(series_id, date);

CREATE TABLE IF NOT EXISTS macro_regimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE UNIQUE NOT NULL,
    growth_roc REAL,
    inflation_roc REAL,
    quadrant INTEGER,
    quadrant_label TEXT,
    yield_curve_spread REAL,
    vix REAL,
    confidence TEXT DEFAULT 'low',
    position_size_modifier REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_regime_date ON macro_regimes(date DESC);

CREATE TABLE IF NOT EXISTS fomc_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date DATE NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT,
    rate_decision TEXT,
    fed_funds_rate REAL,
    statement_url TEXT,
    statement_text TEXT,
    previous_statement_diff TEXT,
    hawkish_dovish_score REAL,
    spx_return_day REAL,
    spx_return_2day REAL,
    user_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fomc_date ON fomc_events(event_date DESC);

-- Phase 5: Signals & Paper Trading (create empty for future)
CREATE TABLE IF NOT EXISTS trading_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    conviction TEXT DEFAULT 'medium',
    source_event_id INTEGER,
    source_table TEXT,
    rationale TEXT,
    macro_regime_at_signal INTEGER,
    position_size_modifier REAL DEFAULT 1.0,
    status TEXT DEFAULT 'pending',
    entry_price REAL,
    entry_date DATE,
    exit_price REAL,
    exit_date DATE,
    pnl_dollars REAL,
    pnl_percent REAL,
    holding_days INTEGER,
    user_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_signals_date ON trading_signals(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON trading_signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_status ON trading_signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_dedup ON trading_signals(ticker, signal_type, signal_date);
CREATE INDEX IF NOT EXISTS idx_events_impact ON regulatory_events(impact_score);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    broker TEXT DEFAULT 'alpaca',
    order_id TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL,
    filled_price REAL,
    filled_at TIMESTAMP,
    status TEXT,
    order_type TEXT DEFAULT 'market',
    raw_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (signal_id) REFERENCES trading_signals(id)
);
CREATE INDEX IF NOT EXISTS idx_paper_ticker ON paper_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_paper_signal ON paper_trades(signal_id);
"""


PHASE_6_ALTERS = [
    # Sprint 2: Trade parameters on signals
    "ALTER TABLE trading_signals ADD COLUMN stop_loss_price REAL",
    "ALTER TABLE trading_signals ADD COLUMN take_profit_price REAL",
    "ALTER TABLE trading_signals ADD COLUMN suggested_position_size REAL",
    "ALTER TABLE trading_signals ADD COLUMN time_horizon_days INTEGER",
    "ALTER TABLE trading_signals ADD COLUMN expected_car REAL",
    "ALTER TABLE trading_signals ADD COLUMN historical_win_rate REAL",
    "ALTER TABLE trading_signals ADD COLUMN historical_p_value REAL",
    "ALTER TABLE trading_signals ADD COLUMN historical_n_events INTEGER",
    # Sprint 3: Prediction markets
    "ALTER TABLE trading_signals ADD COLUMN prediction_market_prob REAL",
]

PHASE_6_TABLES = """
-- Sprint 3: Prediction Markets
CREATE TABLE IF NOT EXISTS prediction_markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL,
    question_text TEXT,
    current_price REAL,
    volume REAL,
    resolution_date DATE,
    category TEXT,
    related_ticker TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pred_category ON prediction_markets(category);

-- Data collection log
CREATE TABLE IF NOT EXISTS data_collection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collector_name TEXT NOT NULL,
    run_type TEXT DEFAULT 'manual',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    records_added INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    errors TEXT,
    status TEXT DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS idx_dcl_collector ON data_collection_log(collector_name, started_at DESC);
"""


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}. Run setup_db.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # Get existing tables for comparison
    before = set(
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    )

    conn.executescript(MIGRATION_SQL)
    conn.executescript(PHASE_6_TABLES)

    # Add columns (safe to run multiple times — SQLite errors on dups which we ignore)
    for alter in PHASE_6_ALTERS:
        try:
            conn.execute(alter)
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()

    after = set(
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    )

    new_tables = after - before
    if new_tables:
        print(f"Created {len(new_tables)} new tables: {', '.join(sorted(new_tables))}")
    else:
        print("All tables already exist (migration is idempotent)")

    print(f"Total tables: {len(after)}")
    conn.close()


if __name__ == "__main__":
    main()
