"""Signal Card — the atomic unit of trading intelligence.

Each card is self-contained: ticker, direction, conviction, rationale,
entry/stop/TP, historical stats, and cross-source evidence checklist.
"""

import sqlite3
from datetime import date, timedelta

import streamlit as st

from config import DB_PATH
from dashboard.components.color_system import (
    CONVICTION_COLORS,
    DIRECTION_COLORS,
    render_conviction_bar,
    render_direction_badge,
)


def _get_supporting_evidence(ticker: str, conn: sqlite3.Connection) -> dict:
    """Check all data sources for supporting evidence on a ticker.

    Returns dict of {source: {"contributing": bool, "detail": str}}.
    """
    evidence = {}
    today_str = date.today().isoformat()

    # 1. Macro regime favors sector
    try:
        regime_row = conn.execute(
            "SELECT quadrant FROM macro_regimes ORDER BY date DESC LIMIT 1"
        ).fetchone()
        sector_row = conn.execute(
            "SELECT sector FROM watchlist WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone()
        if regime_row and sector_row:
            from analysis.macro_regime import QUADRANTS
            favored = QUADRANTS.get(regime_row[0], {}).get("favored_sectors", [])
            # Map sector names to ETF tickers for comparison
            sector_etf_map = {
                "Technology": "XLK", "Consumer Discretionary": "XLY",
                "Energy": "XLE", "Materials": "XLB", "Financials": "XLF",
                "Industrials": "XLI", "Consumer Staples": "XLP",
                "Utilities": "XLU", "Healthcare": "XLV",
                "Defense": "XLI", "Aerospace": "XLI",
            }
            sector = sector_row[0] or ""
            etf = sector_etf_map.get(sector, "")
            is_favored = etf in favored
            evidence["Macro regime"] = {
                "contributing": is_favored,
                "detail": f"Regime favors {sector}" if is_favored else f"{sector} not in favored sectors",
            }
        else:
            evidence["Macro regime"] = {"contributing": False, "detail": "No regime data"}
    except Exception:
        evidence["Macro regime"] = {"contributing": False, "detail": "No regime data"}

    # 2. Lobbying spend trending up
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
            evidence["Lobbying spend"] = {
                "contributing": qoq > 0.15,
                "detail": f"QoQ change: {qoq:+.0%}",
            }
        else:
            evidence["Lobbying spend"] = {"contributing": False, "detail": "Insufficient data"}
    except Exception:
        evidence["Lobbying spend"] = {"contributing": False, "detail": "No lobbying data"}

    # 3. Congressional insiders buying
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()
    try:
        buy_count = conn.execute(
            """SELECT COUNT(*) FROM congress_trades
               WHERE ticker = ? AND trade_type = 'purchase'
                 AND trade_date >= ?""",
            (ticker, ninety_days_ago),
        ).fetchone()[0]
        evidence["Congress buying"] = {
            "contributing": buy_count > 0,
            "detail": f"{buy_count} purchase(s) in 90 days" if buy_count else "No recent purchases",
        }
    except Exception:
        evidence["Congress buying"] = {"contributing": False, "detail": "No trade data"}

    # 4. Prediction market supports
    try:
        pred_row = conn.execute(
            """SELECT current_price, question_text FROM prediction_markets
               WHERE related_ticker = ? AND current_price IS NOT NULL
               ORDER BY volume DESC LIMIT 1""",
            (ticker,),
        ).fetchone()
        if pred_row:
            evidence["Prediction market"] = {
                "contributing": pred_row[0] > 0.6,
                "detail": f"{pred_row[0]:.0%} — {pred_row[1][:50]}",
            }
        else:
            evidence["Prediction market"] = {"contributing": False, "detail": "No related contracts"}
    except Exception:
        evidence["Prediction market"] = {"contributing": False, "detail": "No market data"}

    # 5. FDA catalyst upcoming
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
            drug = fda_row[1] or "unknown drug"
            evidence["FDA catalyst"] = {
                "contributing": True,
                "detail": f"{fda_row[0]} for {drug} on {fda_row[2]}",
            }
        else:
            evidence["FDA catalyst"] = {"contributing": False, "detail": "No upcoming events"}
    except Exception:
        evidence["FDA catalyst"] = {"contributing": False, "detail": "No FDA data"}

    return evidence


def render_signal_card(signal: dict, show_evidence: bool = True, conn: sqlite3.Connection = None):
    """Render a self-contained signal card.

    Args:
        signal: Dict or Series with signal data (ticker, direction, conviction,
                signal_type, rationale, entry_price, stop_loss_price,
                take_profit_price, historical_win_rate, etc.)
        show_evidence: Whether to show the cross-source evidence checklist.
        conn: Optional DB connection. If None, creates one.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        _render_card_html(signal, show_evidence, conn)
    finally:
        if close_conn:
            conn.close()


def _render_card_html(signal: dict, show_evidence: bool, conn: sqlite3.Connection):
    """Internal: build and render the signal card HTML."""
    ticker = signal.get("ticker", "???")
    direction = (signal.get("direction") or "watch").lower()
    conviction = (signal.get("conviction") or "low").lower()
    signal_type = (signal.get("signal_type") or "unknown").replace("_", " ").title()
    rationale = signal.get("rationale") or ""
    signal_date = signal.get("signal_date", "")

    dir_color = DIRECTION_COLORS.get(direction, DIRECTION_COLORS["neutral"])
    _ = CONVICTION_COLORS.get(conviction, CONVICTION_COLORS["low"])

    # Direction badge and conviction bar HTML
    dir_badge = render_direction_badge(direction)
    conv_bar = render_conviction_bar(conviction)

    # Price levels
    entry = signal.get("entry_price")
    stop = signal.get("stop_loss_price")
    tp = signal.get("take_profit_price")
    horizon = signal.get("time_horizon_days")

    price_html = ""
    if entry or stop or tp:
        parts = []
        if entry:
            parts.append(f"Entry: <b>${float(entry):,.2f}</b>")
        if stop:
            parts.append(f"Stop: <b>${float(stop):,.2f}</b>")
        if tp:
            parts.append(f"TP: <b>${float(tp):,.2f}</b>")
        if horizon:
            parts.append(f"Horizon: <b>{horizon}d</b>")
        price_html = (
            f'<div style="margin-top:8px; padding:6px 10px; background:#f8fafc; '
            f'border-radius:6px; font-size:13px; color:#475569;">'
            f'{" &nbsp;|&nbsp; ".join(parts)}'
            f'</div>'
        )

    # Historical performance
    win_rate = signal.get("historical_win_rate")
    car = signal.get("expected_car")
    p_val = signal.get("historical_p_value")
    n_events = signal.get("historical_n_events")

    def _valid(v):
        """Check if a value is non-None and not NaN."""
        if v is None:
            return False
        try:
            return v == v  # NaN != NaN
        except (TypeError, ValueError):
            return True

    hist_html = ""
    if _valid(win_rate) or _valid(car):
        parts = []
        if _valid(car):
            parts.append(f"{float(car):+.1%} avg return")
        if _valid(win_rate):
            parts.append(f"{float(win_rate):.0%} win rate")
        if _valid(n_events):
            parts.append(f"N={int(n_events)}")
        if _valid(p_val):
            sig = "p<0.01" if p_val < 0.01 else "p<0.05" if p_val < 0.05 else "p<0.10" if p_val < 0.10 else f"p={p_val:.2f}"
            parts.append(sig)
        hist_html = (
            f'<div style="margin-top:6px; font-size:12px; color:#64748b;">'
            f'Historical: {", ".join(parts)}'
            f'</div>'
        )

    # Evidence checklist
    evidence_html = ""
    if show_evidence:
        evidence = _get_supporting_evidence(ticker, conn)
        if evidence:
            items = []
            for source, info in evidence.items():
                check = "✓" if info["contributing"] else "✗"
                check_color = "#22c55e" if info["contributing"] else "#94a3b8"
                items.append(
                    f'<div style="font-size:12px; color:{check_color}; margin:2px 0;">'
                    f'{check} {source}: {info["detail"]}'
                    f'</div>'
                )
            evidence_html = (
                f'<div style="margin-top:10px; padding-top:8px; border-top:1px solid #e2e8f0;">'
                f'<div style="font-size:11px; font-weight:600; color:#94a3b8; margin-bottom:4px;">SUPPORTING EVIDENCE</div>'
                f'{"".join(items)}'
                f'</div>'
            )

    # Assemble card
    card_html = f"""
    <div style="border:1px solid {dir_color}33; border-left:4px solid {dir_color};
                border-radius:8px; padding:16px; margin-bottom:12px;
                background:white;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <div style="display:flex; align-items:center; gap:10px;">
                {dir_badge}
                <span style="font-size:20px; font-weight:bold;">{ticker}</span>
            </div>
            <div>{conv_bar}</div>
        </div>
        <div style="font-size:14px; font-weight:500; color:#334155; margin-bottom:4px;">
            {signal_type}
        </div>
        <div style="font-size:13px; color:#64748b; line-height:1.5;">
            {rationale[:200]}{"..." if len(rationale) > 200 else ""}
        </div>
        {price_html}
        {hist_html}
        {evidence_html}
        <div style="font-size:11px; color:#94a3b8; margin-top:8px; text-align:right;">
            {signal_date}
        </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)
