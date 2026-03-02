"""Federal Register API collector.

Fetches regulatory events (rules, proposed rules, executive orders, notices)
from the Federal Register API. No API key required.
"""

import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta

import requests

from config import DB_PATH

logger = logging.getLogger(__name__)

BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"
DOC_TYPES = ["RULE", "PRORULE", "PRESDOCU", "NOTICE"]
PER_PAGE = 200
RATE_LIMIT_DELAY = 1.0  # seconds between requests

# Map Federal Register document types to our event_type values
TYPE_MAP = {
    "Rule": "final_rule",
    "Proposed Rule": "proposed_rule",
    "Presidential Document": "executive_order",
    "Notice": "notice",
}


def _fetch_page(
    doc_type: str,
    page: int = 1,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict | None:
    """Fetch a single page of results from the Federal Register API."""
    params = {
        "conditions[type][]": doc_type,
        "per_page": PER_PAGE,
        "page": page,
        "order": "newest",
        "format": "json",
    }
    if start_date:
        params["conditions[publication_date][gte]"] = start_date
    if end_date:
        params["conditions[publication_date][lte]"] = end_date

    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            logger.error("HTTP error fetching %s page %d: %s", doc_type, page, e)
            return None
        except requests.exceptions.RequestException as e:
            logger.error("Request error fetching %s page %d: %s", doc_type, page, e)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return None
    return None


def _parse_document(doc: dict) -> dict | None:
    """Parse a Federal Register API document into our DB row format."""
    doc_number = doc.get("document_number")
    if not doc_number:
        return None

    doc_type = doc.get("type", "")
    event_type = TYPE_MAP.get(doc_type, doc_type.lower().replace(" ", "_"))

    # Extract agency names
    agencies = doc.get("agencies", [])
    agency_names = [a.get("name", a.get("raw_name", "")) for a in agencies if a]
    agency_str = ", ".join(filter(None, agency_names))

    return {
        "source": "federal_register",
        "source_id": doc_number,
        "event_type": event_type,
        "title": doc.get("title", ""),
        "summary": doc.get("abstract", ""),
        "agency": agency_str,
        "publication_date": doc.get("publication_date"),
        "effective_date": doc.get("effective_on"),
        "comment_deadline": doc.get("comments_close_on"),
        "url": doc.get("html_url", ""),
        "raw_json": json.dumps(doc),
    }


def _insert_events(conn: sqlite3.Connection, events: list[dict]) -> int:
    """Insert events into the database, skipping duplicates. Returns count inserted."""
    inserted = 0
    for event in events:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO regulatory_events
                   (source, source_id, event_type, title, summary, agency,
                    publication_date, effective_date, comment_deadline, url, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["source"],
                    event["source_id"],
                    event["event_type"],
                    event["title"],
                    event["summary"],
                    event["agency"],
                    event["publication_date"],
                    event["effective_date"],
                    event["comment_deadline"],
                    event["url"],
                    event["raw_json"],
                ),
            )
            if conn.total_changes:
                inserted += 1
        except sqlite3.Error as e:
            logger.error("DB error inserting %s: %s", event["source_id"], e)
    conn.commit()
    return inserted


def collect(
    start_date: str | None = None,
    end_date: str | None = None,
    max_pages_per_type: int = 5,
) -> int:
    """Run the Federal Register collector.

    Args:
        start_date: YYYY-MM-DD start date filter (default: last 7 days)
        end_date: YYYY-MM-DD end date filter (default: today)
        max_pages_per_type: Maximum pages to fetch per document type

    Returns:
        Total number of new events inserted.
    """
    if not start_date:
        start_date = (date.today() - timedelta(days=7)).isoformat()
    if not end_date:
        end_date = date.today().isoformat()

    logger.info(
        "Federal Register collector: %s to %s (max %d pages/type)",
        start_date,
        end_date,
        max_pages_per_type,
    )

    conn = sqlite3.connect(DB_PATH)
    total_inserted = 0
    total_fetched = 0

    for doc_type in DOC_TYPES:
        logger.info("Fetching %s documents...", doc_type)
        for page in range(1, max_pages_per_type + 1):
            data = _fetch_page(doc_type, page, start_date, end_date)
            if not data:
                break

            results = data.get("results", [])
            if not results:
                break

            events = []
            for doc in results:
                parsed = _parse_document(doc)
                if parsed:
                    events.append(parsed)

            inserted = _insert_events(conn, events)
            total_fetched += len(results)
            total_inserted += inserted
            logger.info(
                "  %s page %d: %d fetched, %d new",
                doc_type,
                page,
                len(results),
                inserted,
            )

            # Check if there are more pages
            if len(results) < PER_PAGE:
                break

            time.sleep(RATE_LIMIT_DELAY)

    # Auto-tag tariff events
    tariff_tagged = tag_tariff_events(conn)
    if tariff_tagged:
        logger.info("  Tariff auto-tagged: %d events", tariff_tagged)

    conn.close()
    logger.info(
        "Federal Register collector done: %d fetched, %d new, %d tariff-tagged",
        total_fetched,
        total_inserted,
        tariff_tagged,
    )
    return total_inserted


# ── Tariff auto-detection ────────────────────────────────────────

TARIFF_KEYWORDS = [
    "tariff", "duty", "duties", "import tax", "trade barrier",
    "anti-dumping", "countervailing", "safeguard measure",
    "trade remedy", "section 301", "section 232", "section 201",
    "harmonized tariff", "customs", "trade agreement",
    "retaliatory", "most-favored-nation", "trade deficit",
]

# Tariff events affect broad market indices and specific sectors
TARIFF_TICKERS = {
    "steel": "X,NUE,CLF",
    "aluminum": "AA,CENX",
    "automobile": "F,GM,TM",
    "automotive": "F,GM,TM",
    "solar": "FSLR,ENPH,XLE",
    "semiconductor": "NVDA,AMD,INTC",
    "agriculture": "ADM,BG,DE",
    "petroleum": "XOM,CVX,XLE",
    "oil country": "XOM,CVX,XLE",
    "default": "SPY,EWC,EWJ",  # Broad market
}


def _detect_tariff_sector(title: str, summary: str) -> str:
    """Detect which sector a tariff event affects."""
    text = (title + " " + (summary or "")).lower()
    for sector, tickers in TARIFF_TICKERS.items():
        if sector == "default":
            continue
        if sector in text:
            return tickers
    return TARIFF_TICKERS["default"]


def tag_tariff_events(conn: sqlite3.Connection | None = None) -> int:
    """Auto-tag regulatory events with tariff keywords and affected tickers.

    Returns count of events tagged.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    # Find untagged events with tariff keywords
    like_clauses = " OR ".join(f"title LIKE '%{kw}%'" for kw in TARIFF_KEYWORDS[:8])
    rows = conn.execute(
        f"""SELECT id, title, summary FROM regulatory_events
            WHERE (tickers IS NULL OR tickers = '')
              AND ({like_clauses})""",
    ).fetchall()

    tagged = 0
    for row_id, title, summary in rows:
        # Verify it's actually tariff-related (not just coincidental keyword)
        text = (title + " " + (summary or "")).lower()
        if not any(kw in text for kw in TARIFF_KEYWORDS):
            continue

        tickers = _detect_tariff_sector(title, summary)
        conn.execute(
            "UPDATE regulatory_events SET tickers = ? WHERE id = ? AND (tickers IS NULL OR tickers = '')",
            (tickers, row_id),
        )
        tagged += 1

    conn.commit()
    if close_conn:
        conn.close()

    logger.info("Tariff auto-detection: tagged %d events", tagged)
    return tagged


def backfill(start_date: str, end_date: str, max_pages_per_type: int = 50) -> int:
    """Backfill historical data with higher page limits."""
    logger.info("Backfilling Federal Register: %s to %s", start_date, end_date)
    return collect(start_date, end_date, max_pages_per_type)
