"""Phase 4.2 Packet 5.4B.1: hybrid editorial policy simulation - Policy E (`HYBRID_RESERVED`)
and Policy F (`HYBRID_RESERVED_DIVERSITY`).

Packet 5.4B's Policies A-D (`radar/editorial_policy.py`) are used here ONLY as read-only
building blocks - this module imports their private helpers (bucket ordering, cooldown
application) to guarantee identical semantics, but adds NO code to that module and calls NONE
of its four `select_policy_*` functions, so A/B/C/D's own behavior is provably untouched by this
packet (see `tests/test_editorial_policy_hybrid.py::test_policy_c_and_d_unchanged`, which
re-runs Packet 5.4B's own fixtures against `radar.editorial_policy` directly).

## Policy E - HYBRID_RESERVED

Identical to Policy C (novelty eligibility, Policy-B bucket ordering, 3-session cooldown with
the same three overrides) up through cooldown - see `radar.editorial_policy._apply_cooldown`,
reused verbatim, never reimplemented. Then, before filling the session's 5 slots:

1. From the cooldown-SURVIVING pool (cooldown always runs first - packet spec section 8; being
   3-family is never itself a cooldown override), take every candidate with
   `independent_signal_count == 3`.
2. Reserve up to `MAX_RESERVED_3FAMILY_SLOTS` (2) of them, ordered state-change-type first, then
   `NEW_CANDIDATE`, then `symbol ASC` within each - the same state-change-first philosophy
   Policy B/C already use, restricted to the already-3-family subset (never price move, never
   RVOL).
3. Fill the remaining capacity (`5 - reserved_count`) from the FULL cooldown-surviving pool,
   in Policy C's own order, skipping symbols already reserved. This fill step can still surface
   an UNRESERVED 3-family candidate if one remains and ranks high enough in Policy C's own
   ordering - reservation only guarantees a FLOOR, it never caps 3-family representation.

## Policy F - HYBRID_RESERVED_DIVERSITY

Identical reservation step to Policy E (its own independent cooldown history - never shared
with E, C, or D), then Policy D's three-round diversity fill (first eligible
`ALIGNED_POSITIVE`, first eligible `ALIGNED_NEGATIVE`, then remaining base order) applied ONLY
to the remaining (non-reserved) capacity - a reserved candidate is never evicted to manufacture
direction balance (packet spec's explicit invariant), and an unavailable direction is never
invented.

## No score anywhere

Both policies are entirely gates + explicit ordering + `symbol ASC` tie-breaks, exactly like
Policies A-D. Every selected story carries `reserved_3family` (bool) and `diversity_role`
(`POSITIVE_SLOT`/`NEGATIVE_SLOT`/`BASE_ORDER`/`NOT_APPLICABLE`) so no selection is opaque.
"""
from __future__ import annotations

from . import editorial_policy as ep

MAX_RESERVED_3FAMILY_SLOTS = 2

POLICY_HYBRID_RESERVED = "HYBRID_RESERVED"
POLICY_HYBRID_RESERVED_DIVERSITY = "HYBRID_RESERVED_DIVERSITY"
ALL_HYBRID_POLICIES = (POLICY_HYBRID_RESERVED, POLICY_HYBRID_RESERVED_DIVERSITY)

_RESERVED_BUCKET_STATE_CHANGE = "RESERVED_3FAMILY_STATE_CHANGE"
_RESERVED_BUCKET_NEW_CANDIDATE = "RESERVED_3FAMILY_NEW_CANDIDATE"


def _hybrid_story(record, *, policy: str, bucket: str, cooldown_status: str,
                  reserved_3family: bool, diversity_role: str) -> dict:
    """Same shape as `radar.editorial_policy`'s own story dict, plus the two fields packet spec
    section 21 requires (`reserved_3family`, a real `diversity_role` even for Policy E, which
    always reports `NOT_APPLICABLE` since it has no diversity logic). A fresh function, not a
    change to `radar.editorial_policy._story` - that helper, and every Policy A-D call site
    using it, is untouched."""
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
        "reserved_3family": reserved_3family,
        "diversity_role": diversity_role,
    }


def _reserved_3family_key(record) -> tuple:
    is_state_change = record["novelty_type"] in ep._STATE_CHANGE_TYPES
    return (0 if is_state_change else 1, record["symbol"])


def reserve_3family(eligible: list, max_slots: int = MAX_RESERVED_3FAMILY_SLOTS) -> tuple[list, list]:
    """`eligible`: `[(record, cooldown_status), ...]`, already cooldown-filtered and in Policy
    C's own bucket order (`radar.editorial_policy._policy_b_ordered` + `._apply_cooldown`).

    Returns `(reserved, remaining)` - `reserved` ordered state-change-3family then
    NEW_CANDIDATE-3family then `symbol ASC` (packet spec section 5); `remaining` is `eligible`
    with reserved symbols removed, preserving `eligible`'s own relative order untouched (never
    re-sorted - a candidate not reserved is exactly as eligible for the normal fill step as it
    always was).
    """
    eligible_3family = [e for e in eligible if e[0]["independent_signal_count"] == 3]
    reserved_count = min(max_slots, len(eligible_3family))
    if reserved_count == 0:
        return [], list(eligible)
    ordered_3family = sorted(eligible_3family, key=lambda e: _reserved_3family_key(e[0]))
    reserved = ordered_3family[:reserved_count]
    reserved_symbols = {e[0]["symbol"] for e in reserved}
    remaining = [e for e in eligible if e[0]["symbol"] not in reserved_symbols]
    return reserved, remaining


def _reserved_bucket(record) -> str:
    return (_RESERVED_BUCKET_STATE_CHANGE if record["novelty_type"] in ep._STATE_CHANGE_TYPES
           else _RESERVED_BUCKET_NEW_CANDIDATE)


# ------------------------------------------------------------------ Policy E
def select_policy_e(pool: list, session_index: int, publication_history: dict) -> tuple[list, list]:
    """Returns `(stories, suppressed)`. `publication_history` is Policy E's OWN dict - never
    shared with C, D, or F. Caller updates it with the returned stories' symbols after this
    call, exactly like `radar.editorial_policy.select_policy_c`."""
    ordered = ep._policy_b_ordered(pool)
    eligible, suppressed = ep._apply_cooldown(ordered, session_index, publication_history)
    reserved, remaining = reserve_3family(eligible)

    stories = []
    for record, status in reserved:
        stories.append(_hybrid_story(record, policy=POLICY_HYBRID_RESERVED,
                                     bucket=_reserved_bucket(record), cooldown_status=status,
                                     reserved_3family=True, diversity_role="NOT_APPLICABLE"))

    remaining_capacity = ep.MAX_STORIES_PER_SESSION - len(stories)
    for record, status in remaining[:remaining_capacity]:
        stories.append(_hybrid_story(record, policy=POLICY_HYBRID_RESERVED,
                                     bucket=ep._POLICY_B_LABELS[ep._policy_b_group(record)],
                                     cooldown_status=status, reserved_3family=False,
                                     diversity_role="NOT_APPLICABLE"))
    return stories, suppressed


# ------------------------------------------------------------------ Policy F
def select_policy_f(pool: list, session_index: int, publication_history: dict
                    ) -> tuple[list, list, dict]:
    """Returns `(stories, suppressed, diagnostics)`. `diagnostics` records what the diversity
    fill step could see for this session - `positive_available`/`negative_available` (present
    anywhere in the non-reserved remaining pool) versus `positive_selected`/`negative_selected`
    (actually chosen) and `remaining_capacity`, so a caller can tell "diversity had nothing to
    work with" apart from "diversity had options but ran out of remaining slots" without
    re-deriving it from the story list."""
    ordered = ep._policy_b_ordered(pool)
    eligible, suppressed = ep._apply_cooldown(ordered, session_index, publication_history)
    reserved, remaining = reserve_3family(eligible)

    stories = []
    for record, status in reserved:
        stories.append(_hybrid_story(record, policy=POLICY_HYBRID_RESERVED_DIVERSITY,
                                     bucket=_reserved_bucket(record), cooldown_status=status,
                                     reserved_3family=True, diversity_role="NOT_APPLICABLE"))

    remaining_capacity = ep.MAX_STORIES_PER_SESSION - len(stories)
    picked_symbols = {s["symbol"] for s in stories}

    positive_available = any(e[0]["direction_compatibility"] == "ALIGNED_POSITIVE" for e in remaining)
    negative_available = any(e[0]["direction_compatibility"] == "ALIGNED_NEGATIVE" for e in remaining)
    positive_selected = negative_selected = False

    fill_rounds: list = []
    if remaining_capacity > 0:
        pos = next((e for e in remaining if e[0]["direction_compatibility"] == "ALIGNED_POSITIVE"
                   and e[0]["symbol"] not in picked_symbols), None)
        if pos is not None:
            fill_rounds.append((*pos, "POSITIVE_SLOT"))
            picked_symbols.add(pos[0]["symbol"])
            positive_selected = True

    if len(fill_rounds) < remaining_capacity:
        neg = next((e for e in remaining if e[0]["direction_compatibility"] == "ALIGNED_NEGATIVE"
                   and e[0]["symbol"] not in picked_symbols), None)
        if neg is not None:
            fill_rounds.append((*neg, "NEGATIVE_SLOT"))
            picked_symbols.add(neg[0]["symbol"])
            negative_selected = True

    for record, status in remaining:
        if len(fill_rounds) >= remaining_capacity:
            break
        if record["symbol"] in picked_symbols:
            continue
        fill_rounds.append((record, status, "BASE_ORDER"))
        picked_symbols.add(record["symbol"])

    for record, status, role in fill_rounds[:remaining_capacity]:
        stories.append(_hybrid_story(record, policy=POLICY_HYBRID_RESERVED_DIVERSITY,
                                     bucket=ep._POLICY_B_LABELS[ep._policy_b_group(record)],
                                     cooldown_status=status, reserved_3family=False,
                                     diversity_role=role))

    both_available_but_capacity_short = (positive_available and negative_available
                                         and remaining_capacity < 2)
    diagnostics = {
        "remaining_capacity": remaining_capacity,
        "positive_available": positive_available, "negative_available": negative_available,
        "positive_selected": positive_selected, "negative_selected": negative_selected,
        "both_directions_available_but_capacity_prevented_one": both_available_but_capacity_short,
    }
    return stories, suppressed, diagnostics


# ------------------------------------------------------------------ chronological driver
def simulate_hybrid_history(sessions: list) -> dict:
    """`sessions`: `[(session_date, [record, ...]), ...]`, oldest-first, already consecutive
    valid trading sessions - identical contract to `radar.editorial_policy.simulate_history`.
    Policy E and Policy F each keep their OWN independent publication-history dict; neither
    reads the other's, and neither reads Policy C/D's (which this module never even
    constructs). Selection for session index `i` reads only that session's own pool and each
    policy's history built from sessions `< i`.
    """
    stories = {p: {} for p in ALL_HYBRID_POLICIES}
    suppressed = {p: {} for p in ALL_HYBRID_POLICIES}
    reservation = {p: {} for p in ALL_HYBRID_POLICIES}
    diversity_diagnostics: dict = {}

    history_e: dict = {}
    history_f: dict = {}

    for i, (session_date, records) in enumerate(sessions):
        pool = ep.eligible_pool(records)

        ordered = ep._policy_b_ordered(pool)
        eligible_e, _ = ep._apply_cooldown(ordered, i, history_e)
        eligible_3family_e = [e for e in eligible_e if e[0]["independent_signal_count"] == 3]

        e_stories, e_suppressed = select_policy_e(pool, i, history_e)
        stories[POLICY_HYBRID_RESERVED][session_date] = e_stories
        suppressed[POLICY_HYBRID_RESERVED][session_date] = e_suppressed
        reservation[POLICY_HYBRID_RESERVED][session_date] = {
            "eligible_3family": len(eligible_3family_e),
            "reserved_count": sum(1 for s in e_stories if s["reserved_3family"]),
        }
        for s in e_stories:
            history_e[s["symbol"]] = i

        f_stories, f_suppressed, f_diag = select_policy_f(pool, i, history_f)
        stories[POLICY_HYBRID_RESERVED_DIVERSITY][session_date] = f_stories
        suppressed[POLICY_HYBRID_RESERVED_DIVERSITY][session_date] = f_suppressed
        ordered_f = ep._policy_b_ordered(pool)
        eligible_f, _ = ep._apply_cooldown(ordered_f, i, {**history_f})  # diagnostic-only re-check
        reservation[POLICY_HYBRID_RESERVED_DIVERSITY][session_date] = {
            "eligible_3family": sum(1 for e in eligible_f if e[0]["independent_signal_count"] == 3),
            "reserved_count": sum(1 for s in f_stories if s["reserved_3family"]),
        }
        diversity_diagnostics[session_date] = f_diag
        for s in f_stories:
            history_f[s["symbol"]] = i

    return {"stories": stories, "suppressed": suppressed, "reservation": reservation,
           "diversity_diagnostics": diversity_diagnostics}


__all__ = ["MAX_RESERVED_3FAMILY_SLOTS", "POLICY_HYBRID_RESERVED",
          "POLICY_HYBRID_RESERVED_DIVERSITY", "ALL_HYBRID_POLICIES", "reserve_3family",
          "select_policy_e", "select_policy_f", "simulate_hybrid_history"]
