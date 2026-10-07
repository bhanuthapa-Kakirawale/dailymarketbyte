"""Editorial Planner V3 - bounded historical SHADOW validation (manual, read-only).

Replays every stored edition through the production planning path and summarises what the
editorial plan selected:

    POST  every stored non-demo canonical report (output/reports/premarket_<edition>.json):
          the persisted Market Structure / official / market-events snapshots of its session
          (load_public_intelligence(replay=True) - never a fetch), the editorial plan, then
          products.post_unified.build_post_storyboard under PUBLIC_UNREGISTERED, deterministic
          hook (no Gemini)
    PRE   every stored PRE shadow brief (output/pre_shadow/<d>/pre_brief_<d>.json): the stored
          overnight readings and institutional context + the previous session's canonical
          report, the persisted public snapshots (replay), apply_publication_profile, the PRE
          plan and storyboard

Nothing is rendered, uploaded or fetched. The production history DB is read through a byte
COPY (storage.readonly.copy_database) and the output tree is fingerprinted before/after; the
only write is the summary under --out (default output/editorial_v3_shadow/).

It calls only public APIs that also exist before V3, so the SAME script run in a checkout of
the pre-V3 commit (with DAILY_BYTE_OUT pointing at this output tree) gives the V2 baseline over
identical inputs:  python validate_editorial_v3.py --label v2 --out <dir>
Thresholds are never tuned from these results.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import glob
import hashlib
import json
import os
import sys
import tempfile

import config

OUT_DIR = config.OUT_DIR
STRUCTURAL = {"HOOK", "PULSE", "CLOSING", "OVERNIGHT", "SETUP", "WATCH", "RADAR"}


# --------------------------------------------------------------------------- read-only guard
def fingerprint(root: str, skip: str) -> str:
    h = hashlib.sha256()
    for dirpath, _, files in os.walk(root):
        if os.path.abspath(dirpath).startswith(os.path.abspath(skip)):
            continue
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            h.update(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


def history_copy(tmp: str):
    from storage.readonly import copy_database
    from storage.repository import MarketHistory, default_db_path
    src = default_db_path(OUT_DIR)
    dst = os.path.join(tmp, "market_history.db")
    copy_database(src, dst)
    return MarketHistory(dst)


# --------------------------------------------------------------------------- metrics
def summarise(sb, edition: str, session: str) -> dict:
    scenes = [s for s in sb.scenes]
    sections = [s.section for s in scenes]
    optional = [s for s in sections if s not in STRUCTURAL]
    plan = (sb.post_plan if edition == "POST" else sb.pre_plan) or {}
    ed = plan.get("editorial") or {}
    trace = ed.get("trace") or []
    # A duplicate = a scene that restates a fact another scene already carries: a second
    # UNDER THE SURFACE scene, or a broad breadth move in the index's own direction shown next
    # to the headline on a >= 0.6% Nifty day. Measured on the storyboard, so it applies to V2
    # and V3 alike.
    dup = max(0, sum(1 for s in scenes if s.kind == "STRUCTURE") - 1)
    pct = None
    if edition == "POST":
        pct = ((sb.post_plan or {}).get("pulse") or {}).get("change")
    for s in scenes:
        if s.kind == "STRUCTURE" and s.headline.startswith("A broad move") and pct:
            try:
                if abs(float(pct.rstrip("%"))) >= 0.6:
                    dup += 1
            except ValueError:
                pass
    return {"edition": edition, "session": session, "scenes": len(scenes),
            "optional_sections": optional, "optional_count": len(optional),
            "duration": round(sb.total_duration, 2), "duplicates": dup,
            "hook": (sb.hook_plan or {}).get("candidate_id"),
            "candidates": len([t for t in trace if t.get("role") != "REQUIRED"]),
            "suppressed": len([t for t in trace if t.get("role") != "REQUIRED"
                               and t.get("decision") not in ("SELECTED", "QUIET_FLOOR")]),
            "decisions": {t["candidate_id"]: t["decision"] for t in trace},
            "lead": ed.get("lead")}


# --------------------------------------------------------------------------- POST
def post_editions(history) -> list:
    import intelligence
    from core import MarketReport
    from operations.sessions import next_session
    from presentation.public_intelligence import load_public_intelligence
    from products import post_unified as PU
    out = []
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "reports", "premarket_*.json"))):
        if "DEMO" in path:
            continue
        try:
            report = MarketReport.from_json(open(path, encoding="utf-8").read())
            snapshot = intelligence.build_snapshot(report, history)
            plan = PU.plan_for_post(report, snapshot, now=report.generated_at)
            session = report.session_date or report.report_date
            intel = load_public_intelligence(session, next_session(session) or report.report_date,
                                             OUT_DIR, fetch=False, snapshot_session=session,
                                             replay=True)
            sb, _ = PU.build_post_storyboard(report, plan, intelligence=intel, hook_ai=False)
            out.append(summarise(sb, "POST", session.isoformat()))
        except Exception as exc:       # one bad edition never hides the others
            out.append({"edition": "POST", "session": os.path.basename(path),
                        "error": f"{type(exc).__name__}: {exc}"})
    return out


# --------------------------------------------------------------------------- PRE
def _dt(v):
    return dt.datetime.fromisoformat(v) if v else None


def _d(v):
    return dt.date.fromisoformat(v) if v else None


def _quote(d):
    from core.freshness import FreshnessStatus, QuoteKind
    from providers.premarket import PreMarketQuote
    names = {f.name for f in dataclasses.fields(PreMarketQuote)}
    kw = {k: v for k, v in d.items() if k in names}
    kw.update(kind=QuoteKind(d["kind"]), freshness=FreshnessStatus(d["freshness"]),
              reference_date=_d(d.get("reference_date")), market_date=_d(d.get("market_date")),
              market_timestamp=_dt(d.get("market_timestamp")),
              retrieved_at=_dt(d.get("retrieved_at")))
    return PreMarketQuote(**kw)


def _vix(d):
    if not d:
        return None
    from core.freshness import FreshnessStatus
    from providers.premarket import VixReading
    return VixReading(value=d["value"], session=_d(d["session"]),
                      previous_value=d.get("previous_value"),
                      previous_session=_d(d.get("previous_session")),
                      change_pct=d.get("change_pct"), freshness=FreshnessStatus(d["freshness"]),
                      freshness_reason=d.get("freshness_reason", ""),
                      source=d.get("source", "yahoo_finance"),
                      validation_status=d.get("validation_status", "SINGLE_SOURCE"),
                      crosscheck=d.get("crosscheck", ""))


def pre_editions() -> list:
    from core.event_calendar import scheduled_events_for
    from core import MarketReport
    from core.trading_calendar import SessionCalendar
    from daily_video.pre_storyboard import build_pre_storyboard
    from presentation.pre_plan import build_pre_brief, plan_pre_sections
    from presentation.pre_public import apply_publication_profile
    from presentation.public_intelligence import load_public_intelligence
    from providers.premarket import PreMarketAcquisition
    from config import EXPIRY_WEEKDAY
    cal = SessionCalendar()
    out = []
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "pre_shadow", "*", "pre_brief_*.json"))):
        if "DEMO" in path:
            continue
        try:
            stored = json.load(open(path, encoding="utf-8"))
            pre_date = _d(stored["pre_date"])
            as_of = _dt(stored["as_of"])
            report_path = os.path.join(OUT_DIR, "reports", f"premarket_{pre_date}.json")
            report = MarketReport.from_json(open(report_path, encoding="utf-8").read())
            acq = PreMarketAcquisition(global_cues=[_quote(q) for q in stored["global_cues"]],
                                       vix=_vix(stored.get("vix")), gift=None)
            events = scheduled_events_for(pre_date, cal, EXPIRY_WEEKDAY)
            brief = build_pre_brief(report, pre_date, as_of, cal, acq, events)
            prev = brief.previous_session
            brief.public_intelligence = load_public_intelligence(
                prev, pre_date, OUT_DIR, fetch=False, snapshot_session=prev, replay=True,
                now=as_of, capture_mode="PRE_FALLBACK", calendar=cal)
            brief.institutional = stored.get("institutional")
            brief.institutional_nse_context = stored.get("institutional_nse_context") or {}
            gate = apply_publication_profile(brief)
            plan = plan_pre_sections(brief)
            sb = build_pre_storyboard(brief, plan, gate=gate)
            out.append(summarise(sb, "PRE", pre_date.isoformat()))
        except Exception as exc:
            out.append({"edition": "PRE", "session": os.path.basename(path),
                        "error": f"{type(exc).__name__}: {exc}"})
    return out


# --------------------------------------------------------------------------- report
FAMILY_SECTIONS = ("SECTORS", "STRUCTURE", "FLOWS", "MARKET_EVENTS", "EVENT", "MOVERS", "IPO",
                   "EXCHANGE", "NIFTY", "GLOBAL", "VIX", "STOCK_WATCH")


def aggregate(rows: list) -> dict:
    ok = [r for r in rows if "error" not in r]
    n = len(ok) or 1
    out = {"editions": len(ok), "errors": len(rows) - len(ok),
           "avg_scenes": round(sum(r["scenes"] for r in ok) / n, 2),
           "avg_optional": round(sum(r["optional_count"] for r in ok) / n, 2),
           "avg_duration": round(sum(r["duration"] for r in ok) / n, 2),
           "min_duration": min((r["duration"] for r in ok), default=None),
           "max_duration": max((r["duration"] for r in ok), default=None),
           "duplicates_total": sum(r["duplicates"] for r in ok),
           "editions_with_duplicates": sum(1 for r in ok if r["duplicates"]),
           "inclusion": {k: sum(1 for r in ok if k in r["optional_sections"])
                         for k in FAMILY_SECTIONS}}
    cands = sum(r["candidates"] for r in ok)
    out["suppression_rate"] = round(sum(r["suppressed"] for r in ok) / cands, 3) if cands else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="v3")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "editorial_v3_shadow"))
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    before = fingerprint(OUT_DIR, args.out)
    with tempfile.TemporaryDirectory() as tmp:
        history = history_copy(tmp)
        try:
            post = post_editions(history)
        finally:
            history.close()
    pre = pre_editions()
    after = fingerprint(OUT_DIR, args.out)
    result = {"label": args.label, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "production_state_modified": before != after,
              "post": {"summary": aggregate(post), "editions": post},
              "pre": {"summary": aggregate(pre), "editions": pre}}
    path = os.path.join(args.out, f"editorial_shadow_{args.label}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    for ed in ("post", "pre"):
        s = result[ed]["summary"]
        print(f"{args.label} {ed.upper()}: {s['editions']} editions ({s['errors']} errors) - "
              f"avg {s['avg_scenes']} scenes / {s['avg_optional']} optional / "
              f"{s['avg_duration']}s, duplicates {s['duplicates_total']}, "
              f"suppression {s['suppression_rate']}")
    print(f"production_state_modified: {result['production_state_modified']} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
