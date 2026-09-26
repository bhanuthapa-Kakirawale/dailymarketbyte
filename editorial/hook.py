"""Deterministic hook selection: the one fact that earns the first three seconds.

The opening used to be a title card - branding, date, "Indian Stock Market Recap" - which
answers a question nobody scrolling asked. The hook answers the only one they did: *why keep
watching?*

Selection is rule-based and reproducible. Every candidate scores itself from canonical facts
or intelligence insights, candidates sort by score with explicit tie-breaks, and the first
one that passes content safety wins. No model chooses, so the same report and snapshot
always open the same way.

Nothing here invents emphasis. A candidate's score comes from how unusual the underlying
reading already was - an intelligence `selection_score`, or a magnitude threshold - so the
hook is the most *informative* fact available, never the most dramatic phrasing of a dull one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core import Metric, ReportType

from .config import HARD_PRIMARY_WORDS, HARD_SECONDARY_WORDS

# Magnitudes at which a plain reading is interesting enough to open on, with no historical
# context needed. Deliberately high: an ordinary day should fall through to the fallback.
BIG_INDEX_MOVE_PCT = 1.0
BIG_FLOW_CRORE = 2500.0
BIG_SECTOR_MOVE_PCT = 2.0
BIG_MOVER_PCT = 5.0


@dataclass
class HookCandidate:
    """One possible opening, and why it scored what it did."""
    candidate_id: str
    primary_text: str
    primary_value: str = ""
    secondary_text: str = ""
    primary_numeric: float | None = None
    primary_positive: bool | None = None
    score: float = 0.0
    order: int = 99                       # stable tie-break when scores match exactly
    source_fact_ids: list = field(default_factory=list)
    source_insight_ids: list = field(default_factory=list)

    def within_budget(self) -> bool:
        primary = len(self.primary_text.split()) + len(self.primary_value.split())
        return (primary <= HARD_PRIMARY_WORDS
                and len(self.secondary_text.split()) <= HARD_SECONDARY_WORDS)

    def texts(self) -> list:
        return [t for t in (self.primary_text, self.primary_value, self.secondary_text) if t]


def _fact(report, metric, instrument=None):
    for fact in report.facts_for(metric):
        if fact.value is None:
            continue
        if instrument is None or fact.instrument == instrument:
            return fact
    return None


def _insight(snapshot, insight_id):
    if snapshot is None:
        return None
    return next((i for i in snapshot.insights
                 if i.insight_id == insight_id and i.is_displayable), None)


def _crore(value: float) -> str:
    return f"Rs {abs(value):,.0f} CR"


def _temporal(report, today_text: str, previous_session_text: str) -> str:
    """Anchors a hook's secondary line to what the report actually describes, never to
    wall-clock time. `report.report_type` is the only authority: PRE_MARKET recaps an
    already-completed previous session, so a completed-session fact must never be worded as
    "today's" - that claim only becomes true once POST_MARKET reports the current session.

    "Previous session" rather than "yesterday" is used deliberately: weekends and market
    holidays mean the previous RECORDED session is not always the immediately preceding
    calendar day, and this must stay deterministic from canonical metadata alone.
    """
    return today_text if report.report_type is ReportType.POST_MARKET else previous_session_text


# --------------------------------------------------------------------- candidates
def candidates(report, snapshot) -> list:
    """Every hook the day's data supports, strongest first.

    Ordering is by score, then by the fixed `order` of the candidate family, then by id -
    total and stable, so the choice never depends on dictionary iteration or timing.
    """
    found = []

    # 1. An index move that history says was unusual. The strongest possible open: a number
    #    plus the reason it matters, both already validated.
    move = _insight(snapshot, "index-move-20")
    index_fact = _fact(report, Metric.INDEX_CHANGE_PCT, "NIFTY 50")
    if move and index_fact is not None:
        larger = move.metadata.get("larger_than_sessions", 0)
        if larger >= 15:
            pct = float(index_fact.value)
            found.append(HookCandidate(
                candidate_id="hook-index-unusual",
                primary_text="NIFTY", primary_value=f"{pct:+.2f}%",
                secondary_text=f"Bigger than {larger} of the last 20 sessions",
                primary_numeric=pct, primary_positive=pct >= 0,
                score=0.90 + 0.09 * (larger / 20), order=1,
                source_fact_ids=[index_fact.fact_id], source_insight_ids=[move.insight_id]))

    # 2. Volatility sitting at an extreme of its own recent range.
    vix = _insight(snapshot, "vix-rank-20")
    vix_fact = _fact(report, Metric.VOLATILITY_INDEX, "INDIA VIX")
    if vix and vix_fact is not None:
        higher = vix.metadata.get("higher_than_sessions", 0)
        if higher >= 17 or higher <= 3:
            value = float(vix_fact.value)
            direction = "highest" if higher >= 17 else "lowest"
            found.append(HookCandidate(
                candidate_id="hook-vix-extreme",
                primary_text="INDIA VIX", primary_value=f"{value:.1f}",
                secondary_text=f"Among its {direction} in 20 sessions",
                primary_numeric=value, primary_positive=None,
                score=0.85, order=2,
                source_fact_ids=[vix_fact.fact_id], source_insight_ids=[vix.insight_id]))

    # 3. A persistent institutional streak - the kind of thing a daily viewer tracks.
    for subject, metric in (("FII", Metric.FII_NET_CASH), ("DII", Metric.DII_NET_CASH)):
        streak = _insight(snapshot, f"{subject.lower()}-flow-streak")
        flow_fact = _fact(report, metric)
        if not streak or flow_fact is None:
            continue
        sessions = streak.metadata.get("streak_sessions", 0)
        if sessions < 3:
            continue
        value = float(flow_fact.value)
        word = "SOLD" if value < 0 else "BOUGHT"
        found.append(HookCandidate(
            candidate_id=f"hook-{subject.lower()}-streak",
            primary_text=f"{subject}s {word}", primary_value=_crore(value),
            secondary_text=f"{sessions} consecutive recorded sessions",
            primary_numeric=value, primary_positive=value >= 0,
            score=0.70 + 0.04 * min(sessions, 6), order=3,
            source_fact_ids=[flow_fact.fact_id], source_insight_ids=[streak.insight_id]))

    # 4. A flow large enough to stand on its own without any history.
    fii_fact = _fact(report, Metric.FII_NET_CASH)
    if fii_fact is not None and abs(float(fii_fact.value)) >= BIG_FLOW_CRORE:
        value = float(fii_fact.value)
        found.append(HookCandidate(
            candidate_id="hook-fii-large",
            primary_text=f"FIIs {'SOLD' if value < 0 else 'BOUGHT'}",
            primary_value=_crore(value),
            secondary_text="Net, in the cash market",
            primary_numeric=value, primary_positive=value >= 0,
            score=0.62, order=4, source_fact_ids=[fii_fact.fact_id]))

    # 5. An index move large in absolute terms, when no history exists to qualify it.
    if index_fact is not None and abs(float(index_fact.value)) >= BIG_INDEX_MOVE_PCT:
        pct = float(index_fact.value)
        found.append(HookCandidate(
            candidate_id="hook-index-large",
            primary_text="NIFTY", primary_value=f"{pct:+.2f}%",
            secondary_text="A notable one-day move",
            primary_numeric=pct, primary_positive=pct >= 0,
            score=0.58, order=5, source_fact_ids=[index_fact.fact_id]))

    # 6. An exceptional sector move.
    sectors = [f for f in report.facts_for(Metric.SECTOR_CHANGE_PCT) if f.value is not None]
    if sectors:
        strongest = max(sectors, key=lambda f: (abs(float(f.value)), f.instrument))
        pct = float(strongest.value)
        if abs(pct) >= BIG_SECTOR_MOVE_PCT:
            found.append(HookCandidate(
                candidate_id="hook-sector-move",
                primary_text=f"NIFTY {strongest.instrument.upper()}",
                primary_value=f"{pct:+.2f}%",
                secondary_text=_temporal(report, "Sharpest sector move today",
                                         "Sharpest sector move, previous session"),
                primary_numeric=pct, primary_positive=pct >= 0,
                score=0.52, order=6, source_fact_ids=[strongest.fact_id]))

    # 7. A single stock that moved far enough to be the story.
    movers = [f for f in report.facts_for(Metric.STOCK_CHANGE_PCT) if f.value is not None]
    if movers:
        biggest = max(movers, key=lambda f: (abs(float(f.value)), f.instrument))
        pct = float(biggest.value)
        if abs(pct) >= BIG_MOVER_PCT:
            found.append(HookCandidate(
                candidate_id="hook-mover",
                primary_text=biggest.instrument, primary_value=f"{pct:+.2f}%",
                secondary_text=_temporal(report, "Biggest tracked move today",
                                         "Biggest tracked move, previous session"),
                primary_numeric=pct, primary_positive=pct >= 0,
                score=0.48, order=7, source_fact_ids=[biggest.fact_id]))

    # 8. Any other historical observation that scored highly enough to be worth opening on.
    if snapshot is not None:
        for insight in snapshot.selected(limit=2):
            if insight.selection_score < 0.75 or insight.insight_id in {
                    "index-move-20", "vix-rank-20"}:
                continue
            found.append(HookCandidate(
                candidate_id=f"hook-insight-{insight.insight_id}",
                primary_text=insight.subject.upper()[:18],
                secondary_text=insight.statement,
                score=0.40 + 0.05 * insight.selection_score, order=8,
                source_insight_ids=[insight.insight_id],
                source_fact_ids=list(insight.supporting_fact_ids[:1])))

    found.sort(key=lambda c: (-round(c.score, 6), c.order, c.candidate_id))
    return found


def fallback(report) -> HookCandidate:
    """The neutral open used when nothing stood out - still a fact, never filler."""
    index_fact = _fact(report, Metric.INDEX_CHANGE_PCT, "NIFTY 50")
    if index_fact is not None:
        pct = float(index_fact.value)
        return HookCandidate(
            candidate_id="hook-fallback-index",
            primary_text="NIFTY", primary_value=f"{pct:+.2f}%",
            secondary_text="How the session closed",
            primary_numeric=pct, primary_positive=pct >= 0,
            score=0.0, order=90, source_fact_ids=[index_fact.fact_id])
    return HookCandidate(candidate_id="hook-fallback-plain", primary_text="MARKET RECAP",
                         secondary_text=_temporal(report, "Today's validated session summary",
                                                  "Previous session's validated summary"),
                         score=0.0, order=99)


def select(report, snapshot, is_safe=None, admit=None) -> HookCandidate:
    """The strongest candidate that passes content safety, else the neutral fallback.

    `is_safe` receives each piece of hook text; safety is never bypassed, so an unsafe
    candidate is skipped in favour of the next one rather than being published or rewritten.
    `admit` (the publication gate, see editorial.planner) receives each candidate; a candidate
    it refuses - e.g. a single named stock under PUBLIC_UNREGISTERED - is skipped the same way.
    """
    if is_safe is None:
        def is_safe(_text):
            return True

    for candidate in candidates(report, snapshot):
        if not candidate.within_budget():
            continue
        if admit is not None and not admit(candidate):
            continue
        if all(is_safe(text) for text in candidate.texts()):
            return candidate

    neutral = fallback(report)
    if all(is_safe(text) for text in neutral.texts()):
        return neutral
    return HookCandidate(candidate_id="hook-fallback-plain", primary_text="MARKET RECAP",
                         secondary_text="", score=0.0, order=99)


__all__ = ["select", "candidates", "fallback", "HookCandidate", "BIG_INDEX_MOVE_PCT",
           "BIG_FLOW_CRORE", "BIG_SECTOR_MOVE_PCT", "BIG_MOVER_PCT"]
