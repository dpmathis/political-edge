"""Tests for dashboard/collection_logger.py — shared collection logging."""

import sqlite3

import pytest

from dashboard.collection_logger import log_collection_step


def _get_log_row(conn, log_id):
    """Fetch a data_collection_log row by id."""
    return conn.execute(
        "SELECT collector_name, status, records_added, errors, completed_at "
        "FROM data_collection_log WHERE id = ?",
        (log_id,),
    ).fetchone()


class TestLogCollectionStep:
    """Tests for log_collection_step."""

    def test_successful_collection_logged(self, db_path):
        conn = sqlite3.connect(db_path)
        result = log_collection_step(conn, "test_collector", lambda: 42)
        assert result == 42
        row = conn.execute(
            "SELECT status, records_added, completed_at FROM data_collection_log "
            "WHERE collector_name = 'test_collector' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row[0] == "success"
        assert row[1] == 42
        assert row[2] is not None  # completed_at set

    def test_failed_collection_logged_with_error(self, db_path):
        conn = sqlite3.connect(db_path)
        with pytest.raises(ValueError, match="boom"):
            log_collection_step(conn, "fail_collector", _raise_error)
        row = conn.execute(
            "SELECT status, errors, completed_at FROM data_collection_log "
            "WHERE collector_name = 'fail_collector' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row[0] == "error"
        assert "boom" in row[1]
        assert row[2] is not None

    def test_records_count_from_int_return(self, db_path):
        conn = sqlite3.connect(db_path)
        log_collection_step(conn, "int_collector", lambda: 99)
        row = conn.execute(
            "SELECT records_added FROM data_collection_log "
            "WHERE collector_name = 'int_collector'"
        ).fetchone()
        conn.close()
        assert row[0] == 99

    def test_non_int_return_records_zero(self, db_path):
        conn = sqlite3.connect(db_path)
        log_collection_step(conn, "dict_collector", lambda: {"status": "ok"})
        row = conn.execute(
            "SELECT records_added FROM data_collection_log "
            "WHERE collector_name = 'dict_collector'"
        ).fetchone()
        conn.close()
        assert row[0] == 0

    def test_error_message_truncated(self, db_path):
        conn = sqlite3.connect(db_path)
        long_msg = "x" * 1000

        def _raise_long():
            raise RuntimeError(long_msg)

        with pytest.raises(RuntimeError):
            log_collection_step(conn, "long_error", _raise_long)
        row = conn.execute(
            "SELECT errors FROM data_collection_log WHERE collector_name = 'long_error'"
        ).fetchone()
        conn.close()
        assert len(row[0]) <= 500

    def test_status_starts_running(self, db_path):
        """Verify the initial INSERT sets status='running'."""
        conn = sqlite3.connect(db_path)
        # Use a function that lets us check mid-flight
        captured = {}

        def _check_running():
            row = conn.execute(
                "SELECT status FROM data_collection_log "
                "WHERE collector_name = 'running_check' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            captured["status"] = row[0]
            return 0

        log_collection_step(conn, "running_check", _check_running)
        conn.close()
        assert captured["status"] == "running"

    def test_passes_args_and_kwargs(self, db_path):
        conn = sqlite3.connect(db_path)

        def _adder(a, b, multiplier=1):
            return (a + b) * multiplier

        result = log_collection_step(conn, "args_test", _adder, 3, 4, multiplier=2)
        conn.close()
        assert result == 14


def _raise_error():
    raise ValueError("boom")
