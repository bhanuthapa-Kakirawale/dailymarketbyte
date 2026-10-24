"""python -m shadow_scheduler {run <job>|status|reconcile|audit}. See
docs/SHADOW_PRODUCTION_OPERATIONS.md.

    python -m shadow_scheduler run intelligence_am
    python -m shadow_scheduler run pre
    python -m shadow_scheduler run intelligence_pm
    python -m shadow_scheduler run post
    python -m shadow_scheduler status [--json]
    python -m shadow_scheduler reconcile [--execute] [--json]   # DRY RUN unless --execute
    python -m shadow_scheduler audit [--from D] [--to D] [--json]   # read-only

Exit codes for `run`: the CHILD's own exit code, verbatim, for COMPLETED/CHILD_NONZERO; 0 for
EXPIRED/EARLY (invoked outside its own occurrence's window - nothing launched); 3 for LOCKED
(the global scheduler lock was busy past its bounded wait); 4 for FAILED (the child could not
even be launched, or the runner itself hit an unexpected exception); 5 for DEPENDENCY_FAILED.
Never retried, never remapped. `status` always exits 0 unless the aggregate is BROKEN (exit 1)
- still read-only either way. `reconcile` and `audit` always exit 0; `audit` and a
`reconcile` without `--execute` never write anything.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import audit, reconcile, runner, status
from .models import BROKEN, JOBS


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m shadow_scheduler",
                                 description="PK-E shadow scheduling operations "
                                             "(launches existing entry points only)")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run one of the 4 scheduled jobs")
    r.add_argument("job", choices=JOBS)
    s = sub.add_parser("status", help="read-only: the 4 jobs' last outcome + health")
    s.add_argument("--json", action="store_true")
    c = sub.add_parser("reconcile", help="classify non-terminal run records; DRY RUN unless "
                                         "--execute (append-only ABANDONED markers only)")
    c.add_argument("--execute", action="store_true")
    c.add_argument("--json", action="store_true")
    a = sub.add_parser("audit", help="read-only: replay run records under the current "
                                     "execution-window rule")
    a.add_argument("--from", dest="date_from", type=dt.date.fromisoformat)
    a.add_argument("--to", dest="date_to", type=dt.date.fromisoformat)
    a.add_argument("--json", action="store_true")
    return ap


def _emit(result: dict, as_json: bool, render) -> None:
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(render(result))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return runner.run_job(args.job, cli_argv=sys.argv[1:])
    if args.command == "reconcile":
        _emit(reconcile.reconcile(execute=args.execute), args.json, reconcile.render_human)
        return 0
    if args.command == "audit":
        _emit(audit.audit(date_from=args.date_from, date_to=args.date_to), args.json,
              audit.render_human)
        return 0

    s = status.overall_status()
    _emit(s, args.json, status.render_human)
    return 1 if s["overall"] == BROKEN else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "build_parser"]
