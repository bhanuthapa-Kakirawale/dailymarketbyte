"""DeskRepository: every read the desk makes, behind one stable interface.

Hides the storage layout (four SQLite stores + per-session JSON artifacts) from the services
and templates. Read-only by construction: SQLite only through `private_desk.db`, JSON only
through `open(..., encoding=...)` in read mode, the official snapshots only through
`official_snapshots.store`'s read functions (`load_manifest` / `load_current`, which also verify
the checksum). Nothing here constructs a `*Store` class (their constructors migrate and write)
and nothing here fetches.

Missing data is `None` / `[]` with the reason left to the caller - never a zero, never an older
session substituted for the one asked for.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sqlite3

from storage.candidate_history_models import StoredCandidateState

from .db import SourceUnavailable, db_path, ro_connection

BENCHMARK_SYMBOL = "^NSEI"
OHLCV_SOURCE = "yahoo"
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
OFFICIAL_KINDS = ("FNO_BAN", "ASM", "GSM", "IPO")


def _d(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _candidate(row) -> StoredCandidateState:
    return StoredCandidateState(
        session_date=_d(row["session_date"]), instrument=row["instrument"],
        active_families=tuple(_loads(row["active_families_json"], [])),
        reason_codes=tuple(_loads(row["reason_codes_json"], [])),
        independent_signal_count=row["independent_signal_count"],
        direction_compatibility=row["direction_compatibility"],
        attention_level=row["attention_level"], persistence_state=row["persistence_state"],
        price_change_pct=row["price_change_pct"], calculation_version=row["calculation_version"],
        created_at=None)


class DeskRepository:
    def __init__(self, out_dir: str):
        self.out_dir = out_dir

    # ------------------------------------------------------------------ paths
    def path(self, *parts) -> str:
        return os.path.join(self.out_dir, *parts)

    def db_file(self, name: str) -> str:
        return db_path(self.out_dir, name)

    def _dated_files(self, pattern: str) -> dict:
        out = {}
        for p in glob.glob(self.path(pattern)):
            name = os.path.basename(p)
            # ".rev" = an immutable Market Structure point-in-time revision sibling
            # (market_structure.store), never the live, always-current file this reads.
            if "_DEMO" in name or ".rev" in name:
                continue
            m = _DATE_RE.search(name)
            if m:
                out[_d(m.group(1))] = p
        return out

    # ------------------------------------------------------------------ Radar candidate history
    def radar_run_markers(self) -> list:
        """`candidate_history_runs`, oldest first: the authoritative "this session was run"."""
        with ro_connection(self.out_dir, "candidate_history") as c:
            rows = c.execute("SELECT * FROM candidate_history_runs "
                             "ORDER BY session_date ASC, calculation_version ASC").fetchall()
        return [dict(r) for r in rows]

    def radar_sessions(self) -> list:
        """Sessions with a COMPLETE candidate-history run, oldest first."""
        return sorted({_d(m["session_date"]) for m in self.radar_run_markers()
                       if m["status"] == "COMPLETE"})

    def candidates(self, session: dt.date) -> list:
        with ro_connection(self.out_dir, "candidate_history") as c:
            rows = c.execute("SELECT * FROM radar_candidate_history WHERE session_date = ? "
                             "ORDER BY instrument ASC", (session.isoformat(),)).fetchall()
        return [_candidate(r) for r in rows]

    def candidates_between(self, start: dt.date, end: dt.date) -> list:
        with ro_connection(self.out_dir, "candidate_history") as c:
            rows = c.execute("SELECT * FROM radar_candidate_history WHERE session_date >= ? "
                             "AND session_date <= ? ORDER BY session_date ASC, instrument ASC",
                             (start.isoformat(), end.isoformat())).fetchall()
        return [_candidate(r) for r in rows]

    def symbol_candidate_history(self, symbol: str) -> list:
        with ro_connection(self.out_dir, "candidate_history") as c:
            rows = c.execute("SELECT * FROM radar_candidate_history WHERE instrument = ? "
                             "ORDER BY session_date ASC", (symbol,)).fetchall()
        return [_candidate(r) for r in rows]

    def appearance_counts(self) -> dict:
        with ro_connection(self.out_dir, "candidate_history") as c:
            rows = c.execute("SELECT instrument, COUNT(*) n, MIN(session_date) first, "
                             "MAX(session_date) last FROM radar_candidate_history "
                             "GROUP BY instrument").fetchall()
        return {r["instrument"]: {"count": r["n"], "first": _d(r["first"]), "last": _d(r["last"])}
                for r in rows}

    # ------------------------------------------------------------------ editorial selections
    def selections(self, session: dt.date) -> dict:
        with ro_connection(self.out_dir, "editorial") as c:
            rows = c.execute("SELECT * FROM editorial_selections WHERE session_date = ?",
                             (session.isoformat(),)).fetchall()
        return {r["instrument"]: dict(r) for r in rows}

    def symbol_selections(self, symbol: str) -> dict:
        with ro_connection(self.out_dir, "editorial") as c:
            rows = c.execute("SELECT * FROM editorial_selections WHERE instrument = ? "
                             "ORDER BY session_date", (symbol,)).fetchall()
        return {_d(r["session_date"]): dict(r) for r in rows}

    def selections_between(self, start: dt.date, end: dt.date) -> dict:
        with ro_connection(self.out_dir, "editorial") as c:
            rows = c.execute("SELECT * FROM editorial_selections WHERE session_date >= ? AND "
                             "session_date <= ?", (start.isoformat(), end.isoformat())).fetchall()
        return {(_d(r["session_date"]), r["instrument"]): dict(r) for r in rows}

    def publications(self, session: dt.date) -> list:
        with ro_connection(self.out_dir, "editorial") as c:
            rows = c.execute("SELECT * FROM radar_publications WHERE session_date = ? "
                             "ORDER BY story_rank", (session.isoformat(),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ JSON artifacts
    def radar_artifact_sessions(self) -> list:
        return sorted(self._dated_files(os.path.join("radar", "daily_radar_*.json")))

    def radar_artifact(self, session: dt.date):
        p = self._dated_files(os.path.join("radar", "daily_radar_*.json")).get(session)
        return (_read_json(p), p) if p else (None, None)

    def market_structure_sessions(self) -> list:
        return sorted(self._dated_files(os.path.join("market_structure", "market_structure_*.json")))

    def market_structure_path(self, session: dt.date) -> str | None:
        return self._dated_files(os.path.join("market_structure",
                                              "market_structure_*.json")).get(session)

    def market_structure(self, session: dt.date):
        p = self.market_structure_path(session)
        return (_read_json(p), p) if p else (None, None)

    def universe(self, session: dt.date):
        """`({symbol: {company, industry, sector}}, source_session)` from the Market Structure
        artifact of `session`, else the latest artifact BEFORE it (constituent metadata changes
        rarely; the source session is always returned so the caller can label it)."""
        for s in sorted(self.market_structure_sessions(), reverse=True):
            if s <= session:
                data, _ = self.market_structure(s)
                cons = ((data or {}).get("universe") or {}).get("constituents")
                if cons:
                    return cons, s
        return {}, None

    def intelligence(self, session: dt.date):
        p = self._dated_files(os.path.join("intelligence", "intelligence_*.json")).get(session)
        return (_read_json(p), p) if p else (None, None)

    # ------------------------------------------------------------------ canonical report
    def report_rows(self) -> list:
        with ro_connection(self.out_dir, "market_history") as c:
            rows = c.execute("SELECT report_id, report_type, report_date, session_date, "
                             "generated_at, publication_ready, is_demo, json_artifact_path, "
                             "created_at FROM reports WHERE is_demo = 0 AND report_type != "
                             "'RADAR_SCAN' ORDER BY session_date ASC, created_at ASC").fetchall()
        return [dict(r) for r in rows]

    def report(self, session: dt.date):
        """`(report_dict, row, status)`: the canonical report describing exactly `session` -
        same rule as `operations.report_lookup` (row + artifact + matching ids), read-only."""
        from operations.report_lookup import resolve_report_artifact
        try:
            rows = [r for r in self.report_rows() if _d(r["session_date"]) == session]
        except SourceUnavailable:
            return None, None, "HISTORY_UNAVAILABLE"
        if not rows:
            return None, None, "MISSING"
        row = rows[-1]
        path = resolve_report_artifact(row["json_artifact_path"], self.out_dir)
        if not path:
            return None, row, "ARTIFACT_MISSING"
        data = _read_json(path)
        if not data:
            return None, row, "UNREADABLE"
        if data.get("report_id") != row["report_id"] or _d(data.get("session_date")) != session:
            return None, row, "SESSION_MISMATCH"
        return data, row, "FOUND"

    def publication_runs(self, limit: int = 12) -> list:
        with ro_connection(self.out_dir, "market_history") as c:
            rows = c.execute("SELECT run_id, mode, job_type, target_date, source_session_date, "
                             "run_status, started_at, failure_stage, failure_reason FROM "
                             "publication_runs ORDER BY started_at DESC LIMIT ?",
                             (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ OHLCV
    def ohlcv_rows(self, symbols, start: dt.date, end: dt.date) -> list:
        """`OHLCVRow`s (the store's own read type) for `symbols` in [start, end], oldest first."""
        from storage.ohlcv_models import QualityStatus
        from storage.ohlcv_repository import OHLCVRow
        symbols = list(symbols)
        if not symbols:
            return []
        out = []
        with ro_connection(self.out_dir, "ohlcv") as c:
            for i in range(0, len(symbols), 400):
                chunk = symbols[i:i + 400]
                rows = c.execute(
                    f"SELECT * FROM daily_ohlcv WHERE symbol IN ({','.join('?' * len(chunk))}) "
                    "AND session_date >= ? AND session_date <= ? AND source = ? "
                    "ORDER BY symbol, session_date",
                    [*chunk, start.isoformat(), end.isoformat(), OHLCV_SOURCE]).fetchall()
                for r in rows:
                    try:
                        status = QualityStatus(r["quality_status"])
                    except ValueError:
                        continue
                    out.append(OHLCVRow(
                        symbol=r["symbol"], session_date=_d(r["session_date"]), source=r["source"],
                        open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                        volume=r["volume"], retrieved_at=r["retrieved_at"],
                        quality_status=status))
        return out

    def benchmark_series(self, end: dt.date, days: int = 400) -> list:
        """The market benchmark (^NSEI) from the store, `[{"date", "close"}]` oldest first -
        the shape `radar.relative` / `radar.session_alignment` consume."""
        rows = self.ohlcv_rows([BENCHMARK_SYMBOL], end - dt.timedelta(days=days), end)
        return [{"date": r.session_date, "close": float(r.close)} for r in rows
                if r.quality_status.value == "OK" and r.close and r.close > 0]

    def ohlcv_status(self) -> dict:
        with ro_connection(self.out_dir, "ohlcv") as c:
            latest = c.execute("SELECT symbol, MAX(session_date) d FROM daily_ohlcv WHERE "
                               "source = ? AND quality_status = 'OK' GROUP BY symbol",
                               (OHLCV_SOURCE,)).fetchall()
            quality = c.execute("SELECT quality_status, COUNT(*) n FROM daily_ohlcv "
                                "GROUP BY quality_status").fetchall()
            retrieved = c.execute("SELECT MAX(retrieved_at) FROM daily_ohlcv").fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM daily_ohlcv").fetchone()[0]
        return {"latest_by_symbol": {r["symbol"]: _d(r["d"]) for r in latest},
                "quality_counts": {r["quality_status"]: r["n"] for r in quality},
                "max_retrieved_at": retrieved, "row_count": total}

    # ------------------------------------------------------------------ official snapshots
    def official_sessions(self) -> list:
        root = self.path("official_snapshots")
        if not os.path.isdir(root):
            return []
        return sorted(d for d in (_d(n) for n in os.listdir(root)
                                  if os.path.isfile(os.path.join(root, n,
                                                                 "official_snapshot_manifest.json")))
                      if d)

    def official_manifest(self, session: dt.date):
        from official_snapshots.store import load_manifest
        return load_manifest(self.out_dir, session)

    def official_snapshot(self, session: dt.date, kind: str):
        from official_snapshots.store import load_current
        try:
            snap, _ = load_current(self.out_dir, session, kind)
        except (OSError, ValueError, KeyError):
            return None
        return snap

    # ------------------------------------------------------------------ institutional flows
    def institutional_snapshots(self, source: str) -> list:
        from institutional_flows.store import list_snapshots
        return list_snapshots(self.out_dir, source)

    def institutional_latest(self, source: str, on_or_before: dt.date | None = None):
        from institutional_flows.store import load_latest
        return load_latest(self.out_dir, source, on_or_before=on_or_before)

    def institutional_attempts(self, days: int = 3) -> list:
        from institutional_flows.store import latest_attempts
        return latest_attempts(self.out_dir, days=days)

    # ------------------------------------------------------------------ market events
    def market_events_latest(self, family: str, *, symbol: str | None = None,
                             on_or_before: dt.date | None = None) -> list:
        from market_events.store import load_latest
        return load_latest(self.out_dir, family, symbol=symbol, on_or_before=on_or_before)

    def market_events_list(self, family: str) -> list:
        from market_events.store import list_events
        return list_events(self.out_dir, family)

    def market_events_attempts(self, days: int = 3) -> list:
        from market_events.store import latest_attempts
        return latest_attempts(self.out_dir, days=days)

    # ------------------------------------------------------------------ database health
    def db_health(self, name: str) -> dict:
        path = self.db_file(name)
        info = {"name": name, "file": os.path.basename(path), "exists": os.path.isfile(path)}
        if not info["exists"]:
            info["status"] = "MISSING"
            return info
        info["size_bytes"] = os.path.getsize(path)
        info["modified"] = dt.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(
            timespec="seconds")
        wal = path + "-wal"
        info["wal_pending"] = os.path.exists(wal) and os.path.getsize(wal) > 0
        try:
            with ro_connection(self.out_dir, name) as c:
                info["user_version"] = c.execute("PRAGMA user_version").fetchone()[0]
                info["quick_check"] = c.execute("PRAGMA quick_check").fetchone()[0]
                info["tables"] = {
                    t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                          "AND name NOT LIKE 'sqlite_%'").fetchall()}
            info["status"] = "OK" if info["quick_check"] == "ok" else "CHECK_FAILED"
        except (SourceUnavailable, sqlite3.Error) as exc:
            info["status"] = "UNREADABLE"
            info["error"] = str(exc)
        return info


__all__ = ["DeskRepository", "BENCHMARK_SYMBOL", "OFFICIAL_KINDS"]
