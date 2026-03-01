"""Reusable event display components."""

import streamlit as st

# Color coding for impact scores
IMPACT_COLORS = {
    1: "gray",
    2: "blue",
    3: "orange",
    4: "red",
    5: "red",
}

EVENT_TYPE_LABELS = {
    "final_rule": "Final Rule",
    "proposed_rule": "Proposed Rule",
    "executive_order": "Executive Order",
    "notice": "Notice",
    "bill_signed": "Bill Signed",
    "bill_passed_chamber": "Passed Chamber",
    "bill_passed_committee": "Passed Committee",
    "bill_introduced": "Bill Introduced",
    "hearing_scheduled": "Hearing Scheduled",
    "comment_period_open": "Comment Period Open",
    "comment_period_close": "Comment Period Close",
}

TRADE_ACTION_OPTIONS = ["none", "watch", "long", "short", "close"]


def render_impact_badge(score: int) -> str:
    """Return a colored impact score badge."""
    if score >= 4:
        return f":red[**{score}/5**]"
    elif score >= 3:
        return f":orange[**{score}/5**]"
    elif score >= 2:
        return f":blue[{score}/5]"
    return f"{score}/5"


def format_event_type(event_type: str) -> str:
    return EVENT_TYPE_LABELS.get(event_type, event_type.replace("_", " ").title())
