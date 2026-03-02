"""Report 2: Executive Order Market Impact — Topic-Conditional Abnormal Returns.

Tests whether executive orders classified by topic (tariff/trade, sanctions,
defense, energy, healthcare, technology) generate statistically significant
abnormal returns in the associated sector tickers. Includes sub-analyses for
tariff imposition vs. relief, Biden vs. Trump administration comparisons, and
a cross-sectional regression of CARs on topic/macro/FOMC features.
"""

import logging
import sqlite3

import numpy as np
import pandas as pd
from scipy import stats

from config import DB_PATH
from analysis.event_study import EventStudy, EventStudyResults
from analysis.eo_classifier import classify_eo, TOPIC_KEYWORDS, TOPIC_TICKERS
from analysis.research.base import (
    ResearchReportResults,
    get_macro_regime_at_date,
    get_fomc_proximity,
    run_cross_sectional_regression,
    run_wilcoxon_ranksum,
)

logger = logging.getLogger(__name__)

ADMINISTRATION_CUTOFF = "2025-01-20"


# ── Data Loading ─────────────────────────────────────────────────────


def _load_eos(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load and classify all executive orders from the database.

    Queries regulatory_events for EOs, classifies each by topic via the
    eo_classifier, and enriches with macro regime, FOMC proximity, and
    administration columns.

    Returns:
        DataFrame with columns: id, publication_date, title, topic, tickers,
        direction, tariff_direction, macro_regime, fomc_proximity, administration.
    """
    rows = conn.execute(
        "SELECT id, publication_date, title "
        "FROM regulatory_events "
        "WHERE event_type = 'executive_order' "
        "ORDER BY publication_date"
    ).fetchall()

    records = []
    for row_id, pub_date, title in rows:
        classification = classify_eo(title)
        macro_regime = get_macro_regime_at_date(conn, pub_date)
        fomc_proximity = get_fomc_proximity(conn, pub_date)
        administration = "biden" if pub_date < ADMINISTRATION_CUTOFF else "trump"

        records.append(
            {
                "id": row_id,
                "publication_date": pub_date,
                "title": title,
                "topic": classification["topic"],
                "tickers": classification["tickers"],
                "direction": classification["direction"],
                "tariff_direction": classification["tariff_direction"],
                "macro_regime": macro_regime,
                "fomc_proximity": fomc_proximity,
                "administration": administration,
            }
        )

    df = pd.DataFrame(records)
    logger.info(
        "Loaded %d executive orders (%d classified, %d 'other')",
        len(df),
        len(df[df["topic"] != "other"]) if not df.empty else 0,
        len(df[df["topic"] == "other"]) if not df.empty else 0,
    )
    return df


# ── Per-Topic Event Studies ──────────────────────────────────────────


def _build_events(eos_df: pd.DataFrame, topic_tickers_override: dict | None = None) -> list[dict]:
    """Build event list from EO DataFrame, one event per (EO, ticker) pair.

    Args:
        eos_df: DataFrame of EOs (must have publication_date, title, topic, tickers).
        topic_tickers_override: If provided, use these tickers instead of the
            per-row tickers column.

    Returns:
        List of dicts suitable for EventStudy.run().
    """
    events = []
    for _, row in eos_df.iterrows():
        if topic_tickers_override and row["topic"] in topic_tickers_override:
            tickers = topic_tickers_override[row["topic"]]
        else:
            tickers = row["tickers"] if isinstance(row["tickers"], list) else []

        for ticker in tickers:
            events.append(
                {
                    "date": row["publication_date"],
                    "ticker": ticker,
                    "label": row["title"],
                }
            )
    return events


def _run_per_topic_studies(eos_df: pd.DataFrame, db_path: str) -> list[EventStudyResults]:
    """Run event studies for each topic and an all-topics aggregate.

    Args:
        eos_df: Full classified EO DataFrame.
        db_path: Path to the SQLite database.

    Returns:
        List of EventStudyResults, one per topic plus the aggregate.
    """
    es = EventStudy(db_path)
    results = []

    for topic in TOPIC_KEYWORDS.keys():
        topic_df = eos_df[eos_df["topic"] == topic]
        if topic_df.empty:
            logger.info("No EOs for topic '%s', skipping", topic)
            continue

        events = _build_events(topic_df)
        if not events:
            logger.info("No events generated for topic '%s', skipping", topic)
            continue

        try:
            study_result = es.run(
                events,
                study_name=f"report2_eo_{topic}",
                hypothesis=f"EOs on {topic} generate abnormal returns",
                window_pre=1,
                window_post=5,
            )
            results.append(study_result)
            logger.info(
                "Topic '%s': N=%d, CAR=%+.2f%%, p=%.4f",
                topic,
                study_result.num_events,
                study_result.mean_car * 100,
                study_result.p_value,
            )
        except Exception as e:
            logger.error("Event study failed for topic '%s': %s", topic, e)

    # All topics aggregate (exclude "other")
    classified_df = eos_df[eos_df["topic"] != "other"]
    if not classified_df.empty:
        all_events = _build_events(classified_df)
        if all_events:
            try:
                aggregate_result = es.run(
                    all_events,
                    study_name="report2_eo_all_topics",
                    hypothesis="Classified EOs generate abnormal returns across all topics",
                    window_pre=1,
                    window_post=5,
                )
                results.append(aggregate_result)
                logger.info(
                    "All topics aggregate: N=%d, CAR=%+.2f%%, p=%.4f",
                    aggregate_result.num_events,
                    aggregate_result.mean_car * 100,
                    aggregate_result.p_value,
                )
            except Exception as e:
                logger.error("Aggregate event study failed: %s", e)

    return results


# ── Tariff Imposition vs. Relief ─────────────────────────────────────


def _run_imposition_relief_test(eos_df: pd.DataFrame, db_path: str) -> dict:
    """Compare abnormal returns for tariff imposition vs. relief EOs.

    Splits tariff_trade EOs by tariff_direction and runs separate event studies,
    then applies a Wilcoxon rank-sum test to the CARs.

    Args:
        eos_df: Full classified EO DataFrame.
        db_path: Path to the SQLite database.

    Returns:
        Dict with imposition_results, relief_results, and wilcoxon_test.
    """
    es = EventStudy(db_path)
    tariff_df = eos_df[eos_df["topic"] == "tariff_trade"]
    tariff_tickers = TOPIC_TICKERS["tariff_trade"]

    result = {
        "imposition_results": None,
        "relief_results": None,
        "wilcoxon_test": {"error": "Insufficient data", "significant": False},
    }

    for direction in ("imposition", "relief"):
        subset = tariff_df[tariff_df["tariff_direction"] == direction]
        if subset.empty:
            logger.info("No %s tariff EOs found", direction)
            continue

        events = []
        for _, row in subset.iterrows():
            for ticker in tariff_tickers:
                events.append(
                    {
                        "date": row["publication_date"],
                        "ticker": ticker,
                        "label": row["title"],
                    }
                )

        if not events:
            continue

        try:
            study_result = es.run(
                events,
                study_name=f"report2_tariff_{direction}",
                hypothesis=f"Tariff {direction} EOs generate abnormal returns",
                window_pre=1,
                window_post=5,
            )
            result[f"{direction}_results"] = study_result
            logger.info(
                "Tariff %s: N=%d, CAR=%+.2f%%, p=%.4f",
                direction,
                study_result.num_events,
                study_result.mean_car * 100,
                study_result.p_value,
            )
        except Exception as e:
            logger.error("Tariff %s study failed: %s", direction, e)

    # Wilcoxon rank-sum test between the two groups
    imp_results = result["imposition_results"]
    rel_results = result["relief_results"]
    if imp_results and rel_results and imp_results.per_event_results and rel_results.per_event_results:
        imp_cars = [r["car_full"] for r in imp_results.per_event_results]
        rel_cars = [r["car_full"] for r in rel_results.per_event_results]
        result["wilcoxon_test"] = run_wilcoxon_ranksum(imp_cars, rel_cars)

    return result


# ── Administration Subsample ─────────────────────────────────────────


def _run_administration_subsample(eos_df: pd.DataFrame, db_path: str) -> dict:
    """Compare abnormal returns between Biden and Trump administrations.

    Splits all classified EOs by administration and runs separate event studies,
    then performs a two-sample t-test on the CARs.

    Args:
        eos_df: Full classified EO DataFrame.
        db_path: Path to the SQLite database.

    Returns:
        Dict with biden_results, trump_results, and comparison_test.
    """
    es = EventStudy(db_path)
    classified_df = eos_df[eos_df["topic"] != "other"]

    result = {
        "biden_results": None,
        "trump_results": None,
        "comparison_test": {"error": "Insufficient data", "significant": False},
    }

    for admin in ("biden", "trump"):
        subset = classified_df[classified_df["administration"] == admin]
        if subset.empty:
            logger.info("No classified EOs for administration '%s'", admin)
            continue

        events = _build_events(subset)
        if not events:
            continue

        try:
            study_result = es.run(
                events,
                study_name=f"report2_admin_{admin}",
                hypothesis=f"Classified EOs under {admin} generate abnormal returns",
                window_pre=1,
                window_post=5,
            )
            result[f"{admin}_results"] = study_result
            logger.info(
                "Admin %s: N=%d, CAR=%+.2f%%, p=%.4f",
                admin,
                study_result.num_events,
                study_result.mean_car * 100,
                study_result.p_value,
            )
        except Exception as e:
            logger.error("Admin subsample '%s' study failed: %s", admin, e)

    # Two-sample t-test
    biden_res = result["biden_results"]
    trump_res = result["trump_results"]
    if biden_res and trump_res and biden_res.per_event_results and trump_res.per_event_results:
        biden_cars = [r["car_full"] for r in biden_res.per_event_results]
        trump_cars = [r["car_full"] for r in trump_res.per_event_results]
        try:
            t_stat, p_value = stats.ttest_ind(biden_cars, trump_cars, equal_var=False)
            result["comparison_test"] = {
                "t_statistic": float(t_stat),
                "p_value": float(p_value),
                "significant": float(p_value) < 0.05,
                "mean_biden": float(np.mean(biden_cars)),
                "mean_trump": float(np.mean(trump_cars)),
                "n_biden": len(biden_cars),
                "n_trump": len(trump_cars),
            }
        except Exception as e:
            result["comparison_test"] = {"error": str(e), "significant": False}

    return result


# ── Cross-Sectional Regression ───────────────────────────────────────


def _run_cross_sectional(
    eos_df: pd.DataFrame,
    per_topic_results: list[EventStudyResults],
    db_path: str,
) -> dict:
    """Regress per-event CARs on topic dummies, macro regime, and FOMC proximity.

    Extracts per-event CARs from the aggregate (all-topics) event study,
    merges back to the EO metadata, one-hot-encodes topics, and runs OLS.

    Args:
        eos_df: Full classified EO DataFrame.
        per_topic_results: List of EventStudyResults from _run_per_topic_studies.
        db_path: Path to the SQLite database.

    Returns:
        Regression results dict from run_cross_sectional_regression.
    """
    # Find the aggregate study
    aggregate_study = None
    for r in per_topic_results:
        if r.study_name == "report2_eo_all_topics":
            aggregate_study = r
            break

    if aggregate_study is None or not aggregate_study.per_event_results:
        return {"error": "No aggregate study results available"}

    # Build per-event CAR DataFrame from the aggregate study
    car_records = []
    for evt in aggregate_study.per_event_results:
        car_records.append(
            {
                "event_date": evt["event_date"],
                "ticker": evt["ticker"],
                "car_full": evt["car_full"],
            }
        )
    car_df = pd.DataFrame(car_records)

    if car_df.empty:
        return {"error": "No per-event CARs available"}

    # Build the EO-level metadata: one row per (EO, ticker) pair
    meta_records = []
    classified_df = eos_df[eos_df["topic"] != "other"]
    for _, row in classified_df.iterrows():
        tickers = row["tickers"] if isinstance(row["tickers"], list) else []
        for ticker in tickers:
            meta_records.append(
                {
                    "event_date": row["publication_date"],
                    "ticker": ticker,
                    "topic": row["topic"],
                    "macro_regime": row["macro_regime"],
                    "fomc_proximity": row["fomc_proximity"],
                    "administration": row["administration"],
                }
            )
    meta_df = pd.DataFrame(meta_records)

    if meta_df.empty:
        return {"error": "No metadata records built"}

    # Merge CARs with metadata on (event_date, ticker)
    merged = pd.merge(car_df, meta_df, on=["event_date", "ticker"], how="inner")

    if merged.empty or len(merged) < 10:
        return {"error": f"Insufficient merged observations ({len(merged)})"}

    # Create topic dummies
    topic_dummies = pd.get_dummies(merged["topic"], prefix="topic", dtype=float)
    # Drop one to avoid multicollinearity (drop "other" if present, else first)
    if "topic_other" in topic_dummies.columns:
        topic_dummies = topic_dummies.drop(columns=["topic_other"])
    elif len(topic_dummies.columns) > 1:
        topic_dummies = topic_dummies.iloc[:, 1:]

    # Administration dummy (1 = trump, 0 = biden)
    merged["admin_trump"] = (merged["administration"] == "trump").astype(float)

    # Coerce numeric columns
    merged["macro_regime"] = pd.to_numeric(merged["macro_regime"], errors="coerce")
    merged["fomc_proximity"] = pd.to_numeric(merged["fomc_proximity"], errors="coerce")

    # Assemble regression DataFrame
    reg_df = pd.concat([merged[["car_full", "macro_regime", "fomc_proximity"]], topic_dummies], axis=1)
    x_cols = list(topic_dummies.columns) + ["macro_regime", "fomc_proximity"]

    result = run_cross_sectional_regression(reg_df, "car_full", x_cols)
    logger.info("Cross-sectional regression: %s", result)
    return result


# ── Recommendations Builder ──────────────────────────────────────────


def _build_recommendations(
    per_topic_results: list[EventStudyResults],
    imposition_relief: dict,
    admin_subsample: dict,
    regression: dict,
) -> list[str]:
    """Generate plain-English findings from the analyses.

    Args:
        per_topic_results: Per-topic event study results.
        imposition_relief: Imposition vs. relief test output.
        admin_subsample: Administration subsample output.
        regression: Cross-sectional regression output.

    Returns:
        List of recommendation strings.
    """
    recs = []

    # Per-topic findings
    significant_topics = []
    non_significant_topics = []
    for r in per_topic_results:
        if r.study_name == "report2_eo_all_topics":
            recs.append(
                f"Aggregate across all classified topics: mean CAR={r.mean_car:+.2%} "
                f"(p={r.p_value:.4f}, N={r.num_events}). "
                f"{'Statistically significant.' if r.is_significant() else 'Not significant.'}"
            )
        else:
            topic_label = r.study_name.replace("report2_eo_", "")
            if r.is_significant():
                significant_topics.append(
                    f"{topic_label} (CAR={r.mean_car:+.2%}, p={r.p_value:.4f}, N={r.num_events})"
                )
            else:
                non_significant_topics.append(
                    f"{topic_label} (CAR={r.mean_car:+.2%}, p={r.p_value:.4f}, N={r.num_events})"
                )

    if significant_topics:
        recs.append(f"Significant topics: {'; '.join(significant_topics)}.")
    if non_significant_topics:
        recs.append(f"Non-significant topics: {'; '.join(non_significant_topics)}.")

    # Imposition vs relief
    wilcoxon = imposition_relief.get("wilcoxon_test", {})
    if wilcoxon.get("significant"):
        recs.append(
            f"Tariff imposition vs. relief CARs differ significantly "
            f"(U={wilcoxon.get('u_statistic', 0):.0f}, p={wilcoxon.get('p_value', 1):.4f}). "
            f"Imposition mean={wilcoxon.get('mean_a', 0):+.2%}, "
            f"relief mean={wilcoxon.get('mean_b', 0):+.2%}."
        )
    elif "error" not in wilcoxon:
        recs.append(
            f"No significant difference between tariff imposition and relief CARs "
            f"(p={wilcoxon.get('p_value', 1):.4f})."
        )

    # Admin subsample
    comparison = admin_subsample.get("comparison_test", {})
    if "error" not in comparison:
        if comparison.get("significant"):
            recs.append(
                f"Significant difference in CARs between administrations "
                f"(t={comparison.get('t_statistic', 0):.3f}, p={comparison.get('p_value', 1):.4f}). "
                f"Biden mean={comparison.get('mean_biden', 0):+.2%}, "
                f"Trump mean={comparison.get('mean_trump', 0):+.2%}."
            )
        else:
            recs.append(
                f"No significant difference between administrations "
                f"(p={comparison.get('p_value', 1):.4f})."
            )

    # Regression
    if "r_squared" in regression:
        recs.append(
            f"Cross-sectional regression R-squared={regression['r_squared']:.4f} "
            f"(adj={regression.get('adj_r_squared', 0):.4f}, N={regression.get('n_obs', 0)})."
        )
        sig_vars = [
            k for k, v in regression.get("p_values", {}).items()
            if v < 0.05 and k != "const"
        ]
        if sig_vars:
            recs.append(f"Significant regression predictors: {', '.join(sig_vars)}.")
        else:
            recs.append("No individual regression predictors significant at 5%.")
    elif "error" in regression:
        recs.append(f"Cross-sectional regression not run: {regression['error']}.")

    return recs


# ── Main Entry Point ─────────────────────────────────────────────────


def run_report(db_path: str | None = None) -> ResearchReportResults:
    """Run Report 2: Executive Order Market Impact.

    Executes per-topic event studies, tariff imposition/relief comparison,
    administration subsample analysis, and cross-sectional regression, then
    assembles findings into a ResearchReportResults container.

    Args:
        db_path: Path to the SQLite database. Defaults to config.DB_PATH.

    Returns:
        ResearchReportResults with all sub-studies and analyses.
    """
    db_path = db_path or DB_PATH
    logger.info("Starting Report 2: Executive Order Market Impact (db=%s)", db_path)

    try:
        conn = sqlite3.connect(db_path)
        eos_df = _load_eos(conn)
        conn.close()
    except Exception as e:
        logger.error("Failed to load executive orders: %s", e)
        return ResearchReportResults(
            report_name="Executive Order Market Impact",
            report_number=2,
            hypothesis="EOs classified by topic generate significant abnormal returns in sector tickers",
            recommendations=[f"Report failed during data loading: {e}"],
        )

    if eos_df.empty:
        return ResearchReportResults(
            report_name="Executive Order Market Impact",
            report_number=2,
            hypothesis="EOs classified by topic generate significant abnormal returns in sector tickers",
            recommendations=["No executive orders found in the database."],
        )

    # Per-topic event studies
    all_event_studies: list[EventStudyResults] = []
    try:
        per_topic_results = _run_per_topic_studies(eos_df, db_path)
        all_event_studies.extend(per_topic_results)
    except Exception as e:
        logger.error("Per-topic studies failed: %s", e)
        per_topic_results = []

    # Imposition vs relief
    try:
        imposition_relief = _run_imposition_relief_test(eos_df, db_path)
        for key in ("imposition_results", "relief_results"):
            if imposition_relief.get(key) is not None:
                all_event_studies.append(imposition_relief[key])
    except Exception as e:
        logger.error("Imposition/relief test failed: %s", e)
        imposition_relief = {
            "imposition_results": None,
            "relief_results": None,
            "wilcoxon_test": {"error": str(e), "significant": False},
        }

    # Administration subsample
    try:
        admin_subsample = _run_administration_subsample(eos_df, db_path)
        for key in ("biden_results", "trump_results"):
            if admin_subsample.get(key) is not None:
                all_event_studies.append(admin_subsample[key])
    except Exception as e:
        logger.error("Administration subsample failed: %s", e)
        admin_subsample = {
            "biden_results": None,
            "trump_results": None,
            "comparison_test": {"error": str(e), "significant": False},
        }

    # Cross-sectional regression
    try:
        regression = _run_cross_sectional(eos_df, per_topic_results, db_path)
    except Exception as e:
        logger.error("Cross-sectional regression failed: %s", e)
        regression = {"error": str(e)}

    # Build recommendations
    recommendations = _build_recommendations(
        per_topic_results, imposition_relief, admin_subsample, regression,
    )

    # Summary stats
    classified_df = eos_df[eos_df["topic"] != "other"]
    summary_stats = {
        "total_eos": len(eos_df),
        "classified_eos": len(classified_df),
        "unclassified_eos": len(eos_df) - len(classified_df),
        "topic_counts": classified_df["topic"].value_counts().to_dict() if not classified_df.empty else {},
        "admin_counts": eos_df["administration"].value_counts().to_dict() if not eos_df.empty else {},
    }

    report = ResearchReportResults(
        report_name="Executive Order Market Impact",
        report_number=2,
        hypothesis="EOs classified by topic generate significant abnormal returns in sector tickers",
        event_studies=all_event_studies,
        additional_analyses={
            "cross_sectional_regression": regression,
            "imposition_relief_test": imposition_relief.get("wilcoxon_test", {}),
            "administration_comparison": admin_subsample.get("comparison_test", {}),
        },
        summary_stats=summary_stats,
        recommendations=recommendations,
    )

    logger.info("Report 2 complete: %d sub-studies, %d recommendations", len(all_event_studies), len(recommendations))
    return report
