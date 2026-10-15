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
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import time

from . import records
from .models import CHILD_NONZERO, COMPLETED, EXPIRED, FAILED, LOCKED

SATISFIED = "SATISFIED"
PENDING = "PENDING"
DEP_FAILED = "FAILED"

_SATISFYING_STATUSES = frozenset({COMPLETED, CHILD_NONZERO})
_TERMINAL_NON_SATISFYING = frozenset({FAILED, LOCKED, EXPIRED})


def _started_today(latest: dict, today: dt.date, ist) -> bool:
    started = latest.get("started_at")
    try:
        parsed = dt.datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(ist).date() == today


def check(out_dir: str, dep_job: str, today: dt.date, ist) -> tuple[str, dict | None]:
    """One point-in-time read of `dep_job`'s own TODAY run record. Never waits, never writes.

    A missing record, or one whose `started_at` (converted to `ist`) is not `today`, is PENDING
    - this is also how a PRIOR-DAY record is rejected: yesterday's completion never satisfies
    today's dependency (section 14's current-day-only rule)."""
    latest = records.read_latest(out_dir, dep_job)
    if not latest or not _started_today(latest, today, ist):
        return PENDING, None
    rstatus = latest.get("runner_status")
    if rstatus in _SATISFYING_STATUSES:
        return SATISFIED, latest
    if rstatus in _TERMINAL_NON_SATISFYING:
        return DEP_FAILED, latest
    return PENDING, latest   # RUNNING / WAITING_DEPENDENCY / unrecognised - still in flight


@dataclasses.dataclass
class WaitResult:
    outcome: str                  # "SATISFIED" | "FAILED" | "TIMED_OUT"
    record: dict | None
    waited_seconds: float
    satisfied_at: str | None = None


def wait(out_dir: str, dep_job: str, deadline_dt: dt.datetime, *, utcnow, ist,
        sleep=time.sleep, poll_s: float, clock=time.monotonic) -> WaitResult:
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
        outcome, latest = check(out_dir, dep_job, now_ist.date(), ist)
        if outcome == SATISFIED:
            return WaitResult(SATISFIED, latest, clock() - t0, satisfied_at=utcnow().isoformat())
        if outcome == DEP_FAILED:
            return WaitResult(DEP_FAILED, latest, clock() - t0)
        sleep(poll_s)


__all__ = ["SATISFIED", "PENDING", "DEP_FAILED", "check", "WaitResult", "wait"]
