#!/usr/bin/env python3
"""Run all active collectors sequentially."""

import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import federal_register, market_data, fda_calendar
from collectors import congress, lobbying, congress_trades, regulations_gov
from collectors import fred_macro, fomc
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

    # 5. FDA events
    try:
        logger.info("--- FDA Events ---")
        fda_count = fda_calendar.collect_from_regulatory_events()
        logger.info("FDA events: %d extracted from regulatory events", fda_count)
    except Exception as e:
        logger.error("FDA calendar collector failed: %s", e, exc_info=True)

    # 6. Congress.gov (requires API key)
    try:
        logger.info("--- Congress.gov ---")
        new = congress.collect()
        logger.info("Congress.gov: %d new events", new)
    except Exception as e:
        logger.error("Congress.gov collector failed: %s", e, exc_info=True)

    # 7. Regulations.gov (requires API key)
    try:
        logger.info("--- Regulations.gov ---")
        new = regulations_gov.collect()
        logger.info("Regulations.gov: %d new events", new)
    except Exception as e:
        logger.error("Regulations.gov collector failed: %s", e, exc_info=True)

    # 8. Lobbying filings
    try:
        logger.info("--- Lobbying Filings ---")
        new = lobbying.collect()
        logger.info("Lobbying: %d new filings", new)
    except Exception as e:
        logger.error("Lobbying collector failed: %s", e, exc_info=True)

    # 9. Congressional trades
    try:
        logger.info("--- Congressional Trades ---")
        new = congress_trades.collect()
        logger.info("Congressional Trades: %d new trades", new)
    except Exception as e:
        logger.error("Congressional trades collector failed: %s", e, exc_info=True)

    # 10. FRED macro data (requires API key)
    try:
        logger.info("--- FRED Macro Data ---")
        new = fred_macro.collect()
        logger.info("FRED: %d new observations", new)
    except Exception as e:
        logger.error("FRED collector failed: %s", e, exc_info=True)

    # 11. FOMC events
    try:
        logger.info("--- FOMC Events ---")
        new = fomc.collect()
        logger.info("FOMC: %d new events", new)
    except Exception as e:
        logger.error("FOMC collector failed: %s", e, exc_info=True)

    # 12. Macro regime classification (after FRED data)
    try:
        logger.info("--- Macro Regime Classification ---")
        from analysis.macro_regime import classify_current_regime
        result = classify_current_regime()
        if result:
            logger.info("Regime: Q%d %s (confidence: %s)", result["quadrant"], result["label"], result["confidence"])
        else:
            logger.info("Insufficient data for regime classification")
    except Exception as e:
        logger.error("Macro regime classification failed: %s", e, exc_info=True)

    # 13. Pipeline rules refresh (after regulatory events + sector tagging)
    try:
        logger.info("--- Pipeline Rules ---")
        from analysis.pipeline_builder import build_pipeline, refresh_statuses
        result = build_pipeline()
        logger.info("Pipeline: %d matched, %d pending", result["matched"], result["pending"])
        changed = refresh_statuses()
        logger.info("Pipeline: %d statuses refreshed", changed)
    except Exception as e:
        logger.error("Pipeline builder failed: %s", e, exc_info=True)

    # 14. Signal generation (after all data is collected)
    try:
        logger.info("--- Signal Generator ---")
        from analysis.signal_generator import generate_signals, review_active_signals
        new_signals = generate_signals()
        logger.info("Signals: %d new signals generated", len(new_signals))
        closed = review_active_signals()
        logger.info("Signals: %d active signals closed", closed)
    except Exception as e:
        logger.error("Signal generator failed: %s", e, exc_info=True)

    # 15. Alert engine (after all collectors and signals)
    try:
        logger.info("--- Alert Engine ---")
        from analysis.alert_engine import evaluate_and_send
        alerts_sent = evaluate_and_send()
        logger.info("Alerts sent: %d", alerts_sent)
    except Exception as e:
        logger.error("Alert engine failed: %s", e, exc_info=True)

    elapsed = datetime.now() - start
    logger.info("=" * 60)
    logger.info("Collection run completed in %s", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
