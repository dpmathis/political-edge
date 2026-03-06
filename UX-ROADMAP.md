# Political Edge UX Overhaul Roadmap

> From Data Firehose to Actionable Intelligence

## Current State: UX Audit Findings

### Critical Issues (Blocking Adoption)
- [ ] No onboarding or context for new users — homepage is a plain list of 15 page names
- [ ] Unexplained jargon everywhere ("Q1 Goldilocks", "Position Modifier: 1.2x", "CAR", "p-value")
- [ ] Raw markdown rendering in the UI — Today page shows literal `**asterisks**` instead of bold
- [ ] 187 active signals with no prioritization — no ranking, no "top 5 today"

### Major Issues (Hurting Usability)
- [ ] 15 pages with no hierarchy or grouping — flat sidebar list
- [ ] Data freshness badges are confusing — no reference for whether a date is stale or fresh
- [ ] Research page metrics columns are truncated ("Mean Cumulative Abnormal Return...")
- [ ] Watchlist confluence cards show "X" for missing factors with no explanation

### Minor Issues (Reducing Polish)
- [ ] Inconsistent page titles and subtitles
- [ ] Homepage bottom bar shows raw database table names (developer artifact)
- [ ] No empty state guidance when data is missing

---

## Phase 1: First Impressions (Weeks 1-3)

**Goal:** New user understands what Political Edge does and finds first actionable insight within 60 seconds.

### 1.1 Replace Homepage with Guided Dashboard
- **Priority:** P0 | **Effort:** Medium | **Impact:** High
- Replace list-of-pages with guided landing experience
- Single-sentence value prop: "Track how Washington moves markets"
- "What's happening now" section: top 3-5 highest-conviction signals with plain-English explanations
- "Market conditions" card: regime card with human-readable sentence (e.g., "Growth is up, inflation is down — historically good for tech stocks")
- Quick-links to three most useful pages for new users

### 1.2 Restructure Navigation into Groups
- **Priority:** P0 | **Effort:** Low | **Impact:** High
- Group sidebar pages into 4 sections using Streamlit section headers:
  - **Daily Briefing:** Today
  - **Market Intelligence:** Watchlist, Signals, Macro
  - **Data Feeds:** RegWatch, FDA, Lobbying, Congress Trades, Contracts, Prediction Markets, EO Tracker, Pipeline
  - **Advanced:** Research, Backtests, Settings

### 1.3 Fix Markdown Rendering Bugs
- **Priority:** P0 | **Effort:** Low | **Impact:** Medium
- Audit every page for raw markdown in UI
- Fix Today page briefing text showing literal asterisks
- Convert `st.info()`/`st.success()` calls with markdown to `st.markdown()` with proper HTML

### 1.4 Remove Developer Artifacts
- **Priority:** P1 | **Effort:** Low | **Impact:** Medium
- Remove horizontal database table name bar from homepage bottom
- Remove/rename developer-facing labels ("Generate Signals", "Reconcile Trades" buttons)
- Move data management controls to Settings page

### 1.5 Add "New to Political Edge?" Tooltip Layer
- **Priority:** P1 | **Effort:** Medium | **Impact:** High
- Persistent "?" icon in top-right toggles help overlay
- Hovering over any metric/chart/card shows plain-English tooltip
- Cheaper than redesigning every component; instant context for beginners

---

## Phase 2: Clarity & Context (Weeks 4-6)

**Goal:** Every number, chart, and card explains itself. No data point requires outside knowledge.

### 2.1 Build Glossary and Inline Definitions
- **Priority:** P0 | **Effort:** Medium | **Impact:** High
- Centralized glossary of all domain terms (confluence score, macro regime, CAR, p-value, PDUFA, impact score, position modifier, etc.)
- Clickable tooltips throughout — jargon terms underlined/marked with info icon
- 1-2 sentence plain-English definitions on click
- Macro page "What this means" pattern becomes standard for every page

### 2.2 Redesign Signal Cards with "So What?" Framing
- **Priority:** P0 | **Effort:** Medium | **Impact:** High
- Lead every signal card with plain-English narrative: "FDA likely to approve Keytruda expansion next week — Merck stock historically rises 3.2% on approval days"
- Move technical details (CAR, p-value, entry/stop/target) into expandable "Details" section
- Current cards show ticker/direction/conviction but force user to connect the dots

### 2.3 Fix Truncated Labels on Research Page
- **Priority:** P1 | **Effort:** Low | **Impact:** Medium
- Replace truncated headers with short labels + tooltips
- "Mean Cumulative Abnormal Return..." → "Avg. Return" (tooltip: full name)
- "How confident we are this isn't rando..." → "Confidence" (tooltip: p-value explanation)

### 2.4 Add "No Data" vs. "Negative Signal" Distinction
- **Priority:** P1 | **Effort:** Low | **Impact:** Medium
- Watchlist confluence cards: replace ambiguous "X" with distinct states
- Gray dash ("—") for "no data available"
- Red X for "active negative signal"
- Subtle label: "No data" vs. "Not aligned"

### 2.5 Humanize Data Freshness Indicators
- **Priority:** P2 | **Effort:** Low | **Impact:** Low
- Replace "data through 2026-03-05" with relative freshness
- Green dot: "Updated 4 hours ago"
- Yellow dot: "Updated 2 days ago"
- Red dot: "Stale — last updated 5 days ago"
- Info icon explaining update schedule per data source

---

## Phase 3: Guided Workflows (Weeks 7-9)

**Goal:** Clear, step-by-step paths from seeing a signal to understanding it and deciding whether to act.

### 3.1 Create "Top Signals Today" Ranked View
- **Priority:** P0 | **Effort:** Medium | **Impact:** High
- Replace flat 187-signal list with ranked view
- Top 5-10 signals sorted by combined conviction + confluence + time sensitivity
- Each card: rank number, plain-English narrative, "Dig deeper" link to watchlist deep-dive

### 3.2 Build Ticker Deep-Dive Flow
- **Priority:** P0 | **Effort:** High | **Impact:** High
- Clicking any ticker anywhere → unified deep-dive page with full story:
  - What's happening (active signals)
  - Why it matters (confluence evidence)
  - Macro context (regime alignment)
  - What research says (event study stats)
  - What to do (suggested position with entry/stop/target)
- Currently scattered across Watchlist, Signals, Research, RegWatch

### 3.3 Add Empty State Guidance
- **Priority:** P1 | **Effort:** Low | **Impact:** Medium
- Every empty section shows helpful message
- Portfolio $0: "No active positions yet. Signals appear on Today page when opportunities are detected."
- Same pattern for empty filters, empty date ranges, pages with no recent data

### 3.4 Simplify Macro Regime Display
- **Priority:** P1 | **Effort:** Medium | **Impact:** Medium
- Add visual 2x2 quadrant diagram showing current position
- Traffic-light summary ("Green: conditions favor stocks")
- Sentence about what changed since last week
- Replace "Position Modifier: 1.2x" with "Conditions suggest slightly larger positions than normal"

### 3.5 Cross-Link Related Data Across Pages
- **Priority:** P2 | **Effort:** Medium | **Impact:** Medium
- RegWatch event affecting defense → "See BA, LMT, NOC on Watchlist" link
- Signal on Signals page → link to backing research study
- Lobbying spike → link to related regulatory events
- Data is already connected in DB — just surface connections in UI

---

## Phase 4: Power & Polish (Weeks 10-12)

**Goal:** Daily-use tool with personalization, notifications, and responsive design.

### 4.1 User-Customizable Watchlist
- **Priority:** P1 | **Effort:** High | **Impact:** High
- Search-and-add interface to add/remove tickers without editing config.yaml
- Type ticker → match to sector/keywords → add to tracked set
- Persist in database or browser storage

### 4.2 Smart Alert Preferences
- **Priority:** P1 | **Effort:** High | **Impact:** High
- Preferences panel: signal types, minimum conviction, sectors of interest, notification channel
- In-app badge, email digest, or both
- Backend alert system already exists — expose to users

### 4.3 Responsive Layout for Tablet/Mobile
- **Priority:** P2 | **Effort:** High | **Impact:** Medium
- Custom CSS media queries for proper stacking on smaller screens
- Prioritize Today and Signals pages (highest-value daily use)

### 4.4 Consistent Design System
- **Priority:** P2 | **Effort:** Medium | **Impact:** Medium
- Standardize card styles, color usage (green=long/bullish, red=short/bearish, amber=watch)
- Consistent typography hierarchy, spacing, interaction patterns across all pages

### 4.5 Loading States and Performance
- **Priority:** P2 | **Effort:** Medium | **Impact:** Medium
- Skeleton loading states for all DB-querying pages
- Use `st.spinner()` or custom placeholders
- Add caching for expensive queries that don't change between page loads

---

## Success Metrics

| Metric | Current State | Target (Post-Phase 4) |
|--------|--------------|----------------------|
| Time to first insight | 5+ min (user must know where to look) | Under 60 seconds from homepage |
| Pages visited per session | 1-2 (users get lost and leave) | 3-5 (guided exploration) |
| Jargon comprehension | Requires finance background | Understandable by any investor |
| Signal-to-action rate | Unknown (no tracking) | Top 5 signals reviewed daily |
| Return visit rate | Low (one-time curiosity) | Daily use for market prep |

---

## Implementation Notes

### Technical Constraints
- All changes implementable within Streamlit (custom CSS via `st.markdown(unsafe_allow_html=True)`, native sidebar grouping, `help=` tooltips, expanders for progressive disclosure)
- Existing architecture (SQLite + Python + Plotly) supports all changes — no framework migration needed

### What NOT to Change
- Data collection pipeline, signal generation engine, research methodology — all strong
- Macro regime card and Watchlist confluence scoring — well-designed, just need better presentation
- 12-source data integration is the core competitive advantage — make this visible, don't hide it

### Prioritization
- **P0:** High-impact, complete first within each phase
- **P1:** Important, can slip to next phase if needed
- **P2:** Enhancements that improve quality but aren't blocking
- Each phase has at least one P0 item delivering standalone value — roadmap can pause after any phase
