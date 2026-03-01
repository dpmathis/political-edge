"""Email Alert Engine.

Evaluates alert rules after each collector run and sends email notifications
for matching events. Uses SMTP (Gmail) for delivery.

Usage:
    from analysis.alert_engine import evaluate_and_send
    evaluate_and_send()
"""

import logging
import smtplib
import sqlite3
from email.mime.text import MIMEText

from config import DB_PATH, load_config

logger = logging.getLogger(__name__)


def _get_alert_config() -> dict | None:
    """Load alert configuration. Returns None if alerts are disabled or unconfigured."""
    cfg = load_config()
    alerts = cfg.get("alerts", {})

    if not alerts.get("smtp_user") or not alerts.get("smtp_password"):
        logger.debug("Alert email not configured, skipping")
        return None

    if not alerts.get("email"):
        logger.debug("No alert recipient configured, skipping")
        return None

    return alerts


def _send_email(config: dict, subject: str, body: str):
    """Send an email via SMTP."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config["smtp_user"]
    msg["To"] = config["email"]

    try:
        with smtplib.SMTP(config["smtp_server"], config["smtp_port"]) as server:
            server.starttls()
            server.login(config["smtp_user"], config["smtp_password"])
            server.send_message(msg)
        logger.info("Alert sent: %s", subject)
    except Exception as e:
        logger.error("Failed to send alert email: %s", e)


def _format_event_body(rule_name: str, rows: list[tuple], columns: list[str]) -> str:
    """Format event rows into a readable email body."""
    lines = [
        rule_name,
        "=" * 40,
        "",
    ]

    for row in rows[:10]:  # Limit to 10 events per alert
        for i, col in enumerate(columns):
            if row[i] is not None:
                lines.append(f"  {col}: {row[i]}")
        lines.append("")

    if len(rows) > 10:
        lines.append(f"  ... and {len(rows) - 10} more events")

    lines.extend([
        "",
        "=" * 40,
        "View in dashboard: https://political-edge.streamlit.app",
    ])

    return "\n".join(lines)


def _check_regime_change(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if the macro regime has changed."""
    rows = conn.execute(
        "SELECT date, quadrant, quadrant_label FROM macro_regimes ORDER BY date DESC LIMIT 2"
    ).fetchall()

    if len(rows) < 2:
        return False, ""

    current = rows[0]
    previous = rows[1]

    if current[1] != previous[1]:
        body = (
            f"Macro Regime Change Detected\n"
            f"{'=' * 40}\n\n"
            f"  Previous: Q{previous[1]} {previous[2]} ({previous[0]})\n"
            f"  Current:  Q{current[1]} {current[2]} ({current[0]})\n"
        )
        return True, body

    return False, ""


def _check_lobbying_spikes(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check for companies with >25% QoQ lobbying spend increase."""
    from collectors.lobbying import calculate_qoq_changes
    changes = calculate_qoq_changes(conn)
    spikes = [c for c in changes if c.get("spike")]

    if not spikes:
        return False, ""

    lines = [
        "Lobbying Spend Spikes (>25% QoQ)",
        "=" * 40,
        "",
    ]
    for s in spikes[:10]:
        pct = s["pct_change"]
        lines.append(
            f"  {s['ticker']} ({s['client_name']}): "
            f"${s['current_amount']:,.0f} ({pct:+.0%} QoQ) — {s['filing_year']} {s['filing_period']}"
        )

    return True, "\n".join(lines)


def evaluate_and_send() -> int:
    """Evaluate all alert rules and send notifications.

    Returns:
        Count of alerts sent.
    """
    config = _get_alert_config()
    if not config:
        return 0

    rules = config.get("rules", [])
    if not rules:
        return 0

    conn = sqlite3.connect(DB_PATH)
    alerts_sent = 0

    for rule in rules:
        name = rule.get("name", "Unknown Rule")
        table = rule.get("table", "")
        condition = rule.get("condition", "1=1")
        lookback = rule.get("lookback_hours", 24)
        is_custom = rule.get("custom_query", False)

        try:
            if is_custom:
                # Handle custom alert types
                if "Lobbying" in name:
                    has_alert, body = _check_lobbying_spikes(conn)
                elif "Regime" in name:
                    has_alert, body = _check_regime_change(conn)
                else:
                    continue

                if has_alert:
                    _send_email(config, f"[Political Edge] {name}", body)
                    alerts_sent += 1
            else:
                # Standard query-based alert
                query = (
                    f"SELECT * FROM {table} "
                    f"WHERE ({condition}) "
                    f"AND created_at >= datetime('now', '-{lookback} hours')"
                )
                cursor = conn.execute(query)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []

                if rows:
                    body = _format_event_body(name, rows, columns)
                    _send_email(config, f"[Political Edge] {name}: {len(rows)} new events", body)
                    alerts_sent += 1

        except Exception as e:
            logger.error("Error evaluating rule '%s': %s", name, e)

    conn.close()
    logger.info("Alert engine: %d alerts sent", alerts_sent)
    return alerts_sent
