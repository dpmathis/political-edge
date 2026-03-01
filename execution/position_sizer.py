"""Position Sizer.

Calculates position sizes with macro regime overlay and risk limits.

Usage:
    from execution.position_sizer import PositionSizer
    sizer = PositionSizer()
    dollars = sizer.calculate(signal, equity=100000, current_exposure=30000)
"""

import logging
import sqlite3

from config import DB_PATH

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculates position sizes with macro regime overlay."""

    def __init__(
        self,
        max_single_position_pct: float = 0.10,
        max_total_exposure_pct: float = 0.60,
    ):
        self.max_single = max_single_position_pct
        self.max_total = max_total_exposure_pct

    def calculate(self, signal: dict, equity: float, current_exposure: float) -> float:
        """Returns dollar amount to allocate.

        Checks:
        1. Single position limit (10%)
        2. Total exposure limit (60%)
        3. Macro regime modifier
        4. Conviction level
        """
        if equity <= 0:
            return 0.0

        if current_exposure >= self.max_total * equity:
            return 0.0  # Portfolio fully allocated

        remaining_capacity = (self.max_total * equity) - current_exposure
        base = self._conviction_to_base(signal.get("conviction", "medium"))
        modifier = signal.get("position_size_modifier", 1.0)
        sized = equity * base * modifier
        capped = min(sized, self.max_single * equity, remaining_capacity)
        return max(capped, 0.0)

    def _conviction_to_base(self, conviction: str) -> float:
        return {"low": 0.02, "medium": 0.04, "high": 0.06}.get(conviction, 0.04)

    def get_current_exposure(self, positions: list[dict]) -> float:
        """Calculate current exposure from open positions."""
        return sum(abs(p.get("current_price", 0) * p.get("qty", 0)) for p in positions)
