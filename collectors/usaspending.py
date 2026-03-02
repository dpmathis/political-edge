"""USASpending federal contract awards collector.

Fetches large federal contract awards from the USASpending API v2.
No API key required (public API).

Usage:
    from collectors import usaspending
    usaspending.collect()
"""

import json
import logging
import sqlite3
import time
from datetime import date, timedelta

import requests

from config import DB_PATH

logger = logging.getLogger(__name__)

BASE_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AWARD_URL_BASE = "https://www.usaspending.gov/award"
RATE_LIMIT_DELAY = 1.0  # seconds between requests
MAX_PAGES_PER_RUN = 5
PAGE_SIZE = 100
LOOKBACK_DAYS = 30
MIN_AWARD_AMOUNT = 10_000_000  # $10M floor

# Award type codes: A=BPA Call, B=Purchase Order, C=Delivery Order, D=Definitive Contract
AWARD_TYPE_CODES = ["A", "B", "C", "D"]

FIELDS = [
    "Award ID",
    "Recipient Name",
    "Award Amount",
    "Awarding Agency",
    "Start Date",
    "Description",
    "NAICS Code",
    "Place of Performance State Code",
    "Place of Performance Country Code",
    "Contract Award Type",
]


def _build_ticker_lookup(conn: sqlite3.Connection) -> dict[str, str]:
    """Build case-insensitive contractor name -> ticker lookup from DB."""
    lookup = {}

    # From company_contractor_map
    rows = conn.execute(
        "SELECT ticker, contractor_name FROM company_contractor_map"
    ).fetchall()
    for ticker, name in rows:
        lookup[name.upper()] = ticker

    return lookup


def _match_recipient_ticker(
    recipient_name: str, lookup: dict[str, str]
) -> str | None:
    """Find a matching ticker for a contract recipient via substring match.

    Uses the same LIKE '%name%' logic as the SQL query: checks whether any
    known contractor name appears within the recipient name.
    """
    recipient_upper = recipient_name.upper()
    for contractor_name, ticker in lookup.items():
        if contractor_name in recipient_upper:
            return ticker
    return None


def _build_request_body(page: int, start_date: str, end_date: str) -> dict:
    """Build the POST request body for the USASpending search endpoint."""
    return {
        "filters": {
            "award_type_codes": AWARD_TYPE_CODES,
            "time_period": [
                {"start_date": start_date, "end_date": end_date}
            ],
            "award_amounts": [{"lower_bound": MIN_AWARD_AMOUNT}],
        },
        "fields": FIELDS,
        "page": page,
        "limit": PAGE_SIZE,
        "sort": "Award Amount",
        "order": "desc",
    }


def _fetch_page(page: int, start_date: str, end_date: str) -> dict | None:
    """Fetch a single page of contract awards from USASpending API."""
    body = _build_request_body(page, start_date, end_date)

    for attempt in range(3):
        try:
            resp = requests.post(BASE_URL, json=body, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(
                "Request error page %d: %s (attempt %d)", page, e, attempt + 1
            )
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return None
    return None


def _format_place_of_performance(
    state_code: str | None, country_code: str | None
) -> str | None:
    """Combine state and country codes into a place of performance string."""
    parts = []
    if state_code:
        parts.append(state_code)
    if country_code:
        parts.append(country_code)
    return ", ".join(parts) if parts else None


def _parse_award(award: dict, ticker_lookup: dict[str, str]) -> dict | None:
    """Parse a single USASpending award result into a DB row."""
    award_id = award.get("generated_internal_id")
    if not award_id:
        return None

    recipient_name = award.get("Recipient Name", "")
    if not recipient_name:
        return None

    recipient_ticker = _match_recipient_ticker(recipient_name, ticker_lookup)

    place_of_performance = _format_place_of_performance(
        award.get("Place of Performance State Code"),
        award.get("Place of Performance Country Code"),
    )

    return {
        "award_id": award_id,
        "recipient_name": recipient_name,
        "recipient_ticker": recipient_ticker,
        "awarding_agency": award.get("Awarding Agency"),
        "award_amount": award.get("Award Amount"),
        "award_date": award.get("Start Date"),
        "description": award.get("Description"),
        "naics_code": award.get("NAICS Code"),
        "place_of_performance": place_of_performance,
        "contract_type": award.get("Contract Award Type"),
        "url": f"{AWARD_URL_BASE}/{award_id}",
        "raw_json": json.dumps(award),
    }


def _insert_awards(conn: sqlite3.Connection, awards: list[dict]) -> int:
    """Insert awards into contract_awards, skipping duplicates. Returns count inserted."""
    inserted = 0
    for a in awards:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO contract_awards
                   (award_id, recipient_name, recipient_ticker, awarding_agency,
                    award_amount, award_date, description, naics_code,
                    place_of_performance, contract_type, url, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a["award_id"],
                    a["recipient_name"],
                    a["recipient_ticker"],
                    a["awarding_agency"],
                    a["award_amount"],
                    a["award_date"],
                    a["description"],
                    a["naics_code"],
                    a["place_of_performance"],
                    a["contract_type"],
                    a["url"],
                    a["raw_json"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.Error as e:
            logger.error("DB error inserting award %s: %s", a["award_id"], e)
    conn.commit()
    return inserted


def collect(
    start_date: str | None = None,
    end_date: str | None = None,
    max_pages: int = MAX_PAGES_PER_RUN,
) -> int:
    """Fetch recent federal contract awards from USASpending.

    Args:
        start_date: YYYY-MM-DD start date filter (default: 30 days ago)
        end_date: YYYY-MM-DD end date filter (default: today)
        max_pages: Maximum pages to fetch (100 awards per page)

    Returns:
        Count of new contract awards inserted.
    """
    if not end_date:
        end_date = date.today().isoformat()
    if not start_date:
        start_date = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    logger.info(
        "USASpending collector: %s to %s (max %d pages, >=$%dM)",
        start_date,
        end_date,
        max_pages,
        MIN_AWARD_AMOUNT // 1_000_000,
    )

    conn = sqlite3.connect(DB_PATH)
    ticker_lookup = _build_ticker_lookup(conn)
    total_inserted = 0
    total_fetched = 0

    for page in range(1, max_pages + 1):
        data = _fetch_page(page, start_date, end_date)
        if not data:
            logger.warning("Failed to fetch USASpending page %d", page)
            break

        results = data.get("results", [])
        if not results:
            logger.info("  Page %d: no results, stopping", page)
            break

        awards = []
        for award_data in results:
            parsed = _parse_award(award_data, ticker_lookup)
            if parsed:
                awards.append(parsed)

        if awards:
            inserted = _insert_awards(conn, awards)
            total_fetched += len(awards)
            total_inserted += inserted
            logger.info(
                "  Page %d: %d awards fetched, %d new",
                page,
                len(awards),
                inserted,
            )

        # Check if there are more pages
        page_metadata = data.get("page_metadata", {})
        if not page_metadata.get("hasNext", False):
            break

        if page < max_pages:
            time.sleep(RATE_LIMIT_DELAY)

    conn.close()
    logger.info(
        "USASpending collector done: %d fetched, %d new",
        total_fetched,
        total_inserted,
    )
    return total_inserted


def backfill(
    start_date: str, end_date: str, max_pages: int = 50
) -> int:
    """Backfill historical contract awards with higher page limits."""
    logger.info("Backfilling USASpending: %s to %s", start_date, end_date)
    return collect(start_date, end_date, max_pages)
