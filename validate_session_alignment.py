"""Session-alignment patch validation. Validation only - changes no rule, persists no history.

    python validate_session_alignment.py known                 # known provider holiday rows
    python validate_session_alignment.py replay                # before/after decision replay
    python validate_session_alignment.py live 2026-09-15 ...   # time-travelled POST dry-run
    python validate_session_alignment.py radar 2026-09-15 ...  # Radar dry-run, before/after
    python validate_session_alignment.py audit                 # fold into the summary audit

Outputs go to output/session_alignment_validation/.

BEFORE = the pre-patch logic: a symbol's previous close is its own previous provider row, and
the session spine is the ^NSEI bar dates. AFTER = the patch: provider rows on canonical
non-sessions are dropped first, the previous session is `core.trading_calendar`'s canonical
previous session, and the benchmark itself must carry it (else a session-level failure).

`live` and `radar` run the real production code for a PAST session by truncating every
Yahoo series at that session (Nifty, Bank Nifty, VIX, sector indices); NSE/Gemini/global cues
are stubbed out (they only ever describe "now", never a past session). Nothing is rendered,
uploaded or written to canonical history. `radar` runs `run_daily_radar(dry_run=True)` against
a SANDBOX copy of output/data, so even the OHLCV write-through never touches the real store.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys

import pandas as pd

import config
from config import OUT_DIR

VAL_DIR = os.path.join(OUT_DIR, "session_alignment_validation")
STORE = os.path.join(OUT_DIR, "data", "market_ohlcv.db")
KNOWN = ["2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-09-22"]
TOP_N = 5


def _dump(rel, obj):
    path = os.path.join(VAL_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    print(f"  wrote {path}")
    return path


def _load(rel):
    path = os.path.join(VAL_DIR, rel)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _store_frame():
    con = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
    df = pd.read_sql_query("select symbol, session_date, open, high, low, close, volume from "
                           "daily_ohlcv where quality_status='OK' and source='yahoo'", con)
    con.close()
    df["session_date"] = pd.to_datetime(df["session_date"]).dt.date
    return df


# --------------------------------------------------------------------------- known
def cmd_known() -> int:
    from core.trading_calendar import SessionCalendar
    df = _store_frame()
    index_dates = sorted(df[df.symbol == "^NSEI"].session_date)
    cal = SessionCalendar.from_index_dates(index_dates)
    stocks = df[df.symbol != "^NSEI"]
    out = []
    for iso in KNOWN:
        d = dt.date.fromisoformat(iso)
        day = stocks[stocks.session_date == d].set_index("symbol")
        prev_idx = max(x for x in index_dates if x < d)
        prev = stocks[stocks.session_date == prev_idx].set_index("symbol")
        both = day.join(prev, rsuffix="_prev", how="inner")
        nxt = min(x for x in cal.sessions_between(d + dt.timedelta(1), d + dt.timedelta(10)))
        out.append({
            "date": iso, "weekday": d.strftime("%A"),
            "nse_holiday_list": d in cal.holidays.get(d.year, ()),
            "canonical_status": cal.status(d),
            "benchmark_has_bar": d in set(index_dates),
            "stock_rows": int(len(day)),
            "rows_close_equals_previous_close": int((abs(both.close - both.close_prev) < 1e-9).sum()),
            "rows_open_high_low_close_equal": int(((day.open == day.high) & (day.high == day.low)
                                                   & (day.low == day.close)).sum()),
            "rows_zero_volume": int((day.volume.fillna(0) == 0).sum()),
            "classification": ("PROVIDER_HOLIDAY_PLACEHOLDER" if cal.status(d) == "NON_SESSION"
                               else "REAL_SESSION_MISSING_FROM_BENCHMARK"),
            "handling": ("excluded from all analytics; kept in the raw OHLCV cache"
                         if cal.status(d) == "NON_SESSION" else
                         "kept: a real session. The benchmark lacks it, so the NEXT session's "
                         "index one-session change cannot be established from Yahoo"),
            "next_canonical_session": nxt.isoformat(),
            "next_session_canonical_previous": cal.previous_session(nxt).isoformat(),
            "next_session_benchmark_previous_bar": max(x for x in index_dates if x < nxt).isoformat(),
        })
    _dump("known_holiday_rows.json", {
        "source": "local OHLCV store (raw provider cache, read-only) + NSE holiday master",
        "benchmark_missing_sessions": [x.isoformat() for x in cal.benchmark_gaps()],
        "rows": out})
    return 0


# --------------------------------------------------------------------------- replay
def cmd_replay() -> int:
    import market
    from core.move_guard import validate_move
    from core.trading_calendar import AlignmentAudit, SessionCalendar, align_rows
    from editorial.config import MOVERS_MIN_COVERAGE_PCT
    from presentation.post_plan import MOVERS_MIN_PCT, MOVERS_MIN_SPREAD, structural_event

    universe = market.get_universe("NIFTY100")
    df = _store_frame()
    nifty = df[df.symbol == "^NSEI"].sort_values("session_date")
    index_dates = list(nifty.session_date)
    cal = SessionCalendar.from_index_dates(index_dates)
    nifty_df = nifty.set_index(pd.DatetimeIndex(pd.to_datetime(nifty.session_date))).rename(
        columns=str.capitalize)[["Open", "High", "Low", "Close"]]
    nifty_df["ema20"] = nifty_df.Close.ewm(span=20, adjust=False).mean()
    raw = {s: g.sort_values("session_date").to_dict("records")
           for s, g in df[df.symbol != "^NSEI"].groupby("symbol")}
    audit = AlignmentAudit()
    aligned = {s: align_rows(rows, cal, date_of=lambda r: r["session_date"], audit=audit)
               for s, rows in raw.items()}
    stock_start = min(df[df.symbol != "^NSEI"].session_date)

    def evaluate(day, prev, series_by_symbol, syms):
        observed, validated, gap, missing, suspicious, rvols = [], [], [], [], [], {}
        for s in syms:
            rows = series_by_symbol.get(s) or []
            pos = {r["session_date"]: i for i, r in enumerate(rows)}
            if day not in pos:
                missing.append(s)
                continue
            k = pos[day]
            if k < 1 or rows[k - 1]["session_date"] != prev:
                gap.append(s)
                continue
            r, p = rows[k], rows[k - 1]
            prior = [x["volume"] for x in rows[max(0, k - 20):k]]
            rvol = (r["volume"] / (sum(prior) / 20)) if len(prior) == 20 and sum(prior) > 0 else None
            pct = (r["close"] / p["close"] - 1) * 100
            v = validate_move(close=r["close"], prev_close=p["close"], open_=r["open"],
                              high=r["high"], low=r["low"], change_pct=pct, relative_volume=rvol,
                              prev_date=p["session_date"], expected_prev_date=prev)
            rec = {"symbol": s, "pct": round(pct, 2), "rvol": None if rvol is None else round(rvol, 3)}
            rvols[s] = rec["rvol"]
            observed.append(rec)
            (validated if v.publishable else suspicious).append({**rec, "status": v.status})
        exp = len(syms)
        cov = round(100.0 * len(validated) / exp, 2) if exp else 0.0
        hi = max(validated, key=lambda x: x["pct"]) if validated else None
        lo = min(validated, key=lambda x: x["pct"]) if validated else None
        mag = bool(hi and (abs(hi["pct"]) >= MOVERS_MIN_PCT or abs(lo["pct"]) >= MOVERS_MIN_PCT
                           or hi["pct"] - lo["pct"] >= MOVERS_MIN_SPREAD))
        return {"observed": len(observed), "validated": len(validated), "date_gap": len(gap),
                "missing": len(missing), "coverage_pct": cov,
                "acquisition": "OK" if len(observed) >= 2 * TOP_N else "ABORT_TOO_FEW_CLEAN_ROWS",
                "movers_fire": bool(cov >= MOVERS_MIN_COVERAGE_PCT and mag),
                "top_gain": hi, "top_fall": lo,
                "suspicious_moves": sorted(suspicious, key=lambda x: x["symbol"]),
                "_rvol": rvols}

    sessions = sorted(set(index_dates) | set(cal.sessions_between(index_dates[0], index_dates[-1])))
    rows = []
    for day in sessions:
        if day <= stock_start:
            continue
        on_index = day in set(index_dates)
        prior_index = [d for d in index_dates if d < day]
        before = after = None
        if on_index and prior_index:
            b = evaluate(day, prior_index[-1], raw, list(universe))
            b["status"] = "OK" if b["acquisition"] == "OK" else "FAILED"
            before = b
        can_prev = cal.previous_session(day)
        a = evaluate(day, can_prev, aligned, list(universe)) if can_prev else None
        if a is not None:
            if not on_index:
                a["status"] = "NOT_A_POST_RECAP_SESSION (benchmark has no bar for it)"
            elif can_prev not in set(index_dates):
                a["status"] = "SESSION_LEVEL_FAIL: BENCHMARK_MISSING_PREVIOUS_SESSION"
            else:
                a["status"] = "OK" if a["acquisition"] == "OK" else "FAILED"
        after = a
        window = nifty_df[nifty_df.index.date <= day] if on_index else None
        chart_before = structural_event(window) if on_index else None
        chart_after = chart_before if (after and after["status"] == "OK") else None
        rvol_delta = None
        if before and after:
            common = [s for s in before["_rvol"] if s in after["_rvol"]
                      and before["_rvol"][s] is not None and after["_rvol"][s] is not None]
            deltas = [abs(after["_rvol"][s] - before["_rvol"][s]) for s in common]
            rvol_delta = {"symbols_compared": len(common),
                          "changed": sum(1 for x in deltas if x > 1e-6),
                          "max_abs_delta": round(max(deltas), 3) if deltas else 0.0}
        for x in (before, after):
            if x:
                x.pop("_rvol", None)
        rows.append({"session": day.isoformat(),
                     "benchmark_bar": on_index,
                     "prev_before": prior_index[-1].isoformat() if (on_index and prior_index) else None,
                     "prev_after": can_prev.isoformat() if can_prev else None,
                     "before": before, "after": after,
                     "nifty_chart_before": chart_before["family"] if chart_before else None,
                     "nifty_chart_after": chart_after["family"] if chart_after else None,
                     "rvol_change": rvol_delta})

    def key(r, side, f):
        x = r[side]
        return None if x is None else (x[f] if f != "suspicious" else
                                       [s["symbol"] for s in x["suspicious_moves"]])
    changed = [r for r in rows if any(key(r, "before", f) != key(r, "after", f)
                                      for f in ("status", "coverage_pct", "movers_fire", "suspicious"))
               or r["nifty_chart_before"] != r["nifty_chart_after"]]
    summary = {
        "note": ("Real local OHLCV (Nifty 100 universe, Yahoo raw cache, read-only). Rules applied "
                 "exactly as validate_post_freeze.py: coverage gate >= 90% then 7% / 12 pts over "
                 "validated moves; move guard on every observed move."),
        "sessions_replayed": len(rows), "first": rows[0]["session"], "last": rows[-1]["session"],
        "failed_before": [r["session"] for r in rows if r["before"] and r["before"]["status"] != "OK"],
        "failed_after": [{"session": r["session"], "status": r["after"]["status"]}
                         for r in rows if r["after"] and r["after"]["status"] != "OK"],
        "movers_fire_before": sum(1 for r in rows if r["before"] and r["before"]["movers_fire"]),
        "movers_fire_after": sum(1 for r in rows if r["after"] and r["after"]["status"] == "OK"
                                 and r["after"]["movers_fire"]),
        "sessions_with_any_change": len(changed),
        "changes": [{"session": r["session"],
                     "status": [key(r, "before", "status"), key(r, "after", "status")],
                     "coverage_pct": [key(r, "before", "coverage_pct"), key(r, "after", "coverage_pct")],
                     "movers_fire": [key(r, "before", "movers_fire"), key(r, "after", "movers_fire")],
                     "suspicious": [key(r, "before", "suspicious"), key(r, "after", "suspicious")],
                     "nifty_chart": [r["nifty_chart_before"], r["nifty_chart_after"]],
                     "rvol_change": r["rvol_change"]} for r in changed],
        "rvol_sessions_changed": [{"session": r["session"], **r["rvol_change"]} for r in rows
                                  if r["rvol_change"] and r["rvol_change"]["changed"]],
        "alignment_audit": audit.to_dict(),
    }
    _dump("replay_before_after.json", {"summary": summary, "sessions": rows})
    print(json.dumps({k: v for k, v in summary.items() if k not in ("changes", "rvol_sessions_changed")},
                     indent=1, default=str)[:3000])
    return 0


# --------------------------------------------------------------------------- live (time travel)
@contextlib.contextmanager
def _time_travel(session: dt.date, before: bool):
    """Patch `market`/`news` so the production collect() describes `session` from real Yahoo
    data truncated at that date. `before=True` restores the pre-patch behaviour (no non-session
    filter, previous session = the benchmark's previous bar)."""
    import market
    import news
    saved = {}

    def patch(mod, name, val):
        saved[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)

    real_history, real_nifty = market.history, market._nifty_history_with_backfill_check
    cache = {}

    def history(ticker, period="1y"):
        if ticker not in cache:
            cache[ticker] = real_history(ticker, "1y")
        d = cache[ticker]
        return d[d.index.date <= session]

    def nifty(period="1y", max_extra_attempts=2):
        if "^NSEI" not in cache:
            cache["^NSEI"] = real_nifty("1y", 0)
        d = cache["^NSEI"]
        return d[d.index.date <= session]

    class _NoNSE:
        def get(self, path):
            raise RuntimeError("NSE stubbed in time-travel validation")

    patch(market, "history", history)
    patch(market, "_nifty_history_with_backfill_check", nifty)
    patch(market, "NSE", _NoNSE)
    patch(market, "get_globals", lambda: [])
    patch(news, "ai_pass", lambda *a, **k: ({}, "", []))
    if before:
        patch(market, "_drop_non_sessions", lambda d, cal, audit=None: d)
        patch(market, "check_index_session_alignment",
              lambda dates, cal=None: {"status": "PRE_PATCH_NOT_CHECKED"})
    try:
        yield
    finally:
        for (mod, name), val in saved.items():
            setattr(mod, name, val)


def _summarize_report(report, ok, raw) -> dict:
    md = report.metadata or {}
    cov = md.get("movers_coverage") or {}
    return {
        "report_id": report.report_id, "session_date": str(report.session_date),
        "previous_session_date": md.get("previous_session_date"),
        "publication_ready": bool(report.publication_ready), "check_publication": ok,
        "nifty": {"close": raw["m"]["close"], "pct": round(raw["m"]["pct"], 4),
                  "prev_close": raw["m"]["prev"]},
        "sectors": len(raw["sec"]),
        "movers_coverage": {k: cov.get(k) for k in ("universe_expected", "universe_observed",
                                                    "universe_validated", "coverage_pct")},
        "skipped_date_gap": len(cov.get("skipped_date_gap") or []),
        "move_guard_held": [(x["symbol"], round(x["pct"], 2), x["validation"]["status"])
                            for x in cov.get("excluded") or []],
        "top_gainers": [(g["symbol"], round(g["pct"], 2), g["prev_date"]) for g in raw["gainers"]],
        "top_losers": [(g["symbol"], round(g["pct"], 2), g["prev_date"]) for g in raw["losers"]],
        "session_alignment": md.get("session_alignment"),
        "stock_session_alignment": cov.get("session_alignment"),
    }


def _run_live(session: dt.date, before: bool) -> dict:
    import main
    today = session + dt.timedelta(days=1)
    args = argparse.Namespace(demo=False, upload=False, force=True)
    with _time_travel(session, before):
        try:
            raw = main.collect(args, today)
        except Exception as exc:
            return {"outcome": "FAILED", "stage": "acquisition",
                    "error_type": type(exc).__name__, "error": str(exc)[:600]}
        if raw["m"]["recap_date"] != session:
            return {"outcome": "WRONG_SESSION", "recap_date": str(raw["m"]["recap_date"])}
        report = main.build_report(raw, today, demo=False)
        ok = main.check_publication(report)
        return {"outcome": "OK", **_summarize_report(report, ok, raw)}


def cmd_live(sessions) -> int:
    for iso in sessions:
        d = dt.date.fromisoformat(iso)
        print(f"== {iso} before")
        b = _run_live(d, before=True)
        print(f"== {iso} after")
        a = _run_live(d, before=False)
        _dump(f"{iso}/report_summary.json", {"session": iso, "before": b, "after": a,
                                             "baseline": _baseline(iso)})
        _dump(f"{iso}/alignment_audit.json", {
            "session": iso,
            "index_alignment": a.get("session_alignment") if a.get("outcome") == "OK" else a,
            "stock_alignment": a.get("stock_session_alignment"),
            "before_outcome": b.get("outcome"), "after_outcome": a.get("outcome")})
    return 0


def _baseline(iso):
    """What the approved POST renders were built from (21/24 Sep), for the unchanged check."""
    ref = {"2026-09-21": "output/reports/premarket_2026-09-23.json",
           "2026-09-24": "output/reports/premarket_2026-09-25.json"}.get(iso)
    if not ref or not os.path.exists(ref):
        return None
    with open(ref, encoding="utf-8") as fh:
        rep = json.load(fh)
    cov = (rep.get("metadata") or {}).get("movers_coverage") or {}
    return {"report": ref, "session_date": rep.get("session_date"),
            "nifty_pct": (rep.get("nifty") or {}).get("change_pct"),
            "movers_coverage": {k: cov.get(k) for k in ("universe_expected", "universe_observed",
                                                        "universe_validated", "coverage_pct")},
            "top_gainers": [(g.get("symbol"), g.get("change_pct")) for g in rep.get("gainers") or []],
            "top_losers": [(g.get("symbol"), g.get("change_pct")) for g in rep.get("losers") or []]}


# --------------------------------------------------------------------------- radar dry-run
def _sandbox() -> str:
    sb = os.path.join(VAL_DIR, "_sandbox")
    if not os.path.exists(os.path.join(sb, "data")):
        os.makedirs(os.path.join(sb, "data"), exist_ok=True)
        for name in os.listdir(os.path.join(OUT_DIR, "data")):
            if name.endswith(".db"):
                shutil.copy2(os.path.join(OUT_DIR, "data", name), os.path.join(sb, "data", name))
    return sb


def _radar(session: dt.date, before: bool) -> dict:
    from radar import daily_pipeline, session_alignment
    sb = _sandbox()
    saved_out, saved_spine = config.OUT_DIR, session_alignment.canonical_session_spine
    config.OUT_DIR = sb
    if before:
        session_alignment.canonical_session_spine = (
            lambda bench: frozenset(r["date"] for r in bench if r.get("date") is not None))
    try:
        res = daily_pipeline.run_daily_radar(session, out_dir=sb, dry_run=True)
        d = res.to_dict() if hasattr(res, "to_dict") else dict(res.__dict__)
    except Exception as exc:
        return {"pipeline_status": "EXCEPTION", "error": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        config.OUT_DIR, session_alignment.canonical_session_spine = saved_out, saved_spine
    stories = d.get("stories") or []
    return {"pipeline_status": d.get("pipeline_status"),
            "universe_requested": d.get("universe_requested"),
            "universe_usable": d.get("universe_usable"),
            "coverage_diagnostics": d.get("coverage_diagnostics"),
            "counts": {k: d.get(k) for k in ("volume_event_count", "technical_event_count",
                                             "relative_count", "composite_candidate_count",
                                             "editorial_selection_count")},
            "stories_in_selector_order": [
                {"instrument": st.get("instrument") or st.get("symbol"),
                 "price_change_pct": st.get("price_change_pct"),
                 "relative_volume": st.get("relative_volume")} for st in stories],
            "issues": [{"code": i.get("code"), "message": (i.get("message") or "")[:200]}
                       for i in d.get("issues") or []]}


def cmd_radar(sessions) -> int:
    for iso in sessions:
        d = dt.date.fromisoformat(iso)
        out = {"session": iso, "before": _radar(d, True), "after": _radar(d, False)}
        _dump(f"{iso}/radar_dry_run.json", out)
    return 0


# --------------------------------------------------------------------------- audit
def cmd_audit() -> int:
    from core.trading_calendar import NSE_TRADING_HOLIDAYS, SPECIAL_SESSIONS
    replay = _load("replay_before_after.json") or {}
    live = {s: _load(f"{s}/report_summary.json") for s in
            ("2026-09-15", "2026-09-21", "2026-09-23", "2026-09-24")}
    radar = {s: _load(f"{s}/radar_dry_run.json") for s in live}
    _dump("session_alignment_audit.json", {
        "canonical_source": "core.trading_calendar.SessionCalendar = NSE holiday list + "
                            "benchmark index bars (index-spine fallback for uncovered years)",
        "holiday_years_covered": sorted(NSE_TRADING_HOLIDAYS),
        "special_sessions": {k.isoformat(): v for k, v in SPECIAL_SESSIONS.items()},
        "known_rows": (_load("known_holiday_rows.json") or {}).get("rows"),
        "replay_summary": {k: v for k, v in (replay.get("summary") or {}).items() if k != "changes"},
        "live": {s: {"before": (v or {}).get("before", {}).get("outcome"),
                     "after": (v or {}).get("after", {}).get("outcome"),
                     "after_error": (v or {}).get("after", {}).get("error")} for s, v in live.items()},
        "radar": {s: {"before": ((v or {}).get("before") or {}).get("pipeline_status"),
                      "after": ((v or {}).get("after") or {}).get("pipeline_status")}
                  for s, v in radar.items()},
    })
    return 0


def main_() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["known", "replay", "live", "radar", "audit"])
    ap.add_argument("sessions", nargs="*")
    a = ap.parse_args()
    return {"known": cmd_known, "replay": cmd_replay, "audit": cmd_audit,
            "live": lambda: cmd_live(a.sessions), "radar": lambda: cmd_radar(a.sessions)}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main_())
