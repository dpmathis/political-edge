"""End-to-end integration tests for the paper trading flow.

Tests the full lifecycle: signal execution, daily limits, position expiration,
trade reconciliation, and safety checks.  All tests use an isolated temporary
SQLite database and mock the Alpaca TradingClient so no real API calls are made.
"""

import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_order(order_id="test-order-123", status="accepted", filled_avg_price=None):
    """Return a mock Alpaca order object."""
    order = SimpleNamespace(
        id=order_id,
        status=status,
        filled_avg_price=filled_avg_price,
    )
    return order


def _make_mock_client():
    """Return a MagicMock configured to behave like TradingClient."""
    client = MagicMock()
    client.submit_order.return_value = _make_mock_order()
    client.get_all_positions.return_value = []
    client.close_position.return_value = None
    client.get_order_by_id.return_value = _make_mock_order(
        status="filled", filled_avg_price=152.50,
    )
    return client


def _seed_pending_signal_and_market_data(db_path, ticker="LMT", conviction="high"):
    """Insert a pending trading signal and recent market data into the test DB.

    Returns the signal id.
    """
    conn = sqlite3.connect(db_path)
    today = date.today().isoformat()

    # Market data row so _get_current_price can find a price
    conn.execute(
        """INSERT OR IGNORE INTO market_data
           (ticker, date, open, high, low, close, adj_close, volume)
           VALUES (?, ?, 150.0, 155.0, 149.0, 152.0, 152.0, 5000000)""",
        (ticker, today),
    )

    cur = conn.execute(
        """INSERT INTO trading_signals
           (signal_date, ticker, signal_type, direction, conviction,
            status, position_size_modifier, time_horizon_days)
           VALUES (?, ?, 'regulatory_event', 'long', ?, 'pending', 1.0, 10)""",
        (today, ticker, conviction),
    )
    signal_id = cur.lastrowid
    conn.commit()
    conn.close()
    return signal_id


def _build_paper_trader(db_path, mock_client):
    """Construct a PaperTrader whose Alpaca client is replaced by *mock_client*.

    Bypasses the normal __init__ flow (which calls Alpaca for real) by patching
    load_config, get_api_key, and the TradingClient constructor.
    """
    from execution.paper_trader import PaperTrader

    with (
        patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": False}),
        patch("execution.paper_trader.get_api_key", return_value="fake-key"),
        patch("execution.paper_trader.DB_PATH", db_path),
        patch("alpaca.trading.client.TradingClient", return_value=mock_client),
    ):
        trader = PaperTrader()

    # Ensure client is set even if import path differs between envs
    trader.client = mock_client
    return trader


# ---------------------------------------------------------------------------
# 1. TestExecuteSignalFlow
# ---------------------------------------------------------------------------

class TestExecuteSignalFlow:
    """Execute a single signal and verify DB state afterwards."""

    def test_paper_trade_row_created(self, db_path):
        """paper_trades row has correct ticker, side, and quantity."""
        mock_client = _make_mock_client()
        signal_id = _seed_pending_signal_and_market_data(db_path, ticker="LMT", conviction="high")

        trader = _build_paper_trader(db_path, mock_client)

        signal = {
            "id": signal_id,
            "ticker": "LMT",
            "direction": "long",
            "conviction": "high",
            "position_size_modifier": 1.0,
        }

        with patch("execution.paper_trader.DB_PATH", db_path):
            result = trader.execute_signal(signal, account_equity=100_000)

        assert result["status"] == "submitted"

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT signal_id, ticker, side, quantity, status FROM paper_trades WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == signal_id   # signal_id
        assert row[1] == "LMT"      # ticker
        assert row[2] == "long"      # side
        assert row[3] > 0            # quantity (positive int shares)
        assert row[4] == "accepted"  # status echoed from mock order

    def test_signal_status_updated_to_active(self, db_path):
        """trading_signals row transitions to active with entry_price and entry_date."""
        mock_client = _make_mock_client()
        signal_id = _seed_pending_signal_and_market_data(db_path, ticker="LMT", conviction="medium")

        trader = _build_paper_trader(db_path, mock_client)

        signal = {
            "id": signal_id,
            "ticker": "LMT",
            "direction": "long",
            "conviction": "medium",
            "position_size_modifier": 1.0,
        }

        with patch("execution.paper_trader.DB_PATH", db_path):
            trader.execute_signal(signal, account_equity=100_000)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, entry_price, entry_date FROM trading_signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        conn.close()

        assert row[0] == "active"
        assert row[1] is not None and row[1] > 0   # entry_price
        assert row[2] == date.today().isoformat()   # entry_date

    def test_position_sizing_respects_conviction(self, db_path):
        """Higher conviction should produce a larger share count (same equity)."""
        results = {}

        for conviction in ("low", "medium", "high"):
            mock_client = _make_mock_client()
            signal_id = _seed_pending_signal_and_market_data(
                db_path, ticker="LMT", conviction=conviction,
            )
            trader = _build_paper_trader(db_path, mock_client)
            signal = {
                "id": signal_id,
                "ticker": "LMT",
                "direction": "long",
                "conviction": conviction,
                "position_size_modifier": 1.0,
            }

            with patch("execution.paper_trader.DB_PATH", db_path):
                res = trader.execute_signal(signal, account_equity=100_000)

            if res["status"] == "submitted":
                results[conviction] = res["shares"]

        # At minimum medium and high should both execute; high > medium
        assert "high" in results and "medium" in results
        assert results["high"] > results["medium"]

    def test_short_direction_sets_sell_side(self, db_path):
        """A short signal should record side='short' in paper_trades."""
        mock_client = _make_mock_client()
        signal_id = _seed_pending_signal_and_market_data(db_path, ticker="LMT", conviction="high")

        trader = _build_paper_trader(db_path, mock_client)

        signal = {
            "id": signal_id,
            "ticker": "LMT",
            "direction": "short",
            "conviction": "high",
            "position_size_modifier": 1.0,
        }

        with patch("execution.paper_trader.DB_PATH", db_path):
            result = trader.execute_signal(signal, account_equity=100_000)

        assert result["status"] == "submitted"

        # Verify OrderSide.SELL was used (inspecting the call to submit_order)
        call_args = mock_client.submit_order.call_args
        order_request = call_args[0][0] if call_args[0] else call_args[1].get("order_data")
        assert order_request.side.name == "SELL" or str(order_request.side).lower() == "sell"


# ---------------------------------------------------------------------------
# 2. TestDailyTradeLimit
# ---------------------------------------------------------------------------

class TestDailyTradeLimit:
    """Verify the per-day trade cap (DAILY_TRADE_LIMIT = 10)."""

    def test_rejects_trade_when_limit_reached(self, db_path):
        """After 10 trades today, the 11th should be rejected."""
        conn = sqlite3.connect(db_path)

        # Insert 10 paper_trades with today's timestamp
        for i in range(10):
            conn.execute(
                """INSERT INTO paper_trades
                   (signal_id, broker, order_id, ticker, side, quantity, price, status, order_type)
                   VALUES (?, 'alpaca', ?, 'SPY', 'long', 10, 450.0, 'filled', 'market')""",
                (i + 1000, f"order-{i}"),
            )
        conn.commit()
        conn.close()

        mock_client = _make_mock_client()
        signal_id = _seed_pending_signal_and_market_data(db_path, ticker="LMT", conviction="high")
        trader = _build_paper_trader(db_path, mock_client)

        signal = {
            "id": signal_id,
            "ticker": "LMT",
            "direction": "long",
            "conviction": "high",
            "position_size_modifier": 1.0,
        }

        with patch("execution.paper_trader.DB_PATH", db_path):
            result = trader.execute_signal(signal, account_equity=100_000)

        assert result["status"] == "skipped"
        assert "Daily trade limit" in result["reason"]

    def test_allows_trade_under_limit(self, db_path):
        """With fewer than 10 trades today, execution should proceed normally."""
        conn = sqlite3.connect(db_path)
        # Insert only 5 trades for today
        for i in range(5):
            conn.execute(
                """INSERT INTO paper_trades
                   (signal_id, broker, order_id, ticker, side, quantity, price, status, order_type)
                   VALUES (?, 'alpaca', ?, 'SPY', 'long', 10, 450.0, 'filled', 'market')""",
                (i + 2000, f"order-limit-{i}"),
            )
        conn.commit()
        conn.close()

        mock_client = _make_mock_client()
        signal_id = _seed_pending_signal_and_market_data(db_path, ticker="LMT", conviction="high")
        trader = _build_paper_trader(db_path, mock_client)

        signal = {
            "id": signal_id,
            "ticker": "LMT",
            "direction": "long",
            "conviction": "high",
            "position_size_modifier": 1.0,
        }

        with patch("execution.paper_trader.DB_PATH", db_path):
            result = trader.execute_signal(signal, account_equity=100_000)

        assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# 3. TestCloseExpiredPositions
# ---------------------------------------------------------------------------

class TestCloseExpiredPositions:
    """Signals past their time_horizon_days should be closed with P&L."""

    def test_expired_signal_closed_with_pnl(self, db_path):
        """An active signal that exceeded time_horizon_days is closed."""
        conn = sqlite3.connect(db_path)
        entry_date = (date.today() - timedelta(days=20)).isoformat()

        # Insert an active signal that entered 20 days ago with a 10-day horizon
        cur = conn.execute(
            """INSERT INTO trading_signals
               (signal_date, ticker, signal_type, direction, conviction,
                status, entry_price, entry_date, time_horizon_days, position_size_modifier)
               VALUES (?, 'LMT', 'regulatory_event', 'long', 'high',
                       'active', 150.0, ?, 10, 1.0)""",
            (entry_date, entry_date),
        )
        signal_id = cur.lastrowid

        # Ensure market_data has a recent close for LMT so exit_price resolves
        today = date.today().isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO market_data
               (ticker, date, open, high, low, close, adj_close, volume)
               VALUES ('LMT', ?, 155.0, 158.0, 154.0, 157.0, 157.0, 3000000)""",
            (today,),
        )
        conn.commit()
        conn.close()

        mock_client = _make_mock_client()
        trader = _build_paper_trader(db_path, mock_client)

        with patch("execution.paper_trader.DB_PATH", db_path):
            closed_count = trader.close_expired_positions()

        assert closed_count >= 1

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, exit_price, exit_date, pnl_percent, holding_days FROM trading_signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        conn.close()

        assert row[0] == "closed"
        assert row[1] is not None and row[1] > 0  # exit_price
        assert row[2] == date.today().isoformat()  # exit_date
        assert row[3] is not None                  # pnl_percent calculated
        assert row[4] is not None and row[4] >= 20 # holding_days

    def test_non_expired_signal_untouched(self, db_path):
        """An active signal still within its horizon should remain active."""
        conn = sqlite3.connect(db_path)
        entry_date = (date.today() - timedelta(days=2)).isoformat()

        cur = conn.execute(
            """INSERT INTO trading_signals
               (signal_date, ticker, signal_type, direction, conviction,
                status, entry_price, entry_date, time_horizon_days, position_size_modifier)
               VALUES (?, 'LMT', 'regulatory_event', 'long', 'medium',
                       'active', 150.0, ?, 30, 1.0)""",
            (entry_date, entry_date),
        )
        signal_id = cur.lastrowid
        conn.commit()
        conn.close()

        mock_client = _make_mock_client()
        trader = _build_paper_trader(db_path, mock_client)

        with patch("execution.paper_trader.DB_PATH", db_path):
            trader.close_expired_positions()

        conn = sqlite3.connect(db_path)
        status = conn.execute(
            "SELECT status FROM trading_signals WHERE id = ?", (signal_id,),
        ).fetchone()[0]
        conn.close()

        assert status == "active"

    def test_pnl_negative_for_short_gone_up(self, db_path):
        """A short signal where price went up should show negative P&L."""
        conn = sqlite3.connect(db_path)
        entry_date = (date.today() - timedelta(days=15)).isoformat()

        cur = conn.execute(
            """INSERT INTO trading_signals
               (signal_date, ticker, signal_type, direction, conviction,
                status, entry_price, entry_date, time_horizon_days, position_size_modifier)
               VALUES (?, 'LMT', 'regulatory_event', 'short', 'high',
                       'active', 150.0, ?, 10, 1.0)""",
            (entry_date, entry_date),
        )
        signal_id = cur.lastrowid

        today = date.today().isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO market_data
               (ticker, date, open, high, low, close, adj_close, volume)
               VALUES ('LMT', ?, 155.0, 160.0, 154.0, 160.0, 160.0, 3000000)""",
            (today,),
        )
        conn.commit()
        conn.close()

        mock_client = _make_mock_client()
        trader = _build_paper_trader(db_path, mock_client)

        with patch("execution.paper_trader.DB_PATH", db_path):
            trader.close_expired_positions()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, pnl_percent FROM trading_signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        conn.close()

        assert row[0] == "closed"
        # Price went from 150 -> 160 on a short: P&L should be negative
        assert row[1] < 0


# ---------------------------------------------------------------------------
# 4. TestReconcileTrades
# ---------------------------------------------------------------------------

class TestReconcileTrades:
    """Verify reconcile_trades() syncs paper_trades with Alpaca order status."""

    def test_submitted_trade_updated_to_filled(self, db_path):
        """A trade with status 'submitted' should be updated to 'filled'."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO paper_trades
               (signal_id, broker, order_id, ticker, side, quantity, price, status, order_type)
               VALUES (1, 'alpaca', 'order-reconcile-1', 'LMT', 'long', 10, 150.0, 'submitted', 'market')""",
        )
        conn.commit()

        trade_id = conn.execute(
            "SELECT id FROM paper_trades WHERE order_id = 'order-reconcile-1'"
        ).fetchone()[0]
        conn.close()

        mock_client = _make_mock_client()
        mock_client.get_order_by_id.return_value = _make_mock_order(
            order_id="order-reconcile-1",
            status="filled",
            filled_avg_price=152.50,
        )

        trader = _build_paper_trader(db_path, mock_client)

        with patch("execution.paper_trader.DB_PATH", db_path):
            updated = trader.reconcile_trades()

        assert updated >= 1

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, filled_price FROM paper_trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        conn.close()

        assert row[0] == "filled"
        assert row[1] == pytest.approx(152.50)

    def test_already_filled_trade_not_reprocessed(self, db_path):
        """Trades already in 'filled' status should not be queried again."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO paper_trades
               (signal_id, broker, order_id, ticker, side, quantity, price,
                filled_price, status, order_type)
               VALUES (2, 'alpaca', 'order-already-filled', 'SPY', 'long', 5, 450.0,
                       451.0, 'filled', 'market')""",
        )
        conn.commit()
        conn.close()

        mock_client = _make_mock_client()
        trader = _build_paper_trader(db_path, mock_client)

        with patch("execution.paper_trader.DB_PATH", db_path):
            trader.reconcile_trades()

        # get_order_by_id should NOT have been called for the already-filled order
        for call in mock_client.get_order_by_id.call_args_list:
            assert call[0][0] != "order-already-filled"

    def test_canceled_order_updated(self, db_path):
        """A submitted trade whose Alpaca order was canceled should reflect that."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO paper_trades
               (signal_id, broker, order_id, ticker, side, quantity, price, status, order_type)
               VALUES (3, 'alpaca', 'order-canceled-1', 'LMT', 'long', 10, 150.0, 'submitted', 'market')""",
        )
        conn.commit()

        trade_id = conn.execute(
            "SELECT id FROM paper_trades WHERE order_id = 'order-canceled-1'"
        ).fetchone()[0]
        conn.close()

        mock_client = _make_mock_client()
        mock_client.get_order_by_id.return_value = _make_mock_order(
            order_id="order-canceled-1",
            status="canceled",
            filled_avg_price=None,
        )

        trader = _build_paper_trader(db_path, mock_client)

        with patch("execution.paper_trader.DB_PATH", db_path):
            updated = trader.reconcile_trades()

        assert updated >= 1

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, filled_price FROM paper_trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        conn.close()

        assert row[0] == "canceled"
        assert row[1] is None


# ---------------------------------------------------------------------------
# 5. TestSafetyChecks
# ---------------------------------------------------------------------------

class TestSafetyChecks:
    """Verify the paper trader's safety mechanisms."""

    def test_live_trading_flag_prevents_initialization(self):
        """When live_trading_enabled is True, is_configured must be False."""
        from execution.paper_trader import PaperTrader

        with (
            patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": True}),
            patch("execution.paper_trader.get_api_key", return_value="fake-key"),
        ):
            trader = PaperTrader()

        assert trader.is_configured is False
        assert trader.client is None

    def test_missing_api_keys_prevents_initialization(self):
        """Without Alpaca API keys the client stays None."""
        from execution.paper_trader import PaperTrader

        with (
            patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": False}),
            patch("execution.paper_trader.get_api_key", return_value=None),
        ):
            trader = PaperTrader()

        assert trader.is_configured is False

    def test_execute_signal_error_when_unconfigured(self):
        """Calling execute_signal on an unconfigured trader returns an error dict."""
        from execution.paper_trader import PaperTrader

        with (
            patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": True}),
            patch("execution.paper_trader.get_api_key", return_value="fake-key"),
        ):
            trader = PaperTrader()

        result = trader.execute_signal({"id": 1, "ticker": "SPY"}, 100_000)
        assert result["status"] == "error"
        assert "not configured" in result["reason"].lower() or "Alpaca" in result["reason"]

    def test_reconcile_returns_zero_when_unconfigured(self):
        """reconcile_trades() should return 0 when not configured."""
        from execution.paper_trader import PaperTrader

        with (
            patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": True}),
            patch("execution.paper_trader.get_api_key", return_value="fake-key"),
        ):
            trader = PaperTrader()

        assert trader.reconcile_trades() == 0

    def test_close_expired_returns_zero_when_unconfigured(self):
        """close_expired_positions() should return 0 when not configured."""
        from execution.paper_trader import PaperTrader

        with (
            patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": True}),
            patch("execution.paper_trader.get_api_key", return_value="fake-key"),
        ):
            trader = PaperTrader()

        assert trader.close_expired_positions() == 0

    def test_paper_always_true_in_client_construction(self):
        """Confirm TradingClient is always called with paper=True."""
        from execution.paper_trader import PaperTrader

        with (
            patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": False}),
            patch("execution.paper_trader.get_api_key", return_value="fake-key"),
            patch("alpaca.trading.client.TradingClient") as MockTC,
        ):
            MockTC.return_value = MagicMock()
            PaperTrader()

            MockTC.assert_called_once()
            call_kwargs = MockTC.call_args[1]
            assert call_kwargs["paper"] is True
