"""Lobbying Disclosure Act filings collector.

Fetches quarterly lobbying disclosure filings from lda.gov API.
No API key required. Tracks QoQ spending changes for watchlist companies.

Usage:
    from collectors import lobbying
    lobbying.collect()                # Current year
    lobbying.backfill(start_year=2024)
"""

import json
import logging
import sqlite3
import time
from datetime import date

import requests

from config import DB_PATH

logger = logging.getLogger(__name__)

BASE_URL = "https://lda.gov/api/v1/filings/"
RATE_LIMIT_DELAY = 1.0
USER_AGENT = "PoliticalEdge/1.0 (research tool)"


def _build_client_ticker_lookup(conn: sqlite3.Connection) -> dict[str, str]:
    """Build case-insensitive client name → ticker lookup from DB."""
    lookup = {}

    # From company_contractor_map
    rows = conn.execute("SELECT ticker, contractor_name FROM company_contractor_map").fetchall()
    for ticker, name in rows:
        lookup[name.lower()] = ticker

    # From watchlist
    rows = conn.execute("SELECT ticker, company_name FROM watchlist WHERE active = 1").fetchall()
    for ticker, name in rows:
        lookup[name.lower()] = ticker

    return lookup


def _match_client_ticker(client_name: str, lookup: dict[str, str]) -> str | None:
    """Find a matching ticker for a lobbying client via substring match.

    Requires the matching name to be at least 5 chars to avoid false positives
    like 'META' matching 'METALS'.
    """
    client_lower = client_name.lower()
    for name, ticker in lookup.items():
        if len(name) < 5:
            # Short names require exact match (avoid 'meta' in 'metals')
            if client_lower == name:
                return ticker
        elif name in client_lower or client_lower in name:
            return ticker
    return None


def _fetch_page(url: str, params: dict | None = None) -> dict | None:
    """Fetch a page from lda.gov API with retry logic."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
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


def _parse_filing(filing: dict, client_lookup: dict[str, str]) -> dict | None:
    """Parse a single lda.gov filing into a DB row."""
    filing_uuid = filing.get("filing_uuid")
    if not filing_uuid:
        return None

    registrant = filing.get("registrant", {}) or {}
    client = filing.get("client", {}) or {}
    client_name = client.get("name", "")
    registrant_name = registrant.get("name", "")

    if not client_name:
        return None

    # Determine amount — income for lobbying firms, expenses for in-house
    amount = filing.get("income") or filing.get("expenses")
    if amount is not None:
        try:
            amount = float(str(amount).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            amount = None

    # Map client to ticker
    client_ticker = _match_client_ticker(client_name, client_lookup)

    # Extract lobbying activities
    activities = filing.get("lobbying_activities", []) or []
    specific_issues = []
    gov_entities = set()
    lobbyist_names = set()

    for activity in activities:
        issues = activity.get("specific_issues", "")
        if issues:
            specific_issues.append(issues.strip())

        for entity in (activity.get("government_entities", []) or []):
            name = entity.get("name", "")
            if name:
                gov_entities.add(name)

        for lobbyist in (activity.get("lobbyists", []) or []):
            name = lobbyist.get("name", "")
            if name:
                lobbyist_names.add(name)

    filing_year = filing.get("filing_year")
    filing_period = filing.get("filing_period")

    return {
        "filing_id": filing_uuid,
        "registrant_name": registrant_name,
        "client_name": client_name,
        "client_ticker": client_ticker,
        "amount": amount,
        "filing_year": filing_year,
        "filing_period": filing_period,
        "specific_issues": " | ".join(specific_issues)[:2000] if specific_issues else None,
        "government_entities": ", ".join(sorted(gov_entities))[:1000] if gov_entities else None,
        "lobbyists": ", ".join(sorted(lobbyist_names))[:1000] if lobbyist_names else None,
        "url": f"https://lda.gov/filings/{filing_uuid}",
        "raw_json": json.dumps(filing),
    }


def _insert_filings(conn: sqlite3.Connection, filings: list[dict]) -> int:
    """Insert filings into lobbying_filings, skipping duplicates."""
    inserted = 0
    for f in filings:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO lobbying_filings
                   (filing_id, registrant_name, client_name, client_ticker, amount,
                    filing_year, filing_period, specific_issues, government_entities,
                    lobbyists, url, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f["filing_id"],
                    f["registrant_name"],
                    f["client_name"],
                    f["client_ticker"],
                    f["amount"],
                    f["filing_year"],
                    f["filing_period"],
                    f["specific_issues"],
                    f["government_entities"],
                    f["lobbyists"],
                    f["url"],
                    f["raw_json"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.Error as e:
            logger.error("DB error inserting filing %s: %s", f["filing_id"], e)
    conn.commit()
    return inserted


def collect(filing_year: int | None = None, filing_period: str | None = None, max_pages: int = 50) -> int:
    """Fetch lobbying disclosure filings from lda.gov.

    Args:
        filing_year: Year to collect (default: current year)
        filing_period: Period to collect (e.g. 'Q1', 'Q2'). None = all periods.
        max_pages: Maximum pages to paginate through.

    Returns:
        Count of new filings inserted.
    """
    if filing_year is None:
        filing_year = date.today().year

    logger.info("Lobbying collector: year=%d period=%s", filing_year, filing_period or "all")

    conn = sqlite3.connect(DB_PATH)
    client_lookup = _build_client_ticker_lookup(conn)
    total_inserted = 0

    params = {
        "filing_year": filing_year,
        "format": "json",
        "page_size": 25,
    }
    if filing_period:
        params["filing_period"] = filing_period

    url = BASE_URL
    for page in range(max_pages):
        data = _fetch_page(url, params if page == 0 else None)
        if not data:
            logger.warning("Failed to fetch lobbying page %d", page + 1)
            break

        results = data.get("results", [])
        if not results:
            break

        filings = []
        for filing_data in results:
            parsed = _parse_filing(filing_data, client_lookup)
            if parsed:
                filings.append(parsed)

        if filings:
            inserted = _insert_filings(conn, filings)
            total_inserted += inserted
            logger.info("  Page %d: %d filings, %d new", page + 1, len(filings), inserted)

        # Follow next page URL
        next_url = data.get("next")
        if not next_url:
            break
        url = next_url
        params = None  # next URL includes params already
        time.sleep(RATE_LIMIT_DELAY)

    conn.close()
    logger.info("Lobbying collector done: %d new filings", total_inserted)
    return total_inserted


def calculate_qoq_changes(conn: sqlite3.Connection | None = None) -> list[dict]:
    """Calculate QoQ spending changes for companies with ticker mappings.

    Returns list of {ticker, client_name, current_amount, prior_amount, pct_change,
                     filing_year, filing_period}.
    Flags companies with >25% QoQ increase.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        rows = conn.execute(
            """SELECT client_ticker, client_name, filing_year, filing_period, SUM(amount) as total
               FROM lobbying_filings
               WHERE client_ticker IS NOT NULL AND amount IS NOT NULL
               GROUP BY client_ticker, filing_year, filing_period
               ORDER BY client_ticker, filing_year, filing_period"""
        ).fetchall()

        # Build periods per ticker
        ticker_periods = {}
        for ticker, client_name, year, period, total in rows:
            key = ticker
            if key not in ticker_periods:
                ticker_periods[key] = []
            ticker_periods[key].append({
                "client_name": client_name,
                "filing_year": year,
                "filing_period": period,
                "amount": total,
            })

        changes = []
        for ticker, periods in ticker_periods.items():
            for i in range(1, len(periods)):
                current = periods[i]
                prior = periods[i - 1]
                if prior["amount"] and prior["amount"] > 0:
                    pct_change = (current["amount"] - prior["amount"]) / prior["amount"]
                else:
                    pct_change = None

                changes.append({
                    "ticker": ticker,
                    "client_name": current["client_name"],
                    "current_amount": current["amount"],
                    "prior_amount": prior["amount"],
                    "pct_change": pct_change,
                    "filing_year": current["filing_year"],
                    "filing_period": current["filing_period"],
                    "spike": pct_change is not None and pct_change > 0.25,
                })

        return changes
    finally:
        if close_conn:
            conn.close()


def backfill(start_year: int = 2024, end_year: int | None = None) -> int:
    """Backfill lobbying filings for multiple years."""
    if end_year is None:
        end_year = date.today().year

    logger.info("Backfilling lobbying filings: %d to %d", start_year, end_year)
    total = 0
    for year in range(start_year, end_year + 1):
        new = collect(filing_year=year, max_pages=100)
        total += new
    return total
