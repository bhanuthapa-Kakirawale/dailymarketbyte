"""POST freeze revalidation. Validation only - nothing here changes a rule or persists history.

    python validate_post_freeze.py replay                 # 186-session decision replay (local OHLCV)
    python validate_post_freeze.py live 2026-09-24 ...     # live mover acquisition coverage audit
    python validate_post_freeze.py render <report.json> <session> <radar_dir>
    python validate_post_freeze.py audits                  # fold everything into the audit files

Outputs go to output/post_freeze_validation/.

replay - real local OHLCV prices (NOT validated MarketReports): per session it measures Nifty
         100 universe coverage exactly as `market.get_movers_audited` defines it (a row on the
         session whose own previous row is the Nifty calendar's previous session), runs every
         observed move through `core.move_guard`, applies the OLD Movers rule (4% / 6 pts, all
         observed rows) and the NEW one (coverage gate >= 90%, then 7% / 12 pts over validated
         rows), and, on |Nifty| >= 1.5% days, checks the hook-priority rarity exception on the
         strongest unusual-volume stock (a proxy - the store has no per-session Radar output).
live   - calls the production acquisition `market.get_movers_audited` for real past sessions
         (Yahoo, 3-month window) - the coverage production would actually have recorded.
render - renders a real POST Short from a real validated report + Radar output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time

import pandas as pd

from config import OUT_DIR

VAL_DIR = os.path.join(OUT_DIR, "post_freeze_validation")
OLD_MIN_PCT, OLD_MIN_SPREAD = 4.0, 6.0


def _dump(name, obj):
    os.makedirs(VAL_DIR, exist_ok=True)
    path = os.path.join(VAL_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    return path


def _load(name):
    path = os.path.join(VAL_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- replay
def cmd_replay() -> int:
    import market
    from core.move_guard import validate_move
    from editorial.config import MOVERS_MIN_COVERAGE_PCT
    from hooks import policy as hp
    from presentation.post_plan import (MOVERS_MIN_PCT, MOVERS_MIN_SPREAD, direction_phrase,
                                        session_support, structural_event)

    universe = market.get_universe("NIFTY100")
    con = sqlite3.connect(os.path.join(OUT_DIR, "data", "market_ohlcv.db"))
    df = pd.read_sql_query("select symbol, session_date, open, high, low, close, volume from "
                           "daily_ohlcv where quality_status='OK' and source='yahoo'", con,
                           parse_dates=["session_date"])
    con.close()
    nifty = df[df.symbol == "^NSEI"].sort_values("session_date").set_index("session_date")
    nifty = nifty.rename(columns=str.capitalize)[["Open", "High", "Low", "Close"]]
    nifty["ema20"] = nifty.Close.ewm(span=20, adjust=False).mean()
    spine = list(nifty.index)
    per = {s: g.sort_values("session_date").set_index("session_date")
           for s, g in df[df.symbol.isin(list(universe))].groupby("symbol")}

    rows, flagged = [], []
    for i in range(60, len(spine)):
        day, prev = spine[i], spine[i - 1]
        window = nifty.iloc[: i + 1]
        last = window.iloc[-1]
        npct = (last.Close / window.iloc[-2].Close - 1) * 100
        ev = structural_event(window)
        sup = session_support(last.Open, last.High, last.Low, last.Close)

        observed, validated, missing, gap = [], [], [], []
        for sym in universe:
            g = per.get(sym)
            if g is None or day not in g.index:
                missing.append(sym)
                continue
            k = g.index.get_loc(day)
            if k < 1 or g.index[k - 1] != prev:
                gap.append(sym)
                continue
            r, p = g.iloc[k], g.iloc[k - 1]
            prior = g.volume.iloc[max(0, k - 20):k]
            rvol = float(r.volume / prior.mean()) if len(prior) == 20 and prior.mean() > 0 else None
            pct = (r.close / p.close - 1) * 100
            v = validate_move(close=r.close, prev_close=p.close, open_=r.open, high=r.high,
                              low=r.low, change_pct=pct, relative_volume=rvol,
                              prev_date=g.index[k - 1], expected_prev_date=prev)
            rec = {"symbol": sym, "pct": round(pct, 2), "rvol": None if rvol is None else round(rvol, 2),
                   "status": v.status}
            observed.append(rec)
            if v.publishable:
                validated.append(rec)
            if v.status != "VALIDATED":
                flagged.append({"session": day.date().isoformat(), **rec, "reason": v.reason,
                                "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                                "prev_close": p.close, "details": v.details,
                                "publishable": v.publishable})
        exp = len(universe)
        cov = round(100.0 * len(validated) / exp, 2) if exp else 0.0

        def top(rs):
            if not rs:
                return None, None
            hi = max(rs, key=lambda x: x["pct"])
            lo = min(rs, key=lambda x: x["pct"])
            return hi, lo

        og, ol = top(observed)
        old_fire = bool(og and (og["pct"] >= OLD_MIN_PCT or abs(ol["pct"]) >= OLD_MIN_PCT
                                or og["pct"] - ol["pct"] >= OLD_MIN_SPREAD))
        vg, vl = top(validated)
        gate_ok = cov >= MOVERS_MIN_COVERAGE_PCT
        new_mag = bool(vg and (abs(vg["pct"]) >= MOVERS_MIN_PCT or abs(vl["pct"]) >= MOVERS_MIN_PCT
                               or vg["pct"] - vl["pct"] >= MOVERS_MIN_SPREAD))
        new_fire = gate_ok and new_mag
        top_changed = bool(og and vg and (og["symbol"], ol["symbol"]) != (vg["symbol"], vl["symbol"]))

        hook = None
        if abs(npct) >= hp.MAJOR_INDEX_PCT:
            unusual = [x for x in validated if (x["rvol"] or 0) >= hp.UNUSUAL_RVOL
                       and abs(x["pct"]) >= hp.HIDDEN_ACTION_STOCK_PCT]
            cand = max(unusual, key=lambda x: (x["rvol"], abs(x["pct"]))) if unusual else None
            exc = bool(cand and abs(cand["pct"]) >= hp.EXCEPTIONAL_STOCK_PCT
                       and cand["rvol"] >= hp.EXCEPTIONAL_RVOL
                       and abs(cand["pct"]) >= hp.EXCEPTIONAL_INDEX_MULTIPLE * abs(npct))
            hook = {"unusual_stock_proxy": cand, "exceptional": exc,
                    "winner": "UNUSUAL_ACTIVITY (exception)" if exc else "BIG_MOVE"}
        rows.append({
            "session": day.date().isoformat(), "nifty_pct": round(npct, 2),
            "headline": direction_phrase(npct), "support": sup["text"],
            "nifty_chart": ev["family"] if ev else None,
            "coverage": {"universe_expected": exp, "universe_observed": len(observed),
                         "universe_validated": len(validated), "coverage_pct": cov,
                         "missing": len(missing), "date_gap": len(gap), "gate_ok": gate_ok},
            "movers_before": {"fires": old_fire, "top_gain": og, "top_fall": ol},
            "movers_after": {"fires": new_fire, "magnitude_fires": new_mag, "top_gain": vg,
                             "top_fall": vl, "ranking_changed_by_guard": top_changed},
            "hook_priority": hook})

    n_all = len(rows)
    # The local store's stock history starts later than the Nifty history; a session with no
    # stock rows at all says nothing about production coverage, so rates use data sessions.
    has = [r["coverage"]["missing"] < r["coverage"]["universe_expected"] for r in rows]
    data = [r for i, r in enumerate(rows) if has[i] and i > 0 and has[i - 1]]
    n = len(data)
    share = lambda k: round(k / n, 3) if n else None
    before = sum(r["movers_before"]["fires"] for r in data)
    after = sum(r["movers_after"]["fires"] for r in data)
    mag = sum(r["movers_after"]["magnitude_fires"] for r in data)
    gated = [r for r in data if not r["coverage"]["gate_ok"]]
    major = [r for r in data if r["hook_priority"]]
    major_all = [r for r in rows if r["hook_priority"]]
    summary = {
        "note": ("Replay on real local OHLCV (Nifty 100 universe, Yahoo), NOT validated "
                 "MarketReports. Movers rates measure the inclusion RULE only: no Radar "
                 "de-duplication (no per-session Radar output in the store) and no optional-"
                 "section budget. Hook rows use a proxy unusual stock (strongest validated "
                 "rvol >= 2x mover), not the Radar selector."),
        "sessions_replayed": n_all, "first": rows[0]["session"] if rows else None,
        "last": rows[-1]["session"] if rows else None,
        "sessions_with_stock_data": n,
        "first_with_stock_data": data[0]["session"] if data else None,
        "movers_fire_before_4pct_6pts": before, "movers_fire_before_share": share(before),
        "movers_fire_after_7pct_12pts_gated": after, "movers_fire_after_share": share(after),
        "movers_after_magnitude_only": mag, "movers_after_magnitude_share": share(mag),
        "coverage_gate_suppressed_sessions": len(gated),
        "coverage_gate_suppressed_list": [(r["session"], r["coverage"]["coverage_pct"],
                                           f"missing {r['coverage']['missing']}, date gap "
                                           f"{r['coverage']['date_gap']}") for r in gated],
        "coverage_pct_min": min((r["coverage"]["coverage_pct"] for r in data), default=None),
        "coverage_pct_median": (float(pd.Series([r["coverage"]["coverage_pct"] for r in data]).median())
                                if data else None),
        "guard_flagged_records": len(flagged),
        "guard_flagged_by_status": pd.Series([f["status"] for f in flagged]).value_counts().to_dict(),
        "guard_held_records": sum(1 for f in flagged if not f["publishable"]),
        "sessions_where_guard_changed_top_mover": sum(r["movers_after"]["ranking_changed_by_guard"]
                                                      for r in rows),
        "major_index_sessions_all": len(major_all),
        "major_index_sessions": len(major),
        "major_index_big_move_wins": sum(1 for r in major if not r["hook_priority"]["exceptional"]),
        "major_index_exceptions": [(r["session"], r["nifty_pct"], r["hook_priority"]["unusual_stock_proxy"])
                                   for r in major if r["hook_priority"]["exceptional"]],
        "nifty_chart_sessions": sum(1 for r in rows if r["nifty_chart"]),
        "nifty_chart_share": round(sum(1 for r in rows if r["nifty_chart"]) / n_all, 3) if n_all else None,
        "nifty_chart_by_event": pd.Series([r["nifty_chart"] or "none" for r in rows]).value_counts().to_dict(),
        "pulse_headline": pd.Series([r["headline"] for r in rows]).value_counts().to_dict(),
    }
    _dump("decision_replay_after.json", {"summary": summary, "sessions": rows})
    _dump("_replay_flagged.json", flagged)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


# --------------------------------------------------------------------------- live
def cmd_live(sessions) -> int:
    import market
    universe = market.get_universe("NIFTY100")
    con = sqlite3.connect(os.path.join(OUT_DIR, "data", "market_ohlcv.db"))
    spine = [dt.date.fromisoformat(r[0]) for r in con.execute(
        "select session_date from daily_ohlcv where symbol='^NSEI' order by 1")]
    con.close()
    out = _load("_live_coverage.json") or {}
    for s in sessions:
        day = dt.date.fromisoformat(s)
        prev = max(d for d in spine if d < day)
        t0 = time.time()
        try:
            g, lo, audit = market.get_movers_audited(universe, day, prev, 5)
            audit = dict(audit)
            audit["top_gainers"] = [(r["symbol"], round(r["pct"], 2)) for r in g]
            audit["top_losers"] = [(r["symbol"], round(r["pct"], 2)) for r in lo]
            audit["excluded"] = [{k: r.get(k) for k in ("symbol", "pct", "close", "prev_close",
                                                        "open", "high", "low", "volx", "validation")}
                                 for r in audit["excluded"]]
        except Exception as e:                   # an acquisition refusal is itself the result
            audit = {"session_date": s, "previous_session_date": prev.isoformat(),
                     "error": f"{type(e).__name__}: {e}"}
        audit["seconds"] = round(time.time() - t0, 1)
        out[s] = audit
        print(s, {k: audit.get(k) for k in ("universe_expected", "universe_observed",
                                           "universe_validated", "coverage_pct", "error")})
    _dump("_live_coverage.json", out)
    return 0


# --------------------------------------------------------------------------- render
def cmd_render(report, session, radar_dir) -> int:
    import render_daily_market_byte as r
    out = os.path.join(VAL_DIR, session)
    t0 = time.time()
    rc = r.main(["--report", report, "--radar-dir", radar_dir, "--out-dir", out,
                 "--out", os.path.join(out, "full_post.mp4")])
    with open(os.path.join(out, "render_seconds.txt"), "w") as fh:
        fh.write(f"{time.time() - t0:.1f}\n")
    return rc


# --------------------------------------------------------------------------- audits
REAL = {"2026-09-21": ("output/reports/premarket_2026-09-23.json", "output/radar"),
        "2026-09-24": ("output/reports/premarket_2026-09-25.json", "output/post_validation/radar")}
BEFORE_HOOKS = {"2026-09-21": "output/daily_market_byte_redesign/hook_plan_2026-09-21.json",
                "2026-09-24": "output/post_validation/2026-09-24/hook_plan_2026-09-24.json"}


def _jsonf(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _real_sheet(session):
    import render_daily_market_byte as r
    from hooks import post_market_sheet
    report, radar_dir = REAL[session]
    plan, pres, rp, rr, ev, uni, _ = r.load_inputs(report, radar_dir, session)
    sb = _jsonf(os.path.join(VAL_DIR, session, f"storyboard_{session}.json"))
    published = [a["instrument"] for a in sb["radar_guard"] if a["publishable"]][:3]
    by = {s["instrument"]: s for s in rr["stories"]}
    stories = [(sc["story"], by[sc["story"]["instrument"]]) for sc in rp["scenes"]
               if sc.get("role") == "STORY" and sc["story"]["instrument"] in published]
    return post_market_sheet(plan, pres, stories, ev, list(sb["post_plan"]["order"]), uni), sb


def _beats_view(sheet, beats):
    from hooks import diversity
    out = []
    for b in beats:
        beat = sheet.beat(b)
        out.append({"beat": b, "family": diversity.family(beat) if beat else None,
                    "entities": sorted(diversity.entities(sheet, beat)) if beat else None})
    return out


class _Meta:
    def __init__(self, metadata):
        self.metadata = metadata


def cmd_audits() -> int:
    from editorial.movers_gate import movers_coverage_verdict
    from hooks import diversity, post_market_sheet
    from hooks.candidates import build_candidates
    from hooks.fixtures import POST_EXAMPLES
    replay = _load("decision_replay_after.json")
    live = _load("_live_coverage.json") or {}
    flagged = _load("_replay_flagged.json") or []
    s = replay["summary"]

    real = {}
    for session in REAL:
        sheet, sb = _real_sheet(session)
        rep = _jsonf(REAL[session][0])
        real[session] = {"sheet": sheet, "sb": sb, "report": rep,
                         "gate": movers_coverage_verdict(_Meta(rep.get("metadata") or {})),
                         "hook": _jsonf(os.path.join(VAL_DIR, session, f"hook_plan_{session}.json")),
                         "before": _jsonf(BEFORE_HOOKS[session])}

    # ---- coverage
    live_keys = ("universe_expected", "universe_observed", "universe_validated", "coverage_pct",
                 "skipped_date_gap", "missing", "error", "top_gainers", "top_losers")
    _dump("coverage_audit.json", {
        "rule": "Movers / top gainer / top loser publish only at coverage_pct >= 90, where "
                "coverage_pct = universe_validated / universe_expected (acquisition audit in "
                "report.metadata['movers_coverage']); no record = UNKNOWN = suppressed.",
        "real_reports": {k: {"report_id": v["report"]["report_id"], "gate": v["gate"],
                             "reported_top_gainers": [(g["symbol"], round(g["change_pct"], 2))
                                                      for g in v["report"]["gainers"]],
                             "reported_top_losers": [(g["symbol"], round(g["change_pct"], 2))
                                                     for g in v["report"]["losers"]],
                             "live_full_universe_reacquisition":
                                 {kk: (live.get(k) or {}).get(kk) for kk in live_keys}}
                         for k, v in real.items()},
        "live_acquisition": {k: {kk: v.get(kk) for kk in live_keys} for k, v in live.items()},
        "replay": {"sessions_with_stock_data": s["sessions_with_stock_data"],
                   "suppressed_sessions": s["coverage_gate_suppressed_sessions"],
                   "suppressed": s["coverage_gate_suppressed_list"],
                   "coverage_pct_median": s["coverage_pct_median"],
                   "per_session": [{"session": r["session"], **r["coverage"]}
                                   for r in replay["sessions"]]},
    })

    # ---- extreme moves
    radar = {k: v["sb"]["radar_guard"] for k, v in real.items()}
    held_r = [dict(a, session=k) for k, v in radar.items() for a in v if not a["publishable"]]
    _dump("extreme_move_audit.json", {
        "rule": "core/move_guard.py GUARD_VERSION 1.0 - see its docstring; no symbol hard-coded.",
        "summary": {"replay_flagged": len(flagged),
                    "replay_by_status": s["guard_flagged_by_status"],
                    "replay_held": s["guard_held_records"],
                    "replay_published_extreme": sum(1 for f in flagged if f["publishable"]),
                    "replay_sessions_top_mover_changed": s["sessions_where_guard_changed_top_mover"],
                    "radar_real_stories_checked": sum(len(v) for v in radar.values()),
                    "radar_real_held": [(a["session"], a["instrument"],
                                         round(a["price_change_pct"], 2), a["status"])
                                        for a in held_r],
                    "live_acquisition_held": {k: v.get("excluded") for k, v in live.items()}},
        "replay_records": flagged, "radar_real_sessions": radar,
    })

    # ---- hook priority
    major = [r for r in replay["sessions"] if r["hook_priority"]]
    pick = lambda d, keys: {x: d.get(x) for x in keys}
    _dump("hook_priority_audit.json", {
        "rule": real["2026-09-24"]["hook"]["priority_rule"]["rule"],
        "decided_by": "hooks.candidates.major_index_priority (deterministic, before Gemini)",
        "real_sessions": {k: {"nifty_pct": v["hook"]["priority_rule"]["nifty_pct"],
                              "before": pick(v["before"], ("archetype", "candidate_id",
                                                           "curiosity_line", "source", "candidates")),
                              "after": pick(v["hook"], ("archetype", "candidate_id", "curiosity_line",
                                                        "source", "fallback_reason", "candidates",
                                                        "priority_rule"))}
                          for k, v in real.items()},
        "replay_major_index_sessions": {
            "count": len(major),
            "big_move_wins": sum(1 for r in major if not r["hook_priority"]["exceptional"]),
            "exceptions": [r for r in major if r["hook_priority"]["exceptional"]],
            "note": "proxy unusual stock = strongest validated rvol >= 2x mover (no Radar in store)",
            "sessions": [{"session": r["session"], "nifty_pct": r["nifty_pct"], **r["hook_priority"]}
                         for r in major]},
    })

    # ---- teaser diversity
    fixtures = {}
    for name, fn in POST_EXAMPLES.items():
        b = fn()
        sh = post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"], b["sections"],
                               b["universe"])
        fixtures[name] = [{"candidate": c.candidate_id, "beats": _beats_view(sh, c.default_beats),
                           "entity_clashes": diversity.entity_clashes(sh, c.default_beats)}
                          for c in build_candidates(sh)]
    realt = {}
    for k, v in real.items():
        sh = v["sheet"]
        before = list(v["before"]["teaser_beats"])
        realt[k] = {"before": {"source": v["before"].get("source"), "beats": _beats_view(sh, before),
                               "entity_clashes": diversity.entity_clashes(sh, before)},
                    "after": {"source": v["hook"]["source"],
                              "beats": _beats_view(sh, v["hook"]["teaser_beats"]),
                              "entity_clashes": diversity.entity_clashes(sh, v["hook"]["teaser_beats"])},
                    "all_candidates_after": [{"candidate": c.candidate_id, "beats": list(c.default_beats),
                                              "entity_clashes": diversity.entity_clashes(sh, c.default_beats)}
                                             for c in build_candidates(sh)]}
    clashes = (sum(1 for v in fixtures.values() for c in v if c["entity_clashes"])
               + sum(1 for v in realt.values() for c in v["all_candidates_after"] if c["entity_clashes"]))
    _dump("teaser_diversity_audit.json", {
        "rule": "POST: one beat per entity; different families preferred, played INDEX -> "
                "BREADTH (sector/flow) -> STOCK; fewer beats rather than a repeat. Gemini picks "
                "that repeat an entity fail structural validation.",
        "real_sessions": realt, "synthetic_fixtures": fixtures,
        "candidates_with_entity_clashes": clashes,
    })
    print("audits written")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("replay")
    sub.add_parser("audits")
    a = sub.add_parser("live")
    a.add_argument("sessions", nargs="+")
    b = sub.add_parser("render")
    b.add_argument("report")
    b.add_argument("session")
    b.add_argument("radar_dir")
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        return cmd_replay()
    if args.cmd == "audits":
        return cmd_audits()
    if args.cmd == "live":
        return cmd_live(args.sessions)
    return cmd_render(args.report, args.session, args.radar_dir)


if __name__ == "__main__":
    sys.exit(main())
