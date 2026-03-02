"""Tests for analysis/research/base.py — statistical helper functions."""

import numpy as np
import pandas as pd

from analysis.research.base import (
    SECTOR_ETF_MAP,
    run_cross_sectional_regression,
    run_granger_causality,
    run_wilcoxon_ranksum,
)


def test_wilcoxon_ranksum_significant():
    group_a = [1.0, 2.0, 3.0, 4.0, 5.0]
    group_b = [10.0, 11.0, 12.0, 13.0, 14.0]
    result = run_wilcoxon_ranksum(group_a, group_b)
    assert result["significant"]
    assert result["p_value"] < 0.05


def test_wilcoxon_ranksum_insufficient():
    result = run_wilcoxon_ranksum([1.0, 2.0], [3.0, 4.0, 5.0])
    assert "error" in result
    assert result["significant"] is False


def test_cross_sectional_regression():
    np.random.seed(42)
    n = 50
    x = np.random.randn(n)
    y = 2 * x + np.random.randn(n) * 0.3
    df = pd.DataFrame({"y": y, "x": x})
    result = run_cross_sectional_regression(df, y_col="y", x_cols=["x"])
    assert result["r_squared"] > 0.5
    assert abs(result["coefficients"]["x"] - 2.0) < 0.5


def test_granger_causality_insufficient():
    cause = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    effect = pd.Series([2.0, 3.0, 4.0, 5.0, 6.0])
    result = run_granger_causality(cause, effect)
    assert "error" in result
    assert result["significant"] is False


def test_sector_etf_map():
    assert "Defense" in SECTOR_ETF_MAP
    defense_tickers = SECTOR_ETF_MAP["Defense"]
    assert "LMT" in defense_tickers
    assert "RTX" in defense_tickers
