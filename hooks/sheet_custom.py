"""CUSTOM_SINGLE_STOCK fact sheet: one stock's strongest validated facts.

The custom-stock video does not exist yet (Phase 1 builds only its hook). `CustomStockInputs`
is the contract its data layer will fill: series and levels from the local OHLCV store (the
same `RadarVisualEvidence` fields Market Radar uses), detector event types, and - when a
validated fundamentals source exists - fundamental figures. Nothing here computes any of them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import policy
from .models import BeatKind, HookFactSheet, HookMode
from .sheet_common import (EVENT_CLAIM, EVENT_PHRASE, FAMILY_CHIP, beat, breakout_payload,
                           count_fact, event_window, fact, move_claims, pct, price,
                           primary_event, rvol_label, session_fact, sign)

SECTION_CHIP = {"TECHNICAL": "Price trend", "VOLUME": "Volume", "RELATIVE": "vs Nifty",
                "FUNDAMENTALS": "Fundamentals", "LEVELS": "Levels"}
WEAK_EVENTS = {"CROSS_BELOW_SMA20", "CROSS_BELOW_SMA50", "BREAK_BELOW_20D_RANGE",
               "BREAK_BELOW_50D_RANGE"}


@dataclass
class CustomStockInputs:
    symbol: str
    company: str
    session_date: dt.date
    close: float
    change_pct: float
    closes: list
    volumes: list | None = None
    sma20: list | None = None
    sma50: list | None = None
    range20: tuple | None = None          # (low, high) of the prior 20 sessions
    range50: tuple | None = None
    events: list = field(default_factory=list)       # detector event types
    rvol: float | None = None
    rel20_pp: float | None = None
    stock_norm: list | None = None
    bench_norm: list | None = None
    families: list = field(default_factory=list)
    fundamentals: list = field(default_factory=list)  # {"key","label","display","value","claims"}
    sections: list = field(default_factory=list)


def custom_stock_sheet(inp: CustomStockInputs) -> HookFactSheet:
    sym = inp.symbol
    aliases = (inp.company,) if inp.company else ()
    facts = [session_fact(inp.session_date)]
    beats = []
    ch = pct(inp.change_pct, 1)
    facts.append(fact("stock.move", "STOCK_MOVE", sym, f"{sym} closed {ch} at {price(inp.close, 1)}.",
                      ch, value=inp.change_pct, polarity=sign(inp.change_pct), aliases=aliases,
                      claims=move_claims(inp.change_pct, 1.0, policy.BIG_STOCK_PCT)))
    facts.append(fact("stock.close", "STOCK_LEVEL", sym, f"{sym} closing price {price(inp.close, 1)}.",
                      price(inp.close, 1), value=inp.close, aliases=aliases))

    ev = primary_event(inp.events)
    w = event_window(ev)
    band = edge = None
    if ev and "RANGE" in ev and ev != "RANGE_COMPRESSION":
        rng = inp.range50 if w == 50 else inp.range20
        if rng:
            band = rng
            edge = f"{w}-day high {price(rng[1], 1)}" if "ABOVE" in ev else f"{w}-day low {price(rng[0], 1)}"
    if ev in EVENT_PHRASE:
        claims = {EVENT_CLAIM[ev]} if ev in EVENT_CLAIM else set()
        if ev in WEAK_EVENTS:
            claims.add("weak_technical")
        display = f"{EVENT_PHRASE[ev]} ({edge.split(' ', 2)[-1]})" if edge else EVENT_PHRASE[ev]
        facts.append(fact("stock.event", "STOCK_EVENT", sym, f"{sym}: {display}.", display,
                          aliases=aliases, claims=claims))
    if inp.rvol is not None:
        facts.append(fact("stock.volume", "STOCK_VOLUME", sym,
                          f"{sym} traded {rvol_label(inp.rvol)} (vs its prior 20-session average).",
                          rvol_label(inp.rvol), value=inp.rvol, aliases=aliases,
                          claims={"unusual_volume"} if inp.rvol >= policy.UNUSUAL_RVOL else set()))
    if inp.rel20_pp is not None:
        facts.append(fact("stock.relative", "STOCK_RELATIVE", sym,
                          f"{sym} is {inp.rel20_pp:+.1f} pts vs Nifty over 20 sessions.",
                          f"{inp.rel20_pp:+.1f} pts vs Nifty (20 sessions)", value=inp.rel20_pp,
                          aliases=aliases))
    signals = [f for f in inp.families if f in FAMILY_CHIP]
    if len(signals) >= 3:
        facts.append(fact("stock.signals", "STOCK_SIGNALS", sym,
                          f"{sym}: {len(signals)} independent signal families changed together.",
                          f"{len(signals)} signals changed together", aliases=aliases,
                          claims={"convergence"}))
    for fd in inp.fundamentals:
        facts.append(fact(f"fund.{fd['key']}", "FUNDAMENTAL", sym,
                          f"{sym} {fd['label']}: {fd['display']}.", fd["display"],
                          value=fd.get("value"), polarity=None, aliases=aliases,
                          claims=set(fd.get("claims") or ())))
        beats.append(beat(f"METRIC:{fd['key']}", BeatKind.METRIC, f"{fd['label']} {fd['display']} card",
                          (f"fund.{fd['key']}",),
                          {"label": fd["label"].upper(), "value": fd["display"],
                           "positive": (fd.get("value") or 0) >= 0, "note": fd.get("period", "")}))
    if inp.fundamentals:
        facts.append(count_fact("fund.count", len(inp.fundamentals), "fundamental figures",
                                f"{len(inp.fundamentals)} validated fundamental figures.",
                                units=("fundamental", "figures", "numbers")))

    ma = None
    if ev and "SMA" in ev:
        ma = inp.sma50 if w == 50 else inp.sma20
    payload = breakout_payload(sym, ch, inp.change_pct >= 0, inp.closes, ev, band=band, ma=ma,
                               edge_label=edge or "")
    payload["company"] = inp.company
    payload["chips"] = [FAMILY_CHIP[f] for f in ("STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE")
                        if f in inp.families]
    if inp.rvol is not None and inp.volumes:
        payload["volumes"] = [float(v) for v in inp.volumes]
        payload["volume_label"] = rvol_label(inp.rvol)
    beats.append(beat("STOCK_TREND", BeatKind.LINE, f"{sym} price line to its close ({ch})",
                      ("stock.move", "stock.close"),
                      {"series": [float(v) for v in inp.closes], "label": sym,
                       "value": price(inp.close, 1), "change": ch, "positive": inp.change_pct >= 0}))
    if ev:
        beats.append(beat("STOCK_EVENT", BeatKind.BREAKOUT,
                          f"{sym} chart: {EVENT_PHRASE.get(ev, ev).lower()}",
                          [x for x in ("stock.move", "stock.event") if any(f.fact_id == x for f in facts)],
                          payload))
    if inp.rvol is not None and inp.volumes:
        beats.append(beat("STOCK_VOLUME", BeatKind.VOLUME, f"{sym} volume, last bar {rvol_label(inp.rvol)}",
                          ("stock.volume",),
                          {"symbol": sym, "volumes": [float(v) for v in inp.volumes],
                           "label": rvol_label(inp.rvol), "title": "VOLUME"}))
    labels = [SECTION_CHIP.get(s, s.title()) for s in inp.sections]
    return HookFactSheet(mode=HookMode.CUSTOM_SINGLE_STOCK, session_date=inp.session_date,
                         facts=facts, beats=beats, sections=list(inp.sections), section_labels=labels,
                         metadata={"symbol": sym, "chart_payload": payload,
                                   "session_label": inp.session_date.strftime("%A")})


__all__ = ["CustomStockInputs", "custom_stock_sheet"]
