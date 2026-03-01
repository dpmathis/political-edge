"""Paper Trading Integration via Alpaca.

Manages paper trading execution with safety controls.

Usage:
    from execution.paper_trader import PaperTrader
    trader = PaperTrader()
    account = trader.get_account()
    trader.execute_signal(signal, account["equity"])

CRITICAL: paper=True is ALWAYS set. Live trading is not supported in this codebase.
"""

import logging
import sqlite3
from datetime import date

from config import DB_PATH, load_config, get_api_key

logger = logging.getLogger(__name__)

# Safety limits
MAX_SINGLE_POSITION_PCT = 0.10  # 10% of equity
MAX_TOTAL_EXPOSURE_PCT = 0.60  # 60% of equity
DAILY_TRADE_LIMIT = 10


class PaperTrader:
    """Manages paper trading execution via Alpaca API."""

    def __init__(self):
        self.client = None
        self._initialize()

    def _initialize(self):
        """Initialize Alpaca client. Returns False if not configured."""
        cfg = load_config()

        # Safety check: refuse if live trading flag is set
        if cfg.get("live_trading_enabled", False):
            logger.error("SAFETY: live_trading_enabled is True. Paper trader refuses to run.")
            return

        key_id = get_api_key("alpaca_key_id")
        secret_key = get_api_key("alpaca_secret_key")

        if not key_id or not secret_key:
            logger.debug("Alpaca API keys not configured")
            return

        try:
            from alpaca.trading.client import TradingClient
            self.client = TradingClient(
                api_key=key_id,
                secret_key=secret_key,
                paper=True,  # ALWAYS paper trading
            )
            logger.info("Alpaca paper trading client initialized")
        except ImportError:
            logger.warning("alpaca-py not installed. Run: pip install alpaca-py")
        except Exception as e:
            logger.error("Failed to initialize Alpaca client: %s", e)

    @property
    def is_configured(self) -> bool:
        return self.client is not None

    def get_account(self) -> dict | None:
        """Return account equity, buying power, positions."""
        if not self.is_configured:
            return None

        try:
            account = self.client.get_account()
            return {
                "equity": float(account.equity),
                "buying_power": float(account.buying_power),
                "cash": float(account.cash),
                "portfolio_value": float(account.portfolio_value),
            }
        except Exception as e:
            logger.error("Failed to get account: %s", e)
            return None

    def get_positions(self) -> list[dict]:
        """Return current open positions."""
        if not self.is_configured:
            return []

        try:
            positions = self.client.get_all_positions()
            return [
                {
                    "ticker": p.symbol,
                    "qty": int(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "pnl": float(p.unrealized_pl),
                    "pnl_pct": float(p.unrealized_plpc),
                    "market_value": float(p.market_value),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            return []

    def execute_signal(self, signal: dict, account_equity: float) -> dict:
        """Execute a trading signal.

        Args:
            signal: dict with keys: id, ticker, direction, conviction, position_size_modifier
            account_equity: current account equity for position sizing

        Returns:
            dict with order details or error info
        """
        if not self.is_configured:
            return {"status": "error", "reason": "Alpaca not configured"}

        # Check daily trade limit
        conn = sqlite3.connect(DB_PATH)
        today_trades = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE date(created_at) = date('now')"
        ).fetchone()[0]

        if today_trades >= DAILY_TRADE_LIMIT:
            conn.close()
            return {"status": "skipped", "reason": f"Daily trade limit ({DAILY_TRADE_LIMIT}) reached"}

        # Calculate position size
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        positions = self.get_positions()
        current_exposure = sizer.get_current_exposure(positions)
        position_dollars = sizer.calculate(signal, account_equity, current_exposure)

        if position_dollars < 100:  # Minimum $100 trade
            conn.close()
            return {"status": "skipped", "reason": "Position size too small"}

        ticker = signal["ticker"]
        direction = signal.get("direction", "long")

        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Get current price to calculate shares
            current_price = self._get_current_price(ticker)
            if not current_price or current_price <= 0:
                conn.close()
                return {"status": "error", "reason": f"Could not get price for {ticker}"}

            shares = int(position_dollars / current_price)
            if shares < 1:
                conn.close()
                return {"status": "skipped", "reason": "Position size less than 1 share"}

            side = OrderSide.BUY if direction == "long" else OrderSide.SELL
            order_request = MarketOrderRequest(
                symbol=ticker,
                qty=shares,
                side=side,
                time_in_force=TimeInForce.DAY,
            )
            order = self.client.submit_order(order_request)

            # Record in paper_trades table
            conn.execute(
                """INSERT INTO paper_trades
                   (signal_id, broker, order_id, ticker, side, quantity, price, status, order_type)
                   VALUES (?, 'alpaca', ?, ?, ?, ?, ?, ?, 'market')""",
                (
                    signal.get("id"),
                    str(order.id),
                    ticker,
                    direction,
                    shares,
                    current_price,
                    str(order.status),
                ),
            )

            # Update signal status
            conn.execute(
                """UPDATE trading_signals SET
                   status = 'active', entry_price = ?, entry_date = ?
                   WHERE id = ?""",
                (current_price, date.today().isoformat(), signal["id"]),
            )

            conn.commit()
            conn.close()

            logger.info("Executed %s %d shares of %s at ~$%.2f", direction, shares, ticker, current_price)
            return {
                "status": "submitted",
                "order_id": str(order.id),
                "shares": shares,
                "estimated_price": current_price,
                "position_dollars": position_dollars,
            }

        except Exception as e:
            conn.close()
            logger.error("Failed to execute signal for %s: %s", ticker, e)
            return {"status": "error", "reason": str(e)}

    def _get_current_price(self, ticker: str) -> float | None:
        """Get latest price from market_data table."""
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT close FROM market_data WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        conn.close()
        return row[0] if row else None

    def close_position(self, ticker: str) -> dict:
        """Close all shares of a position."""
        if not self.is_configured:
            return {"status": "error", "reason": "Alpaca not configured"}

        try:
            self.client.close_position(ticker)
            logger.info("Closed position in %s", ticker)
            return {"status": "closed", "ticker": ticker}
        except Exception as e:
            logger.error("Failed to close position %s: %s", ticker, e)
            return {"status": "error", "reason": str(e)}
