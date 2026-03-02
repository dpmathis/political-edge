#!/usr/bin/env python3
"""Run all active collectors sequentially."""

import logging
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH

from collectors import federal_register, market_data, fda_calendar
from collectors import congress, lobbying, congress_trades, regulations_gov
from collectors import fred_macro, fomc, polymarket
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


def _log_collection(conn, collector_name, func, *args, **kwargs):
    """Run a collector and log the result to data_collection_log."""
    conn.execute(
        "INSERT INTO data_collection_log (collector_name, status, started_at) VALUES (?, 'running', CURRENT_TIMESTAMP)",
        (collector_name,),
    )
    log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    try:
        result = func(*args, **kwargs)
        records = result if isinstance(result, int) else 0
        conn.execute(
            "UPDATE data_collection_log SET status = 'success', records_added = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (records, log_id),
        )
        conn.commit()
        return result
    except Exception as e:
        conn.execute(
            "UPDATE data_collection_log SET status = 'error', errors = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(e)[:500], log_id),
        )
        conn.commit()
        raise


def main():
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("Collection run started at %s", start.isoformat())
    logger.info("=" * 60)

    log_conn = sqlite3.connect(DB_PATH)

    # 1. Federal Register
    try:
        logger.info("--- Federal Register ---")
        new_events = _log_collection(log_conn, "federal_register", federal_register.collect)
        logger.info("Federal Register: %d new events", new_events)
    except Exception as e:
        logger.error("Federal Register collector failed: %s", e, exc_info=True)

    # 2. Sector tagging
    try:
        logger.info("--- Sector Tagging ---")
        tagged = _log_collection(log_conn, "sector_mapper", sector_mapper.tag_all_untagged)
        logger.info("Tagged %d events with sectors/tickers", tagged)
    except Exception as e:
        logger.error("Sector tagging failed: %s", e, exc_info=True)

    # 3. Impact scoring
    try:
        logger.info("--- Impact Scoring ---")
        scored = _log_collection(log_conn, "impact_scorer", impact_scorer.score_all_unscored)
        logger.info("Scored %d events", scored)
    except Exception as e:
        logger.error("Impact scoring failed: %s", e, exc_info=True)

    # 4. Market data
    try:
        logger.info("--- Market Data ---")
        rows = _log_collection(log_conn, "market_data", market_data.collect)
        logger.info("Market data: %d rows inserted", rows)
    except Exception as e:
        logger.error("Market data collector failed: %s", e, exc_info=True)

    # 5. FDA events (full collect: regulatory events + FDA.gov calendar)
    try:
        logger.info("--- FDA Events ---")
        fda_count = _log_collection(log_conn, "fda_calendar", fda_calendar.collect)
        logger.info("FDA events: %d collected", fda_count)
    except Exception as e:
        logger.error("FDA calendar collector failed: %s", e, exc_info=True)

    # 5b. FDA event enrichment (ticker/company matching + openFDA)
    try:
        logger.info("--- FDA Event Enrichment ---")
        from scripts.enrich_fda_events import enrich_existing_records, fetch_openfda_approvals, _load_pharma_lookup
        lookup = _load_pharma_lookup()
        fda_conn = sqlite3.connect(DB_PATH)
        enriched = enrich_existing_records(fda_conn, lookup)
        logger.info("FDA enrichment: %d records updated with drug/company/ticker", enriched)
        new_approvals = fetch_openfda_approvals(fda_conn, lookup)
        logger.info("FDA enrichment: %d new approvals from openFDA", new_approvals)
        fda_conn.close()
    except Exception as e:
        logger.error("FDA enrichment failed: %s", e, exc_info=True)

    # 6. Congress.gov (requires API key)
    try:
        logger.info("--- Congress.gov ---")
        new = _log_collection(log_conn, "congress", congress.collect)
        logger.info("Congress.gov: %d new events", new)
    except Exception as e:
        logger.error("Congress.gov collector failed: %s", e, exc_info=True)

    # 7. Regulations.gov (requires API key)
    try:
        logger.info("--- Regulations.gov ---")
        new = _log_collection(log_conn, "regulations_gov", regulations_gov.collect)
        logger.info("Regulations.gov: %d new events", new)
    except Exception as e:
        logger.error("Regulations.gov collector failed: %s", e, exc_info=True)

    # 8. Lobbying filings
    try:
        logger.info("--- Lobbying Filings ---")
        new = _log_collection(log_conn, "lobbying", lobbying.collect)
        logger.info("Lobbying: %d new filings", new)
    except Exception as e:
        logger.error("Lobbying collector failed: %s", e, exc_info=True)

    # 9. Contract awards (USASpending — no API key required)
    try:
        logger.info("--- Contract Awards (USASpending) ---")
        from collectors import usaspending
        new = _log_collection(log_conn, "usaspending", usaspending.collect)
        logger.info("Contract Awards: %d new awards", new)
    except Exception as e:
        logger.error("USASpending collector failed: %s", e, exc_info=True)

    # 10. Congressional trades
    try:
        logger.info("--- Congressional Trades ---")
        new = _log_collection(log_conn, "congress_trades", congress_trades.collect)
        logger.info("Congressional Trades: %d new trades", new)
    except Exception as e:
        logger.error("Congressional trades collector failed: %s", e, exc_info=True)

    # 11. FRED macro data (requires API key)
    try:
        logger.info("--- FRED Macro Data ---")
        new = _log_collection(log_conn, "fred_macro", fred_macro.collect)
        logger.info("FRED: %d new observations", new)
    except Exception as e:
        logger.error("FRED collector failed: %s", e, exc_info=True)

    # 12. FOMC events
    try:
        logger.info("--- FOMC Events ---")
        new = _log_collection(log_conn, "fomc", fomc.collect)
        logger.info("FOMC: %d new events", new)
    except Exception as e:
        logger.error("FOMC collector failed: %s", e, exc_info=True)

    # 13. Prediction markets (Polymarket — no API key required)
    try:
        logger.info("--- Prediction Markets ---")
        new = _log_collection(log_conn, "polymarket", polymarket.collect)
        logger.info("Polymarket: %d markets updated", new)
    except Exception as e:
        logger.error("Polymarket collector failed: %s", e, exc_info=True)

    # 14. Macro regime classification (after FRED data)
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

    # 15. Pipeline rules refresh (after regulatory events + sector tagging)
    try:
        logger.info("--- Pipeline Rules ---")
        from analysis.pipeline_builder import build_pipeline, refresh_statuses
        result = build_pipeline()
        logger.info("Pipeline: %d matched, %d pending", result["matched"], result["pending"])
        changed = refresh_statuses()
        logger.info("Pipeline: %d statuses refreshed", changed)
    except Exception as e:
        logger.error("Pipeline builder failed: %s", e, exc_info=True)

    # 16. Signal generation (after all data is collected)
    try:
        logger.info("--- Signal Generator ---")
        from analysis.signal_generator import generate_signals, review_active_signals
        new_signals = generate_signals()
        logger.info("Signals: %d new signals generated", len(new_signals))
        closed = review_active_signals()
        logger.info("Signals: %d active signals closed", closed)
    except Exception as e:
        logger.error("Signal generator failed: %s", e, exc_info=True)

    # 17. Close expired paper trading positions (after signal review)
    try:
        logger.info("--- Close Expired Positions ---")
        from execution.paper_trader import PaperTrader
        trader = PaperTrader()
        if trader.is_configured:
            expired = trader.close_expired_positions()
            logger.info("Closed %d expired positions", expired)
        else:
            logger.info("Alpaca not configured, skipping position close")
    except Exception as e:
        logger.error("Close expired positions failed: %s", e, exc_info=True)

    # 18. Alert engine (after all collectors and signals)
    try:
        logger.info("--- Alert Engine ---")
        from analysis.alert_engine import evaluate_and_send
        alerts_sent = evaluate_and_send()
        logger.info("Alerts sent: %d", alerts_sent)
    except Exception as e:
        logger.error("Alert engine failed: %s", e, exc_info=True)

    log_conn.close()

    elapsed = datetime.now() - start
    logger.info("=" * 60)
    logger.info("Collection run completed in %s", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
