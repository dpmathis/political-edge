"""Shared collection logging — writes collector run results to data_collection_log.

Used by both dashboard/pages/8_Settings.py and scripts/run_collectors.py
to record collector execution status, timing, and record counts.
"""

import sqlite3


def log_collection_step(conn: sqlite3.Connection, collector_name: str, func, *args, **kwargs):
    """Run a collector function and log the result to data_collection_log.

    Args:
        conn: DB connection for logging (separate from collector's own connection).
        collector_name: Name to record in the log table.
        func: Collector function to call.
        *args, **kwargs: Passed through to func.

    Returns:
        Whatever func returns.

    Raises:
        Re-raises any exception from func after logging the error.
    """
    conn.execute(
        "INSERT INTO data_collection_log (collector_name, status, started_at) "
        "VALUES (?, 'running', CURRENT_TIMESTAMP)",
        (collector_name,),
    )
    log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    try:
        result = func(*args, **kwargs)
        records = result if isinstance(result, int) else 0
        conn.execute(
            "UPDATE data_collection_log SET status = 'success', records_added = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (records, log_id),
        )
        conn.commit()
        return result
    except Exception as e:
        conn.execute(
            "UPDATE data_collection_log SET status = 'error', errors = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(e)[:500], log_id),
        )
        conn.commit()
        raise
