"""shadow_scheduler.orphans: READ-ONLY classification of non-terminal run records (PK-OPS-1,
2026-10-24). No write-mode `open()`, no `os.replace()`, no process signal/kill anywhere in this
file - it only observes. Writing an append-only reconciliation marker lives in `records.py`
(`write_reconciliation`), driven by `reconcile.py`.

A RUNNING / WAITING_DEPENDENCY checkpoint is left behind whenever the runner process dies
before its own `finally` writes the terminal record - in practice Task Scheduler's
ExecutionTimeLimit (PT50M) terminating a runner that was still waiting on its dependency or on
its child, or the machine being shut down mid-run. Such a record is only ever classified
ABANDONED on positive evidence, never on age or a missing log alone:

    1. the record was written on THIS host (a cross-host PID can't be checked - UNVERIFIABLE),
    2. its runner PID is verifiably gone - not running, or now a DIFFERENT process (Windows
       reuses PIDs: the live process's creation time is after the record's own start), and
    3. no child it may have launched can still be running:
       - WAITING_DEPENDENCY: no child is ever launched in that state, by construction;
       - RUNNING with a recorded `child_pid`: that PID is verifiably gone/reused too;
       - RUNNING without one (a record written before child PIDs were checkpointed, or one
         whose child never launched): the child is unknowable, so the record must also be
         quiet for LEGACY_QUIET_SECONDS - its log (if any) not written, and the run not
         started, within that period. A missing log is only ever one input to this, never
         sufficient by itself.

Anything that can't be proven is ACTIVE (provably running) or UNVERIFIABLE (can't tell) - both
are left alone by reconciliation. Every decision carries its evidence.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import sys

from . import config, records
from .models import NON_TERMINAL_STATUSES, RUNNING, TERMINAL_STATUSES, WAITING_DEPENDENCY

ACTIVE, ABANDONED, UNVERIFIABLE, RECONCILED, TERMINAL = (
    "ACTIVE", "ABANDONED", "UNVERIFIABLE", "RECONCILED", "TERMINAL")

# Process-state vocabulary for `probe_process`.
ALIVE, DEAD, REUSED, UNKNOWN = "ALIVE", "DEAD", "REUSED", "UNKNOWN"

# A process created more than this long AFTER the moment the record says it started cannot be
# the process that wrote the record (the runner writes `started_at` within ~1s of its own
# start; `child_started_at` is checkpointed right after `Popen` returns).
PID_IDENTITY_SLACK_SECONDS = 5.0

# Legacy RUNNING records carry no child PID. Two hours is > 2x Task Scheduler's own 50-minute
# ExecutionTimeLimit and > every observed PRE/POST/intelligence refresh runtime, so a child
# still running after that much log silence is not a plausible reading.
LEGACY_QUIET_SECONDS = 2 * 3600

_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87


def _parse_utc(value) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _win32_probe(pid: int) -> tuple[str, dt.datetime | None]:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        # Only "no such process" is proof of death - access denied etc. means it may exist.
        return (DEAD, None) if err == _ERROR_INVALID_PARAMETER else (UNKNOWN, None)
    try:
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return DEAD, None   # exited; the object only lingers because a handle is open
        creation, exit_t, kernel_t, user_t = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t),
                                        ctypes.byref(kernel_t), ctypes.byref(user_t)):
            return ALIVE, None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        created = (dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)
                   + dt.timedelta(microseconds=ticks // 10))
        return ALIVE, created
    finally:
        kernel32.CloseHandle(handle)


def probe_process(pid, started_at: dt.datetime | None) -> dict:
    """{"state": ALIVE|DEAD|REUSED|UNKNOWN, "created_at": iso|None}. Never signals the process
    (on win32 `os.kill(pid, 0)` can terminate it - only OpenProcess/GetExitCodeProcess/
    GetProcessTimes are used). REUSED: a live process holds the PID but was created after
    `started_at` + slack, so it is not the one that wrote the record."""
    if not isinstance(pid, int) or pid <= 0:
        return {"state": UNKNOWN, "created_at": None}
    if sys.platform == "win32":
        try:
            state, created = _win32_probe(pid)
        except Exception:   # pragma: no cover - ctypes failure: can't tell
            return {"state": UNKNOWN, "created_at": None}
        if (state == ALIVE and created is not None and started_at is not None
                and created > started_at + dt.timedelta(seconds=PID_IDENTITY_SLACK_SECONDS)):
            state = REUSED
        return {"state": state, "created_at": created.isoformat() if created else None}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"state": DEAD, "created_at": None}
    except OSError:
        return {"state": UNKNOWN, "created_at": None}
    return {"state": UNKNOWN, "created_at": None}   # exists; identity unverifiable here


def _read_lock(out_dir: str) -> dict | None:
    from .lock import lock_path
    try:
        with open(lock_path(out_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _log_evidence(out_dir: str, record: dict) -> dict:
    rel = record.get("log_path") or ""
    if not rel:
        return {"log_path": None, "log_exists": False, "log_mtime": None}
    path = os.path.join(out_dir, rel)
    try:
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
    except OSError:
        return {"log_path": rel, "log_exists": False, "log_mtime": None}
    return {"log_path": rel, "log_exists": True, "log_mtime": mtime.isoformat()}


def _business_evidence(business_root: str | None, record: dict) -> dict | None:
    """Information only ("finished but not finalized" evidence) - never used to claim the
    child succeeded, and never changes the classification."""
    spec = config.JOB_REGISTRY.get(record.get("job"))
    if not business_root or spec is None or not spec.business_latest_relpath:
        return None
    try:
        with open(os.path.join(business_root, spec.business_latest_relpath),
                  encoding="utf-8") as fh:
            biz = json.load(fh)
    except (OSError, ValueError):
        return None
    biz_started = _parse_utc(biz.get("started_at"))
    rec_started = _parse_utc(record.get("started_at"))
    if biz_started is None or rec_started is None or biz_started < rec_started:
        return None
    return {"path": spec.business_latest_relpath.replace(os.sep, "/"),
            "run_id": biz.get("run_id"), "started_at": biz.get("started_at"),
            "completed_at": biz.get("completed_at"),
            "status": biz.get("orchestrator_status") or biz.get("status"),
            "note": "a business run started after this record - evidence only, not attribution"}


def attribution(record: dict) -> dict:
    """(scheduled_date, invocation) for any record - the stored fields when present, else
    inferred from `started_at` with the SAME `config.resolve_occurrence` rule the runner now
    enforces (records written before PK-OPS-1 carry neither field)."""
    if record.get("scheduled_date") and record.get("invocation"):
        return {"scheduled_date": record["scheduled_date"], "invocation": record["invocation"],
                "inferred": False}
    spec = config.JOB_REGISTRY.get(record.get("job"))
    started = _parse_utc(record.get("started_at"))
    if spec is None or started is None:
        return {"scheduled_date": None, "invocation": None, "inferred": True}
    occ = config.resolve_occurrence(spec, started)
    return {"scheduled_date": occ.scheduled_date.isoformat(), "invocation": occ.invocation,
            "inferred": True}


# A run id starts with its own UTC invocation time (`records.new_run_id`). An invocation is
# attributed to an occurrence at most `config._OCCURRENCE_SEARCH_DAYS` away, so a file whose
# run-id timestamp is further than this from `scheduled_date` is skipped without being parsed -
# keeps the dependency's 10-second poll loop cheap as runs/ grows. A run id that does not parse
# that way is always read (never silently skipped).
_OCCURRENCE_SCAN_SPAN = dt.timedelta(days=10)


def _run_id_time(path: str) -> dt.datetime | None:
    stem = os.path.basename(path)[:16]
    try:
        return dt.datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def occurrence_records(out_dir: str, job: str, scheduled_date: dt.date) -> list:
    """Every record of `job` attributed to the occurrence `scheduled_date` (any invocation
    class, any status), oldest first, each with an added `_attribution` key. Read-only."""
    import glob
    anchor = dt.datetime.combine(scheduled_date, dt.time(), tzinfo=dt.timezone.utc)
    out = []
    for path in glob.glob(os.path.join(records.runs_dir(out_dir), "*.json")):
        stamp = _run_id_time(path)
        if stamp is not None and abs(stamp - anchor) > _OCCURRENCE_SCAN_SPAN:
            continue
        rec = records.read_record(path)
        if not rec or rec.get("job") != job:
            continue
        attr = attribution(rec)
        if attr["scheduled_date"] != scheduled_date.isoformat():
            continue
        out.append(dict(rec, _attribution=attr))
    out.sort(key=lambda r: r.get("started_at") or "")
    return out


def classify(record: dict, *, out_dir: str, now: dt.datetime | None = None,
             hostname: str | None = None, probe=None, lock_content=None,
             business_root: str | None = None) -> dict:
    """Pure observation of ONE run record. Returns {"run_id", "job", "classification",
    "reasons": [...], "evidence": {...}}. `probe`/`now`/`hostname`/`lock_content` are
    injectable so every branch is testable without a real process."""
    now = now or dt.datetime.now(dt.timezone.utc)
    hostname = hostname or socket.gethostname()
    probe = probe or probe_process   # resolved at call time, so it can be injected/patched
    run_id = record.get("run_id")
    status = record.get("runner_status")
    out = {"run_id": run_id, "job": record.get("job"), "runner_status": status,
           "started_at": record.get("started_at"), "classification": None, "reasons": [],
           "evidence": {"attribution": attribution(record)}}
    reasons, evidence = out["reasons"], out["evidence"]

    if status in TERMINAL_STATUSES:
        out["classification"] = TERMINAL
        return out
    marker = records.read_reconciliation(out_dir, run_id) if run_id else None
    if marker is not None:
        out["classification"] = RECONCILED
        evidence["reconciliation"] = {"reconciled_at": marker.get("reconciled_at"),
                                      "effective_status": marker.get("effective_status")}
        reasons.append("an append-only reconciliation marker already exists for this run")
        return out
    if status not in NON_TERMINAL_STATUSES:
        out["classification"] = UNVERIFIABLE
        reasons.append(f"unrecognised runner_status {status!r}")
        return out

    started = _parse_utc(record.get("started_at"))
    evidence["record_hostname"] = record.get("hostname")
    if record.get("hostname") != hostname:
        out["classification"] = UNVERIFIABLE
        reasons.append(f"written on host {record.get('hostname')!r}, not {hostname!r} - "
                       "liveness cannot be verified remotely")
        return out

    runner_probe = probe(record.get("pid"), started)
    evidence["runner_pid"] = record.get("pid")
    evidence["runner_process"] = runner_probe
    lock_content = _read_lock(out_dir) if lock_content is None else lock_content
    holds_lock = bool(lock_content) and lock_content.get("run_id") == run_id
    evidence["holds_global_lock"] = holds_lock
    evidence.update(_log_evidence(out_dir, record))
    biz = _business_evidence(business_root, record)
    if biz:
        evidence["business_run_after_start"] = biz

    if runner_probe["state"] == ALIVE:
        out["classification"] = ACTIVE
        reasons.append(f"runner pid {record.get('pid')} is running and is the same process")
        return out
    if runner_probe["state"] == UNKNOWN:
        out["classification"] = UNVERIFIABLE
        reasons.append(f"runner pid {record.get('pid')} liveness/identity cannot be verified")
        return out
    reasons.append(f"runner pid {record.get('pid')} is "
                   + ("not running" if runner_probe["state"] == DEAD
                      else "now a different, later-created process (PID reused)"))
    if holds_lock:
        reasons.append("the global lock still names this run - it is stale (dead owner) and "
                       "the next runner's own acquire() reclaims it; reconciliation never "
                       "touches the lock")

    if status == WAITING_DEPENDENCY:
        out["classification"] = ABANDONED
        reasons.append("WAITING_DEPENDENCY never launches a child, so no child can be running")
        out["abandoned_kind"] = "RUNNER_TERMINATED_WAITING_DEPENDENCY"
        return out

    # status == RUNNING
    child_pid = record.get("child_pid")
    if child_pid:
        child_probe = probe(child_pid, _parse_utc(record.get("child_started_at")))
        evidence["child_pid"] = child_pid
        evidence["child_process"] = child_probe
        if child_probe["state"] == ALIVE:
            out["classification"] = ACTIVE
            reasons.append(f"the runner is gone but its child pid {child_pid} is still running")
            return out
        if child_probe["state"] == UNKNOWN:
            out["classification"] = UNVERIFIABLE
            reasons.append(f"child pid {child_pid} liveness/identity cannot be verified")
            return out
        out["classification"] = ABANDONED
        out["abandoned_kind"] = "RUNNER_AND_CHILD_TERMINATED_UNFINALIZED"
        reasons.append(f"child pid {child_pid} is gone too; its exit code was never recorded")
        return out

    if record.get("child_phase") == "NOT_LAUNCHED":
        out["classification"] = ABANDONED
        out["abandoned_kind"] = "RUNNER_TERMINATED_BEFORE_CHILD_LAUNCH"
        reasons.append("child_phase NOT_LAUNCHED: the runner died before its LAUNCHING "
                       "checkpoint, so no child was ever started")
        return out

    # Legacy (no child_phase) or LAUNCHING without a PID: the child is unknowable - require
    # quiet as well.
    last_activity = started
    if evidence.get("log_mtime"):
        log_mtime = _parse_utc(evidence["log_mtime"])
        if log_mtime and (last_activity is None or log_mtime > last_activity):
            last_activity = log_mtime
    if last_activity is None:
        out["classification"] = UNVERIFIABLE
        reasons.append("no parseable started_at - cannot judge how long the run has been quiet")
        return out
    quiet_s = (now - last_activity).total_seconds()
    evidence["quiet_seconds"] = round(quiet_s, 1)
    if quiet_s < LEGACY_QUIET_SECONDS:
        out["classification"] = UNVERIFIABLE
        reasons.append(f"no child pid recorded and the run was active {quiet_s:.0f}s ago "
                       f"(< {LEGACY_QUIET_SECONDS}s) - a child may still be running")
        return out
    out["classification"] = ABANDONED
    out["abandoned_kind"] = "RUNNER_TERMINATED_CHILD_UNKNOWN"
    reasons.append("no child pid recorded; "
                   + ("its log was last written" if evidence.get("log_exists")
                      else "no log file exists (only one input, never sufficient alone); "
                           "the run started")
                   + f" {quiet_s / 3600:.1f}h ago, beyond the {LEGACY_QUIET_SECONDS // 3600}h "
                     "quiet threshold")
    return out


def scan(out_dir: str, *, now: dt.datetime | None = None, hostname: str | None = None,
         probe=None, business_root: str | None = None,
         include_terminal: bool = False) -> list:
    """Classify every non-terminal record (oldest first). Read-only."""
    lock_content = _read_lock(out_dir) or {}
    rows = []
    for rec in records.list_records(out_dir):
        if not include_terminal and rec.get("runner_status") in TERMINAL_STATUSES:
            continue
        rows.append(classify(rec, out_dir=out_dir, now=now, hostname=hostname, probe=probe,
                             lock_content=lock_content, business_root=business_root))
    return rows


def effective_status(out_dir: str, record: dict | None, *, now=None, hostname=None,
                     probe=None) -> str | None:
    """The status a READER should act on: the record's own terminal status; ABANDONED once
    reconciled; "ORPHAN_SUSPECTED" for an unreconciled checkpoint that `classify` proves
    ABANDONED (live evaluation, no write); else the record's own (in-flight) status."""
    if not record:
        return None
    status = record.get("runner_status")
    if status in TERMINAL_STATUSES:
        return status
    if record.get("run_id") and records.read_reconciliation(out_dir, record["run_id"]):
        from .models import ABANDONED as _ABANDONED
        return _ABANDONED
    if status in (RUNNING, WAITING_DEPENDENCY):
        verdict = classify(record, out_dir=out_dir, now=now, hostname=hostname, probe=probe)
        if verdict["classification"] == ABANDONED:
            return ORPHAN_SUSPECTED
    return status


ORPHAN_SUSPECTED = "ORPHAN_SUSPECTED"


__all__ = ["ACTIVE", "ABANDONED", "UNVERIFIABLE", "RECONCILED", "TERMINAL", "ALIVE", "DEAD",
          "REUSED", "UNKNOWN", "PID_IDENTITY_SLACK_SECONDS", "LEGACY_QUIET_SECONDS",
          "ORPHAN_SUSPECTED", "probe_process", "attribution", "occurrence_records",
          "classify", "scan",
          "effective_status"]
