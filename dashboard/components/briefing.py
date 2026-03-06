"""Daily Briefing Generator — template-based, no LLM dependency.

Synthesizes macro regime, active signals, upcoming catalysts, and prediction
market data into a 2-3 sentence plain-English daily outlook.
"""

import sqlite3
from datetime import date, timedelta

import streamlit as st

from config import DB_PATH

# Regime narratives
_REGIME_OUTLOOK = {
    1: ("bullish", "growth is accelerating while inflation cools (Goldilocks)"),
    2: ("cautiously bullish", "both growth and inflation are accelerating (Reflation)"),
    3: ("defensive", "growth is slowing while inflation rises (Stagflation)"),
    4: ("risk-off", "both growth and inflation are decelerating (Deflation)"),
}


def generate_briefing(conn: sqlite3.Connection = None) -> str:
    """Generate a plain-English daily briefing from current data.

    Returns a markdown string ready for st.markdown().
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        return _build_briefing(conn)
    finally:
        if close_conn:
            conn.close()


def _build_briefing(conn: sqlite3.Connection) -> str:
    today = date.today()
    sentences = []

    # 1. Macro regime
    regime_text = ""
    try:
        regime = conn.execute(
            """SELECT quadrant, quadrant_label, confidence, position_size_modifier
               FROM macro_regimes ORDER BY date DESC LIMIT 1"""
        ).fetchone()
        if regime:
            q, label, confidence, modifier = regime
            outlook, desc = _REGIME_OUTLOOK.get(q, ("mixed", "conditions are unclear"))
            conf_str = f" ({confidence} confidence)" if confidence else ""
            regime_text = f"We're in a **{label}** regime{conf_str} — {desc}."
            sentences.append(f"**{today.strftime('%B %d, %Y')} — {outlook.title()} Tilt.**")
            sentences.append(regime_text)
    except Exception:
        sentences.append(f"**{today.strftime('%B %d, %Y')}**")

    # 2. Active signals summary
    try:
        sig_rows = conn.execute(
            """SELECT direction, COUNT(*) as cnt FROM trading_signals
               WHERE status IN ('pending', 'active')
               GROUP BY direction"""
        ).fetchall()
        if sig_rows:
            total = sum(r[1] for r in sig_rows)
            dir_counts = {r[0]: r[1] for r in sig_rows}
            long_count = dir_counts.get("long", 0)
            short_count = dir_counts.get("short", 0)

            if total == 0:
                pass
            elif long_count > 0 and short_count == 0:
                sentences.append(f"You have **{total} active signal{'s' if total > 1 else ''}**, all long-biased.")
            elif short_count > 0 and long_count == 0:
                sentences.append(f"You have **{total} active signal{'s' if total > 1 else ''}**, all short-biased.")
            else:
                sentences.append(
                    f"You have **{total} active signal{'s' if total > 1 else ''}** "
                    f"({long_count} long, {short_count} short)."
                )
    except Exception:
        pass

    # 3. Upcoming catalysts in next 48 hours
    two_days = (today + timedelta(days=2)).isoformat()
    today_str = today.isoformat()
    catalyst_parts = []

    # FDA
    try:
        fda = conn.execute(
            """SELECT ticker, drug_name, event_type, event_date FROM fda_events
               WHERE event_date >= ? AND event_date <= ?
                 AND event_type IN ('adcom_vote', 'pdufa_date', 'approval')
               ORDER BY event_date LIMIT 3""",
            (today_str, two_days),
        ).fetchall()
        for row in fda:
            ticker = row[0] or ""
            _ = row[1] or "a drug"
            days = (date.fromisoformat(row[3]) - today).days
            catalyst_parts.append(f"{ticker} has a {row[2].replace('_', ' ')} in {days} day{'s' if days != 1 else ''}")
    except Exception:
        pass

    # FOMC
    try:
        fomc = conn.execute(
            """SELECT event_date FROM fomc_events
               WHERE event_date >= ? AND event_date <= ?
               LIMIT 1""",
            (today_str, (today + timedelta(days=7)).isoformat()),
        ).fetchone()
        if fomc:
            days = (date.fromisoformat(fomc[0]) - today).days
            catalyst_parts.append(f"FOMC meeting in {days} day{'s' if days != 1 else ''}")
    except Exception:
        pass

    if catalyst_parts:
        sentences.append(f"Catalysts ahead: {'; '.join(catalyst_parts)}.")

    # 4. Prediction market highlight
    try:
        pred = conn.execute(
            """SELECT question_text, current_price FROM prediction_markets
               WHERE category = 'fomc' AND current_price IS NOT NULL
               ORDER BY volume DESC LIMIT 1"""
        ).fetchone()
        if pred and pred[1]:
            prob = pred[1]
            q_text = pred[0] or ""
            if "no change" in q_text.lower() or "hold" in q_text.lower():
                sentences.append(f"Prediction markets price a Fed hold at {prob:.0%}.")
    except Exception:
        pass

    if not sentences:
        sentences = [
            f"**{today.strftime('%B %d, %Y')}**",
            "No data available yet. Run data collection from the Settings page to populate your briefing.",
        ]

    return " ".join(sentences)


def _md_to_html(text: str) -> str:
    """Convert basic markdown bold/italic to HTML for embedding in HTML blocks."""
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    return text


def render_briefing(conn: sqlite3.Connection = None):
    """Render the daily briefing as a styled banner."""
    text = generate_briefing(conn)
    html_text = _md_to_html(text)
    st.markdown(
        f"""
        <div style="background:linear-gradient(135deg, #1e293b 0%, #334155 100%);
                    border-radius:12px; padding:24px 28px; margin-bottom:20px;
                    color:#f1f5f9; line-height:1.6; font-size:15px;">
            {html_text}
        </div>
        """,
        unsafe_allow_html=True,
    )
