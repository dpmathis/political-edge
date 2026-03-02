"""Tests for execution/position_sizer.py and execution/paper_trader.py."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


class TestPositionSizer:
    """Test PositionSizer.calculate() position sizing logic."""

    def test_medium_conviction_base(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        signal = {"conviction": "medium", "position_size_modifier": 1.0}
        result = sizer.calculate(signal, equity=100000, current_exposure=0)
        # medium = 4% base * 1.0 modifier = $4,000
        assert result == 4000.0

    def test_high_conviction_base(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        signal = {"conviction": "high", "position_size_modifier": 1.0}
        result = sizer.calculate(signal, equity=100000, current_exposure=0)
        # high = 6% base * 1.0 modifier = $6,000
        assert result == 6000.0

    def test_low_conviction_base(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        signal = {"conviction": "low", "position_size_modifier": 1.0}
        result = sizer.calculate(signal, equity=100000, current_exposure=0)
        # low = 2% base * 1.0 modifier = $2,000
        assert result == 2000.0

    def test_modifier_scales_position(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        signal = {"conviction": "medium", "position_size_modifier": 1.5}
        result = sizer.calculate(signal, equity=100000, current_exposure=0)
        # medium = 4% * 1.5 = 6% = $6,000
        assert result == 6000.0

    def test_caps_at_single_position_limit(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer(max_single_position_pct=0.10)
        signal = {"conviction": "high", "position_size_modifier": 3.0}
        result = sizer.calculate(signal, equity=100000, current_exposure=0)
        # 6% * 3.0 = 18%, capped at 10% = $10,000
        assert result == 10000.0

    def test_caps_at_remaining_capacity(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer(max_total_exposure_pct=0.60)
        signal = {"conviction": "high", "position_size_modifier": 1.0}
        result = sizer.calculate(signal, equity=100000, current_exposure=58000)
        # Remaining capacity: 60000 - 58000 = $2,000
        assert result == 2000.0

    def test_zero_equity_returns_zero(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        signal = {"conviction": "high", "position_size_modifier": 1.0}
        assert sizer.calculate(signal, equity=0, current_exposure=0) == 0.0

    def test_fully_allocated_returns_zero(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer(max_total_exposure_pct=0.60)
        signal = {"conviction": "high", "position_size_modifier": 1.0}
        assert sizer.calculate(signal, equity=100000, current_exposure=60000) == 0.0

    def test_default_modifier_when_missing(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        signal = {"conviction": "medium"}  # No modifier key
        result = sizer.calculate(signal, equity=100000, current_exposure=0)
        # Default modifier is 1.0
        assert result == 4000.0

    def test_get_current_exposure(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        positions = [
            {"current_price": 100, "qty": 10},
            {"current_price": 50, "qty": 20},
        ]
        assert sizer.get_current_exposure(positions) == 2000.0

    def test_get_current_exposure_empty(self):
        from execution.position_sizer import PositionSizer
        sizer = PositionSizer()
        assert sizer.get_current_exposure([]) == 0.0


class TestPaperTrader:
    """Test PaperTrader with mocked Alpaca client."""

    def test_not_configured_without_keys(self):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.get_api_key", return_value=None):
            trader = PaperTrader()
            assert trader.is_configured is False

    def test_get_account_returns_none_when_unconfigured(self):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.get_api_key", return_value=None):
            trader = PaperTrader()
            assert trader.get_account() is None

    def test_get_positions_returns_empty_when_unconfigured(self):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.get_api_key", return_value=None):
            trader = PaperTrader()
            assert trader.get_positions() == []

    def test_execute_signal_returns_error_when_unconfigured(self):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.get_api_key", return_value=None):
            trader = PaperTrader()
            result = trader.execute_signal({"id": 1, "ticker": "SPY"}, 100000)
            assert result["status"] == "error"

    def test_get_current_price_from_db(self, db_path):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.get_api_key", return_value=None):
            with patch("execution.paper_trader.DB_PATH", db_path):
                trader = PaperTrader()
                price = trader._get_current_price("SPY")
                assert price is not None
                assert price > 0

    def test_get_current_price_unknown_ticker(self, db_path):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.get_api_key", return_value=None):
            with patch("execution.paper_trader.DB_PATH", db_path):
                trader = PaperTrader()
                price = trader._get_current_price("ZZZZZ")
                assert price is None

    def test_close_position_returns_error_when_unconfigured(self):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.get_api_key", return_value=None):
            trader = PaperTrader()
            result = trader.close_position("SPY")
            assert result["status"] == "error"

    def test_refuses_live_trading(self):
        from execution.paper_trader import PaperTrader
        with patch("execution.paper_trader.load_config", return_value={"live_trading_enabled": True}):
            with patch("execution.paper_trader.get_api_key", return_value="fake"):
                trader = PaperTrader()
                assert trader.is_configured is False
