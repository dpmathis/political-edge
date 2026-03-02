#!/usr/bin/env python3
"""Pipeline migration — backfill dates from raw_json and create pipeline_rules table."""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH

PIPELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_event_id INTEGER NOT NULL,
    final_event_id INTEGER,
    agency TEXT,
    sector TEXT,
    tickers TEXT,
    proposed_date DATE,
    comment_deadline DATE,
    estimated_final_date DATE,
    actual_final_date DATE,
    status TEXT DEFAULT 'proposed',
    days_in_pipeline INTEGER,
    title_similarity REAL,
    impact_score INTEGER DEFAULT 0,
    proposed_title TEXT,
    historical_car REAL,
    historical_n INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proposed_event_id) REFERENCES regulatory_events(id),
    FOREIGN KEY (final_event_id) REFERENCES regulatory_events(id),
    UNIQUE(proposed_event_id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_status ON pipeline_rules(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_sector ON pipeline_rules(sector);
CREATE INDEX IF NOT EXISTS idx_pipeline_proposed ON pipeline_rules(proposed_event_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_deadline ON pipeline_rules(comment_deadline);
CREATE INDEX IF NOT EXISTS idx_pipeline_proposed_date ON pipeline_rules(proposed_date);
"""


def backfill_dates_from_raw_json(conn: sqlite3.Connection) -> dict:
    """Parse raw_json to populate effective_date and comment_deadline."""
    rows = conn.execute(
        """SELECT id, raw_json, effective_date, comment_deadline
           FROM regulatory_events
           WHERE raw_json IS NOT NULL"""
    ).fetchall()

    effective_count = 0
    comment_count = 0

    for row_id, raw_json_str, existing_eff, existing_comment in rows:
        try:
            doc = json.loads(raw_json_str)
        except (json.JSONDecodeError, TypeError):
            continue

        updates = []
        params = []

        # Backfill effective_date
        if not existing_eff:
            effective = doc.get("effective_on")
            if effective:
                updates.append("effective_date = ?")
                params.append(effective)
                effective_count += 1

        # Backfill comment_deadline
        if not existing_comment:
            comment_close = doc.get("comments_close_on")
            # Also check Regulations.gov JSON:API format
            if not comment_close:
                attrs = doc.get("attributes", {})
                if isinstance(attrs, dict):
                    comment_close = attrs.get("commentEndDate", "")
                    if comment_close and "T" in comment_close:
                        comment_close = comment_close.split("T")[0]
            if comment_close:
                updates.append("comment_deadline = ?")
                params.append(comment_close)
                comment_count += 1

        if updates:
            params.append(row_id)
            conn.execute(
                f"UPDATE regulatory_events SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    conn.commit()
    return {"effective_date_backfilled": effective_count, "comment_deadline_backfilled": comment_count}


def create_pipeline_table(conn: sqlite3.Connection) -> None:
    """Create pipeline_rules table and indexes."""
    conn.executescript(PIPELINE_SCHEMA)
    conn.commit()
    print("Created pipeline_rules table")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # Step 1: Create pipeline_rules table
    create_pipeline_table(conn)

    # Step 2: Backfill dates from raw_json
    print("Backfilling dates from raw_json...")
    result = backfill_dates_from_raw_json(conn)
    print(f"  effective_date: {result['effective_date_backfilled']} rows updated")
    print(f"  comment_deadline: {result['comment_deadline_backfilled']} rows updated")

    # Verify
    stats = conn.execute(
        """SELECT
             SUM(CASE WHEN effective_date IS NOT NULL THEN 1 ELSE 0 END) as has_effective,
             SUM(CASE WHEN comment_deadline IS NOT NULL THEN 1 ELSE 0 END) as has_comment,
             COUNT(*) as total
           FROM regulatory_events"""
    ).fetchone()
    print("\nPost-migration stats:")
    print(f"  effective_date populated: {stats[0]}/{stats[2]}")
    print(f"  comment_deadline populated: {stats[1]}/{stats[2]}")

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
