"""Phase 4.2 Packet 5.4B: historical validation of the deterministic editorial policy
simulation, applied to the exact Packet 5.4A novelty artifact (20 sessions, 561 raw candidates,
420 novel / non-CONTINUATION records). Analysis only - `radar.editorial_policy.simulate_history`
mutates nothing it is given; this module reads its output and measures it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
from collections import Counter

from config import OUT_DIR

from . import editorial_policy as ep
from .models import NoveltyType

SCHEMA_VERSION = "1.0"
ARTIFACT_DIR = os.path.join(OUT_DIR, "radar_validation")

_STATE_CHANGE_TRIGGER_PREFIXES = {
    "DIRECTION_TRANSITION": "DIRECTION_TRANSITION_",
    "PERSISTENCE_TRANSITION": "PERSISTENCE_TRANSITION_",
    "ATTENTION_ESCALATION": "ATTENTION_ESCALATION",
    "NEW_EVIDENCE_FAMILY": "NEW_FAMILY_",
    "NEW_TECHNICAL_EVENT": "NEW_TECHNICAL_EVENT_",
}


# ------------------------------------------------------------------ input loading
def load_novelty_artifact(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def sessions_from_novelty_artifact(artifact: dict) -> list:
    """`[(session_date, [record, ...]), ...]`, oldest-first, from a `radar.novelty_validation`
    artifact's own `metadata.session_dates` (already the correct chronological, consecutive
    trading-session order) and `candidates` list."""
    by_date: dict = {}
    for r in artifact["candidates"]:
        by_date.setdefault(r["date"], []).append(r)
    return [(dt.date.fromisoformat(d), by_date.get(d, [])) for d in artifact["metadata"]["session_dates"]]


# ------------------------------------------------------------------ per-policy metrics
def _story_counts_per_session(stories_by_date: dict, session_dates: list) -> list:
    return [len(stories_by_date.get(d, [])) for d in session_dates]


def _session_distribution(counts: list) -> dict:
    buckets = {"0": 0, "1-2": 0, "3": 0, "4": 0, "5": 0}
    for c in counts:
        if c == 0:
            buckets["0"] += 1
        elif c <= 2:
            buckets["1-2"] += 1
        elif c == 3:
            buckets["3"] += 1
        elif c == 4:
            buckets["4"] += 1
        else:
            buckets["5"] += 1
    return buckets


def _recurrence(all_stories: list) -> dict:
    counts = Counter(s["symbol"] for s in all_stories)
    buckets = {"1_time": 0, "2_times": 0, "3_plus_times": 0}
    for _sym, c in counts.items():
        if c == 1:
            buckets["1_time"] += 1
        elif c == 2:
            buckets["2_times"] += 1
        else:
            buckets["3_plus_times"] += 1
    top10 = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {"buckets": buckets, "max_appearances": max(counts.values()) if counts else 0,
           "top_10_symbols": [{"symbol": s, "count": c} for s, c in top10]}


def _quiet_preservation(all_stories: list) -> dict:
    total = len(all_stories)
    lt1 = sum(1 for s in all_stories if s["price_change_pct"] is not None and abs(s["price_change_pct"]) < 1.0)
    lt2 = sum(1 for s in all_stories if s["price_change_pct"] is not None and abs(s["price_change_pct"]) < 2.0)
    lt3 = sum(1 for s in all_stories if s["price_change_pct"] is not None and abs(s["price_change_pct"]) < 3.0)
    return {"total_selected": total, "lt_1pct": lt1, "lt_2pct": lt2, "lt_3pct": lt3,
           "pct_lt_1pct": _pct(lt1, total), "pct_lt_2pct": _pct(lt2, total), "pct_lt_3pct": _pct(lt3, total)}


def _top_mover_differentiation(all_stories: list) -> dict:
    total = len(all_stories)
    outside = sum(1 for s in all_stories if s["outside_top5_movers"])
    overlap = total - outside
    return {"total_selected": total, "outside_top5": outside, "overlapping_top5": overlap,
           "pct_outside_top5": _pct(outside, total), "pct_overlapping_top5": _pct(overlap, total)}


def _direction_distribution(all_stories: list, stories_by_date: dict, session_dates: list) -> dict:
    total = len(all_stories)
    counts = Counter(s["direction_compatibility"] for s in all_stories)
    pct = {k: _pct(v, total) for k, v in counts.items()}

    all_positive = all_negative = mixed_sessions = 0
    for d in session_dates:
        directions = {s["direction_compatibility"] for s in stories_by_date.get(d, [])}
        if not directions:
            continue
        if directions == {"ALIGNED_POSITIVE"}:
            all_positive += 1
        elif directions == {"ALIGNED_NEGATIVE"}:
            all_negative += 1
        elif {"ALIGNED_POSITIVE", "ALIGNED_NEGATIVE"} <= directions or len(directions) > 1:
            mixed_sessions += 1
    return {"counts": dict(counts), "percentages": pct,
           "sessions_all_positive": all_positive, "sessions_all_negative": all_negative,
           "sessions_with_both_directions_present": mixed_sessions}


def _novelty_composition(all_stories: list) -> dict:
    out = {t.value: 0 for t in NoveltyType}
    for s in all_stories:
        out[s["novelty_type"]] += 1
    return out


def _three_family_capture(sessions: list, stories_by_date: dict) -> dict:
    eligible_total = selected_total = 0
    zero_capture_sessions = 0
    for session_date, records in sessions:
        pool = ep.eligible_pool(records)
        eligible_3fam = [r for r in pool if r["independent_signal_count"] == 3]
        selected_3fam = [s for s in stories_by_date.get(session_date, [])
                        if s["independent_signal_count"] == 3]
        eligible_total += len(eligible_3fam)
        selected_total += len(selected_3fam)
        if eligible_3fam and not selected_3fam:
            zero_capture_sessions += 1
    return {"eligible": eligible_total, "selected": selected_total,
           "capture_pct": _pct(selected_total, eligible_total),
           "sessions_with_eligible_but_zero_selected": zero_capture_sessions}


def _state_change_capture(sessions: list, stories_by_date: dict) -> dict:
    def _count_type(records_or_stories, novelty_type):
        return sum(1 for r in records_or_stories if r["novelty_type"] == novelty_type)

    def _count_trigger(records_or_stories, prefix):
        return sum(1 for r in records_or_stories
                  if any(c.startswith(prefix) for c in (r.get("novelty_change_reason_codes") or [])))

    eligible_all, selected_all = [], []
    for session_date, records in sessions:
        eligible_all.extend(ep.eligible_pool(records))
        selected_all.extend(stories_by_date.get(session_date, []))
    # `stories` (built by `radar.editorial_policy._story`) don't carry `novelty_change_reason_codes`
    # by design (they are a published-story summary, not the full novelty record) - trigger-level
    # SELECTED capture is instead read off `cooldown_status`, which already names any override
    # trigger that fired; for policies with no cooldown (A/B) trigger capture uses the same
    # `novelty_change_reason_codes` presence on the underlying record, looked up by (date, symbol).
    by_key = {(r["date"], r["symbol"]): r for r in eligible_all}

    def _selected_trigger_count(prefix):
        n = 0
        for s in selected_all:
            rec = by_key.get((s["session_date"], s["symbol"]))
            if rec and any(c.startswith(prefix) for c in (rec.get("novelty_change_reason_codes") or [])):
                n += 1
        return n

    return {
        "eligible_MULTIPLE_CHANGES": _count_type(eligible_all, "MULTIPLE_CHANGES"),
        "selected_MULTIPLE_CHANGES": _count_type(selected_all, "MULTIPLE_CHANGES"),
        "eligible_NEW_TECHNICAL_EVENT": _count_type(eligible_all, "NEW_TECHNICAL_EVENT"),
        "selected_NEW_TECHNICAL_EVENT": _count_type(selected_all, "NEW_TECHNICAL_EVENT"),
        "eligible_NEW_EVIDENCE_FAMILY": _count_type(eligible_all, "NEW_EVIDENCE_FAMILY"),
        "selected_NEW_EVIDENCE_FAMILY": _count_type(selected_all, "NEW_EVIDENCE_FAMILY"),
        "underlying_triggers": {
            name: {"eligible": _count_trigger(eligible_all, prefix),
                  "selected": _selected_trigger_count(prefix)}
            for name, prefix in _STATE_CHANGE_TRIGGER_PREFIXES.items()
        },
    }


def _cooldown_metrics(sessions: list, sim: dict, policy: str) -> dict:
    stories_by_date = sim["stories"][policy]
    suppressed_by_date = sim["suppressed"][policy]
    total_suppressed = sum(len(v) for v in suppressed_by_date.values())
    all_stories = [s for stories in stories_by_date.values() for s in stories]
    overrides = Counter()
    for s in all_stories:
        if s["cooldown_status"].startswith("COOLDOWN_OVERRIDE_"):
            for trig in ("ATTENTION_ESCALATION", "NEW_EVIDENCE_FAMILY", "DIRECTION_TRANSITION"):
                if trig in s["cooldown_status"]:
                    overrides[trig] += 1

    fewer_than_5_due_to_cooldown = 0
    for session_date, records in sessions:
        pool = ep.eligible_pool(records)
        selected_n = len(stories_by_date.get(session_date, []))
        if len(pool) >= ep.MAX_STORIES_PER_SESSION and selected_n < ep.MAX_STORIES_PER_SESSION:
            fewer_than_5_due_to_cooldown += 1

    return {"total_suppressed": total_suppressed, "override_counts": dict(overrides),
           "sessions_with_fewer_than_5_due_to_cooldown": fewer_than_5_due_to_cooldown}


def policy_report(policy: str, sessions: list, sim: dict) -> dict:
    session_dates = [d for d, _ in sessions]
    stories_by_date = sim["stories"][policy]
    all_stories = [s for d in session_dates for s in stories_by_date.get(d, [])]
    counts = _story_counts_per_session(stories_by_date, session_dates)
    n = len(counts)

    report = {
        "policy": policy,
        "total_stories": len(all_stories),
        "stories_per_session": {
            "min": min(counts) if n else None, "max": max(counts) if n else None,
            "median": statistics.median(counts) if n else None,
            "mean": (sum(counts) / n) if n else None,
        },
        "session_distribution": _session_distribution(counts),
        "three_family_capture": _three_family_capture(sessions, stories_by_date),
        "state_change_capture": _state_change_capture(sessions, stories_by_date),
        "quiet_discovery_preservation": _quiet_preservation(all_stories),
        "top_mover_differentiation": _top_mover_differentiation(all_stories),
        "direction_distribution": _direction_distribution(all_stories, stories_by_date, session_dates),
        "publication_recurrence": _recurrence(all_stories),
        "novelty_composition": _novelty_composition(all_stories),
    }
    if policy in sim["suppressed"]:
        report["cooldown"] = _cooldown_metrics(sessions, sim, policy)
    return report


# ------------------------------------------------------------------ cross-policy
def _story_keys(stories_by_date: dict) -> set:
    return {(d, s["symbol"]) for d, stories in stories_by_date.items() for s in stories}


def policy_overlap(sim: dict, policy_a: str, policy_b: str) -> dict:
    keys_a = _story_keys(sim["stories"][policy_a])
    keys_b = _story_keys(sim["stories"][policy_b])
    intersection = keys_a & keys_b
    union = keys_a | keys_b
    return {"a": policy_a, "b": policy_b, "a_count": len(keys_a), "b_count": len(keys_b),
           "intersection": len(intersection), "union": len(union),
           "jaccard_overlap_pct": _pct(len(intersection), len(union))}


def representative_sessions(sessions: list) -> dict:
    """Deterministic min/median-ish/max novel-count sessions (packet spec section 22) - pool
    size IS the novel-development count for that session (CONTINUATION already excluded)."""
    sizes = [(d, len(ep.eligible_pool(records))) for d, records in sessions]
    min_session = min(sizes, key=lambda ds: (ds[1], ds[0]))
    max_session = max(sizes, key=lambda ds: (ds[1], ds[0]))
    median_value = statistics.median(s for _d, s in sizes)
    median_session = min(sizes, key=lambda ds: (abs(ds[1] - median_value), ds[0]))
    return {"minimum": {"session_date": min_session[0].isoformat(), "novel_count": min_session[1]},
           "median": {"session_date": median_session[0].isoformat(), "novel_count": median_session[1]},
           "maximum": {"session_date": max_session[0].isoformat(), "novel_count": max_session[1]}}


def representative_session_selections(sessions: list, sim: dict, rep: dict) -> dict:
    out = {}
    for label, info in rep.items():
        d = dt.date.fromisoformat(info["session_date"])
        out[label] = {"session_date": info["session_date"], "novel_count": info["novel_count"],
                     "by_policy": {p: sim["stories"][p].get(d, []) for p in ep.ALL_POLICIES}}
    return out


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


# ------------------------------------------------------------------ full artifact
def build_artifact(sessions: list, *, generated_at: dt.datetime | None = None) -> dict:
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc)
    session_dates = [d for d, _ in sessions]
    starting_pool = sum(len(ep.eligible_pool(records)) for _d, records in sessions)

    sim = ep.simulate_history(sessions)

    policies = {p: policy_report(p, sessions, sim) for p in ep.ALL_POLICIES}
    overlaps = [policy_overlap(sim, ep.POLICY_EVIDENCE_BREADTH, ep.POLICY_STATE_CHANGE_FIRST),
               policy_overlap(sim, ep.POLICY_STATE_CHANGE_FIRST, ep.POLICY_STATE_CHANGE_WITH_COOLDOWN),
               policy_overlap(sim, ep.POLICY_STATE_CHANGE_WITH_COOLDOWN, ep.POLICY_DIVERSITY_AWARE)]

    rep = representative_sessions(sessions)
    rep_selections = representative_session_selections(sessions, sim, rep)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "configuration": {
            "max_stories_per_session": ep.MAX_STORIES_PER_SESSION,
            "publication_cooldown_sessions": ep.PUBLICATION_COOLDOWN_SESSIONS,
            "cooldown_override_triggers": ["ATTENTION_ESCALATION", "NEW_EVIDENCE_FAMILY",
                                          "DIRECTION_TRANSITION"],
        },
        "policy_definitions": {
            ep.POLICY_EVIDENCE_BREADTH: "3-family candidates, then 2-family candidates; symbol ASC within each.",
            ep.POLICY_STATE_CHANGE_FIRST: "state-change x 3-family, state-change x 2-family, "
                                         "NEW_CANDIDATE x 3-family, NEW_CANDIDATE x 2-family; symbol ASC within each.",
            ep.POLICY_STATE_CHANGE_WITH_COOLDOWN: "Policy B ordering, symbols published by THIS "
                                                 "policy within the prior 3 trading sessions suppressed "
                                                 "unless ATTENTION_ESCALATION/NEW_EVIDENCE_FAMILY/"
                                                 "DIRECTION_TRANSITION present.",
            ep.POLICY_DIVERSITY_AWARE: "Policy C's cooldown-filtered/ordered pool (own independent "
                                      "history), then first eligible ALIGNED_POSITIVE, first eligible "
                                      "ALIGNED_NEGATIVE, then remaining slots in base order.",
        },
        "metadata": {"sessions_analyzed": len(sessions), "session_dates": [d.isoformat() for d in session_dates],
                    "starting_novel_pool": starting_pool},
        "policies": policies,
        "policy_overlap": overlaps,
        "representative_sessions": rep,
        "representative_session_selections": {
            label: {"session_date": v["session_date"], "novel_count": v["novel_count"],
                   "by_policy": {p: v["by_policy"][p] for p in ep.ALL_POLICIES}}
            for label, v in rep_selections.items()
        },
        "warnings": [],
    }


def save_artifact(artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"editorial_policy_simulation_{end_date}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, default=str)
    return path


def render_markdown(artifact: dict) -> str:
    lines = ["# Editorial Policy Simulation", "", f"Generated: {artifact['generated_at']}",
            f"Sessions: {artifact['metadata']['sessions_analyzed']}, starting novel pool: "
            f"{artifact['metadata']['starting_novel_pool']}", ""]
    for name, rep in artifact["policies"].items():
        sps = rep["stories_per_session"]
        lines += [f"## {name}", "",
                 f"Total stories: {rep['total_stories']}; min/mean/median/max per session: "
                 f"{sps['min']}/{sps['mean']:.2f}/{sps['median']}/{sps['max']}" if sps["mean"] is not None
                 else "no sessions", f"Distribution: {rep['session_distribution']}",
                 f"3-family capture: {rep['three_family_capture']}",
                 f"Quiet preservation: {rep['quiet_discovery_preservation']}",
                 f"Top-mover differentiation: {rep['top_mover_differentiation']}",
                 f"Direction: {rep['direction_distribution']}",
                 f"Recurrence: {rep['publication_recurrence']['buckets']}, "
                 f"max {rep['publication_recurrence']['max_appearances']}", ""]
        if "cooldown" in rep:
            lines += [f"Cooldown: {rep['cooldown']}", ""]
    lines += ["## Policy overlap", ""]
    for o in artifact["policy_overlap"]:
        lines.append(f"- {o['a']} vs {o['b']}: {o['jaccard_overlap_pct']}% "
                    f"(intersection {o['intersection']}/{o['union']})")
    lines += ["", "## Representative sessions", "", str(artifact["representative_sessions"])]
    return "\n".join(lines)


def save_markdown(text: str, artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"editorial_policy_simulation_{end_date}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4.2 Packet 5.4B editorial policy "
                                                 "simulation over an existing novelty artifact.")
    parser.add_argument("--novelty-artifact", required=True)
    args = parser.parse_args(argv)

    novelty_artifact = load_novelty_artifact(args.novelty_artifact)
    sessions = sessions_from_novelty_artifact(novelty_artifact)
    artifact = build_artifact(sessions)
    json_path = save_artifact(artifact)
    md_path = save_markdown(render_markdown(artifact), artifact)
    print(f"[radar.editorial_policy_validation] artifacts written:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load_novelty_artifact", "sessions_from_novelty_artifact", "policy_report",
          "policy_overlap", "representative_sessions", "build_artifact", "save_artifact",
          "render_markdown", "save_markdown", "main"]
