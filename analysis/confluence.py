"""Confluence Score Engine — multi-source convergence scoring.

For every watchlist ticker, computes a real-time Confluence Score by
aggregating signals across all data sources. The score indicates how many
independent data sources point in the same direction.

Score of 4+ = Strong confluence — multiple independent signals align
Score of 2-3 = Moderate confluence — thesis has support but gaps
Score of 0-1 = Weak confluence — insufficient evidence
"""

import logging
import sqlite3
from datetime import date, timedelta

from config import DB_PATH

logger = logging.getLogger(__name__)


def compute_confluence(ticker: str, conn: sqlite3.Connection = None) -> dict:
    """Compute confluence score for a ticker across all data sources.

    Args:
        ticker: Stock ticker symbol.
        conn: Optional DB connection. If None, creates one.

    Returns:
        Dict with keys: ticker, score, direction, factors, strength
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        return _compute(ticker, conn)
    finally:
        if close_conn:
            conn.close()


def _compute(ticker: str, conn: sqlite3.Connection) -> dict:
    today_str = date.today().isoformat()
    factors = []
    directional_score = 0  # positive = long, negative = short

    # ── 1. Macro regime favors sector ─────────────────────────────────
    try:
        regime_row = conn.execute(
            "SELECT quadrant FROM macro_regimes ORDER BY date DESC LIMIT 1"
        ).fetchone()
        sector_row = conn.execute(
            "SELECT sector FROM watchlist WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone()

        if regime_row and sector_row:
            from analysis.macro_regime import QUADRANTS
            q_info = QUADRANTS.get(regime_row[0], {})
            favored = q_info.get("favored_sectors", [])
            avoided = q_info.get("avoid_sectors", [])

            sector_etf_map = {
                "Technology": "XLK", "Consumer Discretionary": "XLY",
                "Energy": "XLE", "Materials": "XLB", "Financials": "XLF",
                "Industrials": "XLI", "Consumer Staples": "XLP",
                "Utilities": "XLU", "Healthcare": "XLV",
                "Defense": "XLI", "Aerospace": "XLI",
                "Real Estate": "XLRE",
            }
            sector = sector_row[0] or ""
            etf = sector_etf_map.get(sector, "")

            if etf in favored:
                directional_score += 1
                factors.append({
                    "source": "Macro Regime",
                    "signal": f"Regime favors {sector}",
                    "contributing": True,
                    "direction": "long",
                })
            elif etf in avoided:
                directional_score -= 1
                factors.append({
                    "source": "Macro Regime",
                    "signal": f"Regime disfavors {sector}",
                    "contributing": True,
                    "direction": "short",
                })
            else:
                factors.append({
                    "source": "Macro Regime",
                    "signal": f"{sector} neutral in current regime",
                    "contributing": False,
                    "direction": "neutral",
                })
        else:
            factors.append({
                "source": "Macro Regime",
                "signal": "No regime data",
                "contributing": False,
                "direction": "neutral",
            })
    except Exception:
        logger.exception("Confluence: failed to check Macro Regime for %s", ticker)
        factors.append({
            "source": "Macro Regime",
            "signal": "Unable to check",
            "contributing": False,
            "direction": "neutral",
        })

    # ── 2. Active trading signal ──────────────────────────────────────
    try:
        sig_row = conn.execute(
            """SELECT direction, conviction FROM trading_signals
               WHERE ticker = ? AND status IN ('pending', 'active')
               ORDER BY CASE conviction WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
               LIMIT 1""",
            (ticker,),
        ).fetchone()

        if sig_row:
            direction, conviction = sig_row
            weight = 2 if conviction == "high" else 1
            if direction == "long":
                directional_score += weight
            elif direction == "short":
                directional_score -= weight
            factors.append({
                "source": "Trading Signal",
                "signal": f"{direction.upper()} ({conviction})",
                "contributing": True,
                "direction": direction,
            })
        else:
            factors.append({
                "source": "Trading Signal",
                "signal": "No active signal",
                "contributing": False,
                "direction": "neutral",
            })
    except Exception:
        logger.exception("Confluence: failed to check Trading Signal for %s", ticker)
        factors.append({
            "source": "Trading Signal",
            "signal": "Unable to check",
            "contributing": False,
            "direction": "neutral",
        })

    # ── 3. Lobbying spend trending up >15% QoQ ───────────────────────
    try:
        lob_rows = conn.execute(
            """SELECT SUM(amount) FROM lobbying_filings
               WHERE client_ticker = ?
               GROUP BY filing_year, filing_period
               ORDER BY filing_year DESC, filing_period DESC
               LIMIT 2""",
            (ticker,),
        ).fetchall()

        if len(lob_rows) >= 2 and lob_rows[0][0] and lob_rows[1][0] and lob_rows[1][0] > 0:
            qoq = (lob_rows[0][0] - lob_rows[1][0]) / lob_rows[1][0]
            if qoq > 0.15:
                directional_score += 1
                factors.append({
                    "source": "Lobbying Spend",
                    "signal": f"Up {qoq:+.0%} QoQ",
                    "contributing": True,
                    "direction": "long",
                })
            else:
                factors.append({
                    "source": "Lobbying Spend",
                    "signal": f"QoQ change: {qoq:+.0%}",
                    "contributing": False,
                    "direction": "neutral",
                })
        else:
            factors.append({
                "source": "Lobbying Spend",
                "signal": "Insufficient data",
                "contributing": False,
                "direction": "neutral",
            })
    except Exception:
        logger.exception("Confluence: failed to check Lobbying Spend for %s", ticker)
        factors.append({
            "source": "Lobbying Spend",
            "signal": "No data",
            "contributing": False,
            "direction": "neutral",
        })

    # ── 4. Recent high-impact regulatory event ────────────────────────
    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
    try:
        reg_row = conn.execute(
            """SELECT event_type, impact_score, title FROM regulatory_events
               WHERE tickers LIKE ? AND impact_score >= 4
                 AND publication_date >= ?
               ORDER BY impact_score DESC LIMIT 1""",
            (f"%{ticker}%", seven_days_ago),
        ).fetchone()

        if reg_row:
            # Direction based on event type
            event_type = reg_row[0]
            if event_type in ("final_rule", "bill_signed"):
                directional_score += 1
                direction = "long"
            elif event_type in ("proposed_rule",):
                direction = "neutral"
            else:
                directional_score += 1
                direction = "long"
            factors.append({
                "source": "Regulatory Event",
                "signal": f"Impact {reg_row[1]}: {reg_row[2][:50]}",
                "contributing": True,
                "direction": direction,
            })
        else:
            factors.append({
                "source": "Regulatory Event",
                "signal": "No recent high-impact events",
                "contributing": False,
                "direction": "neutral",
            })
    except Exception:
        logger.exception("Confluence: failed to check Regulatory Event for %s", ticker)
        factors.append({
            "source": "Regulatory Event",
            "signal": "Unable to check",
            "contributing": False,
            "direction": "neutral",
        })

    # ── 5. Congressional insider buying ───────────────────────────────
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()
    try:
        buy_count = conn.execute(
            """SELECT COUNT(*) FROM congress_trades
               WHERE ticker = ? AND trade_type = 'purchase'
                 AND trade_date >= ?""",
            (ticker, ninety_days_ago),
        ).fetchone()[0]

        sell_count = conn.execute(
            """SELECT COUNT(*) FROM congress_trades
               WHERE ticker = ? AND trade_type = 'sale'
                 AND trade_date >= ?""",
            (ticker, ninety_days_ago),
        ).fetchone()[0]

        if buy_count > sell_count and buy_count > 0:
            directional_score += 1
            factors.append({
                "source": "Congress Trades",
                "signal": f"{buy_count} buy(s) vs {sell_count} sell(s) in 90d",
                "contributing": True,
                "direction": "long",
            })
        elif sell_count > buy_count and sell_count > 0:
            directional_score -= 1
            factors.append({
                "source": "Congress Trades",
                "signal": f"{sell_count} sell(s) vs {buy_count} buy(s) in 90d",
                "contributing": True,
                "direction": "short",
            })
        else:
            factors.append({
                "source": "Congress Trades",
                "signal": "No recent trades" if buy_count == 0 else "Mixed signals",
                "contributing": False,
                "direction": "neutral",
            })
    except Exception:
        logger.exception("Confluence: failed to check Congress Trades for %s", ticker)
        factors.append({
            "source": "Congress Trades",
            "signal": "No data",
            "contributing": False,
            "direction": "neutral",
        })

    # ── 6. Prediction market supports thesis ──────────────────────────
    try:
        pred_row = conn.execute(
            """SELECT current_price, question_text FROM prediction_markets
               WHERE related_ticker = ? AND current_price IS NOT NULL
               ORDER BY volume DESC LIMIT 1""",
            (ticker,),
        ).fetchone()

        if pred_row and pred_row[0] is not None:
            prob = pred_row[0]
            if prob > 0.6:
                directional_score += 1
                factors.append({
                    "source": "Prediction Market",
                    "signal": f"{prob:.0%} — {pred_row[1][:40]}",
                    "contributing": True,
                    "direction": "long",
                })
            else:
                factors.append({
                    "source": "Prediction Market",
                    "signal": f"{prob:.0%} — {pred_row[1][:40]}",
                    "contributing": False,
                    "direction": "neutral",
                })
        else:
            factors.append({
                "source": "Prediction Market",
                "signal": "No related contracts",
                "contributing": False,
                "direction": "neutral",
            })
    except Exception:
        logger.exception("Confluence: failed to check Prediction Market for %s", ticker)
        factors.append({
            "source": "Prediction Market",
            "signal": "No data",
            "contributing": False,
            "direction": "neutral",
        })

    # ── 7. FDA catalyst upcoming ──────────────────────────────────────
    thirty_days = (date.today() + timedelta(days=30)).isoformat()
    try:
        fda_row = conn.execute(
            """SELECT event_type, drug_name, event_date FROM fda_events
               WHERE ticker = ? AND event_date >= ? AND event_date <= ?
                 AND event_type IN ('adcom_vote', 'pdufa_date', 'approval')
               ORDER BY event_date LIMIT 1""",
            (ticker, today_str, thirty_days),
        ).fetchone()

        if fda_row:
            drug = fda_row[1] or "unknown"
            days_until = (date.fromisoformat(fda_row[2]) - date.today()).days
            directional_score += 1
            factors.append({
                "source": "FDA Catalyst",
                "signal": f"{fda_row[0].replace('_', ' ')} for {drug} in {days_until}d",
                "contributing": True,
                "direction": "long",
            })
        else:
            factors.append({
                "source": "FDA Catalyst",
                "signal": "No upcoming events",
                "contributing": False,
                "direction": "neutral",
            })
    except Exception:
        logger.exception("Confluence: failed to check FDA Catalyst for %s", ticker)
        factors.append({
            "source": "FDA Catalyst",
            "signal": "No data",
            "contributing": False,
            "direction": "neutral",
        })

    # ── Compute final score ───────────────────────────────────────────
    abs_score = abs(directional_score)
    contributing_count = sum(1 for f in factors if f["contributing"])

    if abs_score >= 4:
        strength = "strong"
    elif abs_score >= 2:
        strength = "moderate"
    else:
        strength = "weak"

    if directional_score > 0:
        direction = "long"
    elif directional_score < 0:
        direction = "short"
    else:
        direction = "neutral"

    return {
        "ticker": ticker,
        "score": contributing_count,
        "directional_score": directional_score,
        "direction": direction,
        "factors": factors,
        "strength": strength,
    }
