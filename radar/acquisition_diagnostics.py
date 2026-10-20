"""Read-only "where did my 200 symbols go?" audit over recent Radar runs (Radar OHLCV acquisition
reliability V1, docs/OHLCV_STORE.md "Acquisition health").

    python -m radar.acquisition_diagnostics [--sessions 20] [--json]

Reads ONLY what earlier runs already wrote - `output/radar/daily_radar_<session>.json`,
`output/report_jobs/<session>/*.json` and `candidate_history_runs` (through
`storage.readonly.connect_readonly`, which never creates a file). No network call, no write,
no recomputation: an artifact that predates the `acquisition` block is classified from the
counts it recorded (`health` marked `*`), never re-acquired.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import config

from .ohlcv_service import AcquisitionHealth, CoverageDiagnostics, classify_health


def _load_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _candidate_markers(out_dir: str) -> dict:
    path = os.path.join(out_dir, "data", "radar_candidate_history.db")
    if not os.path.exists(path):
        return {}
    from storage.readonly import connect_readonly
    conn = connect_readonly(path)
    try:
        rows = conn.execute("SELECT session_date, status, candidate_count, processed_at "
                            "FROM candidate_history_runs").fetchall()
    except Exception:
        return {}
    finally:
        conn.close()
    out: dict = {}
    for session, status, count, processed_at in rows:
        prev = out.get(session)
        if prev is None or str(processed_at) > str(prev["processed_at"]):
            out[session] = {"status": status, "candidate_count": count,
                            "processed_at": processed_at}
    return out


def _report_jobs(out_dir: str, session: str) -> list:
    jobs = []
    for path in sorted(glob.glob(os.path.join(out_dir, "report_jobs", session, "*.json"))):
        rec = _load_json(path) or {}
        radar = (rec.get("details") or {}).get("radar") or {}
        jobs.append({"as_of": rec.get("as_of"), "run_status": rec.get("run_status"),
                     "failure_reason": rec.get("failure_reason"),
                     "radar_status": radar.get("status"),
                     "radar_issue": next((i.get("code") for i in radar.get("issues") or []
                                          if i.get("severity") == "ERROR"), None)})
    return jobs


def _acquisition(art: dict) -> dict:
    cov = art.get("coverage_diagnostics") or {}
    acq = cov.get("acquisition")
    if acq:
        return {**acq, "inferred": False}
    # pre-V1 artifact: classify from the counts it recorded; provider/written unknown
    requested = int(art.get("universe_requested") or cov.get("requested_symbols") or 0)
    usable = int(art.get("universe_usable") or cov.get("usable_symbols") or 0)
    health = classify_health(CoverageDiagnostics(requested_symbols=requested, usable_symbols=usable,
                                                 provider_returned=1))
    return {"health": health, "expected_universe": requested, "usable": usable,
            "rejected": int(cov.get("skipped_symbols") or 0), "provider_returned": None,
            "bars_written": None, "skip_reason_counts": {}, "inferred": True}


def collect(out_dir: str | None = None, sessions: int = 20) -> list:
    out_dir = out_dir or config.OUT_DIR
    radar_files = {os.path.basename(p)[len("daily_radar_"):-len(".json")]: p
                   for p in glob.glob(os.path.join(out_dir, "radar", "daily_radar_*.json"))}
    job_sessions = {os.path.basename(p) for p in glob.glob(os.path.join(out_dir, "report_jobs", "*"))
                    if os.path.isdir(p)}
    markers = _candidate_markers(out_dir)
    rows = []
    for session in sorted(set(radar_files) | job_sessions)[-sessions:]:
        art = _load_json(radar_files[session]) if session in radar_files else None
        acq = _acquisition(art) if art else None
        rows.append({
            "session": session,
            "radar_status": (art or {}).get("pipeline_status"),
            "generated_at": (art or {}).get("generated_at"),
            "acquisition": acq,
            "composite_candidates": (art or {}).get("composite_candidate_count"),
            "candidate_history": markers.get(session),
            "report_jobs": _report_jobs(out_dir, session)})
    return rows


def summarize(rows: list) -> dict:
    analysed = [r for r in rows if r["acquisition"]]
    failed = [r["session"] for r in analysed
              if r["acquisition"]["health"] in AcquisitionHealth.FAILED]
    partial = [r["session"] for r in analysed
               if r["acquisition"]["health"] == AcquisitionHealth.PARTIAL]
    no_radar = [r["session"] for r in rows if not r["acquisition"]]
    return {"sessions": len(rows), "with_radar_artifact": len(analysed),
            "acquisition_failed": failed, "partial": partial, "no_radar_artifact": no_radar,
            "acquisition_failed_pct": round(100.0 * len(failed) / len(analysed), 1)
            if analysed else None}


def _fmt(rows: list, summary: dict) -> str:
    lines = [f"{'session':<11} {'radar':<9} {'health':<16} {'usable':>9} {'cand':>5} "
             f"{'hist':<16} report_job / reasons"]
    for r in rows:
        acq = r["acquisition"] or {}
        health = (acq.get("health") or "-") + ("*" if acq.get("inferred") else "")
        usable = (f"{acq.get('usable')}/{acq.get('expected_universe')}" if acq else "-")
        hist = r["candidate_history"] or {}
        hist_s = (f"{hist.get('status')}({hist.get('candidate_count')})" if hist else "-")
        jobs = "; ".join(
            f"{(j['run_status'] or '?')}" + (f" radar={j['radar_status']}" if j["radar_status"] else "")
            + (f" [{j['radar_issue']}]" if j["radar_issue"] else "")
            + (f" ({j['failure_reason'][:70]})" if j["failure_reason"] else "")
            for j in r["report_jobs"]) or "-"
        reasons = ", ".join(f"{k}={v}" for k, v in (acq.get("skip_reason_counts") or {}).items())
        lines.append(f"{r['session']:<11} {(r['radar_status'] or '-'):<9} {health:<16} "
                     f"{usable:>9} {str(r['composite_candidates'] if r['composite_candidates'] is not None else '-'):>5} "
                     f"{hist_s:<16} {jobs}" + (f" | rejected: {reasons}" if reasons else ""))
    lines.append("")
    lines.append(f"sessions {summary['sessions']}, with Radar artifact "
                 f"{summary['with_radar_artifact']}, acquisition FAILED "
                 f"{len(summary['acquisition_failed'])} ({summary['acquisition_failed_pct']}%): "
                 f"{summary['acquisition_failed']}, PARTIAL {summary['partial']}, "
                 f"no Radar artifact {summary['no_radar_artifact']}")
    lines.append("* = pre-V1 artifact, health inferred from its recorded usable/requested counts")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m radar.acquisition_diagnostics",
                                 description="Read-only Radar OHLCV acquisition audit.")
    ap.add_argument("--sessions", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = collect(sessions=args.sessions)
    summary = summarize(rows)
    if args.json:
        print(json.dumps({"summary": summary, "sessions": rows}, indent=2, default=str))
    else:
        print(_fmt(rows, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
