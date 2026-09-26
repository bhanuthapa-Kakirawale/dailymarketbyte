"""Schema versioning for `editorial_selections.db`, via SQLite's `PRAGMA user_version`.

Same minimal approach as `storage/migrations.py` and `storage/ohlcv_migrations.py` (a single
integer, a list of upgrade steps, a refusal to run against a database newer than the code
understands) applied to this third, independent schema - deliberately not shared code, since
all three databases must be free to evolve on independent schedules.
"""
from __future__ import annotations

import sqlite3

from .editorial_schema import SCHEMA_SQL, SCHEMA_VERSION


class EditorialSchemaVersionError(RuntimeError):
    """The editorial-selections database on disk is not a schema this build can safely use."""


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _v2_radar_publications(conn: sqlite3.Connection) -> None:
    """v1 -> v2: add the RADAR_PUBLISHED ledger. Additive only - existing selection rows keep
    their lifecycle_state; nothing is backfilled (no v1 selection was ever confirmed as
    published, so none is published)."""
    conn.executescript(SCHEMA_SQL)


# Upgrade steps, applied in order for any database below SCHEMA_VERSION - see
# storage/migrations.py for the same convention.
MIGRATIONS: list = [(2, _v2_radar_publications)]


def initialise(conn: sqlite3.Connection) -> int:
    """Create or upgrade the schema, returning the version now on disk."""
    version = current_version(conn)

    if version > SCHEMA_VERSION:
        raise EditorialSchemaVersionError(
            f"editorial-selections database schema v{version} is newer than this build "
            f"supports (v{SCHEMA_VERSION}); refusing to write to it")

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
        "name='editorial_selections'").fetchone()
    return bool(row and row[0])


__all__ = ["initialise", "current_version", "EditorialSchemaVersionError", "SCHEMA_VERSION",
          "MIGRATIONS"]
