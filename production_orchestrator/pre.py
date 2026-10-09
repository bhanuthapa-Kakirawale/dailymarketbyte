"""PK-D PRE orchestration: ONE command for the "before the bell" shadow run.

Sequences the EXISTING command `python main.py --mode premarket --shadow` with a run lock,
idempotency (new - today's `run_morning_pre.bat` always re-renders), `--resume`, and the PK-C
readiness gate at both PREFLIGHT and POST_RENDER. PRE never uploads (V1), so `--force`/`--resume`
only ever affect PK-D's own bookkeeping, never a flag reaching main.py beyond the ones this
module always passes.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket

from . import manifest as MF
from .lock import LockConflict, RunLock
from .models import (ALREADY_COMPLETED, BLOCKED, DEGRADED, FAILED, LOCKED, SHADOW, SKIPPED,
                     STAGE_BLOCKED, STAGE_FAILED, STAGE_OK, STAGE_SKIPPED, SUCCESS, RunManifest)

EDITION = "PRE"


def _default_runner(args: list) -> int:
    import subprocess
    import sys

    import config
    print(f"\n--- python main.py {' '.join(args)}", flush=True)
    return subprocess.call([sys.executable, "main.py", *args], cwd=config.BASE_DIR)


def _default_pre_readiness(pre_date: dt.date, as_of: dt.datetime, out_dir: str, calendar,
                           intent: str, mode: str = "LIVE"):
    """`mode="REPLAY"` (an explicit --session-date/--as-of was given) never fetches the
    overnight cues - PRE's LIVE mode is the only readiness call in this whole gate that touches
    the network, so getting this switch wrong would silently turn a REPLAY into a live fetch."""
    from readiness.pre import evaluate_pre
    from readiness.report import write_report
    res = evaluate_pre(pre_date, as_of, mode=mode, intent=intent, out_dir=out_dir,
                       calendar=calendar)
    path = None
    if mode == "LIVE":
        try:
            path = write_report(res, out_dir)
        except Exception as exc:    # the verdict stands; the file is diagnostics only
            res.notes.append(f"readiness report not written: {type(exc).__name__}: {exc}")
    return res, path


def _default_post_render(pre_date: dt.date, out_dir: str, intent: str):
    from readiness.post_render import evaluate_post_render
    return evaluate_post_render("PRE", pre_date, out_dir=out_dir, intent=intent)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _shadow_manifest(out_dir: str, pre_date: dt.date) -> dict | None:
    return _load_json(os.path.join(out_dir, "pre_shadow", pre_date.isoformat(),
                                   "shadow_manifest.json"))


def _fill_pre_artifacts(m: RunManifest, out_dir: str, pre_date: dt.date) -> None:
    sm = _shadow_manifest(out_dir, pre_date) or {}
    artifacts = sm.get("artifacts") or {}
    video = artifacts.get("mp4")
    qa = sm.get("qa") or {}
    m.video_artifact = {"path": video, "duration": qa.get("total_duration"),
                        "exists": bool(video and os.path.exists(video))}
    run_dir = os.path.join(out_dir, "pre_shadow", pre_date.isoformat())
    result = _load_json(os.path.join(run_dir, f"pre_result_{pre_date}.json")) or {}
    pa = result.get("publication_audit") or {}
    m.audit_artifact = {"path": None, "final": pa.get("final"),
                        "failed_checks": pa.get("failed_checks")}


def run_pre(*, session_date: dt.date | None = None, as_of: dt.datetime | None = None,
           intent: str = SHADOW, resume: bool = False, force: bool = False,
           no_write: bool = False, out_dir: str | None = None, runner=None,
           readiness_fn=None, post_render_fn=None, clock=None, calendar=None,
           cli_argv: list | None = None) -> RunManifest:
    import config
    from core.freshness import IST
    from core.trading_calendar import SessionCalendar
    from products.premarket import DEFAULT_CUTOFF

    out_dir = out_dir or config.OUT_DIR
    runner = runner or _default_runner
    readiness_fn = readiness_fn or _default_pre_readiness
    post_render_fn = post_render_fn or _default_post_render
    clock = clock or config.now_ist
    intent = (intent or SHADOW).upper()
    cli_argv = list(cli_argv or [])
    cal = calendar or SessionCalendar()

    # LIVE iff neither session_date nor as_of was given by the caller (readiness.__main__
    # .evaluate's own rule) - REPLAY never touches the network for the overnight cues.
    readiness_mode = "REPLAY" if (session_date is not None or as_of is not None) else "LIVE"

    # `products.premarket.run_premarket`'s own pre_date/as_of resolution, reused exactly.
    as_of_given = as_of
    as_of_eff = as_of or clock()
    if as_of_eff.tzinfo is None:
        as_of_eff = as_of_eff.replace(tzinfo=IST)
    pre_date = session_date or as_of_eff.date()
    if session_date and as_of_given is None:
        as_of_eff = dt.datetime.combine(pre_date, DEFAULT_CUTOFF, tzinfo=IST)
    pre_date_iso = pre_date.isoformat()
    now = clock()

    def _write(manifest: RunManifest) -> RunManifest:
        if not no_write:
            MF.write_manifest(out_dir, manifest)
        return manifest

    def _new_manifest(run_id: str) -> RunManifest:
        return RunManifest(edition=EDITION, command="pre", run_id=run_id, session_date=pre_date_iso,
                           intent=intent, forced=force, no_write=no_write,
                           git_commit=MF.git_commit(config.BASE_DIR),
                           host={"hostname": socket.gethostname(), "pid": os.getpid()},
                           cli={"argv": cli_argv, "resume": resume, "force": force,
                                "as_of": as_of_eff.isoformat()})

    # ---- SESSION_RESOLUTION: a non-session day is SKIPPED, never a new blocking behaviour
    if cal.is_session(pre_date) is False:
        m = _new_manifest(MF.new_run_id(now))
        m.begin_stage("SESSION_RESOLUTION")
        m.finish_stage("SESSION_RESOLUTION", STAGE_SKIPPED,
                       f"{pre_date} is not an NSE trading session - no PRE edition")
        m.finalize(SKIPPED)
        return _write(m)

    # ---- RUN_LOCK (+ --resume: reuse a leftover RUNNING manifest's run_id)
    resumable = MF.running_manifest_to_resume(out_dir, EDITION, pre_date_iso) if resume else None
    run_id = resumable["run_id"] if resumable else MF.new_run_id(now)
    m = _new_manifest(run_id)
    if resumable:
        m.resumed_from_run_id = run_id
        m.started_at = resumable.get("started_at") or m.started_at
    m.begin_stage("RUN_LOCK")
    lock = RunLock(out_dir, EDITION, pre_date_iso, now=lambda: dt.datetime.now(dt.timezone.utc))
    try:
        reclaimed = lock.acquire(run_id, intent, "pre", cli_argv)
    except LockConflict as exc:
        m.finish_stage("RUN_LOCK", STAGE_FAILED, str(exc))
        m.failure = {"stage": "RUN_LOCK", "reason": str(exc)}
        m.finalize(LOCKED)
        return _write(m)
    if reclaimed:
        m.reclaimed_stale_lock = reclaimed
    m.finish_stage("RUN_LOCK", STAGE_OK, f"acquired for {EDITION} {pre_date_iso}")
    _write(m)      # a RUNNING checkpoint - what a later --resume would find after a crash

    try:
        return _run_pre_locked(m, pre_date=pre_date, pre_date_iso=pre_date_iso, as_of=as_of_eff,
                               intent=intent, force=force, out_dir=out_dir, runner=runner,
                               readiness_fn=readiness_fn, post_render_fn=post_render_fn,
                               calendar=cal, resumable=resumable, write=_write,
                               readiness_mode=readiness_mode)
    finally:
        lock.release()


def _run_pre_locked(m, *, pre_date, pre_date_iso, as_of, intent, force, out_dir, runner,
                    readiness_fn, post_render_fn, calendar, resumable, write, readiness_mode):
    # ---- IDEMPOTENCY_CHECK
    m.begin_stage("IDEMPOTENCY_CHECK")
    if force:
        m.finish_stage("IDEMPOTENCY_CHECK", STAGE_SKIPPED, "--force")
    else:
        sm = _shadow_manifest(out_dir, pre_date)
        if sm is not None and sm.get("run_status") in ("SUCCESS", "DEGRADED"):
            prior = post_render_fn(pre_date, out_dir, intent)
            if prior.overall_status in ("READY", "DEGRADED"):
                m.finish_stage("IDEMPOTENCY_CHECK", STAGE_OK,
                               f"already completed (readiness {prior.overall_status})")
                m.readiness_status_post_render = prior.overall_status
                m.warnings.extend(prior.warnings)
                _fill_pre_artifacts(m, out_dir, pre_date)
                m.finalize(ALREADY_COMPLETED)
                return write(m)
            m.finish_stage("IDEMPOTENCY_CHECK", STAGE_OK,
                           "a prior PRE run exists but is not confirmed complete "
                           f"(readiness {prior.overall_status}) - running")
        else:
            m.finish_stage("IDEMPOTENCY_CHECK", STAGE_OK, "no prior completed run for this session")

    # ---- PREFLIGHT_READINESS (PK-C). Resume optimization: a leftover RUNNING manifest for the
    # SAME as_of already paid for the network-touching overnight-cue probe - reuse its verdict
    # rather than re-touching the network.
    m.begin_stage("PREFLIGHT_READINESS")
    reused_cli = (resumable or {}).get("cli") or {}
    can_reuse = bool(resumable and resumable.get("readiness_status_preflight")
                     and reused_cli.get("as_of") == as_of.isoformat())
    if can_reuse:
        status = resumable["readiness_status_preflight"]
        m.readiness_status_preflight = status
        m.readiness_report = resumable.get("readiness_report")
        m.warnings.extend(resumable.get("warnings") or [])
        m.finish_stage("PREFLIGHT_READINESS", STAGE_OK,
                       f"reused from run {resumable['run_id']} (--resume, same as_of, no new "
                       "network calls)")
        if status == "BLOCKED":
            m.blocking_reasons.extend(resumable.get("blocking_reasons") or [])
            m.finalize(BLOCKED)
            return write(m)
    else:
        try:
            gate, report_path = readiness_fn(pre_date, as_of, out_dir, calendar, intent,
                                             mode=readiness_mode)
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

    # ---- RENDERER (one subprocess call - PRE has no separate acquire/build checkpoint)
    m.begin_stage("RENDERER")
    args = ["--mode", "premarket", "--shadow", "--session-date", pre_date_iso,
           "--as-of", as_of.isoformat()]
    prc = runner(args)
    m.runtime.setdefault("subprocess_calls", []).append({"args": args, "exit_code": prc})
    m.finish_stage("RENDERER", STAGE_OK if prc == 0 else STAGE_FAILED, f"exit {prc}")

    # ---- POST_RENDER_CHECK (PK-C) - the ONLY thing that can turn this into SUCCESS/DEGRADED
    m.begin_stage("POST_RENDER_CHECK")
    pr = post_render_fn(pre_date, out_dir, intent)
    m.readiness_status_post_render = pr.overall_status
    m.warnings.extend(pr.warnings)
    _fill_pre_artifacts(m, out_dir, pre_date)
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


__all__ = ["EDITION", "run_pre"]
