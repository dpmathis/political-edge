#!/usr/bin/env python3
"""Run all active collectors sequentially."""

import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import federal_register, market_data
from analysis import sector_mapper, impact_scorer

# Configure logging
log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(log_dir, "collector.log")),
    ],
)
logger = logging.getLogger("run_collectors")


def main():
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("Collection run started at %s", start.isoformat())
    logger.info("=" * 60)

    # 1. Federal Register
    try:
        logger.info("--- Federal Register ---")
        new_events = federal_register.collect()
        logger.info("Federal Register: %d new events", new_events)
    except Exception as e:
        logger.error("Federal Register collector failed: %s", e, exc_info=True)

    # 2. Sector tagging
    try:
        logger.info("--- Sector Tagging ---")
        tagged = sector_mapper.tag_all_untagged()
        logger.info("Tagged %d events with sectors/tickers", tagged)
    except Exception as e:
        logger.error("Sector tagging failed: %s", e, exc_info=True)

    # 3. Impact scoring
    try:
        logger.info("--- Impact Scoring ---")
        scored = impact_scorer.score_all_unscored()
        logger.info("Scored %d events", scored)
    except Exception as e:
        logger.error("Impact scoring failed: %s", e, exc_info=True)

    # 4. Market data
    try:
        logger.info("--- Market Data ---")
        rows = market_data.collect()
        logger.info("Market data: %d rows inserted", rows)
    except Exception as e:
        logger.error("Market data collector failed: %s", e, exc_info=True)

    elapsed = datetime.now() - start
    logger.info("=" * 60)
    logger.info("Collection run completed in %s", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
