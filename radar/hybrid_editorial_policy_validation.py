"""Phase 4.2 Packet 5.4B.1: historical validation of the hybrid (reservation) editorial
policies against the exact Packet 5.4B input (20 sessions, 561 raw candidates, 420 novel
records). Compares Policy C/D (`radar.editorial_policy`, unmodified) against Policy E/F
(`radar.editorial_policy_hybrid`, new) on the identical historical dataset. Analysis only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter

from config import OUT_DIR

from . import editorial_policy as ep
from . import editorial_policy_hybrid as eph
from . import editorial_policy_validation as epv

SCHEMA_VERSION = "1.0"
ARTIFACT_DIR = os.path.join(OUT_DIR, "radar_validation")


# ------------------------------------------------------------------ per-policy report (reuse epv helpers)
def policy_report(policy: str, sessions: list, stories_by_date: dict, suppressed_by_date: dict | None) -> dict:
    session_dates = [d for d, _ in sessions]
    all_stories = [s for d in session_dates for s in stories_by_date.get(d, [])]
    counts = epv._story_counts_per_session(stories_by_date, session_dates)
    n = len(counts)
    import statistics
    report = {
        "policy": policy,
        "total_stories": len(all_stories),
        "stories_per_session": {
            "min": min(counts) if n else None, "max": max(counts) if n else None,
            "median": statistics.median(counts) if n else None,
            "mean": (sum(counts) / n) if n else None,
        },
        "session_distribution": epv._session_distribution(counts),
        "three_family_capture": epv._three_family_capture(sessions, stories_by_date),
        "state_change_capture": epv._state_change_capture(sessions, stories_by_date),
        "quiet_discovery_preservation": epv._quiet_preservation(all_stories),
        "top_mover_differentiation": epv._top_mover_differentiation(all_stories),
        "direction_distribution": epv._direction_distribution(all_stories, stories_by_date, session_dates),
        "publication_recurrence": epv._recurrence(all_stories),
        "novelty_composition": epv._novelty_composition(all_stories),
    }
    if suppressed_by_date is not None:
        report["cooldown"] = _cooldown_metrics(sessions, stories_by_date, suppressed_by_date)
    return report


def _cooldown_metrics(sessions: list, stories_by_date: dict, suppressed_by_date: dict) -> dict:
    total_suppressed = sum(len(v) for v in suppressed_by_date.values())
    all_stories = [s for stories in stories_by_date.values() for s in stories]
    overrides = Counter()
    for s in all_stories:
        if s["cooldown_status"].startswith("COOLDOWN_OVERRIDE_"):
            for trig in ("ATTENTION_ESCALATION", "NEW_EVIDENCE_FAMILY", "DIRECTION_TRANSITION"):
                if trig in s["cooldown_status"]:
                    overrides[trig] += 1
    fewer_than_5 = 0
    for session_date, records in sessions:
        pool = ep.eligible_pool(records)
        if len(pool) >= ep.MAX_STORIES_PER_SESSION and len(stories_by_date.get(session_date, [])) < ep.MAX_STORIES_PER_SESSION:
            fewer_than_5 += 1
    return {"total_suppressed": total_suppressed, "override_counts": dict(overrides),
           "sessions_with_fewer_than_5_due_to_cooldown": fewer_than_5}


# ------------------------------------------------------------------ 3-family / reservation
def three_family_session_breakdown(sessions: list) -> dict:
    zero = one = two_plus = 0
    for _d, records in sessions:
        n = sum(1 for r in ep.eligible_pool(records) if r["independent_signal_count"] == 3)
        if n == 0:
            zero += 1
        elif n == 1:
            one += 1
        else:
            two_plus += 1
    return {"sessions_with_0_eligible_3family": zero, "sessions_with_1_eligible_3family": one,
           "sessions_with_2plus_eligible_3family": two_plus}


def reservation_activation(sim_hybrid: dict, policy: str) -> dict:
    reservation_by_date = sim_hybrid["reservation"][policy]
    zero = one = two = 0
    reserved_state_change = reserved_new_candidate = 0
    for session_date, stories in sim_hybrid["stories"][policy].items():
        reserved = [s for s in stories if s["reserved_3family"]]
        n = len(reserved)
        if n == 0:
            zero += 1
        elif n == 1:
            one += 1
        else:
            two += 1
        for s in reserved:
            if s["selection_bucket"] == "RESERVED_3FAMILY_STATE_CHANGE":
                reserved_state_change += 1
            else:
                reserved_new_candidate += 1
    total_reserved = reserved_state_change + reserved_new_candidate
    return {"sessions_reserved_0": zero, "sessions_reserved_1": one, "sessions_reserved_2": two,
           "total_reserved_selections": total_reserved,
           "reserved_state_change_3family": reserved_state_change,
           "reserved_new_candidate_3family": reserved_new_candidate}


# ------------------------------------------------------------------ displacement analysis
def displacement_analysis(sessions: list, stories_hybrid_by_date: dict, stories_baseline_by_date: dict) -> dict:
    """For every session, symbols selected by the hybrid policy but not the baseline
    ("added") versus symbols selected by the baseline but not the hybrid policy ("removed"),
    paired deterministically (`symbol ASC` on each side, zipped)."""
    pairs = []
    for session_date, _records in sessions:
        hybrid_stories = {s["symbol"]: s for s in stories_hybrid_by_date.get(session_date, [])}
        baseline_stories = {s["symbol"]: s for s in stories_baseline_by_date.get(session_date, [])}
        added_symbols = sorted(set(hybrid_stories) - set(baseline_stories))
        removed_symbols = sorted(set(baseline_stories) - set(hybrid_stories))
        for added_sym, removed_sym in zip(added_symbols, removed_symbols):
            a, r = hybrid_stories[added_sym], baseline_stories[removed_sym]
            pairs.append({
                "session_date": session_date.isoformat(),
                "added_symbol": a["symbol"], "added_families": a["independent_signal_count"],
                "added_novelty_type": a["novelty_type"],
                "removed_symbol": r["symbol"], "removed_families": r["independent_signal_count"],
                "removed_novelty_type": r["novelty_type"],
            })
        # Unequal-length leftovers (rare - different total selected counts): record unpaired.
        for extra in added_symbols[len(removed_symbols):]:
            a = hybrid_stories[extra]
            pairs.append({"session_date": session_date.isoformat(), "added_symbol": a["symbol"],
                         "added_families": a["independent_signal_count"],
                         "added_novelty_type": a["novelty_type"],
                         "removed_symbol": None, "removed_families": None, "removed_novelty_type": None})
        for extra in removed_symbols[len(added_symbols):]:
            r = baseline_stories[extra]
            pairs.append({"session_date": session_date.isoformat(), "added_symbol": None,
                         "added_families": None, "added_novelty_type": None,
                         "removed_symbol": r["symbol"], "removed_families": r["independent_signal_count"],
                         "removed_novelty_type": r["novelty_type"]})

    state_change_types = {"MULTIPLE_CHANGES", "NEW_EVIDENCE_FAMILY", "NEW_TECHNICAL_EVENT",
                          "PERSISTENCE_TRANSITION", "DIRECTION_TRANSITION", "ATTENTION_ESCALATION"}
    aggregate = {
        "number_of_swaps": len(pairs),
        "3family_added": sum(1 for p in pairs if p["added_families"] == 3),
        "2family_removed": sum(1 for p in pairs if p["removed_families"] == 2),
        "state_change_added": sum(1 for p in pairs if p["added_novelty_type"] in state_change_types),
        "state_change_removed": sum(1 for p in pairs if p["removed_novelty_type"] in state_change_types),
        "new_candidate_added": sum(1 for p in pairs if p["added_novelty_type"] == "NEW_CANDIDATE"),
        "new_candidate_removed": sum(1 for p in pairs if p["removed_novelty_type"] == "NEW_CANDIDATE"),
    }
    sessions_with_any_swap = len({p["session_date"] for p in pairs})
    return {"pairs": pairs, "aggregate": aggregate, "sessions_with_any_swap": sessions_with_any_swap}


# ------------------------------------------------------------------ F-specific diversity impact
def f_diversity_impact(sessions: list, sim_hybrid: dict) -> dict:
    diag_by_date = sim_hybrid["diversity_diagnostics"]
    e_stories = sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED]
    f_stories = sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY]

    altered_nonreserved = 0
    capacity_prevented_both = 0
    for session_date, _records in sessions:
        e_fill = {s["symbol"] for s in e_stories.get(session_date, []) if not s["reserved_3family"]}
        f_fill = {s["symbol"] for s in f_stories.get(session_date, []) if not s["reserved_3family"]}
        if e_fill != f_fill:
            altered_nonreserved += 1
        diag = diag_by_date.get(session_date, {})
        if diag.get("both_directions_available_but_capacity_prevented_one"):
            capacity_prevented_both += 1
    return {"sessions_where_diversity_altered_a_nonreserved_slot": altered_nonreserved,
           "sessions_where_capacity_prevented_both_directions": capacity_prevented_both}


# ------------------------------------------------------------------ overlap / representative sessions
def _story_keys(stories_by_date: dict) -> set:
    return {(d, s["symbol"]) for d, stories in stories_by_date.items() for s in stories}


def overlap(stories_a_by_date: dict, stories_b_by_date: dict, label_a: str, label_b: str) -> dict:
    keys_a, keys_b = _story_keys(stories_a_by_date), _story_keys(stories_b_by_date)
    inter, union = keys_a & keys_b, keys_a | keys_b
    return {"a": label_a, "b": label_b, "a_count": len(keys_a), "b_count": len(keys_b),
           "intersection": len(inter), "union": len(union),
           "jaccard_overlap_pct": epv._pct(len(inter), len(union))}


def representative_sessions(sessions: list, displacement_e_vs_c: dict) -> dict:
    sizes = [(d, sum(1 for r in ep.eligible_pool(records) if r["independent_signal_count"] == 3))
            for d, records in sessions]
    zero = next((d for d, n in sizes if n == 0), None)
    one = next((d for d, n in sizes if n == 1), None)
    two_plus = min((ds for ds in sizes if ds[1] >= 2), key=lambda ds: (-ds[1], ds[0]), default=None)

    swap_counts = Counter(p["session_date"] for p in displacement_e_vs_c["pairs"])
    max_displacement_date = max(swap_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if swap_counts else None

    out = {}
    if zero is not None:
        out["zero_eligible_3family"] = {"session_date": zero.isoformat(), "eligible_3family": 0}
    if one is not None:
        out["one_eligible_3family"] = {"session_date": one.isoformat(), "eligible_3family": 1}
    if two_plus is not None:
        out["two_plus_eligible_3family"] = {"session_date": two_plus[0].isoformat(),
                                            "eligible_3family": two_plus[1]}
    if max_displacement_date is not None:
        out["max_displacement_e_vs_c"] = {"session_date": max_displacement_date,
                                          "swap_count": swap_counts[max_displacement_date]}
    return out


def session_selections(sessions: list, sim_cd: dict, sim_hybrid: dict, session_date: dt.date) -> dict:
    def _summ(stories):
        return [{"symbol": s["symbol"], "families": s["independent_signal_count"],
                "novelty_type": s["novelty_type"], "direction": s["direction_compatibility"],
                "price_change_pct": s["price_change_pct"], "selection_bucket": s["selection_bucket"],
                "reserved_3family": s.get("reserved_3family"), "cooldown_status": s["cooldown_status"]}
               for s in stories]
    return {"session_date": session_date.isoformat(),
           "C": _summ(sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN].get(session_date, [])),
           "D": _summ(sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE].get(session_date, [])),
           "E": _summ(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED].get(session_date, [])),
           "F": _summ(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY].get(session_date, []))}


# ------------------------------------------------------------------ full artifact
def build_artifact(sessions: list, *, generated_at: dt.datetime | None = None) -> dict:
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc)
    session_dates = [d for d, _ in sessions]
    starting_pool = sum(len(ep.eligible_pool(records)) for _d, records in sessions)

    sim_cd = ep.simulate_history(sessions)
    sim_hybrid = eph.simulate_hybrid_history(sessions)

    reports = {
        ep.POLICY_STATE_CHANGE_WITH_COOLDOWN: policy_report(
            ep.POLICY_STATE_CHANGE_WITH_COOLDOWN, sessions,
            sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
            sim_cd["suppressed"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN]),
        ep.POLICY_DIVERSITY_AWARE: policy_report(
            ep.POLICY_DIVERSITY_AWARE, sessions, sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
            sim_cd["suppressed"][ep.POLICY_DIVERSITY_AWARE]),
        eph.POLICY_HYBRID_RESERVED: policy_report(
            eph.POLICY_HYBRID_RESERVED, sessions, sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
            sim_hybrid["suppressed"][eph.POLICY_HYBRID_RESERVED]),
        eph.POLICY_HYBRID_RESERVED_DIVERSITY: policy_report(
            eph.POLICY_HYBRID_RESERVED_DIVERSITY, sessions,
            sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
            sim_hybrid["suppressed"][eph.POLICY_HYBRID_RESERVED_DIVERSITY]),
    }

    reservation = {p: reservation_activation(sim_hybrid, p) for p in eph.ALL_HYBRID_POLICIES}

    disp_e_vs_c = displacement_analysis(sessions, sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
                                        sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN])
    disp_f_vs_d = displacement_analysis(sessions, sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
                                        sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE])

    f_diversity = f_diversity_impact(sessions, sim_hybrid)

    overlaps = [
        overlap(sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
               sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
               ep.POLICY_STATE_CHANGE_WITH_COOLDOWN, eph.POLICY_HYBRID_RESERVED),
        overlap(sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
               sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
               ep.POLICY_DIVERSITY_AWARE, eph.POLICY_HYBRID_RESERVED_DIVERSITY),
        overlap(sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED],
               sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY],
               eph.POLICY_HYBRID_RESERVED, eph.POLICY_HYBRID_RESERVED_DIVERSITY),
        overlap(sim_cd["stories"][ep.POLICY_STATE_CHANGE_WITH_COOLDOWN],
               sim_cd["stories"][ep.POLICY_DIVERSITY_AWARE],
               ep.POLICY_STATE_CHANGE_WITH_COOLDOWN, ep.POLICY_DIVERSITY_AWARE),
    ]

    rep = representative_sessions(sessions, disp_e_vs_c)
    rep_selections = {label: session_selections(sessions, sim_cd, sim_hybrid, dt.date.fromisoformat(v["session_date"]))
                      for label, v in rep.items()}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "configuration": {
            "max_stories_per_session": ep.MAX_STORIES_PER_SESSION,
            "publication_cooldown_sessions": ep.PUBLICATION_COOLDOWN_SESSIONS,
            "max_reserved_3family_slots": eph.MAX_RESERVED_3FAMILY_SLOTS,
        },
        "policy_definitions": {
            eph.POLICY_HYBRID_RESERVED: "Policy C ordering/cooldown, then reserve up to 2 "
                                       "eligible 3-family candidates (state-change before "
                                       "NEW_CANDIDATE, symbol ASC), fill remainder in Policy C order.",
            eph.POLICY_HYBRID_RESERVED_DIVERSITY: "Policy E's reservation (own independent "
                                                 "cooldown history), then Policy D's diversity "
                                                 "fill applied only to the remaining capacity - "
                                                 "reserved candidates are never evicted.",
        },
        "metadata": {"sessions_analyzed": len(sessions), "session_dates": [d.isoformat() for d in session_dates],
                    "starting_novel_pool": starting_pool,
                    "three_family_session_breakdown": three_family_session_breakdown(sessions)},
        "policies": reports,
        "reservation_activation": reservation,
        "displacement_e_vs_c": disp_e_vs_c,
        "displacement_f_vs_d": disp_f_vs_d,
        "f_diversity_impact": f_diversity,
        "policy_overlap": overlaps,
        "representative_sessions": rep,
        "representative_session_selections": rep_selections,
        "warnings": [],
    }


def save_artifact(artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"hybrid_editorial_policy_simulation_{end_date}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, ensure_ascii=False, default=str)
    return path


def render_markdown(artifact: dict) -> str:
    lines = ["# Hybrid Editorial Policy Simulation (E/F vs C/D)", "",
            f"Generated: {artifact['generated_at']}",
            f"Sessions: {artifact['metadata']['sessions_analyzed']}, starting novel pool: "
            f"{artifact['metadata']['starting_novel_pool']}",
            f"3-family session breakdown: {artifact['metadata']['three_family_session_breakdown']}", ""]
    for name, r in artifact["policies"].items():
        sps = r["stories_per_session"]
        lines += [f"## {name}", "",
                 f"Total: {r['total_stories']}; min/mean/median/max: {sps['min']}/"
                 f"{sps['mean']:.2f}/{sps['median']}/{sps['max']}" if sps["mean"] is not None else "none",
                 f"3-family capture: {r['three_family_capture']}",
                 f"Quiet: {r['quiet_discovery_preservation']}",
                 f"Top-mover: {r['top_mover_differentiation']}", ""]
    lines += ["## Reservation activation", "", str(artifact["reservation_activation"]), "",
             "## Displacement E vs C", "", str(artifact["displacement_e_vs_c"]["aggregate"]), "",
             "## Displacement F vs D", "", str(artifact["displacement_f_vs_d"]["aggregate"]), "",
             "## F diversity impact", "", str(artifact["f_diversity_impact"]), "",
             "## Policy overlap", ""]
    for o in artifact["policy_overlap"]:
        lines.append(f"- {o['a']} vs {o['b']}: {o['jaccard_overlap_pct']}% "
                    f"(intersection {o['intersection']}/{o['union']})")
    lines += ["", "## Representative sessions", "", str(artifact["representative_sessions"])]
    return "\n".join(lines)


def save_markdown(text: str, artifact: dict, out_dir: str = ARTIFACT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    end_date = artifact["metadata"]["session_dates"][-1] if artifact["metadata"]["session_dates"] else "unknown"
    path = os.path.join(out_dir, f"hybrid_editorial_policy_simulation_{end_date}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4.2 Packet 5.4B.1 hybrid editorial "
                                                 "policy simulation over an existing novelty artifact.")
    parser.add_argument("--novelty-artifact", required=True)
    args = parser.parse_args(argv)

    novelty_artifact = epv.load_novelty_artifact(args.novelty_artifact)
    sessions = epv.sessions_from_novelty_artifact(novelty_artifact)
    artifact = build_artifact(sessions)
    json_path = save_artifact(artifact)
    md_path = save_markdown(render_markdown(artifact), artifact)
    print(f"[radar.hybrid_editorial_policy_validation] artifacts written:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["policy_report", "reservation_activation", "displacement_analysis",
          "f_diversity_impact", "overlap", "representative_sessions", "build_artifact",
          "save_artifact", "render_markdown", "save_markdown", "main"]
