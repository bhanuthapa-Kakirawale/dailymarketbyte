"""Deterministic HookCandidateBuilder: every opening today's validated facts support.

Each archetype has a trigger (a rule on validated facts and their deterministic claims), a
score (how strong a hook it makes - higher only when the underlying reading is more unusual),
a hero payload built from the sheet's own validated series, default teaser beats, and a
templated curiosity + summary line. Templates are run through the same validator Gemini's
lines face; a candidate whose own template fails is dropped, never offered.

Ordering is total and stable: score, then the archetype's fixed order, then candidate id.
"""
from __future__ import annotations

from dataclasses import replace

from . import diversity, policy
from .models import (ARCHETYPE_HEROES, Archetype, HeroVisual, HookCandidate, HookFactSheet,
                     HookMode)
from .sheet_common import count_fact, strings_of
from .text import spell
from .validation import check_text

ORDER = {a: i for i, a in enumerate(Archetype)}
SUMMARY_LEAD = {HookMode.POST_MARKET: "Inside:", HookMode.PRE_MARKET: "Before the bell:",
                HookMode.CUSTOM_SINGLE_STOCK: "Inside:"}
SECTION_ORDER = ("PULSE", "NIFTY", "FLOWS", "SECTORS", "MOVERS", "RADAR", "EXCHANGE", "IPO",
                 "STRUCTURE", "AHEAD", "GLOBAL",
                 "GIFT", "EVENTS", "PREV", "TECHNICAL", "VOLUME", "RELATIVE", "FUNDAMENTALS")
# Which sections each archetype's summary mentions first - the ones that pay off its hook.
SUMMARY_PRIORITY = {
    Archetype.QUIET_MARKET_HIDDEN_ACTION: ("RADAR", "STRUCTURE", "SECTORS", "MOVERS", "NIFTY"),
    Archetype.BIG_MOVE: ("NIFTY", "SECTORS", "MOVERS", "FLOWS", "PREV", "GLOBAL", "TECHNICAL",
                         "VOLUME", "FUNDAMENTALS"),
    Archetype.CONTRAST: ("SECTORS", "FLOWS", "MOVERS", "RADAR", "TECHNICAL", "FUNDAMENTALS",
                         "VOLUME"),
    Archetype.UNUSUAL_ACTIVITY: ("RADAR", "MOVERS", "SECTORS", "TECHNICAL", "VOLUME",
                                 "FUNDAMENTALS"),
    Archetype.OVERNIGHT_CUE: ("GLOBAL", "GIFT", "EVENTS", "PREV"),
    Archetype.EVENT_LED: ("EVENTS", "GLOBAL", "GIFT", "PREV", "SECTORS"),
    Archetype.THINGS_TO_KNOW: ("NIFTY", "SECTORS", "RADAR", "MOVERS", "GLOBAL", "GIFT",
                               "EVENTS", "PREV", "TECHNICAL", "VOLUME", "FUNDAMENTALS"),
}


# --------------------------------------------------------------------------- helpers
def _f(sheet, fid):
    return sheet.fact(fid)


def _abs_display(display: str) -> str:
    return display.lstrip("+-−")


def _verb(value: float) -> str:
    return "rose" if value > 0 else "fell"


def _short(entity: str, sheet) -> str:
    """The friendliest alias of an entity for running text ("Nifty", "Nasdaq")."""
    f = next((f for f in sheet.facts if f.entity == entity), None)
    if f is None:
        return entity
    for a in f.aliases[1:]:
        if a and not a.isupper() and len(a) <= len(entity) + 2 and a.lower() != "vix":
            return a
    return entity


def _section_phrase(sec: str, sheet: HookFactSheet) -> str | None:
    n_sectors = sheet.fact("sectors.count")
    radar = sheet.fact("radar.count")
    fund = sheet.fact("fund.count")
    return {
        "PULSE": "the close",
        "NIFTY": "the Nifty chart",
        "FLOWS": "FII/DII flows",
        "SECTORS": ("every sector" if n_sectors and n_sectors.value > 1 else "the sector check"),
        "MOVERS": "top movers",
        "RADAR": (f"{int(radar.value)} Radar {'stock' if radar.value == 1 else 'stocks'}"
                  if radar else "Market Radar"),
        "AHEAD": "overnight cues",
        "GLOBAL": "overnight cues",
        "EVENTS": "today's events",
        "GIFT": "GIFT Nifty",
        "PREV": "the previous session",
        "WATCH": "what to watch",
        "TECHNICAL": "the price trend",
        "VOLUME": "volume",
        "RELATIVE": "the Nifty comparison",
        "FUNDAMENTALS": f"{int(fund.value)} fundamental figures" if fund else "the fundamentals",
        "STRUCTURE": "what moved under the surface",
        "EXCHANGE": "exchange updates",
        "IPO": "IPO facts",
    }.get(sec)


def summary_line(sheet: HookFactSheet, archetype: Archetype) -> str:
    """"Inside: every sector, top movers and 5 Radar stocks." - only sections the Short has,
    the archetype's pay-off first, written in the order the video plays them."""
    lead = SUMMARY_LEAD[sheet.mode]
    present = [s for s in sheet.sections]
    chosen = [s for s in SUMMARY_PRIORITY[archetype] if s in present]
    chosen += [s for s in present if s not in chosen]
    phrases = []
    for sec in chosen:
        ph = _section_phrase(sec, sheet)
        if ph and ph not in phrases:
            phrases.append((sec, ph))
        if len(phrases) == 3:
            break
    if sheet.mode is HookMode.PRE_MARKET:
        # PRE: `sheet.sections` IS the PreSectionPlan's play order - say them in that order
        phrases.sort(key=lambda p: present.index(p[0]))
    else:
        phrases.sort(key=lambda p: SECTION_ORDER.index(p[0]) if p[0] in SECTION_ORDER else 99)
    for k in (3, 2, 1):
        parts = [p for _, p in phrases[:k]]
        if not parts:
            break
        body = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        text = f"{lead} {body}."
        if len(text) <= policy.SUMMARY_MAX_CHARS and len(text.split()) <= policy.SUMMARY_MAX_WORDS:
            return text
    return f"{lead} today's validated numbers."


def pick_beats(sheet: HookFactSheet, preferred: list, exclude=()) -> tuple:
    """First available beats from `preferred` (exact ids, "PREFIX:" families, or a tuple of
    alternatives - the first available one counts), topped up
    from the rest of the sheet so there are 2-3 whenever the sheet has them.

    POST (freeze): one beat per entity, different information families preferred, played
    index -> sector/flow -> stock, and fewer beats rather than a repeat - see hooks/diversity."""
    if diversity.applies(sheet):
        return diversity.pick_diverse(sheet, preferred, exclude, policy.MAX_BEATS, policy.MIN_BEATS)
    out = []
    for want in preferred:
        if want is None:
            continue
        for alt in (want if isinstance(want, tuple) else (want,)):
            hit = next((b.beat_id for b in sheet.beats
                        if (b.beat_id == alt or (alt.endswith(":") and b.beat_id.startswith(alt)))
                        and b.beat_id not in out and b.beat_id not in exclude), None)
            if hit:
                out.append(hit)
                break
        if len(out) == policy.MAX_BEATS:
            break
    for b in sheet.beats:
        if len(out) >= policy.MIN_BEATS:
            break
        if b.beat_id not in out and b.beat_id not in exclude:
            out.append(b.beat_id)
    return tuple(out)


def _cand(sheet, cid, archetype, score, fact_ids, heroes, beats, curiosity_options, rationale):
    heroes = {h: p for h, p in heroes.items() if p is not None and h in ARCHETYPE_HEROES[archetype]}
    if not heroes:
        return None
    order = [h for h in ARCHETYPE_HEROES[archetype] if h in heroes]
    summ = summary_line(sheet, archetype)
    base = HookCandidate(candidate_id=cid, archetype=archetype, score=round(score, 4),
                         fact_ids=tuple(f for f in fact_ids if f and sheet.fact(f)),
                         heroes={h: heroes[h] for h in order}, default_hero=order[0],
                         default_beats=tuple(beats), curiosity_line="", summary_line=summ,
                         rationale=rationale,
                         hero_strings={h: strings_of(heroes[h]) for h in order})
    for line in curiosity_options:
        if not line:
            continue
        cand = replace(base, curiosity_line=line)
        if not check_text(line, summ, sheet, cand, beats):
            return cand
    return None


# --------------------------------------------------------------------------- hero payloads
def _lollipop(sheet, stock_fact_ids):
    nifty = sheet.fact("nifty.move")
    stocks = [sheet.fact(fid) for fid in stock_fact_ids]
    stocks = [f for f in stocks if f is not None and f.value is not None]
    if nifty is None or len(stocks) < 2:
        return None
    stocks = sorted(stocks, key=lambda f: -f.value)[:6]
    return {"index": {"name": "NIFTY 50", "value": nifty.display, "numeric": nifty.value},
            "stocks": [{"name": f.entity, "value": f.display, "numeric": f.value,
                        "positive": f.value >= 0} for f in stocks],
            "axis_label": "MOVE ON THE SESSION", "zero_label": "0%"}


def _stack_chart(sheet, beat_id):
    b = sheet.beat(beat_id)
    if b is None:
        return None
    return dict(b.payload)


def _headline(sheet, label, move_fact, line_beat=None, sub="", context=""):
    b = sheet.beat(line_beat) if line_beat else None
    return {"label": label, "value": move_fact.display, "positive": (move_fact.value or 0) >= 0,
            "series": b.payload.get("series") if b else None,
            "candles": b.payload.get("candles") if b else None,
            "sub": sub, "context": context}


def _numbered(items):
    items = [dict(i, n=str(k + 1)) for k, i in enumerate(i for i in items if i)]
    return {"items": items} if len(items) >= 2 else None


def _item(f, label=None):
    if f is None:
        return None
    return {"label": label or f.entity, "value": f.display,
            "positive": None if f.polarity is None else f.polarity >= 0}


# --------------------------------------------------------------------------- POST
def _post(sheet):
    out = []
    nifty = sheet.fact("nifty.move")
    hidden = sheet.fact("radar.hidden")
    radar_moves = [f for f in sheet.facts if f.kind == "STOCK_MOVE" and f.fact_id.startswith("radar.")]
    top_radar = _top_radar(sheet)

    # QUIET MARKET, HIDDEN ACTION
    if nifty and "quiet" in nifty.claims and hidden and hidden.value >= 2:
        n = int(hidden.value)
        big = max((abs(f.value) for f in radar_moves), default=0)
        hidden_ids = list(hidden.source_refs)
        opposite = _opposite_radar(sheet, top_radar)
        c = _cand(sheet, "post-quiet-hidden", Archetype.QUIET_MARKET_HIDDEN_ACTION,
                  0.60 + 0.10 * min(n, 5) / 5 + (0.10 if big >= policy.BIG_STOCK_PCT else 0),
                  ["nifty.move", "radar.hidden", "radar.count"],
                  {HeroVisual.DEPTH_LOLLIPOP: _lollipop(sheet, hidden_ids),
                   HeroVisual.SIGNAL_STACK_CHART: _stack_chart(sheet, f"RADAR_EVENT:{top_radar}")},
                  pick_beats(sheet, ["NIFTY_CLOSE", ("SECTOR_CONTRAST", "SECTOR_LEADER", "FLOWS"),
                                     (f"RADAR_EVENT:{top_radar}", f"VOLUME_SPIKE:{top_radar}",
                                      f"RADAR_EVENT:{opposite}" if opposite else None)]),
                  [f"Nifty moved just {nifty.display}. {spell(n).title()} stocks underneath didn't.",
                   f"Nifty moved just {nifty.display}. {n} flagged stocks didn't."],
                  f"Nifty moved only {nifty.display} (quiet); {n} Market Radar stocks each moved "
                  f"at least 3x as much.")
        out.append(c)

    # QUIET MARKET, ACTIVITY UNDERNEATH - the public (Market Structure) form: a count over a
    # named universe, never a stock name (publication.public_hooks.add_structure_facts)
    st = sheet.fact("structure.unusual")
    if nifty and "quiet" in nifty.claims and st and st.value >= 5:
        n, U = int(st.value), st.entity
        c = _cand(sheet, "post-quiet-structure", Archetype.QUIET_MARKET_HIDDEN_ACTION,
                  0.60 + 0.10 * min(n, 20) / 20,
                  ["nifty.move", "structure.unusual"],
                  {HeroVisual.HEADLINE_NUMBER: {
                      "label": f"{U} · UNUSUAL VOLUME",
                      "value": str(sheet.metadata.get("structure_denominator", n)),
                      "positive": None, "sub": "", "context": f"NIFTY 50 {nifty.display}"}},
                  pick_beats(sheet, ["NIFTY_CLOSE", ("SECTOR_CONTRAST", "SECTOR_LEADER", "FLOWS")]),
                  [f"Nifty moved just {nifty.display}. {n} {U} stocks saw unusual volume.",
                   f"{n} {U} stocks saw unusual volume."],
                  f"Nifty moved only {nifty.display} (quiet); {st.statement}")
        out.append(c)

    # BIG MOVE
    if nifty and "big" in nifty.claims:
        rank = sheet.fact("nifty.rank20")
        up = sheet.fact("sectors.up")
        total = sheet.fact("sectors.count")
        lines = []
        if rank:
            lines.append(f"Nifty {_verb(nifty.value)} {_abs_display(nifty.display)}. {rank.display}.")
        if up and total and total.value >= 2 and up.value in (0, total.value) and \
                (up.value == 0) == (nifty.value < 0):
            lines.append(f"Nifty {_verb(nifty.value)} {_abs_display(nifty.display)}. "
                         f"All {int(total.value)} tracked sector indices {_verb(nifty.value)} too.")
        lines.append(f"Nifty {_verb(nifty.value)} {_abs_display(nifty.display)} in one session.")
        close = sheet.fact("nifty.close")
        ctx = sheet.fact("nifty.context")
        mover = "TOP_LOSER" if nifty.value < 0 else "TOP_GAINER"
        c = _cand(sheet, "post-big-move", Archetype.BIG_MOVE,
                  0.70 + 0.10 * min(abs(nifty.value) - 1, 2) / 2 +
                  (0.12 if rank and "extreme_rank" in rank.claims else 0),
                  ["nifty.move", "nifty.rank20", "sectors.up", "sectors.count"],
                  {HeroVisual.HEADLINE_NUMBER: _headline(
                      sheet, "NIFTY 50", nifty, "NIFTY_CLOSE",
                      sub=f"Closed {close.display}" if close else "",
                      context=(rank.display if rank else (ctx.display if ctx else "")))},
                  pick_beats(sheet, ["NIFTY_CLOSE", ("SECTOR_CONTRAST", "SECTOR_LEADER", "FLOWS"),
                                     (mover, f"RADAR_EVENT:{top_radar}" if top_radar else None,
                                      "RADAR_SWEEP")]),
                  lines,
                  f"Nifty moved {nifty.display} - a large one-day move.")
        out.append(c)

    # CONTRAST: sectors pulling opposite ways, or FIIs vs DIIs
    opp = [f for f in sheet.facts_of_kind("SECTOR_MOVE") if "opposite" in f.claims]
    if len(opp) == 2:
        lead, lag = sorted(opp, key=lambda f: -f.value)
        L, G = _short(lead.entity, sheet), _short(lag.entity, sheet)
        tile = lambda f, title, verdict: {"title": title, "name": f.entity, "value": f.display,
                                          "numeric": f.value, "positive": f.value >= 0,
                                          "note": verdict}
        spread = lead.value - lag.value
        c = _cand(sheet, "post-sector-contrast", Archetype.CONTRAST,
                  0.58 + 0.12 * min(spread - policy.CONTRAST_SPREAD_PP, 3) / 3,
                  [lead.fact_id, lag.fact_id],
                  {HeroVisual.VERSUS_SPLIT: {"left": tile(lead, "LEADER", "Strongest sector"),
                                             "right": tile(lag, "LAGGARD", "Weakest sector"),
                                             "vs_label": "VS"}},
                  pick_beats(sheet, ["NIFTY_CLOSE", "FLOWS",
                                     ("TOP_GAINER", "TOP_LOSER",
                                      f"RADAR_EVENT:{top_radar}" if top_radar else None,
                                      "RADAR_SWEEP")],
                             exclude=("SECTOR_CONTRAST", "SECTOR_LEADER")),
                  [f"{L} {lead.display}, {G} {lag.display}. Same session, opposite directions.",
                   f"{L} {lead.display}, {G} {lag.display}. Opposite directions.",
                   f"{L} {lead.display}. {G} {lag.display}."],
                  f"{lead.entity} {lead.display} vs {lag.entity} {lag.display}: sectors split.")
        out.append(c)
    flows = [f for f in sheet.facts_of_kind("FLOW") if "opposite" in f.claims]
    if len(flows) == 2:
        a, b = flows
        verb = lambda f: "net buyers" if f.value >= 0 else "net sellers"
        tile = lambda f: {"title": f"{f.entity}s", "name": f"{f.entity}s {verb(f)}",
                          "value": f.display, "numeric": f.value, "positive": f.value >= 0,
                          "note": "Net, cash market"}
        c = _cand(sheet, "post-flow-contrast", Archetype.CONTRAST,
                  0.56 + 0.1 * min(min(abs(a.value), abs(b.value)) / 5000, 1),
                  [a.fact_id, b.fact_id],
                  {HeroVisual.VERSUS_SPLIT: {"left": tile(a), "right": tile(b), "vs_label": "VS"}},
                  pick_beats(sheet, ["NIFTY_CLOSE", ("SECTOR_CONTRAST", "SECTOR_LEADER"),
                                     ("TOP_LOSER", "TOP_GAINER",
                                      f"RADAR_EVENT:{top_radar}" if top_radar else None,
                                      "RADAR_SWEEP")],
                             exclude=("FLOWS",)),
                  [f"{a.entity}s were {verb(a)}: {_abs_display(a.display)}. "
                   f"{b.entity}s were {verb(b)}.",
                   f"{a.entity}s were {verb(a)}; {b.entity}s were {verb(b)}."],
                  f"{a.entity}s and {b.entity}s moved money in opposite directions.")
        out.append(c)

    # UNUSUAL ACTIVITY on one Radar stock
    if top_radar:
        key = top_radar.replace("-", "_")
        mv = sheet.fact(f"radar.{_slug(top_radar)}.move")
        vol = sheet.fact(f"radar.{_slug(top_radar)}.volume")
        ev = sheet.fact(f"radar.{_slug(top_radar)}.event")
        sig = sheet.fact(f"radar.{_slug(top_radar)}.signals")
        strong = (sig is not None or (vol and "unusual_volume" in vol.claims and mv and
                                      abs(mv.value) >= policy.HIDDEN_ACTION_STOCK_PCT))
        if mv and strong:
            lines = []
            if vol and "unusual_volume" in vol.claims:
                lines.append(f"{top_radar} {mv.display} on {vol.display}.")
            if ev:
                lines.append(f"{top_radar} {mv.display}. {ev.display.split(' (')[0]}.")
            lines.append(f"{top_radar} {mv.display}.")
            score = (0.60 + (0.06 if sig else 0) + (0.05 if vol and vol.value >= 3 else 0)
                     + (0.05 if abs(mv.value) >= policy.BIG_STOCK_PCT else 0))
            c = _cand(sheet, f"post-unusual-{key.lower()}", Archetype.UNUSUAL_ACTIVITY, score,
                      [mv.fact_id, vol.fact_id if vol else None, ev.fact_id if ev else None,
                       sig.fact_id if sig else None],
                      {HeroVisual.SIGNAL_STACK_CHART: _stack_chart(sheet, f"RADAR_EVENT:{top_radar}"),
                       HeroVisual.DEPTH_LOLLIPOP: _lollipop(sheet, [f.fact_id for f in radar_moves])},
                      pick_beats(sheet, ["NIFTY_CLOSE", ("SECTOR_CONTRAST", "SECTOR_LEADER", "FLOWS"),
                                         "RADAR_SWEEP"]),
                      lines, f"{top_radar}: several independent signals changed on one session.")
            out.append(c)

    # EVENT
    out.extend(_event_candidates(sheet, "post"))

    # THINGS TO KNOW - always possible, lowest score
    lead_sector = next((f for f in sheet.facts_of_kind("SECTOR_MOVE") if "leader" in f.claims), None)
    third = sheet.fact(f"radar.{_slug(top_radar)}.move") if top_radar else sheet.fact("mover.gainer")
    items = [_item(nifty, "NIFTY 50"),
             _item(lead_sector, f"{lead_sector.entity.upper()} · LEADER") if lead_sector else None,
             _item(third, f"{third.entity} · {'RADAR' if top_radar else 'TOP GAINER'}") if third else None]
    out.append(_things(sheet, "post-things", items,
                       [nifty.fact_id if nifty else None, lead_sector.fact_id if lead_sector else None,
                        third.fact_id if third else None],
                       f"{sheet.metadata.get('session_label', 'the')}'s session",
                       ["NIFTY_CLOSE", "SECTOR_LEADER", "TOP_GAINER", "RADAR_SWEEP"]))
    return out


def _slug(s):
    return "".join(ch if ch.isalnum() else "_" for ch in (s or "").upper()).strip("_")


def _top_radar(sheet):
    """The Radar story with the most going on: signal count, then volume, then |move|."""
    best = None
    for f in sheet.facts:
        if not (f.kind == "STOCK_MOVE" and f.fact_id.startswith("radar.")):
            continue
        k = _slug(f.entity)
        sig = sheet.fact(f"radar.{k}.signals")
        vol = sheet.fact(f"radar.{k}.volume")
        rank = (1 if sig else 0, vol.value if vol else 0, abs(f.value or 0), f.entity)
        if sheet.beat(f"RADAR_EVENT:{f.entity}") is None:
            continue
        if best is None or rank > best[0]:
            best = (rank, f.entity)
    return best[1] if best else None


def _opposite_radar(sheet, top):
    t = sheet.fact(f"radar.{_slug(top)}.move") if top else None
    if t is None:
        return None
    opp = [f for f in sheet.facts if f.kind == "STOCK_MOVE" and f.fact_id.startswith("radar.")
           and f.value is not None and (f.value < 0) != (t.value < 0)
           and sheet.beat(f"RADAR_EVENT:{f.entity}") is not None]
    return max(opp, key=lambda f: abs(f.value)).entity if opp else None


def _things(sheet, cid, items, fact_ids, span_label, beat_prefs, opener=None):
    items = [i for i in items if i][:3]
    if len(items) < 2:
        return None
    n = len(items)
    count = sheet.fact("briefing.items")
    if count is None or int(count.value) != n:
        sheet.facts = [f for f in sheet.facts if f.fact_id != "briefing.items"]
        sheet.facts.append(count_fact("briefing.items", n, "things",
                                      f"This briefing lists {n} validated items.",
                                      units=("things", "numbers", "facts")))
    lines = [opener or f"{spell(n).title()} things from {span_label}."]
    return _cand(sheet, cid, Archetype.THINGS_TO_KNOW, 0.30, list(fact_ids) + ["briefing.items"],
                 {HeroVisual.NUMBERED_LIST: _numbered(items)}, pick_beats(sheet, beat_prefs), lines,
                 f"No single fact dominates; a {n}-item briefing of the strongest validated facts.")


def _event_candidates(sheet, prefix):
    out = []
    ev = next((f for f in sheet.facts_of_kind("EVENT") if "high_impact" in f.claims), None)
    if ev is None:
        return out
    b = next((b for b in sheet.beats if ev.fact_id in b.fact_ids), None)
    payload = dict(b.payload) if b else {"tag": ev.entity, "title": ev.display, "when": ""}
    payload["weekday"] = sheet.session_date.strftime("%A").upper()
    nifty = sheet.fact("nifty.move")
    prev = sheet.fact("prev.day")
    if nifty is not None:
        payload["context"] = f"NIFTY 50 {nifty.display}" + (f" · {prev.entity.upper()}" if prev else "")
    title = payload.get("title", "")
    when = payload.get("when", "")
    lines = [f"{title} today, {when}." if when else f"{title} today.", f"{title} today."]
    out.append(_cand(sheet, f"{prefix}-event", Archetype.EVENT_LED, 0.66, [ev.fact_id],
                     {HeroVisual.EVENT_CALENDAR: payload},
                     pick_beats(sheet, ["PREV_NIFTY", "NIFTY_CLOSE", "CUE:", "GIFT_NIFTY"],
                                exclude=(b.beat_id,) if b else ()),
                     lines, f"A scheduled, named event today: {ev.display}."))
    return out


# --------------------------------------------------------------------------- PRE
def _pre(sheet):
    out = []
    cues = sorted(sheet.facts_of_kind("GLOBAL_CUE"), key=lambda f: -abs(f.value or 0))
    gift = sheet.fact("gift.gap")
    nifty = sheet.fact("nifty.move")
    prev = sheet.fact("prev.day")
    if cues and "big" in cues[0].claims:
        lead = cues[0]
        name = _short(lead.entity, sheet)
        when, note = sheet.metadata.get("cue_when", {}).get(lead.fact_id, ("overnight", "OVERNIGHT"))
        ctime = sheet.metadata.get("cue_time", {}).get(lead.fact_id)
        # a LIVE reading is written with its own time, in the past tense - never "is falling"
        base = (f"{name} was {_abs_display(lead.display)} "
                f"{'higher' if lead.value >= 0 else 'lower'} at {ctime}." if ctime else
                f"{name} {_verb(lead.value)} {_abs_display(lead.display)} {when}.")
        lines = []
        if gift:
            # PRE V1: with a known reading time GIFT is written in the past tense with that
            # time ("was 0.60% lower at 7:52 AM") - never "points to" an open.
            gt = sheet.metadata.get("gift_time", "")
            gword = "higher" if gift.value >= 0 else "lower"
            gnum = _abs_display(gift.display.split(' ')[0])
            lines.append(f"{base} GIFT Nifty was {gnum} {gword} at {gt}." if gt else
                         f"{base} GIFT Nifty is {gnum} {gword}.")
        lines.append(base)
        tile = lambda f: {"name": f.entity, "value": f.display, "positive": (f.value or 0) >= 0}
        board = {"lead": {**tile(lead), "note": note},
                 "others": [tile(f) for f in cues[1:4]],
                 "gift": {"name": "GIFT NIFTY", "value": f"{gift.value:+.2f}%",
                          "positive": gift.value >= 0,
                          "note": (f"AT {sheet.metadata['gift_time']} IST"
                                   if sheet.metadata.get("gift_time") else "VS NIFTY CLOSE")}
                 if gift else None}
        c = _cand(sheet, "pre-overnight", Archetype.OVERNIGHT_CUE,
                  0.62 + 0.10 * min(abs(lead.value) - 1, 2) / 2 + (0.05 if gift else 0),
                  [lead.fact_id, gift.fact_id if gift else None],
                  {HeroVisual.OVERNIGHT_BOARD: board,
                   HeroVisual.HEADLINE_NUMBER: {"label": lead.entity, "value": lead.display,
                                                "positive": lead.value >= 0, "series": None,
                                                "candles": None, "sub": note,
                                                "context": ""}},
                  pick_beats(sheet, [f"CUE:{_slug(c.entity)}" for c in cues[1:3]] +
                             ["GIFT_NIFTY", "PREV_NIFTY", "EVENT:"],
                             exclude=(f"CUE:{_slug(lead.entity)}",)),
                  lines, f"{lead.entity} moved {lead.display} overnight - the largest global cue.")
        out.append(c)

    out.extend(_event_candidates(sheet, "pre"))

    if nifty and "big" in nifty.claims and prev:
        close = sheet.fact("nifty.close")
        out.append(_cand(sheet, "pre-prev-big", Archetype.BIG_MOVE,
                         0.55 + 0.10 * min(abs(nifty.value) - 1, 2) / 2, ["nifty.move", "prev.day"],
                         {HeroVisual.HEADLINE_NUMBER: _headline(
                             sheet, f"NIFTY 50 · {prev.entity.upper()}", nifty, "PREV_NIFTY",
                             sub=f"Closed {close.display}" if close else "")},
                         pick_beats(sheet, ["CUE:", "GIFT_NIFTY", "EVENT:"]),
                         [f"Nifty {_verb(nifty.value)} {_abs_display(nifty.display)} on {prev.entity}."],
                         f"The previous session moved {nifty.display}."))

    ev = next(iter(sheet.facts_of_kind("EVENT")), None)
    items = [_item(cues[0]) if cues else None,
             _item(gift, "GIFT NIFTY") if gift else None,
             {"label": ev.entity, "value": ev.display.split(",")[0], "positive": None} if ev else None,
             _item(nifty, f"NIFTY 50 · {prev.entity.upper()}") if nifty and prev else None]
    out.append(_things(sheet, "pre-things", [i for i in items if i][:3],
                       [cues[0].fact_id if cues else None, gift.fact_id if gift else None,
                        ev.fact_id if ev else None, nifty.fact_id if nifty else None],
                       "", ["CUE:", "GIFT_NIFTY", "EVENT:", "PREV_NIFTY"],
                       opener=f"{spell(min(3, len([i for i in items if i]))).title()} things before the bell."))
    return out


# --------------------------------------------------------------------------- CUSTOM
def _custom(sheet):
    out = []
    sym = sheet.metadata.get("symbol")
    mv, ev = sheet.fact("stock.move"), sheet.fact("stock.event")
    vol, sig = sheet.fact("stock.volume"), sheet.fact("stock.signals")
    chart = sheet.metadata.get("chart_payload")
    if mv is None:
        return out
    if ev and (ev.claims & {"breakout", "breakdown"}) and vol and "unusual_volume" in vol.claims:
        word = ev.display.split(" (")[0].replace("Closed above its ", "").replace("Closed below its ", "")
        lines = [f"{sym} broke its {word} on {vol.value:.1f}× volume.",
                 f"{sym} {mv.display} on {vol.display}."]
        out.append(_cand(sheet, "custom-unusual", Archetype.UNUSUAL_ACTIVITY,
                         0.64 + (0.06 if sig else 0) + (0.05 if vol.value >= 3 else 0),
                         [mv.fact_id, ev.fact_id, vol.fact_id, sig.fact_id if sig else None],
                         {HeroVisual.SIGNAL_STACK_CHART: chart},
                         pick_beats(sheet, ["STOCK_TREND", "STOCK_VOLUME", "METRIC:"]), lines,
                         f"{sym}: {ev.display} on {vol.display}."))
    strong = [f for f in sheet.facts_of_kind("FUNDAMENTAL") if "strong_fundamental" in f.claims]
    if ev and "weak_technical" in ev.claims and strong:
        fd = strong[0]
        label = fd.statement.split(":")[0].replace(f"{sym} ", "")
        split = {"left": {"title": "PRICE TREND", "name": ev.display.split(" (")[0],
                          "value": mv.display, "numeric": -1.0, "positive": False, "note": "Weaker"},
                 "right": {"title": label.upper(), "name": label, "value": fd.display,
                           "numeric": 1.0, "positive": True, "note": "Stronger"},
                 "vs_label": "VS", "qualitative": True}
        out.append(_cand(sheet, "custom-tech-vs-fund", Archetype.CONTRAST, 0.63,
                         [ev.fact_id, fd.fact_id, mv.fact_id], {HeroVisual.VERSUS_SPLIT: split},
                         pick_beats(sheet, ["STOCK_EVENT", "METRIC:", "METRIC:", "STOCK_VOLUME"]),
                         [f"{sym}: weak chart, strong fundamentals.",
                          f"{sym}: weak chart, strong numbers."],
                         f"{sym}'s price trend weakened while {label} is strong."))
    if "big" in mv.claims:
        out.append(_cand(sheet, "custom-big-move", Archetype.BIG_MOVE, 0.58, [mv.fact_id],
                         {HeroVisual.HEADLINE_NUMBER: _headline(sheet, sym, mv, "STOCK_TREND",
                                                                sub=sheet.fact("stock.close").display)},
                         pick_beats(sheet, ["STOCK_VOLUME", "STOCK_EVENT", "METRIC:"]),
                         [f"{sym} {_verb(mv.value)} {_abs_display(mv.display)} in one session."],
                         f"{sym} moved {mv.display}."))
    if "quiet" in mv.claims and ev and ev.claims & {"cross_below", "cross_above"}:
        w = "".join(ch for ch in ev.display if ch.isdigit())[:2]
        verb = "slipped below" if "cross_below" in ev.claims else "crossed above"
        out.append(_cand(sheet, "custom-quiet", Archetype.QUIET_MARKET_HIDDEN_ACTION, 0.55,
                         [mv.fact_id, ev.fact_id], {HeroVisual.SIGNAL_STACK_CHART: chart},
                         pick_beats(sheet, ["STOCK_TREND", "STOCK_VOLUME", "METRIC:"]),
                         [f"{sym} moved just {mv.display}. It {verb} its {w}-day average."],
                         f"{sym} barely moved, but crossed its {w}-day average."))
    items = [_item(mv, sym), _item(ev, "PRICE TREND") if ev else None,
             _item(vol, "VOLUME") if vol else None,
             _item(strong[0], strong[0].statement.split(":")[0].replace(f'{sym} ', '').upper())
             if strong else None]
    items = [i for i in items if i][:3]
    out.append(_things(sheet, "custom-things", items,
                       [mv.fact_id, ev.fact_id if ev else None, vol.fact_id if vol else None],
                       "", ["STOCK_TREND", "STOCK_EVENT", "STOCK_VOLUME", "METRIC:"],
                       opener=f"{spell(len(items)).title()} things about {sym} today."))
    return out


# --------------------------------------------------------------------------- entry point
def build_candidates(sheet: HookFactSheet) -> list:
    """3-5 approved candidates (fewer only when the day supports fewer), strongest first."""
    builder = {HookMode.POST_MARKET: _post, HookMode.PRE_MARKET: _pre,
               HookMode.CUSTOM_SINGLE_STOCK: _custom}[sheet.mode]
    found = [c for c in builder(sheet) if c is not None]
    if sheet.mode is HookMode.POST_MARKET:
        found = major_index_priority(sheet, found)
    found.sort(key=lambda c: (-c.score, ORDER[c.archetype], c.candidate_id))
    seen, out = set(), []
    for c in found:
        if c.candidate_id not in seen:
            seen.add(c.candidate_id)
            out.append(c)
    return out[:policy.MAX_CANDIDATES]


def exceptional_stock_event(sheet: HookFactSheet, symbol: str, nifty_pct: float) -> tuple:
    """(is_exceptional, record) - the explicit rarity rule in hooks/policy.py, on validated
    Radar facts only. Deterministic; no model involved."""
    mv = sheet.fact(f"radar.{_slug(symbol)}.move")
    vol = sheet.fact(f"radar.{_slug(symbol)}.volume")
    move = abs(mv.value) if mv is not None and mv.value is not None else None
    rvol = vol.value if vol is not None else None
    tests = {
        f"|move| >= {policy.EXCEPTIONAL_STOCK_PCT:g}%": move is not None and move >= policy.EXCEPTIONAL_STOCK_PCT,
        f"relative volume >= {policy.EXCEPTIONAL_RVOL:g}x": rvol is not None and rvol >= policy.EXCEPTIONAL_RVOL,
        f"|move| >= {policy.EXCEPTIONAL_INDEX_MULTIPLE:g}x |Nifty|":
            move is not None and move >= policy.EXCEPTIONAL_INDEX_MULTIPLE * abs(nifty_pct),
    }
    return all(tests.values()), {"symbol": symbol, "abs_move_pct": move, "relative_volume": rvol,
                                 "tests": tests}


def major_index_priority(sheet: HookFactSheet, found: list) -> list:
    """POST freeze: on a major index day BIG_MOVE outranks a normal single-stock
    UNUSUAL_ACTIVITY hook. The decision is recorded on `sheet.metadata["hook_priority"]`."""
    nifty = sheet.fact("nifty.move")
    rec = {"rule": (f"|Nifty| >= {policy.MAJOR_INDEX_PCT:g}%: BIG_MOVE outranks UNUSUAL_ACTIVITY "
                    f"unless the stock event is exceptional (|move| >= "
                    f"{policy.EXCEPTIONAL_STOCK_PCT:g}%, relative volume >= "
                    f"{policy.EXCEPTIONAL_RVOL:g}x, |move| >= "
                    f"{policy.EXCEPTIONAL_INDEX_MULTIPLE:g}x |Nifty|)"),
           "nifty_pct": None if nifty is None else nifty.value, "applies": False,
           "suppressed": [], "exceptions": []}
    sheet.metadata["hook_priority"] = rec
    if nifty is None or nifty.value is None or abs(nifty.value) < policy.MAJOR_INDEX_PCT:
        rec["note"] = "not a major index day"
        return found
    if not any(c.archetype is Archetype.BIG_MOVE for c in found):
        rec["note"] = "major index day but no BIG_MOVE candidate passed validation"
        return found
    rec["applies"] = True
    kept = []
    for c in found:
        if c.archetype is Archetype.UNUSUAL_ACTIVITY:
            mv = next((sheet.fact(f) for f in c.fact_ids
                       if sheet.fact(f) is not None and sheet.fact(f).kind == "STOCK_MOVE"), None)
            ok, why = exceptional_stock_event(sheet, mv.entity if mv else "", nifty.value)
            (rec["exceptions"] if ok else rec["suppressed"]).append(
                {"candidate_id": c.candidate_id, "score": c.score, **why})
            if not ok:
                continue
        kept.append(c)
    return kept


def emergency_candidate(sheet: HookFactSheet) -> HookCandidate:
    """Used only when no candidate survives: the Short still opens, with the session day and
    whatever sections it has - never a failure."""
    day = sheet.fact("session.day")
    label = day.entity if day else "Today"
    items = [_item(f) for f in sheet.facts
             if f.kind in ("INDEX_MOVE", "GLOBAL_CUE", "STOCK_MOVE", "SECTOR_MOVE")][:3]
    payload = _numbered(items) or {"items": []}
    return HookCandidate(candidate_id="emergency", archetype=Archetype.THINGS_TO_KNOW, score=0.0,
                         fact_ids=("session.day",), heroes={HeroVisual.NUMBERED_LIST: payload},
                         default_hero=HeroVisual.NUMBERED_LIST,
                         default_beats=tuple(b.beat_id for b in sheet.beats[:3]),
                         curiosity_line=f"Your {label} market briefing.",
                         summary_line=summary_line(sheet, Archetype.THINGS_TO_KNOW),
                         rationale="No candidate passed validation.",
                         hero_strings={HeroVisual.NUMBERED_LIST: strings_of(payload)})


__all__ = ["major_index_priority", "exceptional_stock_event", "build_candidates", "emergency_candidate", "summary_line", "pick_beats"]
