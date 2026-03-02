#!/usr/bin/env python3
"""Initialize the Political Edge database — creates tables, indexes, and seeds data."""

import os
import sys
import sqlite3

# Allow running from scripts/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH, load_config, load_sector_mappings

SCHEMA_SQL = """
-- ============================================
-- CORE TABLES
-- ============================================

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

CREATE TABLE IF NOT EXISTS contract_awards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    award_id TEXT UNIQUE NOT NULL,
    recipient_name TEXT NOT NULL,
    recipient_ticker TEXT,
    awarding_agency TEXT,
    award_amount REAL,
    award_date DATE,
    description TEXT,
    naics_code TEXT,
    place_of_performance TEXT,
    contract_type TEXT,
    url TEXT,
    raw_json TEXT,
    user_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lobbying_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id TEXT UNIQUE NOT NULL,
    registrant_name TEXT NOT NULL,
    client_name TEXT NOT NULL,
    client_ticker TEXT,
    amount REAL,
    filing_year INTEGER,
    filing_period TEXT,
    specific_issues TEXT,
    government_entities TEXT,
    lobbyists TEXT,
    url TEXT,
    raw_json TEXT,
    user_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS congress_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    politician TEXT NOT NULL,
    party TEXT,
    chamber TEXT,
    ticker TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    amount_range TEXT,
    trade_date DATE,
    disclosure_date DATE,
    asset_description TEXT,
    committees TEXT,
    url TEXT,
    source TEXT,
    user_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT UNIQUE NOT NULL,
    company_name TEXT,
    sector TEXT,
    subsector TEXT,
    thesis TEXT,
    key_agencies TEXT,
    key_keywords TEXT,
    added_date DATE DEFAULT CURRENT_DATE,
    active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sector_keyword_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT NOT NULL,
    keyword TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    UNIQUE(sector, keyword)
);

CREATE TABLE IF NOT EXISTS company_contractor_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    contractor_name TEXT NOT NULL,
    UNIQUE(ticker, contractor_name)
);

CREATE TABLE IF NOT EXISTS pipeline_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_event_id INTEGER NOT NULL,
    final_event_id INTEGER,
    agency TEXT,
    sector TEXT,
    tickers TEXT,
    proposed_date DATE,
    comment_deadline DATE,
    estimated_final_date DATE,
    actual_final_date DATE,
    status TEXT DEFAULT 'proposed',
    days_in_pipeline INTEGER,
    title_similarity REAL,
    impact_score INTEGER DEFAULT 0,
    proposed_title TEXT,
    historical_car REAL,
    historical_n INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proposed_event_id) REFERENCES regulatory_events(id),
    FOREIGN KEY (final_event_id) REFERENCES regulatory_events(id),
    UNIQUE(proposed_event_id)
);

-- ============================================
-- INDEXES
-- ============================================

CREATE INDEX IF NOT EXISTS idx_events_date ON regulatory_events(publication_date DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON regulatory_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_agency ON regulatory_events(agency);
CREATE INDEX IF NOT EXISTS idx_events_sectors ON regulatory_events(sectors);
CREATE INDEX IF NOT EXISTS idx_contracts_date ON contract_awards(award_date DESC);
CREATE INDEX IF NOT EXISTS idx_contracts_ticker ON contract_awards(recipient_ticker);
CREATE INDEX IF NOT EXISTS idx_lobbying_client ON lobbying_filings(client_ticker);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON congress_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_date ON congress_trades(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_market_ticker ON market_data(ticker, date);
CREATE INDEX IF NOT EXISTS idx_pipeline_status ON pipeline_rules(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_sector ON pipeline_rules(sector);
CREATE INDEX IF NOT EXISTS idx_pipeline_proposed ON pipeline_rules(proposed_event_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_deadline ON pipeline_rules(comment_deadline);
CREATE INDEX IF NOT EXISTS idx_pipeline_proposed_date ON pipeline_rules(proposed_date);
"""


def create_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    print(f"Database created at {DB_PATH}")
    return conn


def seed_watchlist(conn: sqlite3.Connection):
    cfg = load_config()
    count = 0
    for sector_name, companies in cfg.get("watchlist", {}).items():
        for company in companies:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO watchlist
                       (ticker, company_name, sector, subsector, key_agencies, key_keywords)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        company["ticker"],
                        company["name"],
                        company.get("sector", sector_name.title()),
                        company.get("subsector", ""),
                        company.get("agencies", ""),
                        company.get("keywords", ""),
                    ),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass
    conn.commit()
    print(f"Seeded {count} watchlist entries")


def seed_sector_keywords(conn: sqlite3.Connection):
    mappings = load_sector_mappings()
    count = 0
    for sector, keywords in mappings.items():
        for keyword in keywords:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO sector_keyword_map (sector, keyword) VALUES (?, ?)",
                    (sector, keyword),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass
    conn.commit()
    print(f"Seeded {count} sector keyword mappings")


def seed_contractor_mappings(conn: sqlite3.Connection):
    cfg = load_config()
    count = 0
    for ticker, names in cfg.get("contractor_mappings", {}).items():
        for name in names:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO company_contractor_map (ticker, contractor_name) VALUES (?, ?)",
                    (ticker, name),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass
    conn.commit()
    print(f"Seeded {count} contractor mappings")


def main():
    conn = create_database()
    seed_watchlist(conn)
    seed_sector_keywords(conn)
    seed_contractor_mappings(conn)
    conn.close()
    print("Setup complete.")


if __name__ == "__main__":
    main()
