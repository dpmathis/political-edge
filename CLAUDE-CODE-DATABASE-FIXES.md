# Claude Code: Database Asset Fixes

Priority-ordered instructions for fixing the five broken/degraded database assets in `data/political_edge.db`.

---

## Fix 1: Event Study Deduplication Bug (CRITICAL — blocks all research)

**Problem:** The `high_impact_regulatory` event study has duplicate entries. When a regulatory event maps to multiple tickers (e.g., an EPA rule tagged `XOM,NEE`), and multiple events land on the same date with the same tickers, the event study creates duplicate (date, ticker) pairs. Example: BA on 2024-01-02 appears 5 times with identical CARs, inflating N from ~15 true events to 36 and letting BA's -13% CAR dominate the mean.

**File to modify:** `analysis/event_study.py`

**Fix:**
```python
# In the EventStudy.run() method, after building the events list but before calculating CARs:
# Add deduplication step
events_df = pd.DataFrame(events)
events_df = events_df.drop_duplicates(subset=['event_date', 'ticker'], keep='first')
events = events_df.to_dict('records')
```

Also update `analysis/backtest_runner.py` — the `_build_events_from_regulatory()` method constructs events by splitting the comma-separated `tickers` field. When multiple regulatory events on the same day share tickers, duplicates are created. Add dedup there too:

```python
# In _build_events_from_regulatory(), after the events list is built:
seen = set()
unique_events = []
for e in events:
    key = (e['event_date'], e['ticker'])
    if key not in seen:
        seen.add(key)
        unique_events.append(e)
events = unique_events
```

**Validation:** After fix, re-run the `high_impact_regulatory` study and verify:
- N should be ~15–20 unique (date, ticker) pairs, not 36
- Mean CAR should change significantly (BA was over-weighted)
- Check that `event_study_results` table has no duplicate (study_id, event_date, ticker) rows

---

## Fix 2: Market Data Backfill for Sector ETFs (HIGH — blocks tariff and sector rotation studies)

**Problem:** Sector ETF coverage is severely incomplete:
- XLF: 51 days (needs ~580)
- XLP: 50 days (needs ~580)
- XLE: 192 days (needs ~580)
- XLK: 298 days (needs ~580)
- XLB: 338 days (needs ~580)
- SPY: 435 days but stops at 2025-07-18 (needs through 2026-02-27)

All sector ETFs should have full daily data from 2017-12-29 (tariff study start) through present.

**File to modify:** `collectors/market_data_collector.py` (or create a one-time backfill script)

**Fix — create `scripts/backfill_etf_data.py`:**
```python
import yfinance as yf
import sqlite3
from datetime import datetime

DB_PATH = 'data/political_edge.db'
ETFS = ['SPY', 'XLI', 'XLB', 'XLK', 'XLE', 'XLF', 'XLP']
START = '2017-01-01'
END = datetime.now().strftime('%Y-%m-%d')

conn = sqlite3.connect(DB_PATH)

for ticker in ETFS:
    # Check existing coverage
    existing = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM market_data WHERE ticker = ?",
        (ticker,)
    ).fetchone()
    print(f"{ticker}: existing {existing[2]} rows from {existing[0]} to {existing[1]}")

    # Fetch full history
    df = yf.download(ticker, start=START, end=END, auto_adjust=False)
    df = df.reset_index()

    # Delete existing and reinsert (cleanest approach for backfill)
    conn.execute("DELETE FROM market_data WHERE ticker = ?", (ticker,))

    for _, row in df.iterrows():
        conn.execute("""
            INSERT INTO market_data (ticker, date, open, high, low, close, adj_close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, row['Date'].strftime('%Y-%m-%d'),
              float(row['Open']), float(row['High']), float(row['Low']),
              float(row['Close']), float(row['Adj Close']), int(row['Volume'])))

    conn.commit()
    new_count = conn.execute(
        "SELECT COUNT(*) FROM market_data WHERE ticker = ?", (ticker,)
    ).fetchone()[0]
    print(f"  → Backfilled to {new_count} rows")

conn.close()
```

**Validation:** Each ETF should have ~2,000+ trading days from 2017 through present. Run:
```sql
SELECT ticker, COUNT(*), MIN(date), MAX(date)
FROM market_data
WHERE ticker IN ('SPY','XLI','XLB','XLK','XLE','XLF','XLP')
GROUP BY ticker;
```

---

## Fix 3: FDA Events Enrichment (HIGH — blocks FDA research track)

**Problem:** All 98 `adcom_vote` records are empty shells — `drug_name`, `company_name`, `ticker`, `outcome`, and `vote_result` are all NULL. The 1,182 `fda_notice` records are similarly sparse with only 18 total events having tickers.

**Root cause:** The FDA collector (`collectors/fda_collector.py`) is pulling calendar dates but not enriching with drug/company details.

**Fix — two-part approach:**

### Part A: Enrich existing adcom_vote records
Create `scripts/enrich_fda_events.py` that:
1. For each `adcom_vote` record with a date, query the FDA Advisory Committee Calendar API or scrape `https://www.fda.gov/advisory-committees/advisory-committee-calendar` to get the drug name, sponsor company, and indication
2. Map company names to tickers using the existing `company_contractor_map` table (extend it) or a pharma company lookup from `config/pharma_companies.yaml`
3. Look up the vote outcome from FDA press releases or meeting minutes
4. Calculate `abnormal_return` using the event study framework: `pre_event_price` (close on day -1), `post_event_price` (close on day +1), benchmark-adjusted

### Part B: Expand FDA data sources
Add to `collectors/fda_collector.py`:
- **openFDA API** (`https://api.fda.gov/drug/drugsfda.json`) — approval decisions with dates, application numbers, sponsor names
- **ClinicalTrials.gov API** — PDUFA dates, phase transitions, study completions
- Map all results to tickers using the pharma_companies.yaml lookup

### Part C: Calculate abnormal returns
After enrichment, run event studies on the FDA events:
```python
from analysis.event_study import EventStudy

fda_study = EventStudy(
    study_name='fda_adcom_enriched',
    hypothesis='FDA AdCom positive votes generate +5-21% CAR over [0,+5] days',
    benchmark='SPY',
    window_pre=1,
    window_post=5,
    method='market_adjusted'
)
# Build events from enriched fda_events table
events = [{'event_date': row.event_date, 'ticker': row.ticker}
          for row in enriched_adcoms if row.ticker]
results = fda_study.run(events)
results.save_to_db()
```

**Validation:**
- At least 50+ adcom_vote records should have non-NULL drug_name, company_name, and ticker
- abnormal_return should be calculated for all events with tickers and sufficient market data
- Event study should produce a significant result (academic literature shows +5-21% CAR for positive votes)

---

## Fix 4: Congressional Trades — Date Population (MEDIUM)

**Problem:** All 180 `congress_trades` records have NULL `trade_date` and `disclosure_date`. Without dates, no temporal analysis or event study is possible.

**Root cause:** The Congress trades collector is pulling STOCK Act filings but not parsing dates from the disclosure PDFs or API responses.

**Fix:**
1. Check the `raw_json` or `url` fields in existing records — dates may already be in the raw data but not extracted
2. If not, the primary data sources are:
   - **Capitol Trades API** (`https://www.capitoltrades.com/`) — has structured trade dates
   - **Quiver Quantitative API** (`https://api.quiverquant.com/`) — congressional trading with dates
   - **House/Senate financial disclosure websites** — parse the PDF filings directly
3. Update `collectors/congress_trades_collector.py` to extract and store `trade_date` and `disclosure_date`
4. Backfill existing 180 records by re-fetching from the source

**Validation:**
```sql
SELECT COUNT(*) FROM congress_trades WHERE trade_date IS NOT NULL;
-- Should be 180 (all records)
SELECT MIN(trade_date), MAX(trade_date) FROM congress_trades;
-- Should span at least 6-12 months
```

---

## Fix 5: Macro Regime Backfill (MEDIUM — blocks regime conditioning study)

**Problem:** Only 1 macro regime classification exists (2026-03-01, Goldilocks). The `classify_current_regime()` method in `analysis/macro_regime.py` only calculates the current regime, not historical.

**Fix — create `scripts/backfill_macro_regimes.py`:**
```python
import sqlite3
import pandas as pd
from analysis.macro_regime import MacroRegimeClassifier

DB_PATH = 'data/political_edge.db'
conn = sqlite3.connect(DB_PATH)

# Get GDP and CPI data
gdp = pd.read_sql("SELECT date, value, rate_of_change_6m FROM macro_indicators WHERE series_id = 'GDPC1' ORDER BY date", conn)
cpi = pd.read_sql("SELECT date, value, rate_of_change_6m FROM macro_indicators WHERE series_id = 'CPIAUCSL' ORDER BY date", conn)

# Get VIX and yield curve for daily interpolation
vix = pd.read_sql("SELECT date, value FROM macro_indicators WHERE series_id = 'VIXCLS' ORDER BY date", conn)
t10y2y = pd.read_sql("SELECT date, value FROM macro_indicators WHERE series_id = 'T10Y2Y' ORDER BY date", conn)

# GDP/CPI are quarterly/monthly — forward-fill to daily using last known value
# For each trading day, use the most recent GDP RoC and CPI RoC to classify
gdp['date'] = pd.to_datetime(gdp['date'])
cpi['date'] = pd.to_datetime(cpi['date'])

# Create daily date range
dates = pd.date_range('2024-01-01', '2026-02-28', freq='B')

# Forward-fill macro data to daily
gdp_daily = gdp.set_index('date')['rate_of_change_6m'].reindex(dates, method='ffill')
cpi_daily = cpi.set_index('date')['rate_of_change_6m'].reindex(dates, method='ffill')

# Classify each day
classifier = MacroRegimeClassifier()
conn.execute("DELETE FROM macro_regimes")  # Clear existing

for d in dates:
    if pd.isna(gdp_daily[d]) or pd.isna(cpi_daily[d]):
        continue
    growth = float(gdp_daily[d])
    inflation = float(cpi_daily[d])
    # Use the quadrant logic from macro_regime.py
    if growth > 0 and inflation <= 0:
        q, label = 1, 'Goldilocks'
    elif growth > 0 and inflation > 0:
        q, label = 2, 'Reflation'
    elif growth <= 0 and inflation > 0:
        q, label = 3, 'Stagflation'
    else:
        q, label = 4, 'Deflation'

    # Get VIX and yield curve for that date
    vix_val = vix[vix['date'] == d.strftime('%Y-%m-%d')]['value'].values
    t10y2y_val = t10y2y[t10y2y['date'] == d.strftime('%Y-%m-%d')]['value'].values

    modifier = {1: 1.2, 2: 1.0, 3: 0.6, 4: 0.4}[q]

    conn.execute("""INSERT INTO macro_regimes
        (date, growth_roc, inflation_roc, quadrant, quadrant_label,
         yield_curve_spread, vix, confidence, position_size_modifier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (d.strftime('%Y-%m-%d'), growth, inflation, q, label,
         float(t10y2y_val[0]) if len(t10y2y_val) else None,
         float(vix_val[0]) if len(vix_val) else None,
         'medium', modifier))

conn.commit()
print(f"Backfilled {conn.execute('SELECT COUNT(*) FROM macro_regimes').fetchone()[0]} regime days")

# Summary
for q in [1,2,3,4]:
    count = conn.execute("SELECT COUNT(*) FROM macro_regimes WHERE quadrant = ?", (q,)).fetchone()[0]
    label = conn.execute("SELECT quadrant_label FROM macro_regimes WHERE quadrant = ? LIMIT 1", (q,)).fetchone()
    print(f"  Q{q} ({label[0] if label else '?'}): {count} days")

conn.close()
```

**Validation:**
```sql
SELECT quadrant_label, COUNT(*), MIN(date), MAX(date)
FROM macro_regimes GROUP BY quadrant_label;
-- Should have ~500+ days across 2-3 regimes (Goldilocks likely dominant in 2024-2025)
```

---

## Execution Order

1. **Fix 1 (Dedup)** — 15 minutes. Unblocks all event studies.
2. **Fix 2 (ETF backfill)** — 30 minutes. Unblocks tariff and sector rotation studies.
3. **Fix 5 (Macro backfill)** — 30 minutes. Unblocks regime conditioning.
4. **Fix 3 (FDA enrichment)** — 2-4 hours. Complex but high-value.
5. **Fix 4 (Congress dates)** — 1-2 hours. Depends on API access.

Fixes 1, 2, and 5 can be done in parallel. Fix 3 is the most labor-intensive but unlocks the highest-alpha signal class (FDA adcom votes).
