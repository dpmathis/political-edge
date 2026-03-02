# Political Edge: Research Report Proposals

## Data Audit Summary

Before proposing studies, a candid assessment of what the database *actually* supports.

**Statistically strong assets:**

- **58,634 regulatory events** (Jan 2024 – Feb 2026, 26 months). Federal Register (58,510) and Regulations.gov (124). Impact-scored 1–5, sector-tagged, ticker-mapped. ~8,063 events at impact ≥4. This is the platform's most valuable dataset.
- **827 executive orders** with full titles enabling topic classification. Clustered: sanctions/foreign (84), tariff/trade (51), defense (41), immigration (30), healthcare (28), technology (25), energy (22).
- **8,946 market data rows** across 13 watchlist tickers + SPY + 6 sector ETFs. Daily OHLCV, Jan 2024 – Feb 2026 for watchlist stocks; 2017–2025 for tariff-era ETFs.
- **7,160 macro indicator observations** across 17 FRED series (yields, CPI, GDP, VIX, employment, etc.) with pre-calculated 3m/6m/12m rates of change.
- **12 tariff events** (2018–2025) with sector ETF mappings — small N but high-impact, well-documented in academic literature.

**Data quality gaps (affect study design):**

- **FDA events (1,426 rows):** 98 adcom_vote records are *all empty* — no drug names, companies, tickers, or outcomes. The 1,182 fda_notice entries are similarly sparse. Zero abnormal returns calculated. This table needs enrichment before any FDA study is viable.
- **Congressional trades (180 rows):** All `trade_date` and `disclosure_date` fields are NULL. No temporal analysis is possible without dates.
- **Lobbying filings (909 rows):** Only 33 have dollar amounts (total: $917K). No client tickers mapped. Covers only 2025–2026.
- **Trading signals (129 rows):** All are `regulatory_event` type, all `pending` status, zero P&L recorded. No signal validation has been performed.
- **Event studies (2 completed):** `tariff_sectors` (N=23, mean CAR: -0.02%, not significant) and `high_impact_regulatory` (N=36, mean CAR: -6.4%, p<0.001 but negative, suggesting the current methodology has issues — duplicate events for same ticker/date inflate N while BA's -13% CAR dominates results).

---

## Proposed Research Reports

### Report 1: Regulatory Intensity Shocks and Sector Volatility — A Federal Register Event Study

**Research question:** Do abnormal surges in high-impact regulatory activity from specific agencies predict short-term sector volatility and abnormal returns for the affected sector's constituent stocks?

**Why this matters for trading:** If weekly spikes in EPA final rules reliably precede energy sector volatility, a trader can position with straddles or adjust sector exposure 1–3 days ahead. This transforms the Federal Register from a compliance tool into a leading indicator.

**Data requirements (all available):**

- 58,634 regulatory events aggregated to weekly agency-level counts
- Impact score ≥4 filter → ~8,063 high-impact events
- Agency-to-sector mapping via the existing `sector_keyword_map` (116 mappings)
- Daily market data for 13 watchlist stocks + SPY + sector ETFs
- VIX daily (814 observations) for realized vs. implied volatility comparison

**Methodology:**

1. Construct weekly "regulatory intensity" time series per agency-sector pair (e.g., EPA→Energy, FDA→Healthcare, FAA→Defense/Industrials)
2. Define "shock" as agency-sector weekly count >2σ above its rolling 8-week mean
3. Event study: Calculate 5-day cumulative abnormal returns for the relevant sector ETF and individual stocks following each shock
4. Control for macro regime (Goldilocks vs. Stagflation) and concurrent FOMC proximity
5. Granger causality test: Does regulatory intensity Granger-cause sector realized volatility at weekly frequency?
6. Out-of-sample test: Train on 2024, validate on 2025, test on 2026 YTD

**Expected sample size:** ~100–150 agency-sector shocks over 26 months (sufficient for parametric tests)

**Key hypothesis:** High-impact final rules from sector-relevant agencies generate measurable abnormal returns within a [-1, +5] day window, with the effect concentrated in the first 2 days and amplified during risk-off macro regimes.

**Novel contribution:** Most academic work treats the Federal Register as a monolithic output. This study disaggregates by agency, event type (final_rule vs. proposed_rule vs. notice), and impact score to isolate the signal-bearing subset.

---

### Report 2: Executive Order Market Impact — Topic-Conditional Abnormal Returns Across Sectors

**Research question:** Do executive orders generate statistically significant sector-level abnormal returns, and does the magnitude depend on the EO's topic classification and the prevailing macro regime?

**Why this matters for trading:** Executive orders are announced with minimal lead time. If tariff-related EOs reliably move industrials by 50–150bps within 48 hours, a trader with a real-time Federal Register feed can execute within minutes of publication.

**Data requirements (all available):**

- 827 executive orders with publication dates and full titles
- NLP-derived topic classification (already verified: tariff/trade=51, sanctions=84, defense=41, healthcare=28, energy=22, technology=25)
- Daily market data for sector ETFs (XLI, XLK, XLE, XLF, XLB, XLP) and SPY
- Macro regime classification at time of each EO
- 24 FOMC meeting dates for proximity control

**Methodology:**

1. Classify each EO into one of 8 topic clusters using keyword matching (already validated) and assign primary/secondary affected sector ETFs
2. Standard event study with [-1, +5] window, market-adjusted and market-model methods
3. Cross-sectional regression: CAR = f(topic, impact_breadth, macro_regime, FOMC_proximity, same_direction_streak)
4. Distinguish between EOs that *impose* restrictions (tariffs, sanctions, regulations) vs. those that *relieve* them (trade deals, deregulation) — test for asymmetry
5. Time-of-day analysis: EOs published pre-market vs. during trading hours vs. post-market
6. Subsample analysis by presidential administration (2024 EOs under Biden vs. 2025–2026 under Trump 2.0)

**Expected sample size:** 827 EOs total; ~250–350 with clear sector mappings. Per-topic N ranges from 22 (energy) to 84 (sanctions) — marginal for some subgroups but viable for the aggregate and top-3 topics.

**Key hypothesis:** Tariff/trade EOs produce the largest and most immediate sector ETF abnormal returns (50–200bps within 2 days), with imposition events showing larger magnitude than relief events (the "bad news travels faster" asymmetry documented in behavioral finance).

**Novel contribution:** The administration transition in Jan 2025 provides a natural experiment: policy regime change with observable structural break in EO topic distribution. The Feb 2025 drop in total regulatory volume (1,206 vs. 2,353 in Jan) suggests the incoming administration's regulatory freeze is itself a tradeable event.

---

### Report 3: The Regulatory Pipeline as a Sector Rotation Signal — From Proposed Rule to Final Rule

**Research question:** Does the lag structure between proposed rules and final rules from the same agency predict when sector-specific regulatory impact will materialize, and can this timeline be exploited for sector rotation?

**Why this matters for trading:** A proposed rule is a *forward-looking* signal with a known regulatory timeline (typically 60–180 day comment period before final rule). If the market underreacts to proposed rules and only prices the final rule, there's a systematic trading opportunity in the lead time.

**Data requirements (all available):**

- 3,611 proposed rules and 6,149 final rules with dates, agencies, and sectors
- Comment deadlines (`comment_deadline` field) for proposed rules — provides an expected final rule timeline
- Market data for sector ETFs and individual watchlist stocks
- Impact scores to weight economic significance

**Methodology:**

1. Match proposed rules to their corresponding final rules using agency + title keyword similarity + temporal proximity (proposed rule typically precedes final rule by 90–365 days)
2. Measure abnormal returns at three event windows: (a) proposed rule publication, (b) comment deadline, (c) final rule publication
3. Test the "information cascade" hypothesis: Does CAR at proposed rule publication predict the direction but underestimate the magnitude of CAR at final rule publication?
4. Build a "regulatory pipeline pressure" indicator: count of proposed rules from sector-relevant agencies that have passed their comment deadline but not yet issued a final rule. Test as a sector rotation signal.
5. Sector-specific analysis: EPA→Energy, FDA→Healthcare, FCC→Technology, DoD→Defense
6. Calendar effects: Year-end regulatory surges (Dec 2024 shows 3,218 events, highest month — the "midnight regulation" phenomenon)

**Expected sample size:** ~3,600 proposed rules with subset matchable to final rules. The pipeline indicator is a time-series signal tested monthly.

**Key hypothesis:** Markets incorporate ~40–60% of regulatory impact at the proposed rule stage, with the remainder priced at final rule publication. The pipeline indicator generates 100–200bps of monthly alpha in sector rotation strategies.

**Novel contribution:** This study treats the regulatory process as a *predictable timeline* rather than an event shock. The proposed→final rule lag is structural and observable, making it a rare example of a government-generated leading indicator with a known time horizon.

---

### Report 4: Tariff Announcement Asymmetry and Sector Dispersion — Imposition vs. Relief

**Research question:** Is there a measurable asymmetry in sector ETF responses to tariff imposition announcements versus tariff relief/deal announcements, and does the magnitude decay across successive announcements within an escalation cycle?

**Why this matters for trading:** The 2025–2026 tariff cycle (Liberation Day, 90-day pause, US-China Geneva deal, US-UK deal) provides fresh, out-of-sample data to validate or refute patterns observed in the 2018–2019 cycle. If asymmetry is systematic, traders can size positions differently for escalation vs. de-escalation.

**Data requirements (all available):**

- 12 tariff events (2018–2025) with sector ETF mappings from `tariff_events.yaml`
- Additional tariff-related EOs from the database (51 tariff/trade EOs identified via keyword scan, including "Ending Certain Tariff Actions" on Feb 25, 2026 and "Imposing a Temporary Import Surcharge" on the same date)
- Sector ETF daily prices: XLI (386 days), XLB (338), XLK (298), XLE (192), XLF (51), XLP (50)
- SPY (435 days) as benchmark
- VIX for volatility regime conditioning
- Macro regime classification for interaction effects

**Methodology:**

1. Expand the tariff event set from 12 to ~30–40 by incorporating the 51 tariff/trade EOs that are already in the database, classifying each as "imposition" (new tariff, rate increase, scope expansion) or "relief" (pause, reduction, deal, exemption)
2. Standard event study: [-1, +5] day window with sector ETF returns, market-adjusted
3. Test imposition vs. relief asymmetry using difference-in-means and Wilcoxon rank-sum
4. Measure "sector dispersion" as cross-sectional standard deviation of sector ETF returns on event days — test whether tariff events increase dispersion vs. non-tariff days
5. Escalation decay: Within the 2018–2019 cycle, test whether the Nth announcement produces smaller absolute returns than the (N-1)th (habituation effect)
6. Cross-cycle comparison: 2018–2019 China tariff war vs. 2025–2026 Liberation Day cycle
7. Conditional analysis: Do tariff announcements during rising-VIX environments produce larger sector dispersion?

**Expected sample size:** 30–40 events after expansion (12 original + ~20–25 tariff-related EOs). Small N but large effect sizes in the literature (5–17% sector dispersion per the existing event study hypothesis).

**Key hypothesis:** (1) Imposition announcements produce 1.5–2.0x larger absolute sector dispersion than relief announcements. (2) The escalation decay effect is present within cycles but resets across cycles. (3) The 2025–2026 cycle shows larger effects than 2018–2019 due to broader scope (Liberation Day affected all trading partners vs. China-specific in 2018).

**Novel contribution:** Most tariff research focuses on the 2018–2019 period. This study is among the first to include the 2025–2026 Liberation Day cycle as an out-of-sample test, with cross-cycle comparison.

---

### Report 5: Macro Regime-Conditional Government Signal Returns — When Does Political Edge Matter Most?

**Research question:** Does the prevailing macroeconomic regime (growth/inflation quadrant) modify the magnitude and direction of abnormal returns from government signals, and can a regime-conditional signal weighting scheme improve trading performance?

**Why this matters for trading:** If regulatory events only generate tradeable returns during Stagflation or Deflation regimes (when policy sensitivity is highest), then the entire Political Edge signal set should be scaled by a macro overlay — turning off during Goldilocks when markets are driven by fundamentals, and amplifying during stress regimes when government action is the marginal price-setter.

**Data requirements (all available):**

- Macro regime classification (currently 1 data point, but can be back-calculated for the full 2024–2026 period using the 7,160 macro indicator observations and the existing `classify_current_regime()` method in `macro_regime.py`)
- 58,634 regulatory events with impact scores and sector/ticker tags
- 827 executive orders
- 8,946 market data rows
- 17 FRED macro series with rates of change
- Yield curve data (T10Y2Y, T10Y3M) as real-time regime indicators
- VIX as volatility regime proxy

**Methodology:**

1. Back-fill macro regime classification to daily frequency using the GDP/CPI rate-of-change methodology already implemented (6-month RoC on GDPC1 and CPIAUCSL, interpolated to daily using the yield curve and VIX as high-frequency proxies)
2. Partition the full event study sample (from Reports 1–3) by macro regime at event date
3. Test whether mean CAR differs significantly across regimes using ANOVA and post-hoc Tukey HSD
4. Interaction model: CAR = β₁(event_impact) + β₂(macro_quadrant) + β₃(event_impact × macro_quadrant) + controls
5. Build a "regime-adjusted conviction score" that multiplies the raw signal conviction by the macro modifier (Q1=1.2x, Q2=1.0x, Q3=0.6x, Q4=0.4x — as already specified in the codebase) and test whether this improves Sharpe ratio vs. un-adjusted signals
6. Backtest: Simulate a trading strategy that receives all signals from Reports 1–4 but applies regime-conditional position sizing. Compare against equal-weighted baseline.
7. Robustness: Alternative regime definitions using yield curve inversion, VIX thresholds, and credit spreads (BAMLH0A0HYM2, 825 observations)

**Expected sample size:** Full event set from Reports 1–3 (~300–500 events) partitioned into 4 regimes. The backtest covers 24 months with daily rebalancing.

**Key hypothesis:** Government signals produce 2–3x larger absolute abnormal returns during Q3 (Stagflation) and Q4 (Deflation) regimes compared to Q1 (Goldilocks), because policy actions are more consequential for asset prices when the macroeconomic backdrop is fragile. The regime-adjusted conviction score improves out-of-sample Sharpe ratio by 30–50%.

**Novel contribution:** This study bridges two literatures — political risk premia and macro regime investing — that are typically studied independently. The Hedgeye-style quadrant framework is widely used by practitioners but rarely tested academically with government signal data.

---

## Recommended Execution Order

**Report 1 (Regulatory Intensity Shocks)** should be first — it uses the largest, cleanest dataset (58,634 events) and establishes the foundational methodology for subsequent studies.

**Report 4 (Tariff Asymmetry)** should be second — the 2025–2026 data is timely and the event set is well-defined, making it the fastest to produce with the highest immediate trading relevance.

**Report 2 (Executive Order Impact)** third — builds on Report 1's methodology but narrows to the EO subset with topic conditioning.

**Report 3 (Regulatory Pipeline)** fourth — the proposed→final rule matching requires NLP work but offers the most unique alpha source (a forward-looking signal with known time horizon).

**Report 5 (Macro Conditioning)** last — this is a meta-study that synthesizes findings from Reports 1–4 and adds the regime overlay. It requires the other studies' CAR estimates as inputs.

---

## Data Enrichment Required Before Execution

Before running these studies, the following data quality issues must be addressed:

1. **FDA events table:** All 98 adcom_vote records are empty shells (no drug names, companies, tickers, or outcomes). The FDA collector needs enrichment from ClinicalTrials.gov or FDA.gov advisory committee calendars to make any FDA-based study viable.

2. **Congressional trades:** All 180 records have NULL trade_date and disclosure_date. The Capitol Trades or Quiver Quantitative API would need to be integrated to populate temporal fields. Without dates, no event study is possible.

3. **Market data gaps:** Sector ETF coverage is thin (XLF has only 51 days, XLP only 50). Full 2024–2026 daily data should be backfilled for all sector ETFs used in the tariff and sector rotation studies.

4. **Event study deduplication:** The existing high_impact_regulatory study has duplicate entries (same ticker/date appearing 4–5 times due to multiple regulatory events mapping to the same stock on the same day). The event study framework needs a deduplication step before aggregating CARs.

5. **Macro regime backfill:** Currently only 1 regime classification exists. The back-calculation to daily frequency across the full sample period is a prerequisite for Report 5.
