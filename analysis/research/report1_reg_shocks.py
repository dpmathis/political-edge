"""Report 1: Regulatory Intensity Shocks and Sector Volatility.

Expands the hardcoded reg_shock_detector (2 agencies) into a formal research
report that covers ALL agencies with sufficient data in the regulatory_events
table. For each agency whose weekly high-impact event count exceeds its
rolling z-score threshold, we run event studies on the mapped sector tickers,
Granger-causality tests, and out-of-sample validation splits.

Key questions addressed:
    1. Do abnormal surges in regulatory activity predict sector abnormal returns?
    2. Which agencies produce the most tradeable shocks?
    3. Does the signal hold out of sample (2024 train / 2025 validate / 2026 test)?
"""

import logging
import re
import sqlite3
from collections import Counter

import numpy as np
import pandas as pd

from config import DB_PATH
from analysis.event_study import EventStudy, EventStudyResults
from analysis.research.base import (
    ResearchReportResults,
    get_agency_sector_mapping,
    run_granger_causality,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

REPORT_NAME = "Regulatory Intensity Shocks and Sector Volatility"
REPORT_NUMBER = 1
HYPOTHESIS = (
    "Abnormal surges in regulatory activity predict sector abnormal returns"
)

Z_THRESHOLD = 2.0
ROLLING_WINDOW = 8
MIN_ROLLING_PERIODS = 4
MIN_WEEKS_PER_AGENCY = 10
MIN_SHOCKS_FOR_SUB_STUDY = 5
TOP_AGENCIES_FOR_GRANGER = 5
MAX_GRANGER_LAGS = 4
MIN_IMPACT_SCORE = 4

WINDOW_PRE = 1
WINDOW_POST = 5
EVENT_STUDY_METHOD = "market_adjusted"
BENCHMARK = "SPY"


# ── Slug helper ──────────────────────────────────────────────────────────────

def _slugify(name: str, max_len: int = 50) -> str:
    """Convert an agency name to a safe, lowercase slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:max_len]


# ── Step 1: Build weekly intensity per agency ────────────────────────────────

def _build_agency_weekly_intensity(conn: sqlite3.Connection) -> pd.DataFrame:
    """Compute rolling z-scores of weekly high-impact event counts per agency.

    Returns a DataFrame with columns:
        agency, week_start, count, rolling_mean, rolling_std, z_score
    """
    try:
        reg_df = pd.read_sql_query(
            """SELECT publication_date, agency, impact_score
               FROM regulatory_events
               WHERE impact_score >= ?
               ORDER BY publication_date""",
            conn,
            params=(MIN_IMPACT_SCORE,),
        )
    except Exception as e:
        logger.error("Failed to query regulatory_events: %s", e)
        return pd.DataFrame()

    if reg_df.empty:
        logger.warning("No regulatory events with impact_score >= %d", MIN_IMPACT_SCORE)
        return pd.DataFrame()

    reg_df["date"] = pd.to_datetime(reg_df["publication_date"])
    # Monday-aligned week_start
    reg_df["week_start"] = reg_df["date"] - pd.to_timedelta(
        reg_df["date"].dt.weekday, unit="D"
    )

    weekly = (
        reg_df.groupby(["agency", "week_start"])
        .size()
        .reset_index(name="count")
    )

    results = []
    for agency, grp in weekly.groupby("agency"):
        grp = grp.sort_values("week_start")

        # Fill missing weeks with zero for a continuous series
        all_weeks = pd.date_range(
            grp["week_start"].min(), grp["week_start"].max(), freq="W-MON"
        )
        grp = (
            grp.set_index("week_start")
            .reindex(all_weeks, fill_value=0)
            .reset_index()
        )
        grp.columns = ["week_start", "count"]
        grp["agency"] = agency

        grp["rolling_mean"] = (
            grp["count"]
            .rolling(ROLLING_WINDOW, min_periods=MIN_ROLLING_PERIODS)
            .mean()
        )
        grp["rolling_std"] = (
            grp["count"]
            .rolling(ROLLING_WINDOW, min_periods=MIN_ROLLING_PERIODS)
            .std()
        )
        grp["z_score"] = (grp["count"] - grp["rolling_mean"]) / grp[
            "rolling_std"
        ].replace(0, np.nan)

        results.append(grp)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)


# ── Step 2: Detect shocks across all agencies ───────────────────────────────

def _detect_all_agency_shocks(
    weekly_df: pd.DataFrame,
    agency_tickers: dict[str, list[str]],
) -> list[dict]:
    """Find weeks where z_score > threshold and map to ticker events.

    Each shock creates MULTIPLE events (one per ticker in the agency mapping).

    Returns list of event dicts suitable for EventStudy.run():
        {"date": str, "ticker": str, "agency": str, "z_score": float, "label": str}
    """
    if weekly_df.empty:
        return []

    events: list[dict] = []
    for agency, grp in weekly_df.groupby("agency"):
        # Only agencies with enough data and ticker mappings
        valid_weeks = grp.dropna(subset=["z_score"])
        if len(valid_weeks) < MIN_WEEKS_PER_AGENCY:
            continue
        if agency not in agency_tickers:
            continue

        tickers = agency_tickers[agency]
        shocks = valid_weeks[valid_weeks["z_score"] > Z_THRESHOLD]

        for _, row in shocks.iterrows():
            # Next business day after week_start as the event date
            event_date = (
                pd.Timestamp(row["week_start"]) + pd.offsets.BDay(1)
            ).strftime("%Y-%m-%d")
            z = float(row["z_score"])
            label = f"{agency[:40]} shock z={z:.1f}"

            for ticker in tickers:
                events.append(
                    {
                        "date": event_date,
                        "ticker": ticker,
                        "agency": agency,
                        "z_score": z,
                        "label": label,
                    }
                )

    logger.info(
        "Detected %d shock-ticker events across %d unique agencies",
        len(events),
        len({e["agency"] for e in events}),
    )
    return events


# ── Step 3: Per-agency sub-studies ───────────────────────────────────────────

def _run_per_agency_studies(
    all_events: list[dict],
    db_path: str,
) -> list[EventStudyResults]:
    """Run a separate event study for each agency that has enough shocks."""
    agency_events: dict[str, list[dict]] = {}
    for e in all_events:
        agency_events.setdefault(e["agency"], []).append(e)

    results: list[EventStudyResults] = []
    es = EventStudy(db_path)

    for agency, events in agency_events.items():
        # Deduplicate by (date, ticker) for counting distinct shocks
        unique_dates = {e["date"] for e in events}
        if len(unique_dates) < MIN_SHOCKS_FOR_SUB_STUDY:
            logger.debug(
                "Skipping per-agency study for %s: only %d shock weeks",
                agency[:40],
                len(unique_dates),
            )
            continue

        slug = _slugify(agency)
        study_name = f"report1_reg_shocks_{slug}"
        try:
            result = es.run(
                events=events,
                study_name=study_name,
                hypothesis=f"Reg shocks from {agency[:60]} predict abnormal returns",
                window_pre=WINDOW_PRE,
                window_post=WINDOW_POST,
                benchmark=BENCHMARK,
                method=EVENT_STUDY_METHOD,
            )
            results.append(result)
            logger.info(
                "Agency '%s': N=%d, CAR=%+.2f%%, p=%.4f",
                agency[:40],
                result.num_events,
                result.mean_car * 100,
                result.p_value,
            )
        except Exception as e:
            logger.warning("Failed per-agency study for %s: %s", agency[:40], e)

    return results


# ── Step 4: Granger causality tests ─────────────────────────────────────────

def _run_granger_tests(
    conn: sqlite3.Connection,
    agency_tickers: dict[str, list[str]],
    all_events: list[dict],
) -> dict:
    """Run Granger causality: reg intensity -> realized volatility.

    Tests the top agencies by shock count. For each agency, uses the first
    mapped ticker's 5-day rolling std of daily returns as the volatility proxy.
    """
    # Rank agencies by number of shocks
    agency_shock_counts = Counter(e["agency"] for e in all_events)
    top_agencies = [
        a for a, _ in agency_shock_counts.most_common(TOP_AGENCIES_FOR_GRANGER)
        if a in agency_tickers
    ]

    granger_results: dict[str, dict] = {}

    for agency in top_agencies:
        ticker = agency_tickers[agency][0]  # first ticker as proxy

        try:
            # Weekly regulatory intensity
            reg_df = pd.read_sql_query(
                """SELECT publication_date, impact_score
                   FROM regulatory_events
                   WHERE agency = ? AND impact_score >= ?
                   ORDER BY publication_date""",
                conn,
                params=(agency, MIN_IMPACT_SCORE),
            )
            if reg_df.empty:
                continue

            reg_df["date"] = pd.to_datetime(reg_df["publication_date"])
            reg_df["week_start"] = reg_df["date"] - pd.to_timedelta(
                reg_df["date"].dt.weekday, unit="D"
            )
            intensity = (
                reg_df.groupby("week_start").size().rename("intensity")
            )

            # Weekly realized volatility for ticker
            price_df = pd.read_sql_query(
                "SELECT date, close FROM market_data WHERE ticker = ? ORDER BY date",
                conn,
                params=(ticker,),
            )
            if price_df.empty or len(price_df) < 30:
                continue

            price_df["date"] = pd.to_datetime(price_df["date"])
            price_df = price_df.set_index("date").sort_index()
            price_df["return"] = price_df["close"].pct_change()
            price_df["vol_5d"] = price_df["return"].rolling(5).std()

            # Resample to weekly (Monday) using last value of the week
            weekly_vol = price_df["vol_5d"].resample("W-MON").last().rename("volatility")

            # Align and run test
            combined = pd.DataFrame({"intensity": intensity, "volatility": weekly_vol}).dropna()
            if len(combined) < MAX_GRANGER_LAGS * 3 + 10:
                granger_results[agency] = {
                    "error": "Insufficient aligned data",
                    "significant": False,
                    "n_obs": len(combined),
                    "ticker": ticker,
                }
                continue

            result = run_granger_causality(
                combined["intensity"],
                combined["volatility"],
                max_lags=MAX_GRANGER_LAGS,
            )
            result["ticker"] = ticker
            granger_results[agency] = result

        except Exception as e:
            logger.warning("Granger test failed for %s: %s", agency[:40], e)
            granger_results[agency] = {
                "error": str(e),
                "significant": False,
            }

    return granger_results


# ── Step 5: Out-of-sample analysis ──────────────────────────────────────────

def _out_of_sample_analysis(
    all_shock_events: list[dict],
    db_path: str,
) -> dict:
    """Split shocks by year and run separate event studies for each period.

    Periods: 2024 (train), 2025 (validate), 2026 (test).
    """
    if not all_shock_events:
        return {"error": "No shock events for OOS analysis"}

    year_buckets: dict[str, list[dict]] = {"2024": [], "2025": [], "2026": []}
    for e in all_shock_events:
        year = e["date"][:4]
        if year in year_buckets:
            year_buckets[year].append(e)

    es = EventStudy(db_path)
    oos_results: dict[str, EventStudyResults | str] = {}

    for year, events in year_buckets.items():
        if not events:
            oos_results[year] = "No events in this period"
            continue
        try:
            result = es.run(
                events=events,
                study_name=f"report1_reg_shocks_oos_{year}",
                hypothesis=f"OOS {year}: {HYPOTHESIS}",
                window_pre=WINDOW_PRE,
                window_post=WINDOW_POST,
                benchmark=BENCHMARK,
                method=EVENT_STUDY_METHOD,
            )
            oos_results[year] = result
        except Exception as e:
            logger.warning("OOS study failed for %s: %s", year, e)
            oos_results[year] = f"Failed: {e}"

    # Build comparison summary
    comparison: dict[str, dict] = {}
    for year in ("2024", "2025", "2026"):
        r = oos_results.get(year)
        if isinstance(r, EventStudyResults):
            comparison[year] = {
                "num_events": r.num_events,
                "mean_car": r.mean_car,
                "p_value": r.p_value,
                "significant": r.is_significant(),
                "win_rate": r.win_rate,
            }
        else:
            comparison[year] = {"status": str(r)}

    oos_results["comparison"] = comparison
    return oos_results


# ── Main entry point ────────────────────────────────────────────────────────

def run_report(db_path: str | None = None) -> ResearchReportResults:
    """Run Report 1: Regulatory Intensity Shocks and Sector Volatility.

    Orchestrates all analysis steps:
        1. Build weekly regulatory intensity per agency
        2. Detect z-score shocks across all agencies
        3. Aggregate event study on all shocks
        4. Per-agency event studies for agencies with enough shocks
        5. Granger causality tests (top agencies)
        6. Out-of-sample validation (2024/2025/2026)

    Args:
        db_path: Path to the SQLite database. Defaults to config.DB_PATH.

    Returns:
        ResearchReportResults with all sub-studies, Granger tests, and OOS results.
    """
    db_path = db_path or DB_PATH
    logger.info("Starting Report 1: %s", REPORT_NAME)

    conn = sqlite3.connect(db_path)

    try:
        # ── 1. Agency-ticker mapping ─────────────────────────────────────
        agency_tickers = get_agency_sector_mapping(conn, min_events=5)
        logger.info("Found %d agencies with ticker mappings", len(agency_tickers))

        if not agency_tickers:
            logger.warning("No agency-ticker mappings found; returning empty report")
            return ResearchReportResults(
                report_name=REPORT_NAME,
                report_number=REPORT_NUMBER,
                hypothesis=HYPOTHESIS,
                summary_stats={"error": "No agency-ticker mappings found"},
                recommendations=["Collect more regulatory event data with sector tags"],
            )

        # ── 2. Weekly intensity & shock detection ────────────────────────
        weekly_df = _build_agency_weekly_intensity(conn)
        if weekly_df.empty:
            logger.warning("No weekly intensity data; returning empty report")
            return ResearchReportResults(
                report_name=REPORT_NAME,
                report_number=REPORT_NUMBER,
                hypothesis=HYPOTHESIS,
                summary_stats={"error": "No weekly intensity data"},
                recommendations=["Ensure regulatory_events table has data with impact_score >= 4"],
            )

        all_shock_events = _detect_all_agency_shocks(weekly_df, agency_tickers)
        if not all_shock_events:
            logger.warning("No shocks detected; returning empty report")
            return ResearchReportResults(
                report_name=REPORT_NAME,
                report_number=REPORT_NUMBER,
                hypothesis=HYPOTHESIS,
                summary_stats={
                    "total_shocks": 0,
                    "agencies_tested": len(agency_tickers),
                },
                recommendations=[
                    "No z > 2.0 shocks detected. Consider lowering z-threshold or collecting more data."
                ],
            )

        unique_agencies_shocked = list({e["agency"] for e in all_shock_events})
        unique_shock_weeks = len(
            {(e["agency"], e["date"]) for e in all_shock_events}
        )
        logger.info(
            "Total shock-ticker events: %d, unique agency-weeks: %d",
            len(all_shock_events),
            unique_shock_weeks,
        )

        # ── 3. Aggregate event study ─────────────────────────────────────
        es = EventStudy(db_path)
        event_studies: list[EventStudyResults] = []

        try:
            aggregate_result = es.run(
                events=all_shock_events,
                study_name="report1_reg_shocks_aggregate",
                hypothesis=HYPOTHESIS,
                window_pre=WINDOW_PRE,
                window_post=WINDOW_POST,
                benchmark=BENCHMARK,
                method=EVENT_STUDY_METHOD,
            )
            event_studies.append(aggregate_result)
            logger.info(
                "Aggregate: N=%d, CAR=%+.2f%%, p=%.4f",
                aggregate_result.num_events,
                aggregate_result.mean_car * 100,
                aggregate_result.p_value,
            )
        except Exception as e:
            logger.error("Aggregate event study failed: %s", e)

        # ── 4. Per-agency event studies ──────────────────────────────────
        per_agency_results = _run_per_agency_studies(all_shock_events, db_path)
        event_studies.extend(per_agency_results)

        # ── 5. Granger causality ─────────────────────────────────────────
        granger_results = _run_granger_tests(conn, agency_tickers, all_shock_events)

        # ── 6. Out-of-sample analysis ────────────────────────────────────
        oos_results = _out_of_sample_analysis(all_shock_events, db_path)

        # Add OOS event studies to the master list
        for year in ("2024", "2025", "2026"):
            r = oos_results.get(year)
            if isinstance(r, EventStudyResults):
                event_studies.append(r)

    finally:
        conn.close()

    # ── Assemble summary statistics ──────────────────────────────────────
    significant_agencies = [
        r.study_name
        for r in per_agency_results
        if r.is_significant()
    ]

    summary_stats = {
        "total_shocks": unique_shock_weeks,
        "total_shock_ticker_events": len(all_shock_events),
        "agencies_tested": len(unique_agencies_shocked),
        "agencies_with_sub_study": len(per_agency_results),
        "significant_agencies": len(significant_agencies),
        "significant_agency_names": significant_agencies,
        "granger_significant": sum(
            1 for v in granger_results.values()
            if isinstance(v, dict) and v.get("significant")
        ),
    }

    if event_studies:
        agg = event_studies[0]  # aggregate is first
        summary_stats["aggregate_mean_car"] = agg.mean_car
        summary_stats["aggregate_p_value"] = agg.p_value
        summary_stats["aggregate_significant"] = agg.is_significant()

    # ── Build recommendations ────────────────────────────────────────────
    recommendations: list[str] = []

    if event_studies and event_studies[0].is_significant():
        recommendations.append(
            f"Aggregate signal is statistically significant "
            f"(CAR={event_studies[0].mean_car:+.2%}, p={event_studies[0].p_value:.4f}). "
            f"Regulatory intensity shocks carry predictive information for sector returns."
        )
    else:
        recommendations.append(
            "Aggregate signal is NOT statistically significant at the 5% level. "
            "The relationship may be agency-specific rather than universal."
        )

    if significant_agencies:
        recommendations.append(
            f"{len(significant_agencies)} agency-level sub-studies show significant results. "
            f"Focus trading signals on these agencies."
        )

    granger_sig_agencies = [
        a for a, v in granger_results.items()
        if isinstance(v, dict) and v.get("significant")
    ]
    if granger_sig_agencies:
        recommendations.append(
            f"Granger causality confirmed for {len(granger_sig_agencies)} agencies: "
            f"regulatory intensity Granger-causes sector volatility."
        )
    else:
        recommendations.append(
            "No Granger causality found — shocks may be contemporaneous rather than leading."
        )

    # OOS comparison
    comparison = oos_results.get("comparison", {})
    oos_significant = [
        yr for yr, info in comparison.items()
        if isinstance(info, dict) and info.get("significant")
    ]
    if oos_significant:
        recommendations.append(
            f"Out-of-sample signal holds in {', '.join(oos_significant)}. "
            f"Signal is not purely in-sample overfitting."
        )
    else:
        recommendations.append(
            "Signal does not hold out of sample — possible in-sample overfitting. "
            "Use with caution."
        )

    # ── Return assembled report ──────────────────────────────────────────
    return ResearchReportResults(
        report_name=REPORT_NAME,
        report_number=REPORT_NUMBER,
        hypothesis=HYPOTHESIS,
        event_studies=event_studies,
        additional_analyses={
            "granger_causality": granger_results,
            "out_of_sample": oos_results,
        },
        summary_stats=summary_stats,
        recommendations=recommendations,
        signal_parameters={
            "z_threshold": Z_THRESHOLD,
            "rolling_window": ROLLING_WINDOW,
            "min_impact_score": MIN_IMPACT_SCORE,
            "window_pre": WINDOW_PRE,
            "window_post": WINDOW_POST,
            "method": EVENT_STUDY_METHOD,
            "benchmark": BENCHMARK,
        },
    )
