"""Schema versioning via SQLite's built-in PRAGMA user_version.

Deliberately minimal: one integer, a list of upgrade steps, and a refusal to run against a
database newer than the code understands. A migration framework would be more machinery than
a single-file local index can justify, but silently writing into a schema written by a newer
version of this application would corrupt history quietly, which is the one outcome worth
real code to prevent.
"""
from __future__ import annotations

import sqlite3

from .schema import SCHEMA_SQL, SCHEMA_VERSION


class SchemaVersionError(RuntimeError):
    """The database on disk is not a schema this build can safely use."""


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA does not accept bound parameters; version is an int we control, never user input.
    conn.execute(f"PRAGMA user_version = {int(version)}")


# Upgrade steps, applied in order for any database below SCHEMA_VERSION. Each entry is
# (target_version, callable). Empty today because version 1 is the first schema; the
# mechanism exists so the first real migration is a data change, not an architecture change.
MIGRATIONS: list = []


def initialise(conn: sqlite3.Connection) -> int:
    """Create or upgrade the schema, returning the version now on disk.

    A fresh database (user_version 0 and no tables) is created at SCHEMA_VERSION. A database
    from a newer build is refused rather than written to.
    """
    version = current_version(conn)

    if version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema v{version} is newer than this build supports (v{SCHEMA_VERSION}); "
            "refusing to write to it - upgrade the application rather than downgrading history")

    if version == 0 and not _has_tables(conn):
        conn.executescript(SCHEMA_SQL)
        _set_version(conn, SCHEMA_VERSION)
        return SCHEMA_VERSION

    if version == 0:
        # Tables exist but no version stamp: a database created before versioning existed.
        # Treat it as version 1 rather than re-creating anything.
        _set_version(conn, 1)
        version = 1

    for target, step in MIGRATIONS:
        if version < target:
            step(conn)
            _set_version(conn, target)
            version = target

    # CREATE TABLE IF NOT EXISTS, so this adds anything a partial older build missed without
    # touching existing rows.
    conn.executescript(SCHEMA_SQL)
    return version


def _has_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='reports'").fetchone()
    return bool(row and row[0])


__all__ = ["initialise", "current_version", "SchemaVersionError", "SCHEMA_VERSION", "MIGRATIONS"]
