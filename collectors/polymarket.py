"""Polymarket Prediction Market Collector.

Fetches active prediction market contracts from Polymarket's public Gamma API.
Filters for politically and economically relevant markets (FOMC, tariffs, FDA,
government shutdown, antitrust, etc.) and stores in prediction_markets table.

No authentication required — uses public endpoints only.

Usage:
    from collectors import polymarket
    polymarket.collect()
"""

import json
import logging
import sqlite3

import requests

from config import DB_PATH

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# Categories and their associated search keywords
CATEGORIES = {
    "fomc": ["fed", "interest rate", "federal reserve", "fomc", "fed chair",
             "rate cut", "rate hike", "monetary policy"],
    "tariff": ["tariff", "trade war", "import duty", "trade deal", "trade agreement"],
    "fda": ["fda", "drug approval", "pharmaceutical"],
    "fiscal": ["shutdown", "government shutdown", "debt ceiling",
               "government funding", "continuing resolution"],
    "antitrust": ["antitrust", "ftc", "sec enforcement", "doj antitrust",
                  "merger", "monopoly"],
    "geopolitical": ["sanction", "executive order", "war", "conflict"],
    "regulation": ["regulation", "deregulation", "sec ", "crypto regulation"],
    "election": ["election", "congress", "senate", "trump", "presidential"],
    "recession": ["recession", "gdp", "inflation", "unemployment rate"],
}

# Minimum volume threshold to avoid illiquid/spam markets
MIN_VOLUME = 10_000

# Ticker mapping: keyword in market question → related stock ticker
QUESTION_TICKER_MAP = {
    "fed": "SPY",
    "interest rate": "TLT",
    "tariff": "SPY",
    "fda": "XBI",
    "shutdown": "SPY",
    "recession": "SPY",
    "antitrust": "SPY",
}


def _categorize_market(question: str) -> str | None:
    """Classify a market question into a category."""
    q_lower = question.lower()
    for category, keywords in CATEGORIES.items():
        if any(kw in q_lower for kw in keywords):
            return category
    return None


def _find_related_ticker(question: str) -> str | None:
    """Find a related stock ticker for a market question."""
    q_lower = question.lower()
    for keyword, ticker in QUESTION_TICKER_MAP.items():
        if keyword in q_lower:
            return ticker
    return None


def _fetch_markets(limit: int = 100, offset: int = 0) -> list[dict]:
    """Fetch active markets from Polymarket Gamma API, sorted by volume."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "closed": "false",
                "limit": limit,
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("Failed to fetch Polymarket markets: %s", e)
        return []


def collect(max_pages: int = 3) -> int:
    """Collect relevant prediction markets from Polymarket.

    Args:
        max_pages: Number of 100-market pages to fetch.

    Returns:
        Number of markets upserted.
    """
    conn = sqlite3.connect(DB_PATH)
    upserted = 0

    for page in range(max_pages):
        markets = _fetch_markets(limit=100, offset=page * 100)
        if not markets:
            break

        for market in markets:
            question = market.get("question", "")
            category = _categorize_market(question)
            if not category:
                continue

            volume = market.get("volumeNum", 0) or 0
            if volume < MIN_VOLUME:
                continue

            # Parse outcome prices
            prices_raw = market.get("outcomePrices", "[]")
            if isinstance(prices_raw, str):
                try:
                    prices = json.loads(prices_raw)
                except json.JSONDecodeError:
                    prices = []
            else:
                prices = prices_raw

            # Current price = probability of "Yes" outcome
            current_price = float(prices[0]) if prices else None

            contract_id = market.get("conditionId") or str(market.get("id", ""))
            end_date = market.get("endDateIso")
            related_ticker = _find_related_ticker(question)

            try:
                conn.execute(
                    """INSERT INTO prediction_markets
                       (contract_id, platform, question_text, current_price, volume,
                        resolution_date, category, related_ticker, last_updated)
                       VALUES (?, 'polymarket', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(contract_id) DO UPDATE SET
                        current_price = excluded.current_price,
                        volume = excluded.volume,
                        last_updated = CURRENT_TIMESTAMP""",
                    (contract_id, question, current_price, volume,
                     end_date, category, related_ticker),
                )
                upserted += 1
            except Exception as e:
                logger.error("Failed to upsert market %s: %s", contract_id, e)

    conn.commit()
    conn.close()
    logger.info("Polymarket: upserted %d markets", upserted)
    return upserted


def get_fomc_probabilities(conn: sqlite3.Connection | None = None) -> dict:
    """Get current FOMC rate decision probabilities from prediction markets.

    Returns dict with keys like 'no_change', 'cut_25', 'hike_25' and probability values.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    rows = conn.execute(
        """SELECT question_text, current_price FROM prediction_markets
           WHERE category = 'fomc' AND platform = 'polymarket'
             AND question_text LIKE '%Fed%interest rate%'
           ORDER BY volume DESC"""
    ).fetchall()

    if close_conn:
        conn.close()

    probs = {}
    for question, price in rows:
        q_lower = question.lower()
        if "no change" in q_lower:
            probs["no_change"] = price
        elif "decrease" in q_lower and "50" in q_lower:
            probs["cut_50"] = price
        elif "decrease" in q_lower and "25" in q_lower:
            probs["cut_25"] = price
        elif "increase" in q_lower and "25" in q_lower:
            probs["hike_25"] = price
        elif "increase" in q_lower:
            probs["hike"] = price

    return probs
