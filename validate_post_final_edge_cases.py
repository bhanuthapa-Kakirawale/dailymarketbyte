"""POST final edge-case patch - real validation (ops script). Nothing here uploads.

    python validate_post_final_edge_cases.py benchmark     # 22 Sep recap-session fallback audit
    python validate_post_final_edge_cases.py live          # 22/23/24 Sep time-travelled POST runs
    python validate_post_final_edge_cases.py render        # 24 Sep COROMANDEL scene + full POST
    python validate_post_final_edge_cases.py all

Outputs: output/post_final_edge_cases/. Real Yahoo history is truncated at each session and the
clock is set to the scheduled run (next weekday 07:40 IST), exactly what production would see;
NSE's official end-of-day index file is fetched for real. NSE's cookie API, Gemini and global
cues are stubbed (as in validate_session_alignment). No store is modified: the local SQLite
stores are hashed before and after and the audit says whether they changed.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import sys
import time

from config import IST, OUT_DIR

VAL_DIR = os.path.join(OUT_DIR, "post_final_edge_cases")
DATA_DIR = os.path.join(OUT_DIR, "data")
GAP = dt.date(2026, 9, 22)
SESSIONS = (dt.date(2026, 9, 22), dt.date(2026, 9, 23), dt.date(2026, 9, 24))
REPORT_24 = os.path.join(OUT_DIR, "reports", "premarket_2026-09-25.json")
RADAR_24 = os.path.join(OUT_DIR, "benchmark_gap_recovery", "2026-09-24", "radar")
STORES = ("market_ohlcv.db", "radar_candidate_history.db", "market_history.db")


def _dump(rel, obj):
    path = os.path.join(VAL_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    print(f"  wrote {path}")
    return path


def _store_hashes() -> dict:
    out = {}
    for name in STORES:
        p = os.path.join(DATA_DIR, name)
        if os.path.exists(p):
            with open(p, "rb") as fh:
                out[name] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _run_time(session: dt.date) -> dt.datetime:
    """The scheduled POST run for `session`: the next weekday, 07:40 IST."""
    day = session + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(7, 40), IST)


# Yahoo has since BACKFILLED 2026-09-22 on its Indian index feeds (checked 25 Sep evening:
# ^NSEI 23329.0, ^NSEBANK 56215.55, ...). Production at 23 Sep 07:40 saw none of them (verified
# 2026-09-25 against every Yahoo Indian index feed). `as_served=True` reconstructs that state:
# the 22 Sep bar is removed from every Indian INDEX series ("^..." tickers) - stocks keep it,
# as they had it then. The backfilled bars are kept aside as an independent cross-check.
INDIAN_INDEX_PREFIXES = ("^NSE", "^CNX", "^INDIAVIX", "^BSESN")


@contextlib.contextmanager
def _travel(session: dt.date, now: dt.datetime | None = None, as_served: bool = True):
    import market
    from validate_session_alignment import _time_travel
    now = now or _run_time(session)
    saved = market.now_ist
    with _time_travel(session, before=False):
        hist, nifty = market.history, market._nifty_history_with_backfill_check

        def _strip(ticker, d):
            if as_served and ticker.startswith(INDIAN_INDEX_PREFIXES):
                return d[d.index.date != GAP]
            return d
        market.history = lambda t, period="1y": _strip(t, hist(t, period))
        market._nifty_history_with_backfill_check = (
            lambda period="1y", max_extra_attempts=2: _strip("^NSEI", nifty(period)))
        market.now_ist = lambda: now
        try:
            yield now
        finally:
            market.now_ist = saved
            market.history, market._nifty_history_with_backfill_check = hist, nifty


def _yahoo_backfill_crosscheck(records) -> list:
    """Compare every VALIDATED NSE-recovered 22 Sep close with Yahoo's later backfill."""
    import market
    out, seen = [], set()
    for r in records or []:
        if r.get("validation_status") != "VALIDATED" or r.get("session_date") != GAP.isoformat()                 or r["index"] in seen:
            continue
        seen.add(r["index"])
        try:
            d = market.history(r["index"], "1mo")
            y = next((float(v) for ts, v in d.Close.items() if ts.date() == GAP), None)
        except Exception:
            y = None
        c = r["values"]["close"]
        out.append({"index": r["index"], "role": r.get("role", "INTERNAL_GAP"),
                    "nse_close": c, "yahoo_backfilled_close": y,
                    "abs_diff": None if y is None else round(abs(y - c), 4),
                    "agrees_within_0.05pct": None if y is None else abs(y / c - 1) <= 0.0005})
    return out


# --------------------------------------------------------------------------- benchmark
def cmd_benchmark() -> int:
    import market
    before = _store_hashes()
    market._ARCHIVE_CACHE.clear()
    with _travel(GAP) as now:
        primary = market._nifty_history_with_backfill_check("1y")
        m = market.get_market()
        sector_log = []
        sectors = market.get_sectors(m["recap_date"], m["prev_date"], {},
                                     session_dates=m["session_dates"], recovery_log=sector_log)
    # intraday: the same missing session asked for at 14:00 IST on its own day - never fetched
    fetched = []
    real_fetch = market.nse_index_close_archive

    def spy(day):
        fetched.append(day)
        return real_fetch(day)
    intraday_now = dt.datetime.combine(GAP, dt.time(14, 0), IST)
    with _travel(GAP, now=intraday_now):
        d = market._drop_non_sessions(primary, market.session_calendar(
            [ts.date() for ts in primary.index]))
        out_i, recs_i = market.recover_recap_session(d, "^NSEI", now=intraday_now, fetch=spy)
    # independent forward check: NSE's own NEXT-session file implies the recovered close
    nxt = dt.date(2026, 9, 23)
    nf = market.nse_index_close_archive(nxt)
    rec = next((r for r in m["session_alignment"]["benchmark_recovery"]
                if r["session_date"] == GAP.isoformat()), None)
    fwd = None
    if rec and "rows" in nf:
        row = nf["rows"]["NIFTY 50"]
        c, chg = market._archive_float(row, "Closing Index Value"), market._archive_float(row, "Points Change")
        fwd = {"next_session": nxt.isoformat(), "source_url": nf["url"], "nse_next_close": c,
               "nse_next_points_change": chg, "implied_gap_close": round(c - chg, 4),
               "recovered_gap_close": m["close"],
               "matches": abs((c - chg) - m["close"]) < 0.01}
    _dump("benchmark_current_session_fallback_audit.json", {
        "case": "Yahoo ^NSEI has no bar for Tue 2026-09-22 (a real NSE session); the scheduled "
                "23 Sep 07:40 POST run could only see 21 Sep",
        "run_time_simulated": now.isoformat(),
        "primary_last_bar": str(primary.index[-1].date()),
        "fallback": market.INDEX_FALLBACK_SOURCE,
        "rules": {"used_only_when": ["canonical calendar (NSE holiday list) covers the date and "
                                     "calls it a SESSION",
                                     f"the session is FINAL ({market.SESSION_FINAL_TIME} IST on "
                                     "its own date has passed) - never intraday",
                                     "the primary series ends before it (recap session missing)",
                                     "the official NSE end-of-day row validates",
                                     f"at most {market.RECAP_RECOVERY_MAX_SESSIONS} trailing "
                                     "sessions (more = broken feed, nothing filled)"],
                  "validation": ["source row dated the session", "OHLC internally consistent",
                                 "implied previous close (close - points change) within "
                                 f"{market.RECOVERY_ANCHOR_TOLERANCE:.2%} of the previous "
                                 "canonical close already held", "stated % change agrees",
                                 f"|change| < {market.RECOVERY_MAX_ABS_PCT}%"],
                  "never": ["interpolate", "infer from constituents", "use intraday values",
                            "write fallback rows into the Yahoo OHLCV cache"],
                  "if_both_fail": "main.collect raises SessionAlignmentError "
                                  "(BENCHMARK_MISSING_RECAP_SESSION) - publication blocked"},
        "result": {"recap_date": str(m["recap_date"]), "prev_date": str(m["prev_date"]),
                   "close": m["close"], "prev_close": m["prev"], "points_change": m["chg"],
                   "pct": round(m["pct"], 4), "close_source": m["close_source"],
                   "previous_close_source": m["prev_source"],
                   "alignment_status": m["session_alignment"]["status"]},
        "nifty_recap_record": rec,
        "forward_continuity_check": fwd,
        "other_index_records": m["session_alignment"].get("index_recovery"),
        "sectors": sectors,
        "sector_records": sector_log,
        "yahoo_later_backfill_crosscheck": _yahoo_backfill_crosscheck(
            m["session_alignment"]["benchmark_recovery"]
            + (m["session_alignment"].get("index_recovery") or []) + sector_log),
        "replay_note": "Yahoo has since backfilled 22 Sep on its index feeds; the replay removes "
                       "that bar from every Indian INDEX series to reproduce what the 23 Sep "
                       "07:40 run was served (verified at the time). Stocks keep their rows.",
        "intraday_forbidden_check": {"now_ist": intraday_now.isoformat(),
                                     "archive_fetches": [str(x) for x in fetched],
                                     "records": recs_i,
                                     "primary_last_bar_after": str(out_i.index[-1].date()),
                                     "passed": not fetched and not recs_i},
        "stores_unchanged": before == _store_hashes(),
    })
    return 0


# --------------------------------------------------------------------------- live
def _run_live(session: dt.date) -> dict:
    import main
    import market
    from validate_session_alignment import _summarize_report
    out = {}
    with _travel(session) as now:
        today = now.date()
        out["run_time_simulated"] = now.isoformat()
        saved = main.OUT_DIR
        main.OUT_DIR = os.path.join(VAL_DIR, "_state")
        os.makedirs(main.OUT_DIR, exist_ok=True)
        try:
            raw_up = main.collect(argparse.Namespace(demo=False, upload=True, force=False), today)
            out["upload_mode_decision"] = "PROCEEDS" if raw_up else "SKIPPED"
        except market.SessionAlignmentError as exc:
            raw_up = None
            out["upload_mode_decision"] = f"BLOCKED: {str(exc)[:400]}"
        except Exception as exc:
            raw_up = None
            out["upload_mode_decision"] = f"FAILED: {type(exc).__name__}: {str(exc)[:400]}"
        finally:
            main.OUT_DIR = saved
        if raw_up is None:
            out["outcome"] = "NOT_PUBLISHABLE"
            return out
        raw = raw_up
        if raw["m"]["recap_date"] != session:
            out.update({"outcome": "WRONG_SESSION", "recap_date": str(raw["m"]["recap_date"])})
            return out
        report = main.build_report(raw, today, demo=False)
        ok = main.check_publication(report)
        s = _summarize_report(report, ok, raw)
        s["nifty"]["close_source"] = raw["m"].get("close_source")
        s["nifty"]["previous_close_source"] = raw["m"].get("prev_source")
        s["nifty"]["prev_date"] = str(raw["m"]["prev_date"])
        s["sectors_detail"] = raw["sec"]
        from core import Metric
        s["nifty_facts"] = [
            {"metric": f.metric.value, "status": f.validation_status.value,
             "observations": [{"source": o.source_name, "source_type": o.source_type.value,
                               "value": o.value, "metadata": dict(o.metadata)}
                              for o in f.observations]}
            for f in report.facts if f.instrument == "NIFTY 50"
            and f.metric in (Metric.INDEX_CLOSE, Metric.INDEX_CHANGE_PCT)]
        s["blocking_issues"] = list(report.validation_summary.blocking_issues)
        out.update({"outcome": "OK", **s})
    return out


def cmd_live() -> int:
    before = _store_hashes()
    prior_dir = os.path.join(OUT_DIR, "benchmark_gap_recovery")
    for session in SESSIONS:
        iso = session.isoformat()
        print(f"== {iso}")
        prior = None
        p = os.path.join(prior_dir, iso, "report_summary.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                prior = (json.load(fh) or {}).get("result")
        after = _run_live(session)
        _dump(f"{iso}/report_summary.json", {"session": iso, "result": after,
                                             "before_this_patch": prior})
    _dump("stores_check.json", {"before": before, "after": _store_hashes(),
                                "unchanged": before == _store_hashes()})
    return 0


# --------------------------------------------------------------------------- render
def cmd_render() -> int:
    import render_daily_market_byte as r
    from daily_video import Composer
    from daily_video.storyboard import Storyboard
    out = os.path.join(VAL_DIR, "2026-09-24")
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    rc = r.main(["--report", REPORT_24, "--radar-dir", RADAR_24, "--out-dir", out,
                 "--out", os.path.join(out, "full_post_final.mp4"), "--no-hook-ai"])
    with open(os.path.join(out, f"storyboard_2026-09-24.json"), encoding="utf-8") as fh:
        sbd = json.load(fh)
    # the COROMANDEL scene on its own
    plan, pres, rp, rr, ev, uni, src = r.load_inputs(REPORT_24, RADAR_24, "2026-09-24")
    from daily_video import build_storyboard
    sb = build_storyboard(plan, pres, rp, rr, ev, uni, src, hook_ai=False)
    spec = next(s for s in sb.scenes if s.kind == "RADAR_STORY" and s.texts["symbol"] == "COROMANDEL")
    one = Storyboard(session_date=sb.session_date, date_label=sb.date_label, kicker=sb.kicker,
                     scenes=[spec])
    comp = Composer(one)
    res = comp.render(os.path.join(out, "coromandel_scene.mp4"))
    frames = {}
    for name, t in (("first_frame", 0.02), ("volume_rising", 1.6), ("spike", 2.7),
                    ("settled", spec.freeze["t"])):
        img, _, qa = comp.freeze(0, t)
        img.convert("RGB").save(os.path.join(out, f"coromandel_{name}.png"))
        frames[name] = {"t": t, "qa_passed": qa["passed"], "marks": qa["marks"],
                        "qa_scope": ("settled frame: full freeze-frame QA" if name == "settled"
                                     else "mid-animation: evidence marks not all drawn yet by "
                                          "design; only the settled frame is gated")}
    _dump("2026-09-24/coromandel_scene_check.json", {
        "before": {"event_family": "TEXT", "panel": "empty card",
                   "takeaway": "Something changed on the chart with unusually high volume.",
                   "frame": "output/benchmark_gap_recovery/2026-09-24/freeze_frames/"
                            "radar_coromandel.png"},
        "after": {"event_family": spec.data["event_family"], "texts": spec.texts,
                  "support": {k: v for k, v in spec.data["support"].items() if k != "volume"},
                  "price_change_pct": next(s["price_change_pct"] for s in rr["stories"]
                                           if s["instrument"] == "COROMANDEL"),
                  "internal_direction": next(s["direction"] for s in rr["stories"]
                                             if s["instrument"] == "COROMANDEL"),
                  "warnings": spec.data["warnings"], "frames": frames, "render": res},
        "full_post": {"render_rc": rc, "total_duration": sbd.get("total_duration"),
                      "scenes": [(s["kind"], s.get("duration")) for s in sbd.get("scenes", [])]},
        "render_seconds": round(time.time() - t0, 1)})
    return 0 if rc == 0 and res.get("ok") else 1


def main_() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["benchmark", "live", "render", "all"])
    a = ap.parse_args()
    if a.cmd == "all":
        return max(cmd_benchmark(), cmd_live(), cmd_render())
    return {"benchmark": cmd_benchmark, "live": cmd_live, "render": cmd_render}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main_())
