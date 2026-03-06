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

    # Read user preferences from DB (override config.yaml)
    prefs = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        for row in conn.execute("SELECT key, value FROM user_preferences").fetchall():
            prefs[row[0]] = row[1]
        conn.close()
    except Exception:
        pass

    # User can disable alerts
    if prefs.get("alert_enabled") == "false":
        logger.debug("Alerts disabled by user preference")
        return None

    # User email overrides config
    if prefs.get("alert_email"):
        alerts["email"] = prefs["alert_email"]

    if not alerts.get("smtp_user") or not alerts.get("smtp_password"):
        logger.debug("Alert email not configured, skipping")
        return None

    if not alerts.get("email"):
        logger.debug("No alert recipient configured, skipping")
        return None

    # Filter rules based on user preferences
    if "alert_rules" in prefs:
        import json
        try:
            enabled_rules = json.loads(prefs["alert_rules"])
            alerts["rules"] = [
                r for r in alerts.get("rules", [])
                if enabled_rules.get(r.get("name", ""), True)
            ]
        except (json.JSONDecodeError, TypeError):
            pass

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
        f"View in dashboard: {load_config().get('dashboard', {}).get('url', 'https://political-edge.streamlit.app')}",
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


def _check_high_conviction_signals(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check for high-conviction trading signals generated in the last 24 hours."""
    rows = conn.execute(
        """SELECT signal_date, ticker, signal_type, direction, rationale
           FROM trading_signals
           WHERE conviction = 'high'
             AND created_at >= datetime('now', '-24 hours')
             AND status = 'pending'
           ORDER BY signal_date DESC"""
    ).fetchall()

    if not rows:
        return False, ""

    lines = [
        "High-Conviction Trading Signals",
        "=" * 40,
        "",
    ]
    for row in rows[:10]:
        lines.append(f"  {row[1]} ({row[2]}): {row[3]}")
        lines.append(f"    Date: {row[0]}")
        if row[4]:
            lines.append(f"    Rationale: {row[4][:120]}")
        lines.append("")

    if len(rows) > 10:
        lines.append(f"  ... and {len(rows) - 10} more signals")

    return True, "\n".join(lines)


def _check_pipeline_deadlines(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check for pipeline rules with comment deadlines approaching within 7 days."""
    rows = conn.execute(
        """SELECT pr.proposed_title, pr.agency, pr.comment_deadline, pr.tickers, pr.impact_score
           FROM pipeline_rules pr
           WHERE pr.status IN ('proposed', 'in_comment')
             AND pr.comment_deadline BETWEEN date('now') AND date('now', '+7 days')
             AND pr.impact_score >= 3
           ORDER BY pr.comment_deadline"""
    ).fetchall()

    if not rows:
        return False, ""

    lines = [
        "Pipeline Deadlines Approaching (next 7 days)",
        "=" * 40,
        "",
    ]
    for row in rows[:10]:
        lines.append(f"  {row[0][:80]}")
        lines.append(f"    Agency: {row[1]} | Deadline: {row[2]} | Tickers: {row[3] or 'N/A'}")
        lines.append("")

    if len(rows) > 10:
        lines.append(f"  ... and {len(rows) - 10} more rules")

    return True, "\n".join(lines)


def _check_data_staleness(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Check if any key data source is more than its threshold days stale."""
    from datetime import date as date_cls, datetime as dt_cls

    checks = [
        ("regulatory_events", "publication_date", 3),
        ("market_data", "date", 2),
        ("macro_indicators", "date", 7),
        ("trading_signals", "signal_date", 3),
    ]
    stale = []
    for table, col, max_days in checks:
        try:
            row = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
            if row and row[0]:
                latest = dt_cls.strptime(str(row[0])[:10], "%Y-%m-%d").date()
                days_old = (date_cls.today() - latest).days
                if days_old > max_days:
                    stale.append(f"  {table}: {days_old} days old (max {max_days})")
        except Exception:
            pass

    if not stale:
        return False, ""

    body = (
        "Data Staleness Warning\n"
        + "=" * 40
        + "\n\n"
        + "\n".join(stale)
        + "\n\n"
        + "Check collector logs and ensure the daily pipeline is running."
    )
    return True, body


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
                elif "Conviction" in name or "Signal" in name:
                    has_alert, body = _check_high_conviction_signals(conn)
                elif "Pipeline" in name or "Deadline" in name:
                    has_alert, body = _check_pipeline_deadlines(conn)
                elif "Stale" in name or "Staleness" in name:
                    has_alert, body = _check_data_staleness(conn)
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


def dry_run_alerts() -> list[dict]:
    """Evaluate all alert rules without sending emails.

    Returns list of {"rule_name": str, "body": str, "event_count": int}
    for each rule that would fire.
    """
    cfg = load_config()
    alerts_cfg = cfg.get("alerts", {})
    rules = alerts_cfg.get("rules", [])

    if not rules:
        return []

    # Apply user preference filters (same as _get_alert_config but skip SMTP requirement)
    prefs = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        for row in conn.execute("SELECT key, value FROM user_preferences").fetchall():
            prefs[row[0]] = row[1]
        conn.close()
    except Exception:
        pass

    if prefs.get("alert_enabled") == "false":
        return []

    if "alert_rules" in prefs:
        import json
        try:
            enabled_rules = json.loads(prefs["alert_rules"])
            rules = [
                r for r in rules
                if enabled_rules.get(r.get("name", ""), True)
            ]
        except (json.JSONDecodeError, TypeError):
            pass

    conn = sqlite3.connect(DB_PATH)
    fired = []

    for rule in rules:
        name = rule.get("name", "Unknown Rule")
        table = rule.get("table", "")
        condition = rule.get("condition", "1=1")
        lookback = rule.get("lookback_hours", 24)
        is_custom = rule.get("custom_query", False)

        try:
            if is_custom:
                if "Lobbying" in name:
                    try:
                        has_alert, body = _check_lobbying_spikes(conn)
                    except Exception:
                        continue
                elif "Regime" in name:
                    has_alert, body = _check_regime_change(conn)
                elif "Conviction" in name or "Signal" in name:
                    has_alert, body = _check_high_conviction_signals(conn)
                elif "Pipeline" in name or "Deadline" in name:
                    has_alert, body = _check_pipeline_deadlines(conn)
                elif "Stale" in name or "Staleness" in name:
                    has_alert, body = _check_data_staleness(conn)
                else:
                    continue

                if has_alert:
                    fired.append({
                        "rule_name": name,
                        "body": body,
                        "event_count": body.count("\n\n"),
                    })
            else:
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
                    fired.append({
                        "rule_name": name,
                        "body": body,
                        "event_count": len(rows),
                    })
        except Exception as e:
            logger.error("Error in dry-run for rule '%s': %s", name, e)

    conn.close()
    return fired
