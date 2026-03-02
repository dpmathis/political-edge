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
    "eo_signal": {
        "description": "Executive order with statistically significant sector impact",
        "source_table": "regulatory_events",
        "trigger_sql": None,  # Custom — uses eo_classifier.py
        "default_direction": "long",
        "base_conviction": "high",
    },
    "reg_shock": {
        "description": "Abnormal surge in regulatory activity from specific agency",
        "source_table": "regulatory_events",
        "trigger_sql": None,  # Custom — uses reg_shock_detector.py
        "default_direction": "long",
        "base_conviction": "high",
    },
    "fomc_drift": {
        "description": "Pre-FOMC drift trade (documented +0.49% avg SPY drift 5 days before meeting)",
        "source_table": "fomc_events",
        "trigger_sql": None,  # Custom — date-based
        "default_direction": "long",
        "base_conviction": "medium",
    },
    "pipeline_pressure": {
        "description": "Sector rotation based on regulatory pipeline pressure (Report 3)",
        "source_table": "regulatory_events",
        "trigger_sql": None,  # Custom — uses report3 pipeline pressure logic
        "default_direction": "long",
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


def _determine_regulatory_direction(event_type: str, title: str, agency: str = None,
                                     macro_quadrant: int = None) -> str:
    """Determine direction for regulatory events based on type, title, agency, and macro.

    Logic:
    1. Strong keyword signals override everything
    2. Event type semantics (proposed_rule = uncertainty → short bias)
    3. Agency-specific patterns (DoD → long defense, EPA → short energy)
    4. Macro regime context (stagflation → short bias for new regulation)
    """
    title_lower = title.lower() if title else ""
    agency_lower = agency.lower() if agency else ""

    # Strong restrictive keywords → short
    restrict_words = ["restrict", "ban", "prohibit", "penalty", "enforcement",
                      "recall", "suspend", "revoke", "terminate", "cease", "fine"]
    if any(w in title_lower for w in restrict_words):
        return "short"

    # Strong supportive keywords → long
    support_words = ["subsid", "incentive", "credit", "approve", "grant",
                     "award", "fund", "waiver", "deregulat", "relief", "expedit"]
    if any(w in title_lower for w in support_words):
        return "long"

    # Tariff-specific: imposition → short affected sectors, relief → long
    if "tariff" in title_lower or "duty" in title_lower:
        tariff_negative = ["impos", "increas", "additional", "retaliatory"]
        if any(w in title_lower for w in tariff_negative):
            return "short"
        tariff_positive = ["reduc", "eliminat", "exempt", "suspen", "relief"]
        if any(w in title_lower for w in tariff_positive):
            return "long"
        return "short"  # Default tariffs to short (uncertainty)

    # Event type semantics
    if event_type == "proposed_rule":
        # Proposed rules create uncertainty — lean short unless clearly supportive
        return "short"
    if event_type == "final_rule":
        # Final rules resolve uncertainty — lean long (clarity)
        return "long"

    # Agency-specific patterns
    if any(a in agency_lower for a in ["defense", "dod", "army", "navy", "air force"]):
        return "long"  # Defense spending = long defense tickers
    if any(a in agency_lower for a in ["environmental protection", "epa"]):
        # EPA rules → typically short for energy, but macro-dependent
        if macro_quadrant in (1, 2):  # Growth accelerating, can absorb regulation
            return "long"
        return "short"

    # Macro regime context for ambiguous events
    if macro_quadrant in (3, 4):  # Stagflation/Deflation — new regulation is headwind
        return "short"

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

            direction = _determine_regulatory_direction(
                event["event_type"], event["title"],
                agency=event.get("agency"), macro_quadrant=macro_quadrant,
            )
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


def _generate_eo_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate signals from recent executive orders using topic classification."""
    signals = []

    try:
        from analysis.eo_classifier import classify_eo
    except ImportError:
        logger.error("Failed to import eo_classifier")
        return signals

    rows = conn.execute(
        """SELECT id, publication_date, title
           FROM regulatory_events
           WHERE event_type = 'executive_order'
             AND publication_date >= date('now', '-2 days')
           ORDER BY publication_date DESC"""
    ).fetchall()

    for row_id, pub_date, title in rows:
        classification = classify_eo(title)

        if not classification["is_tradeable"]:
            continue

        topic = classification["topic"]
        for ticker in classification["tickers"]:
            signal_type = f"eo_{topic}"
            if _has_recent_signal(conn, ticker, signal_type, days=3):
                continue

            rationale = (
                f"Executive Order: {title[:100]}. "
                f"Topic: {topic.replace('_', ' ').title()}. "
                f"Expected CAR: {classification['expected_car']:+.2%} over 3 days "
                f"(N={classification.get('sample_size', '?')})."
            )

            signals.append({
                "ticker": ticker,
                "signal_type": signal_type,
                "direction": classification["direction"],
                "conviction": classification["confidence"],
                "source_event_id": row_id,
                "source_table": "regulatory_events",
                "rationale": rationale,
                "macro_regime_at_signal": macro_quadrant,
                "position_size_modifier": macro_modifier,
                "expected_car": classification["expected_car"],
            })

    return signals


def _generate_reg_shock_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate signals from regulatory intensity shocks by agency."""
    signals = []

    try:
        from analysis.reg_shock_detector import detect_shocks
    except ImportError:
        logger.error("Failed to import reg_shock_detector")
        return signals

    shocks = detect_shocks(lookback_weeks=1, conn=conn)

    for shock in shocks:
        for ticker in shock["tickers"]:
            if _has_recent_signal(conn, ticker, "reg_shock", days=7):
                continue

            rationale = (
                f"Regulatory intensity shock: {shock['agency'][:60]}. "
                f"Weekly count: {shock['count']} (z-score: {shock['z_score']:.1f}). "
                f"Expected CAR: {shock['expected_car']:+.2%} over {shock['hold_days']} days."
            )

            signals.append({
                "ticker": ticker,
                "signal_type": "reg_shock",
                "direction": shock["direction"],
                "conviction": shock["confidence"],
                "source_event_id": None,
                "source_table": "regulatory_events",
                "rationale": rationale,
                "macro_regime_at_signal": macro_quadrant,
                "position_size_modifier": macro_modifier,
                "expected_car": shock["expected_car"],
                "time_horizon_days": shock.get("hold_days", 5),
            })

    return signals


def _generate_fomc_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate pre-FOMC drift and post-decision signals.

    Pre-FOMC drift: documented +0.49% average SPY drift in 5 days before meeting.
    Post-decision rotation: after rate decision, sector rotation signals.
    """

    signals = []
    today_dt = date.today()

    # --- Pre-FOMC Drift ---
    # Find FOMC meetings 3-8 days from now (entry window)
    upcoming = conn.execute(
        """SELECT id, event_date FROM fomc_events
           WHERE event_date >= date('now', '+3 days')
             AND event_date <= date('now', '+8 days')
           ORDER BY event_date LIMIT 1"""
    ).fetchone()

    if upcoming and not _has_recent_signal(conn, "SPY", "fomc_drift", days=14):
        fomc_id, fomc_date = upcoming
        days_until = (date.fromisoformat(fomc_date) - today_dt).days

        rationale = (
            f"Pre-FOMC drift: FOMC meeting on {fomc_date} ({days_until} days). "
            f"Historical avg drift: +0.49% in 5 days before meeting."
        )

        signals.append({
            "ticker": "SPY",
            "signal_type": "fomc_drift",
            "direction": "long",
            "conviction": "medium",
            "source_event_id": fomc_id,
            "source_table": "fomc_events",
            "rationale": rationale,
            "macro_regime_at_signal": macro_quadrant,
            "position_size_modifier": macro_modifier,
            "expected_car": 0.0049,
            "time_horizon_days": days_until,
        })

    # --- Post-Decision Sector Rotation ---
    # Check if a meeting happened in last 1 day with a rate decision
    recent_decision = conn.execute(
        """SELECT id, event_date, rate_decision, hawkish_dovish_score
           FROM fomc_events
           WHERE event_date >= date('now', '-1 day')
             AND event_date <= date('now')
             AND rate_decision IS NOT NULL
           ORDER BY event_date DESC LIMIT 1"""
    ).fetchone()

    if recent_decision:
        fomc_id, fomc_date, decision, hd_score = recent_decision

        # Rate cut → long financials/REITs
        if decision and "cut" in decision.lower():
            rotation_tickers = {"XLF": "Financials benefit from yield curve steepening",
                                "XLRE": "REITs benefit from lower rates"}
            direction = "long"
        # Rate hike → short rate-sensitive sectors
        elif decision and "hike" in decision.lower():
            rotation_tickers = {"XLF": "Financials pressured by flattening curve",
                                "XLRE": "REITs pressured by higher rates"}
            direction = "short"
        else:
            rotation_tickers = {}

        for ticker, reason in rotation_tickers.items():
            if _has_recent_signal(conn, ticker, "fomc_drift", days=7):
                continue

            # Adjust conviction based on hawkish/dovish score magnitude
            conviction = "medium"
            if hd_score is not None and abs(hd_score) > 0.5:
                conviction = "high"

            rationale = (
                f"Post-FOMC rotation: {decision} on {fomc_date}. {reason}. "
                f"H/D score: {hd_score:+.2f}." if hd_score else
                f"Post-FOMC rotation: {decision} on {fomc_date}. {reason}."
            )

            signals.append({
                "ticker": ticker,
                "signal_type": "fomc_drift",
                "direction": direction,
                "conviction": conviction,
                "source_event_id": fomc_id,
                "source_table": "fomc_events",
                "rationale": rationale,
                "macro_regime_at_signal": macro_quadrant,
                "position_size_modifier": macro_modifier,
                "time_horizon_days": 10,
            })

    return signals


def _generate_pipeline_pressure_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate sector rotation signals from regulatory pipeline pressure.

    Overweight sectors with low pipeline pressure (few pending rules),
    underweight sectors with high pressure (many pending rules without final resolution).
    """
    signals = []

    try:
        # Count proposed rules past comment_deadline without a matching final rule
        # per sector, in the last 90 days
        rows = conn.execute(
            """SELECT sectors, COUNT(*) as pending_count
               FROM regulatory_events
               WHERE event_type = 'proposed_rule'
                 AND comment_deadline IS NOT NULL
                 AND comment_deadline <= date('now')
                 AND sectors IS NOT NULL AND sectors != ''
                 AND id NOT IN (
                     SELECT pr.id FROM regulatory_events pr
                     JOIN regulatory_events fr ON fr.event_type = 'final_rule'
                       AND fr.agency = pr.agency
                       AND fr.publication_date > pr.publication_date
                       AND fr.publication_date <= date(pr.comment_deadline, '+365 days')
                     WHERE pr.event_type = 'proposed_rule'
                 )
                 AND publication_date >= date('now', '-180 days')
               GROUP BY sectors
               ORDER BY pending_count DESC"""
        ).fetchall()
    except Exception as e:
        logger.warning("Pipeline pressure query failed: %s", e)
        return signals

    if not rows:
        return signals

    from analysis.research.base import SECTOR_ETF_ONLY

    # Find the sector with lowest pressure → long signal
    sector_pressure = {}
    for sectors_str, count in rows:
        for sector in sectors_str.split(","):
            sector = sector.strip()
            if sector in SECTOR_ETF_ONLY:
                sector_pressure[sector] = sector_pressure.get(sector, 0) + count

    if not sector_pressure:
        return signals

    # Sort by pressure: lowest pressure sectors are candidates for long
    sorted_sectors = sorted(sector_pressure.items(), key=lambda x: x[1])

    # Signal on the lowest-pressure sector if we haven't recently
    for sector, pressure in sorted_sectors[:2]:
        ticker = SECTOR_ETF_ONLY[sector]
        if _has_recent_signal(conn, ticker, "pipeline_pressure", days=20):
            continue

        rationale = (
            f"Low regulatory pipeline pressure for {sector}: "
            f"{pressure} pending proposed rules past deadline. "
            f"Sector may face less regulatory headwind."
        )

        signals.append({
            "ticker": ticker,
            "signal_type": "pipeline_pressure",
            "direction": "long",
            "conviction": "medium",
            "source_event_id": None,
            "source_table": "regulatory_events",
            "rationale": rationale,
            "macro_regime_at_signal": macro_quadrant,
            "position_size_modifier": macro_modifier,
            "time_horizon_days": 20,
        })

    return signals


def _generate_pipeline_deadline_signals(conn: sqlite3.Connection, macro_modifier: float, macro_quadrant: int | None) -> list[dict]:
    """Generate signals when proposed rules approach their comment deadline.

    Report 3 found proposed rules generate -0.25% CAR (p=0.016).
    This triggers on rules approaching their comment deadline (within 7 days)
    with impact_score >= 3.
    """
    signals = []

    try:
        rows = conn.execute(
            """SELECT pr.id, pr.proposed_event_id, pr.agency, pr.sector, pr.tickers,
                      pr.proposed_title, pr.impact_score, pr.comment_deadline,
                      pr.historical_car
               FROM pipeline_rules pr
               WHERE pr.status IN ('proposed', 'in_comment')
                 AND pr.comment_deadline IS NOT NULL
                 AND pr.comment_deadline >= date('now')
                 AND pr.comment_deadline <= date('now', '+7 days')
                 AND pr.impact_score >= 3"""
        ).fetchall()
    except Exception as e:
        logger.warning("Pipeline deadline query failed: %s", e)
        return signals

    if not rows:
        return signals

    from analysis.research.base import SECTOR_ETF_ONLY

    for row in rows:
        pr_id, event_id, agency, sector, tickers_str, title, impact, deadline, hist_car = row

        # Determine tickers
        tickers = []
        if tickers_str and isinstance(tickers_str, str):
            tickers = [t.strip() for t in tickers_str.split(",") if t.strip()]
        if not tickers and sector in SECTOR_ETF_ONLY:
            tickers = [SECTOR_ETF_ONLY[sector]]
        if not tickers:
            tickers = ["SPY"]

        # Conviction: medium if we have significant historical evidence
        conviction = "medium" if hist_car is not None else "low"

        car_display = f"{hist_car:+.2%}" if hist_car else "-0.25%"
        rationale = (
            f"Pipeline deadline approaching: {title[:60]}. "
            f"Comment deadline {deadline}. "
            f"Historical CAR for similar rules: {car_display}. "
            f"Impact score: {impact}/5."
        )

        for ticker in tickers[:3]:
            if _has_recent_signal(conn, ticker, "pipeline_deadline", days=14):
                continue

            signals.append({
                "ticker": ticker,
                "signal_type": "pipeline_deadline",
                "direction": "short",  # Negative CAR per Report 3
                "conviction": conviction,
                "source_event_id": event_id,
                "source_table": "pipeline_rules",
                "rationale": rationale,
                "macro_regime_at_signal": macro_quadrant,
                "position_size_modifier": macro_modifier,
                "time_horizon_days": 10,
            })

    return signals


def _apply_prediction_market_modifier(sig: dict, conn: sqlite3.Connection) -> None:
    """Adjust conviction based on prediction market probabilities.

    If a matching prediction market exists:
    - Event priced at >90% → reduce conviction (priced in, limited upside)
    - Event priced at <50% and our signal agrees → boost conviction (contrarian edge)
    - Store the prediction market probability on the signal for reference
    """
    ticker = sig.get("ticker", "")
    signal_type = sig.get("signal_type", "")

    # Find matching prediction market contracts
    # Match by related_ticker or by category alignment
    category_map = {
        "fda_catalyst": "fda",
        "fomc_drift": "fomc",
    }
    category = category_map.get(signal_type)

    market = None
    if category == "fomc":
        # For FOMC, specifically look for rate decision markets (not nominations)
        market = conn.execute(
            """SELECT current_price, question_text FROM prediction_markets
               WHERE category = 'fomc' AND current_price IS NOT NULL
                 AND question_text LIKE '%interest rate%'
               ORDER BY volume DESC LIMIT 1""",
        ).fetchone()
    elif category:
        market = conn.execute(
            """SELECT current_price, question_text FROM prediction_markets
               WHERE category = ? AND current_price IS NOT NULL
               ORDER BY volume DESC LIMIT 1""",
            (category,),
        ).fetchone()
    elif ticker:
        market = conn.execute(
            """SELECT current_price, question_text FROM prediction_markets
               WHERE related_ticker = ? AND current_price IS NOT NULL
               ORDER BY volume DESC LIMIT 1""",
            (ticker,),
        ).fetchone()

    if not market:
        return

    prob, question = market
    sig["prediction_market_prob"] = prob

    # Apply conviction adjustment
    current_conviction = sig.get("conviction", "medium")
    conviction_idx = CONVICTION_LEVELS.index(current_conviction)

    if prob > 0.90:
        # Event highly priced in → reduce conviction (limited alpha)
        conviction_idx = max(0, conviction_idx - 1)
        sig["conviction"] = CONVICTION_LEVELS[conviction_idx]
    elif prob < 0.50 and sig.get("direction") == "long":
        # Market skeptical but we're long → contrarian edge → boost
        conviction_idx = min(len(CONVICTION_LEVELS) - 1, conviction_idx + 1)
        sig["conviction"] = CONVICTION_LEVELS[conviction_idx]
    elif prob > 0.50 and sig.get("direction") == "short":
        # Market expects event but we're short → contrarian edge → boost
        conviction_idx = min(len(CONVICTION_LEVELS) - 1, conviction_idx + 1)
        sig["conviction"] = CONVICTION_LEVELS[conviction_idx]


def _enrich_signal(sig: dict, conn: sqlite3.Connection) -> None:
    """Add trade parameters (stop/TP, position size, horizon, historical perf) to a signal."""
    from analysis.trading_context import get_historical_performance, get_time_horizon
    from execution.position_sizer import PositionSizer

    ticker = sig["ticker"]
    direction = sig["direction"]
    signal_type = sig["signal_type"]

    # Get current price
    price_row = conn.execute(
        "SELECT close FROM market_data WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()

    current_price = price_row[0] if price_row else None

    if current_price and direction in ("long", "short"):
        if direction == "long":
            sig["stop_loss_price"] = round(current_price * (1 + STOP_LOSS_PCT), 2)
            sig["take_profit_price"] = round(current_price * (1 + TAKE_PROFIT_PCT), 2)
        else:  # short
            sig["stop_loss_price"] = round(current_price * (1 - STOP_LOSS_PCT), 2)
            sig["take_profit_price"] = round(current_price * (1 - TAKE_PROFIT_PCT), 2)

    # Position size as percentage of equity
    sizer = PositionSizer()
    base_pct = sizer._conviction_to_base(sig.get("conviction", "medium"))
    modifier = sig.get("position_size_modifier", 1.0)
    sig["suggested_position_size"] = round(min(base_pct * modifier, sizer.max_single), 4)

    # Time horizon
    sig["time_horizon_days"] = get_time_horizon(signal_type)

    # Historical performance from event studies
    perf = get_historical_performance(signal_type, conn)
    if perf:
        sig["expected_car"] = perf.get("mean_car")
        sig["historical_win_rate"] = perf.get("win_rate")
        sig["historical_p_value"] = perf.get("p_value")
        sig["historical_n_events"] = perf.get("n_events")
    elif sig.get("expected_car") is None:
        # Some signal types (eo_*, reg_shock) already have expected_car set in rationale
        pass

    # Apply prediction market conviction modifier (must run after conviction is set)
    _apply_prediction_market_modifier(sig, conn)

    # Recalculate position size after conviction may have changed
    base_pct = sizer._conviction_to_base(sig.get("conviction", "medium"))
    sig["suggested_position_size"] = round(min(base_pct * modifier, sizer.max_single), 4)


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
        ("eo_signal", _generate_eo_signals),
        ("reg_shock", _generate_reg_shock_signals),
        ("fomc_drift", _generate_fomc_signals),
        ("pipeline_pressure", _generate_pipeline_pressure_signals),
        ("pipeline_deadline", _generate_pipeline_deadline_signals),
    ]

    for name, gen_func in generators:
        try:
            new = gen_func(conn, macro_modifier, macro_quadrant)
            all_signals.extend(new)
            if new:
                logger.info("Signal generator [%s]: %d new signals", name, len(new))
        except Exception as e:
            logger.error("Signal generator [%s] failed: %s", name, e)

    # Enrich signals with trade parameters before insertion

    for sig in all_signals:
        _enrich_signal(sig, conn)

    # Insert signals
    inserted = []
    for sig in all_signals:
        try:
            cursor = conn.execute(
                """INSERT INTO trading_signals
                   (signal_date, ticker, signal_type, direction, conviction,
                    source_event_id, source_table, rationale,
                    macro_regime_at_signal, position_size_modifier, status,
                    stop_loss_price, take_profit_price, suggested_position_size,
                    time_horizon_days, expected_car,
                    historical_win_rate, historical_p_value, historical_n_events,
                    prediction_market_prob)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                           ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    sig.get("stop_loss_price"),
                    sig.get("take_profit_price"),
                    sig.get("suggested_position_size"),
                    sig.get("time_horizon_days"),
                    sig.get("expected_car"),
                    sig.get("historical_win_rate"),
                    sig.get("historical_p_value"),
                    sig.get("historical_n_events"),
                    sig.get("prediction_market_prob"),
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
