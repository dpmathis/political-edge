"""FDA Calendar Collector.

Extracts FDA-related events from existing regulatory_events and scrapes
the FDA.gov Advisory Committee Calendar. Maps to pharma company tickers.
"""

import logging
import re
import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from config import DB_PATH, load_pharma_companies

logger = logging.getLogger(__name__)

# Patterns for extracting drug/company info from Federal Register titles
DRUG_PATTERNS = [
    r"(?:for|of)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:Capsules|Tablets|Injection|Solution|Cream|Oral)",
    r"(?:NDA|BLA|ANDA)\s*(?:#?\s*)?(\d{6})",
]

# Map title keywords to FDA event types
FDA_EVENT_TYPE_MAP = {
    "advisory committee": "adcom_vote",
    "advisory panel": "adcom_vote",
    "approval": "approval",
    "approved": "approval",
    "complete response": "crl",
    "refuse to file": "crl",
    "fast track": "fast_track",
    "breakthrough therapy": "breakthrough",
    "priority review": "priority_review",
    "warning letter": "warning_letter",
    "import alert": "import_alert",
    "pdufa": "pdufa_date",
}


def _build_company_lookup() -> dict[str, str]:
    """Build a lowercase name → ticker lookup from pharma_companies.yaml."""
    companies = load_pharma_companies()
    lookup = {}
    for entry in companies:
        ticker = entry["ticker"]
        for name in entry["names"]:
            lookup[name.lower()] = ticker
    return lookup


def _match_company(text: str, company_lookup: dict[str, str]) -> tuple[str | None, str | None]:
    """Find a pharma company mentioned in text. Returns (company_name, ticker)."""
    text_lower = text.lower()
    for name, ticker in company_lookup.items():
        if name.lower() in text_lower:
            return name, ticker
    return None, None


def _classify_fda_event(title: str) -> str:
    """Determine FDA event type from title text."""
    title_lower = title.lower()
    for keyword, event_type in FDA_EVENT_TYPE_MAP.items():
        if keyword in title_lower:
            return event_type
    return "fda_notice"


def collect_from_regulatory_events(conn: sqlite3.Connection | None = None) -> int:
    """Parse existing regulatory_events for FDA-related items and insert into fda_events."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        company_lookup = _build_company_lookup()

        # Get FDA-related events not yet processed
        existing_urls = set(
            r[0] for r in conn.execute("SELECT source_url FROM fda_events WHERE source_url IS NOT NULL").fetchall()
        )

        rows = conn.execute(
            """SELECT id, title, summary, agency, publication_date, url
               FROM regulatory_events
               WHERE (agency LIKE '%Food and Drug%' OR agency LIKE '%FDA%')
               ORDER BY publication_date DESC"""
        ).fetchall()

        inserted = 0
        for event_id, title, summary, agency, pub_date, url in rows:
            if url in existing_urls:
                continue

            text = f"{title} {summary or ''}"
            event_type = _classify_fda_event(title)
            company_name, ticker = _match_company(text, company_lookup)

            # Extract drug name via regex
            drug_name = None
            for pattern in DRUG_PATTERNS:
                match = re.search(pattern, text)
                if match:
                    drug_name = match.group(1)
                    break

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO fda_events
                       (event_type, drug_name, company_name, ticker, event_date,
                        outcome, source, source_url, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_type,
                        drug_name,
                        company_name,
                        ticker,
                        pub_date,
                        "pending" if event_type in ("adcom_vote", "pdufa_date") else None,
                        "federal_register",
                        url,
                        title,
                    ),
                )
                inserted += 1
            except sqlite3.Error as e:
                logger.error("Error inserting FDA event: %s", e)

        conn.commit()
        logger.info("Extracted %d FDA events from regulatory_events", inserted)
        return inserted

    finally:
        if close_conn:
            conn.close()


def collect_from_fda_calendar() -> int:
    """Scrape FDA.gov Advisory Committee Calendar for upcoming events."""
    url = "https://www.fda.gov/advisory-committees/advisory-committee-calendar"
    logger.info("Scraping FDA Advisory Committee Calendar...")

    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "PoliticalEdge/1.0 (research tool)"})
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch FDA calendar: %s", e)
        return 0

    soup = BeautifulSoup(resp.text, "lxml")
    company_lookup = _build_company_lookup()
    conn = sqlite3.connect(DB_PATH)
    inserted = 0

    # FDA calendar uses various HTML structures; try common patterns
    # Look for table rows or list items with meeting info
    for row in soup.select("table tbody tr, .view-content .views-row"):
        try:
            text = row.get_text(separator=" ", strip=True)
            if not text or len(text) < 20:
                continue

            # Try to extract date (various formats)
            date_match = re.search(r"(\w+ \d{1,2}, \d{4})", text)
            if not date_match:
                date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
            if not date_match:
                continue

            from datetime import datetime
            date_str = date_match.group(1)
            try:
                event_date = datetime.strptime(date_str, "%B %d, %Y").strftime("%Y-%m-%d")
            except ValueError:
                try:
                    event_date = datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    continue

            # Extract committee name and topic
            link = row.select_one("a")
            title = link.get_text(strip=True) if link else text[:200]
            source_url = "https://www.fda.gov" + link["href"] if link and link.get("href", "").startswith("/") else None

            company_name, ticker = _match_company(text, company_lookup)

            conn.execute(
                """INSERT OR IGNORE INTO fda_events
                   (event_type, drug_name, company_name, ticker, event_date,
                    outcome, source, source_url, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "adcom_vote",
                    None,
                    company_name,
                    ticker,
                    event_date,
                    "pending",
                    "fda_calendar",
                    source_url,
                    title,
                ),
            )
            inserted += 1

        except Exception as e:
            logger.warning("Error parsing FDA calendar row: %s", e)
            continue

    conn.commit()
    conn.close()
    logger.info("Scraped %d FDA calendar events", inserted)
    return inserted


def collect() -> int:
    """Run all FDA collection sources."""
    total = 0
    total += collect_from_regulatory_events()
    time.sleep(2)
    total += collect_from_fda_calendar()
    return total
