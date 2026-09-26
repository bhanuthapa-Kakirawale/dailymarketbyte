"""UNDER THE SURFACE: which Market Structure observations earn a scene - deterministic rules on
the snapshot, one primary idea per scene, nothing when nothing meaningful happened.

Candidates, in priority order (at most `MAX_INSIGHTS` are kept):

    BREADTH     Nifty moved one way while most of the universe moved the other (divergence),
                or at least BREADTH_EXTREME_SHARE of the covered universe moved the same way
    UNUSUAL_VOLUME
                at least UNUSUAL_MIN stocks traded at >= 2x their usual volume; sector rows +
                an exact concentration line when one sector holds a real share of them
    RANGE       at least RANGE_MIN stocks closed outside their 20-day range on one side

Every string is a fixed template over validated counts. No stock is ever named here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .aggregator import PARTIAL, PUBLISHABLE, exact_share
from .sectors import UNCLASSIFIED

MAX_INSIGHTS = 2
UNUSUAL_MIN = 5
RANGE_MIN = 10
BREADTH_EXTREME_SHARE = 0.80
DIVERGENCE_MIN_NIFTY_PCT = 0.20
CONCENTRATION_MIN_SHARE = 0.30
CONCENTRATION_MIN_COUNT = 3
SECTOR_ROWS = 4


@dataclass
class StructureInsight:
    kind: str
    headline: str
    hero_value: str
    hero_label: str
    definition: str
    rows: list = field(default_factory=list)       # [{"name", "value", "n"}] sums to numerator
    takeaway: str = ""
    split: list = field(default_factory=list)      # [{"label", "value"}] e.g. adv vs dec
    universe_label: str = ""
    denominator_text: str = ""
    coverage_pct: float = 100.0
    coverage_status: str = PUBLISHABLE
    metric_keys: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def public_strings(self) -> list:
        out = [self.headline, self.hero_value, self.hero_label, self.definition, self.takeaway]
        out += [f"{r['name']} {r['value']}" for r in self.rows]
        out += [f"{s['label']} {s['value']}" for s in self.split]
        return [s for s in out if s]


def _usable(m) -> bool:
    return m is not None and m.status in (PUBLISHABLE, PARTIAL)


def _coverage_note(m) -> str:
    return "" if m.status == PUBLISHABLE else f" ({m.denominator} of {m.universe_size} covered)"


def _rows(m) -> list:
    """Top sectors, then everything else as 'Others' - rows always sum to the numerator."""
    named = [(s, n) for s, n in m.by_sector.items() if s != UNCLASSIFIED]
    named.sort(key=lambda r: (-r[1], r[0]))
    top = named[:SECTOR_ROWS] if len(named) > SECTOR_ROWS + 1 else named[:SECTOR_ROWS + 1]
    shown = sum(n for _, n in top)
    rows = [{"name": s, "value": str(n), "n": n} for s, n in top]
    rest = m.numerator - shown
    if rest > 0:
        rows.append({"name": "Others", "value": str(rest), "n": rest})
    assert sum(r["n"] for r in rows) == m.numerator
    return rows


def _unusual(snap) -> StructureInsight | None:
    m = snap.metric("UNUSUAL_VOLUME")
    if not _usable(m) or m.numerator < UNUSUAL_MIN:
        return None
    U = snap.universe_label
    top = m.top_sector()
    takeaway, headline = "", f"Where unusual volume showed up in the {U}"
    if top and top[1] >= CONCENTRATION_MIN_COUNT and top[1] / m.numerator >= CONCENTRATION_MIN_SHARE:
        share = exact_share(top[1], m.numerator)
        takeaway = (f"{top[0]} accounted for {share} of them." if share and share != "all"
                    else f"{top[0]} accounted for {top[1]} of the {m.numerator}.")
        headline = f"Unusual volume was concentrated in {top[0]}"
    split = []
    sub = next((s for s in (snap.subsets or {}).values() if s.get("status") == PUBLISHABLE), None)
    if sub:
        split = [{"label": sub["subset_label"], "value": sub["subset"]["denominator_text"]},
                 {"label": sub["rest_label"].upper(), "value": sub["rest"]["denominator_text"]}]
    return StructureInsight(
        kind="UNUSUAL_VOLUME", headline=headline, hero_value=m.denominator_text.split(" (")[0]
        if m.status == PUBLISHABLE else f"{m.numerator} / {m.denominator}",
        hero_label=f"{U} STOCKS WITH UNUSUAL VOLUME" + _coverage_note(m).upper(),
        definition="Volume at least 2x their own 20-session average",
        rows=_rows(m), takeaway=takeaway, split=split, universe_label=U,
        denominator_text=m.denominator_text, coverage_pct=m.coverage_pct,
        coverage_status=m.status, metric_keys=["UNUSUAL_VOLUME"],
        reason=f"{m.numerator} unusual-volume observations (>= {UNUSUAL_MIN}), {m.denominator_text}")


def _breadth(snap, nifty_pct) -> StructureInsight | None:
    adv, dec = snap.metric("ADVANCES"), snap.metric("DECLINES")
    if not (_usable(adv) and _usable(dec)) or adv.denominator == 0:
        return None
    U, cov = snap.universe_label, adv.denominator
    unchanged = cov - adv.numerator - dec.numerator
    split = [{"label": "HIGHER", "value": str(adv.numerator)},
             {"label": "LOWER", "value": str(dec.numerator)}]
    if unchanged:
        split.append({"label": "UNCHANGED", "value": str(unchanged)})
    base = dict(kind="BREADTH", definition="Close vs the previous session's close",
                split=split, universe_label=U, coverage_pct=adv.coverage_pct,
                coverage_status=adv.status, metric_keys=["ADVANCES", "DECLINES"])
    of = f"of {cov}" if adv.status == PUBLISHABLE else f"of {cov} covered"
    if nifty_pct is not None and abs(nifty_pct) >= DIVERGENCE_MIN_NIFTY_PCT:
        if nifty_pct > 0 and dec.numerator > adv.numerator:
            return StructureInsight(
                headline=f"Nifty 50 rose {abs(nifty_pct):.2f}%, but most {U} stocks fell",
                hero_value=f"{dec.numerator} / {cov}", hero_label=f"{U} STOCKS CLOSED LOWER",
                takeaway=f"{adv.numerator} {of} closed higher.",
                denominator_text=f"{dec.numerator} {of}",
                reason=f"divergence: Nifty {nifty_pct:+.2f}% vs {dec.numerator} decliners",
                **base)
        if nifty_pct < 0 and adv.numerator > dec.numerator:
            return StructureInsight(
                headline=f"Nifty 50 fell {abs(nifty_pct):.2f}%, but most {U} stocks rose",
                hero_value=f"{adv.numerator} / {cov}", hero_label=f"{U} STOCKS CLOSED HIGHER",
                takeaway=f"{dec.numerator} {of} closed lower.",
                denominator_text=f"{adv.numerator} {of}",
                reason=f"divergence: Nifty {nifty_pct:+.2f}% vs {adv.numerator} advancers",
                **base)
    for m, word, label in ((dec, "fell", "LOWER"), (adv, "rose", "HIGHER")):
        if m.numerator / cov >= BREADTH_EXTREME_SHARE:
            return StructureInsight(
                headline=f"A broad move: {m.numerator} {of} {U} stocks {word}",
                hero_value=f"{m.numerator} / {cov}", hero_label=f"{U} STOCKS CLOSED {label}",
                takeaway="", denominator_text=f"{m.numerator} {of}",
                reason=f"breadth extreme: {m.numerator}/{cov} {word}", **base)
    return None


def _range(snap) -> StructureInsight | None:
    up, down = snap.metric("RANGE_UP"), snap.metric("RANGE_DOWN")
    if not (_usable(up) and _usable(down)):
        return None
    lead, other, side, oside = ((up, down, "above", "below") if up.numerator >= down.numerator
                                else (down, up, "below", "above"))
    if lead.numerator < RANGE_MIN:
        return None
    U = snap.universe_label
    top = lead.top_sector()
    takeaway = f"{other.numerator} closed {oside} their 20-day range."
    if top and top[1] >= CONCENTRATION_MIN_COUNT and top[1] / lead.numerator >= CONCENTRATION_MIN_SHARE:
        takeaway += f" {top[0]}: {top[1]} of the {lead.numerator}."
    return StructureInsight(
        kind="RANGE", headline=f"{lead.numerator} {U} stocks closed {side} their 20-day range",
        hero_value=lead.denominator_text.split(" (")[0] if lead.status == PUBLISHABLE
        else f"{lead.numerator} / {lead.denominator}",
        hero_label=f"{U} STOCKS · CLOSED {side.upper()} 20-DAY RANGE",
        definition=f"Close {side} the {'high' if side == 'above' else 'low'} of the prior 20 sessions",
        rows=_rows(lead), takeaway=takeaway, universe_label=U,
        denominator_text=lead.denominator_text, coverage_pct=lead.coverage_pct,
        coverage_status=lead.status, metric_keys=[lead.key, other.key],
        reason=f"{lead.numerator} range events {side} (>= {RANGE_MIN})")


def select_insights(snapshot, nifty_pct: float | None = None, limit: int = MAX_INSIGHTS) -> tuple:
    """(insights, reasons) - the chosen observations in play order, and why each candidate was
    kept or not. An empty list means the section is omitted - never padded."""
    if snapshot is None:
        return [], {"MARKET_STRUCTURE": "omitted: no Market Structure snapshot for the session"}
    cands = [("BREADTH", _breadth(snapshot, nifty_pct)), ("UNUSUAL_VOLUME", _unusual(snapshot)),
             ("RANGE", _range(snapshot))]
    reasons, out = {}, []
    for key, ins in cands:
        if ins is None:
            reasons[key] = "omitted: below its threshold or coverage suppressed"
        elif len(out) >= limit:
            reasons[key] = f"omitted: qualified ({ins.reason}) but the section shows at most {limit}"
        else:
            out.append(ins)
            reasons[key] = "included: " + ins.reason
    return out, reasons


__all__ = ["StructureInsight", "select_insights", "MAX_INSIGHTS", "UNUSUAL_MIN", "RANGE_MIN"]
