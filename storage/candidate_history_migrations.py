"""Schema versioning for `radar_candidate_history.db`, via SQLite's `PRAGMA user_version`.

Same minimal approach as `storage/migrations.py`, `storage/ohlcv_migrations.py` and
`storage/editorial_migrations.py` - deliberately not shared code, since all these databases
must be free to evolve on independent schedules.
"""
from __future__ import annotations

import sqlite3

from .candidate_history_schema import SCHEMA_SQL, SCHEMA_VERSION


class CandidateHistorySchemaVersionError(RuntimeError):
    """The candidate-history database on disk is not a schema this build can safely use."""


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add `candidate_history_runs` (Phase 4.2 Packet 5.4E) - idempotent (`CREATE TABLE IF NOT
    EXISTS`), so re-running this against a database that already has the table (e.g. one
    created fresh already at v2) is a no-op."""
    conn.executescript(SCHEMA_SQL)


MIGRATIONS: list = [(2, _migrate_v1_to_v2)]


def initialise(conn: sqlite3.Connection) -> int:
    version = current_version(conn)

    if version > SCHEMA_VERSION:
        raise CandidateHistorySchemaVersionError(
            f"candidate-history database schema v{version} is newer than this build supports "
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
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND "
        "name='radar_candidate_history'").fetchone()
    return bool(row and row[0])


__all__ = ["initialise", "current_version", "CandidateHistorySchemaVersionError",
          "SCHEMA_VERSION", "MIGRATIONS"]
