"""Report 5: Macro Regime-Conditional Signal Returns.

Meta-study that consumes results from Reports 1-4 to test whether
political/regulatory signal returns vary systematically across Hedgeye-style
macro regime quadrants. If so, the finding supports regime-conditional
position sizing in the signal generator.
"""

import logging
import sqlite3
from datetime import date as date_type

import numpy as np
import pandas as pd
from scipy import stats

from config import DB_PATH
from analysis.event_study import EventStudy, EventStudyResults
from analysis.macro_regime import QUADRANTS
from analysis.research.base import (
    ResearchReportResults,
    get_macro_regime_at_date,
    run_cross_sectional_regression,
    run_wilcoxon_ranksum,
)

logger = logging.getLogger(__name__)


def run_report(
    report1: ResearchReportResults | None = None,
    report2: ResearchReportResults | None = None,
    report3: ResearchReportResults | None = None,
    report4: ResearchReportResults | None = None,
    db_path: str | None = None,
) -> ResearchReportResults:
    """Run the macro regime-conditional meta-study.

    Accepts already-computed report results to avoid re-running.
    If any report is None, attempts to load from the database.
    """
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)

    try:
        # Collect all per-event CARs
        cars_df = _collect_all_event_cars(
            report1, report2, report3, report4, conn
        )

        if cars_df.empty or len(cars_df) < 20:
            logger.warning("Insufficient per-event CARs for meta-study: %d", len(cars_df))
            return ResearchReportResults(
                report_name="Macro Regime-Conditional Signal Returns",
                report_number=5,
                hypothesis="Signal CARs vary by macro regime quadrant",
                recommendations=["Insufficient data for meta-study"],
            )

        # Annotate with macro regime
        cars_df["regime"] = cars_df["event_date"].apply(
            lambda d: get_macro_regime_at_date(conn, d)
        )
        cars_df = cars_df.dropna(subset=["regime"])
        cars_df["regime"] = cars_df["regime"].astype(int)

        if len(cars_df) < 20:
            logger.warning("Insufficient regime-annotated CARs: %d", len(cars_df))
            return ResearchReportResults(
                report_name="Macro Regime-Conditional Signal Returns",
                report_number=5,
                hypothesis="Signal CARs vary by macro regime quadrant",
                recommendations=["Insufficient regime data for meta-study"],
            )

        event_studies = []
        additional = {}
        summary_stats = {
            "total_events": len(cars_df),
            "events_by_regime": cars_df["regime"].value_counts().to_dict(),
            "events_by_source": cars_df["source_report"].value_counts().to_dict(),
        }

        # Per-regime event studies
        regime_studies = _run_per_regime_studies(cars_df, db)
        event_studies.extend(regime_studies)

        # ANOVA
        anova_result = _run_anova_by_regime(cars_df)
        additional["anova_by_regime"] = anova_result

        # Interaction regression
        interaction_result = _run_interaction_regression(cars_df)
        additional["interaction_regression"] = interaction_result

        # Regime-conditional backtest
        backtest_result = _backtest_regime_conditional(cars_df, conn)
        additional["regime_conditional_backtest"] = backtest_result

        # Robustness: VIX-based regimes
        vix_result = _robustness_vix_regimes(cars_df, conn)
        additional["vix_regime_robustness"] = vix_result

        # Robustness: yield-curve-based regimes
        yc_result = _robustness_yield_curve_regimes(cars_df, conn)
        additional["yield_curve_regime_robustness"] = yc_result

        # Build recommendations
        recommendations = _build_recommendations(
            anova_result, interaction_result, backtest_result,
            vix_result, yc_result, cars_df,
        )

        return ResearchReportResults(
            report_name="Macro Regime-Conditional Signal Returns",
            report_number=5,
            hypothesis="Signal CARs vary by macro regime quadrant",
            event_studies=event_studies,
            additional_analyses=additional,
            summary_stats=summary_stats,
            recommendations=recommendations,
            signal_parameters={
                "position_modifiers": {
                    q: QUADRANTS[q]["position_modifier"] for q in QUADRANTS
                },
                "regime_labels": {
                    q: QUADRANTS[q]["label"] for q in QUADRANTS
                },
            },
        )

    except Exception as e:
        logger.error("Report 5 failed: %s", e, exc_info=True)
        return ResearchReportResults(
            report_name="Macro Regime-Conditional Signal Returns",
            report_number=5,
            hypothesis="Signal CARs vary by macro regime quadrant",
            recommendations=[f"Report failed: {e}"],
        )
    finally:
        conn.close()


# ── Data Collection ────────────────────────────────────────────────


def _collect_all_event_cars(
    report1: ResearchReportResults | None,
    report2: ResearchReportResults | None,
    report3: ResearchReportResults | None,
    report4: ResearchReportResults | None,
    conn: sqlite3.Connection,
) -> pd.DataFrame:
    """Gather per-event CARs from Reports 1-4 into a single DataFrame.

    Sources (in priority order):
    1. Passed-in ResearchReportResults objects (from in-memory runs)
    2. Database event_study_results table (from prior saved runs)
    """
    all_rows = []

    report_map = {
        "report1_reg_shocks": report1,
        "report2_eo_impact": report2,
        "report3_reg_pipeline": report3,
        "report4_tariff_asymmetry": report4,
    }

    for report_key, report_obj in report_map.items():
        if report_obj is not None:
            for es in report_obj.event_studies:
                for per in es.per_event_results:
                    car = per.get("car_full") or per.get("car_post")
                    if car is None:
                        continue
                    all_rows.append({
                        "event_date": per.get("event_date", ""),
                        "ticker": per.get("ticker", ""),
                        "car": float(car),
                        "source_report": report_key,
                        "study_name": es.study_name,
                        "label": per.get("label", ""),
                    })
        else:
            # Try loading from DB
            try:
                prefix = report_key.replace("_", "%")
                db_rows = conn.execute(
                    """SELECT r.event_date, r.ticker, r.car_full, r.car_post,
                              s.study_name
                       FROM event_study_results r
                       JOIN event_studies s ON r.study_id = s.id
                       WHERE s.study_name LIKE ?""",
                    (f"%{prefix}%",),
                ).fetchall()

                for row in db_rows:
                    car = row[2] if row[2] is not None else row[3]
                    if car is None:
                        continue
                    all_rows.append({
                        "event_date": row[0],
                        "ticker": row[1],
                        "car": float(car),
                        "source_report": report_key,
                        "study_name": row[4],
                        "label": "",
                    })
            except Exception as e:
                logger.warning("Could not load %s from DB: %s", report_key, e)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    # Deduplicate on (event_date, ticker, source_report)
    df = df.drop_duplicates(subset=["event_date", "ticker", "source_report"])
    return df


# ── Per-Regime Event Studies ───────────────────────────────────────


def _run_per_regime_studies(cars_df: pd.DataFrame, db_path: str) -> list[EventStudyResults]:
    """Run separate event studies for each macro regime quadrant."""
    studies = []

    for q in sorted(cars_df["regime"].unique()):
        label = QUADRANTS.get(int(q), {}).get("label", f"Q{q}")
        subset = cars_df[cars_df["regime"] == q]

        if len(subset) < 5:
            continue

        events = [
            {"date": row["event_date"], "ticker": row["ticker"],
             "label": f"{row['source_report']}|{row.get('label', '')}"}
            for _, row in subset.iterrows()
        ]

        try:
            es = EventStudy(db_path=db_path, benchmark_ticker="SPY")
            result = es.run(
                events,
                study_name=f"report5_regime_q{int(q)}_{label.lower()}",
                hypothesis=f"Signal CARs during {label} regime (Q{int(q)})",
                window_pre=1,
                window_post=5,
                benchmark="SPY",
            )
            studies.append(result)
        except Exception as e:
            logger.warning("Per-regime study Q%d failed: %s", q, e)

    return studies


# ── ANOVA ──────────────────────────────────────────────────────────


def _run_anova_by_regime(cars_df: pd.DataFrame) -> dict:
    """One-way ANOVA on CARs across regime quadrants + Tukey HSD."""
    groups = [
        group["car"].values
        for _, group in cars_df.groupby("regime")
        if len(group) >= 3
    ]

    if len(groups) < 2:
        return {"error": "Need at least 2 regime groups with >= 3 events", "significant": False}

    try:
        f_stat, p_value = stats.f_oneway(*groups)
    except Exception as e:
        return {"error": str(e), "significant": False}

    result = {
        "f_statistic": float(f_stat) if not np.isnan(f_stat) else 0.0,
        "p_value": float(p_value) if not np.isnan(p_value) else 1.0,
        "significant": bool(p_value < 0.05) if not np.isnan(p_value) else False,
        "group_stats": {},
    }

    for q, group in cars_df.groupby("regime"):
        label = QUADRANTS.get(int(q), {}).get("label", f"Q{q}")
        result["group_stats"][f"Q{int(q)}_{label}"] = {
            "n": len(group),
            "mean_car": float(group["car"].mean()),
            "median_car": float(group["car"].median()),
            "std_car": float(group["car"].std()),
        }

    # Tukey HSD post-hoc
    try:
        from statsmodels.stats.multicomp import pairwise_tukeyhsd

        tukey = pairwise_tukeyhsd(cars_df["car"].values, cars_df["regime"].values, alpha=0.05)
        pairwise = []
        for row in tukey.summary().data[1:]:
            pairwise.append({
                "group1": str(row[0]),
                "group2": str(row[1]),
                "meandiff": float(row[2]),
                "p_adj": float(row[3]),
                "reject": bool(row[5]) if len(row) > 5 else float(row[3]) < 0.05,
            })
        result["tukey_hsd"] = pairwise
    except Exception as e:
        result["tukey_hsd_error"] = str(e)

    return result


# ── Interaction Regression ─────────────────────────────────────────


def _run_interaction_regression(cars_df: pd.DataFrame) -> dict:
    """CAR = b1(impact_proxy) + b2(regime_dummies) + b3(impact x regime) + controls."""
    df = cars_df.copy()

    # Create impact proxy: absolute CAR as a proxy for event magnitude
    df["abs_car"] = df["car"].abs()

    # Regime dummies (drop Q1 as reference)
    regime_dummies = pd.get_dummies(df["regime"], prefix="regime", dtype=float)
    regime_cols = [c for c in regime_dummies.columns if c != "regime_1"]
    df = pd.concat([df, regime_dummies[regime_cols]], axis=1)

    # Source report dummies
    source_dummies = pd.get_dummies(df["source_report"], prefix="src", dtype=float)
    src_cols = list(source_dummies.columns[1:])  # drop first as reference
    df = pd.concat([df, source_dummies[src_cols]], axis=1)

    x_cols = regime_cols + src_cols

    if len(x_cols) == 0 or len(df) < len(x_cols) + 5:
        return {"error": "Insufficient data for regression"}

    return run_cross_sectional_regression(df, "car", x_cols, add_constant=True)


# ── Regime-Conditional Backtest ────────────────────────────────────


def _backtest_regime_conditional(cars_df: pd.DataFrame, conn: sqlite3.Connection) -> dict:
    """Simulate regime-conditional position sizing vs equal-weighted.

    Uses QUADRANTS[q]["position_modifier"] as the position size multiplier.
    """
    df = cars_df.copy()
    df["position_modifier"] = df["regime"].map(
        lambda q: QUADRANTS.get(int(q), {}).get("position_modifier", 1.0)
    )

    # Equal-weighted returns
    eq_returns = df["car"].values
    # Regime-weighted returns
    rw_returns = df["car"].values * df["position_modifier"].values

    if len(eq_returns) < 10:
        return {"error": "Insufficient events for backtest"}

    eq_mean = float(np.mean(eq_returns))
    eq_std = float(np.std(eq_returns, ddof=1)) if len(eq_returns) > 1 else 1.0
    eq_sharpe = eq_mean / eq_std if eq_std > 0 else 0.0

    rw_mean = float(np.mean(rw_returns))
    rw_std = float(np.std(rw_returns, ddof=1)) if len(rw_returns) > 1 else 1.0
    rw_sharpe = rw_mean / rw_std if rw_std > 0 else 0.0

    # Cumulative equity curves
    eq_cum = list(np.cumsum(eq_returns).astype(float))
    rw_cum = list(np.cumsum(rw_returns).astype(float))

    # Max drawdown helper
    def _max_drawdown(cumulative: list[float]) -> float:
        peak = cumulative[0]
        max_dd = 0.0
        for val in cumulative:
            if val > peak:
                peak = val
            dd = (peak - val) / abs(peak) if peak != 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    return {
        "equal_weighted": {
            "mean_return": eq_mean,
            "std_return": eq_std,
            "sharpe": eq_sharpe,
            "total_return": float(np.sum(eq_returns)),
            "max_drawdown": _max_drawdown(eq_cum),
            "n_trades": len(eq_returns),
        },
        "regime_weighted": {
            "mean_return": rw_mean,
            "std_return": rw_std,
            "sharpe": rw_sharpe,
            "total_return": float(np.sum(rw_returns)),
            "max_drawdown": _max_drawdown(rw_cum),
            "n_trades": len(rw_returns),
        },
        "improvement": {
            "sharpe_delta": rw_sharpe - eq_sharpe,
            "return_delta": rw_mean - eq_mean,
            "regime_sizing_helps": rw_sharpe > eq_sharpe,
        },
        "equity_curves": {
            "equal_weighted": eq_cum,
            "regime_weighted": rw_cum,
        },
    }


# ── Robustness: VIX-Based Regimes ─────────────────────────────────


def _robustness_vix_regimes(cars_df: pd.DataFrame, conn: sqlite3.Connection) -> dict:
    """Re-test using VIX-based regime (low < 15, medium 15-25, high > 25)."""
    df = cars_df.copy()

    vix_vals = []
    for event_date in df["event_date"].values:
        try:
            row = conn.execute(
                """SELECT value FROM macro_indicators
                   WHERE series_id = 'VIXCLS' AND date <= ?
                   ORDER BY date DESC LIMIT 1""",
                (event_date,),
            ).fetchone()
            vix_vals.append(float(row[0]) if row else None)
        except Exception:
            vix_vals.append(None)

    df["vix"] = vix_vals
    df = df.dropna(subset=["vix"])

    if len(df) < 15:
        return {"error": "Insufficient VIX data", "significant": False}

    df["vix_regime"] = pd.cut(
        df["vix"],
        bins=[0, 15, 25, 100],
        labels=["low", "medium", "high"],
    )

    groups = {
        name: group["car"].values
        for name, group in df.groupby("vix_regime", observed=True)
        if len(group) >= 3
    }

    if len(groups) < 2:
        return {"error": "Need >= 2 VIX regime groups", "significant": False}

    try:
        f_stat, p_value = stats.f_oneway(*groups.values())
        return {
            "f_statistic": float(f_stat) if not np.isnan(f_stat) else 0.0,
            "p_value": float(p_value) if not np.isnan(p_value) else 1.0,
            "significant": bool(p_value < 0.05) if not np.isnan(p_value) else False,
            "group_stats": {
                name: {
                    "n": len(vals),
                    "mean_car": float(np.mean(vals)),
                    "std_car": float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0,
                }
                for name, vals in groups.items()
            },
        }
    except Exception as e:
        return {"error": str(e), "significant": False}


# ── Robustness: Yield-Curve Regimes ────────────────────────────────


def _robustness_yield_curve_regimes(cars_df: pd.DataFrame, conn: sqlite3.Connection) -> dict:
    """Re-test using yield-curve-based regime (inverted < 0, flat 0-0.5, normal > 0.5)."""
    df = cars_df.copy()

    spread_vals = []
    for event_date in df["event_date"].values:
        try:
            row = conn.execute(
                """SELECT value FROM macro_indicators
                   WHERE series_id = 'T10Y2Y' AND date <= ?
                   ORDER BY date DESC LIMIT 1""",
                (event_date,),
            ).fetchone()
            spread_vals.append(float(row[0]) if row else None)
        except Exception:
            spread_vals.append(None)

    df["yc_spread"] = spread_vals
    df = df.dropna(subset=["yc_spread"])

    if len(df) < 15:
        return {"error": "Insufficient yield-curve data", "significant": False}

    df["yc_regime"] = pd.cut(
        df["yc_spread"],
        bins=[-10, 0, 0.5, 10],
        labels=["inverted", "flat", "normal"],
    )

    groups = {
        name: group["car"].values
        for name, group in df.groupby("yc_regime", observed=True)
        if len(group) >= 3
    }

    if len(groups) < 2:
        return {"error": "Need >= 2 yield curve regime groups", "significant": False}

    try:
        f_stat, p_value = stats.f_oneway(*groups.values())
        return {
            "f_statistic": float(f_stat) if not np.isnan(f_stat) else 0.0,
            "p_value": float(p_value) if not np.isnan(p_value) else 1.0,
            "significant": bool(p_value < 0.05) if not np.isnan(p_value) else False,
            "group_stats": {
                name: {
                    "n": len(vals),
                    "mean_car": float(np.mean(vals)),
                    "std_car": float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0,
                }
                for name, vals in groups.items()
            },
        }
    except Exception as e:
        return {"error": str(e), "significant": False}


# ── Recommendations ───────────────────────────────────────────────


def _build_recommendations(
    anova: dict,
    interaction: dict,
    backtest: dict,
    vix: dict,
    yc: dict,
    cars_df: pd.DataFrame,
) -> list[str]:
    """Build plain-English recommendations from all analyses."""
    recs = []

    # ANOVA finding
    if anova.get("significant"):
        recs.append(
            f"ANOVA confirms CARs differ significantly across macro regimes "
            f"(F={anova['f_statistic']:.2f}, p={anova['p_value']:.4f}). "
            f"Regime-conditional position sizing is statistically justified."
        )
        # Identify best/worst regimes
        if "group_stats" in anova:
            best = max(anova["group_stats"].items(), key=lambda x: x[1]["mean_car"])
            worst = min(anova["group_stats"].items(), key=lambda x: x[1]["mean_car"])
            recs.append(
                f"Best regime for signals: {best[0]} (mean CAR={best[1]['mean_car']:+.2%}). "
                f"Worst: {worst[0]} (mean CAR={worst[1]['mean_car']:+.2%})."
            )
    else:
        recs.append(
            "ANOVA does NOT find significant CAR differences across regimes "
            f"(p={anova.get('p_value', 'N/A')}). Regime-conditional sizing may not add value."
        )

    # Backtest comparison
    if "improvement" in backtest:
        imp = backtest["improvement"]
        if imp.get("regime_sizing_helps"):
            recs.append(
                f"Regime-weighted backtest improves Sharpe by {imp['sharpe_delta']:+.3f} "
                f"over equal-weighted. Implement regime position modifiers."
            )
        else:
            recs.append(
                f"Regime-weighted backtest does NOT improve Sharpe "
                f"(delta={imp['sharpe_delta']:+.3f}). Equal-weight may suffice."
            )

    # Robustness
    robust_count = sum([
        1 if vix.get("significant") else 0,
        1 if yc.get("significant") else 0,
    ])
    if robust_count == 2:
        recs.append("Both VIX and yield-curve regime alternatives confirm the finding — robust result.")
    elif robust_count == 1:
        recs.append("One of two alternative regime definitions confirms the finding — partially robust.")
    else:
        recs.append("Neither VIX nor yield-curve regimes show significance — finding may be specific to Hedgeye quadrants.")

    # Regression
    if "r_squared" in interaction:
        recs.append(
            f"Cross-sectional regression R²={interaction['r_squared']:.3f}. "
            f"Regime dummies explain {'meaningful' if interaction['r_squared'] > 0.05 else 'limited'} "
            f"variation in CARs."
        )

    return recs
