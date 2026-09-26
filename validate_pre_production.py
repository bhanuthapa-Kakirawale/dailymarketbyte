"""PRE production readiness pack -> output/pre_production_readiness/.

    python validate_pre_production.py                       # everything below
    python validate_pre_production.py --connectivity-only   # just the reachability diagnostic
    python validate_pre_production.py --no-live             # skip the live sandbox runs

Writes:
    production_schedule.md          docs/PRODUCTION_SCHEDULE.md + this run's live status
    job_dependency_graph.txt        REPORT -> POST / PRE dependencies and order
    gift_publication_policy.json    the gate as configured here + the rights-review status
    connectivity_diagnostic.json    reachability from THIS machine (GitHub: connectivity.yml)
    official_events_check.json      verification/coverage early warning
    scenario_validation.json        previous-session resolution scenarios (real history, read-only)
    run_history_audit.json          REPORT/POST/PRE run rows (sandbox) + legacy rows (real, read-only)
    shadow_run_template/            per-morning checklist, manifest template, GIFT log header
    sandbox/                        a COPY of output state; live runs happen only here

Live runs (sandbox only, via DAILY_BYTE_OUT - the real canonical history is never written):
    REPORT job for the latest final session, then again (idempotency)
    PRE shadow reconstruction of this morning (GIFT fetched now -> audited, never displayed)
    PRE publication-mode frames-only run (GIFT not fetched: policy disabled)
    PRE shadow for a session whose previous report is missing (BLOCKED)
Nothing uploads, nothing is committed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

import config
from config import IST

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(config.OUT_DIR, "pre_production_readiness")
STATE_DIRS = ("data", "reports", "radar", "intelligence")


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    return path


# --------------------------------------------------------------------------- static outputs
DEPENDENCY_GRAPH = """\
DAILY MARKET BYTE - JOB DEPENDENCY GRAPH (times IST; D = trading session, D+1 = next session)

                  canonical trading calendar (core/trading_calendar.py)
                               |
   15:40 D  SESSION_FINAL_TIME |  bars for D are final
                               v
   19:30 D  +---------------------------------------------+   21:30 D retry (no-op if built)
            | REPORT_BUILD  python main.py --mode report  |
            |  resolve D -> exists? ALREADY_BUILT          |
            |  else main.produce_report (acquire->validate)|
            |  fit   -> MarketReport(D) CANONICAL (JSON+DB)|
            |  unfit -> reports/unfit/ (retryable)          |
            |  -> intelligence snapshot (derived)           |
            |  -> Market Radar(D) artifacts (idempotent)    |
            +---------------------------------------------+
                  |  MarketReport(D) + Radar(D)   (read-only from here on)
          +-------+------------------------------+
          v                                      v
   07:30 D+1  PRE_MARKET (shadow)          07:40 D+1  POST_MARKET
   python main.py --mode premarket          python main.py --upload
     --shadow                                 lookup MarketReport(D)
   require_previous_report(D+1):               found   -> REUSED_CANONICAL
     prev = calendar.previous_session(D+1)     missing -> produce_report (BUILT_INLINE,
     report(prev) exact + publication-ready               same code path, recorded)
     else BLOCKED (never an older one)         render -> data/content/video QA -> upload
   + live pre-open data (US, Asia, VIX,
     GIFT: shadow=fetch+audit, display only
     if GIFT_NIFTY_PUBLICATION_ENABLED+approval)
   -> plan -> render -> QA
   -> output/pre_shadow/D+1/  (never uploads)

Ordering constraints
  REPORT(D)  must finish before PRE(D+1)      (PRE BLOCKS without it)
  REPORT(D)  should finish before POST(D+1)   (POST falls back to building it inline)
  PRE and POST never write MarketReport(D); each appends its own publication_runs row
  All state-touching GitHub jobs share concurrency group `daily-byte-state` (serialised)
"""

SHADOW_README = """\
# PRE shadow week - per-morning checklist

Run: `python main.py --mode premarket --shadow` (GitHub: pre_shadow.yml, 07:30 IST).
Nothing is uploaded. Target: 5 consecutive trading sessions.

For each morning, open `output/pre_shadow/<D>/shadow_manifest.json` and record in the log:

1. run_status (SUCCESS / DEGRADED / BLOCKED / FAILED / SKIPPED) and, if not SUCCESS, the reason
2. previous_session is the canonical session before D (Monday -> Friday; after a holiday -> the
   last session before it)
3. missing_artifacts is empty (plan, provenance, GIFT audit, run record, MP4, QA result)
4. qa.ok true, content_safety SAFE, no language_issues, probed_duration within the PRE ceiling
5. degradations: each one expected? (GitHub runners may not reach NSE IX -> GIFT DEGRADED)
6. GIFT (gift_audit_<D>.json): contract / expiry / live price / exchange timestamp / previous
   settlement / calculated % / exchange change / reconciles_with_settlement / freshness / verdict
   -> copy the line into gift_validation_log.csv. gift_displayed must be false.
7. Watch the MP4 once: numbers match the brief, no GIFT strip, disclaimer present.

Pass criteria for the week: 5/5 runs rendered (SUCCESS or explained DEGRADED), 0 FAILED,
0 unexplained BLOCKED, every morning's GIFT reading reconciles with its settlement (or its
failure is recorded), and no displayed fact with AI provenance.
"""

MANIFEST_TEMPLATE = {
    "shadow": True, "never_uploaded": True, "pre_date": "YYYY-MM-DD",
    "previous_session": "YYYY-MM-DD", "run_id": "run_...",
    "run_status": "SUCCESS | DEGRADED | BLOCKED | FAILED | SKIPPED",
    "stage": "RENDERED", "failure_stage": None, "failure_reason": None,
    "artifacts": {"pre_plan": "pre_section_plan_<D>.json", "provenance": "pre_provenance_<D>.json",
                  "gift_audit": "gift_audit_<D>.json", "run_record": "pre_run_<D>.json",
                  "mp4": "full_pre_<D>.mp4", "qa_result": "pre_result_<D>.json",
                  "brief": "pre_brief_<D>.json", "acquisition": "pre_acquisition_<D>.json",
                  "contact_sheet": "pre_contact_sheet_<D>.png"},
    "missing_artifacts": [],
    "qa": {"ok": True, "blocked": None, "content_safety": "SAFE", "language_issues": [],
           "freeze_frame_qa": True, "probed_duration": 0.0, "total_duration": 0.0},
    "omitted": [{"section": "GIFT NIFTY", "item": "GIFT NIFTY", "reason": "publication policy: ..."}],
    "degradations": [{"source": "...", "status": "...", "reason": "..."}],
    "gift": {"gift_data_available": True, "gift_data_valid": True,
             "gift_publication_allowed": False, "gift_displayed": False, "reason": "..."},
}

GIFT_LOG_HEADER = ("pre_date,run_status,contract,expiry,live_price,exchange_timestamp,"
                   "previous_settlement,settlement_date,calculated_pct,exchange_day_change,"
                   "exchange_pct_change,reconciles_with_settlement,freshness,validation_status,"
                   "gift_displayed,notes\n")


def write_static(out: str) -> dict:
    src = os.path.join(ROOT, "docs", "PRODUCTION_SCHEDULE.md")
    with open(src, encoding="utf-8") as fh:
        doc = fh.read()
    stamp = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    with open(os.path.join(out, "production_schedule.md"), "w", encoding="utf-8") as fh:
        fh.write(f"<!-- generated {stamp} from docs/PRODUCTION_SCHEDULE.md -->\n\n" + doc)
    with open(os.path.join(out, "job_dependency_graph.txt"), "w", encoding="utf-8") as fh:
        fh.write(DEPENDENCY_GRAPH)
    tpl = os.path.join(out, "shadow_run_template")
    os.makedirs(tpl, exist_ok=True)
    with open(os.path.join(tpl, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(SHADOW_README)
    _dump(os.path.join(tpl, "shadow_manifest.template.json"), MANIFEST_TEMPLATE)
    with open(os.path.join(tpl, "gift_validation_log.csv"), "w", encoding="utf-8") as fh:
        fh.write(GIFT_LOG_HEADER)
    from operations.gift_policy import (ENV_APPROVAL, ENV_FLAG, gift_publication_policy)
    from core.sources import SRC_NSEIX_DSP, SRC_NSEIX_LIVE, source_metadata
    policy = gift_publication_policy()
    _dump(os.path.join(out, "gift_publication_policy.json"), {
        "checked_at": stamp, "policy": policy.to_dict(),
        "rights_review": {"status": "OPEN - public display NOT approved",
                          "document": "docs/NSEIX_RIGHTS_REVIEW.md",
                          "registry_display_rights": {
                              s: source_metadata(s).display_rights_status
                              for s in (SRC_NSEIX_LIVE, SRC_NSEIX_DSP)}},
        "behaviour": {
            "publication_mode_gate_closed": "not fetched, not displayed (gift_status POLICY_DISABLED)",
            "shadow_mode_gate_closed": "fetched + validated + audited (gift_audit_<D>.json), "
                                       "removed from the brief -> never displayed",
            "gate_open": "fetched; displayed only when FRESH and validated",
            "never": "GIFT never blocks PRE; a policy withhold is not a degradation"},
        "enable_requires": [f"{ENV_FLAG}=true", f"{ENV_APPROVAL}=<approval/licence reference>"],
    })
    from operations.official_events import check_official_events
    ev = check_official_events(dt.datetime.now(IST).date())
    _dump(os.path.join(out, "official_events_check.json"), ev)
    return {"gift_policy_allowed": policy.publication_allowed, "official_events": ev["status"]}


# --------------------------------------------------------------------------- scenarios (read-only)
def previous_session_scenarios(db_path: str, cases) -> list:
    from core.trading_calendar import SessionCalendar
    from products.premarket import PreMarketBlocked, require_previous_report
    cal = SessionCalendar()
    out = []
    for pre_date, label in cases:
        row = {"scenario": label, "pre_date": pre_date.isoformat(),
               "expected_previous_session": (cal.previous_session(pre_date).isoformat()
                                             if cal.previous_session(pre_date) else None)}
        try:
            prev, report, path = require_previous_report(pre_date, cal, db_path)
            row.update(verdict="PROCEEDS", report_id=report.report_id,
                       report_session=report.session_date.isoformat(), report_path=path)
        except PreMarketBlocked as exc:
            row.update(verdict="SKIPPED" if exc.code == "NOT_A_SESSION" else "BLOCKED",
                       code=exc.code, message=exc.message)
        out.append(row)
    return out


# --------------------------------------------------------------------------- sandbox runs
def make_sandbox(out: str) -> str:
    box = os.path.join(out, "sandbox")
    if os.path.exists(box):
        shutil.rmtree(box)
    os.makedirs(box)
    for d in STATE_DIRS:
        src = os.path.join(config.OUT_DIR, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(box, d),
                            ignore=shutil.ignore_patterns("*.db-wal", "*.db-shm", "*.mp4",
                                                          "*.png"))
    return box


def run_in_sandbox(box: str, argv: list, log_name: str, timeout: int = 1800) -> dict:
    env = dict(os.environ, DAILY_BYTE_OUT=box, PYTHONIOENCODING="utf-8")
    env.pop("GIFT_NIFTY_PUBLICATION_ENABLED", None)          # the default gate: closed
    env.pop("GIFT_NIFTY_PUBLICATION_APPROVAL", None)
    t0 = time.time()
    proc = subprocess.run([sys.executable, "main.py", *argv], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    log = os.path.join(box, "logs", log_name)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    return {"argv": argv, "exit_code": proc.returncode, "seconds": round(time.time() - t0, 1),
            "log": log, "tail": proc.stdout.strip().splitlines()[-6:]}


def latest_runs(db_path: str, limit=50) -> list:
    from storage import MarketHistory
    with MarketHistory(db_path) as h:
        return h.get_publication_runs(limit=limit)


def run_row(r) -> dict:
    d = r.details or {}
    return {"run_id": r.run_id, "job": r.job, "job_type_column": r.job_type, "mode": r.mode,
            "target_date": r.target_date, "source_session_date": r.source_session_date,
            "run_status": r.run_status, "stage": r.stage,
            "publication_status": r.publication_status, "started_at": r.started_at,
            "completed_at": r.completed_at, "data_qa_status": r.data_qa_status,
            "video_qa_status": r.video_qa_status, "report_id": r.report_id,
            "artifact_path": r.artifact_path, "failure_stage": r.failure_stage,
            "failure_reason": (r.failure_reason or "")[:300] or None,
            "report_source": d.get("report_source"),
            "degraded_sources": sorted({x.get("source") for x in d.get("degradations") or []}),
            "gift": ({k: (d.get("gift_audit") or {}).get(k) for k in
                      ("gift_data_available", "gift_data_valid", "gift_publication_allowed",
                       "gift_displayed", "reason")} if d.get("gift_audit") else None)}


def live_validation(out: str) -> dict:
    from operations.sessions import latest_final_session
    now = dt.datetime.now(IST)
    box = make_sandbox(out)
    db = os.path.join(box, "data", "market_history.db")
    res = {"sandbox": box, "now": now.isoformat(), "runs": {}}
    session = latest_final_session(now)
    res["latest_final_session"] = session.isoformat() if session else None
    print(f"[live] REPORT job for {session} in the sandbox...")
    res["runs"]["report_first"] = run_in_sandbox(box, ["--mode", "report"], "report_1.log")
    print(f"[live] REPORT job rerun (idempotency)...")
    res["runs"]["report_rerun"] = run_in_sandbox(box, ["--mode", "report"], "report_2.log")
    # this morning, reconstructed: previous session = the calendar's session before today
    today = now.date()
    as_of = dt.datetime.combine(today, dt.time(7, 45)).isoformat()
    print("[live] PRE shadow reconstruction of this morning (GIFT fetched now, audited)...")
    res["runs"]["pre_shadow_today"] = run_in_sandbox(
        box, ["--mode", "premarket", "--shadow", "--session-date", today.isoformat(),
              "--as-of", as_of], "pre_shadow_today.log")
    print("[live] PRE publication-mode frames-only run (GIFT gate closed -> not fetched)...")
    res["runs"]["pre_publication_frames"] = run_in_sandbox(
        box, ["--mode", "premarket", "--session-date", today.isoformat(), "--as-of", as_of,
              "--frames-only"], "pre_publication.log")
    print("[live] PRE shadow for a session whose previous report is missing...")
    res["runs"]["pre_shadow_missing"] = run_in_sandbox(
        box, ["--mode", "premarket", "--shadow", "--session-date", "2026-09-23",
              "--as-of", "2026-09-23T07:45:00"], "pre_shadow_missing.log")
    res["run_rows"] = [run_row(r) for r in latest_runs(db, 20)]
    shadow_dir = os.path.join(box, "pre_shadow", today.isoformat())
    manifest = os.path.join(shadow_dir, "shadow_manifest.json")
    res["shadow_manifest"] = json.load(open(manifest, encoding="utf-8")) if os.path.exists(manifest) else None
    audit = os.path.join(shadow_dir, f"gift_audit_{today}.json")
    res["gift_audit"] = json.load(open(audit, encoding="utf-8")) if os.path.exists(audit) else None
    pub_audit = os.path.join(box, "premarket", today.isoformat(), f"gift_audit_{today}.json")
    res["gift_audit_publication_mode"] = (json.load(open(pub_audit, encoding="utf-8"))
                                          if os.path.exists(pub_audit) else None)
    nxt = __import__("operations.sessions", fromlist=["next_session"]).next_session(session) if session else None
    res["next_morning_lookup"] = (previous_session_scenarios(
        db, [(nxt, f"next PRE ({nxt}) after tonight's REPORT job")]) if nxt else None)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--connectivity-only", action="store_true")
    ap.add_argument("--no-live", action="store_true", help="skip the sandbox runs")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    from operations.connectivity import run_diagnostic, write_diagnostic
    conn = run_diagnostic()
    write_diagnostic(conn, os.path.join(args.out, "connectivity_diagnostic.json"))
    for c in conn["checks"]:
        print(f"  {c['check']:<22} {c['status']:<12} {c['detail']}")
    if args.connectivity_only:
        return 0

    summary = {"generated_at": dt.datetime.now(IST).isoformat(),
               "connectivity": conn["summary"], **write_static(args.out)}
    from storage import default_db_path
    real_db = default_db_path(config.OUT_DIR)
    scenarios = previous_session_scenarios(real_db, [
        (dt.date(2026, 9, 22), "Tue after a normal Monday: report for Mon 21 Sep"),
        (dt.date(2026, 9, 23), "previous session 22 Sep has no canonical report -> BLOCKED "
                               "(the 21 Sep report must NOT be substituted)"),
        (dt.date(2026, 9, 25), "valid previous report (24 Sep) -> PRE proceeds"),
        (dt.date(2026, 9, 28), "Friday report -> Monday PRE (exists only after tonight's REPORT job)"),
        (dt.date(2026, 9, 15), "holiday Mon 14 Sep: previous canonical session is Fri 11 Sep"),
        (dt.date(2026, 10, 5), "holiday Fri 2 Oct: previous canonical session is Thu 1 Oct"),
        (dt.date(2026, 10, 2), "PRE date is itself a holiday -> SKIPPED"),
    ])
    live = None if args.no_live else live_validation(args.out)
    _dump(os.path.join(args.out, "scenario_validation.json"),
          {"real_history_read_only": real_db, "previous_session_scenarios": scenarios,
           "live_sandbox": live})
    legacy = [run_row(r) for r in latest_runs(real_db, 50)]
    _dump(os.path.join(args.out, "run_history_audit.json"), {
        "note": "sandbox rows come from this pack's live runs (a COPY of output state); real "
                "rows are read-only - pre-v3 rows keep job_type NULL and derive `job` from mode",
        "sandbox_runs": (live or {}).get("run_rows"),
        "real_history_runs": legacy,
        "real_history_by_job": {j: sum(1 for r in legacy if r["job"] == j)
                                for j in sorted({r["job"] or "UNKNOWN" for r in legacy})}})
    summary["scenarios"] = {s["pre_date"]: s["verdict"] for s in scenarios}
    if live:
        summary["live"] = {k: v["exit_code"] for k, v in live["runs"].items()}
    _dump(os.path.join(args.out, "readiness_summary.json"), summary)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
