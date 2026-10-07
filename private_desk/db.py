"""The ONE way the desk opens a SQLite database: read-only, sidecar-free where possible.

`storage.readonly.connect_readonly` opens `mode=ro&immutable=1` when no WAL is pending (creates
nothing next to the file) and plain `mode=ro` when a writer's WAL already exists. On top of
that every connection sets `PRAGMA query_only = ON`, so even a programming mistake that issues
an INSERT/UPDATE fails inside SQLite instead of reaching a production file.

A fresh connection per request (`ro_connection` context manager): under `immutable=1` SQLite
assumes the file cannot change, so a long-lived connection would keep serving a stale view after
the evening job writes. A missing file is never created - it raises `SourceUnavailable`.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from storage.readonly import connect_readonly

DB_FILES = {
    "ohlcv": os.path.join("data", "market_ohlcv.db"),
    "candidate_history": os.path.join("data", "radar_candidate_history.db"),
    "editorial": os.path.join("data", "editorial_selections.db"),
    "market_history": os.path.join("data", "market_history.db"),
}


class SourceUnavailable(Exception):
    """A store the desk wanted to read does not exist or cannot be read."""


def db_path(out_dir: str, name: str) -> str:
    return os.path.join(out_dir, DB_FILES[name])


def open_ro(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise SourceUnavailable(f"database not found: {os.path.basename(path)}")
    try:
        conn = connect_readonly(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        raise SourceUnavailable(f"{os.path.basename(path)}: {exc}") from exc
    return conn


@contextmanager
def ro_connection(out_dir: str, name: str):
    conn = open_ro(db_path(out_dir, name))
    try:
        yield conn
    except sqlite3.Error as exc:       # missing table, locked/rewritten file: unavailable, not a crash
        raise SourceUnavailable(f"{DB_FILES[name]}: {exc}") from exc
    finally:
        conn.close()


__all__ = ["open_ro", "ro_connection", "db_path", "DB_FILES", "SourceUnavailable"]
