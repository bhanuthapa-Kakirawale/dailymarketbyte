"""Find the canonical MarketReport for one trading session - the one question POST and PRE both
ask before doing anything else.

A report is found only if ALL hold: a non-demo, non-RADAR_SCAN row in `MarketHistory` says it
describes exactly `session`, its JSON artifact exists and parses, the artifact's own report_id
and session_date agree with the row. Anything else is a named status, never a substitution:
an older session's report is never returned for a newer one, and nothing here builds, repairs
or re-saves a report.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

FOUND = "FOUND"
MISSING = "MISSING"
ARTIFACT_MISSING = "ARTIFACT_MISSING"
UNREADABLE = "UNREADABLE"
SESSION_MISMATCH = "SESSION_MISMATCH"
HISTORY_UNAVAILABLE = "HISTORY_UNAVAILABLE"

_EDITION_WINDOW_DAYS = 14      # a recap's report_date (edition) is the next session - at most
                               # a long holiday stretch after the session it describes


@dataclass
class ReportLookup:
    session: dt.date
    status: str
    report: object | None = None          # core.MarketReport
    path: str | None = None
    report_id: str | None = None
    reason: str = ""
    candidates: list = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.status == FOUND

    @property
    def publication_ready(self) -> bool:
        return bool(self.report is not None and self.report.publication_ready)

    def to_dict(self) -> dict:
        return {"session": self.session.isoformat(), "status": self.status,
                "report_id": self.report_id, "path": self.path, "reason": self.reason,
                "publication_ready": self.publication_ready if self.found else None,
                "candidates": list(self.candidates)}


def find_canonical_report(session: dt.date, history=None, db_path: str | None = None) -> ReportLookup:
    """The canonical report for `session`. Pass an open `MarketHistory` or a `db_path`."""
    from core import MarketReport
    owned = history is None
    if owned:
        from config import OUT_DIR
        from storage import MarketHistory, default_db_path
        try:
            history = MarketHistory(db_path or default_db_path(OUT_DIR))
        except Exception as exc:
            return ReportLookup(session, HISTORY_UNAVAILABLE,
                                reason=f"{type(exc).__name__}: {exc}")
    try:
        rows = history.get_reports_between(session, session + dt.timedelta(days=_EDITION_WINDOW_DAYS))
    except Exception as exc:
        return ReportLookup(session, HISTORY_UNAVAILABLE, reason=f"{type(exc).__name__}: {exc}")
    finally:
        if owned:
            history.close()

    rows = sorted((r for r in rows if r.session_date == session.isoformat()
                   and r.report_type != "RADAR_SCAN" and not r.is_demo),
                  key=lambda r: (r.report_date, r.report_id))
    if not rows:
        return ReportLookup(session, MISSING,
                            reason=f"no canonical report describes the session {session}")
    candidates = [{"report_id": r.report_id, "report_date": r.report_date,
                   "path": r.json_artifact_path} for r in rows]
    problems = []
    for r in rows:
        path = r.json_artifact_path
        if not path or not os.path.exists(path):
            problems.append((ARTIFACT_MISSING, r.report_id,
                             f"history records {r.report_id} but its JSON artifact is missing: {path!r}"))
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                report = MarketReport.from_json(fh.read())
        except Exception as exc:
            problems.append((UNREADABLE, r.report_id,
                             f"{path} is unreadable: {type(exc).__name__}: {exc}"))
            continue
        if report.report_id != r.report_id or report.session_date != session:
            problems.append((SESSION_MISMATCH, r.report_id,
                             f"{path} holds {report.report_id} for {report.session_date}, "
                             f"history says {r.report_id} for {session}"))
            continue
        note = "; ".join(p[2] for p in problems)
        return ReportLookup(session, FOUND, report=report, path=path, report_id=r.report_id,
                            reason=note, candidates=candidates)
    status, rid, _ = problems[0]
    return ReportLookup(session, status, report_id=rid, candidates=candidates,
                        reason="; ".join(p[2] for p in problems))


__all__ = ["ReportLookup", "find_canonical_report", "FOUND", "MISSING", "ARTIFACT_MISSING",
           "UNREADABLE", "SESSION_MISMATCH", "HISTORY_UNAVAILABLE"]
