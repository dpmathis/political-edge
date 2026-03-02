"""Tests for Congress Trades page SQL query patterns."""

import sqlite3
from datetime import date, timedelta


def _today_offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _seed_trade(conn, politician="Sen. Smith", party="D", chamber="Senate",
                ticker="LMT", trade_type="purchase", days_ago=5, disclosure_delay=15):
    """Insert a congress trade."""
    trade_date = _today_offset(-days_ago)
    disclosure_date = _today_offset(-days_ago + disclosure_delay)
    conn.execute(
        """INSERT INTO congress_trades
           (politician, party, chamber, ticker, trade_type, amount_range,
            trade_date, disclosure_date, asset_description)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (politician, party, chamber, ticker, trade_type, "$1,001 - $15,000",
         trade_date, disclosure_date, f"{ticker} stock"),
    )
    conn.commit()


class TestCongressTradesQueries:
    """Tests for the SQL query patterns used by the Congress Trades page."""

    def test_summary_metrics_with_data(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_trade(conn, politician="Sen. A", trade_type="purchase")
        _seed_trade(conn, politician="Sen. B", trade_type="sale")
        _seed_trade(conn, politician="Sen. A", trade_type="purchase")

        total = conn.execute("SELECT COUNT(*) FROM congress_trades").fetchone()[0]
        unique = conn.execute("SELECT COUNT(DISTINCT politician) FROM congress_trades").fetchone()[0]
        buys = conn.execute("SELECT COUNT(*) FROM congress_trades WHERE trade_type = 'purchase'").fetchone()[0]
        sells = conn.execute("SELECT COUNT(*) FROM congress_trades WHERE trade_type = 'sale'").fetchone()[0]
        conn.close()

        assert total == 3
        assert unique == 2
        assert buys == 2
        assert sells == 1

    def test_summary_metrics_empty_table(self, db_path):
        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT COUNT(*) FROM congress_trades").fetchone()[0]
        conn.close()
        assert total == 0

    def test_recent_trades_ordered_by_disclosure(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_trade(conn, politician="Sen. Old", days_ago=10, disclosure_delay=20)
        _seed_trade(conn, politician="Sen. New", days_ago=2, disclosure_delay=5)

        rows = conn.execute(
            """SELECT politician, disclosure_date FROM congress_trades
               WHERE disclosure_date >= ?
               ORDER BY disclosure_date DESC""",
            (_today_offset(-30),),
        ).fetchall()
        conn.close()

        assert len(rows) >= 2
        # Most recent disclosure first
        assert rows[0][1] >= rows[1][1]

    def test_top_politicians_grouping(self, db_path):
        conn = sqlite3.connect(db_path)
        for _ in range(5):
            _seed_trade(conn, politician="Sen. Active", ticker="AAPL")
        _seed_trade(conn, politician="Sen. Rare", ticker="GOOGL")

        rows = conn.execute(
            """SELECT politician, COUNT(*) as cnt FROM congress_trades
               GROUP BY politician ORDER BY cnt DESC LIMIT 10"""
        ).fetchall()
        conn.close()

        assert rows[0][0] == "Sen. Active"
        assert rows[0][1] == 5

    def test_party_breakdown_counts(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_trade(conn, politician="Dem 1", party="D")
        _seed_trade(conn, politician="Dem 2", party="D")
        _seed_trade(conn, politician="Rep 1", party="R")

        rows = conn.execute(
            "SELECT party, COUNT(*) as cnt FROM congress_trades GROUP BY party ORDER BY cnt DESC"
        ).fetchall()
        conn.close()

        party_counts = {r[0]: r[1] for r in rows}
        assert party_counts["D"] == 2
        assert party_counts["R"] == 1

    def test_top_tickers_grouping(self, db_path):
        conn = sqlite3.connect(db_path)
        for _ in range(4):
            _seed_trade(conn, ticker="MSFT")
        _seed_trade(conn, ticker="AAPL")

        rows = conn.execute(
            """SELECT ticker, COUNT(*) as cnt FROM congress_trades
               GROUP BY ticker ORDER BY cnt DESC LIMIT 10"""
        ).fetchall()
        conn.close()

        assert rows[0][0] == "MSFT"
        assert rows[0][1] == 4

    def test_watchlist_linked_trades(self, db_path):
        conn = sqlite3.connect(db_path)
        # LMT is already in watchlist from conftest
        _seed_trade(conn, ticker="LMT", politician="Sen. Defense")
        _seed_trade(conn, ticker="UNKNOWN", politician="Sen. Other")

        rows = conn.execute(
            """SELECT ct.politician, ct.ticker, w.company_name
               FROM congress_trades ct
               JOIN watchlist w ON ct.ticker = w.ticker
               ORDER BY ct.trade_date DESC"""
        ).fetchall()
        conn.close()

        tickers = [r[1] for r in rows]
        assert "LMT" in tickers
        assert "UNKNOWN" not in tickers

    def test_disclosure_delay_calculation(self, db_path):
        conn = sqlite3.connect(db_path)
        # Trade 10 days ago, disclosed 5 days ago → 5 day delay
        _seed_trade(conn, days_ago=10, disclosure_delay=5)

        row = conn.execute(
            """SELECT AVG(julianday(disclosure_date) - julianday(trade_date))
               FROM congress_trades
               WHERE trade_date IS NOT NULL AND disclosure_date IS NOT NULL"""
        ).fetchone()
        conn.close()

        assert row[0] is not None
        assert abs(row[0] - 5.0) < 0.1
