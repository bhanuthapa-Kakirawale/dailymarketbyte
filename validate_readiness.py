"""PK-C historical / shadow validation of the production readiness gate (read-only).

Evaluates the gate in REPLAY over a bounded set of stored editions - each at its own
historical cutoff, never the wall clock - and reports the READY / DEGRADED / BLOCKED
distribution as it is (never tuned to force READY). Writes only
output/readiness_validation/readiness_validation.{json,md}.

    python validate_readiness.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# (edition, session, cutoff or None = the edition's default replay cutoff, label)
SCENARIOS = [
    ("PRE", "2026-10-01", None, "normal Thursday"),
    ("PRE", "2026-10-02", None, "holiday (Gandhi Jayanti) - no edition"),
    ("PRE", "2026-10-05", None, "Monday after the holiday - previous session Thu 1 Oct"),
    ("PRE", "2026-10-06", None, "normal Tuesday"),
    ("PRE", "2026-10-07", None, "normal Wednesday"),
    ("PRE", "2026-10-08", None, "today, 07:45 cutoff"),
    ("PRE", "2026-10-08", "2026-10-08T09:20:00", "degraded scenario: cutoff after the 09:15 open"),
    ("POST", "2026-09-30", None, "normal session"),
    ("POST", "2026-10-01", None, "pre-holiday session"),
    ("POST", "2026-10-05", None, "Monday session"),
    ("POST", "2026-10-06", None, "thin/quiet early-REPORT session"),
    ("POST", "2026-10-07", None, "end-of-day cutoff"),
    ("POST", "2026-10-07", "2026-10-07T19:30:00", "normal evening cutoff (19:30)"),
    ("POST", "2026-10-07", "2026-10-07T14:00:00", "degraded scenario: session not final yet"),
]


def run(out_dir: str) -> dict:
    from readiness import evaluate_post, evaluate_pre
    rows = []
    for edition, session, cutoff, label in SCENARIOS:
        d = dt.date.fromisoformat(session)
        as_of = dt.datetime.fromisoformat(cutoff).replace(tzinfo=IST) if cutoff else None
        try:
            fn = evaluate_pre if edition == "PRE" else evaluate_post
            r = fn(d, as_of, mode="REPLAY", out_dir=out_dir)
            rows.append({"edition": edition, "session": session, "label": label,
                         "cutoff": r.cutoff.isoformat() if r.cutoff else None,
                         "status": r.overall_status, "decision": r.decision,
                         "blocking": r.blocking_reasons, "warnings": r.warnings,
                         "seconds": r.runtime.get("seconds"),
                         "network_calls": r.runtime.get("network_calls")})
        except Exception as exc:
            rows.append({"edition": edition, "session": session, "label": label,
                         "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"})
    dist = {ed: dict(Counter(r["status"] for r in rows if r["edition"] == ed))
            for ed in ("PRE", "POST")}
    warn_counts = Counter(w.split(":")[0] for r in rows for w in r.get("warnings") or [])
    return {"generated_by": "validate_readiness.py", "mode": "REPLAY (read-only)",
            "scenarios": rows, "distribution": dist,
            "warning_frequency": dict(warn_counts.most_common())}


def to_markdown(res: dict) -> str:
    lines = ["# Readiness gate - historical / shadow validation", "",
             f"Mode: {res['mode']}. Distribution: PRE {res['distribution']['PRE']}, "
             f"POST {res['distribution']['POST']}.", "",
             "| Edition | Session | Scenario | Status | Blocking / warnings | s |",
             "|---|---|---|---|---|---|"]
    for r in res["scenarios"]:
        why = "; ".join((r.get("blocking") or []) + (r.get("warnings") or [])) or r.get("error", "")
        lines.append(f"| {r['edition']} | {r['session']} | {r['label']} | {r['status']} | "
                     f"{why[:300].replace('|', '/')} | {r.get('seconds', '')} |")
    lines += ["", "Warning frequency: " + ", ".join(f"{k} x{v}" for k, v in
                                                     res["warning_frequency"].items())]
    return "\n".join(lines) + "\n"


def main() -> int:
    import config
    res = run(config.OUT_DIR)
    folder = os.path.join(config.OUT_DIR, "readiness_validation")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "readiness_validation.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False, default=str)
    md = to_markdown(res)
    with open(os.path.join(folder, "readiness_validation.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
