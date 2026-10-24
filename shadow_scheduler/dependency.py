"""shadow_scheduler.dependency: scheduler-level dependency ordering (PK-E follow-up).

A small static map (`config.DEPENDENCIES`) says `pre` depends on `intelligence_am` and `post`
depends on `intelligence_pm`. This module answers exactly one question - "has TODAY's upstream
invocation reached a terminal, attempted outcome?" - by reading shadow_scheduler's OWN run
records for the upstream job (`records.read_latest`). It never reads business state (the
canonical report, readiness, Radar, Market Structure, publication status, ...) and never
invokes the upstream job itself - that job remains its own independently scheduled Task
Scheduler entry; this module only observes what `runner.py` already recorded about it.

Two entry points:
    check(...)  - one point-in-time, non-blocking read. Safe to call as often as needed.
    wait(...)   - a bounded poll loop built on top of `check()`. NEVER acquires or touches the
                 global scheduler lock (shadow_scheduler.lock) - the critical ordering rule is
                 that a job waits for its dependency BEFORE attempting the lock, never while
                 holding it, or the two jobs could deadlock/starve each other. Bounded by the
                 caller's own execution-window deadline, never indefinite.

CHILD_NONZERO counts as SATISFIED: the upstream job's own business exit code (e.g. PK-D's
BLOCKED=20) is never re-judged here - "the refresh ATTEMPT finished" is all this layer asks.
PK-C remains the sole authority on whether the dependent job's render is actually safe.

Occurrence identity (PK-OPS-1, 2026-10-24): "today" is the WAITING job's own attributed
scheduled_date (`config.resolve_occurrence`), and an upstream record counts only if IT is
attributed to that same scheduled_date (its stored `scheduled_date`, or - for a record written
before that field existed - the same rule applied to its `started_at`). Before this change the
match was "upstream started on the same wall-clock date", which let last night's PM job fired at
06:48 satisfy last night's POST fired at 06:48. An EARLY (rejected, before-window) upstream
record is ignored - the real in-window run may still come. An upstream checkpoint whose runner
is verifiably gone (`orphans.effective_status` ABANDONED / ORPHAN_SUSPECTED) is a terminal
non-satisfying outcome: it will never finish, so it is never waited on until the deadline.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import time

from . import records
from .models import (ABANDONED, CHILD_NONZERO, COMPLETED, DEPENDENCY_FAILED, EARLY, EXPIRED,
                     FAILED, LOCKED)

SATISFIED = "SATISFIED"
PENDING = "PENDING"
DEP_FAILED = "FAILED"

_SATISFYING_STATUSES = frozenset({COMPLETED, CHILD_NONZERO})
_TERMINAL_NON_SATISFYING = frozenset({FAILED, LOCKED, EXPIRED, DEPENDENCY_FAILED, ABANDONED,
                                      "ORPHAN_SUSPECTED"})

def upstream_records(out_dir: str, dep_job: str, scheduled_date: dt.date) -> list:
    """`dep_job`'s records attributed to `scheduled_date`, EARLY rejections excluded, oldest
    first. Read-only."""
    from .orphans import occurrence_records
    return [r for r in occurrence_records(out_dir, dep_job, scheduled_date)
            if r["_attribution"]["invocation"] != EARLY]


def check(out_dir: str, dep_job: str, today: dt.date, ist) -> tuple[str, dict | None]:
    """One point-in-time read of `dep_job`'s records for the occurrence `today` (the waiting
    job's own attributed scheduled_date - see the module docstring). Never waits, never writes.

    Any satisfying record -> SATISFIED; else any still-in-flight one -> PENDING; else a
    terminal non-satisfying one (incl. a verifiably abandoned checkpoint) -> FAILED; no record
    for that occurrence at all -> PENDING. A PRIOR occurrence's record never counts - yesterday's
    completion never satisfies today's dependency (section 14's current-day-only rule).
    `ist` is kept for signature compatibility; attribution is always Asia/Kolkata."""
    from .orphans import effective_status
    in_flight = failed = None
    for rec in upstream_records(out_dir, dep_job, today):
        status = effective_status(out_dir, rec)
        if status in _SATISFYING_STATUSES:
            return SATISFIED, rec
        if status in _TERMINAL_NON_SATISFYING:
            failed = rec if status == rec.get("runner_status") else dict(rec, runner_status=status)
        else:
            in_flight = rec
    if in_flight is not None:
        return PENDING, in_flight   # RUNNING / WAITING_DEPENDENCY / unrecognised - in flight
    if failed is not None:
        return DEP_FAILED, failed
    return PENDING, None


@dataclasses.dataclass
class WaitResult:
    outcome: str                  # "SATISFIED" | "FAILED" | "TIMED_OUT"
    record: dict | None
    waited_seconds: float
    satisfied_at: str | None = None


def wait(out_dir: str, dep_job: str, deadline_dt: dt.datetime, *, utcnow, ist,
        sleep=time.sleep, poll_s: float, clock=time.monotonic,
        scheduled_date: dt.date | None = None) -> WaitResult:
    """Poll `check()` until SATISFIED, FAILED, or `deadline_dt` passes - `deadline_dt` is the
    WAITING job's OWN execution-window deadline, never the dependency's. The deadline is
    checked FIRST each iteration, before `check()`: if the dependency only becomes SATISFIED
    at/after the deadline, this still returns TIMED_OUT (the window's deadline authority always
    wins - section 6/19/33), not a late SATISFIED. Never acquires the global scheduler lock,
    never invokes `dep_job` itself. If already SATISFIED/FAILED on the very first look, returns
    immediately with `waited_seconds` ~= 0 - the common, healthy-day case costs one cheap local
    file read, nothing more.
    """
    t0 = clock()
    while True:
        now_ist = utcnow().astimezone(ist)
        if now_ist >= deadline_dt:
            return WaitResult("TIMED_OUT", None, clock() - t0)
        # The occurrence is fixed by the caller, never re-derived from the wall clock mid-wait.
        outcome, latest = check(out_dir, dep_job, scheduled_date or now_ist.date(), ist)
        if outcome == SATISFIED:
            return WaitResult(SATISFIED, latest, clock() - t0, satisfied_at=utcnow().isoformat())
        if outcome == DEP_FAILED:
            return WaitResult(DEP_FAILED, latest, clock() - t0)
        sleep(poll_s)


__all__ = ["SATISFIED", "PENDING", "DEP_FAILED", "upstream_records", "check", "WaitResult",
          "wait"]
