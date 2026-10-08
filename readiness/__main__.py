"""python -m readiness {pre|post} - the production readiness gate (docs/PRODUCTION_READINESS.md).

    python -m readiness pre                       # LIVE: now, today's PRE edition
    python -m readiness post                      # LIVE: now, the latest completed session
    python -m readiness pre --session-date D      # REPLAY of D's morning (07:45 IST cutoff)
    python -m readiness post --as-of 2026-10-07T19:30:00+05:30
    python -m readiness post --stage post-render  # did the rendered edition pass QA?

Exit codes: 0 READY, 10 DEGRADED, 20 BLOCKED, 30 the gate itself could not evaluate.
LIVE writes output/readiness/readiness_<EDITION>_<session>.json; REPLAY writes nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .models import (EXIT_ERROR, LIVE, POST_RENDER, PUBLISH, REPLAY, SHADOW,
                     ReadinessExecutionError)


def _parse_as_of(value: str) -> dt.datetime:
    from .evidence import IST
    ts = dt.datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=IST)


def evaluate(edition: str, *, session_date=None, as_of=None, intent=SHADOW, stage="preflight",
             out_dir=None, now=None):
    """The CLI's evaluation, importable (PK-D). LIVE iff neither `session_date` nor `as_of`
    is given: then the cutoff is `now` (default: the IST clock - the ONLY wall-clock read)."""
    import config
    edition = edition.upper()
    if stage == "post-render":
        from .post_render import evaluate_post_render
        if session_date is None:
            from operations.sessions import latest_final_session
            now = now or config.now_ist()
            session_date = (now.date() if edition == "PRE" else latest_final_session(now))
        return evaluate_post_render(edition, session_date, out_dir=out_dir, intent=intent)
    mode = REPLAY if (session_date is not None or as_of is not None) else LIVE
    if mode == LIVE:
        as_of = now or config.now_ist()
    if edition == "PRE":
        from .pre import evaluate_pre
        return evaluate_pre(session_date, as_of, mode=mode, intent=intent, out_dir=out_dir)
    from .post import evaluate_post
    return evaluate_post(session_date, as_of, mode=mode, intent=intent, out_dir=out_dir)


def _short(text, n=104) -> str:
    text = str(text or "").replace("\n", " ")
    return text if len(text) <= n else text[:n - 3] + "..."


def render_text(res, path: str | None = None) -> str:
    d = res.to_dict()
    lines = ["=" * 64,
             f" DMB {res.edition} READINESS  ({res.stage}, {res.mode}, intent {res.intent})",
             "=" * 64]
    sess = d["session_date"] or "-"
    if res.edition == "PRE" and d["source_session"]:
        sess += f" (previous session {d['source_session']})"
    lines.append(f"  {'session':<20} {sess}")
    if d["cutoff"]:
        lines.append(f"  {'cutoff':<20} {d['cutoff']}")
    lines.append(f"  {'status':<20} {d['overall_status']}")
    lines.append("  " + "-" * 60)
    for c in res.checks:
        lines.append(f"  {c.status:<5} {c.check_id:<30} {_short(c.message, 80)}")
    for c in res.checks:
        if c.status in ("FAIL", "WARN") and c.remediation:
            lines.append(f"  {'fix ' + c.check_id:<32} {_short(c.remediation, 78)}")
    rt = d["runtime"] or {}
    if rt:
        lines.append(f"  {'runtime':<20} {rt.get('seconds')}s, "
                     f"{rt.get('network_calls', 0)} network call(s)")
    if path:
        lines.append(f"  {'report':<20} {path}")
    lines += ["=" * 64, f"VERDICT: {d['overall_status']}", f"DECISION: {d['decision']}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m readiness",
                                 description="DMB production readiness gate (never renders, "
                                             "never uploads)")
    ap.add_argument("edition", choices=("pre", "post"))
    ap.add_argument("--session-date", help="PRE: the session about to open; POST: the completed "
                                           "session (implies REPLAY unless LIVE is wanted)")
    ap.add_argument("--as-of", help="historical cutoff, ISO datetime (IST if naive) - REPLAY")
    ap.add_argument("--intent", choices=("shadow", "publish"), default="shadow",
                    help="publish: a rights-review BLOCK also blocks (default shadow)")
    ap.add_argument("--stage", choices=("preflight", "post-render"), default="preflight")
    ap.add_argument("--json", action="store_true", help="print the JSON result only")
    ap.add_argument("--no-write", action="store_true", help="do not write the readiness report")
    a = ap.parse_args(argv)
    try:
        sd = dt.date.fromisoformat(a.session_date) if a.session_date else None
        as_of = _parse_as_of(a.as_of) if a.as_of else None
        res = evaluate(a.edition, session_date=sd, as_of=as_of,
                       intent=PUBLISH if a.intent == "publish" else SHADOW, stage=a.stage)
        path = None
        if res.mode == LIVE and not a.no_write:
            import config
            from .report import write_report
            try:
                path = write_report(res, config.OUT_DIR)
            except Exception as exc:          # the verdict stands; the file is diagnostics
                print(f"readiness report not written: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
    except (ReadinessExecutionError, Exception) as exc:
        err = f"{type(exc).__name__}: {exc}"
        if a.json:
            print(json.dumps({"schema": "dmb.readiness/1", "edition": a.edition.upper(),
                              "overall_status": None, "error": err, "exit_code": EXIT_ERROR}))
        else:
            print(f"READINESS ERROR: {err}\nVERDICT: ERROR (the gate could not evaluate - "
                  "do not treat this as READY)")
        return EXIT_ERROR
    if a.json:
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(res, path))
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "evaluate", "render_text", "POST_RENDER"]
