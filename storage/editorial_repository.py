"""`editorial_selections.db` repository (Phase 4.2 Packet 5.4C). All SQL for this database lives
in this one module, matching `storage/repository.py`/`storage/ohlcv_repository.py`'s convention.

Selector code (`radar.editorial_selector`) depends on this small API, never on raw SQL - see
`save_selections`, `get_prior_selections`, `get_session_selections`, `update_publication_state`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

from .editorial_migrations import EditorialSchemaVersionError, current_version, initialise
from .editorial_models import EditorialSelection

DEFAULT_DB_RELPATH = os.path.join("data", "editorial_selections.db")


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
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


class EditorialStore:
    """Repository over `editorial_selections`. Open one, use it, close it (or context manager)."""

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
    def save_selections(self, selections: list) -> int:
        """Idempotent write: a `selection_id` already on disk is left exactly as it is - a
        rerun against identical inputs (same session, same candidates, same publication
        history, same selector version) produces the SAME selection_ids and writes nothing new
        (packet spec sections 17/18/21). Returns the number of NEW rows actually inserted.
        """
        if not selections:
            return 0
        inserted = 0
        with self.conn:
            for s in selections:
                cur = self.conn.execute(
                    """INSERT INTO editorial_selections
                           (selection_id, session_date, instrument, novelty_type,
                            active_families_json, independent_signal_count,
                            direction_compatibility, attention_level, selection_bucket,
                            reserved_3family, diversity_role, cooldown_status,
                            cooldown_override_reason, reason_codes_json, selection_reason,
                            selector_version, lifecycle_state, selected_at, updated_at,
                            metadata_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(selection_id) DO NOTHING""",
                    (s.selection_id, _iso(s.session_date), s.instrument, s.novelty_type,
                     _dumps(list(s.active_families)), s.independent_signal_count,
                     s.direction_compatibility, s.attention_level, s.selection_bucket,
                     int(s.reserved_3family), s.diversity_role, s.cooldown_status,
                     s.cooldown_override_reason, _dumps(list(s.reason_codes)),
                     s.selection_reason, s.selector_version, s.lifecycle_state,
                     _iso(s.selected_at), _iso(s.updated_at), _dumps(s.metadata)))
                inserted += cur.rowcount
        return inserted

    def update_publication_state(self, selection_id: str, state: str,
                                 updated_at: dt.datetime | None = None) -> bool:
        """In-place lifecycle transition on an ALREADY-written selection (packet spec section
        15) - never a new row, never a change to why/what was selected. Returns False if no
        row with that id exists."""
        updated_at = updated_at or dt.datetime.now(dt.timezone.utc)
        with self.conn:
            cur = self.conn.execute(
                "UPDATE editorial_selections SET lifecycle_state = ?, updated_at = ? "
                "WHERE selection_id = ?", (state, _iso(updated_at), selection_id))
        return cur.rowcount > 0

    # ------------------------------------------------------------------ reading
    def get_prior_selections(self, before_session_date: dt.date, spine: list,
                             lookback_sessions: int) -> dict:
        """`{instrument: last_selected_session_date}` for every instrument selected on any of
        the `lookback_sessions` valid trading sessions STRICTLY BEFORE `before_session_date` -
        ONE bounded query, never one per candidate (packet spec section 21).

        `spine` is the canonical trading-session spine (sorted dates); the cooldown WINDOW is
        translated from "N valid trading sessions" into a concrete date lower bound using spine
        POSITIONS, never calendar-day subtraction, matching every other cooldown-aware module in
        this codebase (`radar.editorial_policy`/`radar.editorial_policy_hybrid`). If
        `before_session_date` is not itself on the spine, or fewer than `lookback_sessions`
        prior spine dates exist, the window simply starts at the earliest available spine date -
        never a fabricated one.
        """
        prior_spine = [d for d in spine if d < before_session_date]
        window_start = prior_spine[-lookback_sessions] if len(prior_spine) >= lookback_sessions \
            else (prior_spine[0] if prior_spine else before_session_date)

        rows = self.conn.execute(
            "SELECT instrument, session_date FROM editorial_selections "
            "WHERE session_date >= ? AND session_date < ? "
            "ORDER BY session_date ASC",
            (_iso(window_start), _iso(before_session_date))).fetchall()

        out: dict = {}
        for row in rows:
            # ASC order means a later row for the same instrument overwrites - "most recent"
            # prior selection wins, exactly like `radar.editorial_policy`'s in-memory history.
            out[row["instrument"]] = _parse_date(row["session_date"])
        return out

    def get_session_selections(self, session_date: dt.date,
                               selector_version: str | None = None) -> list:
        sql = "SELECT * FROM editorial_selections WHERE session_date = ?"
        params: list = [_iso(session_date)]
        if selector_version:
            sql += " AND selector_version = ?"
            params.append(selector_version)
        sql += " ORDER BY instrument"
        return [self._row(r) for r in self.conn.execute(sql, params).fetchall()]

    def selection_exists(self, selection_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM editorial_selections WHERE selection_id = ?",
                                (selection_id,)).fetchone()
        return row is not None

    @staticmethod
    def _row(row) -> EditorialSelection:
        return EditorialSelection(
            selection_id=row["selection_id"], session_date=_parse_date(row["session_date"]),
            instrument=row["instrument"], novelty_type=row["novelty_type"],
            active_families=tuple(_loads(row["active_families_json"]) or []),
            independent_signal_count=row["independent_signal_count"],
            direction_compatibility=row["direction_compatibility"],
            attention_level=row["attention_level"], selection_bucket=row["selection_bucket"],
            reserved_3family=bool(row["reserved_3family"]), diversity_role=row["diversity_role"],
            cooldown_status=row["cooldown_status"],
            cooldown_override_reason=row["cooldown_override_reason"],
            reason_codes=tuple(_loads(row["reason_codes_json"]) or []),
            selection_reason=row["selection_reason"], selector_version=row["selector_version"],
            lifecycle_state=row["lifecycle_state"],
            selected_at=(dt.datetime.fromisoformat(row["selected_at"]) if row["selected_at"] else None),
            updated_at=(dt.datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None),
            metadata=_loads(row["metadata_json"]))


__all__ = ["EditorialStore", "default_db_path", "DEFAULT_DB_RELPATH",
          "EditorialSchemaVersionError", "current_version"]
