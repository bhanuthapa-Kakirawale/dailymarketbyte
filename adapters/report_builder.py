"""Assembles a validated MarketReport from the data the existing pipeline already collected.

This is the strangler seam. It makes no network calls of its own: everything here comes from
dicts main.py is already holding by the time the video is built. The renderer keeps consuming
those same dicts, so the report can be wrong, empty or absent without changing a single frame.
"""
from __future__ import annotations

import datetime as dt
import os

from core import (Fact, MarketReport, Metric, Observation, ReportType, validate_fact)
from core.validation import policy_for

from .market_adapter import MarketAdapter
from .news_adapter import NewsAdapter

REPORT_DIR = "reports"
BUILDER_VERSION = "phase1"


def _now_ist() -> dt.datetime:
    """Timestamps stay in IST like the rest of the pipeline, so an archived report reads in
    the same timezone as the market session it describes."""
    try:
        from config import now_ist
        return now_ist()
    except Exception:
        return dt.datetime.now(dt.timezone.utc)


def _dedupe(observations: list[Observation]) -> list[Observation]:
    """Collapse identical readings, keeping the first.

    Observation ids are deterministic (metric-instrument-source-date), so the same number
    reaching the builder by two routes - a Gemini fact that is also a display tile, say -
    collapses to one observation instead of looking like two agreeing sources.
    """
    seen, out = set(), []
    for obs in observations:
        if obs.observation_id in seen:
            continue
        seen.add(obs.observation_id)
        out.append(obs)
    return out


def facts_from(observations: list[Observation], now: dt.datetime | None = None) -> list[Fact]:
    """Group observations by (metric, instrument) and validate each resulting fact.

    The first observation of a group supplies the fact's published value. Collection order
    is arranged so that the source the video actually displayed comes first: the report
    documents what was published, and shows the alternatives beside it, rather than quietly
    substituting a number no viewer ever saw.
    """
    groups: dict[tuple, list[Observation]] = {}
    for obs in _dedupe(observations):
        groups.setdefault((obs.metric, obs.instrument), []).append(obs)

    facts = []
    for (metric, _instrument), group in groups.items():
        fact = Fact.from_observations(group)
        fact.metadata["published_value_source"] = group[0].source_name
        policy = policy_for(metric, expected_market_date=group[0].market_date)
        facts.append(validate_fact(fact, policy, now=now))
    return facts


def _ids(facts: list[Fact], metric: Metric, instrument: str) -> str | None:
    return next((f.fact_id for f in facts
                 if f.metric == metric and f.instrument == instrument), None)


def build_premarket_report(m: dict, tiles: list, fd: dict, sec: list, gainers: list,
                           losers: list, events: list, nifty_reason: str,
                           ai_facts: dict, nse_idx: dict, report_date: dt.date,
                           universe_label: str = "", demo: bool = False,
                           now: dt.datetime | None = None) -> MarketReport:
    """Build the PRE_MARKET report for `report_date` describing session `m["recap_date"]`."""
    now = now or _now_ist()
    session_date = m["recap_date"]
    ai_facts = ai_facts or {}

    market_ad = MarketAdapter(session_date, now, demo=demo, report_date=report_date)
    news_ad = NewsAdapter(session_date, report_date, now, demo=demo)

    # Order matters: the source the renderer displayed is observed first (see facts_from).
    observations: list[Observation] = []
    observations += market_ad.observe_nifty(m)
    observations += market_ad.observe_nse_indices(nse_idx)
    observations += market_ad.observe_technicals(m)
    observations += market_ad.observe_globals(tiles, ai_labels=_ai_tile_labels(ai_facts))
    observations += market_ad.observe_sectors(sec, nse_idx)
    observations += market_ad.observe_fii_dii(fd)
    observations += market_ad.observe_movers(gainers, "gainer")
    observations += market_ad.observe_movers(losers, "loser")
    observations += news_ad.observe_facts(ai_facts)

    facts = facts_from(observations, now=now)

    report = MarketReport(
        report_date=report_date,
        report_type=ReportType.PRE_MARKET,
        session_date=session_date,
        generated_at=now,
        facts=facts,
        nifty={
            "close": m.get("close"), "change_points": m.get("chg"), "change_pct": m.get("pct"),
            "open": m.get("open"), "high": m.get("high"), "low": m.get("low"),
            "previous_close": m.get("prev"), "bank_nifty_change_pct": m.get("bank_pct"),
            "india_vix": m.get("vix"), "move_summary": nifty_reason or "",
            "fact_ids": [i for i in (_ids(facts, Metric.INDEX_CLOSE, "NIFTY 50"),
                                     _ids(facts, Metric.INDEX_CHANGE_PCT, "NIFTY 50")) if i],
        },
        technicals={
            "ema20": m.get("ema20"), "ema50": m.get("ema50"), "rsi14": m.get("rsi"),
            "support": (m.get("levels") or {}).get("sup", []),
            "resistance": (m.get("levels") or {}).get("res", []),
            "trendline": m.get("trend"), "pivot": m.get("pivot"),
            "basis": "computed from Yahoo daily candles; no external source corroborates these",
        },
        global_cues=[{
            "label": t.get("label"), "value": t.get("value"), "change_pct": t.get("pct"),
            "fact_id": next((f.fact_id for f in facts if f.instrument == t.get("label")), None),
        } for t in tiles or []],
        institutional_flows=({
            "fii_net_cash_cr": fd.get("fii"), "dii_net_cash_cr": fd.get("dii"),
            "legacy_source_tag": fd.get("source"),
            "fact_ids": [i for i in (_ids(facts, Metric.FII_NET_CASH, "FII"),
                                     _ids(facts, Metric.DII_NET_CASH, "DII")) if i],
        } if fd else {}),
        sectors=[{
            "name": s.get("name"), "change_pct": s.get("pct"),
            "fact_id": _ids(facts, Metric.SECTOR_CHANGE_PCT, s.get("name")),
        } for s in sec or []],
        gainers=_movers_section(gainers, facts, news_ad),
        losers=_movers_section(losers, facts, news_ad),
        events=news_ad.describe_events(events),
        metadata={
            "builder_version": BUILDER_VERSION,
            "demo": bool(demo),
            "universe": universe_label,
            "session_date": session_date.isoformat(),
            "notes": ("Phase 1 strangler artifact: the renderer still consumes the legacy dicts. "
                      "Fact values record what was published, with alternative sources beside them."),
        },
    )
    return report


def _ai_tile_labels(ai_facts: dict) -> frozenset:
    """Tiles main.py inserted from the Gemini fact set, so they are attributed to AI."""
    labels = set()
    if (ai_facts or {}).get("gift"):
        labels.add("GIFT NIFTY")
    if (ai_facts or {}).get("brent"):
        labels.add("BRENT CRUDE")
    return frozenset(labels)


def _movers_section(rows: list, facts: list[Fact], news_ad: NewsAdapter) -> list:
    out = []
    for row in rows or []:
        symbol = row.get("symbol")
        out.append({
            "symbol": symbol, "name": row.get("name"), "close": row.get("close"),
            "change_pct": row.get("pct"), "relative_volume": row.get("volx"),
            "catalyst": news_ad.classify_catalyst(row),
            "fact_ids": [i for i in (_ids(facts, Metric.STOCK_CLOSE, symbol),
                                     _ids(facts, Metric.STOCK_CHANGE_PCT, symbol),
                                     _ids(facts, Metric.STOCK_RELATIVE_VOLUME, symbol)) if i],
        })
    return out


def save_report(report: MarketReport, out_dir: str, demo: bool = False) -> str:
    """Write the report JSON beside the video output. Returns the path written."""
    directory = os.path.join(out_dir, REPORT_DIR)
    os.makedirs(directory, exist_ok=True)
    suffix = "_DEMO" if demo else ""
    path = os.path.join(directory, f"premarket_{report.report_date:%Y-%m-%d}{suffix}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report.to_json())
    return path


def build_and_save_report(out_dir: str, **kwargs) -> str | None:
    """Build and persist the report, swallowing any failure.

    Phase 1 runs alongside a working video pipeline: the canonical layer is new code on a
    path that already produces the day's Short, so a defect here must never cost a
    publication. Failures are reported and the run continues.
    """
    demo = bool(kwargs.get("demo"))
    try:
        report = build_premarket_report(**kwargs)
        path = save_report(report, out_dir, demo=demo)
        summary = report.validation_summary
        print(f"      report: {os.path.basename(path)} | {summary.total_facts} facts "
              f"({summary.verified_count} verified, {summary.single_source_count} single-source, "
              f"{summary.provisional_count} provisional, {summary.conflict_count} conflict) "
              f"| publication_ready={summary.publication_ready}")
        for issue in summary.blocking_issues:
            print(f"      report issue: {issue}")
        return path
    except Exception as exc:                                  # never break video production
        print(f"[report] skipped: {type(exc).__name__}: {exc}")
        return None


__all__ = ["build_premarket_report", "build_and_save_report", "save_report", "facts_from"]
