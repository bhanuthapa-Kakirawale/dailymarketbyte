"""python -m production_quality pre-degradation [--runs 20] [--out-dir DIR] [--json]

Read-only: makes zero network calls, zero writes, zero render calls. See
docs/PRODUCTION_READINESS.md.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import pre_degradation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m production_quality")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("pre-degradation",
                         help="audit the last N PRE readiness snapshots for recurring causes")
    pre.add_argument("--runs", type=int, default=20)
    pre.add_argument("--out-dir", default=None,
                     help="defaults to the real output/ root (config.OUT_DIR)")
    pre.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = args.out_dir
    if out_dir is None:
        import config as root_config
        out_dir = root_config.OUT_DIR

    if args.command == "pre-degradation":
        report = pre_degradation.analyze_pre_degradation(out_dir, runs=args.runs)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(pre_degradation.render_human(report))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
