"""Congressional trading collector via Capitol Trades.

Scrapes publicly available trading data from capitoltrades.com.

Usage:
    from collectors import congress_trades
    congress_trades.collect()
"""

import logging
import re
import sqlite3
import time

import requests
from bs4 import BeautifulSoup

from config import DB_PATH

logger = logging.getLogger(__name__)

BASE_URL = "https://www.capitoltrades.com/trades"
RATE_LIMIT_DELAY = 3.0
MAX_PAGES_PER_RUN = 10
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_page(page_num: int) -> BeautifulSoup | None:
    """Fetch and parse a trades page from Capitol Trades."""
    url = f"{BASE_URL}?page={page_num}" if page_num > 1 else BASE_URL
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 403:
                logger.warning("Capitol Trades returned 403 (blocked).")
                return None
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.exceptions.RequestException as e:
            logger.error("Request error page %d: %s (attempt %d)", page_num, e, attempt + 1)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return None
    return None


def _parse_date_cell(text: str) -> str | None:
    """Parse Capitol Trades date like '27 Feb2026' or '27 Feb | 2026'.

    get_text(strip=True) often drops the separator between day/month and year,
    producing '27 Feb2026'. We normalize by inserting a space before the 4-digit year.
    """
    text = text.replace("|", " ").strip()
    # Insert space before 4-digit year if missing (e.g. "27 Feb2026" -> "27 Feb 2026")
    text = re.sub(r"(\d{4})", r" \1", text)
    text = " ".join(text.split())  # collapse multiple spaces
    for fmt in ("%d %b %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_ticker(text: str) -> str | None:
    """Extract ticker from issuer cell like 'VMware Inc | VMW:US'."""
    # Look for TICKER:EXCHANGE pattern
    match = re.search(r"([A-Z]{1,5}):[A-Z]{2}", text)
    if match:
        return match.group(1)
    return None


def _parse_trades_table(soup: BeautifulSoup) -> list[dict]:
    """Extract trade rows from Capitol Trades HTML table."""
    trades = []

    table = soup.select_one("table")
    if not table:
        return trades

    tbody = table.select_one("tbody")
    if not tbody:
        return trades

    for tr in tbody.select("tr"):
        cells = tr.select("td")
        if len(cells) < 8:
            continue

        try:
            # Cell 0: Politician | Party | Chamber | State
            cell0_text = cells[0].get_text(separator="|", strip=True)
            parts = [p.strip() for p in cell0_text.split("|")]
            politician_link = cells[0].select_one("a")
            politician = politician_link.get_text(strip=True) if politician_link else (parts[0] if parts else None)

            party = None
            chamber = None
            for p in parts[1:]:
                if p in ("Democrat", "Republican", "Independent"):
                    party = p[0]  # D, R, I
                elif p in ("House", "Senate"):
                    chamber = p

            # Cell 1: Company Name | TICKER:EXCHANGE
            cell1_text = cells[1].get_text(separator="|", strip=True)
            ticker = _extract_ticker(cell1_text)
            asset_description = cells[1].select_one("a")
            asset_desc = asset_description.get_text(strip=True) if asset_description else None

            # Cell 2: Published date (disclosure date)
            disclosure_date = _parse_date_cell(cells[2].get_text(strip=True))

            # Cell 3: Traded date
            trade_date = _parse_date_cell(cells[3].get_text(strip=True))

            # Cell 5: Owner (Self, Spouse, Joint, Undisclosed, Child)
            # Cell 6: Type (buy, sell, exchange)
            trade_type_text = cells[6].get_text(strip=True).lower()
            trade_type = "buy" if trade_type_text in ("buy", "purchase") else "sell" if "sell" in trade_type_text or "sale" in trade_type_text else trade_type_text

            # Cell 7: Size (amount range like "1K–15K")
            amount_range = cells[7].get_text(strip=True)
            if amount_range and amount_range != "N/A":
                amount_range = "$" + amount_range.replace("–", " – $")
            else:
                amount_range = None

            if not politician:
                continue

            trades.append({
                "politician": politician,
                "party": party,
                "chamber": chamber,
                "ticker": ticker,
                "trade_type": trade_type,
                "amount_range": amount_range,
                "trade_date": trade_date,
                "disclosure_date": disclosure_date,
                "asset_description": asset_desc,
            })
        except Exception as e:
            logger.debug("Error parsing row: %s", e)
            continue

    return trades


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
        if not trade["politician"] or not trade["ticker"]:
            continue
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
                    "Site may block scraping."
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
