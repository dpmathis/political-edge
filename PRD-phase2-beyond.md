# PRD: Political Edge — Phases 2-5 Implementation Guide

## For Claude Code Execution

*Companion to: PRD-political-edge-trading-tool.md (architecture), PRD-phase1-implementation.md (Phase 1), hypothesis-validation-research.md (research findings)*
*Author: Dan Mathis | Version: 1.0 | Date: March 2026*

---

## 0. Context for Claude Code

### What's Already Built (Phase 1 — Complete)

Phase 1 delivered the foundation. The following is operational inside `./political-edge/`:

**Database:** `data/political_edge.db` — SQLite with WAL mode, all 8 tables from the original schema (`regulatory_events`, `contract_awards`, `lobbying_filings`, `congress_trades`, `market_data`, `watchlist`, `sector_keyword_map`, `company_contractor_map`), all indexes, seed data for 15 watchlist companies across Defense, Healthcare, Energy, and Technology sectors.

**Collectors:**
- `collectors/federal_register.py` — Pulls RULE, PRORULE, PRESDOCU, NOTICE docs. Backfilled 2024-present. >1,000 events stored.
- `collectors/usaspending.py` — Pulls contract awards >$1M for watchlist companies via company_contractor_map. Backfilled 2024-present.
- `collectors/market_data.py` — Pulls daily OHLCV via yfinance for all watchlist tickers + sector ETFs (ITA, XLV, XLE, XLK, XLF, SPY).
- `collectors/base.py` — Base collector class with retry logic, logging, connection management.

**Analysis:**
- `analysis/sector_mapper.py` — Keyword-weighted sector tagging from `sector_keyword_map`. Threshold ≥2.0 weight.
- `analysis/impact_scorer.py` — Rules-based 1-5 scoring (base score by event type + boost/dampen keywords + priority agency bonus).

**Dashboard (Streamlit):**
- `dashboard/app.py` — Multi-page app with shared sidebar filters (date range, sector, impact score, ticker).
- `dashboard/pages/1_RegWatch.py` — KPI cards, events by sector/type charts, filterable event table, expandable detail rows, price chart with event overlay.
- `dashboard/pages/2_Contracts.py` — Contract KPIs, value by company bar chart, awards over time, company deep dive with price overlay.
- `dashboard/pages/3_Watchlist.py` — Watchlist table, per-ticker combined view (price chart + events + contracts + quick stats).

**Scripts:**
- `scripts/setup_db.py` — Creates all tables, seeds watchlist/contractor_map/sector_keywords.
- `scripts/run_collectors.py` — Sequential runner for all collectors.
- `scripts/backfill.py` — Historical data loader with monthly chunking.

**Config:**
- `config/config.yaml` — API keys (empty for keyless APIs), collection settings, watchlist seed data.
- `config/sector_mappings.yaml` — Full weighted keyword → sector mappings.

### What This PRD Covers

This document specifies everything remaining to build Political Edge into a complete trading intelligence platform. It is organized into five phases that should be executed sequentially:

| Phase | Name | Scope | Effort |
|-------|------|-------|--------|
| Phase 2 | Research Infrastructure | Event study framework, FDA calendar collector, backtesting foundations | 2 weekends |
| Phase 3 | Remaining Data Collectors | Congress.gov, lobbying (lda.gov), congressional trades, Regulations.gov | 2 weekends |
| Phase 4 | Macro Overlay & Alerts | FRED macro dashboard, regime classifier, email alert engine, FOMC tracker | 1-2 weekends |
| Phase 5 | Integration & Paper Trading | Signal combiner, Alpaca paper trading, hypothesis backtest runner, UX polish | 2 weekends |

### Key Design Decisions (Changed from Original PRD)

The hypothesis validation research produced findings that change the original plan. Claude Code should follow this PRD, not the original, for all remaining work.

**1. FDA calendar tracking is now a first-class data source.** Academic evidence shows FDA advisory committee votes generate the strongest documented abnormal returns of any political catalyst (5-day CAR of +21% for Fast Track designations, 22% override rate). This was not in the original PRD. A new `fda_events` table and collector are specified below.

**2. Congressional trading is downweighted.** Post-STOCK Act evidence shows rank-and-file members have no systematic edge. The `congress_trades` collector and dashboard page remain but are simplified — no standalone page, folded into the Watchlist combined view as a supplementary signal. No effort on committee-level analysis.

**3. The event study framework is the highest-priority new build.** Before adding more collectors, we need the ability to test whether signals actually predict returns. This reusable module powers all hypothesis backtests and eventually feeds the paper trading system.

**4. Lobbying collector targets lda.gov, not lda.senate.gov.** The Senate LDA API sunsets June 30, 2026. Build against the new lda.gov endpoint from the start.

**5. Macro regime is a position-sizing overlay, not a standalone signal.** The growth/inflation quadrant model (Hedgeye-style) has documented relationships but weak tradeable alpha. Implement as a risk management filter that adjusts conviction levels, not as a signal source.

**6. Quiver Quantitative is $25/month, not $10.** The Python package is unmaintained. Use their web API directly with `requests`, or scrape Capitol Trades as the free fallback for congressional trading data.

---

## 1. New Database Schema Additions

Add these tables to `political_edge.db`. Run as a migration script (`scripts/migrate_phase2.py`) that adds tables without destroying existing data.

```sql
-- ============================================
-- PHASE 2: FDA EVENTS
-- ============================================

CREATE TABLE IF NOT EXISTS fda_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,          -- 'adcom_vote', 'pdufa_date', 'fast_track',
                                       -- 'breakthrough', 'approval', 'crl',
                                       -- 'warning_letter', 'import_alert'
    drug_name TEXT,
    company_name TEXT,
    ticker TEXT,                        -- Mapped public company ticker
    indication TEXT,                    -- Disease/condition
    event_date DATE NOT NULL,
    outcome TEXT,                       -- 'positive', 'negative', 'mixed', 'pending'
    vote_result TEXT,                   -- For adcom: '12-1 favorable', etc.
    source TEXT,                        -- 'fda_calendar', 'biopharmcatalyst', 'federal_register'
    source_url TEXT,
    details TEXT,                       -- Free-text detail / summary

    -- Analysis fields
    pre_event_price REAL,              -- Close price day before event
    post_event_price REAL,             -- Close price day of event
    abnormal_return REAL,              -- Calculated AR vs. benchmark
    benchmark_ticker TEXT DEFAULT 'XBI', -- Benchmark used for AR calc

    user_notes TEXT,
    trade_action TEXT,                 -- 'none', 'watch', 'long', 'short', 'close'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fda_date ON fda_events(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_fda_ticker ON fda_events(ticker);
CREATE INDEX IF NOT EXISTS idx_fda_type ON fda_events(event_type);

-- ============================================
-- PHASE 2: EVENT STUDIES
-- ============================================

CREATE TABLE IF NOT EXISTS event_studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_name TEXT NOT NULL,           -- 'fda_adcom_2018_2026', 'tariff_sector_rotation', etc.
    hypothesis TEXT,                    -- What we're testing
    event_source TEXT,                  -- Table/source of events
    benchmark TEXT,                     -- Benchmark ticker or method
    window_pre INTEGER DEFAULT 5,      -- Days before event
    window_post INTEGER DEFAULT 10,    -- Days after event
    num_events INTEGER,                -- Total events in study
    mean_car REAL,                     -- Mean CAR across all events
    median_car REAL,
    t_statistic REAL,
    p_value REAL,
    sharpe_ratio REAL,
    win_rate REAL,                     -- % of events with positive CAR
    results_json TEXT,                 -- Full per-event results as JSON
    parameters_json TEXT,              -- Study configuration as JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_study_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id INTEGER NOT NULL,
    event_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    event_description TEXT,
    car_pre REAL,                      -- CAR for pre-event window
    car_post REAL,                     -- CAR for post-event window
    car_full REAL,                     -- CAR for full window
    abnormal_returns_json TEXT,        -- Daily AR series as JSON array
    benchmark_returns_json TEXT,       -- Daily benchmark returns as JSON array
    FOREIGN KEY (study_id) REFERENCES event_studies(id)
);

CREATE INDEX IF NOT EXISTS idx_esr_study ON event_study_results(study_id);

-- ============================================
-- PHASE 4: MACRO REGIME
-- ============================================

CREATE TABLE IF NOT EXISTS macro_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL,            -- FRED series ID (e.g., 'GDPC1', 'CPIAUCSL')
    date DATE NOT NULL,
    value REAL NOT NULL,
    rate_of_change_3m REAL,            -- 3-month annualized RoC
    rate_of_change_6m REAL,            -- 6-month annualized RoC
    rate_of_change_12m REAL,           -- 12-month YoY RoC
    UNIQUE(series_id, date)
);

CREATE TABLE IF NOT EXISTS macro_regimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE UNIQUE NOT NULL,         -- Month-end date
    growth_roc REAL,                   -- GDP/ISM growth rate of change
    inflation_roc REAL,                -- CPI/PCE inflation rate of change
    quadrant INTEGER,                  -- 1=Goldilocks, 2=Reflation, 3=Stagflation, 4=Deflation
    quadrant_label TEXT,               -- Human-readable label
    yield_curve_spread REAL,           -- 10Y-2Y spread
    vix REAL,                          -- VIX level
    confidence TEXT DEFAULT 'low',     -- 'low', 'medium', 'high' — how confident is the regime call
    position_size_modifier REAL DEFAULT 1.0, -- Multiply default position size by this
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_indicators(series_id, date);
CREATE INDEX IF NOT EXISTS idx_regime_date ON macro_regimes(date DESC);

-- ============================================
-- PHASE 4: FOMC TRACKING
-- ============================================

CREATE TABLE IF NOT EXISTS fomc_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date DATE NOT NULL,
    event_type TEXT NOT NULL,           -- 'meeting', 'minutes', 'speech', 'testimony'
    title TEXT,
    rate_decision TEXT,                 -- 'hike_25', 'hike_50', 'cut_25', 'cut_50', 'hold', null
    fed_funds_rate REAL,               -- Effective rate after decision
    statement_url TEXT,
    statement_text TEXT,               -- Full statement text for diffing
    previous_statement_diff TEXT,      -- Output of difflib comparison
    hawkish_dovish_score REAL,         -- -1.0 (very dovish) to +1.0 (very hawkish)
    spx_return_day REAL,              -- S&P 500 return on announcement day
    spx_return_2day REAL,             -- S&P 500 return t+0 to t+1
    user_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fomc_date ON fomc_events(event_date DESC);

-- ============================================
-- PHASE 5: SIGNALS & PAPER TRADING
-- ============================================

CREATE TABLE IF NOT EXISTS trading_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    signal_type TEXT NOT NULL,          -- 'fda_catalyst', 'contract_momentum', 'regulatory_event',
                                       -- 'lobbying_spike', 'tariff_rotation', 'macro_regime'
    direction TEXT NOT NULL,            -- 'long', 'short', 'close'
    conviction TEXT DEFAULT 'medium',   -- 'low', 'medium', 'high'
    source_event_id INTEGER,           -- FK to originating event (polymorphic — store table name in source_table)
    source_table TEXT,                 -- 'fda_events', 'regulatory_events', 'contract_awards', etc.
    rationale TEXT,                    -- Why this signal was generated
    macro_regime_at_signal INTEGER,    -- Quadrant at time of signal
    position_size_modifier REAL DEFAULT 1.0, -- From macro regime
    status TEXT DEFAULT 'pending',     -- 'pending', 'active', 'closed', 'expired', 'skipped'

    -- Execution tracking (filled by paper/live trading)
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

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,                 -- FK to trading_signals (optional — manual trades have no signal)
    broker TEXT DEFAULT 'alpaca',
    order_id TEXT,                     -- Broker order ID
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,                -- 'buy', 'sell'
    quantity INTEGER NOT NULL,
    price REAL,
    filled_price REAL,
    filled_at TIMESTAMP,
    status TEXT,                       -- 'pending', 'filled', 'cancelled', 'rejected'
    order_type TEXT DEFAULT 'market',  -- 'market', 'limit', 'stop'
    raw_response TEXT,                 -- Full broker response JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (signal_id) REFERENCES trading_signals(id)
);

CREATE INDEX IF NOT EXISTS idx_paper_ticker ON paper_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_paper_signal ON paper_trades(signal_id);
```

### Migration Script (`scripts/migrate_phase2.py`)

```python
"""
Run this once to add Phase 2-5 tables to existing database.
Safe to run multiple times (CREATE IF NOT EXISTS).

Usage:
    python scripts/migrate_phase2.py
"""
```

**Behavior:**
1. Load DB path from `config.yaml`
2. Execute all CREATE TABLE and CREATE INDEX statements above
3. Print summary of tables created/already existing
4. Do NOT modify any existing tables or data

---

## 2. Phase 2: Research Infrastructure

### Priority: HIGHEST — Build this before any other new collector.

The event study framework is a reusable Python module that powers all hypothesis backtests. Every other phase depends on it.

### 2.1 Event Study Framework (`analysis/event_study.py`)

This is the analytical core of the platform. It calculates abnormal returns around dated events and tests for statistical significance.

```
analysis/
├── event_study.py          # Core event study engine
├── backtest_runner.py      # Runs predefined hypothesis backtests
├── sector_mapper.py        # (existing)
└── impact_scorer.py        # (existing)
```

#### Class: `EventStudy`

```python
class EventStudy:
    """
    Reusable event study framework.

    Given a list of (event_date, ticker) pairs and a benchmark,
    calculates abnormal returns, cumulative abnormal returns,
    and statistical significance.
    """

    def __init__(self, db_path: str, benchmark_ticker: str = "SPY"):
        self.db_path = db_path
        self.benchmark_ticker = benchmark_ticker

    def run(
        self,
        events: list[dict],        # [{"date": "2025-04-02", "ticker": "XLE", "label": "Liberation Day"}]
        window_pre: int = 5,       # Trading days before event
        window_post: int = 10,     # Trading days after event
        estimation_window: int = 120,  # Days for expected return estimation
        benchmark: str = None,     # Override default benchmark
        method: str = "market_adjusted"  # 'market_adjusted', 'market_model', 'sector_adjusted'
    ) -> EventStudyResults:
        """Run complete event study."""
        pass

    def _get_price_data(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Pull from market_data table. If missing, fetch via yfinance and cache."""
        pass

    def _calculate_expected_returns(
        self, ticker: str, benchmark: str, estimation_start: str, estimation_end: str, method: str
    ) -> tuple[float, float]:
        """
        Calculate expected returns using estimation window.
        market_adjusted: AR = R_stock - R_benchmark
        market_model: AR = R_stock - (alpha + beta * R_benchmark), where alpha/beta from OLS regression
        sector_adjusted: AR = R_stock - R_sector_etf (auto-select sector ETF from watchlist)
        """
        pass

    def _calculate_abnormal_returns(
        self, ticker: str, event_date: str, window_pre: int, window_post: int,
        expected_alpha: float, expected_beta: float, benchmark: str, method: str
    ) -> dict:
        """
        Returns {
            "daily_ar": [float],          # Daily abnormal returns
            "daily_car": [float],         # Cumulative AR
            "car_pre": float,             # CAR for pre-event window
            "car_post": float,            # CAR for post-event window
            "car_full": float,            # CAR for full window
            "benchmark_returns": [float]  # Daily benchmark returns for reference
        }
        """
        pass

    def _test_significance(self, cars: list[float]) -> dict:
        """
        Returns {
            "mean_car": float,
            "median_car": float,
            "std_car": float,
            "t_stat": float,
            "p_value": float,
            "n_events": int,
            "win_rate": float,            # % of events with positive CAR
            "sharpe": float               # mean_car / std_car (annualized)
        }
        """
        pass
```

#### Dataclass: `EventStudyResults`

```python
@dataclass
class EventStudyResults:
    study_name: str
    hypothesis: str
    method: str
    benchmark: str
    window_pre: int
    window_post: int
    num_events: int
    mean_car: float
    median_car: float
    t_statistic: float
    p_value: float
    win_rate: float
    sharpe_ratio: float
    per_event_results: list[dict]   # Full results per event
    daily_avg_ar: list[float]       # Average AR per day across all events (for plotting)
    daily_avg_car: list[float]      # Average CAR per day across all events

    def save_to_db(self, db_path: str) -> int:
        """Save to event_studies + event_study_results tables. Returns study_id."""
        pass

    def to_dataframe(self) -> pd.DataFrame:
        """Per-event results as DataFrame for analysis."""
        pass

    def summary(self) -> str:
        """Human-readable summary string."""
        pass

    def is_significant(self, alpha: float = 0.05) -> bool:
        """Is the mean CAR statistically significant?"""
        return self.p_value < alpha
```

#### Key Implementation Notes

**Market-adjusted method (default):** AR(t) = R_stock(t) - R_benchmark(t). Simplest, works for most cases.

**Market model method:** Run OLS regression of stock returns on benchmark returns over the 120-day estimation window *before* the event window. Then AR(t) = R_stock(t) - (alpha + beta * R_benchmark(t)). More accurate but requires sufficient estimation data.

**Handling missing price data:** If `market_data` table doesn't have data for the event window, auto-fetch via yfinance and insert into DB. Log a warning. This ensures the event study works even for tickers not in the watchlist.

**Trading days only:** Use `pd.bdate_range` or filter for rows where market_data exists. Event windows are in trading days, not calendar days.

**Ensure no look-ahead bias:** Estimation window must end at least 1 day before the event window starts. If event_date is t=0, estimation window is t-125 to t-6 (with a 5-day gap), event window is t-5 to t+10.

---

### 2.2 FDA Calendar Collector (`collectors/fda_calendar.py`)

**This is a new collector not in the original PRD.** FDA advisory committee votes produce the strongest documented abnormal returns of any political catalyst in the hypothesis validation research.

#### Data Sources (in priority order)

**Source 1: Federal Register API (already connected)**
FDA publishes AdCom meeting notices in the Federal Register as `NOTICE` documents. Filter existing `regulatory_events` for events where `agency LIKE '%Food and Drug%'` and title contains "Advisory Committee" or "Advisory Panel". These are already being collected — they just need to be parsed into `fda_events`.

**Source 2: FDA.gov Advisory Committee Calendar**
URL: `https://www.fda.gov/advisory-committees/advisory-committee-calendar`
HTML page listing upcoming AdCom meetings with dates, committee names, and agenda topics. Parse with BeautifulSoup.

**Source 3: BioPharmCatalyst PDUFA Calendar (backup)**
URL: `https://www.biopharmcatalyst.com/calendars/fda-calendar`
Free, comprehensive calendar of PDUFA dates, AdCom votes, and catalysts. HTML parsing required.

#### Method: `collect()`

1. **Parse Federal Register entries** — Query `regulatory_events` WHERE `agency LIKE '%Food and Drug%'` AND `sectors LIKE '%Healthcare%'` AND `created_at > last_run_date`. For each, extract:
   - Drug name (regex from title: look for brand names, generic names, NDA/BLA numbers)
   - Company name (regex or lookup against a pharma company mapping)
   - Event type (map from title keywords: "Advisory Committee" → `adcom_vote`, "approval" → `approval`, "Complete Response" → `crl`)
   - Event date (from publication_date or extract from title)

2. **Scrape FDA.gov Advisory Committee Calendar** — GET the calendar page, parse HTML for upcoming meetings. Extract: date, committee name, agenda items, related drug/company.

3. **Map to tickers** — Use a new `pharma_company_map` config (see below) plus watchlist lookup.

4. **Dedup and insert** into `fda_events` table.

5. **Auto-calculate abnormal returns** for past events: if `event_date` is in the past and `ticker` is not null, calculate AR vs XBI using the event study framework.

#### Config Addition: `config/pharma_companies.yaml`

```yaml
# Company name → ticker mappings for FDA events
# More comprehensive than watchlist (includes non-watchlist biotechs)
pharma_companies:
  - names: ["Pfizer", "PFIZER INC"]
    ticker: PFE
  - names: ["Eli Lilly", "ELI LILLY AND COMPANY", "Lilly"]
    ticker: LLY
  - names: ["Johnson & Johnson", "JOHNSON & JOHNSON", "J&J", "Janssen"]
    ticker: JNJ
  - names: ["Merck", "MERCK & CO", "MSD"]
    ticker: MRK
  - names: ["AbbVie", "ABBVIE INC"]
    ticker: ABBV
  - names: ["Bristol-Myers Squibb", "BRISTOL-MYERS SQUIBB", "BMS"]
    ticker: BMY
  - names: ["Amgen", "AMGEN INC"]
    ticker: AMGN
  - names: ["Gilead", "GILEAD SCIENCES"]
    ticker: GILD
  - names: ["Regeneron", "REGENERON PHARMACEUTICALS"]
    ticker: REGN
  - names: ["Vertex", "VERTEX PHARMACEUTICALS"]
    ticker: VRTX
  - names: ["Moderna", "MODERNA INC"]
    ticker: MRNA
  - names: ["Novo Nordisk", "NOVO NORDISK"]
    ticker: NVO
  - names: ["AstraZeneca", "ASTRAZENECA"]
    ticker: AZN
  - names: ["Novartis", "NOVARTIS AG"]
    ticker: NVS
  - names: ["Roche", "ROCHE HOLDING"]
    ticker: RHHBY
  # Add more as needed — this list covers the top 15 by market cap
  # Small-cap biotechs won't be in this list and that's OK — they'll need manual ticker assignment
```

#### Collection Schedule

Run daily at 7am ET. FDA calendar updates are infrequent but AdCom dates are published 60+ days ahead, so daily is sufficient.

---

### 2.3 Hypothesis Backtest Runner (`analysis/backtest_runner.py`)

Pre-configured backtest scripts for each validated hypothesis. Each is a function that prepares the event list and calls `EventStudy.run()`.

```python
class BacktestRunner:
    """Runs predefined hypothesis backtests using the EventStudy framework."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.event_study = EventStudy(db_path)

    def run_all(self) -> dict[str, EventStudyResults]:
        """Run all hypothesis backtests. Returns dict of name → results."""
        results = {}
        results["fda_adcom"] = self.backtest_fda_adcom()
        results["contract_awards"] = self.backtest_contract_awards()
        results["tariff_sectors"] = self.backtest_tariff_sectors()
        results["fomc_drift"] = self.backtest_fomc_drift()
        return results

    def backtest_fda_adcom(self) -> EventStudyResults:
        """
        Hypothesis A: FDA AdCom positive votes → +5-20% CAR over 5 days.

        Event source: fda_events WHERE event_type = 'adcom_vote'
        Benchmark: XBI (biotech ETF)
        Window: (-1, +5)
        Split by: outcome (positive/negative/mixed)
        """
        pass

    def backtest_contract_awards(self) -> EventStudyResults:
        """
        Hypothesis C: Large DOD contract awards (>$100M) → positive CAR for winning firm.

        Event source: contract_awards WHERE award_amount >= 100000000
                       AND awarding_agency LIKE '%Defense%'
                       AND recipient_ticker IS NOT NULL
        Benchmark: ITA (defense ETF)
        Window: (0, +10)
        Split by: contract size bucket ($100M-500M, $500M-1B, >$1B)
        """
        pass

    def backtest_tariff_sectors(self) -> EventStudyResults:
        """
        Hypothesis B: Tariff announcements → 5-17% sector dispersion.

        Event source: Hardcoded list of major tariff event dates (see TARIFF_EVENTS below).
        Run for each sector ETF: XLE, XLI, XLB, XLV, XLP, XLU, XLK, XLF, XLRE.
        Benchmark: SPY
        Window: (0, +5)
        Test for: consistent sector ranking across events, mean reversion at +10 days.
        """
        pass

    def backtest_fomc_drift(self) -> EventStudyResults:
        """
        Hypothesis F (partial): Pre-FOMC announcement drift.

        Event source: fomc_events WHERE event_type = 'meeting'
        Ticker: SPY
        Benchmark: None (absolute returns)
        Window: (-1, +1) — focus on day before and day of announcement
        """
        pass
```

#### Hardcoded Tariff Event Dates

These are manually compiled from public reporting. Store in `config/tariff_events.yaml`:

```yaml
tariff_events:
  - date: "2018-03-01"
    description: "Trump announces steel/aluminum tariffs"
    affected_sectors: ["XLI", "XLB"]
  - date: "2018-03-22"
    description: "Section 301 tariffs on China announced ($50B)"
    affected_sectors: ["XLK", "XLI"]
  - date: "2018-07-06"
    description: "First $34B China tariffs take effect"
    affected_sectors: ["XLI", "XLK", "XLB"]
  - date: "2018-09-24"
    description: "$200B China tariffs take effect (10%)"
    affected_sectors: ["XLI", "XLK", "XLB", "XLE"]
  - date: "2019-05-10"
    description: "China tariffs raised from 10% to 25%"
    affected_sectors: ["XLI", "XLK", "XLB"]
  - date: "2019-08-01"
    description: "Trump announces additional 10% on $300B"
    affected_sectors: ["XLI", "XLK", "XLB", "XLP"]
  - date: "2019-08-23"
    description: "China retaliates; tariffs escalate further"
    affected_sectors: ["XLI", "XLK", "XLB", "XLE"]
  - date: "2025-02-04"
    description: "Trump executive orders on Canada/Mexico/China tariffs"
    affected_sectors: ["XLI", "XLB", "XLE"]
  - date: "2025-04-02"
    description: "Liberation Day — sweeping reciprocal tariffs"
    affected_sectors: ["XLE", "XLI", "XLB", "XLK", "XLF"]
  - date: "2025-04-09"
    description: "90-day pause announced (expect reversal/bounce)"
    affected_sectors: ["XLE", "XLI", "XLB", "XLK", "XLF"]
  - date: "2025-05-12"
    description: "US-China Geneva trade deal — tariff reduction"
    affected_sectors: ["XLI", "XLK"]
  - date: "2025-07-10"
    description: "US-UK trade deal signed"
    affected_sectors: ["XLI"]
```

---

## 3. Phase 3: Remaining Data Collectors

### 3.1 Congress.gov Collector (`collectors/congress.py`)

**Requires API key** — free from https://api.congress.gov/sign-up/ (uses api.data.gov key system).

**API:** `https://api.congress.gov/v3/`

**Rate limit:** 5,000 requests/hour (recently increased from 1,000).

#### Method: `collect(since_date: str = None)`

1. Fetch recent bill actions:
```
GET /v3/bill?format=json&limit=250&offset=0&fromDateTime={since_date}T00:00:00Z
```

2. For each bill, fetch actions:
```
GET /v3/bill/{congress}/{billType}/{billNumber}/actions?format=json
```

3. **Filter for market-relevant actions only** — Do NOT store every `bill_introduced` event. Most bills die without action. Only store events matching these action types:

```python
MARKET_RELEVANT_ACTIONS = {
    "becameLaw": {"event_type": "bill_signed", "impact_base": 5},
    "passedHouse": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "passedSenate": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "passedAgreedToInHouse": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "passedAgreedToInSenate": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "reportedToHouse": {"event_type": "bill_passed_committee", "impact_base": 3},
    "reportedToSenate": {"event_type": "bill_passed_committee", "impact_base": 3},
    "hearingHeldBy": {"event_type": "hearing_scheduled", "impact_base": 2},
}
# Ignore: 'introduced*', 'referred*', 'receivedInThe*' — too noisy
```

4. Map bill to sectors via title keyword matching (reuse sector_mapper).
5. INSERT into `regulatory_events` table with `source = 'congress'`.
6. Run sector_mapper and impact_scorer on new events.

**Important:** Set `api_key` via `config.yaml` or `CONGRESS_API_KEY` env var. Pass as query param: `?api_key={key}`.

**Collection schedule:** Every 12 hours. Legislation moves slowly.

**Backfill:** Full current Congress (119th, 2025-2026) at minimum. Use pagination with offset.

---

### 3.2 Lobbying Disclosure Collector (`collectors/lobbying.py`)

**CRITICAL: Build against lda.gov, NOT lda.senate.gov.** The Senate LDA API sunsets June 30, 2026.

**API:** `https://lda.gov/api/v1/filings/`

**No API key required.** Returns paginated JSON.

#### Method: `collect(filing_year: int = None, filing_period: str = None)`

1. Query filings endpoint:
```
GET https://lda.gov/api/v1/filings/?filing_year=2026&filing_period=Q1&format=json
```

If `filing_year` is None, collect current year. If `filing_period` is None, collect all periods.

2. Paginate through results (follow `next` URL in response).

3. For each filing, extract:
   - `filing_uuid` → `filing_id`
   - `registrant.name` → `registrant_name`
   - `client.name` → `client_name`
   - Look up `client_ticker` from `company_contractor_map` + `watchlist` (fuzzy name match)
   - `income` or `expenses` → `amount` (depending on filing type)
   - `filing_year`, `filing_period`
   - `lobbying_activities[].specific_issues` → concatenate into `specific_issues`
   - `lobbying_activities[].government_entities` → concatenate into `government_entities`
   - `lobbying_activities[].lobbyists[].name` → concatenate into `lobbyists`

4. INSERT OR IGNORE on `filing_id`.

5. **Calculate QoQ spend changes** after collection:
```python
def calculate_qoq_changes(self) -> list[dict]:
    """
    For each client_ticker, compare current quarter spend to prior quarter.
    Flag companies with >25% QoQ increase.
    Returns list of {ticker, client_name, current_amount, prior_amount, pct_change}.
    """
```

**Collection schedule:** Quarterly (filings are quarterly, typically available ~45 days after quarter end). Run first of each month.

**Backfill:** Collect 2024 and 2025 to establish baseline.

**Important fallback:** If lda.gov API is not yet live or has issues, fall back to downloading bulk XML filings from the Senate Office of Public Records and parsing with BeautifulSoup/lxml. The XML format is well-documented.

---

### 3.3 Congressional Trading Collector (`collectors/congress_trades.py`)

**Downweighted per hypothesis validation.** Build a minimal collector — no sophisticated analysis, just data ingestion for the combined watchlist view.

#### Approach: Capitol Trades HTML Scraping (Free)

**URL:** `https://www.capitoltrades.com/trades`

Capitol Trades has no API. Scrape the publicly available trades page with BeautifulSoup.

```python
def collect(self) -> dict:
    """
    Scrape Capitol Trades recent trades page.
    Parse HTML table for: politician, party, chamber, ticker, trade_type,
    amount_range, trade_date, disclosure_date, asset_description.
    """
    url = "https://www.capitoltrades.com/trades"
    # Paginate: ?page=1, ?page=2, etc.
    # Parse <table> rows
    # INSERT OR IGNORE into congress_trades
```

**Rate limiting:** 1 request per 3 seconds. Maximum 10 pages per run. This is a public website; be respectful.

**Collection schedule:** Daily at 8am ET.

**Fallback:** If Capitol Trades blocks scraping, use Quiver Quantitative web API ($25/month) with `requests.get("https://api.quiverquant.com/beta/live/congresstrading", headers={"Authorization": f"Bearer {api_key}"})`. Only implement this fallback if the primary approach fails.

---

### 3.4 Regulations.gov Collector (`collectors/regulations_gov.py`)

**API:** `https://api.regulations.gov/v4/`

**Requires API key** — free from https://api.data.gov/signup/. Pass as `X-Api-Key` header.

**Rate limit:** 50 requests/min for comment endpoints; more flexible for document/docket endpoints.

#### Method: `collect(since_date: str = None)`

1. Search for recently posted documents:
```
GET /v4/documents?filter[postedDate][ge]={since_date}&filter[documentType]=Proposed Rule,Rule&sort=-postedDate&page[size]=25
```

2. For each document, extract:
   - `documentId` → `source_id`
   - `documentType` → map to `event_type` ('Proposed Rule' → 'proposed_rule', 'Rule' → 'final_rule')
   - `title`, `summary` (from `attributes`)
   - `agencyId` → `agency`
   - `postedDate` → `publication_date`
   - `commentEndDate` → `comment_deadline`
   - Construct URL: `https://www.regulations.gov/document/{documentId}`

3. INSERT into `regulatory_events` with `source = 'regulations_gov'`.
4. Run sector_mapper and impact_scorer.

**Value add over Federal Register:** Regulations.gov tracks comment periods and docket status more granularly. When a comment period closes, that's a signal the agency is moving toward a final rule.

**Collection schedule:** Every 12 hours.

---

## 4. Phase 4: Macro Overlay & Alerts

### 4.1 FRED Macro Data Collector (`collectors/fred_macro.py`)

**API:** `https://api.stlouisfed.org/fred/`

**Requires API key** — free from https://fred.stlouisfed.org/docs/api/api_key.html. Add as `fred_api_key` in `config.yaml`.

**Rate limit:** 120 requests/minute, 100,000 observations per request.

#### Core FRED Series to Collect

```python
FRED_SERIES = {
    # Growth indicators
    "GDPC1": {"name": "Real GDP", "frequency": "quarterly", "category": "growth"},
    "INDPRO": {"name": "Industrial Production", "frequency": "monthly", "category": "growth"},
    "PAYEMS": {"name": "Nonfarm Payrolls", "frequency": "monthly", "category": "growth"},
    "UNRATE": {"name": "Unemployment Rate", "frequency": "monthly", "category": "growth"},
    "ICSA": {"name": "Initial Jobless Claims", "frequency": "weekly", "category": "growth"},
    "RSXFS": {"name": "Retail Sales ex Food/Auto", "frequency": "monthly", "category": "growth"},

    # Inflation indicators
    "CPIAUCSL": {"name": "CPI All Urban", "frequency": "monthly", "category": "inflation"},
    "CPILFESL": {"name": "Core CPI (ex food/energy)", "frequency": "monthly", "category": "inflation"},
    "PCEPI": {"name": "PCE Price Index", "frequency": "monthly", "category": "inflation"},
    "T5YIE": {"name": "5Y Breakeven Inflation", "frequency": "daily", "category": "inflation"},

    # Interest rates & yield curve
    "DFF": {"name": "Fed Funds Rate", "frequency": "daily", "category": "rates"},
    "DGS2": {"name": "2Y Treasury Yield", "frequency": "daily", "category": "rates"},
    "DGS10": {"name": "10Y Treasury Yield", "frequency": "daily", "category": "rates"},
    "T10Y2Y": {"name": "10Y-2Y Spread", "frequency": "daily", "category": "rates"},
    "T10Y3M": {"name": "10Y-3M Spread", "frequency": "daily", "category": "rates"},

    # Financial conditions
    "VIXCLS": {"name": "VIX", "frequency": "daily", "category": "conditions"},
    "BAMLH0A0HYM2": {"name": "HY OAS Spread", "frequency": "daily", "category": "conditions"},
}
```

#### Method: `collect()`

1. For each series in `FRED_SERIES`, use `fredapi`:
```python
from fredapi import Fred
fred = Fred(api_key=config["api_keys"]["fred_api_key"])
data = fred.get_series(series_id, observation_start=since_date)
```

2. INSERT into `macro_indicators` table.

3. **Calculate rate-of-change columns** after raw data collection:
```python
def calculate_roc(self, series_id: str):
    """
    For each observation, compute:
    - rate_of_change_3m: annualized 3-month change
    - rate_of_change_6m: annualized 6-month change
    - rate_of_change_12m: year-over-year change
    UPDATE macro_indicators SET rate_of_change_* WHERE series_id = ?
    """
```

**Collection schedule:** Daily at 6am ET (before market open). Most FRED series update overnight.

---

### 4.2 Macro Regime Classifier (`analysis/macro_regime.py`)

Implements the Hedgeye-style four-quadrant growth/inflation model.

#### Quadrant Definitions

```python
QUADRANTS = {
    1: {"label": "Goldilocks", "description": "Growth accelerating, Inflation decelerating",
        "equity_bias": "long", "position_modifier": 1.2,
        "favored_sectors": ["XLK", "XLY"], "avoid_sectors": ["XLP", "XLU"]},
    2: {"label": "Reflation", "description": "Growth accelerating, Inflation accelerating",
        "equity_bias": "long_cautious", "position_modifier": 1.0,
        "favored_sectors": ["XLE", "XLB", "XLF"], "avoid_sectors": ["XLU"]},
    3: {"label": "Stagflation", "description": "Growth decelerating, Inflation accelerating",
        "equity_bias": "defensive", "position_modifier": 0.6,
        "favored_sectors": ["XLE", "XLP"], "avoid_sectors": ["XLK", "XLY", "XLF"]},
    4: {"label": "Deflation", "description": "Growth decelerating, Inflation decelerating",
        "equity_bias": "short_or_cash", "position_modifier": 0.4,
        "favored_sectors": ["XLU", "XLP"], "avoid_sectors": ["XLE", "XLI", "XLB"]},
}
```

#### Method: `classify_current_regime() -> dict`

1. Pull latest GDP growth 6-month RoC from `macro_indicators` (series GDPC1)
2. Pull latest CPI inflation 6-month RoC from `macro_indicators` (series CPIAUCSL)
3. Determine direction of each: compare current RoC to prior period RoC
   - Growth RoC increasing → growth accelerating
   - Growth RoC decreasing → growth decelerating
   - Inflation RoC increasing → inflation accelerating
   - Inflation RoC decreasing → inflation decelerating
4. Assign quadrant based on combination
5. Assign confidence:
   - `high`: both growth and inflation direction clear (>0.5% divergence from trend)
   - `medium`: one is clear, one is borderline
   - `low`: both are borderline or conflicting signals
6. INSERT into `macro_regimes` table
7. Return `{"quadrant": 1, "label": "Goldilocks", "confidence": "high", "position_modifier": 1.2, ...}`

#### Method: `get_regime_history(start_date: str) -> pd.DataFrame`

Query `macro_regimes` table for historical regime classifications. Used for backtesting.

#### Method: `backtest_regime_returns() -> dict`

For each historical quadrant period, calculate forward S&P 500 returns (3-month, 6-month). Compare across quadrants. Store in `event_studies` table with study_name = "macro_regime_returns".

---

### 4.3 FOMC Tracker (`collectors/fomc.py`)

#### Data Sources

**FOMC Meeting Dates:** Hardcode from Fed website (published a year in advance). Store in `config/fomc_dates.yaml`:

```yaml
fomc_meetings_2026:
  - date: "2026-01-29"
    type: "meeting"
  - date: "2026-03-18"
    type: "meeting"
  - date: "2026-03-19"
    type: "meeting"  # Two-day meeting
  - date: "2026-05-06"
    type: "meeting"
  - date: "2026-05-07"
    type: "meeting"
  - date: "2026-06-17"
    type: "meeting"
  - date: "2026-06-18"
    type: "meeting"
  - date: "2026-07-29"
    type: "meeting"
  - date: "2026-07-30"
    type: "meeting"
  - date: "2026-09-16"
    type: "meeting"
  - date: "2026-09-17"
    type: "meeting"
  - date: "2026-11-04"
    type: "meeting"
  - date: "2026-11-05"
    type: "meeting"
  - date: "2026-12-15"
    type: "meeting"
  - date: "2026-12-16"
    type: "meeting"
# Add historical dates for backtesting (2018-2025)
```

#### Method: `collect()`

1. Load FOMC dates from config
2. For past meetings, scrape the statement from `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` and meeting-specific pages
3. For each statement:
   a. Store full text in `statement_text`
   b. Diff with previous statement using `difflib.unified_diff`
   c. Store diff in `previous_statement_diff`
   d. Calculate hawkish/dovish score using keyword counting:

```python
HAWKISH_WORDS = [
    "inflation", "overheating", "tightening", "restrictive", "elevated",
    "price stability", "vigilant", "upside risks", "further increases",
    "reducing", "balance sheet reduction", "stronger than expected"
]
DOVISH_WORDS = [
    "accommodation", "supportive", "patient", "gradual", "downside risks",
    "monitoring", "data dependent", "slowing", "easing", "lower",
    "maximum employment", "below target", "moderate"
]

def score_hawkish_dovish(text: str) -> float:
    """Returns -1.0 (very dovish) to +1.0 (very hawkish)."""
    hawk_count = sum(1 for w in HAWKISH_WORDS if w.lower() in text.lower())
    dove_count = sum(1 for w in DOVISH_WORDS if w.lower() in text.lower())
    total = hawk_count + dove_count
    if total == 0:
        return 0.0
    return (hawk_count - dove_count) / total
```

   e. Pull SPY returns for announcement day and day after from `market_data`
   f. INSERT into `fomc_events`

**Collection schedule:** Run after each FOMC meeting (check config dates, run at 3pm ET on meeting days). Also run weekly to check for speeches/testimony.

---

### 4.4 Email Alert Engine (`analysis/alert_engine.py`)

Simple email alerts triggered after each collector run.

#### Alert Rules (from `config/config.yaml`)

```yaml
alerts:
  enabled: true
  email: "dan.mathis@leadershipnowproject.com"
  smtp_server: "smtp.gmail.com"
  smtp_port: 587
  smtp_user: ""          # Gmail address
  smtp_password: ""      # Gmail app password (not regular password)

  rules:
    - name: "High-Impact Regulatory Event"
      condition: "impact_score >= 4"
      table: "regulatory_events"
      lookback_hours: 24

    - name: "Large Contract Award (Watchlist)"
      condition: "award_amount >= 100000000 AND recipient_ticker IS NOT NULL"
      table: "contract_awards"
      lookback_hours: 24

    - name: "New Executive Order"
      condition: "event_type = 'executive_order'"
      table: "regulatory_events"
      lookback_hours: 24

    - name: "FDA AdCom Upcoming (7 days)"
      condition: "event_type = 'adcom_vote' AND event_date BETWEEN date('now') AND date('now', '+7 days') AND outcome = 'pending'"
      table: "fda_events"
      lookback_hours: 168  # 7 days

    - name: "Lobbying Spend Spike (>25% QoQ)"
      condition: "client_ticker IS NOT NULL"
      table: "lobbying_filings"
      custom_query: true
      # Custom query handled in code — compare current vs prior quarter

    - name: "Macro Regime Change"
      condition: "1=1"
      table: "macro_regimes"
      custom_query: true
      # Custom query — compare latest regime to prior
```

#### Method: `evaluate_and_send()`

1. For each rule in config:
   a. Build SQL: `SELECT * FROM {table} WHERE {condition} AND created_at >= datetime('now', '-{lookback_hours} hours')`
   b. If results > 0, format email and send
   c. For custom_query rules, call dedicated methods

2. **Email format:**

```
Subject: [Political Edge] {rule_name}: {event_title_or_summary}

{rule_name}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{Event details — varies by table}

Macro Regime: {current_quadrant_label} (position modifier: {modifier}x)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
View in dashboard: http://localhost:8501
```

3. **Send via SMTP:**
```python
import smtplib
from email.mime.text import MIMEText

def send_email(self, subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = self.config["alerts"]["smtp_user"]
    msg["To"] = self.config["alerts"]["email"]
    with smtplib.SMTP(self.config["alerts"]["smtp_server"], self.config["alerts"]["smtp_port"]) as server:
        server.starttls()
        server.login(self.config["alerts"]["smtp_user"], self.config["alerts"]["smtp_password"])
        server.send_message(msg)
```

---

## 5. Phase 5: Integration & Paper Trading

### 5.1 Signal Generator (`analysis/signal_generator.py`)

Combines outputs from all collectors and analysis modules to produce actionable trading signals.

#### Signal Types and Generation Rules

```python
SIGNAL_RULES = {
    "fda_catalyst": {
        "description": "Upcoming FDA AdCom vote or PDUFA date for a watchlist company",
        "source_table": "fda_events",
        "trigger": "event_type IN ('adcom_vote', 'pdufa_date') AND outcome = 'pending' AND ticker IS NOT NULL",
        "direction_logic": "long (if historical AdCom win rate for this drug class > 60%); watch (otherwise)",
        "conviction_boost": "+1 if firm is small-cap biotech (higher expected AR)",
        "conviction_reduce": "-1 if drug class has high failure rate",
    },
    "contract_momentum": {
        "description": "Watchlist company wins large government contract",
        "source_table": "contract_awards",
        "trigger": "award_amount >= 50000000 AND recipient_ticker IS NOT NULL AND award_date >= date('now', '-7 days')",
        "direction_logic": "long",
        "conviction_boost": "+1 if award > $500M; +1 if DOD contract",
        "conviction_reduce": "-1 if contract type = 'modification' (less novel)",
    },
    "regulatory_event": {
        "description": "High-impact regulatory event affecting watchlist sector",
        "source_table": "regulatory_events",
        "trigger": "impact_score >= 4 AND tickers IS NOT NULL AND publication_date >= date('now', '-3 days')",
        "direction_logic": "Depends on event — final rules that restrict = short affected companies; rules that subsidize/protect = long",
        "conviction_boost": "+1 if executive_order; +1 if matches multiple watchlist tickers",
        "conviction_reduce": "-1 if proposed_rule (may not be finalized)",
    },
    "lobbying_spike": {
        "description": "Company lobbying spend increased >25% QoQ AND a relevant regulatory event exists",
        "source_table": "lobbying_filings",
        "trigger": "Custom — requires QoQ comparison AND matching regulatory_event in same sector within 90 days",
        "direction_logic": "watch — lobbying spike alone is not directional; combine with regulatory event direction",
        "conviction_boost": "+1 if regulatory event is proposed_rule (company is trying to influence outcome)",
        "conviction_reduce": "None",
    },
    "macro_regime": {
        "description": "Macro regime change detected",
        "source_table": "macro_regimes",
        "trigger": "Latest quadrant differs from prior quadrant",
        "direction_logic": "Adjust position_size_modifier on all active signals per QUADRANTS config",
        "conviction_boost": "+1 if confidence = 'high'",
        "conviction_reduce": "-1 if confidence = 'low'",
    },
}
```

#### Method: `generate_signals() -> list[dict]`

1. For each signal rule, execute the trigger query.
2. For matches, determine direction and conviction.
3. Apply macro regime modifier to conviction (multiply `position_size_modifier`).
4. Check for duplicates — don't generate a signal if one already exists for same ticker + signal_type within 5 days.
5. INSERT into `trading_signals` with `status = 'pending'`.
6. Return list of new signals for alert system.

#### Method: `review_active_signals()`

1. Query `trading_signals WHERE status = 'active'`.
2. For each, check if exit conditions are met:
   - Holding days > max_holding_period (default 20 trading days)
   - PnL exceeds stop loss (-5%) or take profit (+15%)
   - The catalytic event has resolved (e.g., FDA vote happened, contract announced)
3. If exit condition met, update status to 'closed', calculate PnL.

---

### 5.2 Alpaca Paper Trading Integration (`execution/paper_trader.py`)

```
execution/
├── __init__.py
├── paper_trader.py          # Alpaca paper trading integration
└── position_sizer.py        # Position sizing with macro regime overlay
```

#### Prerequisites

- Alpaca account (free) with paper trading enabled
- API keys in `config.yaml`:
```yaml
api_keys:
  alpaca_key_id: ""         # APCA-API-KEY-ID
  alpaca_secret_key: ""     # APCA-API-SECRET-KEY
  alpaca_base_url: "https://paper-api.alpaca.markets"  # Paper trading URL
```

#### Class: `PaperTrader`

```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

class PaperTrader:
    """Manages paper trading execution via Alpaca API."""

    def __init__(self, config: dict):
        self.client = TradingClient(
            api_key=config["api_keys"]["alpaca_key_id"],
            secret_key=config["api_keys"]["alpaca_secret_key"],
            paper=True  # ALWAYS paper trading
        )
        self.db_path = config["database"]["path"]

    def get_account(self) -> dict:
        """Return account equity, buying power, positions."""
        account = self.client.get_account()
        return {
            "equity": float(account.equity),
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
        }

    def get_positions(self) -> list[dict]:
        """Return current open positions."""
        positions = self.client.get_all_positions()
        return [{"ticker": p.symbol, "qty": int(p.qty), "avg_entry": float(p.avg_entry_price),
                 "current_price": float(p.current_price), "pnl": float(p.unrealized_pl),
                 "pnl_pct": float(p.unrealized_plpc)} for p in positions]

    def execute_signal(self, signal: dict, account_equity: float) -> dict:
        """
        Execute a trading signal.

        Args:
            signal: dict from trading_signals table
            account_equity: current account equity for position sizing

        Returns:
            dict with order details
        """
        # 1. Calculate position size
        position_size = self._calculate_position_size(
            signal["conviction"],
            signal["position_size_modifier"],
            account_equity
        )

        # 2. Calculate shares
        current_price = self._get_current_price(signal["ticker"])
        shares = int(position_size / current_price)
        if shares < 1:
            return {"status": "skipped", "reason": "Position size too small"}

        # 3. Place order
        side = OrderSide.BUY if signal["direction"] == "long" else OrderSide.SELL
        order_request = MarketOrderRequest(
            symbol=signal["ticker"],
            qty=shares,
            side=side,
            time_in_force=TimeInForce.DAY
        )
        order = self.client.submit_order(order_request)

        # 4. Record in paper_trades table
        self._record_trade(signal["id"], order)

        # 5. Update signal status
        self._update_signal_status(signal["id"], "active", float(order.filled_avg_price), str(order.filled_at))

        return {"status": "filled", "order_id": order.id, "shares": shares, "price": float(order.filled_avg_price)}

    def _calculate_position_size(self, conviction: str, macro_modifier: float, equity: float) -> float:
        """
        Base allocation per trade:
          low conviction: 2% of equity
          medium conviction: 4% of equity
          high conviction: 6% of equity
        Then multiply by macro regime modifier (0.4 to 1.2).
        Max single position: 10% of equity.
        """
        base = {"low": 0.02, "medium": 0.04, "high": 0.06}
        allocation = equity * base.get(conviction, 0.04) * macro_modifier
        max_position = equity * 0.10
        return min(allocation, max_position)
```

**CRITICAL SAFETY RULES:**
- `paper=True` must ALWAYS be set in the TradingClient constructor. Never set to False in this codebase.
- Add a configuration safeguard: `config.yaml` must contain `live_trading_enabled: false`. The paper_trader should refuse to execute if this flag is true (that's for a future, separate module).
- Maximum single trade: 10% of portfolio equity.
- Maximum total exposure: 60% of portfolio equity (hold at least 40% cash).
- Daily trade limit: 10 trades per day.

---

### 5.3 Position Sizer (`execution/position_sizer.py`)

```python
class PositionSizer:
    """Calculates position sizes with macro regime overlay."""

    def __init__(self, db_path: str, max_single_position_pct: float = 0.10,
                 max_total_exposure_pct: float = 0.60):
        self.db_path = db_path
        self.max_single = max_single_position_pct
        self.max_total = max_total_exposure_pct

    def calculate(self, signal: dict, equity: float, current_exposure: float) -> float:
        """
        Returns dollar amount to allocate.

        Checks:
        1. Single position limit (10%)
        2. Total exposure limit (60%)
        3. Macro regime modifier
        4. Conviction level
        """
        if current_exposure >= self.max_total * equity:
            return 0.0  # Portfolio is fully allocated

        remaining_capacity = (self.max_total * equity) - current_exposure
        base = self._conviction_to_base(signal["conviction"])
        sized = equity * base * signal.get("position_size_modifier", 1.0)
        capped = min(sized, self.max_single * equity, remaining_capacity)
        return max(capped, 0.0)

    def _conviction_to_base(self, conviction: str) -> float:
        return {"low": 0.02, "medium": 0.04, "high": 0.06}.get(conviction, 0.04)
```

---

## 6. Dashboard Additions

### 6.1 New Page: FDA Catalyst Tracker (`dashboard/pages/4_FDA_Catalysts.py`)

**Layout:**

**Row 1: KPI Cards**
| Card | Value |
|------|-------|
| Upcoming AdCom Votes (next 90 days) | Count from fda_events WHERE outcome = 'pending' |
| Past AdCom Win Rate | % of past votes with positive outcome |
| Avg Positive CAR | Mean CAR for positive FDA events |
| Avg Negative CAR | Mean CAR for negative FDA events |

**Row 2: Upcoming Events Calendar**
Plotly Gantt chart or timeline showing upcoming FDA events (AdCom votes, PDUFA dates) for next 90 days. Color-coded by event type. Clickable to show detail.

**Row 3: Historical Event Study Results**
If event studies have been run (check `event_studies` table), show:
- Average CAR chart: x-axis = days relative to event (-5 to +10), y-axis = cumulative AR. Two lines: positive outcomes (green) and negative outcomes (red).
- Table of past events with their ARs.

**Row 4: Event Detail**
When event selected: drug name, company, indication, historical approval rate for that indication, prior AdCom outcomes for this drug, link to FDA calendar.

---

### 6.2 New Page: Macro & Fed Dashboard (`dashboard/pages/5_Macro.py`)

**Layout:**

**Row 1: Current Regime Card (large)**
Big display: current quadrant number, label, position modifier, confidence level, favored/avoid sectors. Color-coded (Q1=green, Q2=yellow, Q3=orange, Q4=red).

**Row 2: Key Indicators (4 columns)**
| GDP Growth RoC | CPI Inflation RoC | 10Y-2Y Spread | VIX |
|----------------|-------------------|---------------|-----|
| Sparkline + current value | Sparkline + current value | Sparkline + current value | Sparkline + current value |

**Row 3: Two charts**
Left: **Macro Regime History** — bar chart showing regime classification per month, color-coded, with S&P 500 overlaid as a line.
Right: **Yield Curve** — current yield curve (2Y, 5Y, 10Y, 30Y) plotted, with 1-year-ago curve for comparison.

**Row 4: FOMC Section**
- Next FOMC date and countdown
- Latest statement diff (highlighted additions in green, deletions in red)
- Hawkish/dovish score trend over last 8 meetings (bar chart)
- Table of recent FOMC events with rate decisions and SPX returns

---

### 6.3 New Page: Signals & Paper Trading (`dashboard/pages/6_Signals.py`)

**Layout:**

**Row 1: Portfolio Summary (from Alpaca)**
| Account Equity | Open PnL | Day PnL | Cash | Exposure % |
|Pulled from PaperTrader.get_account()|

**Row 2: Active Signals Table**
`st.data_editor` for interactive management:
- Columns: Date | Ticker | Signal Type | Direction | Conviction | Macro Modifier | Status | Entry Price | Current Price | PnL% | Holding Days | Actions
- Actions column: buttons for "Execute", "Skip", "Close"
- Color-code PnL (green positive, red negative)
- Filter by: status (pending, active, closed), signal type, ticker

**Row 3: Signal Performance Summary**
- Win rate by signal type (bar chart)
- Average PnL by signal type (bar chart)
- Cumulative PnL over time (line chart)
- Sharpe ratio by signal type

**Row 4: Recent Trades**
Table from `paper_trades`: Date | Ticker | Side | Qty | Price | PnL | Signal Type

---

### 6.4 Updated Page: Lobbying (`dashboard/pages/3_Lobbying.py`)

**Note:** This replaces the placeholder from Phase 1 (Phase 1 only built RegWatch, Contracts, and Watchlist).

**Layout:**

**Row 1: KPI Cards**
| Total Lobbying Spend (watchlist) | QoQ Change | Companies w/ >25% Spike | Avg Spend per Company |

**Row 2: Two charts**
Left: **Lobbying Spend Over Time** — line chart for selected companies, quarterly.
Right: **QoQ Change Heatmap** — companies vs. quarters, color-coded by spend change magnitude.

**Row 3: Filings Table**
Columns: Client | Ticker | Amount | Period | Specific Issues (truncated, expandable) | Government Entities | QoQ Change
Sortable. Highlight rows where QoQ change > 25%.

**Row 4: Cross-Reference**
When a company is selected: show regulatory events from the same period that match the lobbying issues text. This is the "lobbying spike + regulatory event" combined signal from the hypothesis validation.

---

### 6.5 Updated Page: Watchlist Combined View

Add these sections to the existing Watchlist per-ticker view:

**New Section: FDA Events** (between Regulatory Events and Contracts)
- Mini table: Date | Event Type | Drug | Outcome | AR
- Max 10 rows, last 180 days

**New Section: Lobbying Activity**
- Mini table: Period | Amount | QoQ Change | Specific Issues
- Max 4 rows (last 4 quarters)

**New Section: Active Signals**
- Mini table: Date | Signal Type | Direction | Conviction | Status | PnL
- Show all signals for this ticker, regardless of status

**New Section: Congressional Trades** (simplified — no standalone page)
- Mini table: Date | Politician | Party | Type | Amount Range
- Max 10 rows, last 180 days

---

## 7. Updated Scripts

### 7.1 Updated Collector Runner (`scripts/run_collectors.py`)

Add new collectors to the sequential runner:

```python
COLLECTOR_ORDER = [
    # Phase 1 (existing)
    ("Federal Register", FederalRegisterCollector),
    ("USASpending", USASpendingCollector),
    ("Market Data", MarketDataCollector),

    # Phase 2
    ("FDA Calendar", FDACalendarCollector),

    # Phase 3
    ("Congress.gov", CongressCollector),          # Requires API key
    ("Lobbying", LobbyingCollector),
    ("Congressional Trades", CongressTradesCollector),
    ("Regulations.gov", RegulationsGovCollector),  # Requires API key

    # Phase 4
    ("FRED Macro", FREDMacroCollector),           # Requires API key
    ("FOMC Tracker", FOMCCollector),
]

POST_COLLECTION = [
    ("Sector Mapper", SectorMapper.process_new_events),
    ("Impact Scorer", ImpactScorer.process_unscored_events),
    ("Macro Regime", MacroRegime.classify_current_regime),
    ("Signal Generator", SignalGenerator.generate_signals),
    ("Alert Engine", AlertEngine.evaluate_and_send),
    ("Signal Review", SignalGenerator.review_active_signals),
]
```

Each collector is wrapped in try/except — one failure does not stop the run. Skip collectors that don't have API keys configured.

### 7.2 Backtest Script (`scripts/run_backtests.py`)

```python
"""
Run all hypothesis backtests.
Usage:
    python scripts/run_backtests.py              # Run all
    python scripts/run_backtests.py --study fda   # Run specific study
    python scripts/run_backtests.py --list        # List available studies
"""
```

Calls `BacktestRunner.run_all()` or individual studies. Prints summary statistics. Saves results to `event_studies` table.

---

## 8. Updated Requirements

Add to `requirements.txt`:

```
# Phase 2
scipy>=1.11.0               # t-tests, statistical functions for event study
statsmodels>=0.14.0          # OLS regression for market model

# Phase 3
beautifulsoup4>=4.12.0       # Already in Phase 1, for HTML scraping
lxml>=4.9.0                  # Fast XML parser for lobbying filings

# Phase 4
fredapi>=0.5.0               # FRED economic data

# Phase 5
alpaca-py>=0.21.0            # Alpaca trading API (newer package, replaces alpaca-trade-api)
```

**Note:** `alpaca-py` is the current recommended package (replaces the deprecated `alpaca-trade-api`).

---

## 9. Implementation Plan — Phase by Phase

### Phase 2: Research Infrastructure (Weekends 1-2)

```
Step 1: Run migration script (scripts/migrate_phase2.py)
Step 2: Implement EventStudy class (analysis/event_study.py)
Step 3: Implement FDA calendar collector (collectors/fda_calendar.py)
  - Add pharma_companies.yaml config
  - Collect FDA events from existing Federal Register data
  - Scrape FDA.gov Advisory Committee Calendar
Step 4: Implement BacktestRunner (analysis/backtest_runner.py)
  - Add tariff_events.yaml config
Step 5: Run tariff sector rotation backtest (quickest to validate)
Step 6: Run contract awards backtest (uses existing USASpending data)
Step 7: Add FDA Catalysts dashboard page
Step 8: Validate — event study produces meaningful results, FDA events display correctly
```

**Validation criteria:**
- EventStudy.run() returns valid results with t-stats and p-values
- Tariff backtest shows sector dispersion consistent with documented evidence
- Contract awards backtest shows positive mean CAR for large DOD awards
- FDA events table populated with historical data
- FDA Catalysts dashboard page loads with data

### Phase 3: Remaining Data Collectors (Weekends 3-4)

```
Step 1: Implement Congress.gov collector (requires API key)
Step 2: Implement lobbying collector targeting lda.gov
Step 3: Implement congressional trades collector (Capitol Trades scraping)
Step 4: Implement Regulations.gov collector (requires API key)
Step 5: Build Lobbying dashboard page
Step 6: Update Watchlist page with new data sections
Step 7: Run backfill for all new collectors
Step 8: Validate — all collectors produce data, all pages render
```

**Validation criteria:**
- Congress.gov collector stores market-relevant bill actions
- Lobbying collector stores filings with QoQ change calculations
- Congressional trades display in Watchlist combined view
- Regulations.gov events appear in RegWatch feed
- All new dashboard pages load without errors

### Phase 4: Macro Overlay & Alerts (Weekends 5-6)

```
Step 1: Implement FRED macro data collector
Step 2: Implement macro regime classifier
Step 3: Implement FOMC tracker
Step 4: Implement email alert engine
Step 5: Build Macro & Fed dashboard page
Step 6: Run macro regime backtest
Step 7: Backfill FRED data (2018-present)
Step 8: Validate — regime classifier produces sensible quadrant, alerts send
```

**Validation criteria:**
- FRED data flows into macro_indicators with RoC calculations
- Regime classifier assigns current quadrant with confidence level
- FOMC events stored with statement diffs and hawkish/dovish scores
- Alert engine sends test email
- Macro dashboard displays current regime and FOMC info

### Phase 5: Integration & Paper Trading (Weekends 7-8)

```
Step 1: Implement signal generator
Step 2: Implement position sizer with macro overlay
Step 3: Implement Alpaca paper trading integration
Step 4: Build Signals & Paper Trading dashboard page
Step 5: Update run_collectors.py with full pipeline
Step 6: Run full pipeline end-to-end
Step 7: Generate initial signals from existing data
Step 8: Execute 2-3 paper trades to validate pipeline
Step 9: Final validation — full system smoke test
```

**Validation criteria:**
- Signal generator produces signals from existing data
- Position sizer correctly applies macro regime modifier
- Paper trades execute successfully on Alpaca
- Signals dashboard shows portfolio state and trade history
- Full `run_collectors.py` completes without errors in <10 minutes
- Email alert sends on high-impact event

---

## 10. Final Validation Checklist

After all phases complete, run this comprehensive check:

```bash
# Database integrity
sqlite3 data/political_edge.db ".tables"
# Expected: 14 tables (8 original + 6 new)

sqlite3 data/political_edge.db "SELECT COUNT(*) FROM regulatory_events"
# Expected: > 2,000

sqlite3 data/political_edge.db "SELECT COUNT(*) FROM fda_events"
# Expected: > 50

sqlite3 data/political_edge.db "SELECT COUNT(*) FROM macro_indicators"
# Expected: > 5,000

sqlite3 data/political_edge.db "SELECT COUNT(*) FROM event_studies"
# Expected: >= 3 (tariff, contract, FOMC at minimum)

sqlite3 data/political_edge.db "SELECT quadrant, quadrant_label FROM macro_regimes ORDER BY date DESC LIMIT 1"
# Expected: current regime classification

sqlite3 data/political_edge.db "SELECT COUNT(*) FROM trading_signals"
# Expected: > 0

# Dashboard runs
streamlit run dashboard/app.py
# Expected: All 6+ pages load, charts render, filters work

# Collectors work
python scripts/run_collectors.py
# Expected: All collectors complete (skip those without API keys)

# Backtest produces results
python scripts/run_backtests.py --list
# Expected: Lists available studies
python scripts/run_backtests.py --study tariff_sectors
# Expected: Prints CAR statistics, saves to DB

# Paper trading connection
python -c "from execution.paper_trader import PaperTrader; print('OK')"
# Expected: OK (even without API keys — import should work)
```

---

## 11. Technical Notes for Claude Code

### All Phase 1 Technical Notes Still Apply

Refer to `PRD-phase1-implementation.md` Section 16 for: type hints, docstrings, logging, parameterized SQL, no hardcoded paths, error handling, independent module runnability.

### Additional Phase 2-5 Notes

**Event study calculations must handle edge cases:**
- Ticker delisted between event and today → skip gracefully, log warning
- Market holiday falls within event window → use available trading days only
- Insufficient price data in estimation window → fall back to market-adjusted method
- Multiple events for same ticker within overlapping windows → flag as contaminated, exclude from aggregate stats (or use the first event only)

**FRED data has revisions:** GDP and employment data are revised after initial release. The `macro_indicators` table stores the value as of collection date. For backtesting, this introduces minor look-ahead bias that we accept (using revised data for past regimes). Note this limitation.

**Alpaca-py vs alpaca-trade-api:** Use `alpaca-py` (the newer SDK). Import paths are different:
```python
# New (correct)
from alpaca.trading.client import TradingClient
# Old (deprecated)
from alpaca_trade_api import REST
```

**BeautifulSoup scraping — be defensive:** Capitol Trades and FDA.gov may change their HTML structure. Wrap all parsing in try/except. Log warnings on parse failures. Include a last-known-good date in config so you know when scraping broke.

**Config additions summary — new keys to add to `config.yaml`:**
```yaml
api_keys:
  congress_gov: ""
  regulations_gov: ""
  fred_api_key: ""
  alpaca_key_id: ""
  alpaca_secret_key: ""
  alpaca_base_url: "https://paper-api.alpaca.markets"

live_trading_enabled: false    # SAFETY: must be false

collection:
  fda_calendar:
    request_delay_seconds: 2.0
  congress:
    request_delay_seconds: 1.0
  lobbying:
    request_delay_seconds: 1.0
  regulations_gov:
    request_delay_seconds: 1.5
  congress_trades:
    request_delay_seconds: 3.0
  fred:
    request_delay_seconds: 0.5

paper_trading:
  max_single_position_pct: 0.10
  max_total_exposure_pct: 0.60
  daily_trade_limit: 10
```

---

## 12. Legal Compliance Reminders (Updated)

All Phase 1 compliance notes remain in effect, plus:

- FDA advisory committee calendars are public information published by a federal agency
- Scraping Capitol Trades: this is a public website. Respect `robots.txt` and rate limits. Do not attempt to access premium/paywalled content.
- FOMC statements are public domain (published by the Federal Reserve)
- Alpaca paper trading is a simulation — no actual securities are traded
- When transitioning to live trading (future, not in this PRD): start with small capital, maintain trade journals, consult a tax professional for wash sale and pattern day trader rules
- Never use this system to trade on material nonpublic information
- The system produces signals for personal use only, not investment advice for others

---

## 13. What This PRD Does NOT Cover (Future Work)

Explicitly out of scope — document for later:

1. **Live trading** — this PRD covers paper trading only. Live execution requires a separate review.
2. **Earnings & Filings NLP (Area 2)** — EdgarTools integration, Loughran-McDonald sentiment, QoQ filing comparison. Build after Phases 2-5 are validated.
3. **State-level legislation** — LegiScan API integration. Build when a specific state-level thesis emerges.
4. **Claude API integration** — Deep regulatory event analysis via Claude API calls. Build when basic signals are validated.
5. **Cloud deployment** — Streamlit Cloud or VPS. Build when you need 24/7 access.
6. **Mobile alerts** — Telegram or Pushover. Build when email alerts are insufficient.
7. **Options strategies** — Interactive Brokers API for options execution. Build when equity paper trading is profitable.
