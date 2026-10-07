"""Editorial Planner V3: one deterministic arbiter for the stories competing for a Short's slots.

    POST planner (presentation.post_plan)  ┐  build the section models exactly as before, then
    PRE planner  (presentation.pre_plan)   ┘  describe each one as an EditorialCandidate
                                                        │
                                                        ▼
                                  arbitrate(candidates, POST_POLICY | PRE_POLICY)
                                                        │
                                                        ▼
                     EditorialDecision: the selected sections in play order + a full trace

This is not a second planner: the planners still decide what each section SAYS and whether it
passes its own value test (a candidate that fails it is tier 0 and is never shown, exactly as
before V3). The arbiter only decides which of the qualifying stories earn the edition's limited
slots, in what order, and records why every other one did not.

Every rule is explicit and every component is written to the trace - there is no single opaque
score. Pure: no I/O, no clock, no network, no model; the same candidates always give the same
decision.

    tier            0 NOT MATERIAL (fails the section's existing value test)
                    1 ROUTINE      2 NOTABLE      3 MATERIAL
    effective tier  tier - 1 when the data is PARTIAL or STALE
    priority        (effective tier, edition relevance, tension with the headline,
                     explains the headline, earlier in the edition's narrative order,
                     candidate id)

Greedy, in priority order, each optional candidate is tested in this order:
    PUBLICATION_BLOCKED / DATA_QUALITY -> NOT_MATERIAL -> TEMPORAL_EXCLUDED
    -> SUPPRESSED_DUPLICATE -> DROPPED_DIVERSITY -> BELOW_EDITION_THRESHOLD -> DROPPED_SLOTS -> DROPPED_BUDGET -> SELECTED
and when nothing reaches the edition threshold the single best ROUTINE story is kept
(QUIET_FLOOR) so the Short still explains something - a further one only while the Short would
otherwise fall below the platform minimum runtime; nothing else is added to fill time.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

ARBITER_VERSION = "editorial-v3.0"

# --------------------------------------------------------------------------- vocabulary
REQUIRED, OPTIONAL = "REQUIRED", "OPTIONAL"
NOT_MATERIAL_T, ROUTINE, NOTABLE, MATERIAL = 0, 1, 2, 3
TIER_NAMES = {0: "NOT_MATERIAL", 1: "ROUTINE", 2: "NOTABLE", 3: "MATERIAL"}

OK, PARTIAL, STALE = "OK", "PARTIAL", "STALE"
SOURCE_FAILURE, EMPTY, PUBLICATION_BLOCKED = "SOURCE_FAILURE", "EMPTY", "PUBLICATION_BLOCKED"
DEMOTING_QUALITY = frozenset({PARTIAL, STALE})
EXCLUDING_QUALITY = frozenset({SOURCE_FAILURE, EMPTY, PUBLICATION_BLOCKED})

# decisions
SELECTED = "SELECTED"
QUIET_FLOOR = "QUIET_FLOOR"
NOT_MATERIAL = "NOT_MATERIAL"
DATA_QUALITY = "DATA_QUALITY"
BLOCKED = "PUBLICATION_BLOCKED"
TEMPORAL_EXCLUDED = "TEMPORAL_EXCLUDED"
SUPPRESSED_DUPLICATE = "SUPPRESSED_DUPLICATE"
DROPPED_DIVERSITY = "DROPPED_DIVERSITY"
BELOW_THRESHOLD = "BELOW_EDITION_THRESHOLD"
DROPPED_SLOTS = "DROPPED_SLOTS"
DROPPED_BUDGET = "DROPPED_BUDGET"
MERGED_INTO = "MERGED_INTO"
SHOWN = frozenset({SELECTED, QUIET_FLOOR})

# families (what kind of story a candidate is - the anti-dominance unit)
INDEX, SECTORS, BREADTH, FLOWS, MOVERS = "INDEX", "SECTORS", "BREADTH", "FLOWS", "MOVERS"
GLOBAL, VOLATILITY, CALENDAR, IPO, EXCHANGE = "GLOBAL", "VOLATILITY", "CALENDAR", "IPO", "EXCHANGE"
STOCK_WATCH, STRUCTURE_ANCHOR = "STOCK_WATCH", "ANCHOR"

# India-session families: in PRE, these may only describe the previous completed session
INDIA_SESSION_FAMILIES = frozenset({INDEX, SECTORS, BREADTH, MOVERS, VOLATILITY, STOCK_WATCH})

# Report sector-index names -> Market Structure sector buckets, for REDUNDANCY ONLY. Explicit
# and conservative: only names that mean the same group of companies. Anything unmapped claims
# no overlap (both stories are kept) - never a guess. NSE's "Pharma" index is not its
# "Healthcare" industry bucket (that one also holds hospitals/diagnostics), so it is not mapped.
SECTOR_TOPIC = {"IT": "IT", "Nifty IT": "IT", "Metal": "Metals", "Nifty Metal": "Metals",
                "Auto": "Auto", "Nifty Auto": "Auto", "FMCG": "FMCG", "Nifty FMCG": "FMCG",
                "Realty": "Realty", "Nifty Realty": "Realty", "Media": "Media",
                "Nifty Media": "Media"}


def sector_topic(name: str | None) -> str | None:
    """`sector:<bucket>` for a report sector-index name or a Market Structure bucket."""
    if not name:
        return None
    bucket = SECTOR_TOPIC.get(name) or (name if name in SECTOR_TOPIC.values() else None)
    return f"sector:{bucket}" if bucket else None


# --------------------------------------------------------------------------- contracts
@dataclass(frozen=True)
class EditorialCandidate:
    candidate_id: str                 # "POST.STRUCTURE.FIFTY_TWO_WEEK"
    section: str                      # the storyboard section key it becomes
    family: str
    tier: int                         # 0-3, from the section's own (documented) rule
    reason: str                       # why that tier - the section's own value-test words
    cost_s: float = 0.0               # estimated scene seconds
    role: str = OPTIONAL
    quality: str = OK
    explains_headline: bool = False   # says WHY the headline number moved / what it hid
    tension: bool = False             # CONTRADICTS the headline (index up, most stocks down)
    topics: frozenset = frozenset()   # facts it states, for redundancy (subset rule)
    as_of: dt.date | None = None      # the session/date the story describes
    merge_target: str | None = None   # where an unselected story's fact is still carried
    payload: object = None            # the planner's own model (never inspected here)

    @property
    def effective_tier(self) -> int:
        return max(0, self.tier - 1) if self.quality in DEMOTING_QUALITY else self.tier


@dataclass(frozen=True)
class EditionPolicy:
    name: str                         # PRE / POST
    relevance: dict                   # family -> 2 PRIMARY / 1 SECONDARY (absent = 1)
    play_order: tuple                 # section keys in narrative order
    anchor: tuple                     # headline sections a tier-3 lead plays straight after
    optional_slots: int
    max_runtime: float
    fixed_cost_s: float               # hook + closing (+ anything else outside the pool)
    min_tier: int = NOTABLE
    india_cutoff: dt.date | None = None   # PRE: the previous completed session
    # keep the single best ROUTINE story when nothing is NOTABLE - only for an edition whose
    # required structure alone would not explain anything (POST: just the headline)
    quiet_floor: bool = True
    # the platform minimum (editorial.MIN_SHORT_DURATION): the quiet floor keeps adding the next
    # best ROUTINE story ONLY while the Short would otherwise run shorter than this
    min_runtime: float = 0.0

    def rel(self, family: str) -> int:
        return int(self.relevance.get(family, 1))

    def order_index(self, section: str) -> int:
        try:
            return self.play_order.index(section)
        except ValueError:
            return len(self.play_order)


@dataclass
class EditorialDecision:
    policy: str
    selected: list                     # candidate ids, in play order
    order: list                        # section keys, in play order (deduplicated)
    lead: str | None                   # the top-priority selected optional candidate id
    lead_section: str | None
    lead_tier: int
    runtime_estimate: float
    trace: list = field(default_factory=list)
    candidates: dict = field(default_factory=dict)   # id -> EditorialCandidate

    def decision(self, cid: str) -> dict | None:
        return next((t for t in self.trace if t["candidate_id"] == cid), None)

    def shown(self, cid: str) -> bool:
        t = self.decision(cid)
        return bool(t) and t["decision"] in SHOWN

    def sections_shown(self) -> set:
        return {t["section"] for t in self.trace if t["decision"] in SHOWN}

    def section_reason(self, section: str, original: str | None = None) -> str | None:
        """The planner's own reason line for `section`, with the V3 verdict appended - the best
        candidate's row for that section (a shown one first). A section the planner had already
        omitted keeps its words; a qualifying one that lost the arbitration says why."""
        rows = [t for t in self.trace if t["section"] == section]
        if not rows:
            return original
        row = next((t for t in rows if t["decision"] in SHOWN), rows[0])
        tag = f" [V3 {row['decision']}, {TIER_NAMES[row['tier']]}]"
        base = original or row["reason"]
        head = base[:48].lower()
        was_omitted = head.startswith("omitted") or ": omitted" in head
        if row["decision"] in SHOWN:
            if was_omitted or not (head.startswith(("included", "core")) or ": included" in head):
                base = "included: " + row["reason"]
            return base + tag
        if was_omitted:
            return base + tag
        return ("omitted: " + base.removeprefix("included: ").removeprefix("core: ")
                + " - " + trace_sentence(row).split(" - ", 1)[-1] + tag)

    def to_dict(self) -> dict:
        return {"version": ARBITER_VERSION, "policy": self.policy, "selected": self.selected,
                "order": self.order, "lead": self.lead, "lead_section": self.lead_section,
                "lead_tier": self.lead_tier, "runtime_estimate": self.runtime_estimate,
                "trace": self.trace}


def trace_sentence(row: dict) -> str:
    d = row["decision"]
    tier = f"{TIER_NAMES[row['tier']]}"
    if d in SHOWN:
        floor = " (quiet-edition floor: nothing reached NOTABLE)" if d == QUIET_FLOOR else ""
        return f"included [V3 {tier}]: {row['reason']}{floor}"
    detail = {
        NOT_MATERIAL: "fails its value test",
        DATA_QUALITY: f"data quality {row['quality']}",
        BLOCKED: "publication gate refused its facts",
        TEMPORAL_EXCLUDED: "describes a session after the edition's cutoff",
        SUPPRESSED_DUPLICATE: f"says what {row.get('duplicate_of')} already says",
        DROPPED_DIVERSITY: f"one {row['family']} story per edition and {row.get('displaced_by')} "
                           "ranks higher",
        BELOW_THRESHOLD: "below the edition's NOTABLE threshold",
        DROPPED_SLOTS: f"edition slots full (lowest selected: {row.get('displaced_by')})",
        DROPPED_BUDGET: "runtime budget",
        MERGED_INTO: f"its fact is carried by {row.get('merged_into')}",
    }.get(d, d)
    return f"omitted [V3 {d}]: {row['reason']} - {detail}"


# --------------------------------------------------------------------------- the arbiter
def priority_key(c: EditorialCandidate, policy: EditionPolicy) -> tuple:
    return (-c.effective_tier, -policy.rel(c.family), not c.tension, not c.explains_headline,
            policy.order_index(c.section), c.candidate_id)


def _row(c: EditorialCandidate, policy: EditionPolicy, decision: str, **extra) -> dict:
    row = {"candidate_id": c.candidate_id, "section": c.section, "family": c.family,
           "role": c.role, "tier": c.tier, "effective_tier": c.effective_tier,
           "quality": c.quality, "relevance": policy.rel(c.family),
           "explains_headline": c.explains_headline, "tension": c.tension,
           "cost_s": round(c.cost_s, 2),
           "topics": sorted(c.topics), "as_of": c.as_of.isoformat() if c.as_of else None,
           "decision": decision, "reason": c.reason, "duplicate_of": None,
           "displaced_by": None, "merged_into": None, "runtime_after": None}
    row.update(extra)
    return row


def arbitrate(candidates, policy: EditionPolicy) -> EditorialDecision:
    cands = list(candidates)
    ids = [c.candidate_id for c in cands]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate candidate ids: {ids}")
    by_id = {c.candidate_id: c for c in cands}
    required = [c for c in cands if c.role == REQUIRED]
    optional = sorted((c for c in cands if c.role != REQUIRED),
                      key=lambda c: priority_key(c, policy))
    runtime = policy.fixed_cost_s + sum(c.cost_s for c in required)
    rows = {}
    topic_owner = {}
    for c in required:
        rows[c.candidate_id] = _row(c, policy, SELECTED, runtime_after=round(runtime, 2))
        for t in c.topics:
            topic_owner.setdefault(t, c.candidate_id)

    chosen = []                          # optional candidates selected, in priority order
    family_of = {}                       # family -> [selected candidate]
    floor_pool = []                      # passed everything but the threshold

    def admit(c, decision=SELECTED):
        nonlocal runtime
        runtime += c.cost_s
        chosen.append(c)
        family_of.setdefault(c.family, []).append(c)
        for t in c.topics:
            topic_owner.setdefault(t, c.candidate_id)
        rows[c.candidate_id] = _row(c, policy, decision, runtime_after=round(runtime, 2))

    for c in optional:
        if c.quality == PUBLICATION_BLOCKED:
            rows[c.candidate_id] = _row(c, policy, BLOCKED)
            continue
        if c.quality in EXCLUDING_QUALITY:
            rows[c.candidate_id] = _row(c, policy, DATA_QUALITY)
            continue
        if c.tier <= NOT_MATERIAL_T:
            rows[c.candidate_id] = _row(c, policy, NOT_MATERIAL)
            continue
        if (policy.india_cutoff is not None and c.as_of is not None
                and c.family in INDIA_SESSION_FAMILIES and c.as_of > policy.india_cutoff):
            rows[c.candidate_id] = _row(c, policy, TEMPORAL_EXCLUDED)
            continue
        if c.topics and all(t in topic_owner for t in c.topics):
            owner = topic_owner[sorted(c.topics)[0]]
            rows[c.candidate_id] = _row(c, policy, SUPPRESSED_DUPLICATE, duplicate_of=owner,
                                        merged_into=by_id[owner].section)
            continue
        holders = family_of.get(c.family) or []
        if holders and not (c.tier >= MATERIAL and all(h.tier >= MATERIAL for h in holders)):
            rows[c.candidate_id] = _row(c, policy, DROPPED_DIVERSITY,
                                        displaced_by=holders[0].candidate_id)
            continue
        if c.effective_tier < policy.min_tier:
            floor_pool.append(c)
            rows[c.candidate_id] = _row(c, policy, BELOW_THRESHOLD)
            continue
        if len(chosen) >= policy.optional_slots:
            rows[c.candidate_id] = _row(c, policy, DROPPED_SLOTS,
                                        displaced_by=chosen[-1].candidate_id)
            continue
        if runtime + c.cost_s > policy.max_runtime:
            rows[c.candidate_id] = _row(c, policy, DROPPED_BUDGET,
                                        runtime_after=round(runtime, 2))
            continue
        admit(c)

    # Quiet edition: nothing reached NOTABLE - keep the one best ROUTINE story (still subject
    # to redundancy/diversity, already checked) so the Short explains something. Never more.
    if not chosen and policy.quiet_floor:
        for c in floor_pool:
            if chosen and runtime >= policy.min_runtime:
                break
            if family_of.get(c.family):
                rows[c.candidate_id] = _row(c, policy, DROPPED_DIVERSITY,
                                            displaced_by=family_of[c.family][0].candidate_id)
                continue
            if runtime + c.cost_s > policy.max_runtime:
                continue
            admit(c, QUIET_FLOOR)

    # A story the planner still carries elsewhere (a PRE watch card) is MERGED, not lost.
    for cid, row in rows.items():
        c = by_id[cid]
        if row["decision"] not in SHOWN and c.merge_target and row["decision"] not in (
                NOT_MATERIAL, DATA_QUALITY, BLOCKED, TEMPORAL_EXCLUDED, SUPPRESSED_DUPLICATE):
            row["merge_candidate"] = c.merge_target

    lead = chosen[0] if chosen else None
    shown_sections = {c.section for c in required} | {c.section for c in chosen}
    order = [s for s in policy.play_order if s in shown_sections]
    order += sorted(s for s in shown_sections if s not in order)
    if lead is not None and lead.tier >= MATERIAL and lead.section in order             and lead.section not in policy.anchor:
        order.remove(lead.section)
        anchor_pos = max((order.index(a) for a in policy.anchor if a in order), default=-1)
        order.insert(anchor_pos + 1, lead.section)
    sel_ids = sorted((c.candidate_id for c in required + chosen),
                     key=lambda cid: (order.index(by_id[cid].section)
                                      if by_id[cid].section in order else 99, cid))
    trace = [rows[c.candidate_id] for c in required] + \
        [rows[c.candidate_id] for c in optional]
    return EditorialDecision(
        policy=policy.name, selected=sel_ids, order=order,
        lead=lead.candidate_id if lead else None, lead_section=lead.section if lead else None,
        lead_tier=lead.tier if lead else 0, runtime_estimate=round(runtime, 2), trace=trace,
        candidates=by_id)


def mark_merged(decision: EditorialDecision, cid: str, target: str) -> None:
    """The planner confirms an unselected story's fact really is carried by `target` (e.g. a
    PRE watch card) - recorded as MERGED_INTO, never claimed before it is true."""
    row = decision.decision(cid)
    if row is not None and row["decision"] not in SHOWN:
        row["omitted_as"] = row["decision"]
        row["decision"] = MERGED_INTO
        row["merged_into"] = target


def with_quality(c: EditorialCandidate, quality: str) -> EditorialCandidate:
    return replace(c, quality=quality)


__all__ = ["ARBITER_VERSION", "EditorialCandidate", "EditionPolicy", "EditorialDecision",
           "arbitrate", "mark_merged", "priority_key", "sector_topic", "trace_sentence",
           "with_quality", "REQUIRED", "OPTIONAL", "ROUTINE", "NOTABLE", "MATERIAL",
           "OK", "PARTIAL", "STALE", "SOURCE_FAILURE", "EMPTY", "PUBLICATION_BLOCKED",
           "SELECTED", "QUIET_FLOOR", "NOT_MATERIAL", "DATA_QUALITY", "BLOCKED",
           "TEMPORAL_EXCLUDED", "SUPPRESSED_DUPLICATE", "DROPPED_DIVERSITY", "BELOW_THRESHOLD",
           "DROPPED_SLOTS", "DROPPED_BUDGET", "MERGED_INTO", "SHOWN", "INDEX", "SECTORS",
           "BREADTH", "FLOWS", "MOVERS", "GLOBAL", "VOLATILITY", "CALENDAR", "IPO", "EXCHANGE",
           "STOCK_WATCH", "STRUCTURE_ANCHOR", "INDIA_SESSION_FAMILIES", "TIER_NAMES"]
