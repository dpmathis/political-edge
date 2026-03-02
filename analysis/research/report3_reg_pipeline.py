"""Report 3: The Regulatory Pipeline as a Sector Rotation Signal.

Matches proposed rules to final rules by agency + title similarity,
measures CAR at proposed, comment deadline, and final rule dates,
and builds a 'pipeline pressure' sector rotation indicator.

Research question: Does the lag structure between proposed and final rules
predict when sector-specific regulatory impact materializes?
"""

import logging
import re
import sqlite3
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
from scipy import stats

from config import DB_PATH
from analysis.event_study import EventStudy, EventStudyResults
from analysis.research.base import (
    ResearchReportResults,
    SECTOR_ETF_ONLY,
    run_cross_sectional_regression,
)

logger = logging.getLogger(__name__)

MIN_TITLE_SIMILARITY = 0.35
MAX_DAYS_APART = 365
MIN_DAYS_APART = 60
DEFAULT_COMMENT_PERIOD_DAYS = 60


def run_report(db_path: str | None = None) -> ResearchReportResults:
    """Run the full Report 3 analysis.

    Steps:
    1. Load all proposed and final rules
    2. Match proposed → final by agency + title similarity + temporal proximity
    3. Run event studies at 3 time points (proposed, comment deadline, final)
    4. Build pipeline pressure indicator
    5. Test pipeline pressure as sector rotation signal
    """
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)
    report = ResearchReportResults(
        report_name="Regulatory Pipeline as Sector Rotation Signal",
        report_number=3,
        hypothesis="Markets underreact to proposed rules and price regulatory impact at final rule publication",
    )

    try:
        # Step 1: Load rules
        proposed_df, final_df = _load_rules(conn)
        if proposed_df.empty or final_df.empty:
            logger.warning("Insufficient proposed or final rules for Report 3")
            return report

        # Step 2: Match proposed → final
        matched_df = _match_proposed_to_final(proposed_df, final_df)
        report.summary_stats["total_proposed"] = len(proposed_df)
        report.summary_stats["total_final"] = len(final_df)
        report.summary_stats["matched_pairs"] = len(matched_df)

        if matched_df.empty:
            logger.warning("No proposed-final rule matches found")
            return report

        report.summary_stats["avg_days_between"] = float(matched_df["days_between"].mean())
        report.summary_stats["avg_title_similarity"] = float(matched_df["similarity"].mean())

        # Step 3: Three-stage event studies
        stage_results = _run_three_stage_event_studies(matched_df, db)
        report.event_studies.extend(stage_results)

        # Step 4: Pipeline pressure indicator
        pressure_df = _build_pipeline_pressure(proposed_df, matched_df, conn)
        report.additional_analyses["pipeline_pressure_summary"] = {
            "months_covered": len(pressure_df) if not pressure_df.empty else 0,
            "sectors_tracked": list(pressure_df["sector"].unique()) if not pressure_df.empty else [],
        }

        # Step 5: Test as rotation signal
        if not pressure_df.empty:
            rotation_result = _test_pipeline_as_rotation_signal(pressure_df, conn)
            report.additional_analyses["rotation_signal"] = rotation_result

        # Recommendations
        if report.event_studies:
            for es in report.event_studies:
                if es.is_significant():
                    report.recommendations.append(
                        f"{es.study_name}: Mean CAR {es.mean_car:+.2%} (p={es.p_value:.4f}) — "
                        f"statistically significant with {es.num_events} events"
                    )

    except Exception as e:
        logger.error("Report 3 failed: %s", e)
        report.additional_analyses["error"] = str(e)
    finally:
        conn.close()

    return report


def _load_rules(conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load proposed and final rules from regulatory_events."""
    try:
        proposed = pd.read_sql_query(
            """SELECT id, publication_date, title, agency, sectors, tickers,
                      impact_score, comment_deadline
               FROM regulatory_events
               WHERE event_type = 'proposed_rule'
                 AND title IS NOT NULL AND title != ''
               ORDER BY publication_date""",
            conn,
        )
        final = pd.read_sql_query(
            """SELECT id, publication_date, title, agency, sectors, tickers,
                      impact_score
               FROM regulatory_events
               WHERE event_type = 'final_rule'
                 AND title IS NOT NULL AND title != ''
               ORDER BY publication_date""",
            conn,
        )
    except Exception as e:
        logger.error("Failed to load rules: %s", e)
        return pd.DataFrame(), pd.DataFrame()

    for df in [proposed, final]:
        if not df.empty:
            df["pub_date"] = pd.to_datetime(df["publication_date"])

    return proposed, final


def _normalize_title(title: str) -> str:
    """Normalize title for comparison: lowercase, remove common prefixes."""
    t = title.lower().strip()
    # Remove common regulatory boilerplate prefixes
    for prefix in ["amendment to ", "amendments to ", "revision of ", "revisions to ",
                    "final rule: ", "proposed rule: ", "notice of proposed rulemaking: "]:
        if t.startswith(prefix):
            t = t[len(prefix):]
    # Remove dates and docket numbers
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", t)
    t = re.sub(r"\b[A-Z]{2,}-\d{4}-\d{4,}\b", "", t, flags=re.IGNORECASE)
    return t.strip()


def _match_proposed_to_final(
    proposed_df: pd.DataFrame,
    final_df: pd.DataFrame,
) -> pd.DataFrame:
    """Match proposed rules to their corresponding final rules.

    Matching criteria (all must pass):
    1. Same agency
    2. Title similarity >= MIN_TITLE_SIMILARITY
    3. Final rule 60-365 days after proposed rule
    """
    if proposed_df.empty or final_df.empty:
        return pd.DataFrame()

    matches = []
    # Group by agency to reduce search space
    proposed_agencies = proposed_df.groupby("agency")
    final_agencies = final_df.groupby("agency")

    common_agencies = set(proposed_df["agency"].dropna().unique()) & set(final_df["agency"].dropna().unique())
    logger.info("Matching proposed→final across %d common agencies", len(common_agencies))

    for agency in common_agencies:
        try:
            p_group = proposed_agencies.get_group(agency)
            f_group = final_agencies.get_group(agency)
        except KeyError:
            continue

        # Pre-normalize titles for this agency group
        p_titles = [(idx, _normalize_title(row["title"]), row) for idx, row in p_group.iterrows()]
        f_titles = [(idx, _normalize_title(row["title"]), row) for idx, row in f_group.iterrows()]

        for p_idx, p_norm, p_row in p_titles:
            p_date = p_row["pub_date"]
            best_match = None
            best_sim = 0.0

            for f_idx, f_norm, f_row in f_titles:
                f_date = f_row["pub_date"]
                days_diff = (f_date - p_date).days

                if days_diff < MIN_DAYS_APART or days_diff > MAX_DAYS_APART:
                    continue

                sim = SequenceMatcher(None, p_norm, f_norm).ratio()
                if sim >= MIN_TITLE_SIMILARITY and sim > best_sim:
                    best_sim = sim
                    best_match = (f_idx, f_row, days_diff, sim)

            if best_match:
                f_idx, f_row, days_diff, sim = best_match
                # Estimate comment deadline if missing
                comment_deadline = p_row.get("comment_deadline")
                if pd.isna(comment_deadline) or not comment_deadline:
                    comment_deadline = (p_date + pd.Timedelta(days=DEFAULT_COMMENT_PERIOD_DAYS)).strftime("%Y-%m-%d")

                matches.append({
                    "proposed_id": p_row["id"],
                    "final_id": f_row["id"],
                    "proposed_date": p_row["publication_date"],
                    "final_date": f_row["publication_date"],
                    "comment_deadline": comment_deadline,
                    "agency": agency,
                    "sectors": p_row.get("sectors", "") or f_row.get("sectors", ""),
                    "tickers": p_row.get("tickers", "") or f_row.get("tickers", ""),
                    "similarity": sim,
                    "days_between": days_diff,
                    "proposed_title": p_row["title"][:100],
                })

    logger.info("Found %d proposed→final matches", len(matches))
    return pd.DataFrame(matches) if matches else pd.DataFrame()


def _get_tickers_for_event(row: dict | pd.Series) -> list[str]:
    """Extract tickers from a matched row, falling back to sector ETF."""
    tickers_str = row.get("tickers", "")
    if tickers_str and isinstance(tickers_str, str) and tickers_str.strip():
        tickers = [t.strip() for t in tickers_str.split(",") if t.strip()]
        if tickers:
            return tickers[:3]  # Limit to avoid too many events per rule

    # Fall back to sector ETF
    sectors_str = row.get("sectors", "")
    if sectors_str and isinstance(sectors_str, str):
        for sector in sectors_str.split(","):
            sector = sector.strip()
            if sector in SECTOR_ETF_ONLY:
                return [SECTOR_ETF_ONLY[sector]]

    return ["SPY"]  # Ultimate fallback


def _run_three_stage_event_studies(
    matched_df: pd.DataFrame,
    db_path: str,
) -> list[EventStudyResults]:
    """Run event studies at proposed, comment deadline, and final rule dates."""
    es = EventStudy(db_path)
    results = []

    stages = [
        ("proposed_date", "report3_pipeline_proposed", "Proposed rule publication generates abnormal returns"),
        ("comment_deadline", "report3_pipeline_deadline", "Comment deadline passage generates abnormal returns"),
        ("final_date", "report3_pipeline_final", "Final rule publication generates abnormal returns"),
    ]

    for date_col, study_name, hypothesis in stages:
        events = []
        for _, row in matched_df.iterrows():
            event_date = row.get(date_col)
            if pd.isna(event_date) or not event_date:
                continue

            tickers = _get_tickers_for_event(row)
            for ticker in tickers:
                events.append({
                    "date": str(event_date)[:10],
                    "ticker": ticker,
                    "label": f"{row.get('proposed_title', '')[:50]} ({date_col})",
                })

        if not events:
            logger.warning("No events for stage %s", study_name)
            continue

        try:
            result = es.run(
                events,
                study_name=study_name,
                hypothesis=hypothesis,
                window_pre=1,
                window_post=5,
                method="market_adjusted",
                benchmark="SPY",
            )
            results.append(result)
            logger.info(
                "Stage %s: N=%d, CAR=%+.2f%%, p=%.4f",
                study_name, result.num_events, result.mean_car * 100, result.p_value,
            )
        except Exception as e:
            logger.error("Failed stage %s: %s", study_name, e)

    return results


def _build_pipeline_pressure(
    proposed_df: pd.DataFrame,
    matched_df: pd.DataFrame,
    conn: sqlite3.Connection,
) -> pd.DataFrame:
    """Build monthly pipeline pressure indicator per sector.

    Pipeline pressure = count of proposed rules where:
    - comment_deadline has passed
    - no matching final rule exists yet (as of that month)
    """
    if proposed_df.empty:
        return pd.DataFrame()

    # Get set of proposed IDs that have been matched
    matched_proposed_ids = set(matched_df["proposed_id"].tolist()) if not matched_df.empty else set()

    # Build monthly time series
    proposed_df = proposed_df.copy()
    if "pub_date" not in proposed_df.columns:
        proposed_df["pub_date"] = pd.to_datetime(proposed_df["publication_date"])

    # Estimate comment deadlines where missing
    proposed_df["cd"] = pd.to_datetime(proposed_df["comment_deadline"], errors="coerce")
    mask = proposed_df["cd"].isna()
    proposed_df.loc[mask, "cd"] = proposed_df.loc[mask, "pub_date"] + pd.Timedelta(days=DEFAULT_COMMENT_PERIOD_DAYS)

    # For each proposed rule, determine its sector
    proposed_df["sector"] = proposed_df["sectors"].apply(
        lambda s: s.split(",")[0].strip() if isinstance(s, str) and s.strip() else "Other"
    )

    # Generate monthly dates
    min_date = proposed_df["pub_date"].min()
    max_date = proposed_df["pub_date"].max()
    if pd.isna(min_date) or pd.isna(max_date):
        return pd.DataFrame()

    months = pd.date_range(min_date, max_date, freq="MS")

    pressure_rows = []
    for month in months:
        month_end = month + pd.offsets.MonthEnd(0)
        for sector in proposed_df["sector"].unique():
            if sector == "Other":
                continue

            sector_proposed = proposed_df[proposed_df["sector"] == sector]
            # Count proposed rules past comment deadline but not yet matched to final rule
            # as of this month
            past_deadline = sector_proposed[
                (sector_proposed["cd"] <= month_end) &
                (sector_proposed["pub_date"] <= month_end)
            ]
            # Exclude those that have been matched to a final rule published before month_end
            unresolved = past_deadline[~past_deadline["id"].isin(matched_proposed_ids)]

            pressure_rows.append({
                "month": month.strftime("%Y-%m-%d"),
                "sector": sector,
                "pressure": len(unresolved),
                "total_proposed": len(sector_proposed[sector_proposed["pub_date"] <= month_end]),
            })

    return pd.DataFrame(pressure_rows) if pressure_rows else pd.DataFrame()


def _test_pipeline_as_rotation_signal(
    pressure_df: pd.DataFrame,
    conn: sqlite3.Connection,
) -> dict:
    """Test pipeline pressure as a monthly sector rotation signal.

    Strategy: overweight sectors with LOW pipeline pressure (less regulatory
    uncertainty), underweight sectors with HIGH pressure.
    """
    if pressure_df.empty:
        return {"error": "No pressure data"}

    # Load monthly sector ETF returns
    try:
        market_df = pd.read_sql_query(
            """SELECT ticker, date, close FROM market_data
               WHERE ticker IN ('XLI','XLK','XLE','XLF','XLB','XLP')
               ORDER BY ticker, date""",
            conn,
        )
    except Exception:
        return {"error": "Could not load market data"}

    if market_df.empty:
        return {"error": "No market data for sector ETFs"}

    market_df["date"] = pd.to_datetime(market_df["date"])

    # Compute monthly returns per ETF
    monthly_returns = {}
    for ticker in market_df["ticker"].unique():
        t_df = market_df[market_df["ticker"] == ticker].sort_values("date")
        t_df = t_df.set_index("date")["close"].resample("MS").last()
        monthly_returns[ticker] = t_df.pct_change().dropna()

    if not monthly_returns:
        return {"error": "Could not compute monthly returns"}

    # Map sectors to ETFs
    sector_etf = {s: e for s, e in SECTOR_ETF_ONLY.items() if e in monthly_returns}

    # Build signal: for each month, rank sectors by pressure (low = good)
    pressure_pivot = pressure_df.pivot_table(
        index="month", columns="sector", values="pressure", fill_value=0
    )

    # Compute strategy returns: long low-pressure sectors, short high-pressure
    strategy_returns = []
    for month_str in pressure_pivot.index:
        month = pd.Timestamp(month_str)
        pressures = pressure_pivot.loc[month_str]

        # Get returns for next month (signal is lagged by 1 month)
        next_month = month + pd.offsets.MonthBegin(1)

        sector_rets = {}
        for sector, etf in sector_etf.items():
            if sector in pressures.index and etf in monthly_returns:
                ret_series = monthly_returns[etf]
                if next_month in ret_series.index:
                    sector_rets[sector] = {
                        "pressure": float(pressures.get(sector, 0)),
                        "return": float(ret_series[next_month]),
                    }

        if len(sector_rets) < 2:
            continue

        # Sort by pressure: long bottom half, short top half
        sorted_sectors = sorted(sector_rets.items(), key=lambda x: x[1]["pressure"])
        n = len(sorted_sectors)
        half = n // 2

        long_ret = np.mean([s[1]["return"] for s in sorted_sectors[:max(1, half)]])
        short_ret = np.mean([s[1]["return"] for s in sorted_sectors[max(1, half):]])
        ls_return = long_ret - short_ret

        strategy_returns.append({
            "month": next_month.strftime("%Y-%m-%d"),
            "long_short_return": ls_return,
            "long_return": long_ret,
            "short_return": short_ret,
        })

    if not strategy_returns:
        return {"error": "Insufficient overlapping data for backtest"}

    ret_df = pd.DataFrame(strategy_returns)
    ls_rets = ret_df["long_short_return"].values

    # Performance metrics
    mean_ret = float(np.mean(ls_rets))
    std_ret = float(np.std(ls_rets, ddof=1)) if len(ls_rets) > 1 else 0.0
    sharpe = (mean_ret / std_ret * np.sqrt(12)) if std_ret > 0 else 0.0
    cumulative = float(np.prod(1 + ls_rets) - 1)
    win_rate = float(np.mean(ls_rets > 0))

    t_stat_val, p_val = stats.ttest_1samp(ls_rets, 0.0) if len(ls_rets) > 1 else (0.0, 1.0)

    return {
        "months_tested": len(strategy_returns),
        "mean_monthly_return": mean_ret,
        "annualized_sharpe": sharpe,
        "cumulative_return": cumulative,
        "win_rate": win_rate,
        "t_statistic": float(t_stat_val),
        "p_value": float(p_val),
        "significant": float(p_val) < 0.05,
    }
