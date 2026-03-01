"""Sector mapper — maps regulatory events to affected sectors and tickers.

Uses keyword matching against the sector_keyword_map table and cross-references
the watchlist to identify affected tickers.
"""

import sqlite3

from config import DB_PATH


def _load_sector_keywords(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Load sector → keywords mapping from DB."""
    rows = conn.execute("SELECT sector, keyword FROM sector_keyword_map").fetchall()
    mappings: dict[str, list[str]] = {}
    for sector, keyword in rows:
        mappings.setdefault(sector, []).append(keyword.lower())
    return mappings


def _load_watchlist(conn: sqlite3.Connection) -> list[dict]:
    """Load active watchlist entries."""
    rows = conn.execute(
        """SELECT ticker, company_name, sector, key_agencies, key_keywords
           FROM watchlist WHERE active = 1"""
    ).fetchall()
    return [
        {
            "ticker": r[0],
            "name": r[1],
            "sector": r[2],
            "agencies": [a.strip() for a in (r[3] or "").split(",") if a.strip()],
            "keywords": [k.strip() for k in (r[4] or "").split(",") if k.strip()],
        }
        for r in rows
    ]


def map_event_to_sectors(
    title: str,
    summary: str,
    agency: str,
    conn: sqlite3.Connection | None = None,
) -> tuple[dict[str, int], list[str]]:
    """Map a regulatory event to affected sectors and tickers.

    Returns:
        (sector_scores, affected_tickers) where sector_scores is {sector: match_count}
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        sector_keywords = _load_sector_keywords(conn)
        watchlist = _load_watchlist(conn)

        text = f"{title} {summary} {agency}".lower()

        # Score sectors by keyword matches
        sector_scores: dict[str, int] = {}
        for sector, keywords in sector_keywords.items():
            matches = sum(1 for kw in keywords if kw in text)
            if matches > 0:
                sector_scores[sector] = matches

        # Find affected tickers from watchlist
        matched_sectors = set(sector_scores.keys())
        affected_tickers: list[str] = []

        for entry in watchlist:
            # Match by sector
            if entry["sector"] in matched_sectors:
                if entry["ticker"] not in affected_tickers:
                    affected_tickers.append(entry["ticker"])
                continue

            # Match by agency
            for ag in entry["agencies"]:
                if ag.lower() in text:
                    if entry["ticker"] not in affected_tickers:
                        affected_tickers.append(entry["ticker"])
                    break

        return sector_scores, affected_tickers
    finally:
        if close_conn:
            conn.close()


def tag_event(event_id: int, conn: sqlite3.Connection | None = None):
    """Auto-tag a single event with sectors and tickers."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        row = conn.execute(
            "SELECT title, summary, agency FROM regulatory_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if not row:
            return

        title, summary, agency = row
        sector_scores, tickers = map_event_to_sectors(
            title or "", summary or "", agency or "", conn
        )

        sectors_str = ",".join(sorted(sector_scores.keys())) if sector_scores else ""
        tickers_str = ",".join(tickers) if tickers else ""

        conn.execute(
            """UPDATE regulatory_events
               SET sectors = ?, tickers = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (sectors_str, tickers_str, event_id),
        )
        conn.commit()
    finally:
        if close_conn:
            conn.close()


def tag_all_untagged(conn: sqlite3.Connection | None = None) -> int:
    """Tag all events that haven't been tagged yet. Returns count tagged."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    try:
        rows = conn.execute(
            "SELECT id FROM regulatory_events WHERE sectors IS NULL OR sectors = ''"
        ).fetchall()

        for (event_id,) in rows:
            tag_event(event_id, conn)

        return len(rows)
    finally:
        if close_conn:
            conn.close()
