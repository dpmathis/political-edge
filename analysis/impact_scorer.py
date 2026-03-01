"""Impact scorer — auto-scores regulatory events on a 1-5 scale.

Scoring is based on event type, keyword boosts, and watchlist relevance.
Users can override the auto-score.
"""

import sqlite3

from config import DB_PATH

# Base scores by event type
EVENT_TYPE_SCORES = {
    "bill_signed": 5,
    "executive_order": 5,
    "final_rule": 4,
    "bill_passed_chamber": 4,
    "bill_passed_committee": 3,
    "proposed_rule": 3,
    "hearing_scheduled": 2,
    "comment_period_open": 2,
    "comment_period_close": 2,
    "bill_introduced": 1,
    "notice": 1,
}

# Title keywords that boost score
BOOST_KEYWORDS = ["emergency", "final", "effective immediately", "executive order", "national security"]
# Title keywords that reduce score
REDUCE_KEYWORDS = ["technical correction", "administrative", "nomenclature", "typographical", "clerical"]


def score_event(
    event_type: str,
    title: str,
    tickers: str,
    agency: str,
) -> int:
    """Calculate impact score for an event.

    Returns:
        Score from 1-5.
    """
    title_lower = (title or "").lower()

    # Base score from event type
    score = EVENT_TYPE_SCORES.get(event_type, 1)

    # Boost if title contains high-impact keywords
    for kw in BOOST_KEYWORDS:
        if kw in title_lower:
            score += 1
            break

    # Reduce if title contains low-impact keywords
    for kw in REDUCE_KEYWORDS:
        if kw in title_lower:
            score -= 1
            break

    # Boost if affects multiple watchlist tickers
    ticker_list = [t.strip() for t in (tickers or "").split(",") if t.strip()]
    if len(ticker_list) >= 3:
        score += 1

    return max(1, min(5, score))


def score_all_unscored(conn: sqlite3.Connection | None = None) -> int:
    """Score all events that haven't been scored (impact_score = 0). Returns count scored."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        rows = conn.execute(
            "SELECT id, event_type, title, tickers, agency FROM regulatory_events WHERE impact_score = 0"
        ).fetchall()

        for event_id, event_type, title, tickers, agency in rows:
            score = score_event(event_type, title, tickers, agency)
            conn.execute(
                "UPDATE regulatory_events SET impact_score = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (score, event_id),
            )

        conn.commit()
        return len(rows)
    finally:
        if close_conn:
            conn.close()
