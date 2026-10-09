"""PK-D POST orchestration: ONE command for the evening production run.

Sequences the EXISTING commands `operations.evening_full` already uses (`python main.py
--mode report`, then `python main.py --no-fetch-public`) with a run lock, idempotency,
`--resume`, and the PK-C readiness gate at both PREFLIGHT and POST_RENDER - never a second
report-builder or renderer. `--force` only ever bypasses PK-D's OWN "already completed" /
"already rendered" shortcuts; it never adds a flag to the main.py subprocess calls, so it can
never duplicate or replace a canonical report (main.py's own idempotency guarantees that).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import socket
import time

from operations import evening_full as EF

from . import manifest as MF
from .lock import LockConflict, RunLock
from .models import (ALREADY_COMPLETED, BLOCKED, DEGRADED, FAILED, LOCKED, SHADOW, STAGE_BLOCKED,
                     STAGE_FAILED, STAGE_OK, STAGE_SKIPPED, SUCCESS, RunManifest)

EDITION = "POST"


def _default_post_readiness(session: dt.date, as_of: dt.datetime, out_dir: str, calendar,
                            intent: str):
    from readiness.post import evaluate_post
    from readiness.report import write_report
    res = evaluate_post(session, as_of, mode="LIVE", intent=intent, out_dir=out_dir,
                        calendar=calendar)
    path = None
    try:
        path = write_report(res, out_dir)
    except Exception as exc:        # the verdict stands; the file is diagnostics only
        res.notes.append(f"readiness report not written: {type(exc).__name__}: {exc}")
    return res, path


def _default_post_render(session: dt.date, out_dir: str, intent: str):
    from readiness.post_render import evaluate_post_render
    return evaluate_post_render("POST", session, out_dir=out_dir, intent=intent)


def _find_report_record(out_dir: str, session_iso: str) -> dict | None:
    """The newest SUCCESS/DEGRADED REPORT-job record describing `session_iso`, else None."""
    folder = os.path.join(out_dir, "report_jobs", session_iso)
    best = None
    for path in sorted(glob.glob(os.path.join(folder, "report_job_*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError, TypeError):
            continue
        if rec.get("target_session") == session_iso and rec.get("run_status") in EF.ACCEPTABLE_REPORT:
            best = rec
    return best


def _artifacts_from_manifest(m: dict | None) -> tuple:
    if not m:
        return None, None
    video = m.get("video")
    video_artifact = {"path": video, "duration": m.get("duration"),
                      "exists": bool(video and os.path.exists(video))}
    pa = m.get("publication_audit") or {}
    audit_artifact = {"path": None, "final": pa.get("final"),
                      "failed_checks": pa.get("failed_checks")}
    return video_artifact, audit_artifact


def run_post(*, session_date: dt.date | None = None, as_of: dt.datetime | None = None,
            intent: str = SHADOW, resume: bool = False, force: bool = False,
            no_write: bool = False, out_dir: str | None = None, runner=None,
            readiness_fn=None, post_render_fn=None, clock=None, calendar=None,
            cli_argv: list | None = None) -> RunManifest:
    import config
    from products.report_job import resolve_session

    out_dir = out_dir or config.OUT_DIR
    runner = runner or EF.run_main
    readiness_fn = readiness_fn or _default_post_readiness
    post_render_fn = post_render_fn or _default_post_render
    clock = clock or config.now_ist
    intent = (intent or SHADOW).upper()
    cli_argv = list(cli_argv or [])
    now = clock()

    def _write(manifest: RunManifest) -> RunManifest:
        if not no_write:
            MF.write_manifest(out_dir, manifest)
        return manifest

    # ---- SESSION_RESOLUTION (products.report_job.resolve_session - never re-derived)
    resolved, problem = resolve_session(session_date, now, calendar)
    if problem:
        code, message = problem
        run_id = MF.new_run_id(now)
        m = RunManifest(edition=EDITION, command="post", run_id=run_id, intent=intent,
                        forced=force, no_write=no_write, git_commit=MF.git_commit(config.BASE_DIR),
                        host={"hostname": socket.gethostname(), "pid": os.getpid()},
                        cli={"argv": cli_argv, "resume": resume, "force": force})
        m.begin_stage("SESSION_RESOLUTION")
        m.finish_stage("SESSION_RESOLUTION", STAGE_BLOCKED, f"{code}: {message}")
        m.blocking_reasons.append(f"{code}: {message}")
        m.failure = {"stage": "SESSION_RESOLUTION", "reason": message}
        m.finalize(BLOCKED)
        return _write(m)
    session = resolved
    session_iso = session.isoformat()

    # ---- RUN_LOCK (+ --resume: reuse a leftover RUNNING manifest's run_id)
    resumable = MF.running_manifest_to_resume(out_dir, EDITION, session_iso) if resume else None
    run_id = resumable["run_id"] if resumable else MF.new_run_id(now)
    m = RunManifest(edition=EDITION, command="post", run_id=run_id, session_date=session_iso,
                    intent=intent, forced=force, no_write=no_write,
                    git_commit=MF.git_commit(config.BASE_DIR),
                    host={"hostname": socket.gethostname(), "pid": os.getpid()},
                    cli={"argv": cli_argv, "resume": resume, "force": force})
    if resumable:
        m.resumed_from_run_id = run_id
        m.started_at = resumable.get("started_at") or m.started_at
    m.begin_stage("RUN_LOCK")
    lock = RunLock(out_dir, EDITION, session_iso, now=lambda: dt.datetime.now(dt.timezone.utc))
    try:
        reclaimed = lock.acquire(run_id, intent, "post", cli_argv)
    except LockConflict as exc:
        m.finish_stage("RUN_LOCK", STAGE_FAILED, str(exc))
        m.failure = {"stage": "RUN_LOCK", "reason": str(exc)}
        m.finalize(LOCKED)
        return _write(m)
    if reclaimed:
        m.reclaimed_stale_lock = reclaimed
    m.finish_stage("RUN_LOCK", STAGE_OK, f"acquired for {EDITION} {session_iso}")
    _write(m)      # a RUNNING checkpoint - what a later --resume would find after a crash

    try:
        return _run_post_locked(m, session=session, session_iso=session_iso, as_of=as_of,
                                intent=intent, force=force, out_dir=out_dir, runner=runner,
                                readiness_fn=readiness_fn, post_render_fn=post_render_fn,
                                clock=clock, calendar=calendar, write=_write)
    finally:
        lock.release()


def _run_post_locked(m, *, session, session_iso, as_of, intent, force, out_dir, runner,
                     readiness_fn, post_render_fn, clock, calendar, write):
    # ---- IDEMPOTENCY_CHECK
    m.begin_stage("IDEMPOTENCY_CHECK")
    if force:
        m.finish_stage("IDEMPOTENCY_CHECK", STAGE_SKIPPED, "--force")
    else:
        rec = _find_report_record(out_dir, session_iso)
        if rec is not None:
            report_id = (rec.get("details") or {}).get("report_id")
            prior = post_render_fn(session, out_dir, intent)
            if report_id and prior.overall_status in ("READY", "DEGRADED"):
                m.finish_stage("IDEMPOTENCY_CHECK", STAGE_OK,
                               f"already completed (readiness {prior.overall_status})")
                m.readiness_status_post_render = prior.overall_status
                m.canonical_report = {"report_id": report_id,
                                      "source": (rec.get("details") or {}).get("report_source")}
                path, pm = EF.existing_post(out_dir, session_iso, report_id)
                m.video_artifact, m.audit_artifact = _artifacts_from_manifest(pm)
                m.warnings.extend(prior.warnings)
                m.finalize(ALREADY_COMPLETED)
                return write(m)
            m.finish_stage("IDEMPOTENCY_CHECK", STAGE_OK,
                           "a prior REPORT record exists but the artifact is not confirmed "
                           f"complete (readiness {prior.overall_status}) - running")
        else:
            m.finish_stage("IDEMPOTENCY_CHECK", STAGE_OK, "no prior completed run for this session")

    # ---- ACQUISITION_OR_REPORT (main.py --mode report; idempotent on its own)
    m.begin_stage("ACQUISITION_OR_REPORT")
    before = EF.report_records(out_dir)
    rc = runner(EF.REPORT_ARGS)
    m.runtime.setdefault("subprocess_calls", []).append({"args": EF.REPORT_ARGS, "exit_code": rc})
    _path, rec = EF.new_report_record(out_dir, before)
    fatal = EF.report_fatal(rc, rec, session_iso)
    if not fatal:
        from operations.sessions import latest_final_session
        latest = latest_final_session(clock(), calendar)
        if latest is not None and latest.isoformat() != session_iso:
            fatal = (f"the latest completed session moved to {latest} while the REPORT ran "
                     f"for {session_iso} - run the command again")
    if fatal:
        m.finish_stage("ACQUISITION_OR_REPORT", STAGE_BLOCKED, fatal)
        m.blocking_reasons.append(fatal)
        m.failure = {"stage": "ACQUISITION_OR_REPORT", "reason": fatal}
        m.finalize(BLOCKED)
        return write(m)
    report_id = rec["details"]["report_id"]
    m.canonical_report = {"report_id": report_id, "path": rec["details"].get("report_path"),
                          "source": rec["details"].get("report_source")}
    m.finish_stage("ACQUISITION_OR_REPORT", STAGE_OK,
                   f"{rec['run_status']} report_id={report_id}")

    # ---- PREFLIGHT_READINESS (PK-C)
    m.begin_stage("PREFLIGHT_READINESS")
    as_of_eff = as_of or clock()
    try:
        gate, report_path = readiness_fn(session, as_of_eff, out_dir, calendar, intent)
    except Exception as exc:
        reason = f"readiness gate could not evaluate ({type(exc).__name__}: {exc})"
        m.finish_stage("PREFLIGHT_READINESS", STAGE_FAILED, reason)
        m.failure = {"stage": "PREFLIGHT_READINESS", "reason": reason}
        m.finalize(FAILED)
        return write(m)
    m.readiness_status_preflight = gate.overall_status
    m.readiness_report = {"preflight_path": report_path}
    m.warnings.extend(gate.warnings)
    if gate.overall_status == "BLOCKED":
        m.finish_stage("PREFLIGHT_READINESS", STAGE_BLOCKED, "; ".join(gate.blocking_reasons))
        m.blocking_reasons.extend(gate.blocking_reasons)
        m.finalize(BLOCKED)
        return write(m)
    m.finish_stage("PREFLIGHT_READINESS", STAGE_OK, gate.decision)
    write(m)       # checkpoint: a crash during RENDERER resumes without re-evaluating readiness

    # ---- RENDERER (skip if a passing POST for this session+report already exists, unless --force)
    m.begin_stage("RENDERER")
    existing_path, existing_m = (None, None) if force else EF.existing_post(out_dir, session_iso,
                                                                            report_id)
    if existing_m is not None:
        rendered_now = False
        post_path, post_m = existing_path, existing_m
        m.finish_stage("RENDERER", STAGE_SKIPPED, "a passing POST manifest for this session "
                       "already exists - not re-rendered (use --force to override)")
    else:
        started = time.time() - 1
        prc = runner(EF.POST_ARGS)
        m.runtime["subprocess_calls"].append({"args": EF.POST_ARGS, "exit_code": prc})
        found = EF.post_manifests(out_dir, since=started)
        post_path, post_m = found[0] if found else (None, None)
        rendered_now = True
        m.finish_stage("RENDERER", STAGE_OK if prc == 0 else STAGE_FAILED, f"exit {prc}")
    fatal = EF.post_fatal(post_m, session_iso, report_id, rendered_now=rendered_now)
    m.video_artifact, m.audit_artifact = _artifacts_from_manifest(post_m)
    if fatal:
        m.blocking_reasons.append(fatal)
        m.failure = {"stage": "RENDERER", "reason": fatal}
        m.finalize(FAILED)
        return write(m)

    # ---- POST_RENDER_CHECK (PK-C) - the ONLY thing that can turn this into SUCCESS/DEGRADED
    m.begin_stage("POST_RENDER_CHECK")
    pr = post_render_fn(session, out_dir, intent)
    m.readiness_status_post_render = pr.overall_status
    m.warnings.extend(pr.warnings)
    if pr.overall_status == "BLOCKED":
        m.finish_stage("POST_RENDER_CHECK", STAGE_BLOCKED, "; ".join(pr.blocking_reasons))
        m.blocking_reasons.extend(pr.blocking_reasons)
        m.failure = {"stage": "POST_RENDER_CHECK", "reason": "the rendered edition failed QA"}
        m.finalize(FAILED)
        return write(m)
    m.finish_stage("POST_RENDER_CHECK", STAGE_OK, pr.decision)

    final = DEGRADED if DEGRADED in (m.readiness_status_preflight, pr.overall_status) else SUCCESS
    m.finalize(final)
    return write(m)


__all__ = ["EDITION", "run_post"]
