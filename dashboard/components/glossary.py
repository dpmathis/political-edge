"""Universal glossary and tooltip system.

Every technical term gets a hover tooltip with a plain-English definition.
Uses st.metric(help=...) for metric tooltips and HTML title= for inline text.
"""

import streamlit as st

# ── Glossary — 20+ terms from PRD Section 6.2 ────────────────────────

GLOSSARY = {
    "CAR": (
        "Cumulative Abnormal Return — how much a stock moved beyond what the "
        "market did. Isolates the signal's actual impact."
    ),
    "p-value": (
        "How confident we are this isn't random luck. Below 0.05 = statistically "
        "significant. Below 0.01 = very strong evidence."
    ),
    "Win Rate": "What percentage of the time this signal made money.",
    "Macro Regime": (
        "Which of 4 economic environments we're in, based on whether growth "
        "and inflation are speeding up or slowing down."
    ),
    "Position Size Modifier": (
        "How much to scale your bet based on the current macro environment. "
        "1.2x = slightly larger than normal. 0.4x = much smaller."
    ),
    "Conviction": (
        "How strongly the evidence supports this trade. Based on event type, "
        "historical performance, and macro context."
    ),
    "VIX": (
        "The 'fear gauge' — measures expected market volatility. Below 15 = calm, "
        "above 25 = anxious, above 35 = panic."
    ),
    "Yield Curve Spread": (
        "Difference between long-term (10Y) and short-term (2Y) interest rates. "
        "Negative = inverted = recession signal."
    ),
    "10Y-2Y Spread": (
        "Difference between 10-year and 2-year Treasury yields. "
        "Negative = inverted = historically predicts recessions."
    ),
    "PDUFA Date": (
        "FDA deadline to decide on a drug application. Stocks often move "
        "significantly around these dates."
    ),
    "AdCom Vote": (
        "FDA advisory committee meeting — a panel of experts votes on whether "
        "to recommend a drug. A positive vote usually means approval is likely."
    ),
    "Hawkish/Dovish Score": (
        "How aggressive (+hawkish, wants higher rates) or accommodative "
        "(-dovish, wants lower rates) the Fed's language is."
    ),
    "FOMC Drift": (
        "The historical tendency for SPY to rise ~0.5% in the 5 trading days "
        "before a Fed meeting. Well-documented market anomaly."
    ),
    "Regulatory Shock": (
        "An unusual surge in regulatory activity from a specific agency — "
        "detected by z-score analysis. May signal policy shift."
    ),
    "QoQ": "Quarter over Quarter — comparing this quarter to last quarter.",
    "RoC": "Rate of Change — how fast a metric is moving up or down.",
    "Impact Score": (
        "How likely this event is to move stock prices, from 1 (minimal) "
        "to 5 (major market mover). Based on event type and keywords."
    ),
    "Confluence Score": (
        "How many independent data sources point the same direction for a ticker. "
        "Score of 4+ = strong multi-factor support. Score of 0-1 = weak evidence."
    ),
    "Goldilocks": (
        "Macro regime where growth is accelerating and inflation is cooling. "
        "Best environment for stocks — lean into risk assets."
    ),
    "Reflation": (
        "Macro regime where both growth and inflation are accelerating. "
        "Favors commodities, energy, and financials."
    ),
    "Stagflation": (
        "Macro regime where growth is slowing but inflation is rising. "
        "Worst environment for stocks — go defensive."
    ),
    "Deflation": (
        "Macro regime where both growth and inflation are decelerating. "
        "Favors cash and bonds — reduce equity exposure."
    ),
    "Sharpe Ratio": (
        "Risk-adjusted return measure. Above 1.0 = good, above 2.0 = very good. "
        "Higher means more return per unit of risk."
    ),
    "Abnormal Return": (
        "Stock return minus what the market did. Positive = the stock beat the "
        "market. Negative = it underperformed."
    ),
    "Pipeline Pressure": (
        "Count of proposed rules past their comment deadline without a matching "
        "final rule. High pressure = more regulatory uncertainty for that sector."
    ),
    "Proposed Rule": (
        "A regulation an agency wants to implement. There's a public comment "
        "period before it becomes final. Markets tend to underreact to these."
    ),
    "Comment Deadline": (
        "The date when public comment on a proposed rule closes. After this, "
        "the agency moves toward a final decision."
    ),
    "Estimated Final Date": (
        "When we predict the final rule will be published, based on how long "
        "this agency typically takes from proposed to final."
    ),
    "Pipeline Scenario": (
        "A what-if analysis: if selected proposed rules finalize (or are withdrawn), "
        "what's the expected market impact based on historical data?"
    ),
    "Contract Award": (
        "A federal government contract given to a company via USASpending. "
        "Large awards can move the recipient's stock price."
    ),
    "Congressional Trade": (
        "A stock trade disclosed by a member of Congress under the STOCK Act. "
        "Historically, congressional trades have outperformed the market."
    ),
    "Disclosure Delay": (
        "Days between when a congressional trade happened and when it was publicly "
        "disclosed. Longer delays may indicate less urgency or oversight gaps."
    ),
    "Prediction Market": (
        "A market where participants trade contracts on the outcome of future events. "
        "Prices reflect the crowd's estimated probability of each outcome."
    ),
    "Backtest": (
        "Running a trading strategy against historical data to see how it would have "
        "performed. Helps validate whether a signal has real predictive power."
    ),
    "Event Study": (
        "A statistical method that measures how a stock reacts to a specific event "
        "(e.g., FDA approval, regulation). Compares actual vs. expected returns."
    ),
    "Sector Mapping": (
        "Linking a regulatory event or government action to the stock market sector "
        "it most likely affects. Used to identify which ETFs or stocks to trade."
    ),
    "Data Freshness": (
        "How recently each data source was updated. Stale data (>24h old) may "
        "mean the collector needs to be re-run from Settings."
    ),
}


# ── Tooltip CSS ───────────────────────────────────────────────────────

_TOOLTIP_CSS = """
<style>
.glossary-term {
    border-bottom: 1px dotted #64748b;
    cursor: help;
    position: relative;
}
.glossary-term:hover {
    border-bottom-color: #3b82f6;
    color: #3b82f6;
}
</style>
"""

_css_injected = False


def inject_tooltip_css():
    """Inject CSS for glossary tooltips. Call once per page."""
    global _css_injected
    if not _css_injected:
        st.markdown(_TOOLTIP_CSS, unsafe_allow_html=True)
        _css_injected = True


# ── Render Helpers ────────────────────────────────────────────────────

def render_metric_with_tooltip(label: str, value, term_key: str = None, **kwargs):
    """Render st.metric with a glossary tooltip via the help parameter.

    Args:
        label: Metric label.
        value: Metric value.
        term_key: Glossary key for the help tooltip. If None, uses label.
        **kwargs: Additional kwargs passed to st.metric (delta, delta_color, etc.)
    """
    key = term_key or label
    help_text = GLOSSARY.get(key)
    st.metric(label, value, help=help_text, **kwargs)


def render_glossary_term(term: str, display_text: str = None) -> str:
    """Return an HTML span with a glossary tooltip for inline use.

    Args:
        term: The glossary key to look up.
        display_text: Text to display. Defaults to the term itself.

    Returns:
        HTML string with title= tooltip and dotted underline.
    """
    text = display_text or term
    definition = GLOSSARY.get(term, "")
    if not definition:
        return text
    # Escape quotes for HTML attribute
    safe_def = definition.replace('"', "&quot;")
    return f'<span class="glossary-term" title="{safe_def}">{text}</span>'


def tooltip(term: str) -> str | None:
    """Get the glossary definition for a term, for use with st.metric(help=...)."""
    return GLOSSARY.get(term)
