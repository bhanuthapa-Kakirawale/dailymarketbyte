"""python -m production_orchestrator {pre|post|status} - PK-D, the ONE command per edition.

    python -m production_orchestrator pre                       # LIVE, today's PRE edition
    python -m production_orchestrator post                      # LIVE, the latest completed session
    python -m production_orchestrator pre --session-date D --as-of ISO --intent publish
    python -m production_orchestrator post --resume             # continue an interrupted attempt
    python -m production_orchestrator post --force              # ignore PK-D's own shortcuts
    python -m production_orchestrator status                    # read-only, no side effects

Exit codes: 0 completed (READY or an allowed DEGRADED/ALREADY_COMPLETED/SKIPPED), 20 BLOCKED by
readiness/policy, 30 execution failure, 40 another production run holds the lock.

Never uploads, regardless of --intent publish: PUBLIC_REVIEW_REQUIRED_POLICY and the existing
readiness/publication-audit gates decide; this module adds no override.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .models import EXIT_ERROR, PUBLISH, SHADOW


def _parse_as_of(value: str) -> dt.datetime:
    from readiness.evidence import IST
    ts = dt.datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=IST)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m production_orchestrator",
                                 description="PK-D production orchestration (never uploads)")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("pre", "post"):
        p = sub.add_parser(name)
        p.add_argument("--session-date", help="the session to target (default: resolved live)")
        p.add_argument("--as-of", help="ISO cutoff for the readiness gate (IST if naive)")
        p.add_argument("--intent", choices=("shadow", "publish"), default="shadow")
        p.add_argument("--resume", action="store_true",
                       help="continue a leftover RUNNING attempt for this session, if any")
        p.add_argument("--force", action="store_true",
                       help="ignore PK-D's own already-completed/already-rendered shortcuts")
        p.add_argument("--json", action="store_true")
        p.add_argument("--no-write", action="store_true", help="do not write the run manifest")
    sub.add_parser("status").add_argument("--json", action="store_true")
    return ap


def _human_summary(manifest) -> str:
    d = manifest.to_dict()
    lines = ["=" * 64, f" DMB {manifest.edition} PRODUCTION RUN", "=" * 64,
             f"  {'session':<22} {d['session_date'] or '-'}",
             f"  {'orchestrator_status':<22} {d['orchestrator_status']}",
             f"  {'readiness (preflight)':<22} {d['readiness_status_preflight'] or '-'}",
             f"  {'readiness (post_render)':<22} {d['readiness_status_post_render'] or '-'}"]
    if d.get("video_artifact"):
        lines.append(f"  {'video':<22} {d['video_artifact'].get('path')}")
    rt = d.get("runtime") or {}
    if rt.get("seconds") is not None:
        lines.append(f"  {'runtime':<22} {rt['seconds']}s")
    if d.get("reclaimed_stale_lock"):
        lines.append(f"  {'reclaimed stale lock':<22} {d['reclaimed_stale_lock']}")
    for w in d.get("warnings") or []:
        lines.append(f"  {'warning':<22} {w}")
    for b in d.get("blocking_reasons") or []:
        lines.append(f"  {'blocking':<22} {b}")
    lines.append(f"  {'run_id':<22} {d['run_id']}")
    lines += ["=" * 64, f"EXIT CODE: {d['exit_code']}"]
    return "\n".join(lines)


def _status_summary(s: dict) -> str:
    lines = ["=" * 64, " DMB PRODUCTION STATUS", "=" * 64]
    for edition, row in s.items():
        if row is None:
            lines.append(f"  {edition:<6} no run yet")
            continue
        lines.append(f"  {edition:<6} {row.get('orchestrator_status')} session="
                     f"{row.get('session_date')} readiness(preflight)="
                     f"{row.get('readiness_status_preflight')} readiness(post_render)="
                     f"{row.get('readiness_status_post_render')} run_id={row.get('run_id')}")
    lines.append("=" * 64)
    return "\n".join(lines)


def main(argv=None, run_pre_fn=None, run_post_fn=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    import config

    if a.command == "status":
        from .status import status
        s = status(config.OUT_DIR)
        print(json.dumps(s, indent=2, ensure_ascii=False, default=str) if a.json
              else _status_summary(s))
        return 0

    from . import post as post_mod
    from . import pre as pre_mod
    run_pre_fn = run_pre_fn or pre_mod.run_pre
    run_post_fn = run_post_fn or post_mod.run_post
    sd = dt.date.fromisoformat(a.session_date) if a.session_date else None
    as_of = _parse_as_of(a.as_of) if a.as_of else None
    intent = PUBLISH if a.intent == "publish" else SHADOW
    fn = run_pre_fn if a.command == "pre" else run_post_fn
    try:
        manifest = fn(session_date=sd, as_of=as_of, intent=intent, resume=a.resume,
                     force=a.force, no_write=a.no_write, cli_argv=sys.argv[1:])
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        if a.json:
            print(json.dumps({"error": err, "exit_code": EXIT_ERROR}))
        else:
            print(f"PRODUCTION ORCHESTRATOR ERROR: {err}")
        return EXIT_ERROR
    print(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False, default=str) if a.json
          else _human_summary(manifest))
    return manifest.exit_code


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "build_parser"]
