"""Trading Context — Historical performance lookup for signal enhancement.

Maps signal types to event study results and provides historical
performance context (mean CAR, win rate, p-value, sample size).

Usage:
    from analysis.trading_context import get_historical_performance
    perf = get_historical_performance("fda_catalyst", conn)
    # Returns: {"mean_car": 0.032, "win_rate": 0.67, "p_value": 0.04, "n_events": 24}
"""

import sqlite3

# Map signal types to event study names in the event_studies table
SIGNAL_STUDY_MAP = {
    "fda_catalyst": "fda_adcom",
    "regulatory_event": "high_impact_regulatory",
    "contract_momentum": "defense_regulatory",
    "lobbying_spike": None,  # No dedicated study
    "macro_regime": None,
    "reg_shock_expanded": "report1_reg_shocks_aggregate",
    "pipeline_pressure": "report3_pipeline_proposed_rule",
    "tariff_asymmetry": "report4_tariff_aggregate",
}

# Default time horizons (trading days) by signal type
TIME_HORIZONS = {
    "fda_catalyst": 15,
    "contract_momentum": 10,
    "regulatory_event": 10,
    "lobbying_spike": 20,
    "macro_regime": 20,
    "reg_shock": 5,
    "fomc_drift": 5,
    "reg_shock_expanded": 5,
    "pipeline_pressure": 20,
    "tariff_asymmetry": 5,
}

# EO signal types get their expected_car from eo_classifier directly
EO_PREFIX = "eo_"


def get_historical_performance(signal_type: str, conn: sqlite3.Connection) -> dict | None:
    """Look up historical event study performance for a signal type.

    Returns dict with mean_car, win_rate, p_value, n_events, or None if no study exists.
    """
    study_name = SIGNAL_STUDY_MAP.get(signal_type)

    if not study_name:
        # Check if it's an EO signal (eo_tariff_trade, eo_defense, etc.)
        if signal_type.startswith(EO_PREFIX):
            return _get_eo_performance(signal_type)
        if signal_type == "reg_shock":
            return _get_reg_shock_performance()
        return None

    row = conn.execute(
        """SELECT mean_car, win_rate, p_value, num_events
           FROM event_studies
           WHERE study_name = ?
           ORDER BY created_at DESC LIMIT 1""",
        (study_name,),
    ).fetchone()

    if not row:
        return None

    return {
        "mean_car": row[0],
        "win_rate": row[1],
        "p_value": row[2],
        "n_events": row[3],
    }


def _get_eo_performance(signal_type: str) -> dict | None:
    """Get performance data for EO signals from the classifier constants."""
    try:
        from analysis.eo_classifier import (
            TOPIC_CONFIDENCE,
            TOPIC_EXPECTED_CAR,
            TOPIC_SAMPLE_SIZE,
        )
    except ImportError:
        return None

    topic = signal_type[len(EO_PREFIX):]
    if topic not in TOPIC_EXPECTED_CAR:
        return None

    # Map confidence to approximate p-value
    confidence_p = {"high": 0.01, "medium": 0.05, "low": 0.10}

    return {
        "mean_car": TOPIC_EXPECTED_CAR[topic],
        "win_rate": None,  # Not available from research
        "p_value": confidence_p.get(TOPIC_CONFIDENCE.get(topic, "low"), 0.10),
        "n_events": TOPIC_SAMPLE_SIZE.get(topic, 0),
    }


def _get_reg_shock_performance() -> dict | None:
    """Get performance data for regulatory shock signals."""
    try:
        from analysis.reg_shock_detector import AGENCY_TICKERS
    except ImportError:
        return None

    # Return aggregate across all agency shock types
    all_cars = [v["expected_car"] for v in AGENCY_TICKERS.values()]
    all_ns = [v.get("sample_size", 0) for v in AGENCY_TICKERS.values()]

    if not all_cars:
        return None

    return {
        "mean_car": sum(all_cars) / len(all_cars),
        "win_rate": None,
        "p_value": 0.05,
        "n_events": sum(all_ns),
    }


def get_time_horizon(signal_type: str) -> int:
    """Get default time horizon in trading days for a signal type."""
    if signal_type.startswith(EO_PREFIX):
        return 3  # EO signals are short-term (3-day event window)
    return TIME_HORIZONS.get(signal_type, 10)
