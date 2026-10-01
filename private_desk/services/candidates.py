"""Radar candidates as the desk shows them: recorded state + Radar novelty + recorded appearance
history + the detector evidence behind each reason code (WHY THIS STOCK).

Sources, in order of authority:
1. `radar_candidate_history` (what the evening run recorded) - the candidate SET and its reason
   codes, families, attention, direction and persistence. The desk never adds or drops one.
2. `radar.novelty.classify_history` over that recorded history (the pipeline's own function, fed
   the pipeline's own `_reconstruct_candidate` shape) - NEW_CANDIDATE / CONTINUATION / ... .
3. The detector replay (`private_desk.replay`) - the VALUES behind each code, shown only when
   the replay reproduced exactly the recorded reason codes (MATCHED).
4. Editorial selections (which candidates became the session's stories) and the official
   exchange snapshots - context only.

Nothing here scores, ranks or recommends. Table order is the recorded order (by attention, then
symbol) - not a ranking of merit.
"""
from __future__ import annotations

import datetime as dt

from .. import replay as rp
from ..repository import OFFICIAL_KINDS, DeskRepository
from . import rules

FIRST_RECORDED, CONSECUTIVE, REAPPEARED = "FIRST_RECORDED", "CONSECUTIVE", "REAPPEARED"
_ATTENTION_ORDER = {"HIGH_INTEREST": 0, "NOTABLE": 1}


# ------------------------------------------------------------------ session spine
def session_spine(repo: DeskRepository, end: dt.date) -> list:
    """Canonical session list ending at `end` (benchmark bars from the store + NSE calendar) -
    the same `radar.session_alignment.canonical_session_list` the pipeline uses."""
    bench = repo.benchmark_series(end)
    if bench:
        return rp._spine(bench, end)
    from core.trading_calendar import SessionCalendar
    return SessionCalendar().sessions_between(end - dt.timedelta(days=400), end)


# ------------------------------------------------------------------ novelty + appearance
def classify_recorded(spine: list, rows: list) -> dict:
    """`{(session, symbol): CandidateNovelty}` for every recorded row, via `radar.novelty`."""
    from radar.daily_pipeline import _reconstruct_candidate
    from radar.novelty import classify_history
    if not rows:
        return {}
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r.session_date, []).append(r)
    first, last = min(by_date), max(by_date)
    sessions = [(d, [_reconstruct_candidate(r) for r in by_date.get(d, [])])
                for d in spine if first <= d <= last]
    out = {}
    for d, novelties in classify_history(sessions).items():
        for n in novelties:
            out[(d, n.instrument)] = n
    return out


def appearance(spine: list, history_dates: list, session: dt.date) -> dict:
    """Recorded-history appearance of one symbol at `session` (desk-derived, documented rule)."""
    prior = [d for d in history_dates if d < session]
    count = sum(1 for d in history_dates if d <= session)
    if not prior:
        return {"label": FIRST_RECORDED, "prior": None, "sessions_since": None, "count": count}
    p = prior[-1]
    since = None
    if p in spine and session in spine:
        since = spine.index(session) - spine.index(p)
    label = CONSECUTIVE if since == 1 else REAPPEARED
    return {"label": label, "prior": p, "sessions_since": since, "count": count}


# ------------------------------------------------------------------ official events
def official_index(repo: DeskRepository, session: dt.date) -> dict:
    """`{symbol: [event, ...]}` from the session's persisted official snapshots, plus status."""
    index: dict = {}
    status = {}
    for kind in OFFICIAL_KINDS:
        snap = repo.official_snapshot(session, kind)
        if snap is None:
            status[kind] = {"status": "UNAVAILABLE"}
            continue
        status[kind] = {"status": snap.status, "source_date": snap.source_date.isoformat()
                        if hasattr(snap.source_date, "isoformat") else snap.source_date,
                        "records": len(snap.records), "source_reference": snap.source_reference,
                        "retrieved_at": str(snap.retrieved_at)}
        for rec in snap.records:
            sym = rec.get("symbol")
            if not sym:
                continue
            index.setdefault(sym, []).append({
                "kind": kind, "status": rec.get("status"),
                "detail": rec.get("detail") or rec.get("board_type") or "",
                "list": rec.get("list"), "row_date": rec.get("row_date") or rec.get("data_as_of"),
                "source_name": snap.source_name, "source_reference": snap.source_reference,
                "source_date": status[kind]["source_date"],
                "issue_open_date": rec.get("issue_open_date"),
                "issue_close_date": rec.get("issue_close_date"),
                "listing_date": rec.get("listing_date"),
            })
    return {"by_symbol": index, "status": status}


# ------------------------------------------------------------------ WHY THIS STOCK
def _event_for(code: str, symbol_replay: dict | None):
    if not symbol_replay:
        return None
    want = code[len("STRUCTURE_"):]
    for e in (symbol_replay.get("technical") or {}).get("events") or []:
        if e.get("event_type") == want:
            return e
    return None


def why_items(cand, replay_status: str, sym_replay: dict | None, replay_cand: dict | None) -> list:
    """One item per recorded reason code: the rule, the actual values, and whether those values
    come from a reconciled replay. No item is invented - a code without values says so."""
    matched = replay_status == rp.MATCHED
    evidence_lines = (replay_cand or {}).get("evidence") or []
    items = []
    for code in cand.reason_codes:
        family = rules.family_of(code)
        item = {"code": code, "family": family, "rule": rules.reason_rule(code),
                "values": {}, "text": "", "verified": matched}
        if not matched:
            item["text"] = (f"Recorded by the evening Radar run; evidence values unavailable - "
                            f"not reproduced by the detector replay ({replay_status}).")
        elif family == "VOLUME":
            item["values"] = {"relative_volume": sym_replay.get("relative_volume"),
                              "session_volume": sym_replay.get("current_volume"),
                              "prior20_avg_volume": sym_replay.get("prior20_avg_volume")}
            item["text"] = next((l for l in evidence_lines if l.startswith("Volume")), "")
        elif family == "STRUCTURE":
            ev = _event_for(code, sym_replay) or {}
            item["values"] = dict(ev.get("evidence") or {})
            item["text"] = ev.get("why", "")
        elif family == "RELATIVE_PERFORMANCE":
            rel = sym_replay.get("relative") or {}
            item["values"] = {"vs_nifty_5d_pp": rel.get("market_relative_5d_pp"),
                              "vs_nifty_20d_pp": rel.get("market_relative_20d_pp")}
            item["text"] = next((l for l in evidence_lines if l.startswith("Persistent")), "")
        items.append(item)
    return items


# ------------------------------------------------------------------ candidate views
def _structure_label(codes) -> str:
    s = [c[len("STRUCTURE_"):] for c in codes if c.startswith("STRUCTURE_")]
    return ", ".join(s)


def build_candidate_views(repo: DeskRepository, session: dt.date, *, replay: dict,
                          spine: list | None = None, history_rows: list | None = None,
                          official: dict | None = None) -> list:
    spine = spine or session_spine(repo, session)
    stored = repo.candidates(session)
    if history_rows is None:
        history_rows = repo.candidates_between(dt.date(1900, 1, 1), session)
    novelty = classify_recorded(spine, history_rows)
    dates_by_symbol: dict = {}
    for r in history_rows:
        dates_by_symbol.setdefault(r.instrument, []).append(r.session_date)
    universe, uni_session = repo.universe(session)
    selections = repo.selections(session)
    published = {p["instrument"] for p in repo.publications(session)}
    recon = rp.reconcile(replay if replay.get("status") == "OK" else None, stored)
    official = official if official is not None else official_index(repo, session)
    symbols_replay = replay.get("symbols") or {}
    cands_replay = replay.get("candidates") or {}

    views = []
    for c in stored:
        meta = universe.get(c.instrument) or {}
        sym_r = symbols_replay.get(c.instrument)
        n = novelty.get((session, c.instrument))
        sel = selections.get(c.instrument)
        status = recon.get(c.instrument, rp.NOT_REPLAYED) if replay.get("status") == "OK" \
            else rp.NOT_REPLAYED
        matched = status == rp.MATCHED
        rel = (sym_r or {}).get("relative") or {}
        app = appearance(spine, sorted(dates_by_symbol.get(c.instrument, [])), session)
        views.append({
            "symbol": c.instrument, "company": meta.get("company"), "sector": meta.get("sector"),
            "industry": meta.get("industry"), "universe_session": uni_session,
            "session": session, "price_change_pct": c.price_change_pct,
            "attention_level": c.attention_level, "families": list(c.active_families),
            "reason_codes": list(c.reason_codes), "signal_count": c.independent_signal_count,
            "direction": c.direction_compatibility, "persistence": c.persistence_state,
            "structure": _structure_label(c.reason_codes),
            "unusual_volume": "VOLUME" in c.active_families,
            "novelty_type": n.novelty_type.value if n else None,
            "novelty_reason": n.reason if n else "",
            "novelty_codes": list(n.change_reason_codes) if n else [],
            "new_technical_events": list(n.new_technical_events) if n else [],
            "new_families": list(n.new_families) if n else [],
            "appearance": app["label"], "prior_appearance": app["prior"],
            "sessions_since_prior": app["sessions_since"], "appearances": app["count"],
            "selected": sel is not None,
            "selection_bucket": (sel or {}).get("selection_bucket"),
            "selection_reason": (sel or {}).get("selection_reason"),
            "published": c.instrument in published,
            "replay_status": status,
            "close": (sym_r or {}).get("close") if matched else None,
            "relative_volume": (sym_r or {}).get("relative_volume") if matched else None,
            "volume_level": (((sym_r or {}).get("volume_anomaly") or {}).get("level")
                             if matched else None),
            "rel_5d_pp": rel.get("market_relative_5d_pp") if matched else None,
            "rel_20d_pp": rel.get("market_relative_20d_pp") if matched else None,
            "why": why_items(c, status, sym_r, cands_replay.get(c.instrument)),
            "official": official["by_symbol"].get(c.instrument, []),
        })
    views.sort(key=lambda v: (_ATTENTION_ORDER.get(v["attention_level"], 9), v["symbol"]))
    return views


def left_radar(repo: DeskRepository, session: dt.date, spine: list) -> list:
    """Candidates on the PREVIOUS spine session that are not candidates now ("no longer a
    candidate"). Only stated when the previous session has a COMPLETE run marker."""
    if session not in spine or spine.index(session) == 0:
        return []
    prev = spine[spine.index(session) - 1]
    if prev not in repo.radar_sessions():
        return []
    now = {c.instrument for c in repo.candidates(session)}
    return [{"symbol": c.instrument, "previous_session": prev,
             "previous_families": list(c.active_families),
             "previous_codes": list(c.reason_codes)}
            for c in repo.candidates(prev) if c.instrument not in now]


def what_changed(views: list, left: list) -> dict:
    """Groups of transitions the Radar's own novelty/appearance data supports - nothing else."""
    def pick(pred):
        return [v for v in views if pred(v)]
    groups = {
        "NEW": pick(lambda v: v["novelty_type"] == "NEW_CANDIDATE"),
        "REAPPEARED": pick(lambda v: v["appearance"] == REAPPEARED),
        "ATTENTION_ESCALATION": pick(lambda v: "ATTENTION_ESCALATION_NOTABLE_TO_HIGH_INTEREST"
                                     in v["novelty_codes"]),
        "RANGE_UP": pick(lambda v: any(e.startswith("BREAK_ABOVE") for e in v["new_technical_events"])
                         or (v["novelty_type"] == "NEW_CANDIDATE" and "BREAK_ABOVE" in v["structure"])),
        "RANGE_DOWN": pick(lambda v: any(e.startswith("BREAK_BELOW") for e in v["new_technical_events"])
                           or (v["novelty_type"] == "NEW_CANDIDATE" and "BREAK_BELOW" in v["structure"])),
        "SMA_CROSS": pick(lambda v: any(e.startswith("CROSS_") for e in v["new_technical_events"])),
        "NEW_UNUSUAL_VOLUME": pick(lambda v: "VOLUME" in v["new_families"]
                                   or (v["novelty_type"] == "NEW_CANDIDATE" and v["unusual_volume"])),
        "PERSISTENCE_TRANSITION": pick(lambda v: any(c.startswith("PERSISTENCE_TRANSITION")
                                                     for c in v["novelty_codes"])),
        "DIRECTION_TRANSITION": pick(lambda v: any(c.startswith("DIRECTION_TRANSITION")
                                                   for c in v["novelty_codes"])),
        "CONTINUATION": pick(lambda v: v["novelty_type"] == "CONTINUATION"),
    }
    groups["NO_LONGER_CANDIDATE"] = left
    return groups


CHANGE_LABELS = {
    "NEW": ("New candidate", "no candidate appearance within the Radar's novelty lookback"),
    "REAPPEARED": ("Reappeared", "earlier recorded appearance, then a gap of at least one session"),
    "ATTENTION_ESCALATION": ("Attention escalated", "NOTABLE -> HIGH_INTEREST vs prior appearance"),
    "RANGE_UP": ("Range break up", "new close above prior 20/50-session high"),
    "RANGE_DOWN": ("Range break down", "new close below prior 20/50-session low"),
    "SMA_CROSS": ("New SMA cross", "close crossed SMA20/SMA50 (new vs prior appearance)"),
    "NEW_UNUSUAL_VOLUME": ("Unusual volume (new)", "VOLUME family newly active"),
    "PERSISTENCE_TRANSITION": ("Relative persistence changed", "5D/20D vs NIFTY state changed"),
    "DIRECTION_TRANSITION": ("Evidence direction changed", "direction compatibility changed"),
    "CONTINUATION": ("Continuing, unchanged", "same recorded state as its prior appearance"),
    "NO_LONGER_CANDIDATE": ("No longer a candidate", "candidate on the previous session, not now"),
}


__all__ = ["build_candidate_views", "session_spine", "classify_recorded", "appearance",
          "official_index", "why_items", "left_radar", "what_changed", "CHANGE_LABELS",
          "FIRST_RECORDED", "CONSECUTIVE", "REAPPEARED"]
