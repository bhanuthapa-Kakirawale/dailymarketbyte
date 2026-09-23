"""`radar_candidate_history.db` repository (Phase 4.2 Packet 5.4D). All SQL for this database
lives in this one module, matching every other store's convention in this package.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

from .candidate_history_migrations import (CandidateHistorySchemaVersionError, current_version,
                                           initialise)
from .candidate_history_models import RunStatus, StoredCandidateState, StoredRunMarker

DEFAULT_DB_RELPATH = os.path.join("data", "radar_candidate_history.db")


def default_db_path(out_dir: str) -> str:
    return os.path.join(out_dir, DEFAULT_DB_RELPATH)


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value)


def _dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _loads(text):
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


class CandidateHistoryStore:
    """Repository over `radar_candidate_history`. Open one, use it, close it (or context manager)."""

    def __init__(self, db_path: str, timeout: float = 30.0):
        self.db_path = db_path
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=timeout)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        self.schema_version = initialise(self.conn)
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ writing
    def save_candidates(self, session_date: dt.date, states: list) -> int:
        """Idempotent write, keyed on `(session_date, instrument)`: a rerun for a session
        already persisted writes nothing new (packet spec sections 7/22). `states`: an iterable
        of `StoredCandidateState`. Returns the number of NEW rows actually inserted."""
        if not states:
            return 0
        now = _iso(dt.datetime.now(dt.timezone.utc))
        inserted = 0
        with self.conn:
            for s in states:
                cur = self.conn.execute(
                    """INSERT INTO radar_candidate_history
                           (session_date, instrument, active_families_json, reason_codes_json,
                            independent_signal_count, direction_compatibility, attention_level,
                            persistence_state, price_change_pct, calculation_version, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(session_date, instrument) DO NOTHING""",
                    (_iso(session_date), s.instrument, _dumps(list(s.active_families)),
                     _dumps(list(s.reason_codes)), s.independent_signal_count,
                     s.direction_compatibility, s.attention_level, s.persistence_state,
                     s.price_change_pct, s.calculation_version, now))
                inserted += cur.rowcount
        return inserted

    # ------------------------------------------------------------------ run markers (Packet 5.4E)
    def mark_run(self, session_date: dt.date, calculation_version: str, status: str,
                candidate_count: int, processed_at: dt.datetime | None = None) -> None:
        """Upsert the `candidate_history_runs` row for `(session_date, calculation_version)`.

        Unlike `save_candidates`, this is a REPLACE, not an insert-and-ignore: a session that
        was previously `FAILED` must be updatable to `COMPLETE` on a successful retry (packet
        spec section 14 - "already COMPLETE sessions should normally be skipped... FAILED/
        incomplete session retried"), and a caller marking `FAILED` after a `COMPLETE` row
        would be a bug in the caller, not something this method tries to prevent - it simply
        records whatever the caller reports.
        """
        processed_at = processed_at or dt.datetime.now(dt.timezone.utc)
        with self.conn:
            self.conn.execute(
                """INSERT INTO candidate_history_runs
                       (session_date, calculation_version, status, candidate_count, processed_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(session_date, calculation_version) DO UPDATE SET
                       status=excluded.status, candidate_count=excluded.candidate_count,
                       processed_at=excluded.processed_at""",
                (_iso(session_date), calculation_version,
                 status.value if isinstance(status, RunStatus) else status,
                 candidate_count, _iso(processed_at)))

    def get_run_status(self, session_date: dt.date, calculation_version: str) -> StoredRunMarker | None:
        row = self.conn.execute(
            "SELECT * FROM candidate_history_runs WHERE session_date = ? AND calculation_version = ?",
            (_iso(session_date), calculation_version)).fetchone()
        if row is None:
            return None
        return StoredRunMarker(
            session_date=_parse_date(row["session_date"]),
            calculation_version=row["calculation_version"], status=row["status"],
            candidate_count=row["candidate_count"],
            processed_at=dt.datetime.fromisoformat(row["processed_at"]))

    def is_session_complete(self, session_date: dt.date, calculation_version: str) -> bool:
        marker = self.get_run_status(session_date, calculation_version)
        return marker is not None and marker.status == RunStatus.COMPLETE.value

    def get_complete_sessions(self, calculation_version: str, *, before: dt.date | None = None,
                              after: dt.date | None = None) -> set:
        """Every `session_date` with a `COMPLETE` row under `calculation_version` - one bounded
        query, the input `radar.candidate_history_backfill.find_missing_candidate_sessions`
        needs (packet spec section 10), never one lookup per candidate spine session."""
        sql = ("SELECT session_date FROM candidate_history_runs "
              "WHERE calculation_version = ? AND status = ?")
        params: list = [calculation_version, RunStatus.COMPLETE.value]
        if before is not None:
            sql += " AND session_date < ?"
            params.append(_iso(before))
        if after is not None:
            sql += " AND session_date > ?"
            params.append(_iso(after))
        rows = self.conn.execute(sql, params).fetchall()
        return {_parse_date(r["session_date"]) for r in rows}

    # ------------------------------------------------------------------ reading
    def get_prior_candidates(self, before_session_date: dt.date, spine: list,
                             lookback_sessions: int) -> list:
        """Every candidate-history row from the `lookback_sessions` valid trading sessions
        STRICTLY BEFORE `before_session_date` - ONE bounded query (packet spec section 25),
        grouped by session date and returned oldest-first as
        `[(session_date, [StoredCandidateState, ...]), ...]` - the exact shape
        `radar.novelty.classify_history` expects for its `sessions` argument (minus the
        current session, which the caller appends itself).

        `spine` translates "N valid trading sessions" into a concrete date lower bound via
        SPINE POSITIONS, never calendar-day subtraction - identical convention to
        `EditorialStore.get_prior_selections`.
        """
        prior_spine = [d for d in spine if d < before_session_date]
        window_dates = prior_spine[-lookback_sessions:] if prior_spine else []
        if not window_dates:
            return []
        window_start = window_dates[0]

        rows = self.conn.execute(
            "SELECT * FROM radar_candidate_history WHERE session_date >= ? AND session_date < ? "
            "ORDER BY session_date ASC, instrument ASC",
            (_iso(window_start), _iso(before_session_date))).fetchall()

        by_date: dict = {}
        for row in rows:
            d = _parse_date(row["session_date"])
            by_date.setdefault(d, []).append(self._row(row))

        return [(d, by_date.get(d, [])) for d in window_dates]

    def get_session_candidates(self, session_date: dt.date) -> list:
        rows = self.conn.execute(
            "SELECT * FROM radar_candidate_history WHERE session_date = ? ORDER BY instrument",
            (_iso(session_date),)).fetchall()
        return [self._row(r) for r in rows]

    def count_rows(self) -> int:
        return int(self.conn.execute("SELECT count(*) FROM radar_candidate_history").fetchone()[0])

    @staticmethod
    def _row(row) -> StoredCandidateState:
        return StoredCandidateState(
            session_date=_parse_date(row["session_date"]), instrument=row["instrument"],
            active_families=tuple(_loads(row["active_families_json"])),
            reason_codes=tuple(_loads(row["reason_codes_json"])),
            independent_signal_count=row["independent_signal_count"],
            direction_compatibility=row["direction_compatibility"],
            attention_level=row["attention_level"], persistence_state=row["persistence_state"],
            price_change_pct=row["price_change_pct"],
            calculation_version=row["calculation_version"],
            created_at=(dt.datetime.fromisoformat(row["created_at"]) if row["created_at"] else None))


__all__ = ["CandidateHistoryStore", "default_db_path", "DEFAULT_DB_RELPATH",
          "CandidateHistorySchemaVersionError", "current_version", "RunStatus", "StoredRunMarker"]
