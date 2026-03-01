"""Congressional trading collector via Capitol Trades.

Scrapes publicly available trading data from capitoltrades.com.
Minimal collector — downweighted per hypothesis validation.

Usage:
    from collectors import congress_trades
    congress_trades.collect()
"""

import logging
import re
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from config import DB_PATH

logger = logging.getLogger(__name__)

BASE_URL = "https://www.capitoltrades.com/trades"
RATE_LIMIT_DELAY = 3.0
MAX_PAGES_PER_RUN = 10
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _fetch_page(page_num: int) -> BeautifulSoup | None:
    """Fetch and parse a trades page from Capitol Trades."""
    url = f"{BASE_URL}?page={page_num}" if page_num > 1 else BASE_URL
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 403:
                logger.warning("Capitol Trades returned 403 (blocked). Site may require JS rendering.")
                return None
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.exceptions.RequestException as e:
            logger.error("Request error fetching page %d: %s (attempt %d)", page_num, e, attempt + 1)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return None
    return None


def _normalize_date(text: str) -> str | None:
    """Parse various date formats to YYYY-MM-DD."""
    text = text.strip()
    for fmt in ("%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_trades_table(soup: BeautifulSoup) -> list[dict]:
    """Extract trade rows from Capitol Trades HTML."""
    trades = []

    # Capitol Trades uses a table with trade data
    # Try multiple selectors since the site structure may vary
    table = soup.select_one("table")
    if not table:
        # Try card-based layout
        rows = soup.select("[class*='trade'], [class*='Trade'], .q-tr")
        if not rows:
            return trades

        for row in rows:
            trade = _parse_card_row(row)
            if trade:
                trades.append(trade)
        return trades

    # Parse table rows
    tbody = table.select_one("tbody")
    if not tbody:
        return trades

    for tr in tbody.select("tr"):
        cells = tr.select("td")
        if len(cells) < 5:
            continue

        trade = _parse_table_row(cells, tr)
        if trade:
            trades.append(trade)

    return trades


def _parse_table_row(cells: list, tr) -> dict | None:
    """Parse a table row into a trade dict."""
    try:
        texts = [c.get_text(strip=True) for c in cells]

        # Look for politician name, ticker, and trade type in any order
        politician = None
        party = None
        chamber = None
        ticker = None
        trade_type = None
        amount_range = None
        trade_date = None
        disclosure_date = None
        asset_desc = None

        for i, text in enumerate(texts):
            # Detect ticker (usually 1-5 uppercase letters)
            ticker_match = re.match(r"^[A-Z]{1,5}$", text)
            if ticker_match and not ticker:
                ticker = text
                continue

            # Detect trade type
            if text.lower() in ("purchase", "buy", "sale", "sale (partial)", "sale (full)", "exchange"):
                trade_type = "buy" if text.lower() in ("purchase", "buy") else "sell"
                continue

            # Detect amount range ($X - $Y)
            if "$" in text and not amount_range:
                amount_range = text
                continue

            # Detect date
            date_val = _normalize_date(text)
            if date_val:
                if not trade_date:
                    trade_date = date_val
                elif not disclosure_date:
                    disclosure_date = date_val
                continue

            # Detect party
            if text in ("R", "D", "I", "Republican", "Democrat", "Independent"):
                party = text[0] if len(text) > 1 else text
                continue

            # Detect chamber
            if text.lower() in ("house", "senate", "representative", "senator"):
                chamber = "House" if text.lower() in ("house", "representative") else "Senate"
                continue

            # First unmatched long text is likely the politician name
            if not politician and len(text) > 3 and not text.startswith("$"):
                politician = text

        # Also check for links with ticker data
        if not ticker:
            ticker_link = tr.select_one("a[href*='/stocks/'], a[href*='ticker']")
            if ticker_link:
                ticker = ticker_link.get_text(strip=True).upper()

        if not politician:
            name_link = tr.select_one("a[href*='/politicians/'], a[href*='/trader/']")
            if name_link:
                politician = name_link.get_text(strip=True)

        if not ticker or not politician:
            return None

        return {
            "politician": politician,
            "party": party,
            "chamber": chamber,
            "ticker": ticker,
            "trade_type": trade_type or "unknown",
            "amount_range": amount_range,
            "trade_date": trade_date,
            "disclosure_date": disclosure_date,
            "asset_description": asset_desc,
        }
    except Exception as e:
        logger.debug("Error parsing table row: %s", e)
        return None


def _parse_card_row(element) -> dict | None:
    """Parse a card-style trade element."""
    try:
        text = element.get_text(separator=" ", strip=True)
        if len(text) < 10:
            return None

        # Try to extract structured data from links and spans
        politician = None
        ticker = None
        trade_type = None

        name_el = element.select_one("a[href*='/politicians/'], a[href*='/trader/'], .politician-name")
        if name_el:
            politician = name_el.get_text(strip=True)

        ticker_el = element.select_one("a[href*='/stocks/'], .ticker, .q-field--issuer")
        if ticker_el:
            ticker_text = ticker_el.get_text(strip=True)
            ticker_match = re.search(r"[A-Z]{1,5}", ticker_text)
            if ticker_match:
                ticker = ticker_match.group()

        if not politician or not ticker:
            return None

        # Extract other fields from text
        trade_type_match = re.search(r"\b(purchase|buy|sale|sell|exchange)\b", text, re.IGNORECASE)
        if trade_type_match:
            t = trade_type_match.group().lower()
            trade_type = "buy" if t in ("purchase", "buy") else "sell"

        amount_match = re.search(r"(\$[\d,]+ ?- ?\$[\d,]+)", text)
        amount_range = amount_match.group(1) if amount_match else None

        date_match = re.search(r"(\w+ \d{1,2}, \d{4})", text)
        trade_date = _normalize_date(date_match.group(1)) if date_match else None

        party_match = re.search(r"\b([RDI])\b", text)
        party = party_match.group(1) if party_match else None

        chamber_match = re.search(r"\b(House|Senate|Representative|Senator)\b", text, re.IGNORECASE)
        chamber = None
        if chamber_match:
            c = chamber_match.group().lower()
            chamber = "House" if c in ("house", "representative") else "Senate"

        return {
            "politician": politician,
            "party": party,
            "chamber": chamber,
            "ticker": ticker,
            "trade_type": trade_type or "unknown",
            "amount_range": amount_range,
            "trade_date": trade_date,
            "disclosure_date": None,
            "asset_description": None,
        }
    except Exception as e:
        logger.debug("Error parsing card row: %s", e)
        return None


def _trade_exists(conn: sqlite3.Connection, trade: dict) -> bool:
    """Check if a trade already exists to avoid duplicates."""
    row = conn.execute(
        """SELECT COUNT(*) FROM congress_trades
           WHERE politician = ? AND ticker = ? AND trade_date = ? AND trade_type = ?""",
        (trade["politician"], trade["ticker"], trade["trade_date"], trade["trade_type"]),
    ).fetchone()
    return row[0] > 0


def _insert_trades(conn: sqlite3.Connection, trades: list[dict]) -> int:
    """Insert trades, checking for duplicates first."""
    inserted = 0
    for trade in trades:
        if trade["trade_date"] and _trade_exists(conn, trade):
            continue
        try:
            conn.execute(
                """INSERT INTO congress_trades
                   (politician, party, chamber, ticker, trade_type, amount_range,
                    trade_date, disclosure_date, asset_description, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade["politician"],
                    trade["party"],
                    trade["chamber"],
                    trade["ticker"],
                    trade["trade_type"],
                    trade["amount_range"],
                    trade["trade_date"],
                    trade["disclosure_date"],
                    trade["asset_description"],
                    "capitol_trades",
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("DB error inserting trade: %s", e)
    conn.commit()
    return inserted


def collect() -> int:
    """Scrape recent congressional trades from Capitol Trades.

    Returns:
        Count of new trades inserted.
    """
    logger.info("Congressional trades collector: scraping Capitol Trades")

    conn = sqlite3.connect(DB_PATH)
    total_inserted = 0

    for page_num in range(1, MAX_PAGES_PER_RUN + 1):
        soup = _fetch_page(page_num)
        if not soup:
            if page_num == 1:
                logger.warning(
                    "Failed to fetch Capitol Trades page 1. "
                    "Site may use JS rendering or block scraping. "
                    "Consider Quiver Quantitative API ($25/mo) as fallback."
                )
            break

        trades = _parse_trades_table(soup)
        if not trades:
            logger.info("  Page %d: no trades found, stopping", page_num)
            break

        inserted = _insert_trades(conn, trades)
        total_inserted += inserted
        logger.info("  Page %d: %d trades found, %d new", page_num, len(trades), inserted)

        if page_num < MAX_PAGES_PER_RUN:
            time.sleep(RATE_LIMIT_DELAY)

    conn.close()
    logger.info("Congressional trades collector done: %d new trades", total_inserted)
    return total_inserted
