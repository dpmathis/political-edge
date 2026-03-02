"""Shared utilities for research reports."""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm

from config import DB_PATH
from analysis.event_study import EventStudyResults

logger = logging.getLogger(__name__)


@dataclass
class ResearchReportResults:
    """Container for a complete research report output."""

    report_name: str
    report_number: int
    hypothesis: str
    event_studies: list[EventStudyResults] = field(default_factory=list)
    additional_analyses: dict[str, Any] = field(default_factory=dict)
    summary_stats: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    signal_parameters: dict[str, Any] = field(default_factory=dict)

    def save_all_to_db(self, db_path: str | None = None) -> list[int]:
        """Save all event studies to the database. Returns list of study_ids."""
        ids = []
        for es in self.event_studies:
            ids.append(es.save_to_db(db_path or DB_PATH))
        return ids

    def summary(self) -> str:
        lines = [f"=== Report {self.report_number}: {self.report_name} ==="]
        lines.append(f"Hypothesis: {self.hypothesis}")
        lines.append(f"Sub-studies: {len(self.event_studies)}")
        for es in self.event_studies:
            sig = "YES" if es.is_significant() else "NO"
            lines.append(
                f"  {es.study_name}: N={es.num_events}, "
                f"CAR={es.mean_car:+.2%}, p={es.p_value:.4f}, sig={sig}"
            )
        for key, val in self.additional_analyses.items():
            if isinstance(val, dict) and "significant" in val:
                lines.append(f"  [{key}]: significant={val['significant']}, p={val.get('p_value', 'N/A')}")
            else:
                lines.append(f"  [{key}]: {val}")
        return "\n".join(lines)


# ── Sector / Agency Mapping ─────────────────────────────────────────

SECTOR_ETF_MAP = {
    "Defense": ["LMT", "RTX", "GD", "NOC", "BA"],
    "Energy": ["XOM", "XLE", "NEE"],
    "Healthcare": ["UNH", "HUM", "PFE", "LLY"],
    "Technology": ["GOOGL", "META", "XLK"],
    "Industrials": ["XLI"],
    "Financials": ["XLF"],
    "Materials": ["XLB"],
    "Consumer Staples": ["XLP"],
}

SECTOR_ETF_ONLY = {
    "Defense": "XLI",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Technology": "XLK",
    "Industrials": "XLI",
    "Financials": "XLF",
    "Materials": "XLB",
    "Consumer Staples": "XLP",
}


def get_agency_sector_mapping(conn: sqlite3.Connection, min_events: int = 5) -> dict[str, list[str]]:
    """Build agency -> ticker list from high-impact regulatory events.

    Scans regulatory_events WHERE impact_score >= 4 to find which agencies
    most frequently map to which sectors, then resolves sectors to tickers.
    """
    rows = conn.execute(
        """SELECT agency, sectors FROM regulatory_events
           WHERE agency IS NOT NULL AND sectors IS NOT NULL AND sectors != ''
             AND impact_score >= 4"""
    ).fetchall()

    agency_sectors: dict[str, dict[str, int]] = {}
    for agency, sectors_str in rows:
        for sector in sectors_str.split(","):
            sector = sector.strip()
            if not sector:
                continue
            agency_sectors.setdefault(agency, {})
            agency_sectors[agency][sector] = agency_sectors[agency].get(sector, 0) + 1

    result = {}
    for agency, sector_counts in agency_sectors.items():
        tickers = []
        for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
            if count >= min_events and sector in SECTOR_ETF_MAP:
                tickers.extend(SECTOR_ETF_MAP[sector])
        if tickers:
            result[agency] = list(dict.fromkeys(tickers))  # deduplicate
    return result


# ── Macro Regime Helpers ─────────────────────────────────────────────

def get_macro_regime_at_date(conn: sqlite3.Connection, event_date: str) -> int | None:
    """Look up macro regime quadrant (1-4) for a given date."""
    row = conn.execute(
        "SELECT quadrant FROM macro_regimes WHERE date <= ? ORDER BY date DESC LIMIT 1",
        (event_date,),
    ).fetchone()
    return row[0] if row else None


def get_fomc_proximity(conn: sqlite3.Connection, event_date: str) -> int | None:
    """Trading days to nearest FOMC meeting (positive=upcoming, negative=past)."""
    upcoming = conn.execute(
        "SELECT event_date FROM fomc_events WHERE event_date >= ? ORDER BY event_date LIMIT 1",
        (event_date,),
    ).fetchone()
    recent = conn.execute(
        "SELECT event_date FROM fomc_events WHERE event_date < ? ORDER BY event_date DESC LIMIT 1",
        (event_date,),
    ).fetchone()

    if not upcoming and not recent:
        return None

    event_dt = date_type.fromisoformat(event_date)
    days_ahead = (date_type.fromisoformat(upcoming[0]) - event_dt).days if upcoming else 999
    days_behind = (event_dt - date_type.fromisoformat(recent[0])).days if recent else 999

    return days_ahead if days_ahead <= days_behind else -days_behind


# ── Statistical Helpers ──────────────────────────────────────────────

def run_granger_causality(
    cause_series: pd.Series,
    effect_series: pd.Series,
    max_lags: int = 4,
) -> dict:
    """Run Granger causality test."""
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
    except ImportError:
        return {"error": "statsmodels not available", "significant": False}

    df = pd.DataFrame({"cause": cause_series, "effect": effect_series}).dropna()
    if len(df) < max_lags * 3 + 10:
        return {"error": "Insufficient data", "significant": False, "n_obs": len(df)}

    try:
        results = grangercausalitytests(df[["effect", "cause"]], maxlag=max_lags, verbose=False)
        best_lag = min(results, key=lambda k: results[k][0]["ssr_ftest"][1])
        f_stat, p_value = results[best_lag][0]["ssr_ftest"][:2]
        return {
            "best_lag": int(best_lag),
            "f_statistic": float(f_stat),
            "p_value": float(p_value),
            "significant": p_value < 0.05,
            "n_obs": len(df),
            "all_lags": {
                int(lag): {"f_stat": float(r[0]["ssr_ftest"][0]), "p_value": float(r[0]["ssr_ftest"][1])}
                for lag, r in results.items()
            },
        }
    except Exception as e:
        return {"error": str(e), "significant": False}


def run_cross_sectional_regression(
    df: pd.DataFrame,
    y_col: str,
    x_cols: list[str],
    add_constant: bool = True,
) -> dict:
    """Run OLS cross-sectional regression."""
    clean = df[[y_col] + x_cols].dropna()
    if len(clean) < len(x_cols) + 5:
        return {"error": f"Insufficient observations ({len(clean)})"}

    y = clean[y_col]
    X = clean[x_cols]
    if add_constant:
        X = sm.add_constant(X)

    try:
        model = sm.OLS(y, X).fit()
        return {
            "coefficients": {k: float(v) for k, v in model.params.items()},
            "t_statistics": {k: float(v) for k, v in model.tvalues.items()},
            "p_values": {k: float(v) for k, v in model.pvalues.items()},
            "r_squared": float(model.rsquared),
            "adj_r_squared": float(model.rsquared_adj),
            "f_statistic": float(model.fvalue) if model.fvalue else 0.0,
            "f_p_value": float(model.f_pvalue) if model.f_pvalue else 1.0,
            "n_obs": int(model.nobs),
        }
    except Exception as e:
        return {"error": str(e)}


def run_wilcoxon_ranksum(group_a: list[float], group_b: list[float]) -> dict:
    """Wilcoxon rank-sum (Mann-Whitney U) test for two independent samples."""
    a = np.array([x for x in group_a if not np.isnan(x)])
    b = np.array([x for x in group_b if not np.isnan(x)])

    if len(a) < 3 or len(b) < 3:
        return {"error": "Insufficient samples", "significant": False, "n_a": len(a), "n_b": len(b)}

    try:
        stat, p_value = stats.mannwhitneyu(a, b, alternative="two-sided")
        return {
            "u_statistic": float(stat),
            "p_value": float(p_value),
            "significant": p_value < 0.05,
            "mean_a": float(np.mean(a)),
            "mean_b": float(np.mean(b)),
            "median_a": float(np.median(a)),
            "median_b": float(np.median(b)),
            "n_a": len(a),
            "n_b": len(b),
        }
    except Exception as e:
        return {"error": str(e), "significant": False}
