"""Schema versioning for `market_ohlcv.db`, via SQLite's `PRAGMA user_version`.

Same minimal approach as `storage/migrations.py` (a single integer, a list of upgrade steps, a
refusal to run against a database newer than the code understands) applied to the separate
OHLCV schema - deliberately not shared code with `storage/migrations.py` since the two
databases are independent and must be free to evolve on independent schedules.
"""
from __future__ import annotations

import sqlite3

from .ohlcv_schema import SCHEMA_SQL, SCHEMA_VERSION


class OhlcvSchemaVersionError(RuntimeError):
    """The OHLCV database on disk is not a schema this build can safely use."""


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


# Upgrade steps, applied in order for any database below SCHEMA_VERSION. Empty today because
# version 1 is the first schema - see storage/migrations.py for the same convention.
MIGRATIONS: list = []


def initialise(conn: sqlite3.Connection) -> int:
    """Create or upgrade the schema, returning the version now on disk."""
    version = current_version(conn)

    if version > SCHEMA_VERSION:
        raise OhlcvSchemaVersionError(
            f"OHLCV database schema v{version} is newer than this build supports "
            f"(v{SCHEMA_VERSION}); refusing to write to it")

    if version == 0 and not _has_tables(conn):
        conn.executescript(SCHEMA_SQL)
        _set_version(conn, SCHEMA_VERSION)
        return SCHEMA_VERSION

    if version == 0:
        _set_version(conn, 1)
        version = 1

    for target, step in MIGRATIONS:
        if version < target:
            step(conn)
            _set_version(conn, target)
            version = target

    conn.executescript(SCHEMA_SQL)
    return version


def _has_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='daily_ohlcv'").fetchone()
    return bool(row and row[0])


__all__ = ["initialise", "current_version", "OhlcvSchemaVersionError", "SCHEMA_VERSION",
           "MIGRATIONS"]
