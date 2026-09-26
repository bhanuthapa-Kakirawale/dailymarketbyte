"""POST multi-session validation (after Phase 3): exercise the POST editorial planner and the
full Short on more real sessions. Validation only - nothing here changes a rule.

    python validate_post_sessions.py radar  2026-09-24          # Radar DRY-RUN for a session
    python validate_post_sessions.py render <report.json> 2026-09-24 [--frames-only]
    python validate_post_sessions.py replay                     # decision replay, local OHLCV

radar   - runs the production Radar pipeline with dry_run=True (detectors and selector run for
          real, but NO candidate-history / editorial-selection rows are persisted, so validation
          never alters what the production selector remembers). Artifacts go to
          output/post_validation/radar/.
render  - renders the complete POST Short for a real validated report + that Radar output,
          into output/post_validation/<session>/.
replay  - replays the planner's Nifty-chart and Movers rules over every session in the local
          OHLCV store (real prices, but NOT validated MarketReports: no sectors, flows, global
          or Radar data exist there). It measures how often each rule fires - calibration
          evidence, not a publishable result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

import pandas as pd

from config import OUT_DIR

VAL_DIR = os.path.join(OUT_DIR, "post_validation")


def cmd_radar(session: str) -> int:
    from radar.daily_pipeline import run_daily_radar
    from radar.presentation_planner import build_radar_presentation, save_presentation
    day = dt.date.fromisoformat(session)
    result = run_daily_radar(session_date=day, dry_run=True)
    d = os.path.join(VAL_DIR, "radar")
    os.makedirs(d, exist_ok=True)
    data = result.to_dict()
    path = os.path.join(d, f"daily_radar_{session}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
    pres = build_radar_presentation(data)
    pres.source_radar_artifact = path
    ppath = save_presentation(pres, out_dir=VAL_DIR)
    print(json.dumps({"session": session, "status": data.get("pipeline_status"),
                      "stories": [s["instrument"] for s in data.get("stories") or []],
                      "artifact": path, "presentation": ppath}, ensure_ascii=False))
    return 0


def cmd_render(report: str, session: str, frames_only: bool) -> int:
    import render_daily_market_byte as r
    out = os.path.join(VAL_DIR, session)
    args = ["--report", report, "--radar-dir", os.path.join(VAL_DIR, "radar"), "--out-dir", out,
            "--out", os.path.join(out, f"full_post_{session}.mp4")]
    if frames_only:
        args.append("--frames-only")
    return r.main(args)


# --------------------------------------------------------------------------- replay
def _load(symbols=None):
    con = sqlite3.connect(os.path.join(OUT_DIR, "data", "market_ohlcv.db"))
    q = ("select symbol, session_date, open, high, low, close from daily_ohlcv "
         "where quality_status='OK' and source='yahoo'")
    df = pd.read_sql_query(q, con, parse_dates=["session_date"])
    con.close()
    return df


def cmd_replay() -> int:
    from presentation.post_plan import (MOVERS_MIN_PCT, MOVERS_MIN_SPREAD, direction_phrase,
                                        session_support, structural_event)
    df = _load()
    nifty = df[df.symbol == "^NSEI"].sort_values("session_date").set_index("session_date")
    nifty = nifty.rename(columns=str.capitalize)[["Open", "High", "Low", "Close"]]
    nifty["ema20"] = nifty.Close.ewm(span=20, adjust=False).mean()   # = market.analyze()
    stocks = df[df.symbol != "^NSEI"].pivot_table(index="session_date", columns="symbol",
                                                  values="close").sort_index()
    spine = list(nifty.index)
    rows = []
    for i in range(60, len(spine)):            # enough history for the 20-day window + EMA warm-up
        day, prev = spine[i], spine[i - 1]
        window = nifty.iloc[: i + 1]
        last = window.iloc[-1]
        pct = (last.Close / window.iloc[-2].Close - 1) * 100
        ev = structural_event(window)
        sup = session_support(last.Open, last.High, last.Low, last.Close)
        mv = None
        if day in stocks.index and prev in stocks.index:
            ch = ((stocks.loc[day] / stocks.loc[prev] - 1) * 100).dropna()
            if len(ch) >= 50:                     # a usable cross-section for this session
                g, l = ch.max(), ch.min()
                mv = {"gain": (ch.idxmax(), round(g, 2)), "fall": (ch.idxmin(), round(l, 2)),
                      "fires": bool(g >= MOVERS_MIN_PCT or abs(l) >= MOVERS_MIN_PCT
                                    or g - l >= MOVERS_MIN_SPREAD), "n": int(len(ch))}
        rows.append({"session": day.date().isoformat(), "nifty_pct": round(pct, 2),
                     "headline": direction_phrase(pct), "support": sup["text"],
                     "nifty_chart": ev["family"] if ev else None, "movers": mv})
    n = len(rows)
    with_mv = [r for r in rows if r["movers"]]
    chart = [r for r in rows if r["nifty_chart"]]
    mv_fire = [r for r in with_mv if r["movers"]["fires"]]
    by_head = pd.Series([r["headline"] for r in rows]).value_counts().to_dict()
    by_sup = pd.Series([r["support"] for r in rows]).value_counts().to_dict()
    by_chart = pd.Series([r["nifty_chart"] or "none" for r in rows]).value_counts().to_dict()
    summary = {
        "note": ("Decision replay on real local OHLCV prices, NOT validated MarketReports. "
                 "Sectors, flows, global cues and Radar are absent from the store, so only the "
                 "Nifty-chart rule, the pulse wording and the Movers magnitude rule are replayed. "
                 "Movers here ignore the Radar de-duplication (no Radar output per session) and "
                 "use the local ~200-stock universe, not the report's editorial top-5."),
        "sessions": n, "first": rows[0]["session"] if rows else None,
        "last": rows[-1]["session"] if rows else None,
        "nifty_chart_sessions": len(chart), "nifty_chart_share": round(len(chart) / n, 3) if n else None,
        "nifty_chart_by_event": by_chart,
        "movers_sessions_with_data": len(with_mv),
        "movers_fire": len(mv_fire),
        "movers_fire_share": round(len(mv_fire) / len(with_mv), 3) if with_mv else None,
        "pulse_headline": by_head, "pulse_support": by_sup,
    }
    os.makedirs(VAL_DIR, exist_ok=True)
    with open(os.path.join(VAL_DIR, "decision_replay.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "sessions": rows}, fh, indent=2, ensure_ascii=False,
                  default=str)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("radar")
    a.add_argument("session")
    b = sub.add_parser("render")
    b.add_argument("report")
    b.add_argument("session")
    b.add_argument("--frames-only", action="store_true")
    sub.add_parser("replay")
    args = ap.parse_args(argv)
    if args.cmd == "radar":
        return cmd_radar(args.session)
    if args.cmd == "render":
        return cmd_render(args.report, args.session, args.frames_only)
    return cmd_replay()


if __name__ == "__main__":
    sys.exit(main())
