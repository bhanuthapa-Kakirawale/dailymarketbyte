"""POST freeze - teaser-beat diversity (POST mode only; PRE and CUSTOM are unchanged).

Rules, in priority order:
  1. At most one teaser beat per entity. A beat's entities are the entities of the facts it
     cites plus every name it draws (a Radar sweep draws all its stocks), so RADAR_EVENT:X,
     VOLUME_SPIKE:X, TOP_LOSER (when X is the top loser) and a sweep that lights X all collide.
  2. Then prefer different information families, in the POST order
     index -> sector / flow -> stock (Radar or mover) -> the rest.
  3. Never force a weak or repeated beat: fewer beats are allowed rather than a duplicate.
"""
from __future__ import annotations

from .models import BeatKind, HookFactSheet, HookMode

BEAT_FAMILY = {
    BeatKind.LINE: "INDEX",
    BeatKind.SECTOR_PAIR: "BREADTH", BeatKind.SECTOR_TILE: "BREADTH", BeatKind.FLOWS: "BREADTH",
    BeatKind.MOVER_BAR: "STOCK", BeatKind.BREAKOUT: "STOCK", BeatKind.VOLUME: "STOCK",
    BeatKind.RADAR: "STOCK",
    BeatKind.CUE: "GLOBAL", BeatKind.EVENT: "EVENT", BeatKind.METRIC: "METRIC",
}
POST_FAMILY_ORDER = ("INDEX", "BREADTH", "STOCK", "GLOBAL", "EVENT", "METRIC")


def applies(sheet: HookFactSheet) -> bool:
    return sheet.mode is HookMode.POST_MARKET


def family(beat) -> str:
    return BEAT_FAMILY.get(beat.kind, "OTHER")


def entities(sheet: HookFactSheet, beat) -> frozenset:
    out = set()
    for fid in beat.fact_ids:
        f = sheet.fact(fid)
        if f is not None and f.kind != "COUNT" and f.entity:
            out.add(f.entity)
    p = beat.payload or {}
    for key in ("symbol", "name"):
        if isinstance(p.get(key), str) and p[key]:
            out.add(p[key])
    out.update(n for n in (p.get("names") or []) if isinstance(n, str) and n)
    return frozenset(e.strip().upper() for e in out)


def entity_clashes(sheet: HookFactSheet, beat_ids) -> list:
    """Pairs of beats (in order) that show the same entity."""
    seen, out = {}, []
    for bid in beat_ids:
        b = sheet.beat(bid)
        if b is None:
            continue
        for e in entities(sheet, b):
            if e in seen and seen[e] != bid:
                out.append((seen[e], bid, e))
            seen.setdefault(e, bid)
    return out


def family_rank(beat) -> int:
    fam = family(beat)
    return POST_FAMILY_ORDER.index(fam) if fam in POST_FAMILY_ORDER else len(POST_FAMILY_ORDER)


def pick_diverse(sheet: HookFactSheet, preferred: list, exclude=(), max_beats=3,
                 min_beats=2) -> tuple:
    chosen, ents, fams = [], set(), set()

    def ok(b, strict_family):
        return (b.beat_id not in exclude and b.beat_id not in [c.beat_id for c in chosen]
                and not (entities(sheet, b) & ents)
                and not (strict_family and family(b) in fams))

    def take(b):
        chosen.append(b)
        ents.update(entities(sheet, b))
        fams.add(family(b))

    for want in preferred:
        if want is None or len(chosen) >= max_beats:
            continue
        for alt in (want if isinstance(want, tuple) else (want,)):
            if alt is None:
                continue
            hit = next((b for b in sheet.beats
                        if (b.beat_id == alt or (alt.endswith(":") and b.beat_id.startswith(alt)))
                        and ok(b, True)), None)
            if hit is not None:
                take(hit)
                break
    # Top up to the minimum from families not yet shown, in the POST family order...
    for b in sorted(sheet.beats, key=family_rank):
        if len(chosen) >= min_beats:
            break
        if ok(b, True):
            take(b)
    # ...then from any family, still never repeating an entity.
    for b in sorted(sheet.beats, key=family_rank):
        if len(chosen) >= min_beats:
            break
        if ok(b, False):
            take(b)
    chosen.sort(key=family_rank)          # play order: index -> sector/flow -> stock
    return tuple(b.beat_id for b in chosen)


def max_diverse(sheet: HookFactSheet, max_beats=3) -> int:
    """How many entity-distinct beats this sheet can offer at all (greedy, family order)."""
    return len(pick_diverse(sheet, [], max_beats=max_beats, min_beats=max_beats))


__all__ = ["BEAT_FAMILY", "POST_FAMILY_ORDER", "applies", "family", "entities",
           "entity_clashes", "pick_diverse", "max_diverse"]
