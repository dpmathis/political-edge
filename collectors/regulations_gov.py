"""Regulations.gov API collector.

Fetches recently posted proposed rules and final rules from
the Regulations.gov v4 API. Requires free API key from api.data.gov.

Usage:
    from collectors import regulations_gov
    regulations_gov.collect()                           # Last 7 days
    regulations_gov.backfill("2024-01-01", "2025-12-31")
"""

import json
import logging
import sqlite3
import time
from datetime import date, timedelta

import requests

from config import DB_PATH, get_api_key

logger = logging.getLogger(__name__)

BASE_URL = "https://api.regulations.gov/v4/documents"
RATE_LIMIT_DELAY = 1.5

DOC_TYPE_MAP = {
    "Proposed Rule": "proposed_rule",
    "Rule": "final_rule",
    "Notice": "notice",
}


def _fetch_documents(api_key: str, since_date: str, page_num: int = 1, page_size: int = 25) -> dict | None:
    """Fetch documents from Regulations.gov API."""
    headers = {
        "X-Api-Key": api_key,
        "Accept": "application/vnd.api+json",
    }
    params = {
        "filter[postedDate][ge]": since_date,
        "filter[documentType]": "Proposed Rule,Rule",
        "sort": "-postedDate",
        "page[size]": page_size,
        "page[number]": page_num,
    }

    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, headers=headers, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            if resp.status_code == 403:
                logger.error("Regulations.gov API returned 403 — check API key")
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("Request error: %s (attempt %d)", e, attempt + 1)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return None
    return None


def _parse_document(doc: dict) -> dict | None:
    """Parse a Regulations.gov JSON:API document into a regulatory_events row."""
    doc_id = doc.get("id", "")
    attributes = doc.get("attributes", {})

    if not doc_id or not attributes:
        return None

    doc_type = attributes.get("documentType", "")
    event_type = DOC_TYPE_MAP.get(doc_type, doc_type.lower().replace(" ", "_"))

    title = attributes.get("title", "")
    summary = attributes.get("summary", "")
    if not summary:
        summary = title[:500]

    agency_id = attributes.get("agencyId", "")
    posted_date = attributes.get("postedDate", "")
    if posted_date and "T" in posted_date:
        posted_date = posted_date.split("T")[0]

    comment_end = attributes.get("commentEndDate", "")
    if comment_end and "T" in comment_end:
        comment_end = comment_end.split("T")[0]

    return {
        "source": "regulations_gov",
        "source_id": f"regsgov-{doc_id}",
        "event_type": event_type,
        "title": title,
        "summary": summary,
        "agency": agency_id,
        "publication_date": posted_date,
        "comment_deadline": comment_end or None,
        "url": f"https://www.regulations.gov/document/{doc_id}",
        "raw_json": json.dumps(doc),
    }


def _insert_events(conn: sqlite3.Connection, events: list[dict]) -> int:
    """Insert events into regulatory_events, skipping duplicates."""
    inserted = 0
    for event in events:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO regulatory_events
                   (source, source_id, event_type, title, summary, agency,
                    publication_date, comment_deadline, url, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["source"],
                    event["source_id"],
                    event["event_type"],
                    event["title"],
                    event["summary"],
                    event["agency"],
                    event["publication_date"],
                    event["comment_deadline"],
                    event["url"],
                    event["raw_json"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.Error as e:
            logger.error("DB error inserting %s: %s", event["source_id"], e)
    conn.commit()
    return inserted


def collect(start_date: str | None = None, end_date: str | None = None, max_pages: int = 10) -> int:
    """Fetch recent proposed rules and final rules from Regulations.gov.

    Args:
        start_date: YYYY-MM-DD (default: 7 days ago)
        end_date: unused (API filters by posted date >= start_date)
        max_pages: Max pages to fetch

    Returns:
        Count of new events inserted.
    """
    api_key = get_api_key("regulations_gov")
    if not api_key:
        logger.info("Regulations.gov API key not configured, skipping")
        return 0

    if not start_date:
        start_date = (date.today() - timedelta(days=7)).isoformat()

    logger.info("Regulations.gov collector: since %s", start_date)

    conn = sqlite3.connect(DB_PATH)
    total_inserted = 0

    for page in range(1, max_pages + 1):
        data = _fetch_documents(api_key, start_date, page_num=page)
        if not data:
            break

        documents = data.get("data", [])
        if not documents:
            break

        events = []
        for doc in documents:
            parsed = _parse_document(doc)
            if parsed:
                events.append(parsed)

        if events:
            inserted = _insert_events(conn, events)
            total_inserted += inserted
            logger.info("  Page %d: %d documents, %d new", page, len(events), inserted)

        # Check if there are more pages
        meta = data.get("meta", {})
        total_elements = meta.get("totalElements", 0)
        if page * 25 >= total_elements:
            break

        time.sleep(RATE_LIMIT_DELAY)

    conn.close()

    # Tag and score new events
    if total_inserted > 0:
        from analysis import sector_mapper, impact_scorer
        tagged = sector_mapper.tag_all_untagged()
        scored = impact_scorer.score_all_unscored()
        logger.info("Tagged %d, scored %d new events", tagged, scored)

    logger.info("Regulations.gov collector done: %d new events", total_inserted)
    return total_inserted


def backfill(start_date: str, end_date: str, max_pages: int = 50) -> int:
    """Backfill historical regulations with chunked date ranges."""
    from datetime import datetime as dt

    logger.info("Backfilling Regulations.gov: %s to %s", start_date, end_date)

    start = dt.strptime(start_date, "%Y-%m-%d").date()
    end = dt.strptime(end_date, "%Y-%m-%d").date()
    chunk_days = 90
    total = 0
    current = start

    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        new = collect(start_date=current.isoformat(), max_pages=max_pages)
        total += new
        current = chunk_end + timedelta(days=1)

    return total
