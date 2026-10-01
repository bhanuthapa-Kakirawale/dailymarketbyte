"""`python -m private_desk.check` - read-only health / freshness check for the Private Desk.

Prints what the desk would show (sources vs the latest completed session, database health,
detector-replay reconciliation for the latest Radar session) and a VERDICT. Exit code 0 = ready,
2 = something needs attention. Starts no server and writes nothing outside the desk cache.
"""
from __future__ import annotations

import argparse
import sys

from .db import DB_FILES
from .services import DeskService
from .settings import DeskSettings

OK, ATTENTION = 0, 2


def _line(label, value) -> None:
    print(f"  {label:<30} {value}")


def run(settings: DeskSettings) -> int:
    svc = DeskService(settings)
    problems = []
    print("PRIVATE DESK CHECK  (read-only; PRIVATE / LOCAL ONLY)")
    print(f"  reading {settings.out_dir}")
    f = svc.freshness()
    print(f"\nSources vs latest completed session {f.latest_completed or 'UNKNOWN'}")
    for s in f.sources:
        _line(s.label, f"{s.session or '-'}  {s.status}  {s.as_of or ''}")
        if s.status != "FRESH":
            problems.append(f"{s.label} {s.status}")

    print("\nDatabases (opened read-only)")
    for name in DB_FILES:
        h = svc.repo.db_health(name)
        _line(h["file"], f"{h['status']}  {h.get('size_bytes', 0) // 1024} KB  "
                         f"WAL {'pending' if h.get('wal_pending') else 'none'}")
        if h["status"] != "OK":
            problems.append(f"{h['file']} {h['status']}")

    session, notice = svc.resolve_session(None)
    print(f"\nRadar session {session or '-'}")
    if session is None:
        problems.append(notice)
    else:
        q = svc.quality(session)
        rp = q.get("replay") or {}
        _line("detector replay", f"{rp.get('status')}  {rp.get('reason') or ''}")
        _line("reconciliation", " ".join(f"{k}={v}" for k, v in (rp.get("reconciliation") or {}).items()))
        if rp.get("not_matched"):
            _line("not matched", ", ".join(rp["not_matched"]))
        if rp.get("status") != "OK":
            problems.append("detector replay unavailable")
        o = q.get("ohlcv") or {}
        _line("universe symbols missing", len(o.get("missing") or []))
        _line("universe symbols stale", len(o.get("stale") or []))
        if o.get("missing") or o.get("stale"):
            problems.append("OHLCV coverage incomplete")
        _line("canonical report", (q.get("report") or {}).get("status"))

    print()
    if problems:
        print("VERDICT: ATTENTION REQUIRED - " + "; ".join(problems))
        return ATTENTION
    print("VERDICT: DESK DATA READY")
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m private_desk.check")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)
    return run(DeskSettings.from_out_dir(args.out_dir, cache_dir=args.cache_dir))


if __name__ == "__main__":
    sys.exit(main())
