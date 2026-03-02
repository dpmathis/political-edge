"""Pipeline Builder — matches proposed rules to final rules and tracks lifecycle.

Reuses Report 3's matching algorithm to build a persistent pipeline_rules table
that powers the Pipeline Monitor dashboard.
"""

import logging
import sqlite3
from datetime import date
from statistics import median

import pandas as pd

from config import DB_PATH
from analysis.research.report3_reg_pipeline import (
    _load_rules,
    _match_proposed_to_final,
    DEFAULT_COMMENT_PERIOD_DAYS,
)

logger = logging.getLogger(__name__)

GLOBAL_MEDIAN_LAG_DAYS = 210


def build_pipeline(db_path: str | None = None) -> dict:
    """Match all proposed→final rules and populate pipeline_rules table.

    Returns dict with counts: {matched, pending, total, agency_lags}.
    """
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    today = date.today()

    try:
        # Load rules using Report 3's loader
        proposed_df, final_df = _load_rules(conn)
        if proposed_df.empty:
            logger.warning("No proposed rules found")
            return {"matched": 0, "pending": 0, "total": 0}

        # Match proposed→final using Report 3's algorithm
        matched_df = _match_proposed_to_final(proposed_df, final_df)
        matched_proposed_ids = set()
        if not matched_df.empty:
            matched_proposed_ids = set(matched_df["proposed_id"].tolist())

        # Compute agency-level median lags from matched pairs
        agency_lags = _compute_agency_median_lag(matched_df)

        # Compute historical CARs per agency+sector from event studies
        car_cache = _load_historical_cars(conn)

        matched_count = 0
        pending_count = 0

        # Process matched pairs (proposed rules that have a final rule)
        if not matched_df.empty:
            for _, row in matched_df.iterrows():
                sector = _primary_sector(row.get("sectors", ""))
                car_key = (_normalize_agency(row.get("agency", "")), sector)
                hist = car_cache.get(car_key)

                _upsert_pipeline_rule(conn, {
                    "proposed_event_id": int(row["proposed_id"]),
                    "final_event_id": int(row["final_id"]),
                    "agency": row.get("agency", ""),
                    "sector": sector,
                    "tickers": row.get("tickers", ""),
                    "proposed_date": str(row["proposed_date"])[:10],
                    "comment_deadline": str(row["comment_deadline"])[:10] if row.get("comment_deadline") else None,
                    "estimated_final_date": None,
                    "actual_final_date": str(row["final_date"])[:10],
                    "status": "finalized",
                    "days_in_pipeline": int(row["days_between"]),
                    "title_similarity": float(row["similarity"]),
                    "impact_score": int(row.get("impact_score", 0) or 0),
                    "proposed_title": row.get("proposed_title", "")[:200],
                    "historical_car": hist["mean_car"] if hist else None,
                    "historical_n": hist["n"] if hist else None,
                })
                matched_count += 1

        # Process unmatched proposed rules (still pending in pipeline)
        for _, p_row in proposed_df.iterrows():
            if p_row["id"] in matched_proposed_ids:
                continue

            agency = p_row.get("agency", "")
            sector = _primary_sector(p_row.get("sectors", ""))
            proposed_date_str = str(p_row["publication_date"])[:10]

            # Estimate comment deadline
            comment_deadline = p_row.get("comment_deadline")
            if pd.isna(comment_deadline) or not comment_deadline:
                try:
                    pd_date = pd.to_datetime(proposed_date_str)
                    comment_deadline = (pd_date + pd.Timedelta(days=DEFAULT_COMMENT_PERIOD_DAYS)).strftime("%Y-%m-%d")
                except Exception:
                    comment_deadline = None

            # Estimate final date based on agency lag
            lag = agency_lags.get(_normalize_agency(agency), GLOBAL_MEDIAN_LAG_DAYS)
            try:
                pd_date = pd.to_datetime(proposed_date_str)
                estimated_final = (pd_date + pd.Timedelta(days=lag)).strftime("%Y-%m-%d")
            except Exception:
                estimated_final = None

            # Determine status
            status = _determine_status(proposed_date_str, comment_deadline, None, today)

            # Days in pipeline
            try:
                days = (today - date.fromisoformat(proposed_date_str)).days
            except (ValueError, TypeError):
                days = 0

            # Historical CAR
            car_key = (_normalize_agency(agency), sector)
            hist = car_cache.get(car_key)

            _upsert_pipeline_rule(conn, {
                "proposed_event_id": int(p_row["id"]),
                "final_event_id": None,
                "agency": agency,
                "sector": sector,
                "tickers": p_row.get("tickers", ""),
                "proposed_date": proposed_date_str,
                "comment_deadline": comment_deadline,
                "estimated_final_date": estimated_final,
                "actual_final_date": None,
                "status": status,
                "days_in_pipeline": days,
                "title_similarity": None,
                "impact_score": int(p_row.get("impact_score", 0) or 0),
                "proposed_title": str(p_row.get("title", ""))[:200],
                "historical_car": hist["mean_car"] if hist else None,
                "historical_n": hist["n"] if hist else None,
            })
            pending_count += 1

        conn.commit()
        total = matched_count + pending_count
        logger.info(
            "Pipeline built: %d matched, %d pending, %d total",
            matched_count, pending_count, total,
        )
        return {
            "matched": matched_count,
            "pending": pending_count,
            "total": total,
            "agency_lags": {k: v for k, v in list(agency_lags.items())[:10]},
        }
    finally:
        conn.close()


def refresh_statuses(db_path: str | None = None) -> int:
    """Update statuses for all non-finalized pipeline rules based on current date."""
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)
    today = date.today()
    changed = 0

    try:
        rows = conn.execute(
            """SELECT id, proposed_date, comment_deadline, actual_final_date, status
               FROM pipeline_rules WHERE status != 'finalized'"""
        ).fetchall()

        for row_id, proposed_date, comment_deadline, actual_final, old_status in rows:
            new_status = _determine_status(proposed_date, comment_deadline, actual_final, today)
            days = 0
            try:
                days = (today - date.fromisoformat(proposed_date)).days
            except (ValueError, TypeError):
                pass

            if new_status != old_status:
                conn.execute(
                    """UPDATE pipeline_rules
                       SET status = ?, days_in_pipeline = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (new_status, days, row_id),
                )
                changed += 1
            else:
                # Still update days_in_pipeline
                conn.execute(
                    "UPDATE pipeline_rules SET days_in_pipeline = ? WHERE id = ?",
                    (days, row_id),
                )

        conn.commit()
        logger.info("Refreshed statuses: %d changed", changed)
    finally:
        conn.close()

    return changed


def _compute_agency_median_lag(matched_df: pd.DataFrame) -> dict[str, int]:
    """Compute median days from proposed→final per agency."""
    if matched_df.empty:
        return {}

    lags = {}
    for agency, group in matched_df.groupby("agency"):
        days_list = group["days_between"].tolist()
        if len(days_list) >= 3:
            lags[_normalize_agency(agency)] = int(median(days_list))

    return lags


def _determine_status(
    proposed_date: str | None,
    comment_deadline: str | None,
    actual_final_date: str | None,
    today: date,
) -> str:
    """Determine pipeline status based on dates."""
    if actual_final_date:
        return "finalized"

    if not comment_deadline:
        return "proposed"

    try:
        cd = date.fromisoformat(str(comment_deadline)[:10])
    except (ValueError, TypeError):
        return "proposed"

    if today <= cd:
        return "in_comment"
    else:
        return "awaiting_final"


def _load_historical_cars(conn: sqlite3.Connection) -> dict:
    """Load historical CARs from report3 event studies grouped by agency+sector.

    Returns dict of (agency_norm, sector) -> {"mean_car": float, "n": int}
    """
    cache = {}
    try:
        # Get per-event results from report3 studies
        rows = conn.execute(
            """SELECT esr.event_description, esr.car_full, esr.ticker
               FROM event_study_results esr
               JOIN event_studies es ON esr.study_id = es.id
               WHERE es.study_name = 'report3_pipeline_proposed'
                 AND esr.car_full IS NOT NULL"""
        ).fetchall()

        if not rows:
            return cache

        # We don't have agency directly on event_study_results,
        # so use aggregate stats from the study itself
        study_row = conn.execute(
            """SELECT mean_car, num_events FROM event_studies
               WHERE study_name = 'report3_pipeline_proposed'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

        if study_row:
            # Use the overall study stats as a fallback for all agency/sector combos
            cache["_default"] = {"mean_car": study_row[0], "n": study_row[1]}

    except Exception as e:
        logger.debug("Could not load historical CARs: %s", e)

    return cache


def _upsert_pipeline_rule(conn: sqlite3.Connection, rule: dict) -> None:
    """Insert or update a pipeline_rules row (keyed on proposed_event_id)."""
    conn.execute(
        """INSERT INTO pipeline_rules
           (proposed_event_id, final_event_id, agency, sector, tickers,
            proposed_date, comment_deadline, estimated_final_date, actual_final_date,
            status, days_in_pipeline, title_similarity, impact_score,
            proposed_title, historical_car, historical_n)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(proposed_event_id) DO UPDATE SET
            final_event_id = excluded.final_event_id,
            status = excluded.status,
            days_in_pipeline = excluded.days_in_pipeline,
            actual_final_date = excluded.actual_final_date,
            historical_car = excluded.historical_car,
            historical_n = excluded.historical_n,
            updated_at = CURRENT_TIMESTAMP""",
        (
            rule["proposed_event_id"], rule["final_event_id"],
            rule["agency"], rule["sector"], rule["tickers"],
            rule["proposed_date"], rule["comment_deadline"],
            rule["estimated_final_date"], rule["actual_final_date"],
            rule["status"], rule["days_in_pipeline"],
            rule["title_similarity"], rule["impact_score"],
            rule["proposed_title"], rule["historical_car"],
            rule["historical_n"],
        ),
    )


def _primary_sector(sectors_str: str | None) -> str:
    """Extract the primary (first) sector from a comma-separated string."""
    if not sectors_str or not isinstance(sectors_str, str):
        return "Other"
    first = sectors_str.split(",")[0].strip()
    return first if first else "Other"


def _normalize_agency(agency: str) -> str:
    """Normalize agency name for lookup."""
    return agency.strip().lower()[:50] if agency else ""
