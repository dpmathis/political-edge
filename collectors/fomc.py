"""FOMC Meeting Tracker.

Tracks FOMC meetings, scrapes statements from the Federal Reserve website,
calculates hawkish/dovish scores, and records SPX returns around announcements.

Usage:
    from collectors import fomc
    fomc.collect()
"""

import difflib
import logging
import re
import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from config import DB_PATH, load_fomc_dates

logger = logging.getLogger(__name__)

HAWKISH_WORDS = [
    "inflation", "overheating", "tightening", "restrictive", "elevated",
    "price stability", "vigilant", "upside risks", "further increases",
    "reducing", "balance sheet reduction", "stronger than expected",
]
DOVISH_WORDS = [
    "accommodation", "supportive", "patient", "gradual", "downside risks",
    "monitoring", "data dependent", "slowing", "easing", "lower",
    "maximum employment", "below target", "moderate",
]

USER_AGENT = "PoliticalEdge/1.0 (research tool)"


def score_hawkish_dovish(text: str) -> float:
    """Score text from -1.0 (very dovish) to +1.0 (very hawkish)."""
    text_lower = text.lower()
    hawk_count = sum(1 for w in HAWKISH_WORDS if w in text_lower)
    dove_count = sum(1 for w in DOVISH_WORDS if w in text_lower)
    total = hawk_count + dove_count
    if total == 0:
        return 0.0
    return (hawk_count - dove_count) / total


def _scrape_fomc_calendar() -> list[dict]:
    """Scrape FOMC calendar page for meeting links and statements."""
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            logger.error("HTTP error fetching FOMC calendar: %s", e)
            return []
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            logger.error("Failed to fetch FOMC calendar: %s", e)
            return []
    else:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    meetings = []

    # Find meeting panels
    for panel in soup.select(".fomc-meeting"):
        date_el = panel.select_one(".fomc-meeting__date")
        if not date_el:
            continue

        date_text = date_el.get_text(strip=True)

        # Look for statement link
        statement_link = None
        for link in panel.select("a"):
            href = link.get("href", "")
            if "statement" in href.lower() or "press" in href.lower():
                statement_link = href
                if not statement_link.startswith("http"):
                    statement_link = "https://www.federalreserve.gov" + statement_link
                break

        meetings.append({
            "date_text": date_text,
            "statement_url": statement_link,
        })

    return meetings


def _scrape_statement(url: str) -> str | None:
    """Scrape the full text of an FOMC statement."""
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            logger.error("HTTP error fetching statement from %s: %s", url, e)
            return None
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            logger.error("Failed to fetch statement from %s: %s", url, e)
            return None
    else:
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # The statement text is typically in a specific div
    content = soup.select_one("#article, .col-xs-12.col-sm-8.col-md-8, #content")
    if content:
        # Remove navigation and footer elements
        for el in content.select("nav, .footer, .breadcrumb"):
            el.decompose()
        return content.get_text(separator=" ", strip=True)

    return None


def _get_spy_returns(conn: sqlite3.Connection, event_date: str) -> tuple[float | None, float | None]:
    """Get SPY returns for the announcement day and day after."""
    rows = conn.execute(
        """SELECT date, close FROM market_data
           WHERE ticker = 'SPY' AND date >= date(?, '-3 days') AND date <= date(?, '+3 days')
           ORDER BY date""",
        (event_date, event_date),
    ).fetchall()

    if len(rows) < 2:
        return None, None

    # Find the event date or nearest trading day
    day_returns = {}
    for i in range(1, len(rows)):
        ret = (rows[i][1] - rows[i - 1][1]) / rows[i - 1][1]
        day_returns[rows[i][0]] = ret

    day_return = day_returns.get(event_date)
    # Two-day return: find event day and next day
    dates = list(day_returns.keys())
    two_day_return = None
    if event_date in dates:
        idx = dates.index(event_date)
        if idx + 1 < len(dates):
            two_day_return = (day_returns[event_date] or 0) + (day_returns[dates[idx + 1]] or 0)

    return day_return, two_day_return


def _extract_rate_decision(text: str) -> str | None:
    """Extract rate decision from statement text."""
    text_lower = text.lower()
    if "increase" in text_lower and "target range" in text_lower:
        # Try to find the basis points
        match = re.search(r"(\d+)\s*(?:basis point|bp)", text_lower)
        if match:
            return f"hike_{match.group(1)}"
        return "hike_25"
    elif "decrease" in text_lower or "lower" in text_lower and "target range" in text_lower:
        match = re.search(r"(\d+)\s*(?:basis point|bp)", text_lower)
        if match:
            return f"cut_{match.group(1)}"
        return "cut_25"
    elif "maintain" in text_lower or "unchanged" in text_lower:
        return "hold"
    return None


def collect() -> int:
    """Populate fomc_events from config dates and scrape available statements.

    Returns:
        Count of new events inserted.
    """
    logger.info("FOMC tracker: collecting events")
    fomc_dates = load_fomc_dates()

    conn = sqlite3.connect(DB_PATH)
    existing = set(
        r[0] for r in conn.execute("SELECT event_date FROM fomc_events").fetchall()
    )

    inserted = 0
    _previous_statement = None  # reserved for future statement diffing

    # Sort by date for statement diffing
    fomc_dates_sorted = sorted(fomc_dates, key=lambda x: x["date"])

    for meeting in fomc_dates_sorted:
        event_date = meeting["date"]
        if event_date in existing:
            # Load previous statement for diffing
            row = conn.execute(
                "SELECT statement_text FROM fomc_events WHERE event_date = ?",
                (event_date,),
            ).fetchone()
            if row and row[0]:
                _ = row[0]  # previous statement available for future diff
            continue

        event_type = meeting.get("type", "meeting")

        # Try to get SPY returns
        day_return, two_day_return = _get_spy_returns(conn, event_date)

        # Calculate diff with previous statement
        statement_diff = None

        hawkish_score = None

        try:
            conn.execute(
                """INSERT OR IGNORE INTO fomc_events
                   (event_date, event_type, title, rate_decision,
                    statement_url, statement_text, previous_statement_diff,
                    hawkish_dovish_score, spx_return_day, spx_return_2day)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_date,
                    event_type,
                    f"FOMC Meeting — {event_date}",
                    None,
                    None,
                    None,
                    statement_diff,
                    hawkish_score,
                    day_return,
                    two_day_return,
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("Error inserting FOMC event %s: %s", event_date, e)

    conn.commit()

    # Now try to scrape statements for events missing them
    events_without_statements = conn.execute(
        """SELECT event_date FROM fomc_events
           WHERE statement_text IS NULL AND event_date <= date('now')
           ORDER BY event_date"""
    ).fetchall()

    if events_without_statements:
        logger.info("Scraping %d FOMC statements...", len(events_without_statements))
        scraped_meetings = _scrape_fomc_calendar()

        for event_row in events_without_statements:
            event_date = event_row[0]

            # Find matching scraped meeting
            for meeting in scraped_meetings:
                if meeting.get("statement_url") and event_date[:7] in meeting.get("date_text", ""):
                    statement_text = _scrape_statement(meeting["statement_url"])
                    if statement_text:
                        score = score_hawkish_dovish(statement_text)
                        rate_decision = _extract_rate_decision(statement_text)

                        # Get previous statement for diff
                        prev = conn.execute(
                            """SELECT statement_text FROM fomc_events
                               WHERE event_date < ? AND statement_text IS NOT NULL
                               ORDER BY event_date DESC LIMIT 1""",
                            (event_date,),
                        ).fetchone()

                        diff_text = None
                        if prev and prev[0]:
                            diff = difflib.unified_diff(
                                prev[0].split(". "),
                                statement_text.split(". "),
                                lineterm="",
                            )
                            diff_text = "\n".join(diff)

                        conn.execute(
                            """UPDATE fomc_events SET
                               statement_url = ?, statement_text = ?,
                               previous_statement_diff = ?, hawkish_dovish_score = ?,
                               rate_decision = ?
                               WHERE event_date = ?""",
                            (
                                meeting["statement_url"],
                                statement_text[:10000],
                                diff_text[:5000] if diff_text else None,
                                score,
                                rate_decision,
                                event_date,
                            ),
                        )
                        logger.info("  Scraped statement for %s (score: %.2f)", event_date, score)

                    time.sleep(2)
                    break

    conn.commit()
    conn.close()
    logger.info("FOMC tracker done: %d new events", inserted)
    return inserted
