"""POST_MARKET fact sheet: the validated session recap, as hook facts and teaser beats.

Reads exactly what the unified renderer's storyboard reads - the editorial `ShortsPlan`, the
`ReportPresentation`, the Market Radar stories and their local visual evidence - so the hook
can never cite a fact, or tease a visual, that the rest of the Short does not contain.
"""
from __future__ import annotations

from . import policy
from .models import BeatKind, HookFactSheet, HookMode
from .sheet_common import (FAMILY_CHIP, beat, breakout_payload, count_fact, event_window, fact,
                           move_claims, pct, price, primary_event, rvol_label, session_fact,
                           sign, slug, EVENT_CLAIM, EVENT_PHRASE)

SECTION_CHIP = {"PULSE": "Market", "NIFTY": "Nifty", "FLOWS": "FII/DII", "SECTORS": "Sectors",
                "MOVERS": "Movers", "RADAR": "Radar", "AHEAD": "Look ahead",
                "STRUCTURE": "Under the surface", "EXCHANGE": "Exchange watch", "IPO": "IPO watch"}


def _plan_scene(plan, kind):
    return next((s for s in getattr(plan, "scenes", []) if s.scene_type.value == kind), None)


def _insight(snapshot, insight_id):
    if snapshot is None:
        return None
    return next((i for i in snapshot.insights
                 if i.insight_id == insight_id and i.is_displayable), None)


def post_market_sheet(plan, pres, stories=(), evidence=None, sections=(), universe="Nifty 100",
                      snapshot=None) -> HookFactSheet:
    """`stories`: [(radar_presentation_story, radar_result_story)] in Radar's own order.
    `sections`: the section keys the storyboard really contains."""
    evidence = evidence or {}
    m = pres.m
    session = pres.session_date
    facts, beats = [session_fact(session)], []
    nifty_scene = _plan_scene(plan, "NIFTY")
    hook_scene = _plan_scene(plan, "HOOK")

    # ------------------------------------------------------------------ index
    nifty_refs = tuple((hook_scene.source_fact_ids if hook_scene else []) +
                       (nifty_scene.source_fact_ids if nifty_scene else []))
    if m.get("pct") is not None:
        p = float(m["pct"])
        claims = move_claims(p, policy.QUIET_INDEX_PCT, policy.BIG_INDEX_PCT)
        facts.append(fact("nifty.move", "INDEX_MOVE", "NIFTY 50",
                          f"Nifty 50 closed {pct(p)} on the session"
                          + (f" at {price(m['close'], 2)}" if m.get("close") else "") + ".",
                          pct(p), value=p, polarity=sign(p), aliases=("Nifty 50", "Nifty"),
                          claims=claims, refs=nifty_refs))
    if m.get("close") is not None:
        facts.append(fact("nifty.close", "INDEX_LEVEL", "NIFTY 50",
                          f"Nifty 50 closing level: {price(m['close'], 2)}.",
                          price(m["close"], 2), value=float(m["close"]),
                          aliases=("Nifty 50", "Nifty"), refs=nifty_refs,
                          extra_numbers=(price(m["close"]),)))
    if nifty_scene is not None and nifty_scene.secondary_text:
        facts.append(fact("nifty.context", "INDEX_CONTEXT", "NIFTY 50",
                          f"Nifty 50: {nifty_scene.secondary_text}.",
                          nifty_scene.secondary_text, aliases=("Nifty 50", "Nifty"),
                          refs=nifty_refs))
    move = _insight(snapshot, "index-move-20")
    if move is not None:
        larger = int(move.metadata.get("larger_than_sessions", 0))
        if larger >= 15:
            facts.append(fact("nifty.rank20", "INDEX_RANK", "NIFTY 50",
                              f"Nifty's move was bigger than {larger} of the last 20 sessions.",
                              f"Bigger than {larger} of the last 20 sessions",
                              aliases=("Nifty 50", "Nifty"),
                              claims={"big", "extreme_rank"} if larger >= 19 else {"big"},
                              refs=(move.insight_id,)))
    if m.get("bank_pct") is not None:
        bp = float(m["bank_pct"])
        facts.append(fact("banknifty.move", "INDEX_MOVE", "BANK NIFTY",
                          f"Bank Nifty closed {pct(bp)}.", pct(bp), value=bp, polarity=sign(bp),
                          aliases=("Bank Nifty",)))
    if m.get("vix") is not None:
        facts.append(fact("vix.level", "VIX", "INDIA VIX", f"India VIX closed at {m['vix']:.2f}.",
                          f"{m['vix']:.2f}", value=float(m["vix"]), aliases=("India VIX", "VIX")))

    # ------------------------------------------------------------------ sectors
    secs = sorted((s for s in (pres.sec or []) if s.get("pct") is not None),
                  key=lambda s: -s["pct"])
    sector_facts = []
    if secs:
        lead, lag = secs[0], secs[-1]
        opposite = (len(secs) > 1 and lead["pct"] > 0 > lag["pct"]
                    and lead["pct"] - lag["pct"] >= policy.CONTRAST_SPREAD_PP)
        for i, s in enumerate(secs):
            claims = set()
            role = ""
            # "led"/"leader" only for a sector that actually ROSE, "lagged" only for one that
            # actually FELL: on a day every sector fell, "IT led" reads as "IT rose" (found on
            # the real 24 Sep 2026 session). The statement still names it strongest/weakest.
            if len(secs) > 1 and i == 0:
                if s["pct"] > 0:
                    claims.add("leader")
                role = f" - strongest of the {len(secs)} sector indices in the report"
            if len(secs) > 1 and i == len(secs) - 1:
                if s["pct"] < 0:
                    claims.add("laggard")
                role = f" - weakest of the {len(secs)} sector indices in the report"
            if opposite and (i == 0 or i == len(secs) - 1):
                claims.add("opposite")
            f = fact(f"sector.{slug(s['name'])}", "SECTOR_MOVE", s["name"],
                     f"Nifty {s['name']} index {pct(s['pct'])}{role}.", pct(s["pct"]),
                     value=float(s["pct"]), polarity=sign(s["pct"]),
                     aliases=(f"Nifty {s['name']}", f"{s['name']} index"), claims=claims)
            facts.append(f)
            sector_facts.append((s, f))
        n_up = sum(1 for s in secs if s["pct"] > 0)
        facts.append(count_fact("sectors.count", len(secs), "tracked sector indices",
                                f"The report tracks {len(secs)} sector indices; {n_up} rose.",
                                units=("tracked", "sector", "sectors")))
        facts.append(count_fact("sectors.up", n_up, "sector indices rose",
                                f"{n_up} of {len(secs)} sector indices closed higher.",
                                units=("sector", "sectors", "of")))
        tile = lambda s, tag: {"name": s["name"], "value": pct(s["pct"]), "numeric": s["pct"],
                               "positive": s["pct"] >= 0, "tag": tag}
        # Same tags as the POST sector scene, so a uniform day is labelled honestly everywhere.
        n_down = sum(1 for s in secs if s["pct"] < 0)
        if len(secs) > 1 and n_down == len(secs):
            lead_tag, lag_tag = "FELL LEAST", "FELL MOST"
        elif len(secs) > 1 and n_up == len(secs):
            lead_tag, lag_tag = "LEADER", "ROSE LEAST"
        else:
            lead_tag, lag_tag = "LEADER", "LAGGARD"
        if len(secs) > 1:
            beats.append(beat("SECTOR_CONTRAST", BeatKind.SECTOR_PAIR,
                              f"{lead['name']} {pct(lead['pct'])} vs {lag['name']} {pct(lag['pct'])} tiles",
                              (sector_facts[0][1].fact_id, sector_facts[-1][1].fact_id),
                              {"left": tile(lead, lead_tag), "right": tile(lag, lag_tag),
                               "vs_label": "VS"}))
        beats.append(beat("SECTOR_LEADER", BeatKind.SECTOR_TILE,
                          f"{lead['name']} {pct(lead['pct'])} heat tile",
                          (sector_facts[0][1].fact_id,),
                          tile(lead, lead_tag if len(secs) > 1 else "SECTOR")))

    # ------------------------------------------------------------------ movers
    g, lo = _plan_scene(plan, "GAINERS"), _plan_scene(plan, "LOSERS")
    for scene, tag, claim, key in ((g, "TOP GAINER", "top_gainer", "gainer"),
                                   (lo, "TOP LOSER", "top_loser", "loser")):
        if scene is None or not scene.items:
            continue
        it = scene.items[0]
        if it.numeric is None:
            continue
        claims = {claim} | move_claims(it.numeric, None, policy.BIG_STOCK_PCT)
        fid = f"mover.{key}"
        facts.append(fact(fid, "STOCK_MOVE", it.title,
                          f"{it.title} {it.value} - the {'top gainer' if key == 'gainer' else 'top loser'} "
                          f"in the {universe} on the session.", it.value, value=float(it.numeric),
                          polarity=sign(it.numeric), claims=claims,
                          refs=tuple(it.source_fact_ids)))
        beats.append(beat(f"TOP_{key.upper()}", BeatKind.MOVER_BAR,
                          f"{it.title} {it.value} bar ({tag.lower()}, {universe})", (fid,),
                          {"name": it.title, "value": it.value, "numeric": float(it.numeric),
                           "positive": it.numeric >= 0, "tag": tag, "context": universe}))

    # ------------------------------------------------------------------ radar
    radar_moves = []
    for sp, story in stories:
        sym = sp["instrument"]
        key = slug(sym)
        chg = story.get("price_change_pct")
        change = sp.get("price_change_display") or (pct(chg, 1) if chg is not None else "")
        aliases = tuple(a for a in (sp.get("company_name") or "",) if a)
        refs = (story.get("selection_id") or sym,)
        if chg is not None and change:
            claims = move_claims(chg, None, policy.BIG_STOCK_PCT)
            f = fact(f"radar.{key}.move", "STOCK_MOVE", sym,
                     f"{sym} (Market Radar) moved {change} on the session.", change,
                     value=float(chg), polarity=sign(chg), aliases=aliases, claims=claims,
                     refs=refs)
            facts.append(f)
            radar_moves.append((sym, change, float(chg), f))
        events = (story.get("technical_context") or {}).get("events") or []
        ev = primary_event(events)
        evd = evidence.get(sym)
        edge = ""
        band = None
        w = event_window(ev)
        if ev and evd is not None and "RANGE" in ev and ev != "RANGE_COMPRESSION":
            hi = evd.range_50_high if w == 50 else evd.range_20_high
            lo_ = evd.range_50_low if w == 50 else evd.range_20_low
            if hi is not None and lo_ is not None:
                band = (lo_, hi)
                edge = (f"{w}-day high {price(hi, 1)}" if "ABOVE" in ev else f"{w}-day low {price(lo_, 1)}")
        if ev and ev in EVENT_PHRASE:
            phrase = EVENT_PHRASE[ev]
            display = f"{phrase} ({edge.split(' ', 2)[-1]})" if edge else phrase
            facts.append(fact(f"radar.{key}.event", "RADAR_EVENT", sym, f"{sym}: {display}.",
                              display, aliases=aliases,
                              claims={EVENT_CLAIM[ev]} if ev in EVENT_CLAIM else set(), refs=refs))
        rvol = (story.get("volume_context") or {}).get("relative_volume")
        if rvol is not None:
            facts.append(fact(f"radar.{key}.volume", "RADAR_VOLUME", sym,
                              f"{sym} traded {rvol_label(rvol)} (vs its prior 20-session average).",
                              rvol_label(rvol), value=float(rvol), aliases=aliases,
                              claims={"unusual_volume"} if rvol >= policy.UNUSUAL_RVOL else set(),
                              refs=refs))
        rel = (story.get("relative_context") or {}).get("market_relative_20d_pp")
        if rel is not None:
            facts.append(fact(f"radar.{key}.relative", "RADAR_RELATIVE", sym,
                              f"{sym} is {rel:+.1f} pts vs Nifty over the last 20 sessions.",
                              f"{rel:+.1f} pts vs Nifty (20 sessions)", value=float(rel),
                              aliases=aliases, refs=refs))
        k = story.get("independent_signal_count") or 0
        if k >= 3:
            facts.append(fact(f"radar.{key}.signals", "RADAR_SIGNALS", sym,
                              f"{sym}: {k} independent signal families changed together.",
                              f"{k} signals changed together", aliases=aliases,
                              claims={"convergence"}, refs=refs))
        if evd is not None and ev:
            ma = None
            if "SMA" in ev:
                ma = evd.sma50_series if w == 50 else evd.sma20_series
            payload = breakout_payload(sym, change, (chg or 0) >= 0, evd.close_series, ev,
                                       band=band, ma=ma, edge_label=edge)
            payload["company"] = sp.get("company_name") or ""
            payload["chips"] = [FAMILY_CHIP[f] for f in ("STRUCTURE", "VOLUME", "RELATIVE_PERFORMANCE")
                                if f in (story.get("active_families") or [])]
            if rvol is not None and evd.volume_series:
                payload["volumes"] = [float(v or 0) for v in evd.volume_series]
                payload["volume_label"] = rvol_label(rvol)
            fids = [f"radar.{key}.move", f"radar.{key}.event"]
            beats.append(beat(f"RADAR_EVENT:{sym}", BeatKind.BREAKOUT,
                              f"{sym} chart: {EVENT_PHRASE.get(ev, ev).lower()}, marker on the last close",
                              [x for x in fids if any(f.fact_id == x for f in facts)], payload))
            if rvol is not None and evd.volume_series:
                beats.append(beat(f"VOLUME_SPIKE:{sym}", BeatKind.VOLUME,
                                  f"{sym} volume bars, last bar amber: {rvol_label(rvol)}",
                                  (f"radar.{key}.volume",),
                                  {"symbol": sym, "volumes": [float(v or 0) for v in evd.volume_series],
                                   "label": rvol_label(rvol), "title": "VOLUME"}))
    if stories:
        n = len(stories)
        facts.append(count_fact("radar.count", n, "stocks flagged by Market Radar",
                                f"Market Radar flagged {n} stocks in the {universe} this session.",
                                units=("stocks", "radar", "flagged", "names")))
        beats.append(beat("RADAR_SWEEP", BeatKind.RADAR,
                          f"radar sweep lighting {n} blips: {', '.join(sp['instrument'] for sp, _ in stories)}",
                          ("radar.count",),
                          {"names": [sp["instrument"] for sp, _ in stories],
                           "count_label": f"{n} stocks flagged"}))
        # "Hidden action": flagged stocks that moved at least 3x the index and at least 1%.
        np_ = abs(float(m["pct"])) if m.get("pct") is not None else None
        if np_ is not None:
            hidden = [r for r in radar_moves if abs(r[2]) >= max(1.0, 3 * np_)]
            if hidden:
                facts.append(count_fact(
                    "radar.hidden", len(hidden), "flagged stocks moved 3x+ Nifty",
                    f"{len(hidden)} Market Radar stocks each moved at least 1% and at least 3x "
                    f"Nifty's {pct(m['pct'])}: " + ", ".join(f"{s} {c}" for s, c, _, _ in hidden) + ".",
                    refs=tuple(f.fact_id for *_, f in hidden),
                    units=("stocks", "flagged", "radar", "names")))

    # ------------------------------------------------------------------ flows
    fl = _plan_scene(plan, "FLOWS")
    if fl is not None and len(fl.items) >= 2:
        rows = []
        vals = []
        for it in fl.items[:2]:
            who = "FII" if "FII" in it.title.upper() else ("DII" if "DII" in it.title.upper() else it.title)
            vals.append(it.numeric)
            rows.append({"name": who, "value": it.value, "numeric": it.numeric,
                         "positive": (it.numeric or 0) >= 0})
        opp = (vals[0] is not None and vals[1] is not None and vals[0] * vals[1] < 0
               and min(abs(vals[0]), abs(vals[1])) >= policy.FLOW_CONTRAST_CRORE)
        for r, it in zip(rows, fl.items[:2]):
            facts.append(fact(f"flows.{r['name'].lower()}", "FLOW", r["name"],
                              f"{r['name']}s net {'bought' if r['positive'] else 'sold'} {r['value']} "
                              f"in the cash market (provisional).", r["value"], value=r["numeric"],
                              polarity=sign(r["numeric"]), aliases=(f"{r['name']}s",),
                              claims={"opposite"} if opp else set(), refs=tuple(it.source_fact_ids)))
        beats.append(beat("FLOWS", BeatKind.FLOWS, "FII vs DII net cash bars",
                          tuple(f"flows.{r['name'].lower()}" for r in rows), {"rows": rows}))

    # ------------------------------------------------------------------ look ahead
    glob = _plan_scene(plan, "GLOBAL")
    if glob is not None:
        for it in glob.items:
            if it.numeric is None:
                continue
            fid = f"global.{slug(it.title)}"
            facts.append(fact(fid, "GLOBAL_CUE", it.title, f"{it.title} {it.value} overnight.",
                              it.value, value=float(it.numeric), polarity=sign(it.numeric),
                              aliases=_global_aliases(it.title),
                              claims=move_claims(it.numeric, None, policy.BIG_GLOBAL_PCT),
                              refs=tuple(it.source_fact_ids)))
        top = max((it for it in glob.items if it.numeric is not None),
                  key=lambda it: abs(it.numeric), default=None)
        if top is not None:
            beats.append(beat(f"GLOBAL_CUE:{slug(top.title)}", BeatKind.CUE,
                              f"{top.title} {top.value} overnight tile",
                              (f"global.{slug(top.title)}",),
                              {"name": top.title, "value": top.value, "positive": top.numeric >= 0,
                               "note": "OVERNIGHT"}))

    # ------------------------------------------------------------------ Nifty line beat
    df = m.get("chart_df")
    if df is not None and len(df) >= 10 and m.get("pct") is not None:
        tail = df.tail(40)
        beats.insert(0, beat("NIFTY_CLOSE", BeatKind.LINE,
                             f"Nifty 50 line drawing to its close {price(m['close'], 2)} ({pct(m['pct'])})",
                             ("nifty.move", "nifty.close"),
                             {"series": [float(v) for v in tail["Close"]], "label": "NIFTY 50",
                              "value": price(m["close"], 2), "change": pct(m["pct"]),
                              "positive": m["pct"] >= 0,
                              "candles": {"open": [float(v) for v in tail["Open"].tail(20)],
                                          "high": [float(v) for v in tail["High"].tail(20)],
                                          "low": [float(v) for v in tail["Low"].tail(20)],
                                          "close": [float(v) for v in tail["Close"].tail(20)]}}))

    sections = [s for s in sections if s in SECTION_CHIP]
    labels = []
    for s in sections:
        lab = SECTION_CHIP[s]
        if s == "RADAR" and stories:
            lab = f"Radar ×{len(stories)}"
        labels.append(lab)
    return HookFactSheet(mode=HookMode.POST_MARKET, session_date=session, facts=facts, beats=beats,
                         sections=list(sections), section_labels=labels,
                         metadata={"universe": universe, "radar_count": len(stories),
                                   "session_label": session.strftime("%A")})


def _global_aliases(title: str) -> tuple:
    t = title.upper()
    table = {"DOW JONES": ("Dow Jones", "Dow"), "NASDAQ": ("Nasdaq",), "S&P 500": ("S&P",),
             "USD / INR": ("USD/INR", "rupee"), "GOLD": ("Gold",), "BRENT": ("Brent", "crude"),
             "NIKKEI": ("Nikkei",), "HANG SENG": ("Hang Seng",)}
    return table.get(t, (title.title(),))


__all__ = ["post_market_sheet", "SECTION_CHIP"]
