"""REPORT job: build the canonical MarketReport for trading session D after the close - no video.

    python main.py --mode report                         # the latest FINAL session
    python main.py --mode report --session-date D        # D must be that session (or already built)
    python main.py --mode report --skip-radar            # report + intelligence only

    resolve D  (core.trading_calendar: today once 15:40 IST has passed, else the previous session)
      -> a valid canonical report for D already exists?  yes -> ALREADY_BUILT, nothing acquired
      -> main.produce_report  (the ONE report-building path, shared with POST)
           acquisition must describe exactly D (an older benchmark session is a hard stop)
           a report that fails data validation is NOT committed (reports/unfit/, retryable)
      -> historical intelligence snapshot (derived, never fatal)
      -> Market Radar for D (idempotent: skipped when D's Radar artifacts already exist)
      -> run record: publication_runs job_type REPORT_BUILD + output/report_jobs/<D>/

Idempotent and safe to retry: a rerun for a built session writes no canonical row; the report
JSON is written once, ever; Radar persistence is keyed on the session. A POST re-render or a PRE
run never modifies what this job persisted.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from types import SimpleNamespace

REPORT_JOB_MODE = "REPORT_BUILD"
REPORT_JOB_DEMO_MODE = "REPORT_BUILD_DEMO"
REPORT_JOB_VERSION = "report-job-1.0"
SUCCESS, DEGRADED, BLOCKED, FAILED = "SUCCESS", "DEGRADED", "BLOCKED", "FAILED"


def _radar_paths(out_dir: str, session: dt.date) -> tuple:
    radar_dir = os.path.join(out_dir, "radar")
    return (os.path.join(radar_dir, f"daily_radar_{session}.json"),
            os.path.join(radar_dir, "presentation", f"radar_presentation_{session}.json"))


def existing_radar(out_dir: str, session: dt.date) -> dict | None:
    """D's Radar artifacts if they already exist and did not fail - a rerun does not redo them."""
    res_path, pres_path = _radar_paths(out_dir, session)
    if not (os.path.exists(res_path) and os.path.exists(pres_path)):
        return None
    try:
        with open(res_path, encoding="utf-8") as fh:
            status = json.load(fh).get("pipeline_status")
    except (OSError, ValueError):
        return None
    if status == "FAILED":
        return None
    return {"status": "ALREADY_BUILT", "pipeline_status": status, "result": res_path,
            "presentation": pres_path}


def run_radar(session: dt.date, out_dir: str, as_of: dt.datetime) -> dict:
    """Production Radar for `session` + its presentation plan (the artifacts POST and PRE read).
    Never raises: a Radar failure degrades the job, it never removes the report."""
    from radar.daily_pipeline import run_daily_radar
    from radar.presentation_planner import build_radar_presentation, save_presentation
    try:
        result = run_daily_radar(session_date=session, out_dir=out_dir, as_of=as_of)
        res_path, _ = _radar_paths(out_dir, session)
        if result.pipeline_status == "FAILED":
            return {"status": "FAILED", "pipeline_status": result.pipeline_status,
                    "issues": [i.to_dict() for i in result.issues][:10]}
        pres = build_radar_presentation(result, as_of=as_of)
        pres_path = save_presentation(pres, out_dir=out_dir)
        return {"status": "BUILT", "pipeline_status": result.pipeline_status,
                "stories": result.editorial_selection_count, "result": res_path,
                "presentation": pres_path}
    except Exception as exc:
        return {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}


def _readiness(report) -> dict:
    """What a consumer needs to know about the report's data readiness."""
    if report is None:
        return {"publication_ready": False}
    summary = report.validation_summary
    meta = report.metadata or {}
    cov = meta.get("movers_coverage") or {}
    align = meta.get("session_alignment") or {}
    return {"publication_ready": summary.publication_ready,
            "blocking_issues": list(summary.blocking_issues),
            "movers_coverage_pct": cov.get("coverage_pct"),
            "fallbacks": [f"{r.get('index')} {r.get('session_date')}: {r.get('source')} "
                          f"{r.get('validation_status')}"
                          for r in (align.get("benchmark_recovery") or [])
                          + (align.get("index_recovery") or [])]}


def resolve_session(session_date: dt.date | None, now: dt.datetime, calendar=None) -> tuple:
    """(session, None) or (None, (code, message)). Never a date whose close is not final."""
    from core.trading_calendar import SessionCalendar
    from operations.sessions import latest_final_session
    cal = calendar or SessionCalendar()
    latest = latest_final_session(now, cal)
    if session_date is None:
        if latest is None:
            return None, ("SESSION_UNKNOWN", "the trading calendar does not cover today - pass "
                                             "--session-date or extend core/trading_calendar.py")
        return latest, None
    if cal.is_session(session_date) is False:
        return None, ("NOT_A_SESSION", f"{session_date} is not an NSE trading session")
    if latest is not None and session_date > latest:
        return None, ("SESSION_NOT_FINAL", f"{session_date} is not final yet (latest final "
                                           f"session: {latest}) - never built mid-session")
    return session_date, None


def run_report_job(session_date: dt.date | None = None, *, demo: bool = False,
                   now: dt.datetime | None = None, skip_radar: bool = False,
                   radar_fn=None, calendar=None) -> dict:
    """The REPORT job. Returns the run record (also written to output/report_jobs/<D>/)."""
    import config
    import main
    from operations.report_lookup import (ARTIFACT_MISSING, SESSION_MISMATCH, UNREADABLE,
                                          find_canonical_report)
    from storage import JOB_REPORT_BUILD, MarketHistory, default_db_path

    out_dir = config.OUT_DIR
    now = now or main.now_ist()
    radar_fn = radar_fn or run_radar
    record = {"version": REPORT_JOB_VERSION, "job_type": JOB_REPORT_BUILD,
              "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "as_of": now.isoformat(),
              "demo": demo}
    try:
        history = MarketHistory(default_db_path(out_dir))
    except Exception as exc:
        # Persistence is a precondition of a canonical report: without history, build nothing.
        record.update(run_status=FAILED, stage="HISTORY_UNAVAILABLE",
                      failure_reason=f"{type(exc).__name__}: {exc}")
        print(f"REPORT job failed: history unavailable ({exc})")
        return record

    session, problem = (None, None) if demo else resolve_session(session_date, now, calendar)
    run_id = history.start_run(REPORT_JOB_DEMO_MODE if demo else REPORT_JOB_MODE,
                               job_type=JOB_REPORT_BUILD, target_date=session,
                               source_session_date=session, stage="STARTED")
    record.update(run_id=run_id, target_session=session.isoformat() if session else None)

    def close(run_status, stage, publication_status="NOT_APPLICABLE", **fields):
        details = fields.pop("details", {})
        record.update(run_status=run_status, stage=stage,
                      completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                      **{k: (v.isoformat() if isinstance(v, dt.date) else v)
                         for k, v in fields.items()}, details=details)
        try:
            history.finish_run(run_id, stage, publication_status, run_status=run_status,
                               details=details, **fields)
        except Exception as exc:
            record["history_error"] = f"{type(exc).__name__}: {exc}"
        return _write_record(record, out_dir)

    try:
        if problem:
            code, message = problem
            print(f"REPORT job blocked - {code}: {message}")
            return close(BLOCKED, "SESSION_RESOLUTION", failure_stage=code,
                         failure_reason=message)

        report = report_path = None
        source = None
        if not demo:
            lookup = find_canonical_report(session, history=history)
            record["lookup"] = lookup.to_dict()
            if lookup.found:
                report, report_path, source = lookup.report, lookup.path, "ALREADY_BUILT"
                print(f"REPORT {session}: canonical report {lookup.report_id} already exists - "
                      "nothing re-acquired, nothing re-written")
                history.update_run(run_id, report_id=lookup.report_id, stage="REPORT_REUSED",
                                   artifact_path=lookup.path)
            elif lookup.status in (ARTIFACT_MISSING, UNREADABLE, SESSION_MISMATCH):
                print(f"REPORT job blocked: canonical record for {session} is unusable: "
                      f"{lookup.reason}")
                return close(BLOCKED, "CANONICAL_ARTIFACT", failure_stage="CANONICAL_ARTIFACT",
                             failure_reason=lookup.reason)

        if report is None:
            args = SimpleNamespace(demo=demo, upload=False, force=False)
            from operations.sessions import edition_date_for
            edition = main.now_ist().date() if demo else edition_date_for(session, calendar)
            try:
                outcome = main.produce_report(args, edition, history, run_id,
                                              expected_session=session, morning_facts=demo)
            except main.market.SessionAlignmentError as exc:
                record.update(run_status=BLOCKED, stage="FAILED", failure_stage="SESSION_ALIGNMENT",
                              failure_reason=str(exc)[:1000])
                return _write_record(record, out_dir)
            if not outcome.ok:
                # produce_report has closed the run; mirror its verdict into the job record
                stored = [r for r in history.get_publication_runs(limit=50) if r.run_id == run_id]
                r = stored[0] if stored else None
                record.update(run_status=(r.run_status if r else BLOCKED) or BLOCKED,
                              stage=r.stage if r else outcome.status,
                              failure_stage=r.failure_stage if r else outcome.status,
                              failure_reason=outcome.reason, diagnostic_artifact=outcome.report_path,
                              completed_at=dt.datetime.now(dt.timezone.utc).isoformat())
                return _write_record(record, out_dir)
            report, report_path, source = outcome.report, outcome.report_path, outcome.source
            session = session or report.session_date
            record["target_session"] = session.isoformat()

        readiness = _readiness(report)
        snapshot = main.build_intelligence(report, history, demo=demo)
        radar = {"status": "SKIPPED", "reason": "--skip-radar"} if skip_radar else (
            {"status": "SKIPPED", "reason": "demo"} if demo else
            existing_radar(out_dir, session) or radar_fn(session, out_dir, now))
        # RADAR_SELECTED only: this job never publishes a Radar story and never advances the
        # publication cooldown - only a completed, QA-passed POST artifact does
        # (products.radar_publication).
        if not demo and radar.get("status") not in ("SKIPPED", "FAILED"):
            from products.radar_publication import radar_selection
            sel = radar_selection(session, out_dir)
            radar["selection"] = {"selected_count": sel["selected_count"],
                                  "selected_symbols": sel["selected_symbols"],
                                  "published_count": 0, "published_symbols": [],
                                  "note": "REPORT_BUILD selects; it never publishes"}
        degraded = []
        if radar.get("status") == "FAILED":
            degraded.append({"source": "MARKET_RADAR", "status": "FAILED",
                             "reason": radar.get("error") or radar.get("issues")})
        if snapshot is None:
            degraded.append({"source": "INTELLIGENCE", "status": "FAILED",
                             "reason": "historical intelligence snapshot not produced"})
        # an unfit report can no longer become canonical (main.may_become_canonical); only a
        # row persisted before that rule could be unfit - immutable, so reported, never rebuilt
        status = (BLOCKED if not readiness["publication_ready"] and not demo
                  else DEGRADED if degraded else SUCCESS)
        print(f"REPORT {session}: {status} ({source}) -> {report_path}; radar {radar.get('status')}")
        return close(status, "REPORT_READY" if status != BLOCKED else "DATA_QA",
                     report_id=report.report_id, artifact_path=report_path,
                     data_qa_status="PASSED" if readiness["publication_ready"] else "FAILED",
                     failure_stage=None if status != BLOCKED else "DATA_QA",
                     failure_reason=None if status != BLOCKED else
                     "; ".join(readiness["blocking_issues"]),
                     target_date=report.session_date, source_session_date=report.session_date,
                     details={"report_source": source, "report_id": report.report_id,
                              "report_path": report_path, "readiness": readiness,
                              "radar": radar, "intelligence": bool(snapshot),
                              "degradations": degraded, "outputs": {
                                  "report": report_path, "radar": radar.get("result"),
                                  "radar_presentation": radar.get("presentation")}})
    except Exception as exc:
        main._fail_open_run(history, run_id, exc)
        record.update(run_status=FAILED, stage="EXCEPTION",
                      failure_reason=f"{type(exc).__name__}: {exc}")
        _write_record(record, out_dir)
        raise
    finally:
        history.close()


def _write_record(record: dict, out_dir: str) -> dict:
    session = record.get("target_session") or "unresolved"
    folder = os.path.join(out_dir, "report_jobs", session)
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"report_job_{record.get('run_id') or 'norun'}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False, default=str)
        record["record_path"] = path
    except OSError as exc:
        record["record_error"] = str(exc)
    return record


__all__ = ["run_report_job", "resolve_session", "existing_radar", "run_radar",
           "REPORT_JOB_MODE", "REPORT_JOB_DEMO_MODE"]
