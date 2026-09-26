"""Benchmark gap recovery + history repair (ops/validation script). Nothing here uploads.

    python recover_benchmark_gap.py benchmark               # 22 Sep fallback recovery audit
    python recover_benchmark_gap.py cache [--dry-run]       # repair cached non-final OHLCV bars
    python recover_benchmark_gap.py radar-history           # minimal Radar candidate-history rebuild
    python recover_benchmark_gap.py live 2026-09-15 ...     # time-travelled POST acquisition + report
    python recover_benchmark_gap.py render                  # corrected 24 Sep POST
    python recover_benchmark_gap.py summary                 # fold into the summary audit

Outputs: output/benchmark_gap_recovery/. `cache` and `radar-history` modify the REAL local
stores (market_ohlcv.db rows known to be non-final; radar_candidate_history.db for the affected
sessions only) - each first writes a full SQLite backup to output/benchmark_gap_recovery/backup/
and records every row it removes or replaces. Canonical history (market_history.db, report
JSON) is never touched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time

import config
from config import OUT_DIR

VAL_DIR = os.path.join(OUT_DIR, "benchmark_gap_recovery")
DATA_DIR = os.path.join(OUT_DIR, "data")
GAP = dt.date(2026, 9, 22)
TARGET = dt.date(2026, 9, 24)
REFERENCE_RADAR = os.path.join(OUT_DIR, "post_validation", "radar", "daily_radar_2026-09-24.json")
CANONICAL_24_REPORT = os.path.join(OUT_DIR, "reports", "premarket_2026-09-25.json")


def _dump(rel, obj):
    path = os.path.join(VAL_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    print(f"  wrote {path}")
    return path


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _backup(db_name: str) -> str:
    """Consistent copy (SQLite backup API - includes WAL content) before any modification."""
    os.makedirs(os.path.join(VAL_DIR, "backup"), exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    dst_path = os.path.join(VAL_DIR, "backup", f"{stamp}_{db_name}")
    src = sqlite3.connect(os.path.join(DATA_DIR, db_name))
    dst = sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    print(f"  backup {dst_path}")
    return dst_path


# --------------------------------------------------------------------------- benchmark
def cmd_benchmark() -> int:
    import market
    nifty = market.history("^NSEI", "1y")
    calendar = market.session_calendar([ts.date() for ts in nifty.index])
    nifty = market._drop_non_sessions(nifty, calendar)
    primary_has_gap = GAP in {ts.date() for ts in nifty.index}
    recovered, records = market.recover_index_gaps(nifty, "^NSEI", calendar)
    others = {}
    for ticker in ["^NSEBANK"] + [yt for _, _, yt in market.SECTORS]:
        try:
            d = market._drop_non_sessions(market.history(ticker, "1mo"), calendar)
            _, rec = market.recover_index_gaps(d, ticker, calendar)
            others[ticker] = rec
        except Exception as exc:
            others[ticker] = [{"error": f"{type(exc).__name__}: {exc}"}]

    # Independent forward check (audit only): NSE's own NEXT-session file states a points change
    # whose implied previous close must equal the recovered close.
    nxt = calendar.sessions_between(GAP + dt.timedelta(1), GAP + dt.timedelta(10))[0]
    nxt_file = market.nse_index_close_archive(nxt)
    fwd = None
    rec22 = next((r for r in records if r["session_date"] == GAP.isoformat()), None)
    if rec22 and "rows" in nxt_file:
        row = nxt_file["rows"].get("NIFTY 50")
        close_n, chg_n = (market._archive_float(row, "Closing Index Value"),
                          market._archive_float(row, "Points Change"))
        yahoo_n = next((float(v) for ts, v in nifty.Close.items() if ts.date() == nxt), None)
        fwd = {"next_session": nxt.isoformat(), "source_url": nxt_file["url"],
               "nse_next_close": close_n, "nse_next_points_change": chg_n,
               "implied_gap_close": round(close_n - chg_n, 4),
               "recovered_gap_close": (rec22.get("values") or {}).get("close"),
               "yahoo_next_close": yahoo_n,
               "matches": abs((close_n - chg_n) - ((rec22.get("values") or {}).get("close") or 0)) < 0.01}
    m23 = None
    if rec22 and rec22["validation_status"] == "VALIDATED":
        row23 = recovered[recovered.index.date == nxt].iloc[0]
        c22 = rec22["values"]["close"]
        m23 = {"session": nxt.isoformat(), "close": float(row23.Close), "previous_close": c22,
               "previous_close_source": rec22["source"],
               "pct": round((float(row23.Close) / c22 - 1) * 100, 4),
               "two_session_change_it_replaces_pct": round(
                   (float(row23.Close) / rec22["anchor_close"] - 1) * 100, 4)}
    _dump("benchmark_fallback_audit.json", {
        "primary": "Yahoo ^NSEI (market.history)", "fallback": market.INDEX_FALLBACK_SOURCE,
        "fallback_url_template": market.NSE_INDEX_ARCHIVE_URLS[0],
        "rules": {"used_only_when": ["canonical calendar (NSE holiday list) says the session "
                                     "existed", "the primary series has no bar for it",
                                     "the gap is INSIDE the primary series (never the recap "
                                     "session itself)", "the fallback row validates"],
                  "validation": ["source row dated the session", "OHLC internally consistent",
                                 "implied previous close (close - points change) within "
                                 f"{market.RECOVERY_ANCHOR_TOLERANCE:.2%} of the adjacent "
                                 "canonical close already held", "stated % change agrees",
                                 f"|change| < {market.RECOVERY_MAX_ABS_PCT}%"],
                  "never": ["interpolate", "infer from stocks", "use a two-session change as "
                            "a daily move", "write fallback rows into the Yahoo OHLCV cache"]},
        "gap_session": GAP.isoformat(),
        "canonical_status": calendar.status(GAP),
        "primary_has_bar": primary_has_gap,
        "primary_benchmark_gaps": [d.isoformat() for d in calendar.benchmark_gaps()],
        "nifty_records": records,
        "forward_continuity_check": fwd,
        "resulting_2026_09_23_nifty_move": m23,
        "other_indices": others})
    return 0


# --------------------------------------------------------------------------- cache
def cmd_cache(dry_run: bool) -> int:
    from core.trading_calendar import SessionCalendar
    from radar import cache_repair
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    import market
    backup = None if dry_run else _backup("market_ohlcv.db")
    store = OHLCVStore(default_db_path(OUT_DIR))
    try:
        idx = [r.session_date for r in store.get_range(["^NSEI"], dt.date(2000, 1, 1),
                                                       dt.date(2100, 1, 1), source="yahoo")]
        cal = SessionCalendar.from_index_dates(idx)
        total_before = store.count_rows()
        res = cache_repair.repair_non_final_rows(store, calendar=cal, dry_run=dry_run)
        total_after = store.count_rows()
    finally:
        store.close()
    d = res.to_dict()
    summary = {k: v for k, v in d.items() if k != "rows"}
    print(json.dumps(summary, indent=1, default=str))
    _dump("cache_repair_audit.json" if not dry_run else "cache_repair_dry_run.json", {
        "store": default_db_path(OUT_DIR), "backup_before_repair": backup, "dry_run": dry_run,
        "session_final_time_ist": market.SESSION_FINAL_TIME.isoformat(),
        "rule": "non-final = BACKFILL_PENDING, or OK but retrieved (IST) before the session's "
                "own SESSION_FINAL_TIME; everything else is final and untouched",
        "row_count_before": total_before, "row_count_after": total_after,
        "summary": summary, "rows": d["rows"]})
    return 0


# --------------------------------------------------------------------------- radar history
def _stored(rows):
    from storage.candidate_history_repository import CandidateHistoryStore
    return [CandidateHistoryStore._row(r) for r in rows]


def _novelty(prior, session, cands):
    from radar import daily_pipeline as dp
    from radar.novelty import classify_history
    sessions = [(d, [dp._reconstruct_candidate(s) for s in rows]) for d, rows in prior]
    sessions.append((session, [dp._reconstruct_candidate(s) for s in cands]))
    res = classify_history(sessions)[session]
    return {n.instrument: n.novelty_type.value for n in res}


def _count(types: dict) -> dict:
    out = {}
    for t in types.values():
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items()))


def _radar_summary(d):
    if not d:
        return None
    return {"pipeline_status": d.get("pipeline_status"), "universe_usable": d.get("universe_usable"),
            "counts": {k: d.get(k) for k in ("volume_event_count", "technical_event_count",
                                             "relative_count", "composite_candidate_count",
                                             "novel_candidate_count", "continuation_count",
                                             "editorial_selection_count")},
            "stories": [{"instrument": s["instrument"], "novelty_type": s["novelty_type"],
                         "price_change_pct": round(s["price_change_pct"], 3)
                         if s.get("price_change_pct") is not None else None,
                         "active_families": s.get("active_families"),
                         "selection_reason": s.get("editorial_selection_reason")}
                        for s in d.get("stories") or []],
            "issues": [i.get("code") for i in d.get("issues") or []]}


def cmd_radar_history() -> int:
    import market
    from radar import candidate_history_backfill as chb
    from radar import daily_pipeline as dp
    from radar import relative_acquisition, session_alignment
    from radar.presentation_planner import build_radar_presentation, save_presentation
    from radar.thresholds import DEFAULT_NOVELTY_THRESHOLDS
    from storage.candidate_history_repository import CandidateHistoryStore
    from storage.candidate_history_repository import default_db_path as ch_path
    from storage.editorial_repository import EditorialStore
    from storage.editorial_repository import default_db_path as ed_path

    lookback = DEFAULT_NOVELTY_THRESHOLDS.lookback_sessions
    bench = relative_acquisition.build_market_benchmark_series(period="1y")
    bench_recovery = relative_acquisition.last_benchmark_recovery()
    new_spine = session_alignment.canonical_session_list(bench)
    old_spine = sorted({r["date"] for r in bench if r.get("source") is None})   # pre-fix: Yahoo bars
    added = sorted(set(new_spine) - set(old_spine))
    removed = sorted(set(old_spine) - set(new_spine))
    first_diff = min(added + removed) if (added or removed) else None
    if first_diff is None:
        print("old and new spines agree - nothing to rebuild")
        return 0

    store = CandidateHistoryStore(ch_path(OUT_DIR))
    ed = EditorialStore(ed_path(OUT_DIR))
    try:
        runs = {r["session_date"]: dict(r) for r in store.conn.execute(
            "SELECT * FROM candidate_history_runs").fetchall()}
        stored_sessions = sorted({dt.date.fromisoformat(s) for s in runs})
        # Minimal range: from the first session where the spines differ through the target.
        # Everything before it saw an identical spine (and so identical windows); every stored
        # session from it on was computed against the old spine and is recomputed.
        rebuild = chb.spine_repair_range(old_spine, new_spine, TARGET)
        stale_after = [d for d in stored_sessions if d > TARGET]
        old = {}
        for d in rebuild:
            cands = store.get_session_candidates(d)
            old[d] = {"run": runs.get(d.isoformat()), "candidates": cands}
        old_selections = {d.isoformat(): [s.instrument for s in ed.get_session_selections(d)]
                          for d in rebuild}
        # Old novelty for each rebuilt session, as the OLD spine/history would have classified it.
        old_novelty = {}
        for d in rebuild:
            if d not in old_spine or not old[d]["candidates"]:
                continue
            prior = store.get_prior_candidates(d, old_spine, lookback)
            old_novelty[d.isoformat()] = _novelty(prior, d, old[d]["candidates"])
    finally:
        ed.close()

    backup = _backup("radar_candidate_history.db")
    removed_rows = store.invalidate_sessions(rebuild)
    store.close()

    universe = market.get_universe("NIFTY200")
    t0 = time.time()
    bf = chb.backfill_candidate_history(spine=new_spine, universe=universe, benchmark_series=bench,
                                        sessions=rebuild, out_dir=OUT_DIR)
    backfill_s = round(time.time() - t0, 1)

    store = CandidateHistoryStore(ch_path(OUT_DIR))
    try:
        new, new_novelty = {}, {}
        for d in rebuild:
            cands = store.get_session_candidates(d)
            marker = store.get_run_status(d, bf.calculation_version)
            new[d] = {"run": marker.__dict__ if marker else None, "candidates": cands}
            prior = store.get_prior_candidates(d, new_spine, lookback)
            new_novelty[d.isoformat()] = _novelty(prior, d, cands)
    finally:
        store.close()

    # Corrected 24 Sep Radar: production pipeline, DRY RUN (no editorial selection persisted -
    # same as the reference it replaces), reading the rebuilt candidate history.
    res = dp.run_daily_radar(TARGET, dry_run=True, out_dir=OUT_DIR)
    radar_dir = os.path.join(VAL_DIR, TARGET.isoformat(), "radar")
    os.makedirs(radar_dir, exist_ok=True)
    data = res.to_dict()
    rpath = os.path.join(radar_dir, f"daily_radar_{TARGET}.json")
    with open(rpath, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
    pres = build_radar_presentation(data)
    pres.source_radar_artifact = rpath
    save_presentation(pres, out_dir=os.path.join(VAL_DIR, TARGET.isoformat()))
    ref = _load(REFERENCE_RADAR)

    def cand_view(c):
        return {"instrument": c.instrument, "families": list(c.active_families),
                "signals": c.independent_signal_count, "attention": c.attention_level,
                "direction": c.direction_compatibility,
                "price_change_pct": None if c.price_change_pct is None else round(c.price_change_pct, 3)}

    sessions = []
    for d in rebuild:
        o, n = old[d], new[d]
        oset = {c.instrument: c for c in o["candidates"]}
        nset = {c.instrument: c for c in n["candidates"]}
        on, nn = old_novelty.get(d.isoformat(), {}), new_novelty.get(d.isoformat(), {})
        sessions.append({
            "session": d.isoformat(),
            "on_old_spine": d in old_spine,
            "old_run_marker": o["run"], "new_run_marker": n["run"],
            "old_candidate_count": len(o["candidates"]), "new_candidate_count": len(n["candidates"]),
            "candidates_added": sorted(set(nset) - set(oset)),
            "candidates_removed": sorted(set(oset) - set(nset)),
            "candidates_changed": sorted(s for s in set(oset) & set(nset)
                                         if cand_view(oset[s]) != cand_view(nset[s])),
            "price_change_examples": [
                {"instrument": s, "old_pct": cand_view(oset[s])["price_change_pct"],
                 "new_pct": cand_view(nset[s])["price_change_pct"]}
                for s in sorted(set(oset) & set(nset))[:8]],
            "old_selections": old_selections.get(d.isoformat()),
            "novelty_old": _count(on) if on else None, "novelty_new": _count(nn),
            "novelty_changed_symbols": sorted(s for s in set(on) & set(nn) if on[s] != nn[s]),
        })
    _dump("radar_history_rebuild_audit.json", {
        "backup_before_rebuild": backup,
        "spine": {"old_rule": "Yahoo ^NSEI bar dates", "new_rule": "core.trading_calendar "
                  "(NSE holiday list + benchmark bars)", "sessions_added": [d.isoformat() for d in added],
                  "sessions_removed": [d.isoformat() for d in removed],
                  "first_difference": first_diff.isoformat()},
        "benchmark_recovery": bench_recovery,
        "minimal_range_rule": "rebuild = canonical sessions from the first spine difference "
                              "through the target session; sessions before it saw an identical "
                              "spine, so their detector windows and novelty lookbacks are unchanged",
        "rebuilt_sessions": [d.isoformat() for d in rebuild],
        "untouched_stored_sessions_before": [d.isoformat() for d in stored_sessions if d < first_diff],
        "stored_sessions_after_target_not_rebuilt": [d.isoformat() for d in stale_after],
        "deleted": {"candidate_rows": len(removed_rows["candidates"]),
                    "run_markers": removed_rows["runs"],
                    "candidate_rows_by_session": _count({i: r["session_date"] for i, r in
                                                         enumerate(removed_rows["candidates"])}),
                    "rows": removed_rows["candidates"]},
        "editorial_selections_in_range": old_selections,
        "editorial_selections_note": "editorial_selections.db is publication history; nothing "
                                     "in the rebuilt range was ever selected/persisted, so it is "
                                     "untouched",
        "backfill": {**bf.to_dict(), "seconds": backfill_s},
        "sessions": sessions,
        "target_session_radar": {"reference_before": _radar_summary(ref),
                                 "corrected": _radar_summary(data),
                                 "artifact": rpath},
    })
    return 0


# --------------------------------------------------------------------------- live (time travel)
def _run_live(session: dt.date) -> dict:
    import main
    import market
    from validate_session_alignment import _summarize_report, _time_travel
    today = session + dt.timedelta(days=1)
    out = {}
    with _time_travel(session, before=False):
        # Production (upload) decision first: holiday / missing-session skip rules.
        saved = main.OUT_DIR
        main.OUT_DIR = os.path.join(VAL_DIR, "_state")
        os.makedirs(main.OUT_DIR, exist_ok=True)
        try:
            raw_up = main.collect(argparse.Namespace(demo=False, upload=True, force=False), today)
            out["upload_mode_decision"] = ("PROCEEDS" if raw_up else "SKIPPED")
        except Exception as exc:
            raw_up = None
            out["upload_mode_decision"] = f"FAILED: {type(exc).__name__}: {str(exc)[:400]}"
        finally:
            main.OUT_DIR = saved
        try:
            raw = raw_up or main.collect(argparse.Namespace(demo=False, upload=False, force=True), today)
        except Exception as exc:
            out.update({"outcome": "FAILED", "stage": "acquisition",
                        "error_type": type(exc).__name__, "error": str(exc)[:800]})
            return out
        if raw["m"]["recap_date"] != session:
            cal = market.session_calendar(raw["m"].get("session_dates"))
            out.update({"outcome": "RECAP_IS_EARLIER_SESSION",
                        "recap_date": str(raw["m"]["recap_date"]),
                        "canonical_status": cal.status(session),
                        "reason": "the primary benchmark has no bar for this session at all; gap "
                                  "recovery fills sessions INSIDE the benchmark series, never "
                                  "the recap session itself"})
            return out
        report = main.build_report(raw, today, demo=False)
        ok = main.check_publication(report)
        s = _summarize_report(report, ok, raw)
        s["nifty"]["previous_close_source"] = raw["m"].get("prev_source")
        s["nifty"]["prev_date"] = str(raw["m"]["prev_date"])
        s["sectors_detail"] = raw["sec"]
        from core import Metric
        s["nifty_change_observations"] = [
            {"source": o.source_name, "value": o.value, "metadata": dict(o.metadata)}
            for f in report.facts_for(Metric.INDEX_CHANGE_PCT) if f.instrument == "NIFTY 50"
            for o in f.observations]
        s["nifty_change_fact_status"] = [f.validation_status.value
                                         for f in report.facts_for(Metric.INDEX_CHANGE_PCT)
                                         if f.instrument == "NIFTY 50"]
        out.update({"outcome": "OK", **s})
    return out


def cmd_live(sessions) -> int:
    prior_dir = os.path.join(OUT_DIR, "session_alignment_validation")
    for iso in sessions:
        d = dt.date.fromisoformat(iso)
        print(f"== {iso}")
        after = _run_live(d)
        prior = (_load(os.path.join(prior_dir, iso, "report_summary.json")) or {}).get("after")
        _dump(f"{iso}/report_summary.json", {"session": iso, "result": after,
                                             "before_this_patch": prior})
    return 0


# --------------------------------------------------------------------------- render
def cmd_render() -> int:
    import render_daily_market_byte as r
    out = os.path.join(VAL_DIR, TARGET.isoformat())
    t0 = time.time()
    rc = r.main(["--report", CANONICAL_24_REPORT, "--radar-dir", os.path.join(out, "radar"),
                 "--out-dir", out, "--out", os.path.join(out, "full_post_corrected.mp4"),
                 "--no-hook-ai"])
    with open(os.path.join(out, "render_seconds.txt"), "w") as fh:
        fh.write(f"{time.time() - t0:.1f}\n")
    return rc


# --------------------------------------------------------------------------- summary
def cmd_summary() -> int:
    live = {s: _load(os.path.join(VAL_DIR, s, "report_summary.json"))
            for s in ("2026-09-15", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24")}
    _dump("summary.json", {
        "benchmark": {k: v for k, v in (_load(os.path.join(VAL_DIR, "benchmark_fallback_audit.json"))
                                        or {}).items() if k in ("gap_session", "canonical_status",
                                                                "forward_continuity_check",
                                                                "resulting_2026_09_23_nifty_move")},
        "cache": (_load(os.path.join(VAL_DIR, "cache_repair_audit.json")) or {}).get("summary"),
        "radar_history": {k: v for k, v in (_load(os.path.join(VAL_DIR, "radar_history_rebuild_audit.json"))
                                            or {}).items() if k in ("rebuilt_sessions", "spine")},
        "live": {s: {"outcome": (v or {}).get("result", {}).get("outcome"),
                     "upload_mode_decision": (v or {}).get("result", {}).get("upload_mode_decision"),
                     "publication_ready": (v or {}).get("result", {}).get("publication_ready")}
                 for s, v in live.items()},
    })
    return 0


def main_() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["benchmark", "cache", "radar-history", "live", "render", "summary"])
    ap.add_argument("sessions", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    return {"benchmark": cmd_benchmark, "cache": lambda: cmd_cache(a.dry_run),
            "radar-history": cmd_radar_history, "live": lambda: cmd_live(a.sessions),
            "render": cmd_render, "summary": cmd_summary}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main_())
