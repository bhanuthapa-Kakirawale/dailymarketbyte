"""Phase 4.2 Packet 5.4B: deterministic editorial policy SIMULATION.

Answers a measurement question - "which deterministic policy reduces ~21 novel developments/
day toward a 40-60s Short's 3-5 story capacity, and what does each one trade off?" - never
which policy is "best" (out of scope; see `docs/MARKET_INTELLIGENCE_RADAR.md`). This module
implements four deterministic policies as pure functions over already-classified novelty
records; nothing here is wired into production, `main.py`, video, or any publication gate.

Hard boundaries, mirrored from every other `radar/` module:

* **No acquisition, no mutation.** Input is a plain list of novelty-record dicts (the exact
  shape `radar.novelty_validation.combined_records` already produces - i.e. `radar.historical_
  validation.candidate_record` plus its novelty fields). Every policy function reads these
  dicts and returns NEW "story" dicts; nothing here writes back into the input records, the raw
  Radar candidates, or the novelty classification that produced them.
* **No score.** Every policy is gates + explicit bucket ordering + a deterministic
  `symbol ASC` tie-break - never a weighted or numeric priority. Price move and RVOL magnitude
  are never selection inputs (packet spec sections 11-12); they may only be MEASURED on already-
  selected stories afterward.

## The four policies

* `EVIDENCE_BREADTH` (Policy A) - 3-family candidates before 2-family candidates, `symbol ASC`
  within each group. No novelty-type preference at all beyond the CONTINUATION exclusion every
  policy shares.
* `STATE_CHANGE_FIRST` (Policy B) - four ordered buckets: state-change (`MULTIPLE_CHANGES`,
  `NEW_EVIDENCE_FAMILY`, `NEW_TECHNICAL_EVENT`, `PERSISTENCE_TRANSITION`, `DIRECTION_TRANSITION`,
  `ATTENTION_ESCALATION`) x 3-family, state-change x 2-family, `NEW_CANDIDATE` x 3-family,
  `NEW_CANDIDATE` x 2-family - `symbol ASC` within each bucket.
* `STATE_CHANGE_WITH_COOLDOWN` (Policy C) - Policy B's bucket ordering, but a symbol already
  SELECTED (published) by THIS policy's own simulated history within the prior
  `PUBLICATION_COOLDOWN_SESSIONS` (3) valid trading sessions is suppressed UNLESS its current
  record's `novelty_change_reason_codes` shows an `ATTENTION_ESCALATION`, `NEW_EVIDENCE_FAMILY`,
  or `DIRECTION_TRANSITION` trigger (a `NEW_TECHNICAL_EVENT` alone never overrides). Cooldown is
  keyed on what THIS policy actually selected, never on every raw Radar occurrence, and never on
  another policy's selections (each policy simulates its own independent publication history -
  packet spec section 25).
* `DIVERSITY_AWARE` (Policy D) - Policy C's cooldown-filtered, bucket-ordered pool (D keeps its
  OWN independent cooldown history, distinct from C's), then a deterministic three-round fill:
  first eligible `ALIGNED_POSITIVE`, then first eligible `ALIGNED_NEGATIVE`, then the remaining
  slots filled by walking the base order and skipping symbols already picked. Never invents a
  direction that isn't present in the eligible pool.

## Determinism and point-in-time

`simulate_session` takes one session's already-filtered (non-`CONTINUATION`) candidate pool and
an explicit `session_index` (a POSITION in the caller's already-consecutive trading-session
list, exactly like `radar.novelty`'s own lookback) plus each policy's own publication-history
dict up to that point - never a full session list it could look ahead into, and never today's
wall clock. `simulate_history` is the thin, chronological driver that keeps each policy's
history strictly separate and only ever passes it what was already decided for sessions
`< i`.
"""
from __future__ import annotations

from .models import NoveltyType

MAX_STORIES_PER_SESSION = 5
PUBLICATION_COOLDOWN_SESSIONS = 3

POLICY_EVIDENCE_BREADTH = "EVIDENCE_BREADTH"
POLICY_STATE_CHANGE_FIRST = "STATE_CHANGE_FIRST"
POLICY_STATE_CHANGE_WITH_COOLDOWN = "STATE_CHANGE_WITH_COOLDOWN"
POLICY_DIVERSITY_AWARE = "DIVERSITY_AWARE"
ALL_POLICIES = (POLICY_EVIDENCE_BREADTH, POLICY_STATE_CHANGE_FIRST,
               POLICY_STATE_CHANGE_WITH_COOLDOWN, POLICY_DIVERSITY_AWARE)

_CONTINUATION = NoveltyType.CONTINUATION.value
_NEW_CANDIDATE = NoveltyType.NEW_CANDIDATE.value
_STATE_CHANGE_TYPES = frozenset({
    NoveltyType.MULTIPLE_CHANGES.value, NoveltyType.NEW_EVIDENCE_FAMILY.value,
    NoveltyType.NEW_TECHNICAL_EVENT.value, NoveltyType.PERSISTENCE_TRANSITION.value,
    NoveltyType.DIRECTION_TRANSITION.value, NoveltyType.ATTENTION_ESCALATION.value,
})

# Cooldown-override reason-code prefixes (packet spec section 9) - a NEW_TECHNICAL_EVENT_* code
# deliberately absent: a persistent technical event alone must never lift a cooldown suppression.
_OVERRIDE_ATTENTION = "ATTENTION_ESCALATION"
_OVERRIDE_NEW_FAMILY = "NEW_EVIDENCE_FAMILY"
_OVERRIDE_DIRECTION = "DIRECTION_TRANSITION"


def eligible_pool(session_records: list) -> list:
    """Every policy's shared starting pool: `novelty_type != CONTINUATION` (packet spec
    section 5) - a CONTINUATION record is never a candidate for any policy."""
    return [r for r in session_records if r["novelty_type"] != _CONTINUATION]


def _family_bucket(record) -> int:
    return 0 if record["independent_signal_count"] == 3 else 1


def _policy_a_key(record):
    return (_family_bucket(record), record["symbol"])


def _policy_a_bucket_label(record) -> str:
    return "3_FAMILY" if _family_bucket(record) == 0 else "2_FAMILY"


def _policy_b_group(record) -> int:
    is_state_change = record["novelty_type"] in _STATE_CHANGE_TYPES
    fam3 = _family_bucket(record) == 0
    if is_state_change and fam3:
        return 0
    if is_state_change and not fam3:
        return 1
    if fam3:
        return 2   # NEW_CANDIDATE, 3-family
    return 3       # NEW_CANDIDATE, 2-family


_POLICY_B_LABELS = {0: "STATE_CHANGE_3_FAMILY", 1: "STATE_CHANGE_2_FAMILY",
                    2: "NEW_CANDIDATE_3_FAMILY", 3: "NEW_CANDIDATE_2_FAMILY"}


def _policy_b_key(record):
    return (_policy_b_group(record), record["symbol"])


def _cooldown_override_triggers(record) -> list:
    """Which of the three exception triggers (packet spec section 9) this record's OWN
    `novelty_change_reason_codes` carries - checked directly, including when the record's
    `novelty_type` is `MULTIPLE_CHANGES` and the individual trigger is one of several folded
    into it. Order is fixed (attention, then family, then direction) purely for a deterministic
    `cooldown_status` label; all present triggers are still returned."""
    codes = record.get("novelty_change_reason_codes") or []
    triggers = []
    if any(c.startswith(_OVERRIDE_ATTENTION) for c in codes):
        triggers.append(_OVERRIDE_ATTENTION)
    if any(c.startswith("NEW_FAMILY_") for c in codes):
        triggers.append(_OVERRIDE_NEW_FAMILY)
    if any(c.startswith(_OVERRIDE_DIRECTION + "_") for c in codes):
        triggers.append(_OVERRIDE_DIRECTION)
    return triggers


def _story(record, *, policy: str, bucket: str, cooldown_status: str,
          diversity_role: str | None = None) -> dict:
    return {
        "session_date": record["date"], "symbol": record["symbol"],
        "price_change_pct": record["price_change_pct"],
        "independent_signal_count": record["independent_signal_count"],
        "active_families": list(record["active_families"]),
        "novelty_type": record["novelty_type"],
        "direction_compatibility": record["direction_compatibility"],
        "outside_top5_movers": record["outside_top5_movers"],
        "rvol": record.get("rvol"),
        "reason": record.get("novelty_reason") or record.get("why_radar_noticed_it") or "",
        "selected_by_policy": policy,
        "selection_bucket": bucket,
        "tie_break": "SYMBOL_ASC",
        "cooldown_status": cooldown_status,
        "diversity_role": diversity_role,
    }


# ------------------------------------------------------------------ Policy A
def select_policy_a(pool: list) -> list:
    ordered = sorted(pool, key=_policy_a_key)
    return [_story(r, policy=POLICY_EVIDENCE_BREADTH, bucket=_policy_a_bucket_label(r),
                   cooldown_status="NOT_APPLICABLE")
           for r in ordered[:MAX_STORIES_PER_SESSION]]


# ------------------------------------------------------------------ Policy B
def _policy_b_ordered(pool: list) -> list:
    return sorted(pool, key=_policy_b_key)


def select_policy_b(pool: list) -> list:
    ordered = _policy_b_ordered(pool)
    return [_story(r, policy=POLICY_STATE_CHANGE_FIRST, bucket=_POLICY_B_LABELS[_policy_b_group(r)],
                   cooldown_status="NOT_APPLICABLE")
           for r in ordered[:MAX_STORIES_PER_SESSION]]


# ------------------------------------------------------------------ cooldown (shared by C, D)
def _apply_cooldown(ordered: list, session_index: int, publication_history: dict) -> tuple[list, list]:
    """`(eligible, suppressed)`. `eligible` keeps `ordered`'s own relative order - suppression
    only ever removes entries, never reorders survivors. `publication_history`:
    `{symbol: session_index_last_selected}`, owned entirely by the calling policy's own
    simulation - never shared across policies (packet spec section 25)."""
    eligible, suppressed = [], []
    for r in ordered:
        last = publication_history.get(r["symbol"])
        within_cooldown = last is not None and (session_index - last) <= PUBLICATION_COOLDOWN_SESSIONS
        if not within_cooldown:
            eligible.append((r, "NOT_RECENTLY_PUBLISHED"))
            continue
        triggers = _cooldown_override_triggers(r)
        if triggers:
            eligible.append((r, "COOLDOWN_OVERRIDE_" + "_AND_".join(triggers)))
        else:
            suppressed.append({"session_date": r["date"], "symbol": r["symbol"],
                               "novelty_type": r["novelty_type"],
                               "sessions_since_last_published": session_index - last})
    return eligible, suppressed


# ------------------------------------------------------------------ Policy C
def select_policy_c(pool: list, session_index: int, publication_history: dict) -> tuple[list, list]:
    """Returns `(stories, suppressed)`. Mutates nothing - the caller is responsible for
    updating `publication_history` with the returned stories' symbols after this call (so a
    story counts as "published" starting from the session it was actually selected on, never
    the session it merely became eligible)."""
    ordered = _policy_b_ordered(pool)
    eligible, suppressed = _apply_cooldown(ordered, session_index, publication_history)
    selected = eligible[:MAX_STORIES_PER_SESSION]
    stories = [_story(r, policy=POLICY_STATE_CHANGE_WITH_COOLDOWN,
                      bucket=_POLICY_B_LABELS[_policy_b_group(r)], cooldown_status=status)
              for r, status in selected]
    return stories, suppressed


# ------------------------------------------------------------------ Policy D
def select_policy_d(pool: list, session_index: int, publication_history: dict) -> tuple[list, list]:
    """Same cooldown-filtered, Policy-B-ordered pool as Policy C (packet spec section 10), but
    with its OWN independent `publication_history`, followed by a deterministic three-round
    diversity fill: first eligible `ALIGNED_POSITIVE`, then first eligible `ALIGNED_NEGATIVE`,
    then remaining capacity filled by walking the base order (skipping already-picked symbols).
    Never invents a direction absent from the eligible pool, and never displaces a candidate
    already picked in an earlier round."""
    ordered = _policy_b_ordered(pool)
    eligible, suppressed = _apply_cooldown(ordered, session_index, publication_history)

    picked_symbols: set = set()
    rounds: list = []          # (record, status, diversity_role)

    pos = next((e for e in eligible if e[0]["direction_compatibility"] == "ALIGNED_POSITIVE"), None)
    if pos is not None:
        rounds.append((*pos, "FIRST_POSITIVE"))
        picked_symbols.add(pos[0]["symbol"])

    neg = next((e for e in eligible if e[0]["direction_compatibility"] == "ALIGNED_NEGATIVE"
               and e[0]["symbol"] not in picked_symbols), None)
    if neg is not None:
        rounds.append((*neg, "FIRST_NEGATIVE"))
        picked_symbols.add(neg[0]["symbol"])

    for r, status in eligible:
        if len(rounds) >= MAX_STORIES_PER_SESSION:
            break
        if r["symbol"] in picked_symbols:
            continue
        rounds.append((r, status, "FILL"))
        picked_symbols.add(r["symbol"])

    rounds = rounds[:MAX_STORIES_PER_SESSION]
    stories = [_story(r, policy=POLICY_DIVERSITY_AWARE, bucket=_POLICY_B_LABELS[_policy_b_group(r)],
                      cooldown_status=status, diversity_role=role)
              for r, status, role in rounds]
    return stories, suppressed


# ------------------------------------------------------------------ chronological driver
def simulate_history(sessions: list) -> dict:
    """`sessions`: `[(session_date, [record, ...]), ...]`, oldest-first, already consecutive
    valid trading sessions (list-index adjacency IS trading-session adjacency - the same
    contract `radar.novelty.classify_history` requires of its own caller). Each record is the
    combined candidate+novelty dict shape `radar.novelty_validation.combined_records` produces.

    Returns `{"stories": {policy: {session_date: [story, ...]}},
              "suppressed": {policy: {session_date: [suppressed, ...]}}}` for the two
    cooldown-aware policies. Selection for session index `i` reads only `sessions[i]`'s own pool
    and each policy's history built from sessions `< i` - no future session can influence an
    earlier one, and no policy's history is shared with another's (packet spec sections 25-26).
    """
    stories = {p: {} for p in ALL_POLICIES}
    suppressed = {POLICY_STATE_CHANGE_WITH_COOLDOWN: {}, POLICY_DIVERSITY_AWARE: {}}
    history_c: dict = {}
    history_d: dict = {}

    for i, (session_date, records) in enumerate(sessions):
        pool = eligible_pool(records)

        stories[POLICY_EVIDENCE_BREADTH][session_date] = select_policy_a(pool)
        stories[POLICY_STATE_CHANGE_FIRST][session_date] = select_policy_b(pool)

        c_stories, c_suppressed = select_policy_c(pool, i, history_c)
        stories[POLICY_STATE_CHANGE_WITH_COOLDOWN][session_date] = c_stories
        suppressed[POLICY_STATE_CHANGE_WITH_COOLDOWN][session_date] = c_suppressed
        for s in c_stories:
            history_c[s["symbol"]] = i

        d_stories, d_suppressed = select_policy_d(pool, i, history_d)
        stories[POLICY_DIVERSITY_AWARE][session_date] = d_stories
        suppressed[POLICY_DIVERSITY_AWARE][session_date] = d_suppressed
        for s in d_stories:
            history_d[s["symbol"]] = i

    return {"stories": stories, "suppressed": suppressed}


__all__ = ["MAX_STORIES_PER_SESSION", "PUBLICATION_COOLDOWN_SESSIONS", "POLICY_EVIDENCE_BREADTH",
          "POLICY_STATE_CHANGE_FIRST", "POLICY_STATE_CHANGE_WITH_COOLDOWN",
          "POLICY_DIVERSITY_AWARE", "ALL_POLICIES", "eligible_pool", "select_policy_a",
          "select_policy_b", "select_policy_c", "select_policy_d", "simulate_history"]
