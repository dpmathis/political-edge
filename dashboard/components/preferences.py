"""User preferences — simple key-value store backed by SQLite."""

import json
import sqlite3

from config import DB_PATH


def get_pref(key: str, default: str = "") -> str:
    """Get a preference value by key."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT value FROM user_preferences WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def set_pref(key: str, value: str):
    """Set a preference value (insert or replace)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_pref_json(key: str, default=None):
    """Get a JSON-serialized preference."""
    raw = get_pref(key, "")
    if not raw:
        return default if default is not None else {}
    return json.loads(raw)


def set_pref_json(key: str, value):
    """Set a JSON-serialized preference."""
    set_pref(key, json.dumps(value))
