# PRD: Political Edge — UI Redesign for Retail Trader Accessibility

**Author:** Dan Mathis | **Version:** 1.0 | **Date:** March 2026
**Status:** Draft for Review

---

## 1. Problem Statement

Political Edge contains an extraordinary breadth of political-regulatory trading intelligence — macro regime classification, FDA catalysts, executive order signals, FOMC tracking, lobbying cross-references, congressional trade disclosures, prediction market sentiment, and event-study-backed statistical evidence. The problem is that all of this is presented in a way that assumes the user already understands what they're looking at.

A retail trader with intermediate finance experience opens the app and is immediately confronted with raw data tables, acronyms (CAR, RoC, H/D Score, p-value), unexplained metrics (position size modifier, macro quadrant confidence), and fragmented signals spread across 9 separate pages with no clear guidance on what matters *right now* or *what to actually do*. The data is there, but the story it tells is invisible.

The cost of not solving this: users who would otherwise gain genuine alpha from political-regulatory intelligence will bounce, misinterpret signals, or fail to connect the dots between correlated data sources — the very thing that makes Political Edge unique.

---

## 2. Goals

| # | Goal | Success Metric |
|---|------|---------------|
| G1 | **Reduce time-to-first-actionable-insight from minutes to seconds** | New users identify a tradeable signal within 30 seconds of landing on the Today page |
| G2 | **Every data point is self-explanatory** | Zero instances of unexplained jargon — every metric has a tooltip or inline explanation visible on hover/tap |
| G3 | **Surface cross-source correlations automatically** | When a signal fires, the UI shows all supporting evidence from other data sources in a single view (not across 5 tabs) |
| G4 | **Make confidence levels and risk intuitive** | Users can correctly rank signals by risk/reward without reading documentation |
| G5 | **Preserve 100% of current data depth** — no data removed, only reorganized | Every current table, metric, and chart remains accessible (some moved from default view to expandable detail) |

---

## 3. Non-Goals

| Non-Goal | Why |
|----------|-----|
| Building a mobile app | Desktop-first for data density; responsive is fine, native mobile is a separate initiative |
| Automated trade execution UX | Paper trading integration already exists; redesigning the Alpaca execution flow is Phase 2 |
| Reducing the number of data sources | The breadth IS the product — we reorganize, not remove |
| AI-generated trade recommendations | Signals are evidence-based rules, not LLM opinions; keep it transparent |
| Real-time streaming data | Current batch-collection model works; real-time is an infrastructure decision, not a UI one |

---

## 4. Design Principles

Before diving into specific pages, these five principles should govern every UI decision:

### Principle 1: "Tell Me What to Do" Before "Show Me the Data"

Every page should open with an **actionable headline** — a plain-English summary of what the data means for the user's trading day — before showing any tables or charts. The current app opens every page with raw data tables. The redesign inverts this: narrative first, evidence second.

**Example — Current Today page:**
> Shows: `st.metric("Macro Regime", "Q1 Goldilocks")` and a raw signals table.

**Redesign:**
> Shows: "**Today's market backdrop is bullish.** Growth is accelerating while inflation cools (Goldilocks regime). Your signals favor long positions in Tech and Consumer Discretionary. There are 3 active signals, led by a high-conviction FDA catalyst for REGN. Below are the details."

### Principle 2: The "Signal Card" Is the Atomic Unit

Every tradeable signal should be presented as a self-contained **Signal Card** — a visual component that contains everything a trader needs: the ticker, direction (long/short/watch), conviction level, the *why* (rationale in plain English), supporting evidence from multiple sources, entry/exit levels, historical win rate, and a confidence gauge. No more hunting across tabs.

### Principle 3: Color = Conviction, Not Decoration

Establish a strict color language:
- **Green** = Long / Bullish / Positive performance
- **Red** = Short / Bearish / Negative performance
- **Amber/Yellow** = Watch / Caution / Low confidence
- **Blue** = Informational / Neutral
- **Purple** = Macro regime / Contextual overlay

Every chart, badge, and indicator follows this. Currently, colors are inconsistent (regime uses green/yellow/orange/red for quadrants, but signals use no color coding at all).

### Principle 4: Progressive Disclosure

Show the answer first, the evidence second, the raw data third. Use a three-layer pattern:

1. **Summary layer** — headline number, direction arrow, one-sentence interpretation
2. **Evidence layer** — charts, key supporting metrics, correlation highlights (expandable)
3. **Data layer** — full tables, raw event feeds, per-event drill-down (expandable)

The current app shows everything at layer 3 by default. Most users need layer 1.

### Principle 5: Cross-Source Correlation Is the Product

The single biggest missed opportunity is that the app siloes data by source (RegWatch, FDA, Lobbying, etc.) instead of by *thesis*. A retail trader doesn't think "let me check lobbying filings" — they think "should I buy LMT?" The Watchlist page attempts this but just stacks tables vertically with no synthesis.

The redesign introduces **Confluence Scores** — when multiple independent data sources point the same direction for a ticker (e.g., rising lobbying spend + favorable EO classification + contract win + macro regime favoring defense), the UI highlights this convergence prominently.

---

## 5. Page-by-Page Redesign Specifications

### 5.1 HOME / TODAY — "Your Trading Briefing"

**Current state:** Macro regime metrics, a raw signals table, three columns of upcoming catalysts, a list of high-impact events, prediction market data, EO signals table, and database row counts. Everything is equally weighted visually.

**Redesign:**

#### Section A: Daily Briefing Banner (NEW)
A full-width narrative banner at the top that synthesizes the day's outlook in 2-3 sentences of plain English. Generated from:
- Current macro regime + confidence
- Number and direction of active signals
- Any upcoming catalysts in the next 48 hours
- Any prediction market sentiment shifts

**Example output:**
> **March 2, 2026 — Bullish Tilt**
> We're in a Goldilocks regime (high confidence) — growth accelerating, inflation cooling. You have 3 active signals, all long-biased. REGN has a PDUFA date in 4 days. Prediction markets price a Fed hold at 87%. Defense tickers show lobbying spend surges alongside new contract awards.

This replaces the current pattern of showing `st.metric("Macro Regime", "Q1 Goldilocks")` + `st.metric("Confidence", "HIGH")` + `st.metric("Position Modifier", "1.2x")` as four separate tiles with no explanation.

#### Section B: Macro Regime Context Card (ENHANCED)
Keep the current quadrant display but add:
- **"What this means for you" tooltip**: e.g., "Goldilocks = lean into risk assets, increase position sizes by 20%. Tech and Consumer Discretionary tend to outperform."
- **Regime transition arrow**: Show which regime we came from and when, e.g., "Shifted from Q2 Reflation → Q1 Goldilocks on Feb 18"
- **Visual quadrant map**: A 2x2 grid showing Growth (x-axis) vs Inflation (y-axis) with a dot showing current position and recent trajectory
- **Inline VIX context**: Instead of just "VIX: 18.2", show "VIX: 18.2 (Low — markets complacent, typical for Goldilocks)"

#### Section C: Active Signal Cards (REDESIGNED)
Replace the current flat table with a card-based layout (max 5 cards visible, scrollable):

Each **Signal Card** contains:
```
┌──────────────────────────────────────────────────────┐
│  🟢 LONG  REGN           Conviction: ████░  HIGH    │
│  FDA Catalyst — PDUFA date in 4 days                 │
│                                                      │
│  "Dupixent label expansion PDUFA on Mar 6.           │
│   Historical FDA catalyst signals: 67% win rate,     │
│   avg +2.3% CAR over 5 days (N=42, p<0.05)."        │
│                                                      │
│  Entry: $812.40  |  Stop: $771.78  |  TP: $934.26   │
│  Position: 8% of equity  |  Horizon: 5 days          │
│                                                      │
│  Supporting Evidence:                                 │
│  ✓ Macro regime favorable (Q1 Goldilocks)            │
│  ✓ Lobbying spend up 34% QoQ (offensive posture)     │
│  ✓ Prediction mkt: 72% approval probability          │
│  ✗ No congressional trades detected                   │
│                                                      │
│  [View Details]  [Mark as Executed]  [Skip]          │
└──────────────────────────────────────────────────────┘
```

**Key changes from current:**
- Conviction is a visual bar, not just text
- Plain-English rationale explains *why* (currently just truncated text)
- Historical performance stats shown inline (currently hidden in a separate expander)
- Cross-source evidence checklist (NEW — pulls from lobbying, prediction markets, congress trades)
- Entry/Stop/TP shown visually as a price ladder, not a raw table column

#### Section D: Upcoming Catalysts Timeline (ENHANCED)
Replace the current three-column layout (FDA / FOMC / Comment Deadlines) with a unified **timeline view** — a horizontal timeline showing the next 14 days with markers for all catalyst types, color-coded by source:
- 🔵 FDA events (PDUFA, AdCom)
- 🟡 FOMC meetings
- 🔴 Regulatory comment deadlines
- 🟣 EO-related dates
- 🟢 Contract award deadlines

Each marker is clickable to expand into a detail card. This replaces the siloed three-column approach where catalysts from different sources are visually disconnected.

#### Section E: Prediction Market Sentiment (ENHANCED)
Keep current FOMC rate probabilities but add:
- **Trend arrows**: Show direction of probability change over last 7 days
- **Divergence alerts** (NEW): When prediction market pricing diverges from our signal direction, flag it: "⚠️ Our signal is LONG REGN, but prediction markets only price 38% approval — contrarian opportunity or a warning?"
- **Plain-English labels**: Instead of "Hold: 87.3%", show "Fed almost certainly holds rates (87%) — this is priced in, limited alpha from FOMC drift trade"

#### Section F: High-Impact Event Feed (STREAMLINED)
Replace the current flat list with a **severity-tiered feed**:
- Impact 5 events get a red banner with explanation
- Impact 4 events get a compact card
- Impact 3 and below are collapsed by default

Each event card includes a one-line "So what?" interpretation:
> **Impact 5 | Executive Order** — "Expanding tariffs on Chinese semiconductor equipment"
> **So what?** Short ASML, LRCX. Historical EO-tariff signals show -1.8% CAR over 3 days (N=15). Macro regime amplifies: Stagflation quadrant would make this worse.

---

### 5.2 REGWATCH — "Regulatory Event Intelligence"

**Current state:** KPI row, sector breakdown bar chart, a sortable event table, event detail panel with notes, and a price chart.

**Redesign:**

#### Key Changes:
1. **Add a "Regulatory Weather" summary** at the top: "This week: 12 new events, 3 high-impact. Defense sector seeing unusual activity (8 events vs. 3 avg). Healthcare quiet."

2. **Heatmap replaces bar chart**: Show a sector × week heatmap of event intensity, so users can spot surges at a glance. This surfaces the same pattern that `reg_shock_detector.py` detects, but visually.

3. **Event table gets traffic-light Impact column**: Replace "Impact: 4/5" with a colored dot + label:
   - 🔴 5 = Critical — Likely to move prices
   - 🟠 4 = High — Worth monitoring
   - 🟡 3 = Moderate — Background noise unless it's in your sector
   - ⚪ 2-1 = Low — Informational only

4. **Each event row shows "Affected Tickers" as clickable chips** that jump to the Watchlist view for that ticker.

5. **"Why does this matter?" auto-generated for each event** based on:
   - Event type (proposed rule = uncertainty, final rule = clarity)
   - Historical impact of similar events from the same agency
   - Current macro regime context (e.g., "New EPA regulation during Stagflation = headwind for energy")
   - Whether any watchlist tickers are affected

6. **Price overlay is auto-loaded** for the first affected ticker (currently requires manual selection).

---

### 5.3 FDA CATALYSTS — "Drug Approval Intelligence"

**Current state:** KPI row, event type breakdown, upcoming events table, event study results (statistical tables), CAR curve chart, per-event results table, and full FDA events table.

**Redesign:**

#### Key Changes:
1. **Calendar-first view**: Replace the upcoming events table with a visual calendar showing PDUFA dates and AdCom votes as event blocks. Each block shows: drug name, company, ticker, and a mini confidence gauge based on historical AdCom approval rates.

2. **Event study results get a "Trader's Summary"**:
   - Current: "Mean CAR: +2.31%, t-stat: 2.87, p-value: 0.012, Win Rate: 67%, Sharpe: 0.82"
   - Redesign: "When FDA advisory committees vote favorably, the stock moves +2.3% on average over the next 5 days. This has worked 67% of the time across 42 events. The statistical evidence is strong (p<0.05)."

3. **CAR curve chart gets annotations**:
   - Vertical line at Event Day already exists
   - Add: shaded confidence interval band
   - Add: annotation explaining what the chart means: "This shows how much the stock typically moves around the event date. The line going up after Day 0 means stocks tend to rise after favorable decisions."

4. **Binary outcome tracker for pending events**: Show a simple scorecard — "Of the last 10 AdCom votes we tracked, 7 were favorable. Our signals captured 5 of those correctly."

---

### 5.4 LOBBYING — "Follow the Money"

**Current state:** KPI row, top spenders bar chart, spending by period chart, filings table, and a cross-reference section.

**Redesign:**

#### Key Changes:
1. **"Lobbying Surge Alerts" at the top** (NEW): When a company's lobbying spend jumps >25% QoQ, highlight it with context:
   > "**LMT lobbying spend up 42% QoQ** — $4.2M → $5.96M. They're lobbying on 'next-gen fighter procurement' and 'defense budget authorization.' This coincides with 3 new DoD regulatory events. Historical lobbying spike + regulatory event = +1.4% avg return over 10 days."

2. **Cross-reference is the headline, not buried at the bottom**: The entire point of the lobbying page is the cross-reference between spending changes and regulatory events. Move the correlation view to the top. Show a matrix: companies on y-axis, data points on x-axis (lobbying QoQ change, recent regulatory events, contract awards, congress trades), with cells colored by direction.

3. **"Why companies lobby" explainer tooltip**: Many retail traders don't understand lobbying disclosures. Add a persistent info banner: "Companies are legally required to disclose lobbying activity. A sudden increase in lobbying spending often signals that a company expects imminent regulation that could affect its business — either as a threat to fight or an opportunity to shape."

---

### 5.5 WATCHLIST — "Ticker Deep Dive" (MAJOR REDESIGN)

**Current state:** Active tickers table, then a dropdown to select a ticker, followed by vertically stacked sections (price chart, regulatory events, FDA events, lobbying, congressional trades, trading signals) with no synthesis.

**Redesign — The Confluence View:**

This page becomes the **Confluence Dashboard** — the single most important redesign.

#### Section A: Ticker Selector with Confluence Scores
Instead of a plain dropdown, show a grid of ticker cards, each showing:
```
┌──────────────────────────┐
│  LMT  Lockheed Martin    │
│  Defense / Aerospace      │
│                          │
│  Confluence: ████░ 4/5   │
│  Direction: 🟢 LONG      │
│                          │
│  ✓ Macro favors sector   │
│  ✓ Lobbying spike +42%   │
│  ✓ Contract win $890M    │
│  ✓ Congress buying        │
│  ✗ No FDA catalyst        │
└──────────────────────────┘
```

**Confluence Score** = count of independent data sources pointing the same direction, weighted by conviction:
- Macro regime favors the sector (+1)
- Active trading signal exists (+1, +2 if high conviction)
- Lobbying spend trending up (+1)
- Recent favorable regulatory event (+1)
- Congressional insiders buying (+1)
- Prediction market supports thesis (+1)
- FDA catalyst upcoming (+1)

Score of 4+ = "Strong confluence — multiple independent signals align"
Score of 2-3 = "Moderate confluence — thesis has support but gaps"
Score of 0-1 = "Weak confluence — insufficient evidence"

#### Section B: Unified Thesis View
When a ticker is selected, instead of stacking raw tables, show a **thesis narrative**:

> **LMT Thesis: LONG (4/5 confluence)**
> "Lockheed Martin has strong multi-factor support. The macro regime (Goldilocks) favors growth equities and defense specifically. Lobbying spend surged 42% QoQ focused on next-gen fighter procurement — suggesting they expect favorable DoD budget action. A $890M contract win last week provides near-term revenue visibility. Two members of Congress (Sen. X, Rep. Y) purchased LMT shares in the last 60 days, both in the $1,001-$15,000 range. The main risk: no specific FDA catalysts (not applicable to this sector) and no prediction market contracts to confirm."

Below the narrative, show the supporting evidence in an **accordion layout** — each section expandable, all data preserved:

1. **Price & Technicals** — chart with event overlays (current functionality, enhanced with moving averages and benchmark comparison enabled by default)
2. **Regulatory Events** — table with "So what?" column
3. **Lobbying Activity** — QoQ trend with interpretation
4. **Congressional Trades** — table with political party context
5. **Trading Signals** — active + historical signals for this ticker
6. **FDA Events** — if applicable
7. **Prediction Markets** — any related contracts

---

### 5.6 MACRO & FED — "The Big Picture"

**Current state:** Regime card with HTML styling, key indicators with sparklines, regime history bar chart, yield curve data, FOMC tracker with hawkish/dovish chart, FOMC events table, and statement diff viewer.

**Redesign:**

#### Key Changes:
1. **Regime explainer**: The current Q1/Q2/Q3/Q4 quadrant labels (Goldilocks, Reflation, Stagflation, Deflation) are not self-explanatory. Add a persistent 2x2 grid visual:
   ```
                  Growth Accelerating
                        ↑
    Q4 Deflation    │   Q1 Goldilocks
    ────────────────┼────────────────→ Inflation
    Q3 Stagflation  │   Q2 Reflation    Accelerating

   ```
   With a pulsing dot showing current position and an arrow showing recent trajectory (e.g., "Moving from Q2 → Q1 over the last 3 months").

2. **"What this means for your portfolio" section**: Translate favored/avoid sectors from ETF tickers to plain English:
   - Current: "Favored: XLK, XLY" / "Avoid: XLP, XLU"
   - Redesign: "**Lean into:** Technology (XLK) and Consumer Discretionary (XLY) — these sectors historically outperform during Goldilocks. **Reduce exposure to:** Consumer Staples (XLP) and Utilities (XLU) — defensive sectors underperform when growth is strong."

3. **FOMC section gets a "What to expect" narrative**:
   > "Next meeting: March 18 (16 days away). Prediction markets price a 87% chance of hold. The historical pre-FOMC drift trade (+0.49% avg over 5 days) is approaching entry window. The last statement was slightly dovish (-0.15) — if this continues, rate-sensitive sectors (Financials, REITs) may benefit."

4. **Statement diff viewer gets a "Key Changes" summary**: Instead of requiring users to read raw diff output, extract and highlight the 2-3 most significant wording changes with interpretation.

---

### 5.7 SIGNALS — "Your Trading Command Center"

**Current state:** Portfolio summary (if Alpaca configured), signal generation controls, signals table with many columns, performance metrics, paper trades table.

**Redesign:**

#### Key Changes:
1. **Signal cards replace the table** for pending/active signals (same Signal Card component from the Today page).

2. **Performance dashboard redesign**:
   - Current: 4 metrics + 2 charts (avg PnL by type, cumulative PnL)
   - Add: **Signal Quality Report** — a breakdown showing which signal types are generating alpha and which aren't:
     ```
     Signal Type       | Win Rate | Avg PnL | N Trades | Verdict
     FDA Catalyst      | 67%      | +2.3%   | 12       | ✅ Keep using
     Contract Momentum | 55%      | +0.8%   | 8        | ⚠️ Marginal
     Regulatory Event  | 48%      | -0.2%   | 15       | ❌ Review rules
     EO Signal         | 71%      | +1.8%   | 7        | ✅ Keep using
     FOMC Drift        | 62%      | +0.5%   | 5        | ⚠️ Small sample
     ```

3. **Risk exposure summary** (NEW): Show current portfolio tilt by sector and direction:
   > "You are currently 72% long, concentrated in Defense (40%) and Healthcare (25%). Your macro regime suggests this is appropriate (Goldilocks favors risk-on), but concentration risk in Defense is elevated."

4. **Historical backtesting results accessible inline**: Currently, event study results are only on the FDA page. Surface backtesting evidence on the Signals page alongside each signal type's performance.

---

### 5.8 EO TRACKER — "Executive Order Intelligence"

**Current state:** KPI row, active signals in a flat layout, topic distribution bar chart, signal evidence table, regulatory shock alerts, EO timeline table, EO detail panel.

**Redesign:**

#### Key Changes:
1. **Impact pathway visualization** (NEW): When an EO is classified into a topic (e.g., "tariff/trade"), show the causal chain:
   ```
   EO: "Expanding tariffs on Chinese semiconductor equipment"
      → Topic: Tariff/Trade (HIGH confidence)
      → Historical impact: -1.8% CAR over 3 days
      → Affected tickers: ASML, LRCX, AMAT
      → Macro context: Q1 Goldilocks dampens impact (growth absorbs)
      → Net signal: SHORT, MEDIUM conviction
   ```

2. **Regulatory shock alerts get severity tiers**: The current `st.warning()` format treats all shocks equally. Add z-score magnitude visualization: a shock with z=4.2 should look dramatically different from z=2.1.

3. **"Evidence strength" indicator**: For each EO topic's expected CAR, show a confidence meter:
   - N>30 and p<0.05 = "Strong evidence" (green)
   - N>15 and p<0.10 = "Moderate evidence" (yellow)
   - N<15 or p>0.10 = "Weak evidence — trade with caution" (red)

---

### 5.9 SETTINGS — "Data Health & Control"

Settings page mostly serves power users and data maintenance. Keep it functional but add:

1. **Data freshness dashboard**: A visual grid showing each data source, when it was last updated, and whether it's stale (amber if >24h, red if >72h).

2. **One-click "Refresh All"**: Currently requires navigating to different collection buttons. Add a master refresh.

3. **Signal backtesting controls**: Move the backtest runner from CLI to the Settings page so users can evaluate signal performance without touching a terminal.

---

## 6. New Cross-Cutting Features

### 6.1 Confluence Score Engine

The most impactful new feature. For every watchlist ticker, compute a real-time Confluence Score by aggregating signals across all data sources:

**Inputs:**
| Source | Signal | Weight |
|--------|--------|--------|
| Macro Regime | Sector in "favored" list | +1 |
| Trading Signal | Active signal exists | +1 (medium), +2 (high conviction) |
| Lobbying | QoQ spend increase >15% | +1 |
| Regulatory Event | Impact ≥4 event in last 7 days | +1 (direction-adjusted) |
| Congressional Trades | Insider buy in last 90 days | +1 |
| Prediction Markets | Related market probability >60% | +1 |
| FDA Catalyst | Upcoming event in 30 days | +1 |
| Contract Awards | Award >$50M in last 30 days | +1 |

**Scoring:**
- Sum directional scores (long sources count +1, short sources count -1)
- Absolute value = strength; sign = direction
- 4+ = Strong confluence
- 2-3 = Moderate
- 0-1 = Weak

**Display:** Confluence badges appear on the Today page, Watchlist cards, and Signal cards.

### 6.2 Glossary & Tooltip System

Every technical term gets a hover tooltip. Build a centralized glossary that the tooltip system references:

| Term | Plain English |
|------|--------------|
| CAR (Cumulative Abnormal Return) | How much a stock moved beyond what the market did — isolates the signal's actual impact |
| p-value | How confident we are this isn't random luck. Below 0.05 = statistically significant |
| Win Rate | What percentage of the time this signal made money |
| Macro Regime | Which of 4 economic environments we're in, based on whether growth and inflation are speeding up or slowing down |
| Position Size Modifier | How much to scale your bet based on the current macro environment. 1.2x = slightly larger. 0.4x = much smaller |
| Conviction | How strongly the evidence supports this trade. Based on event type, historical performance, and macro context |
| VIX | The "fear gauge" — measures expected market volatility. Below 15 = calm, above 25 = anxious, above 35 = panic |
| Yield Curve Spread (10Y-2Y) | Difference between long and short-term interest rates. Negative = inverted = recession signal |
| PDUFA Date | FDA deadline to decide on a drug application. Stocks often move around these dates |
| AdCom Vote | FDA advisory committee meeting — positive vote usually means approval is likely |
| Hawkish/Dovish Score | How aggressive (+hawkish, wants higher rates) or accommodative (-dovish, wants lower rates) the Fed's language is |
| FOMC Drift | The historical tendency for SPY to rise ~0.5% in the 5 days before a Fed meeting |
| Regulatory Shock | An unusual surge in regulatory activity from a specific agency — detected by z-score analysis |
| QoQ | Quarter over Quarter — comparing this quarter to last quarter |

### 6.3 Correlation Alert System (NEW)

When multiple independent data sources align on the same ticker within a short time window, generate a **Correlation Alert**:

> **🔔 Confluence Alert: LMT**
> In the last 7 days, 4 independent sources have aligned on Lockheed Martin:
> 1. DoD contract award: $890M (Feb 27)
> 2. Lobbying spend spike: +42% QoQ (Feb filing)
> 3. Sen. Wicker (R-MS) purchased LMT shares (Feb 24)
> 4. Macro regime favors Defense sector (since Feb 18)
>
> Combined historical performance when 3+ sources align: +3.2% avg over 10 days (N=18, p=0.03)

This turns the app's greatest strength — multi-source intelligence — into a visible, actionable feature rather than something only a power user would assemble manually.

### 6.4 "What If" Scenario Tool (NEW)

Allow users to ask: "What happens to my watchlist if the macro regime shifts from Goldilocks to Stagflation?"

Show a table:
| Ticker | Current Signal | Regime-Shifted Signal | Impact |
|--------|---------------|----------------------|--------|
| LMT | LONG (High) | LONG (Medium) | Position modifier drops 1.2x → 0.6x |
| REGN | LONG (Medium) | LONG (Low) | Healthcare becomes avoid sector |
| XLE | WATCH | LONG (Medium) | Energy becomes favored in Stagflation |

This helps users understand how the macro overlay affects their entire book — currently invisible.

---

## 7. User Stories

### Retail Trader (Primary Persona)
- As a retail trader with intermediate experience, I want a plain-English summary of today's market outlook so that I understand the environment before reviewing any signals.
- As a retail trader, I want to see all supporting evidence for a signal in one card so that I don't have to navigate 5 different pages to evaluate a trade.
- As a retail trader, I want every abbreviation and metric explained on hover so that I never feel lost or confused by the data.
- As a retail trader, I want to see how many independent data sources agree on a ticker so that I can prioritize high-confluence trades.
- As a retail trader, I want a visual confidence gauge (not just "high/medium/low" text) so that I can instantly compare signal quality.
- As a retail trader, I want to understand the historical track record of each signal type so that I know which signals to trust most.

### Intermediate-Advanced Trader
- As an experienced trader, I want to still access the full raw data tables so that I can do my own analysis beyond the automated summaries.
- As an experienced trader, I want to see the p-values and sample sizes so that I can evaluate statistical significance myself.
- As an experienced trader, I want a "What If" scenario tool for regime changes so that I can stress-test my portfolio allocation.
- As an experienced trader, I want to see prediction market divergences flagged so that I can identify contrarian opportunities.

### Edge Cases
- As a user viewing the app with zero data collected, I want a guided setup flow so that I'm not confused by empty states.
- As a user checking the app on a weekend, I want to see the last trading day's context so that I can plan for Monday.
- As a user whose Alpaca paper trading isn't configured, I want the signals to still be fully useful without trade execution so that the intelligence layer stands on its own.

---

## 8. Requirements

### Must-Have (P0) — Ship cannot launch without these

| ID | Requirement | Acceptance Criteria |
|----|-------------|-------------------|
| P0-1 | **Daily Briefing Banner** on Today page | Given macro regime and active signals exist, when user opens Today page, then a 2-3 sentence plain-English summary is shown above all data |
| P0-2 | **Signal Card component** replacing signal tables | Given a trading signal exists, when displayed on Today or Signals page, then it shows: ticker, direction with color, conviction bar, plain-English rationale, entry/stop/TP, and historical stats |
| P0-3 | **Universal tooltip glossary** | Given any technical term appears on any page, when user hovers over it, then a plain-English definition appears. Minimum 20 terms covered |
| P0-4 | **Impact severity colors** on all event feeds | Given a regulatory event has an impact score, when displayed in any table, then it shows a colored indicator (red 5, orange 4, yellow 3, gray 1-2) |
| P0-5 | **Confluence Score** computed for each watchlist ticker | Given a ticker has data from 2+ sources, when displayed on Watchlist page, then a 0-5 score is shown with contributing factors listed |
| P0-6 | **"So what?" interpretation** for high-impact events | Given a regulatory event with impact ≥4, when displayed on RegWatch or Today page, then a one-line plain-English interpretation is shown |
| P0-7 | **Progressive disclosure** on all data-heavy pages | Given a page has a data table with >10 rows, then the summary/chart is visible by default and the full table is in an expandable section |
| P0-8 | **Consistent color language** across all pages | Given any directional indicator (long/short/watch), then green=long, red=short, amber=watch, across every page and component |

### Nice-to-Have (P1) — Significantly improves experience

| ID | Requirement |
|----|-------------|
| P1-1 | **Catalyst Timeline** view replacing the three-column layout on Today |
| P1-2 | **Thesis Narrative** auto-generated for selected ticker on Watchlist |
| P1-3 | **Signal Quality Report** table on Signals page |
| P1-4 | **Macro quadrant visual** with trajectory dot on Macro page |
| P1-5 | **Prediction Market divergence alerts** when our signal disagrees with market pricing |
| P1-6 | **Lobbying Surge Alerts** with cross-reference context at top of Lobbying page |
| P1-7 | **Sector heatmap** on RegWatch replacing bar chart |
| P1-8 | **FOMC "What to expect" narrative** on Macro page |

### Future Considerations (P2) — Design for these but don't build yet

| ID | Requirement |
|----|-------------|
| P2-1 | **"What If" scenario tool** for regime change stress testing |
| P2-2 | **Correlation Alert system** for automatic multi-source convergence detection |
| P2-3 | **Calendar-first FDA view** with approval probability gauges |
| P2-4 | **Statement diff AI summary** for FOMC language changes |
| P2-5 | **Portfolio risk heatmap** showing sector concentration and macro alignment |
| P2-6 | **Push notifications** for high-confluence signals and regime changes |
| P2-7 | **Interactive backtest sandbox** on Settings page |

---

## 9. Success Metrics

### Leading Indicators (1-4 weeks post-launch)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Time to first signal interaction | <30 seconds | Session recording / analytics |
| Tooltip usage rate | >40% of users hover at least one tooltip per session | Click/hover tracking |
| Signal card "View Details" click rate | >60% of visible cards get expanded | Event tracking |
| Watchlist Confluence Score views | >3 tickers checked per session | Page analytics |
| Bounce rate on Today page | <20% (users continue to at least one sub-page) | Session analytics |

### Lagging Indicators (1-3 months post-launch)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Weekly active users | 2x current baseline | DAU/WAU tracking |
| Average session duration | >5 minutes | Session analytics |
| Signal execution rate (paper trades) | >30% of pending signals get executed or explicitly skipped | Database query |
| User-reported understanding score | >4/5 on "I understand what this data means" survey | In-app survey |
| NPS / satisfaction | >40 NPS among active users | Survey |

---

## 10. Implementation Approach

### Streamlit Component Architecture

All redesigns are implementable within Streamlit's component model:

1. **Signal Card** → `st.container()` with `st.columns()` and custom CSS via `st.markdown(unsafe_allow_html=True)`
2. **Tooltips** → Custom CSS hover states via injected HTML, or `help=` parameter on `st.metric()`
3. **Confluence Score** → New Python function in `analysis/confluence.py` that queries all source tables and returns a score dict
4. **Daily Briefing** → Template string populated from macro regime + signal count + catalyst queries
5. **Color language** → CSS class system injected via `st.markdown()`
6. **Progressive disclosure** → `st.expander()` for detail layers (already used, just needs to become the default pattern)

### Suggested File Changes

| File | Changes |
|------|---------|
| `dashboard/components/signal_card.py` | NEW — Signal Card component |
| `dashboard/components/confluence.py` | NEW — Confluence Score widget |
| `dashboard/components/glossary.py` | NEW — Tooltip/glossary system |
| `dashboard/components/briefing.py` | NEW — Daily briefing generator |
| `analysis/confluence.py` | NEW — Confluence Score engine |
| `dashboard/pages/0_Today.py` | Major rewrite — add briefing, signal cards, timeline |
| `dashboard/pages/4_Watchlist.py` | Major rewrite — confluence-first view |
| `dashboard/pages/1_RegWatch.py` | Moderate — add heatmap, "so what?" column |
| `dashboard/pages/6_Signals.py` | Moderate — signal cards, quality report |
| `dashboard/pages/5_Macro.py` | Moderate — add narratives, quadrant visual |
| All other pages | Minor — color system, progressive disclosure, tooltips |

### Phasing Recommendation

**Sprint 1 (Week 1-2):** Signal Card component, color system, tooltip glossary (P0-2, P0-3, P0-4, P0-8) — these are reusable across all pages.

**Sprint 2 (Week 3-4):** Today page redesign with Daily Briefing, enhanced signal display, progressive disclosure (P0-1, P0-6, P0-7).

**Sprint 3 (Week 5-6):** Confluence Score engine and Watchlist redesign (P0-5) — the highest-value new feature.

**Sprint 4 (Week 7-8):** Remaining page enhancements (RegWatch, FDA, Macro, Lobbying, EO Tracker) + P1 items.

---

## 11. Open Questions

| # | Question | Owner | Blocking? |
|---|----------|-------|-----------|
| 1 | Should the Daily Briefing be AI-generated (LLM call) or template-based? Template is faster/cheaper but less natural. | Product + Engineering | Yes — affects architecture |
| 2 | What's the latency budget for Confluence Score calculation? Real-time on page load, or pre-computed during data collection? | Engineering | No — can ship with pre-computed and optimize later |
| 3 | Should Signal Cards support drag-and-drop reordering for user prioritization? | Design | No |
| 4 | Do we need user authentication to persist tooltip preferences (e.g., "don't show me tooltips after 30 days")? | Product | No |
| 5 | Should the "What If" scenario tool (P2) account for cross-sector correlations (e.g., if Defense is long and Tech is long, and regime shifts to favor only one)? | Research | No — P2 feature |
| 6 | What is the minimum data age at which the Daily Briefing should show a "stale data" warning? | Product | Yes — affects UX trust |
| 7 | Should Confluence Scores factor in the recency of each signal, or just existence? (A lobbying spike from 6 months ago is less meaningful than one from last week.) | Research | No — can iterate |

---

## 12. Appendix: Current vs. Redesigned Information Architecture

### Current (Source-Oriented)
```
Home
├── Today (flat signal table, raw catalyst columns, raw events list)
├── RegWatch (events by source: Federal Register)
├── FDA Catalysts (events by source: FDA)
├── Lobbying (events by source: lobbying disclosures)
├── Watchlist (ticker view, but just stacked raw tables)
├── Macro & Fed (regime data, FOMC data)
├── Signals (signal table, performance charts)
├── EO Tracker (executive orders by source)
└── Settings (data management)
```

### Redesigned (Action-Oriented)
```
Home
├── Today — "Your Daily Trading Briefing"
│   ├── Narrative summary (NEW)
│   ├── Signal Cards (REDESIGNED)
│   ├── Catalyst Timeline (REDESIGNED)
│   ├── Prediction Market Context (ENHANCED)
│   └── High-Impact Feed (STREAMLINED)
│
├── Watchlist — "Ticker Deep Dive with Confluence"  ← PRIMARY ANALYSIS VIEW
│   ├── Confluence Score grid (NEW)
│   ├── Thesis Narrative (NEW)
│   └── Accordion evidence panels (REORGANIZED)
│
├── Signals — "Your Trading Command Center"
│   ├── Signal Cards (REDESIGNED)
│   ├── Signal Quality Report (NEW)
│   ├── Risk Exposure Summary (NEW)
│   └── Performance Dashboard (ENHANCED)
│
├── Macro & Fed — "The Big Picture"
│   ├── Regime Explainer with visual quadrant (ENHANCED)
│   ├── "What this means" narrative (NEW)
│   ├── FOMC "What to expect" (NEW)
│   └── Full data tables (PRESERVED in expanders)
│
├── RegWatch — "Regulatory Intelligence"
│   ├── "Regulatory Weather" summary (NEW)
│   ├── Sector Heatmap (REDESIGNED)
│   ├── Events with "So what?" (ENHANCED)
│   └── Full event table (PRESERVED)
│
├── FDA Catalysts — "Drug Approval Intelligence"
│   ├── Calendar view (REDESIGNED)
│   ├── Event study trader's summary (ENHANCED)
│   └── Full data (PRESERVED)
│
├── Lobbying — "Follow the Money"
│   ├── Surge Alerts with cross-reference (NEW → PROMOTED)
│   ├── Spending trends (PRESERVED)
│   └── Filing details (PRESERVED)
│
├── EO Tracker — "Executive Order Intelligence"
│   ├── Impact pathway visualization (NEW)
│   ├── Shock severity tiers (ENHANCED)
│   └── EO timeline (PRESERVED)
│
└── Settings — "Data Health & Control"
    ├── Data freshness dashboard (ENHANCED)
    ├── One-click refresh (NEW)
    └── Backtest controls (NEW)
```

---

*This PRD preserves every data source and every piece of information currently in Political Edge. Nothing is removed — it is reorganized around the question every retail trader asks: "What should I trade today, and why?"*
