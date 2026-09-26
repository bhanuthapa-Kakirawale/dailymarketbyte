"""Assembles the validated MarketReport from provider output.

Phase 2 inverts the old dependency. The report is no longer a record written after the video
from whatever the pipeline happened to be holding; it is built first, from Observations the
providers produced, and the renderer is fed from it. Nothing here fetches anything - it
takes provider results, groups them into Facts, validates, and assembles.
"""
from __future__ import annotations

import datetime as dt
import os

from core import Fact, MarketReport, Metric, Observation, ReportType, validate_fact
from core.sources import registry_snapshot
from core.validation import policy_for

REPORT_DIR = "reports"
BUILDER_VERSION = "phase2"


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
    is arranged so that the source the video displays comes first: the report documents what
    is published, and shows the alternatives beside it, rather than quietly substituting a
    number no viewer ever saw.
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


def build_report(session, narrative, observations: list[Observation], tiles: list,
                 flows: dict, sectors: list, gainers: list, losers: list,
                 report_date: dt.date, universe_label: str = "", demo: bool = False,
                 now: dt.datetime | None = None,
                 content_safety: dict | None = None,
                 movers_coverage: dict | None = None,
                 session_alignment: dict | None = None) -> MarketReport:
    """Build the PRE_MARKET report for `report_date` describing `session.session_date`.

    `session` is a providers.IndexSession, `narrative` a providers.NarrativeBundle - typed
    provider output, not raw dictionaries. Everything displayed downstream is derived from
    what this function puts into the report.

    `movers_coverage` is the acquisition audit from `market.get_movers_audited` (universe
    expected / observed / validated, coverage_pct, guard-held rows). It is recorded as-is in
    `metadata["movers_coverage"]`; absent, the report simply carries no coverage record and the
    editorial coverage gate treats its ranking as unproven.
    """
    now = now or _now_ist()
    facts = facts_from(observations, now=now)

    nifty = session.nifty_section()
    nifty["move_summary"] = narrative.nifty_reason
    nifty["move_summary_provenance"] = narrative.nifty_reason_dict()
    nifty["fact_ids"] = [i for i in (_ids(facts, Metric.INDEX_CLOSE, "NIFTY 50"),
                                     _ids(facts, Metric.INDEX_CHANGE_PCT, "NIFTY 50")) if i]

    report = MarketReport(
        report_date=report_date,
        report_type=ReportType.PRE_MARKET,
        session_date=session.session_date,
        generated_at=now,
        facts=facts,
        nifty=nifty,
        technicals=session.technicals_section(),
        global_cues=[{
            "label": t.get("label"), "value": t.get("value"), "change_pct": t.get("pct"),
            "decimals": t.get("dec", 0), "prefix": t.get("prefix", ""),
            "fact_id": next((f.fact_id for f in facts if f.instrument == t.get("label")), None),
        } for t in tiles or []],
        institutional_flows=({
            "fii_net_cash_cr": flows.get("fii"), "dii_net_cash_cr": flows.get("dii"),
            "legacy_source_tag": flows.get("source"),
            "fact_ids": [i for i in (_ids(facts, Metric.FII_NET_CASH, "FII"),
                                     _ids(facts, Metric.DII_NET_CASH, "DII")) if i],
        } if flows else {}),
        sectors=[{
            "name": s.get("name"), "change_pct": s.get("pct"),
            "fact_id": _ids(facts, Metric.SECTOR_CHANGE_PCT, s.get("name")),
        } for s in sectors or []],
        gainers=_movers_section(gainers, facts, narrative),
        losers=_movers_section(losers, facts, narrative),
        events=[e.to_dict() for e in narrative.events],
        content_safety=content_safety or {},
        sources=registry_snapshot(o.source_name for o in observations),
        metadata={
            "builder_version": BUILDER_VERSION,
            "demo": bool(demo),
            "universe": universe_label,
            "session_date": session.session_date.isoformat(),
            "previous_session_date": (session.prev_date.isoformat() if session.prev_date else None),
            **({"movers_coverage": dict(movers_coverage)} if movers_coverage is not None else {}),
            # Index-level session check (`market.check_index_session_alignment`): canonical
            # previous session, benchmark gaps. Diagnostics only - never read by presentation.
            **({"session_alignment": dict(session_alignment)} if session_alignment else {}),
            "notes": ("Phase 2: this report is the authoritative input to presentation. Every "
                      "number rendered in the video is derived from these facts and sections."),
        },
    )
    if demo:
        # A demo and a production run on the same date share `report_date` and `report_type`,
        # so MarketReport's default report_id (unchanged, untouched here) would collide - the
        # demo run could then silently adopt, or be silently blocked from persisting beside,
        # the SAME canonical row a production run already wrote for that day. Giving it a
        # distinct id is enough to make it a distinct row; nothing about how a normal report's
        # id is generated changes.
        report.report_id = f"{report.report_id}_DEMO"
    return report


def _movers_section(rows: list, facts: list[Fact], narrative) -> list:
    out = []
    for row in rows or []:
        symbol = row.get("symbol")
        catalyst = narrative.catalysts.get(symbol)
        out.append({
            "symbol": symbol, "name": row.get("name"), "close": row.get("close"),
            "change_pct": row.get("pct"), "relative_volume": row.get("volx"),
            "catalyst": catalyst.to_dict() if catalyst else None,
            **({"validation": row["validation"]} if row.get("validation") else {}),
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


def save_unfit_report(report: MarketReport, out_dir: str) -> str:
    """A report that failed data validation in the REPORT job: written for diagnosis under
    reports/unfit/ with a timestamp, NEVER at the canonical path and never indexed in history,
    so a later retry can still produce the canonical report for that session."""
    directory = os.path.join(out_dir, REPORT_DIR, "unfit")
    os.makedirs(directory, exist_ok=True)
    stamp = (report.generated_at or _now_ist()).strftime("%Y%m%dT%H%M%S")
    path = os.path.join(directory, f"{report.report_id}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report.to_json())
    return path


def describe(report: MarketReport) -> str:
    s = report.validation_summary
    return (f"{s.total_facts} facts ({s.verified_count} verified, {s.single_source_count} "
            f"single-source, {s.provisional_count} provisional, {s.conflict_count} conflict) "
            f"| publication_ready={s.publication_ready}")


__all__ = ["build_report", "save_report", "facts_from", "describe", "REPORT_DIR"]
