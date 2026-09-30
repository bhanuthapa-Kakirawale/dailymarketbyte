"""The ONE evening command (docs/USER_GUIDE.md): REPORT for session D, then the same-day
POST_UNIFIED for D, then one operator summary and VERDICT.

    python -m operations.evening_full [--rerender-post]        (scripts\\run_evening_full.bat)

    1. session context   now (IST), is today an NSE session, the latest FINAL session D
                         (operations.sessions.latest_final_session - no new calendar rule)
    2. REPORT            `python main.py --mode report` - exactly scripts\\run_evening.bat:
                         canonical report, private Radar, Market Structure, official snapshots.
                         Idempotent: a built session is not rebuilt, a validated snapshot is not
                         replaced.
    3. REPORT gate       the job record THIS invocation wrote must exist, be SUCCESS or DEGRADED
                         (DEGRADED = optional intelligence only) and describe D - else STOP:
                         no POST is rendered.
    4. POST              `python main.py --no-fetch-public` - scripts\\run_post.bat's command,
                         reading only what step 2 persisted (official snapshots are acquired once
                         per evening; the POST never runs the Radar). Skipped when a POST for D
                         already rendered and passed its checks (a rerun, a weekend) unless
                         --rerender-post.
    5. POST gate         the manifest the POST wrote must describe D, have reused D's canonical
                         report (REUSED_CANONICAL - never a second build), and pass video QA,
                         freeze-frame QA, the content scan and the audit (PASS or
                         RIGHTS_BLOCK_ONLY - the rights block is expected in the shadow trial).
    6. summary           VERDICT: PASS FOR SHADOW REVIEW (exit 0) | ATTENTION REQUIRED (exit 2)

The business logic stays in main.py / products/; this module only sequences the canonical
commands and reads the artifacts they wrote. It never uploads (no --upload is ever passed, and
--upload is refused), runs in the normal output tree only (refused inside a test run - use
scripts\\test_full_cycle.bat), and prints no secret.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import time

PASS, ATTENTION = "PASS FOR SHADOW REVIEW", "ATTENTION REQUIRED"
OK, STOP = 0, 2
REPORT_ARGS = ["--mode", "report"]           # scripts\run_evening.bat
POST_ARGS = ["--no-fetch-public"]            # scripts\run_post.bat + read persisted state only
ACCEPTABLE_REPORT = ("SUCCESS", "DEGRADED")
VALIDATED_SNAPSHOT = ("SUCCESS", "NO_DATA")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return None


def command(args: list) -> list:
    """The canonical entry point, in the normal environment (no output-root override)."""
    return [sys.executable, "main.py", *args]


def run_main(args: list) -> int:
    """Run one canonical command; its output streams straight to the console."""
    import config
    print(f"\n--- python main.py {' '.join(args)}".rstrip(), flush=True)
    return subprocess.call(command(args), cwd=config.BASE_DIR)


# --------------------------------------------------------------------------- session
def session_context(now: dt.datetime, calendar=None) -> dict:
    from core.trading_calendar import SessionCalendar
    from operations.sessions import latest_final_session
    cal = calendar or SessionCalendar()
    today = now.date()
    is_session = cal.is_session(today)
    latest = latest_final_session(now, cal)
    if latest is None:
        note = ("the trading calendar does not cover today - the REPORT job decides and the "
                "POST follows the session it built")
    elif latest == today:
        note = f"today ({today:%a %d %b}) is an NSE session and its close is final"
    elif is_session:
        note = (f"today ({today:%a %d %b}) is an NSE session but not final yet (before 15:40 "
                f"IST) - the last completed session is {latest:%a %d %b}")
    else:
        note = (f"today ({today:%a %d %b}) is NOT an NSE trading session - nothing is built "
                f"for today; the last completed session is {latest:%a %d %b}")
    return {"now": now.isoformat(timespec="seconds"), "today": today.isoformat(),
            "today_is_session": is_session, "session": latest.isoformat() if latest else None,
            "note": note}


# --------------------------------------------------------------------------- REPORT
def report_records(out_dir: str) -> set:
    return set(glob.glob(os.path.join(out_dir, "report_jobs", "*", "report_job_*.json")))


def new_report_record(out_dir: str, before: set) -> tuple:
    new = sorted(report_records(out_dir) - before, key=os.path.getmtime)
    return (new[-1], _load(new[-1]) or {}) if new else (None, None)


def _opt(ok: bool) -> str:
    return "PASS" if ok else "DEGRADED"


def report_rows(rec: dict) -> dict:
    """The per-component rows of one REPORT job record (optional parts never become fatal)."""
    det = rec.get("details") or {}
    radar = det.get("radar") or {}
    r_ok = radar.get("status") in ("BUILT", "ALREADY_BUILT") and \
        radar.get("pipeline_status") != "FAILED"
    sel = (radar.get("selection") or {}).get("selected_count")
    exch = {k: det.get(k) for k in ("fno_status", "asm_status", "gsm_status")}
    ipo = det.get("ipo_snapshot_status")
    return {
        "REPORT": (rec.get("run_status") or "NO_RECORD",
                   f"{det.get('report_source') or '-'} {det.get('report_id') or ''}".strip()),
        "PRIVATE RADAR": ("SKIPPED" if radar.get("status") == "SKIPPED" else
                          _opt(r_ok), f"{radar.get('status')}, pipeline "
                          f"{radar.get('pipeline_status') or '-'}"
                          + (f", {sel} selected (private)" if sel is not None else "")),
        "MARKET STRUCTURE": (_opt(bool(det.get("market_structure_snapshot"))),
                             "snapshot stored" if det.get("market_structure_snapshot")
                             else "no snapshot for this session"),
        "EXCHANGE WATCH": (_opt(det.get("exchange_snapshot_status") == "SUCCESS"),
                           f"F&O ban {exch['fno_status']} / ASM {exch['asm_status']} / "
                           f"GSM {exch['gsm_status']}"),
        "IPO WATCH": (_opt(ipo in VALIDATED_SNAPSHOT), f"IPO lists {ipo}"),
    }


def report_fatal(rc: int, rec: dict | None, expected: str | None) -> str | None:
    """Why the REPORT result stops the evening run, or None when POST may follow."""
    if rc != 0:
        return f"the REPORT job exited with code {rc}"
    if not rec:
        return "the REPORT job wrote no run record"
    status = rec.get("run_status")
    if status not in ACCEPTABLE_REPORT:
        why = rec.get("failure_stage") or rec.get("stage")
        return (f"REPORT {status}: {why}: {rec.get('failure_reason') or ''}"[:300]).rstrip(": ")
    if not rec.get("target_session"):
        return "the REPORT job did not resolve a session"
    if expected and rec["target_session"] != expected:
        return (f"the REPORT describes {rec['target_session']} but the latest completed session "
                f"is {expected}")
    if not (rec.get("details") or {}).get("report_id"):
        return "the REPORT record names no canonical report"
    return None


# --------------------------------------------------------------------------- POST
def post_manifests(out_dir: str, since: float | None = None) -> list:
    """[(path, manifest)] of normal POST runs, newest first (replays and demos excluded)."""
    out = []
    for p in glob.glob(os.path.join(out_dir, "post", "*", "production_manifest.json")):
        tag = os.path.basename(os.path.dirname(p))
        if tag.startswith("replay_") or tag.endswith("_DEMO"):
            continue
        if since is not None and os.path.getmtime(p) < since:
            continue
        m = _load(p)
        if m:
            out.append((os.path.getmtime(p), p, m))
    return [(p, m) for _, p, m in sorted(out, reverse=True)]


def existing_post(out_dir: str, session: str, report_id: str) -> tuple:
    """A POST for `session` from `report_id` that already rendered and passed its checks."""
    from operations.daily_check import post_content_verdict
    for p, m in post_manifests(out_dir):
        if m.get("session_date") == session and m.get("report_id") == report_id and \
                m.get("publication_profile") == "PUBLIC_UNREGISTERED" and \
                post_content_verdict(m)[0]:
            return p, m
    return None, None


def post_rows(m: dict) -> dict:
    from operations.daily_check import audit_verdict
    qa = m.get("qa") or {}
    pa = m.get("publication_audit") or {}
    verdict = audit_verdict(pa.get("final"), pa.get("failed_checks"))
    frames = (qa.get("frames_qa") or {}).get("passed")
    qa_ok = qa.get("video_qa") == "PASS" and bool(frames)
    content_ok = qa.get("content_qa") == "SAFE" and verdict in ("PASS", "RIGHTS_BLOCK_ONLY")
    rights = {"PASS": ("PASS", "audit PASS"),
              "RIGHTS_BLOCK_ONLY": ("BLOCKED", "rights review only - expected in the shadow "
                                               "trial (PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK)")}.get(
        verdict, ("BLOCKED", f"audit {pa.get('final')}: {', '.join(pa.get('failed_checks') or [])}"))
    return {"VIDEO QA": ("PASS" if qa_ok else "FAIL",
                         f"video {qa.get('video_qa')}, freeze-frame {frames}"),
            "CONTENT AUDIT": ("PASS" if content_ok else "FAIL",
                              f"content scan {qa.get('content_qa')}, audit -> {verdict}"),
            "PUBLICATION RIGHTS": rights}


def post_fatal(m: dict | None, session: str, report_id: str, *, rendered_now: bool) -> str | None:
    from operations.daily_check import post_content_verdict
    if not m:
        return ("the POST wrote no production manifest (blocked before rendering - see its "
                "output above)")
    if m.get("session_date") != session:
        return f"the POST describes {m.get('session_date')}, the REPORT built {session}"
    if m.get("report_id") != report_id:
        return f"the POST rendered report {m.get('report_id')}, the REPORT built {report_id}"
    if rendered_now and m.get("report_source") != "REUSED_CANONICAL":
        return (f"the POST did not reuse the evening's canonical report "
                f"(report_source {m.get('report_source')})")
    ok, verdict = post_content_verdict(m)
    if not ok:
        return f"POST QA / content failure (audit {verdict})"
    return None


# --------------------------------------------------------------------------- the run
def run_evening_full(*, clock=None, runner=None, out_dir: str | None = None, calendar=None,
                     rerender_post: bool = False) -> dict:
    """Run REPORT then POST for the latest completed session. Returns the result (see summary)."""
    import config
    from operations.sessions import latest_final_session
    out_dir = out_dir or config.OUT_DIR
    runner = runner or run_main
    clock = clock or config.now_ist
    ctx = session_context(clock(), calendar)
    res = {"context": ctx, "steps": [], "rows": {}, "notes": [], "fatal": None,
           "upload": "NOT ATTEMPTED (this command never uploads)"}
    print("=" * 64)
    print(f" EVENING FULL RUN  -  {ctx['now']} IST")
    print(f" output: {out_dir}")
    print(f" session: {ctx['note']}")
    print("=" * 64, flush=True)

    # ---- REPORT (exactly once)
    before = report_records(out_dir)
    rc = runner(REPORT_ARGS)
    res["steps"].append(("REPORT", rc))
    rec_path, rec = new_report_record(out_dir, before)
    res["report_record"] = rec_path
    if rec:
        res["rows"].update(report_rows(rec))
        res["session"] = rec.get("target_session")
        for d in (rec.get("details") or {}).get("degradations") or []:
            res["notes"].append(f"DEGRADED {d.get('source')}: {d.get('status')}")
    fatal = report_fatal(rc, rec, ctx["session"])
    if fatal:
        res["rows"]["REPORT"] = ("FAIL", fatal)
        res["rows"]["POST_UNIFIED"] = ("NOT RUN", "stopped: the REPORT did not complete")
        res["fatal"] = fatal
        return res
    session, report_id = rec["target_session"], rec["details"]["report_id"]
    res["rows"]["REPORT"] = ("PASS", f"{rec['run_status']} - {res['rows']['REPORT'][1]}")

    # ---- POST (at most once; not again for a session that already has a passing POST)
    path, m = (None, None) if rerender_post else existing_post(out_dir, session, report_id)
    if m is not None:
        res["rows"]["POST_UNIFIED"] = ("PASS", "ALREADY RENDERED for this session - not "
                                               "re-rendered (--rerender-post to force)")
        rendered_now = False
    else:
        # the session must not have moved on while the REPORT ran (a 15:40 boundary): the POST
        # resolves its session from the clock too, and must find the report just built
        latest = latest_final_session(clock(), calendar)
        if latest is not None and latest.isoformat() != session:
            res["fatal"] = (f"the latest completed session moved to {latest} while the REPORT "
                            f"ran for {session} - run the evening command again")
            res["rows"]["POST_UNIFIED"] = ("NOT RUN", res["fatal"])
            return res
        started = time.time() - 1
        prc = runner(POST_ARGS)
        res["steps"].append(("POST", prc))
        found = post_manifests(out_dir, since=started)
        path, m = found[0] if found else (None, None)
        rendered_now = True
        if prc != 0:
            res["fatal"] = f"the POST exited with code {prc}"
    res["post_manifest"] = path
    fatal = res["fatal"] or post_fatal(m, session, report_id, rendered_now=rendered_now)
    if m:
        res["rows"].update(post_rows(m))
        res["video"] = m.get("video")
        res["duration"] = m.get("duration")
        if rendered_now:
            res["rows"]["POST_UNIFIED"] = ("PASS" if not fatal else "FAIL",
                                           f"RENDERED from {m.get('report_source')}")
        for k, v in (m.get("optional_sections") or {}).items():
            res["notes"].append(f"{k}: {(v or {}).get('code')}")
        upload = str(m.get("upload") or "")
        if upload and not upload.startswith(("NOT_ATTEMPTED", "REFUSED")):
            res["upload"] = upload
    else:
        res["rows"]["POST_UNIFIED"] = ("FAIL", fatal)
    res["fatal"] = fatal
    return res


def verdict(res: dict) -> str:
    return ATTENTION if res.get("fatal") else PASS


def print_summary(res: dict) -> None:
    ctx = res["context"]
    print("\n" + "=" * 64)
    print(" EVENING FULL RUN - SUMMARY")
    print(f"  {'session':<20} {res.get('session') or ctx.get('session') or '-'}")
    print(f"  {'context':<20} {ctx['note']}")
    order = ("REPORT", "PRIVATE RADAR", "MARKET STRUCTURE", "EXCHANGE WATCH", "IPO WATCH",
             "POST_UNIFIED", "VIDEO QA", "CONTENT AUDIT", "PUBLICATION RIGHTS")
    for k in order:
        if k in res["rows"]:
            st, detail = res["rows"][k]
            print(f"  {k:<20} {st:<9} {detail or ''}".rstrip())
    if res.get("video"):
        print(f"  {'POST video':<20} {res['video']}")
    if isinstance(res.get("duration"), (int, float)):
        print(f"  {'duration':<20} {res['duration']:.1f}s")
    for n in res.get("notes") or []:
        print(f"  {'note':<20} {n}")
    if res.get("fatal"):
        print(f"  {'STOPPED':<20} {res['fatal']}")
    print(f"  {'upload':<20} {res['upload']}")
    print("=" * 64)
    print(f"VERDICT: {verdict(res)}")
    print("UPLOAD: NOT ATTEMPTED")
    if res.get("fatal"):
        print("See docs\\USER_GUIDE.md section 6; scripts\\check_daily_run.bat re-reads the "
              "artifacts without running anything.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m operations.evening_full",
                                 description="evening REPORT + same-day POST (never uploads)")
    ap.add_argument("--rerender-post", action="store_true",
                    help="render the POST again even if this session already has a passing one")
    ap.add_argument("--upload", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.upload:
        print("Refused: the evening command never uploads. Nothing was run.")
        return STOP
    from operations.run_context import is_test
    if is_test():
        print("Refused: this is the NORMAL daily command; a test run uses "
              "scripts\\test_full_cycle.bat (docs\\TESTING_GUIDE.md). Nothing was run.")
        return STOP
    res = run_evening_full(rerender_post=args.rerender_post)
    print_summary(res)
    return STOP if res.get("fatal") else OK


__all__ = ["run_evening_full", "session_context", "report_rows", "report_fatal", "post_rows",
           "post_fatal", "existing_post", "post_manifests", "command", "REPORT_ARGS",
           "POST_ARGS", "PASS", "ATTENTION", "main"]


if __name__ == "__main__":
    sys.exit(main())
