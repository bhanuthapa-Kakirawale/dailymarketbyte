"""Read a SQLite database WITHOUT creating anything next to it.

The history databases use WAL journaling. In WAL mode even a `mode=ro` connection creates the
`-wal` / `-shm` sidecar files when they are absent - a write into the directory being read. Two
places must never do that to production state: the isolated test runs (docs/TESTING_GUIDE.md)
and the read-only inspection tools (cleanup, checks).

    connect_readonly(path)   no pending WAL -> `immutable=1` (exact, creates nothing);
                             a non-empty WAL -> plain `mode=ro` (its sidecars already exist)
    copy_database(src, dst)  byte copy of the database AND any existing sidecars - no SQLite
                             connection to the source at all; SQLite replays the WAL in the copy
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from urllib.parse import quote


def _uri(path: str, params: str) -> str:
    return f"file:{quote(os.path.abspath(path).replace(os.sep, '/'), safe='/:')}?{params}"


def connect_readonly(path: str) -> sqlite3.Connection:
    wal = path + "-wal"
    if os.path.exists(wal) and os.path.getsize(wal) > 0:
        return sqlite3.connect(_uri(path, "mode=ro"), uri=True)
    return sqlite3.connect(_uri(path, "mode=ro&immutable=1"), uri=True)


def copy_database(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, dst + suffix)


__all__ = ["connect_readonly", "copy_database"]
