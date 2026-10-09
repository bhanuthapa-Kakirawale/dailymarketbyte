"""python -m shadow_scheduler {run <job>|status}. See docs/SHADOW_PRODUCTION_OPERATIONS.md.

    python -m shadow_scheduler run intelligence_am
    python -m shadow_scheduler run pre
    python -m shadow_scheduler run intelligence_pm
    python -m shadow_scheduler run post
    python -m shadow_scheduler status [--json]

Exit codes for `run`: the CHILD's own exit code, verbatim, for COMPLETED/CHILD_NONZERO; 3 for
LOCKED (the global scheduler lock was busy past its bounded wait); 4 for FAILED (the child
could not even be launched). Never retried, never remapped. `status` always exits 0 unless the
aggregate is BROKEN (exit 1) - still read-only either way.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import runner, status
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
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return runner.run_job(args.job, cli_argv=sys.argv[1:])

    s = status.overall_status()
    if args.json:
        print(json.dumps(s, indent=2, ensure_ascii=False, default=str))
    else:
        print(status.render_human(s))
    return 1 if s["overall"] == BROKEN else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "build_parser"]
