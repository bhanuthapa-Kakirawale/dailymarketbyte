"""Historical index over many runs. All SQL lives in this module.

Two guarantees shape everything here:

* **Atomic.** One MarketReport persists inside one transaction. A failure halfway through
  leaves no trace of that report rather than half of it - a half-written report would be
  worse than a missing one, because it looks complete to a later query.
* **Idempotent.** The same report may be processed repeatedly (Actions retry, manual rerun,
  debugging). Re-persisting an existing report is a no-op by default, so canonical history
  cannot be duplicated or silently rewritten. Operational run rows are separate and append.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field

from core import MarketReport

from .migrations import SchemaVersionError, current_version, initialise

DEFAULT_DB_RELPATH = os.path.join("data", "market_history.db")


def default_db_path(out_dir: str) -> str:
    return os.path.join(out_dir, DEFAULT_DB_RELPATH)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def _dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _loads(text):
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


# --------------------------------------------------------------------- returned types
@dataclass
class StoredReport:
    report_id: str
    report_type: str
    report_date: str
    session_date: str | None
    generated_at: str | None
    schema_version: str | None
    publication_ready: bool
    is_demo: bool
    json_artifact_path: str | None
    validation_summary: dict = field(default_factory=dict)
    content_safety: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass
class StoredFact:
    report_id: str
    fact_id: str
    metric: str
    instrument: str
    value: float | None
    unit: str | None
    market_date: str | None
    validation_status: str
    metadata: dict = field(default_factory=dict)


@dataclass
class StoredObservation:
    report_id: str
    fact_id: str
    observation_id: str
    source_name: str | None
    source_type: str | None
    independence_group: str | None
    value: float | None
    unit: str | None
    market_date: str | None
    observed_at: str | None
    retrieved_at: str | None
    source_reference: str | None
    metadata: dict = field(default_factory=dict)


@dataclass
class StoredValidationResult:
    report_id: str
    fact_id: str
    validator: str
    status: str
    message: str
    checked_at: str | None
    details: dict = field(default_factory=dict)


@dataclass
class StoredRun:
    run_id: str
    report_id: str | None
    mode: str
    stage: str | None
    started_at: str
    completed_at: str | None
    data_qa_status: str | None
    content_qa_status: str | None
    video_qa_status: str | None
    publication_status: str | None
    artifact_path: str | None
    youtube_video_id: str | None
    failure_stage: str | None
    failure_reason: str | None
    details: dict = field(default_factory=dict)


# --------------------------------------------------------------------- repository
class MarketHistory:
    """Queryable history across runs. Open one, use it, close it (or use as a context manager)."""

    def __init__(self, db_path: str, timeout: float = 30.0):
        self.db_path = db_path
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        # busy_timeout so an overlapping rerun waits for the lock instead of failing
        # instantly; WAL so a reader never blocks the writer. Neither is distributed
        # locking - the goal is only that concurrent access waits or fails cleanly rather
        # than corrupting history.
        self.conn = sqlite3.connect(db_path, timeout=timeout)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass                      # e.g. some network filesystems; not worth failing over
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
    def report_exists(self, report_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        return row is not None

    def save_report(self, report: MarketReport, artifact_path: str | None = None,
                    replace: bool = False, is_demo: bool | None = None) -> bool:
        """Persist one report and everything under it, atomically. Returns True if written.

        Idempotent by design: an already-stored report_id is left exactly as it is and False
        is returned. Canonical history is not rewritten by a rerun, which keeps it consistent
        with the immutable JSON artifact the run originally produced. `replace=True` is the
        deliberate escape hatch for a corrected re-persist.
        """
        report_id = report.report_id
        exists = self.report_exists(report_id)
        if exists and not replace:
            return False

        demo = bool(report.metadata.get("demo")) if is_demo is None else bool(is_demo)
        now = dt.datetime.now(dt.timezone.utc).isoformat()

        # One transaction for the whole report: a partial report in the index would look
        # complete to every later query, which is worse than no report at all.
        try:
            with self.conn:
                if exists:
                    # ON DELETE CASCADE clears facts/observations/validation/catalysts/events.
                    self.conn.execute("DELETE FROM reports WHERE report_id = ?", (report_id,))

                self._insert_sources(report, now)
                self._insert_report(report, artifact_path, demo, now)
                self._insert_facts(report)
                self._insert_catalysts(report, now)
                self._insert_events(report, now)
        except Exception:
            # `with self.conn` already rolled back; re-raise so the caller can refuse to
            # publish - unauditable history is a publication failure, not a warning.
            raise
        return True

    def _insert_report(self, report: MarketReport, artifact_path, demo: bool, now: str) -> None:
        summary = report.validation_summary
        self.conn.execute(
            """INSERT INTO reports (report_id, report_type, report_date, session_date,
                                    generated_at, schema_version, publication_ready, is_demo,
                                    json_artifact_path, validation_summary_json,
                                    content_safety_json, metadata_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (report.report_id, report.report_type.value, _iso(report.report_date),
             _iso(report.session_date), _iso(report.generated_at),
             report.to_dict().get("report_schema_version"),
             int(bool(summary.publication_ready)), int(demo), artifact_path,
             _dumps(summary.to_dict()), _dumps(report.content_safety),
             _dumps(report.metadata), now))

    def _insert_sources(self, report: MarketReport, now: str) -> None:
        for name, meta in (report.sources or {}).items():
            self.conn.execute(
                """INSERT INTO sources (source_name, source_type, source_family, upstream_source,
                                        independence_group, retrieval_method, reference,
                                        display_rights_status, metadata_json,
                                        first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_name) DO UPDATE SET
                       source_type=excluded.source_type,
                       source_family=excluded.source_family,
                       upstream_source=excluded.upstream_source,
                       independence_group=excluded.independence_group,
                       retrieval_method=excluded.retrieval_method,
                       reference=excluded.reference,
                       display_rights_status=excluded.display_rights_status,
                       metadata_json=excluded.metadata_json,
                       last_seen_at=excluded.last_seen_at""",
                (name, meta.get("source_type"), meta.get("source_family"),
                 meta.get("upstream_source"), meta.get("independence_group"),
                 meta.get("retrieval_method"), meta.get("reference"),
                 meta.get("display_rights_status"), _dumps(meta), now, now))

    def _insert_facts(self, report: MarketReport) -> None:
        for fact in report.facts:
            self.conn.execute(
                """INSERT INTO facts (report_id, fact_id, metric, instrument, value, unit,
                                      market_date, validation_status, metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (report.report_id, fact.fact_id, fact.metric.value, fact.instrument,
                 fact.value, fact.unit, _iso(fact.market_date),
                 fact.validation_status.value, _dumps(fact.metadata)))

            for obs in fact.observations:
                self.conn.execute(
                    """INSERT INTO observations (report_id, fact_id, observation_id, source_name,
                                                 source_type, independence_group, value, unit,
                                                 market_date, observed_at, retrieved_at,
                                                 source_reference, metadata_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (report.report_id, fact.fact_id, obs.observation_id, obs.source_name,
                     obs.source_type.value, obs.independence_group, obs.value, obs.unit,
                     _iso(obs.market_date), _iso(obs.observed_at), _iso(obs.retrieved_at),
                     obs.source_reference, _dumps(obs.metadata)))

            for result in fact.validation_results:
                self.conn.execute(
                    """INSERT INTO validation_results (report_id, fact_id, validator, status,
                                                       message, checked_at, details_json)
                       VALUES (?,?,?,?,?,?,?)""",
                    (report.report_id, fact.fact_id, result.validator, result.status.value,
                     result.message, _iso(result.checked_at), _dumps(result.details)))

    def _insert_catalysts(self, report: MarketReport, now: str) -> None:
        for bucket, rows in (("gainer", report.gainers), ("loser", report.losers)):
            for row in rows or []:
                catalyst = row.get("catalyst") or {}
                if not row.get("symbol"):
                    continue
                self.conn.execute(
                    """INSERT OR REPLACE INTO catalysts (report_id, symbol, bucket, text,
                            catalyst_type, origin, source_name, source_type, independence_group,
                            publisher, headline_date, metadata_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (report.report_id, row["symbol"], bucket, catalyst.get("text"),
                     catalyst.get("type"), catalyst.get("origin"), catalyst.get("source"),
                     catalyst.get("source_type"), catalyst.get("independence_group"),
                     catalyst.get("publisher"), catalyst.get("headline_date"),
                     _dumps(catalyst), now))

    def _insert_events(self, report: MarketReport, now: str) -> None:
        for position, event in enumerate(report.events or []):
            self.conn.execute(
                """INSERT INTO events (report_id, position, tag, text, origin, source_name,
                                       source_type, independence_group, publisher, event_date,
                                       provenance_resolved, metadata_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report.report_id, position, event.get("tag"), event.get("text"),
                 event.get("origin"), event.get("source"), event.get("source_type"),
                 event.get("independence_group"), event.get("publisher"),
                 event.get("event_date"), int(bool(event.get("provenance_resolved"))),
                 _dumps(event), now))

    # ------------------------------------------------------------------ reading
    def get_report(self, report_id: str) -> StoredReport | None:
        row = self.conn.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
        return self._report(row) if row else None

    def get_reports_between(self, start_date, end_date, report_type: str | None = None,
                            include_demo: bool = False) -> list:
        sql = "SELECT * FROM reports WHERE report_date >= ? AND report_date <= ?"
        params = [_iso(start_date), _iso(end_date)]
        if report_type:
            sql += " AND report_type = ?"
            params.append(report_type)
        if not include_demo:
            sql += " AND is_demo = 0"
        sql += " ORDER BY report_date, report_id"
        return [self._report(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_facts(self, metric: str | None = None, instrument: str | None = None,
                  start_date=None, end_date=None, report_id: str | None = None,
                  include_demo: bool = False) -> list:
        sql = ("SELECT f.* FROM facts f JOIN reports r ON r.report_id = f.report_id WHERE 1=1")
        params: list = []
        if metric:
            sql += " AND f.metric = ?"
            params.append(metric)
        if instrument:
            sql += " AND f.instrument = ?"
            params.append(instrument)
        if start_date:
            sql += " AND f.market_date >= ?"
            params.append(_iso(start_date))
        if end_date:
            sql += " AND f.market_date <= ?"
            params.append(_iso(end_date))
        if report_id:
            sql += " AND f.report_id = ?"
            params.append(report_id)
        if not include_demo:
            sql += " AND r.is_demo = 0"
        sql += " ORDER BY f.market_date, f.instrument"
        return [self._fact(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_observations(self, fact_id: str, report_id: str | None = None) -> list:
        sql = "SELECT * FROM observations WHERE fact_id = ?"
        params = [fact_id]
        if report_id:
            sql += " AND report_id = ?"
            params.append(report_id)
        sql += " ORDER BY observation_id"
        return [self._observation(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_validation_results(self, fact_id: str, report_id: str | None = None) -> list:
        sql = "SELECT * FROM validation_results WHERE fact_id = ?"
        params = [fact_id]
        if report_id:
            sql += " AND report_id = ?"
            params.append(report_id)
        sql += " ORDER BY id"
        return [StoredValidationResult(
            report_id=r["report_id"], fact_id=r["fact_id"], validator=r["validator"],
            status=r["status"], message=r["message"] or "", checked_at=r["checked_at"],
            details=_loads(r["details_json"])) for r in self.conn.execute(sql, params).fetchall()]

    def get_catalysts(self, report_id: str) -> list:
        rows = self.conn.execute(
            "SELECT * FROM catalysts WHERE report_id = ? ORDER BY bucket, symbol",
            (report_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_events(self, report_id: str) -> list:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE report_id = ? ORDER BY position", (report_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_source(self, source_name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM sources WHERE source_name = ?",
                                (source_name,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ publication runs
    def start_run(self, mode: str, report_id: str | None = None,
                  run_id: str | None = None) -> str:
        run_id = run_id or f"run_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
        with self.conn:
            self.conn.execute(
                """INSERT INTO publication_runs (run_id, report_id, mode, stage, started_at)
                   VALUES (?,?,?,?,?)""",
                (run_id, report_id, mode, "COLLECTED",
                 dt.datetime.now(dt.timezone.utc).isoformat()))
        return run_id

    def update_run(self, run_id: str, **fields) -> None:
        """Update the mutable operational fields of a run. Unknown keys are ignored."""
        allowed = {"report_id", "stage", "completed_at", "data_qa_status", "content_qa_status",
                   "video_qa_status", "publication_status", "artifact_path",
                   "youtube_video_id", "failure_stage", "failure_reason"}
        sets, params = [], []
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                params.append(_iso(value) if isinstance(value, (dt.date, dt.datetime)) else value)
            elif key == "details":
                sets.append("details_json = ?")
                params.append(_dumps(value))
        if not sets:
            return
        params.append(run_id)
        with self.conn:
            self.conn.execute(f"UPDATE publication_runs SET {', '.join(sets)} WHERE run_id = ?",
                              params)

    def finish_run(self, run_id: str, stage: str, publication_status: str,
                   failure_stage: str | None = None, failure_reason: str | None = None,
                   **fields) -> None:
        self.update_run(run_id, stage=stage, publication_status=publication_status,
                        failure_stage=failure_stage, failure_reason=failure_reason,
                        completed_at=dt.datetime.now(dt.timezone.utc).isoformat(), **fields)

    def get_publication_runs(self, report_id: str | None = None, limit: int = 50) -> list:
        sql = "SELECT * FROM publication_runs"
        params: list = []
        if report_id:
            sql += " WHERE report_id = ?"
            params.append(report_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return [self._run(r) for r in self.conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ row mapping
    @staticmethod
    def _report(row) -> StoredReport:
        return StoredReport(
            report_id=row["report_id"], report_type=row["report_type"],
            report_date=row["report_date"], session_date=row["session_date"],
            generated_at=row["generated_at"], schema_version=row["schema_version"],
            publication_ready=bool(row["publication_ready"]), is_demo=bool(row["is_demo"]),
            json_artifact_path=row["json_artifact_path"],
            validation_summary=_loads(row["validation_summary_json"]),
            content_safety=_loads(row["content_safety_json"]),
            metadata=_loads(row["metadata_json"]), created_at=row["created_at"])

    @staticmethod
    def _fact(row) -> StoredFact:
        return StoredFact(
            report_id=row["report_id"], fact_id=row["fact_id"], metric=row["metric"],
            instrument=row["instrument"], value=row["value"], unit=row["unit"],
            market_date=row["market_date"], validation_status=row["validation_status"],
            metadata=_loads(row["metadata_json"]))

    @staticmethod
    def _observation(row) -> StoredObservation:
        return StoredObservation(
            report_id=row["report_id"], fact_id=row["fact_id"],
            observation_id=row["observation_id"], source_name=row["source_name"],
            source_type=row["source_type"], independence_group=row["independence_group"],
            value=row["value"], unit=row["unit"], market_date=row["market_date"],
            observed_at=row["observed_at"], retrieved_at=row["retrieved_at"],
            source_reference=row["source_reference"], metadata=_loads(row["metadata_json"]))

    @staticmethod
    def _run(row) -> StoredRun:
        return StoredRun(
            run_id=row["run_id"], report_id=row["report_id"], mode=row["mode"],
            stage=row["stage"], started_at=row["started_at"], completed_at=row["completed_at"],
            data_qa_status=row["data_qa_status"], content_qa_status=row["content_qa_status"],
            video_qa_status=row["video_qa_status"], publication_status=row["publication_status"],
            artifact_path=row["artifact_path"], youtube_video_id=row["youtube_video_id"],
            failure_stage=row["failure_stage"], failure_reason=row["failure_reason"],
            details=_loads(row["details_json"]))


__all__ = ["MarketHistory", "default_db_path", "SchemaVersionError", "current_version",
           "StoredReport", "StoredFact", "StoredObservation", "StoredValidationResult",
           "StoredRun", "DEFAULT_DB_RELPATH"]
