#!/usr/bin/env python3
"""Enrich FDA events with drug names, company names, and tickers.

Two-part approach:
1. Parse existing records' details text for drug/company info
2. Fetch new approval events from openFDA API (api.fda.gov)

Usage:
    python scripts/enrich_fda_events.py
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
import sqlite3

import requests
import yaml

from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Load pharma company mappings
PHARMA_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "pharma_companies.yaml")


def _load_pharma_lookup() -> dict[str, str]:
    """Build case-insensitive company name → ticker lookup."""
    lookup = {}
    with open(PHARMA_YAML) as f:
        data = yaml.safe_load(f)

    for company in data.get("pharma_companies", []):
        ticker = company["ticker"]
        for name in company.get("names", []):
            lookup[name.lower()] = ticker
    return lookup


def _match_company_to_ticker(text: str, lookup: dict[str, str]) -> tuple[str | None, str | None]:
    """Search text for a known pharma company name. Returns (company_name, ticker)."""
    text_lower = text.lower()
    for name, ticker in sorted(lookup.items(), key=lambda x: -len(x[0])):
        if name in text_lower:
            # Return the original-cased name from the YAML
            return name.title(), ticker
    return None, None


def _extract_drug_name_from_details(details: str) -> str | None:
    """Try to extract a drug name from FDA event details text."""
    if not details:
        return None

    # Pattern: "DRUGNAME (generic name)" — FDA format
    match = re.search(r"\b([A-Z]{4,})\s*\(", details)
    if match:
        candidate = match.group(1)
        # Exclude common non-drug words
        if candidate not in {"NOTICE", "AGENCY", "MEETING", "COMMITTEE", "ADVISORY", "PROPOSED",
                             "COMMENT", "FEDERAL", "COLLECTION", "APPROVAL", "ISSUANCE", "REVIEW",
                             "WITHDRAWAL", "APPROVED", "DEVICES", "PRODUCTS", "INFORMATION",
                             "ACTIVITIES", "APPLICATIONS", "ANIMAL", "DRUGS", "OFFICE",
                             "MANAGEMENT", "BUDGET", "REQUEST", "ESTABLISHMENT", "GENERAL",
                             "HOSPITAL", "PERSONAL", "SYSTEM", "PANEL", "TOBACCO"}:
            return candidate

    # Pattern: "ZYCUBO (Copper Histidinate)" — priority review format
    match = re.search(r";\s*([A-Z][A-Z]+)\s*\(", details)
    if match:
        candidate = match.group(1)
        if len(candidate) >= 4 and candidate not in {"NOTICE", "MEETING", "COMMITTEE"}:
            return candidate

    return None


def enrich_existing_records(conn: sqlite3.Connection, lookup: dict[str, str]) -> int:
    """Parse drug_name, company_name, ticker from existing details text."""
    updated = 0

    # Focus on records without drug_name
    rows = conn.execute(
        "SELECT id, event_type, details, source FROM fda_events WHERE drug_name IS NULL AND details IS NOT NULL"
    ).fetchall()

    for row_id, event_type, details, source in rows:
        drug_name = _extract_drug_name_from_details(details)
        company_name, ticker = _match_company_to_ticker(details, lookup)

        if drug_name or company_name:
            conn.execute(
                "UPDATE fda_events SET drug_name = COALESCE(?, drug_name), company_name = COALESCE(?, company_name), ticker = COALESCE(?, ticker) WHERE id = ?",
                (drug_name, company_name, ticker, row_id),
            )
            updated += 1

    conn.commit()
    return updated


def fetch_openfda_approvals(conn: sqlite3.Connection, lookup: dict[str, str]) -> int:
    """Fetch drug approvals from openFDA API and insert as new fda_events."""
    base_url = "https://api.fda.gov/drug/drugsfda.json"
    inserted = 0

    # Fetch recent approvals (last 3 years)
    params = {
        "search": 'submissions.submission_type:"ORIG" AND submissions.submission_status:"AP"',
        "limit": 100,
        "sort": "submissions.submission_status_date:desc",
    }

    for attempt in range(3):
        try:
            resp = requests.get(base_url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.RequestException as e:
            logger.error("openFDA API error (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2)
                continue
            return 0

    results = data.get("results", [])
    logger.info("Fetched %d drug records from openFDA", len(results))

    for drug in results:
        brand_name = drug.get("openfda", {}).get("brand_name", [None])[0]
        generic_name = drug.get("openfda", {}).get("generic_name", [None])[0]
        manufacturer = drug.get("openfda", {}).get("manufacturer_name", [None])[0]
        sponsor = drug.get("sponsor_name", "")
        app_number = drug.get("application_number", "")

        # Find the original approval submission
        for sub in drug.get("submissions", []):
            if sub.get("submission_type") != "ORIG":
                continue
            if sub.get("submission_status") != "AP":
                continue

            approval_date = sub.get("submission_status_date")
            if not approval_date:
                continue

            # Format date from YYYYMMDD to YYYY-MM-DD
            try:
                approval_date = f"{approval_date[:4]}-{approval_date[4:6]}-{approval_date[6:8]}"
            except (IndexError, TypeError):
                continue

            # Match to ticker
            search_text = f"{sponsor} {manufacturer or ''} {brand_name or ''}"
            company_name, ticker = _match_company_to_ticker(search_text, lookup)

            # Check for duplicate
            existing = conn.execute(
                "SELECT id FROM fda_events WHERE event_type = 'approval' AND drug_name = ? AND event_date = ?",
                (brand_name, approval_date),
            ).fetchone()

            if existing:
                # Update if we have new info
                if ticker and not conn.execute("SELECT ticker FROM fda_events WHERE id = ?", (existing[0],)).fetchone()[0]:
                    conn.execute(
                        "UPDATE fda_events SET ticker = ?, company_name = ? WHERE id = ?",
                        (ticker, company_name or sponsor, existing[0]),
                    )
                continue

            # Insert new approval event
            details = f"FDA approved {brand_name or generic_name or 'N/A'} ({app_number}). Sponsor: {sponsor}."
            conn.execute(
                """INSERT INTO fda_events
                   (event_type, drug_name, company_name, ticker, event_date, outcome, details, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "approval",
                    brand_name or generic_name,
                    company_name or sponsor,
                    ticker,
                    approval_date,
                    "approved",
                    details,
                    "openfda",
                ),
            )
            inserted += 1

    conn.commit()
    return inserted


def main():
    conn = sqlite3.connect(DB_PATH)
    lookup = _load_pharma_lookup()
    print(f"Loaded {len(lookup)} pharma company name mappings")

    # Part 1: Enrich existing records
    print("\n--- Part 1: Enriching existing records ---")
    updated = enrich_existing_records(conn, lookup)
    print(f"Updated {updated} existing records with parsed drug/company info")

    # Part 2: Fetch from openFDA
    print("\n--- Part 2: Fetching from openFDA API ---")
    inserted = fetch_openfda_approvals(conn, lookup)
    print(f"Inserted {inserted} new approval events from openFDA")

    # Summary
    print("\n--- Summary ---")
    for col in ["drug_name", "company_name", "ticker"]:
        total = conn.execute(f"SELECT COUNT(*) FROM fda_events WHERE {col} IS NOT NULL").fetchone()[0]
        print(f"  Records with {col}: {total}")

    # By event type
    rows = conn.execute(
        """SELECT event_type, COUNT(*), COUNT(drug_name), COUNT(ticker)
           FROM fda_events GROUP BY event_type"""
    ).fetchall()
    print("\n  Event type breakdown:")
    for et, total, with_drug, with_ticker in rows:
        print(f"    {et}: {total} total, {with_drug} with drug_name, {with_ticker} with ticker")

    conn.close()


if __name__ == "__main__":
    main()
