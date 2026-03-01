#!/usr/bin/env python3
"""Backfill historical data for Federal Register and market data."""

import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import federal_register, market_data
from collectors import congress, lobbying, regulations_gov, fred_macro
from analysis import sector_mapper, impact_scorer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill")

# Backfill range
START_DATE = "2024-01-01"
END_DATE = date.today().isoformat()


def main():
    logger.info("Starting backfill: %s to %s", START_DATE, END_DATE)

    # Backfill in 3-month chunks to avoid API timeouts
    from datetime import datetime, timedelta

    start = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    end = datetime.strptime(END_DATE, "%Y-%m-%d").date()
    chunk_days = 90

    total_events = 0
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        logger.info("Chunk: %s to %s", current.isoformat(), chunk_end.isoformat())

        new = federal_register.backfill(
            current.isoformat(), chunk_end.isoformat(), max_pages_per_type=50
        )
        total_events += new
        current = chunk_end + timedelta(days=1)

    logger.info("Federal Register backfill complete: %d total new events", total_events)

    # Tag and score all events
    logger.info("Tagging events with sectors...")
    tagged = sector_mapper.tag_all_untagged()
    logger.info("Tagged %d events", tagged)

    logger.info("Scoring events...")
    scored = impact_scorer.score_all_unscored()
    logger.info("Scored %d events", scored)

    # Backfill market data
    logger.info("Backfilling market data...")
    rows = market_data.collect(start_date=START_DATE, end_date=END_DATE)
    logger.info("Market data: %d rows inserted", rows)

    # Congress.gov backfill (requires API key)
    try:
        logger.info("Backfilling Congress.gov (119th Congress)...")
        new = congress.backfill("2025-01-03", END_DATE)
        logger.info("Congress.gov: %d events", new)
    except Exception as e:
        logger.error("Congress.gov backfill failed: %s", e, exc_info=True)

    # Regulations.gov backfill (requires API key)
    try:
        logger.info("Backfilling Regulations.gov...")
        new = regulations_gov.backfill(START_DATE, END_DATE)
        logger.info("Regulations.gov: %d events", new)
    except Exception as e:
        logger.error("Regulations.gov backfill failed: %s", e, exc_info=True)

    # Lobbying backfill (2024-present)
    try:
        logger.info("Backfilling lobbying filings...")
        new = lobbying.backfill(start_year=2024)
        logger.info("Lobbying: %d filings", new)
    except Exception as e:
        logger.error("Lobbying backfill failed: %s", e, exc_info=True)

    # FRED macro data backfill (requires API key)
    try:
        logger.info("Backfilling FRED macro data from %s...", START_DATE)
        new = fred_macro.backfill(START_DATE)
        logger.info("FRED: %d observations", new)
    except Exception as e:
        logger.error("FRED backfill failed: %s", e, exc_info=True)

    logger.info("Backfill complete.")


if __name__ == "__main__":
    main()
