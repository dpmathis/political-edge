"""Congress.gov API collector.

Fetches market-relevant bill actions (passed, signed, committee reports)
from the Congress.gov v3 API. Requires free API key from api.data.gov.

Usage:
    from collectors import congress
    congress.collect()                    # Last 14 days
    congress.backfill("2025-01-03", "2025-12-31")
"""

import json
import logging
import sqlite3
import time
from datetime import date, timedelta

import requests

from config import DB_PATH, get_api_key

logger = logging.getLogger(__name__)

BASE_URL = "https://api.congress.gov/v3"
RATE_LIMIT_DELAY = 1.0

# Only store actions that move markets; ignore introduced/referred (too noisy)
MARKET_RELEVANT_ACTIONS = {
    "becameLaw": {"event_type": "bill_signed", "impact_base": 5},
    "passedHouse": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "passedSenate": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "passedAgreedToInHouse": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "passedAgreedToInSenate": {"event_type": "bill_passed_chamber", "impact_base": 4},
    "reportedToHouse": {"event_type": "bill_passed_committee", "impact_base": 3},
    "reportedToSenate": {"event_type": "bill_passed_committee", "impact_base": 3},
    "hearingHeldBy": {"event_type": "hearing_scheduled", "impact_base": 2},
}


def _fetch_json(url: str, api_key: str, params: dict | None = None) -> dict | None:
    """GET a Congress.gov API endpoint with retry logic."""
    if params is None:
        params = {}
    params["api_key"] = api_key
    params["format"] = "json"

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("Request error: %s (attempt %d)", e, attempt + 1)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return None
    return None


def _is_market_relevant(action: dict) -> tuple[bool, dict | None]:
    """Check if a bill action is market-relevant. Returns (is_relevant, action_meta)."""
    action_code = action.get("actionCode", "")
    action_type = action.get("type", "")

    # Check actionCode first (more specific)
    if action_code in MARKET_RELEVANT_ACTIONS:
        return True, MARKET_RELEVANT_ACTIONS[action_code]

    # Fall back to type field
    if action_type in MARKET_RELEVANT_ACTIONS:
        return True, MARKET_RELEVANT_ACTIONS[action_type]

    return False, None


def _build_source_id(congress: int, bill_type: str, bill_number: str, action_code: str, action_date: str) -> str:
    """Build a unique source_id for a bill action."""
    return f"congress-{congress}-{bill_type}{bill_number}-{action_code}-{action_date}"


def _insert_events(conn: sqlite3.Connection, events: list[dict]) -> int:
    """Insert events into regulatory_events, skipping duplicates."""
    inserted = 0
    for event in events:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO regulatory_events
                   (source, source_id, event_type, title, summary, agency,
                    publication_date, url, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["source"],
                    event["source_id"],
                    event["event_type"],
                    event["title"],
                    event["summary"],
                    event["agency"],
                    event["publication_date"],
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
    """Fetch recent market-relevant bill actions from Congress.gov.

    Args:
        start_date: YYYY-MM-DD (default: 14 days ago)
        end_date: YYYY-MM-DD (default: today)
        max_pages: Max pages of bills to fetch

    Returns:
        Count of new events inserted.
    """
    api_key = get_api_key("congress_gov")
    if not api_key:
        logger.info("Congress.gov API key not configured, skipping")
        return 0

    if not start_date:
        start_date = (date.today() - timedelta(days=14)).isoformat()
    if not end_date:
        end_date = date.today().isoformat()

    logger.info("Congress.gov collector: %s to %s", start_date, end_date)

    conn = sqlite3.connect(DB_PATH)
    total_inserted = 0
    offset = 0
    limit = 250

    for page in range(max_pages):
        # Fetch bills updated in the date range
        data = _fetch_json(
            f"{BASE_URL}/bill",
            api_key,
            {
                "limit": limit,
                "offset": offset,
                "fromDateTime": f"{start_date}T00:00:00Z",
                "toDateTime": f"{end_date}T23:59:59Z",
            },
        )
        if not data:
            break

        bills = data.get("bills", [])
        if not bills:
            break

        logger.info("  Page %d: %d bills", page + 1, len(bills))

        for bill in bills:
            congress_num = bill.get("congress")
            bill_type = bill.get("type", "").lower()
            bill_number = bill.get("number", "")
            bill_title = bill.get("title", "")
            bill_url = bill.get("url", "")

            if not congress_num or not bill_number:
                continue

            # Fetch actions for this bill
            time.sleep(RATE_LIMIT_DELAY)
            actions_data = _fetch_json(
                f"{BASE_URL}/bill/{congress_num}/{bill_type}/{bill_number}/actions",
                api_key,
            )
            if not actions_data:
                continue

            actions = actions_data.get("actions", [])
            events = []

            for action in actions:
                relevant, meta = _is_market_relevant(action)
                if not relevant:
                    continue

                action_date = action.get("actionDate", "")
                action_code = action.get("actionCode", action.get("type", "unknown"))
                action_text = action.get("text", "")

                # Filter by date range
                if action_date < start_date or action_date > end_date:
                    continue

                source_id = _build_source_id(congress_num, bill_type, bill_number, action_code, action_date)

                # Build committee/sponsor info for agency field
                committee = action.get("committee", {})
                agency = committee.get("name", "") if committee else ""

                events.append({
                    "source": "congress",
                    "source_id": source_id,
                    "event_type": meta["event_type"],
                    "title": bill_title,
                    "summary": action_text[:500] if action_text else "",
                    "agency": agency,
                    "publication_date": action_date,
                    "url": f"https://www.congress.gov/bill/{congress_num}th-congress/{bill_type.replace('.', '')}/{bill_number}",
                    "raw_json": json.dumps({"bill": bill, "action": action}),
                })

            if events:
                inserted = _insert_events(conn, events)
                total_inserted += inserted

        if len(bills) < limit:
            break
        offset += limit
        time.sleep(RATE_LIMIT_DELAY)

    conn.close()

    # Tag and score new events
    if total_inserted > 0:
        from analysis import sector_mapper, impact_scorer
        tagged = sector_mapper.tag_all_untagged()
        scored = impact_scorer.score_all_unscored()
        logger.info("Tagged %d, scored %d new events", tagged, scored)

    logger.info("Congress.gov collector done: %d new events", total_inserted)
    return total_inserted


def backfill(start_date: str, end_date: str, max_pages: int = 50) -> int:
    """Backfill historical bill actions with higher page limits."""
    logger.info("Backfilling Congress.gov: %s to %s", start_date, end_date)
    return collect(start_date, end_date, max_pages)
