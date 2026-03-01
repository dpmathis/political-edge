"""Signal Generator.

Combines outputs from all collectors and analysis modules to produce
actionable trading signals. Evaluates trigger rules, applies macro regime
modifiers, deduplicates, and inserts into trading_signals table.

Usage:
    from analysis.signal_generator import generate_signals, review_active_signals
    new_signals = generate_signals()
    review_active_signals()
"""

import logging
import sqlite3
from datetime import date

from config import DB_PATH

logger = logging.getLogger(__name__)

# Conviction levels
CONVICTION_LEVELS = ["low", "medium", "high"]

SIGNAL_RULES = {
    "fda_catalyst": {
        "description": "Upcoming FDA AdCom vote or PDUFA date for a watchlist company",
        "source_table": "fda_events",
        "trigger_sql": """
            SELECT id, ticker, event_type, event_date, drug_name, company_name
            FROM fda_events
            WHERE event_type IN ('adcom_vote', 'pdufa_date')
              AND outcome = 'pending'
              AND ticker IS NOT NULL
              AND event_date >= date('now')
              AND event_date <= date('now', '+30 days')
        """,
        "default_direction": "long",
        "base_conviction": "medium",
    },
    "contract_momentum": {
        "description": "Watchlist company wins large government contract",
        "source_table": "contract_awards",
        "trigger_sql": """
            SELECT id, recipient_ticker AS ticker, description, award_date, award_amount, awarding_agency
            FROM contract_awards
            WHERE award_amount >= 50000000
              AND recipient_ticker IS NOT NULL
              AND award_date >= date('now', '-7 days')
        """,
        "default_direction": "long",
        "base_conviction": "medium",
    },
    "regulatory_event": {
        "description": "High-impact regulatory event affecting watchlist sector",
        "source_table": "regulatory_events",
        "trigger_sql": """
            SELECT id, tickers, event_type, title, publication_date, impact_score, agency
            FROM regulatory_events
            WHERE impact_score >= 4
              AND tickers IS NOT NULL
              AND tickers != ''
              AND publication_date >= date('now', '-3 days')
        """,
        "default_direction": "long",
        "base_conviction": "medium",
    },
    "lobbying_spike": {
        "description": "Company lobbying spend increased >25% QoQ with relevant regulatory event",
        "source_table": "lobbying_filings",
        "trigger_sql": None,  # Custom query handled in code
        "default_direction": "watch",
        "base_conviction": "low",
    },
    "macro_regime": {
        "description": "Macro regime change detected",
        "source_table": "macro_regimes",
        "trigger_sql": None,  # Custom check handled in code
        "default_direction": "watch",
        "base_conviction": "medium",
    },
}

# Max holding period for active signals
MAX_HOLDING_DAYS = 20
STOP_LOSS_PCT = -0.05
TAKE_PROFIT_PCT = 0.15


def _get_macro_modifier(conn: sqlite3.Connection) -> tuple[float, int | None]:
    """Get current macro regime position modifier and quadrant."""
    row = conn.execute(
        "SELECT quadrant, position_size_modifier FROM macro_regimes ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if row:
        return row[1], row[0]
    return 1.0, None


def _has_recent_signal(conn: sqlite3.Connection, ticker: str, signal_type: str, days: int = 5) -> bool:
    """Check if a signal already exists for this ticker + type within N days."""
    row = conn.execute(
        """SELECT COUNT(*) FROM trading_signals
           WHERE ticker = ? AND signal_type = ? AND signal_date >= date('now', ?)""",
        (ticker, signal_type, f"-{days} days"),
    ).fetchone()
    return row[0] > 0


def _adjust_conviction(base: str, boosts: int, reduces: int) -> str:
    """Adjust conviction level based on boosts and reduces."""
    idx = CONVICTION_LEVELS.index(base)
    idx = max(0, min(len(CONVICTION_LEVELS) - 1, idx + boosts - reduces))
    return CONVICTION_LEVELS[idx]


def _determine_regulatory_direction(event_type: str, title: str) -> str:
    """Determine direction for regulatory events based on type and title."""
    title_lower = title.lower() if title else ""

    # Restrictive keywords suggest short
    restrict_words = ["restrict", "ban", "prohibit", "penalty", "enforcement", "recall", "suspend"]
    if any(w in title_lower for w in restrict_words):
        return "short"

    # Supportive keywords suggest long
    support_words = ["subsid", "incentive", "credit", "approve", "grant", "award", "fund"]
    if any(w in title_lower for w in support_words):
        return "long"

    # Executive orders tend to be directional
    if event_type == "executive_order":
        return "long"  # Default to long for EOs, adjust manually

    return "long"


def _generate_fda_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate signals from FDA catalyst events."""
    signals = []
    rule = SIGNAL_RULES["fda_catalyst"]

    rows = conn.execute(rule["trigger_sql"]).fetchall()
    columns = ["id", "ticker", "event_type", "event_date", "drug_name", "company_name"]

    for row in rows:
        event = dict(zip(columns, row))
        ticker = event["ticker"]

        if _has_recent_signal(conn, ticker, "fda_catalyst"):
            continue

        conviction = _adjust_conviction(rule["base_conviction"], 0, 0)
        rationale = f"Upcoming {event['event_type'].replace('_', ' ')} for {event.get('drug_name', 'N/A')} ({event.get('company_name', '')})"

        signals.append({
            "ticker": ticker,
            "signal_type": "fda_catalyst",
            "direction": rule["default_direction"],
            "conviction": conviction,
            "source_event_id": event["id"],
            "source_table": "fda_events",
            "rationale": rationale,
            "macro_regime_at_signal": macro_quadrant,
            "position_size_modifier": macro_modifier,
        })

    return signals


def _generate_contract_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate signals from large contract awards."""
    signals = []
    rule = SIGNAL_RULES["contract_momentum"]

    rows = conn.execute(rule["trigger_sql"]).fetchall()
    columns = ["id", "ticker", "description", "award_date", "award_amount", "awarding_agency"]

    for row in rows:
        event = dict(zip(columns, row))
        ticker = event["ticker"]

        if _has_recent_signal(conn, ticker, "contract_momentum"):
            continue

        boosts = 0
        if event["award_amount"] and event["award_amount"] >= 500_000_000:
            boosts += 1
        if event["awarding_agency"] and "defense" in event["awarding_agency"].lower():
            boosts += 1

        conviction = _adjust_conviction(rule["base_conviction"], boosts, 0)
        rationale = f"${event['award_amount']:,.0f} contract from {event.get('awarding_agency', 'N/A')}"

        signals.append({
            "ticker": ticker,
            "signal_type": "contract_momentum",
            "direction": rule["default_direction"],
            "conviction": conviction,
            "source_event_id": event["id"],
            "source_table": "contract_awards",
            "rationale": rationale,
            "macro_regime_at_signal": macro_quadrant,
            "position_size_modifier": macro_modifier,
        })

    return signals


def _generate_regulatory_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate signals from high-impact regulatory events."""
    signals = []
    rule = SIGNAL_RULES["regulatory_event"]

    rows = conn.execute(rule["trigger_sql"]).fetchall()
    columns = ["id", "tickers", "event_type", "title", "publication_date", "impact_score", "agency"]

    for row in rows:
        event = dict(zip(columns, row))
        tickers_str = event["tickers"]
        if not tickers_str:
            continue

        # Parse tickers (comma-separated)
        tickers = [t.strip() for t in tickers_str.split(",") if t.strip()]

        for ticker in tickers:
            if _has_recent_signal(conn, ticker, "regulatory_event"):
                continue

            boosts = 0
            reduces = 0
            if event["event_type"] == "executive_order":
                boosts += 1
            if len(tickers) > 1:
                boosts += 1
            if event["event_type"] == "proposed_rule":
                reduces += 1

            direction = _determine_regulatory_direction(event["event_type"], event["title"])
            conviction = _adjust_conviction(rule["base_conviction"], boosts, reduces)
            rationale = f"{event['event_type'].replace('_', ' ').title()}: {event['title'][:100]}"

            signals.append({
                "ticker": ticker,
                "signal_type": "regulatory_event",
                "direction": direction,
                "conviction": conviction,
                "source_event_id": event["id"],
                "source_table": "regulatory_events",
                "rationale": rationale,
                "macro_regime_at_signal": macro_quadrant,
                "position_size_modifier": macro_modifier,
            })

    return signals


def _generate_lobbying_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate signals from lobbying spend spikes combined with regulatory events."""
    signals = []

    try:
        from collectors.lobbying import calculate_qoq_changes
        changes = calculate_qoq_changes(conn)
    except Exception as e:
        logger.error("Failed to calculate QoQ lobbying changes: %s", e)
        return signals

    spikes = [c for c in changes if c.get("spike")]

    for spike in spikes:
        ticker = spike.get("ticker")
        if not ticker or _has_recent_signal(conn, ticker, "lobbying_spike"):
            continue

        # Check for matching regulatory event in the same sector within 90 days
        reg_match = conn.execute(
            """SELECT id, title, event_type FROM regulatory_events
               WHERE tickers LIKE ? AND publication_date >= date('now', '-90 days')
               ORDER BY publication_date DESC LIMIT 1""",
            (f"%{ticker}%",),
        ).fetchone()

        if not reg_match:
            continue  # Lobbying spike alone is not a signal

        boosts = 0
        if reg_match[2] == "proposed_rule":
            boosts += 1  # Company trying to influence outcome

        conviction = _adjust_conviction("low", boosts, 0)
        rationale = (
            f"Lobbying spike: {spike['pct_change']:+.0%} QoQ for {spike.get('client_name', '')}. "
            f"Related event: {reg_match[1][:80]}"
        )

        signals.append({
            "ticker": ticker,
            "signal_type": "lobbying_spike",
            "direction": "watch",
            "conviction": conviction,
            "source_event_id": reg_match[0],
            "source_table": "regulatory_events",
            "rationale": rationale,
            "macro_regime_at_signal": macro_quadrant,
            "position_size_modifier": macro_modifier,
        })

    return signals


def generate_signals() -> list[dict]:
    """Evaluate all signal rules and generate new trading signals.

    Returns:
        List of newly created signal dicts.
    """
    conn = sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
    macro_modifier, macro_quadrant = _get_macro_modifier(conn)

    all_signals = []

    # Run each signal generator
    generators = [
        ("fda_catalyst", _generate_fda_signals),
        ("contract_momentum", _generate_contract_signals),
        ("regulatory_event", _generate_regulatory_signals),
        ("lobbying_spike", _generate_lobbying_signals),
    ]

    for name, gen_func in generators:
        try:
            new = gen_func(conn, macro_modifier, macro_quadrant)
            all_signals.extend(new)
            if new:
                logger.info("Signal generator [%s]: %d new signals", name, len(new))
        except Exception as e:
            logger.error("Signal generator [%s] failed: %s", name, e)

    # Insert signals
    inserted = []
    for sig in all_signals:
        try:
            cursor = conn.execute(
                """INSERT INTO trading_signals
                   (signal_date, ticker, signal_type, direction, conviction,
                    source_event_id, source_table, rationale,
                    macro_regime_at_signal, position_size_modifier, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    today,
                    sig["ticker"],
                    sig["signal_type"],
                    sig["direction"],
                    sig["conviction"],
                    sig.get("source_event_id"),
                    sig.get("source_table"),
                    sig.get("rationale"),
                    sig.get("macro_regime_at_signal"),
                    sig.get("position_size_modifier", 1.0),
                ),
            )
            sig["id"] = cursor.lastrowid
            inserted.append(sig)
        except Exception as e:
            logger.error("Failed to insert signal: %s", e)

    conn.commit()
    conn.close()
    logger.info("Signal generator: %d new signals created", len(inserted))
    return inserted


def review_active_signals() -> int:
    """Review active signals and close those that hit exit conditions.

    Returns:
        Count of signals closed.
    """
    conn = sqlite3.connect(DB_PATH)
    closed = 0

    active = conn.execute(
        """SELECT id, ticker, direction, entry_price, entry_date, signal_type
           FROM trading_signals WHERE status = 'active'"""
    ).fetchall()

    for signal in active:
        sig_id, ticker, direction, entry_price, entry_date, signal_type = signal

        if not entry_price or not entry_date:
            continue

        # Get current price
        price_row = conn.execute(
            "SELECT close FROM market_data WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker,),
        ).fetchone()

        if not price_row:
            continue

        current_price = price_row[0]

        # Calculate PnL
        if direction == "long":
            pnl_pct = (current_price - entry_price) / entry_price
        elif direction == "short":
            pnl_pct = (entry_price - current_price) / entry_price
        else:
            continue

        # Calculate holding days
        try:
            from datetime import datetime
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d").date()
            holding_days = (date.today() - entry_dt).days
        except (ValueError, TypeError):
            holding_days = 0

        # Check exit conditions
        should_close = False
        close_reason = None

        if holding_days >= MAX_HOLDING_DAYS:
            should_close = True
            close_reason = f"Max holding period ({MAX_HOLDING_DAYS} days)"
        elif pnl_pct <= STOP_LOSS_PCT:
            should_close = True
            close_reason = f"Stop loss hit ({pnl_pct:+.1%})"
        elif pnl_pct >= TAKE_PROFIT_PCT:
            should_close = True
            close_reason = f"Take profit hit ({pnl_pct:+.1%})"

        if should_close:
            pnl_dollars = (current_price - entry_price) if direction == "long" else (entry_price - current_price)
            conn.execute(
                """UPDATE trading_signals SET
                   status = 'closed', exit_price = ?, exit_date = ?,
                   pnl_percent = ?, pnl_dollars = ?, holding_days = ?,
                   user_notes = COALESCE(user_notes || ' | ', '') || ?
                   WHERE id = ?""",
                (
                    current_price,
                    date.today().isoformat(),
                    pnl_pct,
                    pnl_dollars,
                    holding_days,
                    f"Auto-closed: {close_reason}",
                    sig_id,
                ),
            )
            closed += 1
            logger.info("Closed signal %d (%s %s): %s", sig_id, ticker, direction, close_reason)

    conn.commit()
    conn.close()
    logger.info("Signal review: %d signals closed", closed)
    return closed
