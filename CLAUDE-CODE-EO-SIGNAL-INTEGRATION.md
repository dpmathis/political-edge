# Claude Code: Executive Order Signal Integration

## Overview

This document provides implementation instructions for integrating the Executive Order (EO) Market Impact signal into the Political Edge Streamlit app. The research (see `EO-Market-Impact-Research-Report.docx`) found statistically significant abnormal returns from three EO topic categories:

| Signal | CAR | p-value | Tickers | Hold |
|--------|-----|---------|---------|------|
| Tariff/Trade EO | +0.98% | 0.004 | XOM, BA, LMT, GOOGL | 3 days |
| Defense EO | +0.74% | 0.008 | LMT, RTX, GD, NOC, BA | 3 days |
| Sanctions EO | +0.88% | 0.033 | XOM, LMT | 3 days |
| DoD Regulatory Shock | +1.77% | 0.002 | RTX, LMT, GD, NOC, BA | 5 days |
| CMS Regulatory Shock | −6.57% | 0.011 | UNH, HUM | 5 days |

## Implementation Tasks

### Task 1: Add EO Topic Classifier (`analysis/eo_classifier.py`)

Create a new module that classifies executive orders by topic using keyword matching on titles.

```python
"""Executive Order topic classifier for trading signal generation."""

TOPIC_KEYWORDS = {
    'tariff_trade': ['tariff', 'trade', 'import', 'duty', 'customs', 'surcharge', 'de minimis'],
    'sanctions': ['sanction', 'russia', 'china', 'iran', 'venezuela', 'cuba', 'libya',
                  'assets control', 'foreign assets'],
    'defense': ['defense', 'national security', 'military', 'armed forces', 'defense production'],
    'energy': ['energy', 'coal', 'oil', 'gas', 'nuclear', 'renewable', 'petroleum',
               'phosphorus', 'clean coal'],
    'healthcare': ['health', 'drug', 'pharma', 'medicare', 'medicaid', 'fentanyl'],
    'technology': ['technolog', 'cyber', 'artificial intelligence', 'data', 'spectrum'],
}

TOPIC_TICKERS = {
    'tariff_trade': ['XOM', 'BA', 'LMT', 'GOOGL'],
    'sanctions': ['XOM', 'LMT'],
    'defense': ['LMT', 'RTX', 'GD', 'NOC', 'BA'],
    'energy': ['XOM', 'NEE'],
    'healthcare': ['UNH', 'HUM', 'PFE', 'LLY'],
    'technology': ['GOOGL', 'META'],
}

TOPIC_DIRECTION = {
    'tariff_trade': 'long',
    'sanctions': 'long',
    'defense': 'long',
    'energy': 'long',
    'healthcare': 'short',  # CMS/healthcare EOs tend to be negative for managed care
    'technology': 'long',
}

TOPIC_EXPECTED_CAR = {
    'tariff_trade': 0.0098,
    'defense': 0.0074,
    'sanctions': 0.0088,
    'energy': 0.0066,
    'healthcare': -0.0108,
    'technology': 0.0054,
}

TOPIC_CONFIDENCE = {
    'tariff_trade': 'high',    # p = 0.004
    'defense': 'high',         # p = 0.008
    'sanctions': 'medium',     # p = 0.033
    'energy': 'low',           # p = 0.379
    'healthcare': 'low',       # p = 0.205
    'technology': 'low',       # p = 0.375
}

IMPOSITION_KEYWORDS = ['imposing', 'increasing', 'surcharge', 'restricting', 'modifying duties']
RELIEF_KEYWORDS = ['ending', 'reducing', 'deal', 'pause', 'exemption', 'waiver', 'suspension']


def classify_eo(title: str) -> dict:
    """Classify an executive order by topic and return trading signal metadata.

    Args:
        title: The executive order title from the Federal Register.

    Returns:
        dict with keys: topic, tickers, direction, expected_car, confidence, is_tradeable
    """
    title_lower = title.lower()

    # Find primary topic
    topic = 'other'
    for t, keywords in TOPIC_KEYWORDS.items():
        if any(k in title_lower for k in keywords):
            topic = t
            break

    if topic == 'other':
        return {
            'topic': 'other',
            'tickers': [],
            'direction': None,
            'expected_car': None,
            'confidence': None,
            'is_tradeable': False,
            'tariff_direction': None,
        }

    # For tariff EOs, determine imposition vs relief
    tariff_dir = None
    if topic == 'tariff_trade':
        if any(k in title_lower for k in RELIEF_KEYWORDS):
            tariff_dir = 'relief'
        elif any(k in title_lower for k in IMPOSITION_KEYWORDS):
            tariff_dir = 'imposition'
        else:
            tariff_dir = 'neutral'

    return {
        'topic': topic,
        'tickers': TOPIC_TICKERS.get(topic, []),
        'direction': TOPIC_DIRECTION.get(topic, 'long'),
        'expected_car': TOPIC_EXPECTED_CAR.get(topic),
        'confidence': TOPIC_CONFIDENCE.get(topic, 'low'),
        'is_tradeable': TOPIC_CONFIDENCE.get(topic) in ('high', 'medium'),
        'tariff_direction': tariff_dir,
    }
```

### Task 2: Add Regulatory Shock Detector (`analysis/reg_shock_detector.py`)

Create a module that detects weekly regulatory intensity shocks per agency.

```python
"""Detect abnormal surges in regulatory activity by agency."""

import sqlite3
import pandas as pd
import numpy as np
from config import DB_PATH

AGENCY_TICKERS = {
    'Defense Department, Defense Acquisition Regulations System': {
        'tickers': ['LMT', 'RTX', 'GD', 'NOC', 'BA'],
        'direction': 'long',
        'expected_car': 0.0177,
        'confidence': 'high',
        'hold_days': 5,
    },
    'Health and Human Services Department, Centers for Medicare & Medicaid Services': {
        'tickers': ['UNH', 'HUM'],
        'direction': 'short',
        'expected_car': -0.0657,
        'confidence': 'high',
        'hold_days': 5,
    },
}

Z_THRESHOLD = 1.5
ROLLING_WINDOW = 8
MIN_WEEKS = 10


def detect_shocks(lookback_weeks: int = 1) -> list[dict]:
    """Detect regulatory intensity shocks in the most recent N weeks.

    Args:
        lookback_weeks: How many recent weeks to check for shocks.

    Returns:
        List of shock dicts with: agency, week_start, count, z_score, signal metadata
    """
    conn = sqlite3.connect(DB_PATH)

    reg_df = pd.read_sql("""
        SELECT publication_date, agency, impact_score
        FROM regulatory_events
        WHERE impact_score >= 4
        ORDER BY publication_date
    """, conn)
    conn.close()

    reg_df['date'] = pd.to_datetime(reg_df['publication_date'])
    reg_df['week_start'] = reg_df['date'] - pd.to_timedelta(reg_df['date'].dt.weekday, unit='D')

    shocks = []
    for agency, meta in AGENCY_TICKERS.items():
        weekly = reg_df[reg_df['agency'] == agency].groupby('week_start').size().reset_index(name='count')
        if len(weekly) < MIN_WEEKS:
            continue

        all_weeks = pd.date_range(weekly['week_start'].min(), weekly['week_start'].max(), freq='W-MON')
        weekly = weekly.set_index('week_start').reindex(all_weeks, fill_value=0).reset_index()
        weekly.columns = ['week_start', 'count']
        weekly = weekly.sort_values('week_start')

        weekly['rm'] = weekly['count'].rolling(ROLLING_WINDOW, min_periods=4).mean()
        weekly['rs'] = weekly['count'].rolling(ROLLING_WINDOW, min_periods=4).std()
        weekly['z'] = (weekly['count'] - weekly['rm']) / weekly['rs'].replace(0, np.nan)

        # Check only recent weeks
        recent = weekly.tail(lookback_weeks)
        for _, row in recent.iterrows():
            if pd.notna(row['z']) and row['z'] > Z_THRESHOLD:
                shocks.append({
                    'agency': agency,
                    'week_start': row['week_start'].strftime('%Y-%m-%d'),
                    'count': int(row['count']),
                    'z_score': round(float(row['z']), 2),
                    'tickers': meta['tickers'],
                    'direction': meta['direction'],
                    'expected_car': meta['expected_car'],
                    'confidence': meta['confidence'],
                    'hold_days': meta['hold_days'],
                })

    return shocks
```

### Task 3: Integrate into Signal Generator (`analysis/signal_generator.py`)

Add two new signal rules to the existing `SignalGenerator` class:

```python
# Add these imports at the top of signal_generator.py
from analysis.eo_classifier import classify_eo
from analysis.reg_shock_detector import detect_shocks

# Add these methods to the SignalGenerator class:

def _check_eo_signals(self) -> list[dict]:
    """Generate signals from new executive orders."""
    signals = []
    conn = sqlite3.connect(self.db_path)

    # Get EOs from the last 2 days that haven't been signaled yet
    recent_eos = pd.read_sql("""
        SELECT id, publication_date, title
        FROM regulatory_events
        WHERE event_type = 'executive_order'
        AND publication_date >= date('now', '-2 days')
        ORDER BY publication_date DESC
    """, conn)

    for _, eo in recent_eos.iterrows():
        classification = classify_eo(eo['title'])

        if not classification['is_tradeable']:
            continue

        for ticker in classification['tickers']:
            if self._has_recent_signal(ticker, 'eo_' + classification['topic'], days=3):
                continue

            signals.append({
                'signal_date': eo['publication_date'],
                'ticker': ticker,
                'signal_type': 'eo_' + classification['topic'],
                'direction': classification['direction'],
                'conviction': classification['confidence'],
                'source_event_id': eo['id'],
                'source_table': 'regulatory_events',
                'rationale': (
                    f"Executive Order: {eo['title'][:100]}. "
                    f"Topic: {classification['topic']}. "
                    f"Expected CAR: {classification['expected_car']:.2%} over 3 days. "
                    f"Based on event study of {147 if classification['topic'] == 'tariff_trade' else 158 if classification['topic'] == 'defense' else 57} observations."
                ),
                'holding_days': 3,
            })

    conn.close()
    return signals


def _check_reg_shock_signals(self) -> list[dict]:
    """Generate signals from regulatory intensity shocks."""
    shocks = detect_shocks(lookback_weeks=1)
    signals = []

    for shock in shocks:
        for ticker in shock['tickers']:
            if self._has_recent_signal(ticker, 'reg_shock', days=7):
                continue

            signals.append({
                'signal_date': shock['week_start'],
                'ticker': ticker,
                'signal_type': 'reg_shock',
                'direction': shock['direction'],
                'conviction': shock['confidence'],
                'source_event_id': None,
                'source_table': 'regulatory_events',
                'rationale': (
                    f"Regulatory intensity shock from {shock['agency'][:60]}. "
                    f"Weekly count: {shock['count']} (z-score: {shock['z_score']:.1f}). "
                    f"Expected CAR: {shock['expected_car']:.2%} over {shock['hold_days']} days."
                ),
                'holding_days': shock['hold_days'],
            })

    return signals
```

Then update the `generate_signals()` method to call both:

```python
def generate_signals(self):
    """Generate all trading signals."""
    all_signals = []
    all_signals.extend(self._check_fda_signals())
    all_signals.extend(self._check_contract_signals())
    all_signals.extend(self._check_regulatory_signals())
    all_signals.extend(self._check_lobbying_signals())
    all_signals.extend(self._check_eo_signals())          # NEW
    all_signals.extend(self._check_reg_shock_signals())    # NEW
    # ... rest of method
```

### Task 4: Create New Dashboard Page (`dashboard/pages/7_EO_Tracker.py`)

Create a dedicated EO Tracker page in the Streamlit dashboard:

```python
"""Executive Order Tracker — Topic-classified EOs with real-time signal generation."""

import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from config import DB_PATH
from analysis.eo_classifier import classify_eo, TOPIC_EXPECTED_CAR, TOPIC_CONFIDENCE

st.set_page_config(page_title="EO Tracker", page_icon="📜", layout="wide")
st.title("📜 Executive Order Tracker")
st.caption("Real-time topic classification with evidence-based trading signals")

conn = sqlite3.connect(DB_PATH)

# ---- Load and classify EOs ----
eos = pd.read_sql("""
    SELECT id, publication_date, title, url
    FROM regulatory_events
    WHERE event_type = 'executive_order'
    ORDER BY publication_date DESC
""", conn)

# Classify each EO
classifications = eos['title'].apply(classify_eo).apply(pd.Series)
eos = pd.concat([eos, classifications], axis=1)

# ---- KPI Row ----
col1, col2, col3, col4 = st.columns(4)

tradeable = eos[eos['is_tradeable']]
recent_7d = eos[pd.to_datetime(eos['publication_date']) >= pd.Timestamp.now() - pd.Timedelta(days=7)]
recent_tradeable = recent_7d[recent_7d['is_tradeable']]

col1.metric("Total EOs", len(eos))
col2.metric("Tradeable Signals", len(tradeable), f"+{len(recent_tradeable)} this week")
col3.metric("Last 7 Days", len(recent_7d))
col4.metric("Top Signal",
    recent_tradeable.iloc[0]['topic'].replace('_', '/').title() if len(recent_tradeable) > 0 else "None")

# ---- Signal Alert Box ----
if len(recent_tradeable) > 0:
    st.markdown("---")
    st.subheader("🚨 Active Signals")
    for _, row in recent_tradeable.head(5).iterrows():
        direction_emoji = "🟢 LONG" if row['direction'] == 'long' else "🔴 SHORT"
        confidence_color = "green" if row['confidence'] == 'high' else "orange"
        st.markdown(f"""
        **{row['publication_date']}** — {direction_emoji} {', '.join(row['tickers'])}
        | Confidence: :{confidence_color}[{row['confidence'].upper()}]
        | Expected CAR: {row['expected_car']:.2%} | Topic: {row['topic'].replace('_', ' ').title()}

        > {row['title'][:120]}
        """)

# ---- Topic Distribution Chart ----
st.markdown("---")
col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("EO Topic Distribution")
    topic_counts = eos['topic'].value_counts().reset_index()
    topic_counts.columns = ['topic', 'count']
    # Exclude 'other' for clarity
    topic_counts = topic_counts[topic_counts['topic'] != 'other']
    fig = px.bar(topic_counts, x='topic', y='count',
                 color='count', color_continuous_scale='Blues',
                 labels={'topic': 'Topic', 'count': 'Count'})
    fig.update_layout(showlegend=False, height=350)
    st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.subheader("Signal Evidence Summary")
    evidence_data = []
    for topic, car in TOPIC_EXPECTED_CAR.items():
        evidence_data.append({
            'Topic': topic.replace('_', ' ').title(),
            'Expected CAR': f"{car:+.2%}",
            'Confidence': TOPIC_CONFIDENCE.get(topic, 'low').upper(),
            'Tradeable': '✅' if TOPIC_CONFIDENCE.get(topic) in ('high', 'medium') else '❌',
        })
    st.dataframe(pd.DataFrame(evidence_data), use_container_width=True, hide_index=True)

# ---- EO Timeline ----
st.markdown("---")
st.subheader("Executive Order Timeline")

# Filter controls
topic_filter = st.multiselect("Filter by topic",
    options=[t for t in eos['topic'].unique() if t != 'other'],
    default=[t for t in eos['topic'].unique() if t != 'other'])

tradeable_only = st.checkbox("Show tradeable signals only", value=True)

filtered = eos[eos['topic'].isin(topic_filter)] if topic_filter else eos[eos['topic'] != 'other']
if tradeable_only:
    filtered = filtered[filtered['is_tradeable']]

# Display table
display_cols = ['publication_date', 'topic', 'direction', 'confidence', 'title']
st.dataframe(
    filtered[display_cols].rename(columns={
        'publication_date': 'Date',
        'topic': 'Topic',
        'direction': 'Direction',
        'confidence': 'Confidence',
        'title': 'Title'
    }),
    use_container_width=True,
    hide_index=True,
    height=400
)

# ---- EO Detail Expander ----
st.markdown("---")
st.subheader("EO Signal Detail")
selected_eo = st.selectbox("Select an Executive Order",
    filtered['title'].head(20).tolist(),
    format_func=lambda x: x[:100])

if selected_eo:
    eo_row = filtered[filtered['title'] == selected_eo].iloc[0]
    st.markdown(f"**Published:** {eo_row['publication_date']}")
    st.markdown(f"**Topic:** {eo_row['topic'].replace('_', ' ').title()}")
    st.markdown(f"**Direction:** {'🟢 LONG' if eo_row['direction'] == 'long' else '🔴 SHORT' if eo_row['direction'] == 'short' else '⚪ N/A'}")
    st.markdown(f"**Confidence:** {eo_row['confidence'].upper() if eo_row['confidence'] else 'N/A'}")
    st.markdown(f"**Expected CAR:** {eo_row['expected_car']:.2%}" if eo_row['expected_car'] else "")
    st.markdown(f"**Affected Tickers:** {', '.join(eo_row['tickers']) if eo_row['tickers'] else 'None'}")

    if eo_row.get('tariff_direction'):
        st.markdown(f"**Tariff Direction:** {eo_row['tariff_direction'].title()}")

    if eo_row.get('url'):
        st.markdown(f"[View on Federal Register]({eo_row['url']})")

conn.close()
```

### Task 5: Add "EO Signal" to Signal Types in Existing Pages

Update `dashboard/pages/6_Signals.py` to display and handle the new signal types:

1. Add `eo_tariff_trade`, `eo_defense`, `eo_sanctions`, and `reg_shock` to the signal type labels in the signal table
2. Update the signal performance charts to include new signal types
3. Add holding period of 3 days (EO) and 5 days (reg shock) to the signal execution logic

### Task 6: Update Data Collection Schedule

Add EO monitoring to the data collection pipeline in `dashboard/app.py`:

```python
# In the collection sequence, after Federal Register collection:
# Add EO classification pass
with st.spinner("Classifying executive orders..."):
    from analysis.eo_classifier import classify_eo
    conn = sqlite3.connect(DB_PATH)
    eos = pd.read_sql("""
        SELECT id, title FROM regulatory_events
        WHERE event_type = 'executive_order'
        AND id NOT IN (SELECT source_event_id FROM trading_signals WHERE signal_type LIKE 'eo_%' AND source_event_id IS NOT NULL)
    """, conn)

    for _, eo in eos.iterrows():
        cls = classify_eo(eo['title'])
        if cls['is_tradeable']:
            # Auto-generate signal
            for ticker in cls['tickers']:
                conn.execute("""
                    INSERT OR IGNORE INTO trading_signals
                    (signal_date, ticker, signal_type, direction, conviction,
                     source_event_id, source_table, rationale, status)
                    VALUES (date('now'), ?, ?, ?, ?, ?, 'regulatory_events', ?, 'pending')
                """, (ticker, 'eo_' + cls['topic'], cls['direction'],
                      cls['confidence'], eo['id'],
                      f"EO Signal: {eo['title'][:100]}. Expected CAR: {cls['expected_car']:.2%}"))
    conn.commit()
    conn.close()
```

### Task 7: Add Regulatory Shock Monitoring

After the EO classification pass, add shock detection:

```python
# In the collection sequence, after EO classification:
with st.spinner("Checking for regulatory intensity shocks..."):
    from analysis.reg_shock_detector import detect_shocks
    shocks = detect_shocks(lookback_weeks=1)
    if shocks:
        st.warning(f"⚠️ {len(shocks)} regulatory shock(s) detected!")
        for shock in shocks:
            st.write(f"  {shock['agency'][:50]}: z={shock['z_score']}, count={shock['count']}")
```

---

## File Summary

| File | Action | Priority |
|------|--------|----------|
| `analysis/eo_classifier.py` | CREATE | P0 |
| `analysis/reg_shock_detector.py` | CREATE | P0 |
| `analysis/signal_generator.py` | MODIFY — add 2 new signal methods | P0 |
| `dashboard/pages/7_EO_Tracker.py` | CREATE | P1 |
| `dashboard/pages/6_Signals.py` | MODIFY — add new signal type labels | P1 |
| `dashboard/app.py` | MODIFY — add EO classification + shock detection to collection pipeline | P1 |

## Testing Checklist

1. Run `python -c "from analysis.eo_classifier import classify_eo; print(classify_eo('Imposing a Temporary Import Surcharge'))"` — should return topic='tariff_trade', direction='long', confidence='high'
2. Run `python -c "from analysis.reg_shock_detector import detect_shocks; print(detect_shocks(4))"` — should return list of recent shocks
3. Navigate to the EO Tracker page in the app — should show classified EOs with signal alerts
4. Run signal generation — should produce eo_tariff_trade, eo_defense, eo_sanctions, and reg_shock signals
5. Verify no duplicate signals are generated for the same EO/ticker pair within 3 days

## Dependencies

No new Python packages required. All analysis uses existing dependencies (pandas, numpy, scipy, plotly, streamlit).
