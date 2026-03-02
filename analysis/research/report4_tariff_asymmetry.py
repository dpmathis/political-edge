"""Report 4: Tariff Announcement Asymmetry and Sector Dispersion.

Investigates whether tariff imposition and relief announcements produce
asymmetric market reactions, whether escalation dulls market response over
time, how the 2018-2019 and 2025-2026 tariff cycles compare, and whether
tariff days exhibit heightened cross-sector return dispersion.

Uses the EventStudy framework for abnormal return calculations and
combines hardcoded YAML tariff events with EO-derived tariff events
from the database.
"""

import logging
import sqlite3
from datetime import date as date_type

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from config import DB_PATH, load_tariff_events
from analysis.event_study import EventStudy, EventStudyResults
from analysis.eo_classifier import classify_eo, IMPOSITION_KEYWORDS, RELIEF_KEYWORDS
from analysis.research.base import ResearchReportResults, run_wilcoxon_ranksum

logger = logging.getLogger(__name__)

# Sector ETFs used when EOs lack explicit sector annotations
_DEFAULT_SECTOR_ETFS = ["XLI", "XLB", "XLK", "XLE", "XLF"]

# Full sector ETF universe for dispersion analysis
_DISPERSION_ETFS = ["XLI", "XLK", "XLB", "XLE", "XLF", "XLP"]

_HYPOTHESIS = (
    "Tariff imposition announcements produce larger absolute abnormal returns "
    "than tariff relief announcements, and market sensitivity decays with "
    "repeated escalation within a tariff cycle."
)


# ── Helpers ──────────────────────────────────────────────────────────


def _classify_yaml_direction(description: str) -> str:
    """Classify a YAML tariff event description as imposition/relief/neutral."""
    desc_lower = description.lower()
    if any(kw in desc_lower for kw in RELIEF_KEYWORDS):
        return "relief"
    if any(kw in desc_lower for kw in IMPOSITION_KEYWORDS):
        return "imposition"
    return "neutral"


def _assign_cycle(event_date: str) -> str:
    """Map a date string to its tariff cycle label."""
    dt = date_type.fromisoformat(event_date)
    if dt < date_type(2020, 1, 1):
        return "2018-2019"
    if dt >= date_type(2025, 1, 1):
        return "2025-2026"
    return "other"


def _events_to_study_list(events_df: pd.DataFrame) -> list[dict]:
    """Expand an events DataFrame into per-sector event study input dicts."""
    study_events = []
    for _, row in events_df.iterrows():
        sectors = row["affected_sectors"]
        if isinstance(sectors, str):
            sectors = [s.strip() for s in sectors.split(",")]
        for etf in sectors:
            study_events.append({
                "date": row["date"],
                "ticker": etf,
                "label": row["description"],
            })
    return study_events


# ── Core pipeline stages ─────────────────────────────────────────────


def _build_expanded_tariff_events(conn: sqlite3.Connection) -> pd.DataFrame:
    """Merge hardcoded YAML tariff events with EO-derived tariff events.

    Returns a deduplicated DataFrame with columns:
        date, description, affected_sectors, tariff_direction, cycle
    """
    records: list[dict] = []

    # 1. Load YAML events
    yaml_events = load_tariff_events()
    yaml_dates: set[str] = set()
    for ev in yaml_events:
        d = ev["date"]
        yaml_dates.add(d)
        records.append({
            "date": d,
            "description": ev["description"],
            "affected_sectors": ev["affected_sectors"],
            "tariff_direction": _classify_yaml_direction(ev["description"]),
            "cycle": _assign_cycle(d),
        })

    # 2. Supplement with EOs classified as tariff_trade
    try:
        rows = conn.execute(
            "SELECT publication_date, title FROM regulatory_events "
            "WHERE event_type = 'executive_order'"
        ).fetchall()
    except Exception as exc:
        logger.warning("Could not query regulatory_events for EOs: %s", exc)
        rows = []

    for pub_date, title in rows:
        if pub_date in yaml_dates:
            # YAML events take precedence — skip duplicate dates
            continue
        classification = classify_eo(title)
        if classification["topic"] != "tariff_trade":
            continue
        records.append({
            "date": pub_date,
            "description": title,
            "affected_sectors": _DEFAULT_SECTOR_ETFS,
            "tariff_direction": classification["tariff_direction"],
            "cycle": _assign_cycle(pub_date),
        })

    df = pd.DataFrame(records)
    if df.empty:
        return df

    # Deduplicate on date, keeping first (YAML) occurrence
    df = df.drop_duplicates(subset="date", keep="first").sort_values("date").reset_index(drop=True)
    logger.info("Expanded tariff events: %d total (%d from YAML, rest from EOs)", len(df), len(yaml_events))
    return df


def _run_asymmetry_tests(events_df: pd.DataFrame, db_path: str) -> dict:
    """Compare absolute CARs between imposition and relief event groups.

    Returns dict with imposition_study, relief_study, and asymmetry_test.
    """
    es = EventStudy(db_path)

    imposition_events = events_df[events_df["tariff_direction"] == "imposition"]
    relief_events = events_df[events_df["tariff_direction"] == "relief"]

    result: dict = {}

    # Imposition study
    if not imposition_events.empty:
        imp_list = _events_to_study_list(imposition_events)
        imp_study = es.run(
            imp_list,
            study_name="report4_tariff_imposition",
            hypothesis="Tariff imposition announcements produce significant negative abnormal returns.",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
        )
        result["imposition_study"] = imp_study
    else:
        logger.warning("No imposition events found; skipping imposition sub-study.")
        result["imposition_study"] = None

    # Relief study
    if not relief_events.empty:
        rel_list = _events_to_study_list(relief_events)
        rel_study = es.run(
            rel_list,
            study_name="report4_tariff_relief",
            hypothesis="Tariff relief announcements produce significant positive abnormal returns.",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
        )
        result["relief_study"] = rel_study
    else:
        logger.warning("No relief events found; skipping relief sub-study.")
        result["relief_study"] = None

    # Wilcoxon rank-sum on |CAR|
    if result.get("imposition_study") and result.get("relief_study"):
        imp_cars = [abs(r["car_full"]) for r in result["imposition_study"].per_event_results]
        rel_cars = [abs(r["car_full"]) for r in result["relief_study"].per_event_results]
        result["asymmetry_test"] = run_wilcoxon_ranksum(imp_cars, rel_cars)
    else:
        result["asymmetry_test"] = {
            "error": "One or both groups empty — cannot compare.",
            "significant": False,
        }

    return result


def _test_escalation_decay(events_df: pd.DataFrame, db_path: str) -> dict:
    """Test whether market sensitivity decays with repeated tariff events within a cycle.

    Computes Spearman rank correlation between event sequence number and |CAR|.
    """
    # Run the aggregate study to get per-event CARs
    es = EventStudy(db_path)
    all_events = _events_to_study_list(events_df)
    if not all_events:
        return {"error": "No events available", "significant": False}

    agg_study = es.run(
        all_events,
        study_name="report4_tariff_escalation_check",
        hypothesis="Market reaction decays with repeated tariff announcements within a cycle.",
        window_pre=1,
        window_post=5,
        benchmark="SPY",
    )

    # Build a lookup from event date to list of |CAR| values
    car_by_date: dict[str, list[float]] = {}
    for r in agg_study.per_event_results:
        car_by_date.setdefault(r["event_date"], []).append(abs(r["car_full"]))

    # Mean |CAR| per event date
    mean_car_by_date: dict[str, float] = {
        d: float(np.mean(cars)) for d, cars in car_by_date.items()
    }

    # Within each cycle, assign sequence numbers
    seq_nums: list[int] = []
    abs_cars: list[float] = []

    for cycle in ["2018-2019", "2025-2026"]:
        cycle_events = events_df[events_df["cycle"] == cycle].sort_values("date")
        for seq, (_, row) in enumerate(cycle_events.iterrows(), start=1):
            d = row["date"]
            if d in mean_car_by_date:
                seq_nums.append(seq)
                abs_cars.append(mean_car_by_date[d])

    if len(seq_nums) < 4:
        return {
            "error": f"Insufficient data points ({len(seq_nums)}) for correlation.",
            "significant": False,
            "n": len(seq_nums),
        }

    try:
        spearman_r, p_value = scipy_stats.spearmanr(seq_nums, abs_cars)
        return {
            "spearman_r": float(spearman_r),
            "p_value": float(p_value),
            "significant": p_value < 0.05,
            "n": len(seq_nums),
        }
    except Exception as exc:
        return {"error": str(exc), "significant": False, "n": len(seq_nums)}


def _cross_cycle_comparison(events_df: pd.DataFrame, db_path: str) -> dict:
    """Compare mean CARs between the 2018-2019 and 2025-2026 tariff cycles."""
    es = EventStudy(db_path)
    result: dict = {}

    cycle_2018 = events_df[events_df["cycle"] == "2018-2019"]
    cycle_2025 = events_df[events_df["cycle"] == "2025-2026"]

    # 2018-2019 study
    if not cycle_2018.empty:
        study_2018 = es.run(
            _events_to_study_list(cycle_2018),
            study_name="report4_tariff_cycle_2018_2019",
            hypothesis="2018-2019 tariff cycle produced significant abnormal returns.",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
        )
        result["cycle_2018_2019"] = study_2018
    else:
        result["cycle_2018_2019"] = None

    # 2025-2026 study
    if not cycle_2025.empty:
        study_2025 = es.run(
            _events_to_study_list(cycle_2025),
            study_name="report4_tariff_cycle_2025_2026",
            hypothesis="2025-2026 tariff cycle produced significant abnormal returns.",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
        )
        result["cycle_2025_2026"] = study_2025
    else:
        result["cycle_2025_2026"] = None

    # Independent samples t-test on CARs
    if result.get("cycle_2018_2019") and result.get("cycle_2025_2026"):
        cars_2018 = [r["car_full"] for r in result["cycle_2018_2019"].per_event_results]
        cars_2025 = [r["car_full"] for r in result["cycle_2025_2026"].per_event_results]
        if len(cars_2018) >= 2 and len(cars_2025) >= 2:
            t_stat, p_value = scipy_stats.ttest_ind(cars_2018, cars_2025, equal_var=False)
            result["difference_test"] = {
                "t_statistic": float(t_stat),
                "p_value": float(p_value),
                "significant": p_value < 0.05,
                "mean_car_2018": float(np.mean(cars_2018)),
                "mean_car_2025": float(np.mean(cars_2025)),
                "n_2018": len(cars_2018),
                "n_2025": len(cars_2025),
            }
        else:
            result["difference_test"] = {
                "error": "Insufficient events in one or both cycles.",
                "significant": False,
            }
    else:
        result["difference_test"] = {
            "error": "One or both cycle studies unavailable.",
            "significant": False,
        }

    return result


def _sector_dispersion_analysis(events_df: pd.DataFrame, conn: sqlite3.Connection) -> dict:
    """Compare cross-sector return dispersion on tariff days vs non-tariff days.

    For each trading day, computes the cross-sectional standard deviation of
    daily returns across sector ETFs and tests whether tariff event dates
    show higher dispersion.
    """
    # Load daily returns for each sector ETF
    sector_returns: dict[str, pd.Series] = {}
    for etf in _DISPERSION_ETFS:
        try:
            df = pd.read_sql_query(
                "SELECT date, close FROM market_data WHERE ticker = ? ORDER BY date",
                conn,
                params=(etf,),
            )
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            sector_returns[etf] = df["close"].pct_change().dropna()
        except Exception as exc:
            logger.warning("Could not load returns for %s: %s", etf, exc)

    if len(sector_returns) < 3:
        return {
            "error": f"Only {len(sector_returns)} ETFs available; need at least 3.",
            "significant": False,
        }

    # Build a DataFrame of aligned returns
    returns_df = pd.DataFrame(sector_returns).dropna()
    if returns_df.empty:
        return {"error": "No overlapping return data.", "significant": False}

    # Cross-sectional standard deviation per day
    dispersion = returns_df.std(axis=1)

    # Tariff event dates
    tariff_dates = set(pd.to_datetime(events_df["date"]).dt.normalize())

    tariff_mask = dispersion.index.normalize().isin(tariff_dates)
    tariff_dispersion = dispersion[tariff_mask]
    non_tariff_dispersion = dispersion[~tariff_mask]

    if len(tariff_dispersion) < 2 or len(non_tariff_dispersion) < 2:
        return {
            "error": "Insufficient tariff or non-tariff trading days for comparison.",
            "significant": False,
        }

    t_stat, p_value = scipy_stats.ttest_ind(
        tariff_dispersion.values,
        non_tariff_dispersion.values,
        equal_var=False,
    )

    return {
        "tariff_day_mean_dispersion": float(tariff_dispersion.mean()),
        "non_tariff_day_mean_dispersion": float(non_tariff_dispersion.mean()),
        "tariff_day_count": int(len(tariff_dispersion)),
        "non_tariff_day_count": int(len(non_tariff_dispersion)),
        "t_stat": float(t_stat),
        "p_value": float(p_value),
        "significant": p_value < 0.05,
    }


# ── Public API ───────────────────────────────────────────────────────


def run_report(db_path: str | None = None) -> ResearchReportResults:
    """Execute Report 4: Tariff Announcement Asymmetry and Sector Dispersion.

    Builds an expanded tariff event set, runs aggregate and directional event
    studies, tests escalation decay, compares tariff cycles, and measures
    cross-sector dispersion on tariff days.

    Args:
        db_path: Path to the SQLite database. Defaults to config.DB_PATH.

    Returns:
        ResearchReportResults with all sub-studies and analyses.
    """
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)
    event_studies: list[EventStudyResults] = []
    additional: dict = {}
    recommendations: list[str] = []

    try:
        # 1. Build expanded event set
        events_df = _build_expanded_tariff_events(conn)
        if events_df.empty:
            logger.error("No tariff events found — aborting report.")
            return ResearchReportResults(
                report_name="Tariff Announcement Asymmetry",
                report_number=4,
                hypothesis=_HYPOTHESIS,
                recommendations=["No tariff events available; cannot produce findings."],
            )

        additional["total_events"] = len(events_df)
        additional["direction_counts"] = events_df["tariff_direction"].value_counts().to_dict()
        additional["cycle_counts"] = events_df["cycle"].value_counts().to_dict()

        # 2. Aggregate event study
        es = EventStudy(db)
        agg_events = _events_to_study_list(events_df)
        agg_study = es.run(
            agg_events,
            study_name="report4_tariff_aggregate",
            hypothesis="Tariff announcements produce significant abnormal returns in affected sectors.",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
        )
        event_studies.append(agg_study)

        if agg_study.is_significant():
            recommendations.append(
                f"Aggregate tariff signal is significant (CAR={agg_study.mean_car:+.2%}, "
                f"p={agg_study.p_value:.4f}). Tariff announcements are a reliable driver "
                f"of sector-level abnormal returns."
            )
        else:
            recommendations.append(
                f"Aggregate tariff signal is not significant at 5% "
                f"(CAR={agg_study.mean_car:+.2%}, p={agg_study.p_value:.4f})."
            )

        # 3. Asymmetry tests (imposition vs relief)
        asymmetry = _run_asymmetry_tests(events_df, db)
        additional["asymmetry_test"] = asymmetry.get("asymmetry_test", {})

        if asymmetry.get("imposition_study"):
            event_studies.append(asymmetry["imposition_study"])
        if asymmetry.get("relief_study"):
            event_studies.append(asymmetry["relief_study"])

        asym_test = asymmetry.get("asymmetry_test", {})
        if asym_test.get("significant"):
            recommendations.append(
                f"Asymmetry confirmed: imposition |CAR| mean={asym_test.get('mean_a', 0):.4f} vs "
                f"relief |CAR| mean={asym_test.get('mean_b', 0):.4f} (p={asym_test.get('p_value', 1):.4f}). "
                f"Position sizing should differentiate by direction."
            )
        elif "error" not in asym_test:
            recommendations.append(
                "No statistically significant asymmetry between imposition and relief reactions."
            )

        # 4. Escalation decay
        decay = _test_escalation_decay(events_df, db)
        additional["escalation_decay"] = decay

        if decay.get("significant"):
            recommendations.append(
                f"Escalation decay detected: Spearman r={decay['spearman_r']:.3f}, "
                f"p={decay['p_value']:.4f}. Later events in a cycle have smaller absolute CARs. "
                f"Reduce position sizes for sequential tariff announcements."
            )
        elif "error" not in decay:
            recommendations.append(
                f"No significant escalation decay (Spearman r={decay.get('spearman_r', 0):.3f}, "
                f"p={decay.get('p_value', 1):.4f})."
            )

        # 5. Cross-cycle comparison
        cross_cycle = _cross_cycle_comparison(events_df, db)
        additional["cross_cycle"] = {
            k: v for k, v in cross_cycle.items() if not isinstance(v, EventStudyResults)
        }

        if cross_cycle.get("cycle_2018_2019"):
            event_studies.append(cross_cycle["cycle_2018_2019"])
        if cross_cycle.get("cycle_2025_2026"):
            event_studies.append(cross_cycle["cycle_2025_2026"])

        diff_test = cross_cycle.get("difference_test", {})
        if diff_test.get("significant"):
            recommendations.append(
                f"Cross-cycle difference is significant (t={diff_test['t_statistic']:.3f}, "
                f"p={diff_test['p_value']:.4f}). "
                f"Mean CAR 2018-2019={diff_test.get('mean_car_2018', 0):+.4f}, "
                f"2025-2026={diff_test.get('mean_car_2025', 0):+.4f}."
            )
        elif "error" not in diff_test:
            recommendations.append(
                "No significant difference between 2018-2019 and 2025-2026 cycle CARs."
            )

        # 6. Sector dispersion analysis
        dispersion = _sector_dispersion_analysis(events_df, conn)
        additional["sector_dispersion"] = dispersion

        if dispersion.get("significant"):
            recommendations.append(
                f"Sector dispersion is significantly higher on tariff days "
                f"(mean={dispersion['tariff_day_mean_dispersion']:.4f}) vs "
                f"non-tariff days ({dispersion['non_tariff_day_mean_dispersion']:.4f}), "
                f"p={dispersion['p_value']:.4f}. Consider pair trades on announcement days."
            )
        elif "error" not in dispersion:
            recommendations.append(
                "Sector dispersion is not significantly elevated on tariff event days."
            )

    except Exception as exc:
        logger.exception("Report 4 encountered an error: %s", exc)
        recommendations.append(f"Report execution error: {exc}")
    finally:
        conn.close()

    return ResearchReportResults(
        report_name="Tariff Announcement Asymmetry",
        report_number=4,
        hypothesis=_HYPOTHESIS,
        event_studies=event_studies,
        additional_analyses=additional,
        recommendations=recommendations,
    )
