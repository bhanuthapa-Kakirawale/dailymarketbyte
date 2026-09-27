"""Isolated local test runs of the whole pipeline (docs/TESTING_GUIDE.md).

    python -m operations.test_run evening [--mode live|replay|fixture] [--session-date D]
    python -m operations.test_run pre     [--run-id ID | --mode replay --session-date D | --mode fixture]
    python -m operations.test_run post    [--run-id ID | --mode replay --session-date D | --mode fixture]
    python -m operations.test_run full    [--mode live|replay|fixture] [--session-date D]
    python -m operations.test_run check   [--run-id ID]
    python -m operations.test_run list

Every run lives in ONE folder, output/test_runs/<run_id>/, which is that run's entire output
root (DAILY_BYTE_OUT): its history databases, reports, Radar, Market Structure, official
snapshots, audits, QA, PRE and POST renders, logs/ and manifest.json. The canonical entry
points (`python main.py ...`) run unchanged, as subprocesses, with DMB_RUN_CONTEXT=test:
config.py refuses any other output root, the writers refuse production paths
(operations.run_context), --upload is refused (UPLOAD_DISABLED_TEST_CONTEXT) and remote state
is refused. Production state is fingerprinted before and after every step, and the manifest
records the comparison (`production_state_modified`).

Modes
    LIVE_SANDBOX  (live)    real sources, fetched normally; the run starts from a READ-ONLY copy
                            of production state so the Radar has its history; nothing is promoted
    REPLAY        (replay)  a past session's stored inputs (a copy); never fetches today's lists;
                            the report must already exist - a replay never builds one
    FIXTURE       (fixture) synthetic data (--demo); empty state; no network
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

MODES = {"live": "LIVE_SANDBOX", "replay": "REPLAY", "fixture": "FIXTURE"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,80}$")
PASS, ATTENTION = "PASS", "ATTENTION REQUIRED"


# --------------------------------------------------------------------------- paths
def _config():
    import config
    return config


def base_dir() -> str:
    return _config().BASE_DIR


def production_out() -> str:
    return os.path.realpath(_config().PRODUCTION_OUT_DIR)


def test_root() -> str:
    return os.path.realpath(_config().TEST_RUNS_DIR)


def run_path(run_id: str) -> str:
    """The folder of one run - refused for anything that is not a plain run id directly under
    output/test_runs/ (no separators, no '..', no symlink)."""
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"invalid run id {run_id!r}")
    p = os.path.join(test_root(), run_id)
    if os.path.islink(p) or os.path.dirname(os.path.realpath(p)) != test_root():
        raise ValueError(f"run id {run_id!r} does not resolve to a folder in {test_root()}")
    return p


# --------------------------------------------------------------------------- production proof
def fingerprint(root: str | None = None) -> dict:
    """{relative path: [size, sha256]} of every file of the PRODUCTION output tree (test_runs
    excluded). Read-only."""
    root = root or production_out()
    tests = test_root()
    out = {}
    if not os.path.isdir(root):
        return out
    for dp, dirs, files in os.walk(root, followlinks=False):
        if os.path.realpath(dp) == tests or os.path.realpath(dp).startswith(tests + os.sep):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if os.path.realpath(os.path.join(dp, d)) != tests]
        for f in files:
            full = os.path.join(dp, f)
            if os.path.islink(full):
                continue
            h = hashlib.sha256()
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[os.path.relpath(full, root).replace(os.sep, "/")] = [os.path.getsize(full),
                                                                     h.hexdigest()]
    return out


def compare(before: dict, after: dict) -> dict:
    changed = sorted(k for k in before if k in after and before[k] != after[k])
    removed = sorted(k for k in before if k not in after)
    added = sorted(k for k in after if k not in before)
    return {"production_state_modified": bool(changed or removed or added),
            "files_checked": len(before), "changed": changed[:50], "removed": removed[:50],
            "added": added[:50]}


def digest(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------- seeding
def seed_from_production(run_dir: str) -> dict:
    """Copy the approved production state (state.sync.NAMESPACES) into the run - READ-ONLY on
    the production side: plain byte copies, databases included (with any existing WAL sidecars,
    storage.readonly.copy_database) - no SQLite connection to a production database is ever
    opened (in WAL mode even a read-only one creates files). The run works on its copies only."""
    from state.sync import NAMESPACES, _local_files
    from storage.readonly import copy_database
    prod = production_out()
    copied = {}
    for ns in NAMESPACES:
        n = 0
        for rel, full in _local_files(prod, ns):
            dest = os.path.join(run_dir, ns.local, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if full.endswith(".db"):
                copy_database(full, dest)
            else:
                shutil.copy2(full, dest)
            n += 1
        copied[ns.key] = n
    return copied


def _latest_canonical_session(run_dir: str) -> dt.date | None:
    db = os.path.join(run_dir, "data", "market_history.db")
    if not os.path.exists(db):
        return None
    from storage.readonly import connect_readonly
    con = connect_readonly(db)
    try:
        row = con.execute("select max(session_date) from reports where coalesce(is_demo,0)=0 "
                          "and coalesce(report_type,'') != 'RADAR_SCAN'").fetchone()
    finally:
        con.close()
    return dt.date.fromisoformat(row[0]) if row and row[0] else None


def _has_report(run_dir: str, session: dt.date) -> bool:
    for p in glob.glob(os.path.join(run_dir, "reports", "premarket_*.json")):
        if p.endswith("_DEMO.json"):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                if json.load(fh).get("session_date") == session.isoformat():
                    return True
        except (OSError, ValueError):
            continue
    return False


# --------------------------------------------------------------------------- manifest
def _load_manifest(run_dir: str) -> dict:
    p = os.path.join(run_dir, "manifest.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save_manifest(run_dir: str, m: dict) -> str:
    m["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    p = os.path.join(run_dir, "manifest.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2, ensure_ascii=False, default=str)
    return p


def new_run(mode: str, session: dt.date | None = None, run_id: str | None = None) -> tuple:
    """Create (and seed) a run folder; returns (run_dir, manifest)."""
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    rid = run_id or f"{stamp}_{mode}"
    run_dir = run_path(rid)
    n = 1
    while os.path.exists(run_dir):
        n += 1
        run_dir = run_path(f"{rid}_{n}")
    os.makedirs(os.path.join(run_dir, "logs"))
    source = MODES[mode]
    m = {"marker": "TEST", "run_id": os.path.basename(run_dir), "test_mode": source,
         "source_mode": source, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
         "output_root": run_dir, "session_date": session.isoformat() if session else None,
         "synthetic": mode == "fixture", "historical_replay": mode == "replay",
         "network_acquisition_used": False, "upload": "UPLOAD_DISABLED_TEST_CONTEXT",
         "steps": {}, "seeded_from_production": None}
    if mode in ("live", "replay"):
        before = fingerprint()
        m["seeded_from_production"] = seed_from_production(run_dir)
        after = fingerprint()
        m["seed_proof"] = compare(before, after)
    _save_manifest(run_dir, m)
    return run_dir, m


def latest_run() -> str | None:
    runs = [p for p in glob.glob(os.path.join(test_root(), "*"))
            if os.path.isdir(p) and not os.path.islink(p)
            and os.path.exists(os.path.join(p, "manifest.json"))]
    return max(runs, key=lambda p: _load_manifest(p).get("started_at", "")) if runs else None


# --------------------------------------------------------------------------- steps
def _child_env(run_dir: str) -> dict:
    env = dict(os.environ)
    for k in ("DMB_STATE_DIR", "DMB_GCS_BUCKET", "DMB_GCS_PREFIX"):
        env.pop(k, None)
    env.update(DAILY_BYTE_OUT=run_dir, DMB_RUN_CONTEXT="test", DMB_STATE_BACKEND="local",
               PYTHONIOENCODING="utf-8")
    return env


def step_command(step: str, mode: str, session: dt.date | None) -> list:
    """The canonical entry point for one step - exactly what the normal scripts run, plus the
    mode's replay / fixture flags. Never --upload."""
    if step == "REPORT":
        return {"live": ["--mode", "report"],
                "replay": ["--mode", "report", "--session-date", str(session), "--skip-radar"],
                "fixture": ["--mode", "report", "--demo"]}[mode]
    if step == "PRE":
        if mode == "replay":
            from core.trading_calendar import SessionCalendar
            from operations.sessions import next_session
            pre_date = next_session(session, SessionCalendar())
            if pre_date is None:
                raise ValueError(f"the calendar has no session after {session}")
            return ["--mode", "premarket", "--shadow", "--session-date", str(pre_date)]
        return {"live": ["--mode", "premarket", "--shadow"],
                "fixture": ["--mode", "premarket", "--demo"]}[mode]
    if step == "POST":
        return {"live": [], "replay": ["--session-date", str(session)], "fixture": ["--demo"]}[mode]
    raise ValueError(step)


NETWORK = {("REPORT", "live"): "LIVE", ("PRE", "live"): "LIVE", ("POST", "live"): "LIVE_IF_MISSING",
           ("PRE", "replay"): "HISTORICAL_DATED_BARS"}


def run_step(run_dir: str, step: str, mode: str, session: dt.date | None) -> dict:
    cmd = [sys.executable, "main.py"] + step_command(step, mode, session)
    log = os.path.join(run_dir, "logs", f"{step.lower()}.log")
    before = fingerprint()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f"\n--- TEST {step}: {' '.join(cmd[1:])}  (log: {log})", flush=True)
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=base_dir(), env=_child_env(run_dir),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        for line in proc.stdout:
            sys.stdout.write(line)
            fh.write(line)
        rc = proc.wait()
    proof = compare(before, fingerprint())
    result = {"command": "python " + " ".join(cmd[1:]), "exit_code": rc, "log": log,
              "started_at": started, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "network": NETWORK.get((step, mode), "NONE"),
              "production_state_modified": proof["production_state_modified"],
              "production_proof": proof}
    result.update(evaluate(run_dir, step, mode))
    result["ok"] = rc == 0 and result.get("acceptable", False) and \
        not proof["production_state_modified"]
    return result


def _latest(pattern: str):
    paths = glob.glob(pattern)
    return max(paths, key=os.path.getmtime) if paths else None


def evaluate(run_dir: str, step: str, mode: str) -> dict:
    """Read the step's own artifacts (what the normal daily check reads)."""
    from operations.daily_check import audit_verdict
    if step == "REPORT":
        p = _latest(os.path.join(run_dir, "report_jobs", "*", "report_job_*.json"))
        rec = json.load(open(p, encoding="utf-8")) if p else {}
        det = rec.get("details") or {}
        return {"status": rec.get("run_status") or "NO_RECORD", "session": rec.get("target_session"),
                "report_id": det.get("report_id"), "report_source": det.get("report_source"),
                "official_snapshot_status": det.get("official_snapshot_status"),
                "radar": (det.get("radar") or {}).get("status"), "record": p,
                "acceptable": rec.get("run_status") in ("SUCCESS", "DEGRADED")}
    if step == "PRE":
        if mode == "fixture":
            p = _latest(os.path.join(run_dir, "premarket", "DEMO", "pre_result_*.json"))
            res = json.load(open(p, encoding="utf-8")) if p else {}
            pa = res.get("publication_audit") or {}
            verdict = audit_verdict(pa.get("final"), pa.get("failed_checks"))
            return {"status": "RENDERED" if res.get("ok") else "FAILED", "qa_ok": res.get("ok"),
                    "audit": verdict, "video": res.get("video"),
                    "acceptable": bool(res.get("ok")) and verdict in ("PASS", "RIGHTS_BLOCK_ONLY")}
        p = _latest(os.path.join(run_dir, "pre_shadow", "*", "shadow_manifest.json"))
        m = json.load(open(p, encoding="utf-8")) if p else {}
        status = m.get("run_status") or "NO_MANIFEST"
        res_p = os.path.join(os.path.dirname(p), f"pre_result_{m.get('pre_date')}.json") if p else ""
        res = json.load(open(res_p, encoding="utf-8")) if res_p and os.path.exists(res_p) else {}
        pa = res.get("publication_audit") or {}
        verdict = audit_verdict(pa.get("final"), pa.get("failed_checks"))
        qa_ok = (m.get("qa") or {}).get("ok")
        return {"status": status, "pre_date": m.get("pre_date"), "qa_ok": qa_ok,
                "audit": verdict if pa else None, "failure": m.get("failure_reason"),
                "video": (m.get("artifacts") or {}).get("mp4"),
                "contact_sheet": (m.get("artifacts") or {}).get("contact_sheet"), "manifest": p,
                "acceptable": status == "SKIPPED" or (
                    status in ("SUCCESS", "DEGRADED") and bool(qa_ok)
                    and verdict in ("PASS", "RIGHTS_BLOCK_ONLY"))}
    p = _latest(os.path.join(run_dir, "post", "*", "production_manifest.json"))
    m = json.load(open(p, encoding="utf-8")) if p else {}
    qa = m.get("qa") or {}
    pa = m.get("publication_audit") or {}
    verdict = audit_verdict(pa.get("final"), pa.get("failed_checks"))
    sheet = None
    if p:
        from operations.daily_check import post_contact_sheet
        sheet = post_contact_sheet(os.path.dirname(p))
    ok = (qa.get("video_qa") == "PASS" and (qa.get("frames_qa") or {}).get("passed")
          and qa.get("content_qa") == "SAFE" and verdict in ("PASS", "RIGHTS_BLOCK_ONLY"))
    return {"status": "RENDERED" if p else "NO_MANIFEST", "session": m.get("session_date"),
            "report_source": m.get("report_source"), "scenes": m.get("scenes"),
            "duration": m.get("duration"), "qa": {"video": qa.get("video_qa"),
                                                  "frames": (qa.get("frames_qa") or {}).get("passed"),
                                                  "content": qa.get("content_qa")},
            "audit": verdict, "upload": m.get("upload"), "video": m.get("video"),
            "contact_sheet": sheet, "manifest": p,
            "inputs": {k: (v or {}).get("source") for k, v in (m.get("inputs") or {}).items()
                       if isinstance(v, dict)},
            "acceptable": bool(ok)}


def finish(run_dir: str, m: dict) -> str:
    steps = m.get("steps") or {}
    m["qa_result"] = {k: v.get("qa_ok", v.get("qa")) for k, v in steps.items() if k != "REPORT"}
    m["publication_audit_result"] = {k: v.get("audit") for k, v in steps.items()
                                     if k != "REPORT"}
    m["network_acquisition_used"] = any(v.get("network", "NONE") != "NONE"
                                        for v in steps.values())
    m["production_state_modified"] = any(v.get("production_state_modified") for v in steps.values()) \
        or bool((m.get("seed_proof") or {}).get("production_state_modified"))
    m["verdict"] = PASS if steps and all(v.get("ok") for v in steps.values()) and \
        not m["production_state_modified"] else ATTENTION
    return _save_manifest(run_dir, m)


def summary(run_dir: str, m: dict) -> None:
    print("\n" + "=" * 64)
    print(f" TEST MODE ({m.get('test_mode')})   run_id: {m.get('run_id')}")
    print(f" test root: {run_dir}")
    print(f" session  : {m.get('session_date') or '-'}")
    for name, s in (m.get("steps") or {}).items():
        extra = (f"audit={s.get('audit')}" if name != "REPORT" else
                 f"official={s.get('official_snapshot_status')} radar={s.get('radar')}")
        print(f"  {name:<7} exit={s.get('exit_code')} status={s.get('status')} {extra} "
              f"-> {'OK' if s.get('ok') else 'ATTENTION'}")
        for k in ("video", "contact_sheet"):
            if s.get(k):
                print(f"          {k}: {s[k]}")
    print(f" production_state_modified: {m.get('production_state_modified')} "
          f"(fingerprint of the production output tree before/after every step)")
    print(f" upload: {m.get('upload')}")
    print(f" manifest: {os.path.join(run_dir, 'manifest.json')}")
    print("=" * 64)
    print(f"VERDICT: {m.get('verdict')}")


# --------------------------------------------------------------------------- CLI
def _session(args, run_dir: str, mode: str) -> dt.date | None:
    if mode != "replay":
        return dt.date.fromisoformat(args.session_date) if args.session_date else None
    session = (dt.date.fromisoformat(args.session_date) if args.session_date
               else _latest_canonical_session(run_dir))
    if session is None:
        raise SystemExit("REPLAY: no canonical report in production state to replay")
    if not _has_report(run_dir, session):
        raise SystemExit(f"REPLAY: production state holds no canonical report for {session} - "
                         "a replay never builds one (pick a session that has one)")
    return session


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m operations.test_run",
                                 description="isolated local test runs (docs/TESTING_GUIDE.md)")
    ap.add_argument("command", choices=("evening", "pre", "post", "full", "check", "list"))
    ap.add_argument("--mode", choices=tuple(MODES), default=None,
                    help="live (default for evening/full) | replay | fixture")
    ap.add_argument("--session-date", help="replay: the stored session to replay (YYYY-MM-DD)")
    ap.add_argument("--run-id", help="pre/post/check: an existing test run")
    ap.add_argument("--upload", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    from operations.run_context import UPLOAD_DISABLED
    if args.upload:
        print(f"{UPLOAD_DISABLED}: test runs never upload. Nothing was run.")
        return 2
    print(f"TEST MODE - {args.command} ({dt.datetime.now():%Y-%m-%d %H:%M:%S})")
    if args.command == "list":
        for p in sorted(glob.glob(os.path.join(test_root(), "*"))):
            m = _load_manifest(p)
            print(f"  {os.path.basename(p):<36} {m.get('test_mode', '?'):<13} "
                  f"session={m.get('session_date')} verdict={m.get('verdict')}")
        return 0
    if args.command == "check":
        run_dir = run_path(args.run_id) if args.run_id else latest_run()
        if not run_dir or not os.path.isdir(run_dir):
            print("no test run found (output/test_runs/)")
            return 1
        m = _load_manifest(run_dir)
        summary(run_dir, m)
        return 0 if m.get("verdict") == PASS else 2

    if args.command in ("evening", "full") or (args.mode in ("replay", "fixture")
                                                and not args.run_id):
        mode = args.mode or "live"
        run_dir, m = new_run(mode)
    else:
        run_dir = run_path(args.run_id) if args.run_id else latest_run()
        if not run_dir or not os.path.isdir(run_dir):
            print("TEST STATE MISSING: no test run to use - run scripts\\test_evening.bat first, "
                  "or pass --mode replay --session-date YYYY-MM-DD, or --mode fixture. "
                  "(A test never falls back to production state.)")
            return 2
        m = _load_manifest(run_dir)
        mode = {v: k for k, v in MODES.items()}[m["test_mode"]]
        if args.mode and args.mode != mode:
            print(f"--mode {args.mode} does not match run {m['run_id']} ({m['test_mode']})")
            return 2
    print(f"TEST MODE: {m['test_mode']}  run_id: {m['run_id']}\n  test root: {run_dir}")
    try:
        session = _session(args, run_dir, mode) if (args.command in ("evening", "full")
                                                     or not m.get("session_date")) else (
            dt.date.fromisoformat(m["session_date"]) if m.get("session_date") else None)
    except SystemExit as exc:
        m["steps"]["SETUP"] = {"ok": False, "status": "FAILED", "error": str(exc)}
        finish(run_dir, m)
        summary(run_dir, m)
        return 2
    m["session_date"] = session.isoformat() if session else m.get("session_date")
    steps = {"evening": ["REPORT"], "pre": ["PRE"], "post": ["POST"],
             "full": ["REPORT", "PRE", "POST"]}[args.command]
    for step in steps:
        res = run_step(run_dir, step, mode, session)
        m["steps"][step] = res
        if step == "REPORT" and not m.get("session_date") and res.get("session"):
            m["session_date"] = res["session"]
        _save_manifest(run_dir, m)
        if step == "REPORT" and not res["ok"] and args.command == "full":
            print("REPORT did not complete - PRE and POST are not run on incomplete test state")
            break
    finish(run_dir, m)
    summary(run_dir, m)
    return 0 if m["verdict"] == PASS else 2


# --------------------------------------------------------------------------- cleanup
def plan_clean(run_id: str | None = None, older_than_days: float | None = None) -> list:
    """[(run_dir, file_count, bytes)] - only direct children of output/test_runs/, never a
    symlink, never anything else."""
    root = test_root()
    if run_id is not None:
        candidates = [run_path(run_id)]
    else:
        candidates = [p for p in glob.glob(os.path.join(root, "*"))]
    out = []
    now = dt.datetime.now().timestamp()
    for p in candidates:
        if os.path.islink(p) or not os.path.isdir(p):
            continue
        if os.path.dirname(os.path.realpath(p)) != root:
            continue
        if older_than_days is not None and now - os.path.getmtime(p) < older_than_days * 86400:
            continue
        files, size = 0, 0
        for dp, _, fs in os.walk(p, followlinks=False):
            for f in fs:
                fp = os.path.join(dp, f)
                if not os.path.islink(fp):
                    files += 1
                    size += os.path.getsize(fp)
        out.append((p, files, size))
    return out


def execute_clean(plan: list) -> dict:
    root = test_root()
    removed, freed = 0, 0
    for p, _, size in plan:
        real = os.path.realpath(p)
        if os.path.islink(p) or os.path.dirname(real) != root:
            continue                                   # re-checked at deletion time
        shutil.rmtree(real)                            # removes links inside, never their targets
        removed += 1
        freed += size
    return {"removed_runs": removed, "freed_bytes": freed}


__all__ = ["main", "new_run", "run_step", "evaluate", "fingerprint", "compare",
           "seed_from_production", "plan_clean", "execute_clean", "run_path", "latest_run",
           "step_command", "MODES"]


if __name__ == "__main__":
    sys.exit(main())
