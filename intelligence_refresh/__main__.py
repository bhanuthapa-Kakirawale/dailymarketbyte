"""python -m intelligence_refresh {refresh|status}. See docs/INTELLIGENCE_REFRESH.md."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import models
from .refresh import run_refresh
from .status import run_status


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m intelligence_refresh")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="catch up the latest session, self-heal the price chain")
    r.add_argument("--days", type=int, default=30)
    r.add_argument("--from", dest="from_date", type=_date, default=None)
    r.add_argument("--to", dest="to_date", type=_date, default=None)
    r.add_argument("--session-date", dest="session_date", type=_date, default=None)
    r.add_argument("--resume", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--no-network", action="store_true")
    r.add_argument("--json", action="store_true")

    s = sub.add_parser("status", help="read-only: report the last refresh run")
    s.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "refresh":
        man = run_refresh(days=args.days, from_date=args.from_date, to_date=args.to_date,
                          session_date=args.session_date, resume=args.resume,
                          dry_run=args.dry_run, no_network=args.no_network)
        if args.json:
            print(json.dumps(man.to_dict(), indent=2, ensure_ascii=False, default=str))
        else:
            print(f"intelligence_refresh run {man.run_id}: {man.orchestrator_status}")
            if man.latest_session_result:
                print(f"  latest session: {man.latest_session_result}")
            for k, v in (man.summary or {}).items():
                print(f"  {k}: {v}")
            for b in man.blocking_reasons:
                print(f"  BLOCKING {b}")
            for w in man.warnings:
                print(f"  WARN {w}")
        return man.exit_code

    exit_code, payload = run_status(json_output=args.json)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(payload)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
