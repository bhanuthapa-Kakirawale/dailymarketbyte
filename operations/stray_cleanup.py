"""One-off removal of KNOWN test contamination from production run history.

    python -m operations.stray_cleanup            # dry run: identify + print, change nothing
    python -m operations.stray_cleanup --apply    # backup, delete, audit

The contamination (owner-identified, PRE shadow readiness): exactly two REPORT_BUILD run rows,
both BLOCKED with HISTORICAL_REBUILD_UNSUPPORTED for session 2026-09-18, written by a faulty
test at ~22:50 IST on 25 Sep 2026, plus their two job records under
output/report_jobs/2026-09-18/.

Identification is by PROPERTIES, never by "the latest rows": every predicate in
`KNOWN_STRAY` must hold, the match must be EXACTLY `EXPECTED_COUNT` rows, and each matched row
must have exactly one job record file whose own run_id/status/reason agree. Anything else and
nothing is deleted. Before deleting: the rows are exported to JSON, the job records are copied,
and the whole database file is copied. The audit JSON records all of it.

Nothing canonical is touched: `reports`/facts/observations are never read for deletion, and the
two rows have no report_id (they never built a report).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil

CLEANUP_VERSION = "stray-cleanup-1.0"
EXPECTED_COUNT = 2
KNOWN_STRAY = {
    "job_type": "REPORT_BUILD",
    "mode": "REPORT_BUILD",
    "run_status": "BLOCKED",
    "failure_stage": "SESSION_RESOLUTION",
    "failure_reason_prefix": "HISTORICAL_REBUILD_UNSUPPORTED",
    "target_date": "2026-09-18",
    "report_id": None,
    # 22:50 IST on 25 Sep 2026 == 17:20 UTC; a window around it, in UTC ISO
    "started_after": "2026-09-25T17:00:00+00:00",
    "started_before": "2026-09-25T17:40:00+00:00",
}


def _matches(row: dict) -> bool:
    k = KNOWN_STRAY
    return (row.get("job_type") == k["job_type"] and row.get("mode") == k["mode"]
            and row.get("run_status") == k["run_status"]
            and row.get("failure_stage") == k["failure_stage"]
            and str(row.get("failure_reason") or "").startswith(k["failure_reason_prefix"])
            and row.get("target_date") == k["target_date"]
            and row.get("report_id") is k["report_id"]
            and k["started_after"] <= str(row.get("started_at") or "") < k["started_before"])


def _job_record(out_dir: str, run_id: str) -> tuple:
    path = os.path.join(out_dir, "report_jobs", KNOWN_STRAY["target_date"],
                        f"report_job_{run_id}.json")
    if not os.path.exists(path):
        return path, None, "job record file missing"
    try:
        rec = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return path, None, f"job record unreadable: {exc}"
    if rec.get("run_id") != run_id or rec.get("run_status") != KNOWN_STRAY["run_status"] or \
            not str(rec.get("failure_reason") or "").startswith(KNOWN_STRAY["failure_reason_prefix"]):
        return path, rec, "job record does not describe the same blocked run"
    return path, rec, None


def identify(history, out_dir: str) -> dict:
    """The deterministic match. Never deletes."""
    rows = [dict(r) for r in history.conn.execute("SELECT * FROM publication_runs").fetchall()]
    matched = sorted((r for r in rows if _matches(r)), key=lambda r: r["started_at"])
    folder = os.path.join(out_dir, "report_jobs", KNOWN_STRAY["target_date"])
    files_present = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    problems, files = [], []
    if len(matched) != EXPECTED_COUNT:
        problems.append(f"expected exactly {EXPECTED_COUNT} matching rows, found {len(matched)}")
    for r in matched:
        path, _rec, err = _job_record(out_dir, r["run_id"])
        files.append(path)
        if err:
            problems.append(f"{r['run_id']}: {err}")
    expected_names = sorted(os.path.basename(p) for p in files)
    unexpected = [f for f in files_present if f not in expected_names]
    if unexpected:
        problems.append(f"{folder} holds other files, which are not touched: {unexpected}")
    return {"total_runs_in_db": len(rows), "matched_rows": matched, "files": files,
            "folder": folder, "folder_files_before": files_present,
            "safe_to_apply": not [p for p in problems if "not touched" not in p],
            "problems": problems}


def cleanup(db_path: str, out_dir: str, audit_dir: str, apply: bool = False,
            now: dt.datetime | None = None) -> dict:
    from storage import MarketHistory
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    history = MarketHistory(db_path)
    try:
        found = identify(history, out_dir)
        audit = {"version": CLEANUP_VERSION, "run_at": now.isoformat(), "applied": False,
                 "db_path": db_path, "criteria": KNOWN_STRAY, "expected_count": EXPECTED_COUNT,
                 "total_runs_before": found["total_runs_in_db"],
                 "matched_run_ids": [r["run_id"] for r in found["matched_rows"]],
                 "matched_rows": found["matched_rows"], "job_record_files": found["files"],
                 "problems": found["problems"], "safe_to_apply": found["safe_to_apply"]}
        if not apply or not found["safe_to_apply"]:
            audit["outcome"] = "DRY_RUN" if found["safe_to_apply"] else "REFUSED"
            return _write(audit, audit_dir)

        backup_dir = os.path.join(audit_dir, "stray_cleanup_backup", stamp)
        os.makedirs(backup_dir, exist_ok=True)
        history.conn.commit()
        db_copy = os.path.join(backup_dir, os.path.basename(db_path))
        history.conn.execute("VACUUM INTO ?", (db_copy,))   # consistent snapshot (WAL-safe)
        rows_path = os.path.join(backup_dir, "removed_publication_runs.json")
        with open(rows_path, "w", encoding="utf-8") as fh:
            json.dump(found["matched_rows"], fh, indent=2, ensure_ascii=False, default=str)
        copied = []
        for f in found["files"]:
            dst = os.path.join(backup_dir, "report_jobs", KNOWN_STRAY["target_date"],
                               os.path.basename(f))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(f, dst)
            copied.append(dst)

        removed = history.remove_contaminated_runs(audit["matched_run_ids"])
        if removed != EXPECTED_COUNT:
            raise RuntimeError(f"removed {removed} rows, expected {EXPECTED_COUNT}; restore "
                               f"from {db_copy}")
        deleted_files = []
        for f in found["files"]:
            os.remove(f)
            deleted_files.append(f)
        folder_removed = False
        if os.path.isdir(found["folder"]) and not os.listdir(found["folder"]):
            os.rmdir(found["folder"])
            folder_removed = True
        after = history.conn.execute("SELECT count(*) FROM publication_runs").fetchone()[0]
        still = history.get_run_rows(audit["matched_run_ids"])
        audit.update(applied=True, outcome="REMOVED", rows_removed=removed,
                     total_runs_after=after, verified_absent=not still,
                     files_deleted=deleted_files, empty_folder_removed=folder_removed,
                     backup={"directory": backup_dir, "database_snapshot": db_copy,
                             "rows_export": rows_path, "job_records": copied},
                     untouched=("reports, facts, observations, validation_results, catalysts, "
                                "events and every other publication_runs row"))
        return _write(audit, audit_dir)
    finally:
        history.close()


def _write(audit: dict, audit_dir: str) -> dict:
    os.makedirs(audit_dir, exist_ok=True)
    path = os.path.join(audit_dir, "stray_cleanup_audit.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, indent=2, ensure_ascii=False, default=str)
    audit["audit_path"] = path
    return audit


def main(argv=None) -> int:
    import argparse
    import config
    from storage import default_db_path
    ap = argparse.ArgumentParser(description="remove the two known stray REPORT_BUILD runs")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    audit = cleanup(default_db_path(config.OUT_DIR), config.OUT_DIR,
                    os.path.join(config.OUT_DIR, "pre_shadow_readiness"), apply=args.apply)
    print(f"stray cleanup: {audit['outcome']} matched={audit['matched_run_ids']} "
          f"problems={audit['problems']} -> {audit['audit_path']}")
    return 0 if audit["outcome"] in ("DRY_RUN", "REMOVED") else 1


__all__ = ["cleanup", "identify", "KNOWN_STRAY", "EXPECTED_COUNT"]

if __name__ == "__main__":
    raise SystemExit(main())
