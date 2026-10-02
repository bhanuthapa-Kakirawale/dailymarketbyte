"""`python -m private_desk.regime.research` - distributions, threshold review and historical
validation of the regime classifier over the stored history. Writes ONLY under
`<OUT_DIR>/private_desk/regime_research/` (regeneratable; reads are read-only).

    distributions.json            per-metric percentiles over the sample
    regime_threshold_review.md    every V1 threshold, where it sits in the sample, why
    regime_history.csv            one row per session: regime, candidate, dimension states, numbers
    validation_summary.json       counts, run lengths, switches, availability, reconciliation,
                                  no-lookahead proof
    validation_report.md          the same, readable

No forward return is computed anywhere in this module: V1 validation is about stability,
coverage and auditability, not profitability (docs/PRIVATE_MARKET_REGIME.md).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

from ..cache import CacheWriteRefused, _inside
from ..repository import DeskRepository
from ..settings import DESK_DIRNAME
from . import metrics as mt
from . import rules as rr
from .data import load_regime_data
from .engine import classify_history, classify_session
from . import model
from .model import INSUFFICIENT_DATA, REGIMES

RESEARCH_DIRNAME = "regime_research"
PERCENTILES = (5, 10, 25, 50, 75, 90, 95)
METRICS = {
    "trend.dist_sma20_pct": "NIFTY 50 close vs SMA20 (%)",
    "trend.dist_sma50_pct": "NIFTY 50 close vs SMA50 (%)",
    "trend.sma20_vs_sma50_pct": "NIFTY 50 SMA20 vs SMA50 (%)",
    "trend.return_20d_pct": "NIFTY 50 20-session return (%)",
    "breadth.advance_share_pct": "NIFTY 200 advance share, one session (%)",
    "breadth.advance_share_10d_pct": "NIFTY 200 advance share, 10-session average (%)",
    "breadth.pct_above_sma50": "NIFTY 200 share above own SMA50 (%)",
    "breadth.pct_above_sma20": "NIFTY 200 share above own SMA20 (%)",
    "sectors.positive_share_pct": "Sectors with positive 20-session median return (%)",
    "volume.events": "Unusual-volume events, 5 sessions",
    "volume.up_share_pct": "Unusual-volume events on advancing stocks (%)",
    "volatility.realised_20d_pct": "NIFTY 50 realised volatility, 20 sessions, annualised (%)",
    "volatility.realised_change_pct": "Realised volatility change vs 5 sessions earlier (%)",
    "volatility.india_vix": "India VIX (canonical report)",
    "relative.outperform_share_pct": "NIFTY 200 share beating NIFTY 50 over 20 sessions (%)",
}
THRESHOLDS = [
    ("TREND_FLAT_SMA50_PCT", rr.TREND_FLAT_SMA50_PCT, "trend.dist_sma50_pct", True,
     "Range-like band for |close vs SMA50|; a 1% band is a conventional 'at the average' zone."),
    ("TREND_FLAT_SPREAD_PCT", rr.TREND_FLAT_SPREAD_PCT, "trend.sma20_vs_sma50_pct", True,
     "Averages within 0.5% of each other = no established slope."),
    ("BREADTH_HIGH_PCT", rr.BREADTH_HIGH_PCT, "breadth.pct_above_sma50", False,
     "60/40 is the conventional participation band for '% above 50-day average'."),
    ("BREADTH_LOW_PCT", rr.BREADTH_LOW_PCT, "breadth.pct_above_sma50", False, "See above."),
    ("PERSIST_MID_PCT", rr.PERSIST_MID_PCT, "breadth.advance_share_10d_pct", False,
     "Direction split of the 10-session advance share; +/-5 pts = balanced."),
    ("WASHOUT_PCT", rr.WASHOUT_PCT, "breadth.advance_share_pct", False,
     "One-session breadth >= 80% on one side (~top/bottom decile here) can only downgrade."),
    ("SECTOR_BROAD_SHARE", round(rr.SECTOR_BROAD_SHARE, 2), "sectors.positive_share_pct", False,
     "Two-thirds of sectors = broad; one-third = narrow. Symmetric, not fitted."),
    ("SECTOR_NARROW_SHARE", round(rr.SECTOR_NARROW_SHARE, 2), "sectors.positive_share_pct", False,
     "See above."),
    ("VOLUME_MIN_EVENTS", rr.VOLUME_MIN_EVENTS, "volume.events", False,
     "Below ~5th percentile of the sample = too quiet to lean."),
    ("VOLUME_UP_SHARE", rr.VOLUME_UP_SHARE, "volume.up_share_pct", False,
     "ASYMMETRIC on purpose: the sample's median up-share is ~60%, so 50% is not neutral; "
     "65 / 45 sit roughly +/-5-15 pts around that median. Most sample-dependent threshold."),
    ("VOLUME_DOWN_SHARE", rr.VOLUME_DOWN_SHARE, "volume.up_share_pct", False, "See above."),
    ("VOL_ELEVATED_PCT", rr.VOL_ELEVATED_PCT, "volatility.realised_20d_pct", False,
     "20% annualised is a conventional stress level for NIFTY 50; the sample's stress episode "
     "(March-April) sits above it, the calm months well below."),
    ("VOL_CHANGE_PCT", rr.VOL_CHANGE_PCT, "volatility.realised_change_pct", True,
     "+/-25% in 5 sessions ~ outer decile of the sample's changes."),
    ("RELATIVE_BROAD_PCT", rr.RELATIVE_BROAD_PCT, "relative.outperform_share_pct", False,
     "Context only."),
    ("RELATIVE_NARROW_PCT", rr.RELATIVE_NARROW_PCT, "relative.outperform_share_pct", False,
     "Context only."),
]


def research_dir(out_dir: str) -> str:
    return os.path.join(out_dir, DESK_DIRNAME, RESEARCH_DIRNAME)


def _write(root: str, out_dir: str, name: str, text: str) -> str:
    if not _inside(root, os.path.join(out_dir, DESK_DIRNAME)) and _inside(root, out_dir):
        raise CacheWriteRefused(f"research output must live under {DESK_DIRNAME}/: {root}")
    path = os.path.join(root, name)
    if not _inside(path, root):
        raise CacheWriteRefused(f"path escapes the research directory: {name}")
    os.makedirs(root, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _get(m: dict, dotted: str):
    a, b = dotted.split(".")
    return (m.get(a) or {}).get(b)


def _percentile(sorted_vals: list, p: float) -> float:
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def distributions(metric_rows: list) -> dict:
    out = {}
    for key, label in METRICS.items():
        vals = sorted(float(v) for v in (_get(m, key) for m in metric_rows) if v is not None)
        out[key] = {"label": label, "n": len(vals),
                    "min": round(vals[0], 4) if vals else None,
                    "max": round(vals[-1], 4) if vals else None,
                    "percentiles": {str(p): round(_percentile(vals, p), 4) for p in PERCENTILES}
                    if vals else {}}
    return out


def _runs(seq: list) -> list:
    runs = []
    for x in seq:
        if runs and runs[-1][0] == x:
            runs[-1][1] += 1
        else:
            runs.append([x, 1])
    return runs


def _run_stats(seq: list) -> dict:
    runs = _runs(seq)
    by = defaultdict(list)
    for x, n in runs:
        by[x].append(n)
    return {"switches": max(0, len(runs) - 1),
            "one_session_reversals": sum(1 for i in range(len(runs) - 2)
                                         if runs[i][0] == runs[i + 2][0] and runs[i + 1][1] == 1),
            "runs_by_regime": {k: {"runs": len(v), "average_length": round(sum(v) / len(v), 2),
                                   "longest": max(v)} for k, v in sorted(by.items())}}


def _reconcile_market_structure(repo: DeskRepository, metric_rows: dict) -> list:
    """Classifier breadth / unusual volume vs the stored Market Structure artifact counts."""
    out = []
    for s in repo.market_structure_sessions():
        data, _ = repo.market_structure(s)
        m = metric_rows.get(s.isoformat())
        if not data or not m:
            continue
        metrics = (data.get("snapshot") or {}).get("metrics") or {}
        day = next((d for d in m["volume"]["days"] if d["session"] == s.isoformat()), {})
        pairs = {"ADVANCES": (m["breadth"]["advances"], (metrics.get("ADVANCES") or {}).get("numerator")),
                 "DECLINES": (m["breadth"]["declines"], (metrics.get("DECLINES") or {}).get("numerator")),
                 "UNUSUAL_VOLUME": (day.get("unusual"),
                                    (metrics.get("UNUSUAL_VOLUME") or {}).get("numerator"))}
        out.append({"session": s.isoformat(),
                    **{k: {"classifier": a, "market_structure": b, "match": a == b}
                       for k, (a, b) in pairs.items()}})
    return out


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "generated_at"}


def build(out_dir: str, end: dt.date | None = None, *, lookahead_check: bool = True,
          now: dt.datetime | None = None) -> dict:
    from config import now_ist
    from operations.sessions import latest_final_session
    now = now or now_ist()
    repo = DeskRepository(out_dir)
    end = end or latest_final_session(now)
    data = load_regime_data(repo, end)
    end = data.last
    stock_start = next((d for i, d in enumerate(data.sessions)        # first constituent bar
                        if (~np.isnan(data.close[i])).any()), data.sessions[0])
    sessions = [d for d in data.sessions if d >= stock_start]
    snaps = classify_history(data, sessions, generated_at=now.isoformat())
    metric_rows = {d.isoformat(): mt.session_metrics(data.until(d)) for d in sessions}

    # no-lookahead proof: reload the inputs CUT at each session and reclassify
    proof = {"checked": 0, "identical": 0, "differences": []}
    if lookahead_check:
        for s in snaps:
            d = dt.date.fromisoformat(s.session_date)
            alone = classify_session(load_regime_data(repo, d), d, generated_at=now.isoformat())
            proof["checked"] += 1
            if _strip(alone.to_dict()) == _strip(s.to_dict()):
                proof["identical"] += 1
            else:
                proof["differences"].append(s.session_date)

    eligible = [s for s in snaps if s.candidate_regime != INSUFFICIENT_DATA]
    avail = {k: Counter() for k in rr.DIMENSION_ORDER}
    for s in snaps:
        for x in s.dimensions:
            avail[x.key][x.state] += 1
    summary = {
        "calculation_version": model.CALCULATION_VERSION, "generated_at": now.isoformat(),
        "end_session": end.isoformat(), "first_session": sessions[0].isoformat(),
        "benchmark_sessions_loaded": len(data.sessions),
        "benchmark_first_session": data.sessions[0].isoformat(),
        "sessions_total": len(snaps), "eligible_sessions": len(eligible),
        "insufficient_sessions": len(snaps) - len(eligible),
        "first_eligible_session": eligible[0].session_date if eligible else None,
        "counts_by_regime": {k: sum(1 for s in snaps if s.regime == k) for k in REGIMES},
        "candidate_counts": {k: sum(1 for s in snaps if s.candidate_regime == k) for k in REGIMES},
        "final": _run_stats([s.regime for s in eligible]),
        "without_confirmation": _run_stats([s.candidate_regime for s in eligible]),
        "regime_switches": _run_stats([s.regime for s in eligible])["switches"],
        "reason_codes": dict(Counter(s.reason_code for s in snaps)),
        "dimension_states": {k: dict(v) for k, v in avail.items()},
        "membership_backdated_sessions": sum(1 for s in snaps if s.universe.get("membership_backdated")),
        "market_structure_reconciliation": _reconcile_market_structure(repo, metric_rows),
        "no_lookahead_proof": proof,
        "vix_sessions": sum(1 for m in metric_rows.values() if m["volatility"]["india_vix"] is not None),
        "flow_sessions": sum(1 for m in metric_rows.values() if m["flows"]["fii_net_cr"] is not None),
        "forward_returns_used": False,
    }
    dist = distributions([metric_rows[s.session_date] for s in snaps])
    root = research_dir(out_dir)
    paths = [
        _write(root, out_dir, "distributions.json", json.dumps(
            {"calculation_version": model.CALCULATION_VERSION, "sample": {
                "first_session": sessions[0].isoformat(), "end_session": end.isoformat(),
                "sessions": len(sessions)}, "metrics": dist}, indent=2)),
        _write(root, out_dir, "regime_threshold_review.md", _threshold_md(dist, summary)),
        _write(root, out_dir, "regime_history.csv", _history_csv(snaps, metric_rows)),
        _write(root, out_dir, "validation_summary.json", json.dumps(summary, indent=2, default=str)),
        _write(root, out_dir, "validation_report.md", _validation_md(summary, snaps)),
    ]
    return {"summary": summary, "paths": paths}


# ------------------------------------------------------------------ renderers
def _threshold_md(dist: dict, summary: dict) -> str:
    L = ["# Regime threshold review (V1, provisional)", "",
         f"Calculation version `{model.CALCULATION_VERSION}`. Sample: {summary['first_session']} .. "
         f"{summary['end_session']} ({summary['sessions_total']} canonical sessions with "
         f"constituent data; {summary['eligible_sessions']} classifiable). Generated "
         f"{summary['generated_at']}.", "",
         "Thresholds are conventional, round and mostly symmetric. They were CHECKED against the "
         "distributions below, not fitted to them: one ~6-month sample (a stress episode in "
         "March-April, a June-August range, a September decline) is far too short to optimise "
         "anything. Sample position = the first listed percentile at or above the threshold.", "",
         "## Distributions", "",
         "| Metric | n | p5 | p10 | p25 | p50 | p75 | p90 | p95 |", "|---|---|---|---|---|---|---|---|---|"]
    for key, d in dist.items():
        p = d["percentiles"]
        cells = [f"{p[str(q)]:.2f}" if p else "-" for q in PERCENTILES]
        L.append(f"| {d['label']} | {d['n']} | " + " | ".join(cells) + " |")
    L += ["", "## Thresholds", "", "| Threshold | Value | Metric | Sample position | Rationale |",
          "|---|---|---|---|---|"]
    for name, value, key, absolute, why in THRESHOLDS:
        rank = "-"
        if dist[key]["n"]:
            pts = dist[key]["percentiles"]
            lo = next((q for q in PERCENTILES if pts[str(q)] >= value), None)
            rank = (f"band +/-{value} (see p25..p75)" if absolute else
                    f"at or below p{lo}" if lo is not None else "above p95")
        L.append(f"| `{name}` | {value} | {dist[key]['label']} | {rank} | {why} |")
    L += ["", "## Not used as rule inputs in V1", "",
          f"- India VIX: {summary['vix_sessions']} stored readings (canonical reports) - shown beside "
          "realised volatility, no threshold set.",
          f"- FII/DII cash flow: {summary['flow_sessions']} sessions - CONTEXT dimension only.",
          "- Relative strength spread: CONTEXT only (overlaps breadth/sectors).", ""]
    return "\n".join(L)


def _history_csv(snaps: list, metric_rows: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session", "regime", "candidate", "previous_candidate", "reason_code",
                *rr.DIMENSION_ORDER, "nifty_close", "dist_sma20_pct", "dist_sma50_pct",
                "sma20_vs_sma50_pct", "advances", "declines", "pct_above_sma50",
                "advance_share_10d_pct", "sectors_positive", "sectors_counted",
                "uv_up_events", "uv_down_events", "realised_vol_20d_pct", "india_vix",
                "outperform_share_pct", "fii_net_cr", "membership_from"])
    for s in snaps:
        m = metric_rows[s.session_date]
        st = {x.key: x.state for x in s.dimensions}
        w.writerow([s.session_date, s.regime, s.candidate_regime, s.previous_candidate,
                    s.reason_code, *[st.get(k) for k in rr.DIMENSION_ORDER],
                    m["trend"]["close"], m["trend"]["dist_sma20_pct"], m["trend"]["dist_sma50_pct"],
                    m["trend"]["sma20_vs_sma50_pct"], m["breadth"]["advances"],
                    m["breadth"]["declines"], m["breadth"]["pct_above_sma50"],
                    m["breadth"]["advance_share_10d_pct"], m["sectors"]["sectors_positive"],
                    m["sectors"]["sectors_counted"], m["volume"]["up_events"],
                    m["volume"]["down_events"], m["volatility"]["realised_20d_pct"],
                    m["volatility"]["india_vix"], m["relative"]["outperform_share_pct"],
                    m["flows"]["fii_net_cr"], s.universe.get("membership_from")])
    return buf.getvalue()


def _validation_md(sm: dict, snaps: list) -> str:
    ab = {"POSITIVE": "POS", "NEGATIVE": "NEG", "MIXED": "MIX", "NEUTRAL": "NEU",
          "UNAVAILABLE": "-", "ELEVATED": "ELEV", "RISING": "RISE", "FALLING": "FALL",
          "STABLE": "STAB"}
    p = sm["no_lookahead_proof"]
    L = ["# Regime historical validation (V1, provisional)", "",
         f"Calculation version `{sm['calculation_version']}` · generated {sm['generated_at']}", "",
         "## Sample", "",
         f"- Constituent OHLCV sessions: {sm['sessions_total']} ({sm['first_session']} .. "
         f"{sm['end_session']}); benchmark sessions loaded: {sm['benchmark_sessions_loaded']} "
         f"(from {sm['benchmark_first_session']}).",
         f"- Classifiable (minimum data met): {sm['eligible_sessions']}; INSUFFICIENT_DATA: "
         f"{sm['insufficient_sessions']} (first classifiable {sm['first_eligible_session']} - "
         "BREADTH needs 50 sessions of constituent history for the SMA50 share).",
         f"- NIFTY 200 membership back-dated (earliest Market Structure list used for earlier "
         f"sessions): {sm['membership_backdated_sessions']} sessions.",
         f"- India VIX readings: {sm['vix_sessions']}; FII/DII readings: {sm['flow_sessions']}.",
         "- This is ~6 months of one market. It is NOT enough for long-cycle validation; every "
         "threshold is provisional.", "",
         "## Regime counts", "", "| Regime | Final | Candidate (no confirmation) |", "|---|---|---|"]
    for k in REGIMES:
        L.append(f"| {k} | {sm['counts_by_regime'][k]} | {sm['candidate_counts'][k]} |")
    L += ["", "## Persistence / switching (classifiable sessions)", "",
          "| | Switches | One-session reversals (A-B-A) |", "|---|---|---|",
          f"| Final (with confirmation rule) | {sm['final']['switches']} | "
          f"{sm['final']['one_session_reversals']} |",
          f"| Candidate only | {sm['without_confirmation']['switches']} | "
          f"{sm['without_confirmation']['one_session_reversals']} |", "",
          "| Regime | Runs | Average length (sessions) | Longest |", "|---|---|---|---|"]
    for k, v in sm["final"]["runs_by_regime"].items():
        L.append(f"| {k} | {v['runs']} | {v['average_length']} | {v['longest']} |")
    L += ["", "## Dimension availability (all sessions)", "",
          "| Dimension | States |", "|---|---|"]
    for k, v in sm["dimension_states"].items():
        L.append(f"| {k} | " + ", ".join(f"{s} {n}" for s, n in sorted(v.items())) + " |")
    L += ["", "## Reconciliation with stored Market Structure artifacts", ""]
    for r in sm["market_structure_reconciliation"]:
        L.append(f"- {r['session']}: " + "; ".join(
            f"{k} {v['classifier']} vs {v['market_structure']} {'OK' if v['match'] else 'DIFF'}"
            for k, v in r.items() if k != "session"))
    L += ["", "## No-lookahead proof", "",
          f"Every session was reclassified from inputs reloaded with the store CUT at that "
          f"session: {p['identical']} / {p['checked']} identical"
          + (f"; differences: {', '.join(p['differences'])}" if p["differences"] else "") + ".",
          "Forward returns are not computed or used anywhere in V1.", "",
          "## Regime history", "",
          "| Session | Regime | Candidate | Reason | Trend | Breadth | Sectors | Volume | Vol |",
          "|---|---|---|---|---|---|---|---|---|"]
    for s in snaps:
        st = {x.key: ab.get(x.state, x.state) for x in s.dimensions}
        L.append(f"| {s.session_date} | {s.regime} | {s.candidate_regime} | {s.reason_code} | "
                 + " | ".join(st.get(k, "-") for k in ("TREND", "BREADTH", "SECTORS", "VOLUME",
                                                        "VOLATILITY")) + " |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m private_desk.regime.research")
    ap.add_argument("--out-dir", default=None, help="DMB output root (default: config.OUT_DIR)")
    ap.add_argument("--end", default=None, help="last session (default: latest completed)")
    ap.add_argument("--skip-lookahead-check", action="store_true")
    args = ap.parse_args(argv)
    import config
    out_dir = os.path.abspath(args.out_dir or config.OUT_DIR)
    res = build(out_dir, dt.date.fromisoformat(args.end) if args.end else None,
                lookahead_check=not args.skip_lookahead_check)
    sm = res["summary"]
    print(f"regime research {sm['calculation_version']}: {sm['sessions_total']} sessions, "
          f"{sm['eligible_sessions']} classifiable, counts {sm['counts_by_regime']}")
    print(f"no-lookahead: {sm['no_lookahead_proof']['identical']}/"
          f"{sm['no_lookahead_proof']['checked']} identical")
    for p in res["paths"]:
        print("  wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
