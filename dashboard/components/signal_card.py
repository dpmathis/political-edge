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


_NO_DATA_PHRASES = (
    "No regime data", "No data", "No lobbying data", "No trade data",
    "No market data", "No FDA data", "Insufficient data", "Unable to check",
    "No related contracts", "No upcoming events", "No recent purchases",
)


def _is_no_data(detail: str) -> bool:
    """Check if a detail string indicates missing data (vs. a negative signal)."""
    return any(phrase in detail for phrase in _NO_DATA_PHRASES)


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
        _render_card(signal, show_evidence, conn)
    finally:
        if close_conn:
            conn.close()


def _valid(v):
    """Check if a value is non-None and not NaN."""
    if v is None:
        return False
    try:
        return v == v  # NaN != NaN
    except (TypeError, ValueError):
        return True


def _render_card(signal: dict, show_evidence: bool, conn: sqlite3.Connection):
    """Build and render the signal card: narrative card + expandable details."""
    ticker = signal.get("ticker", "???")
    direction = (signal.get("direction") or "watch").lower()
    conviction = (signal.get("conviction") or "low").lower()
    signal_type = (signal.get("signal_type") or "unknown").replace("_", " ").title()
    rationale = signal.get("rationale") or ""
    signal_date = signal.get("signal_date", "")

    dir_color = DIRECTION_COLORS.get(direction, DIRECTION_COLORS["neutral"])

    # Direction badge and conviction bar HTML
    dir_badge = render_direction_badge(direction)
    conv_bar = render_conviction_bar(conviction)

    # Main card: header + full narrative
    card_html = f"""
    <div style="border:1px solid {dir_color}33; border-left:4px solid {dir_color};
                border-radius:8px; padding:16px; margin-bottom:4px;
                background:white;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <div style="display:flex; align-items:center; gap:10px;">
                {dir_badge}
                <span style="font-size:20px; font-weight:bold;">{ticker}</span>
                <span style="font-size:12px; color:#94a3b8;">{signal_type}</span>
            </div>
            <div>{conv_bar}</div>
        </div>
        <div style="font-size:14px; color:#334155; line-height:1.6;">
            {rationale}
        </div>
        <div style="font-size:11px; color:#94a3b8; margin-top:8px; text-align:right;">
            {signal_date}
        </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    # Expandable details section
    has_prices = signal.get("entry_price") or signal.get("stop_loss_price") or signal.get("take_profit_price")
    has_stats = _valid(signal.get("historical_win_rate")) or _valid(signal.get("expected_car"))

    if has_prices or has_stats or show_evidence:
        with st.expander("Trade Details", expanded=False):
            # Price levels
            entry = signal.get("entry_price")
            stop = signal.get("stop_loss_price")
            tp = signal.get("take_profit_price")
            horizon = signal.get("time_horizon_days")

            if entry or stop or tp or horizon:
                price_cols = st.columns(4)
                with price_cols[0]:
                    st.metric("Entry", f"${float(entry):,.2f}" if entry else "—")
                with price_cols[1]:
                    st.metric("Stop Loss", f"${float(stop):,.2f}" if stop else "—")
                with price_cols[2]:
                    st.metric("Take Profit", f"${float(tp):,.2f}" if tp else "—")
                with price_cols[3]:
                    st.metric("Horizon", f"{horizon} days" if horizon else "—")

            # Historical stats
            win_rate = signal.get("historical_win_rate")
            car = signal.get("expected_car")
            p_val = signal.get("historical_p_value")
            n_events = signal.get("historical_n_events")

            if _valid(win_rate) or _valid(car):
                stat_cols = st.columns(4)
                with stat_cols[0]:
                    st.metric("Avg Return", f"{float(car):+.1%}" if _valid(car) else "—",
                              help="Historical cumulative abnormal return for this signal type")
                with stat_cols[1]:
                    st.metric("Win Rate", f"{float(win_rate):.0%}" if _valid(win_rate) else "—",
                              help="Percentage of times this signal type made money")
                with stat_cols[2]:
                    st.metric("Sample Size", f"N={int(n_events)}" if _valid(n_events) else "—")
                with stat_cols[3]:
                    if _valid(p_val):
                        sig = "Significant" if p_val < 0.05 else "Not significant"
                        st.metric("p-value", f"{p_val:.3f}", delta=sig,
                                  delta_color="normal" if p_val < 0.05 else "off",
                                  help="Statistical significance — below 0.05 means likely not random")
                    else:
                        st.metric("p-value", "—")

            # Evidence checklist
            if show_evidence:
                evidence = _get_supporting_evidence(ticker, conn)
                if evidence:
                    st.markdown(
                        '<div style="font-size:11px; font-weight:600; color:#94a3b8; '
                        'margin-top:8px; margin-bottom:4px;">SUPPORTING EVIDENCE</div>',
                        unsafe_allow_html=True,
                    )
                    for source, info in evidence.items():
                        if info["contributing"]:
                            icon, color = "✓", "#22c55e"
                        elif _is_no_data(info["detail"]):
                            icon, color = "—", "#cbd5e1"
                        else:
                            icon, color = "✗", "#e87979"
                        st.markdown(
                            f'<div style="font-size:12px; color:{color}; margin:2px 0;">'
                            f'{icon} {source}: {info["detail"]}'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
