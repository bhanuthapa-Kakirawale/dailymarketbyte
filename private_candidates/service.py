"""Orchestration: today's Radar candidates -> eligibility -> metrics -> scoring -> shortlist ->
(optionally) a durable snapshot.

The candidate universe is EXACTLY today's stored Radar candidates (`radar_candidate_history`,
read via `private_desk.repository.DeskRepository.candidates`) - never a fresh market-wide scan.
Detector values are read from `private_desk.replay.get_replay`/`reconcile` - the Radar's OWN
detector chain, re-run read-only from stored OHLCV, reconciled against what was actually
recorded - so this package never re-implements RVOL/prior-high-low/relative-performance itself
(owner spec section 1: "do NOT duplicate existing calculations"). `private_desk.replay`/
`repository` are read-only, exactly like this package's own relationship to the OTHER
subsystems it reads context from; the dependency is one-directional in the sense that matters -
nothing public ever reaches either package (test-enforced).

## Point-in-time capture contract

Every candidate assessment carries two distinct timestamps (owner hardening pass):

`candidate_as_of`
    The authoritative PIT cutoff: the latest instant whose information was eligible for this
    calculation. Every per-subsystem read (`context.py`) is bounded by it, each using that
    subsystem's OWN inclusive/exclusive convention - never a uniform wrapper.
`captured_at`
    When this assessment object was physically computed. Close to `candidate_as_of` for a live
    capture, but conceptually distinct - and can be far apart for a replay or a catch-up.

Resolution (`resolve_capture`):
  - `candidate_as_of` omitted -> defaults to `captured_at` (a genuinely LIVE run: "now" is both
    when this was computed AND the honest knowledge boundary - this is what fixed the original
    bug motivating this design, since "now" at the point the candidate engine actually runs is
    naturally later than every artifact an earlier step in the same evening's job already wrote,
    with no artificial end-of-day padding required).
  - `candidate_as_of` given explicitly -> honored EXACTLY, never silently widened (an explicit
    replay `as_of` of 19:20 must never see a 21:00 revision just because both are "the same
    calendar day" - that was the actual defect in the prior end-of-day-based cutoff).
  - `capture_mode` omitted -> inferred as POINT_IN_TIME only when `candidate_as_of`'s own
    calendar date (IST) equals `session` (an honest same-day claim); RECONSTRUCTED otherwise.
    A caller with actual provenance proof (`capture.resolve_capture_as_of`, used by the
    automatic intelligence_refresh integration) may override this explicitly in either
    direction - never fabricated silently here.
"""
from __future__ import annotations

import datetime as dt

from . import context, eligibility, metrics, policy as policy_mod, scoring
from .models import (DAY_MOVE_AGAINST_RADAR_DIRECTION, SCHEMA_VERSION, EventProximity,
                     TradeCandidateAssessment)
from .shortlist import diversified_shortlist, raw_ranking

POINT_IN_TIME = "POINT_IN_TIME"
RECONSTRUCTED = "RECONSTRUCTED"

# Board views. ALL is engine-neutral (and what capture uses); UPSIDE ranks and shortlists only
# candidates whose Radar evidence is ALIGNED_POSITIVE, listing the rest as information only.
ALL_VIEW = "ALL"
UPSIDE_VIEW = "UPSIDE"
VIEWS = (ALL_VIEW, UPSIDE_VIEW)

LIVE_RECOMPUTE = "LIVE_RECOMPUTE"
STORED_SNAPSHOT = "STORED_SNAPSHOT"


def _default_regime_dir(out_dir: str) -> str:
    from private_desk.settings import DeskSettings
    return DeskSettings.from_out_dir(out_dir).regime_dir


def resolve_capture(session: dt.date, candidate_as_of: dt.datetime | None,
                    capture_mode: str | None,
                    captured_at: dt.datetime | None) -> tuple:
    """`(candidate_as_of, capture_mode, captured_at)` - see module docstring. Pure; takes no
    clock itself so every caller's choice of "now" (or an injected one, in tests) is explicit."""
    from config import IST
    captured_at = captured_at or dt.datetime.now(IST)
    candidate_as_of = candidate_as_of or captured_at
    if capture_mode is None:
        capture_mode = (POINT_IN_TIME if candidate_as_of.astimezone(IST).date() == session
                        else RECONSTRUCTED)
    return candidate_as_of, capture_mode, captured_at


def _ist_cutoff(candidate_as_of: dt.datetime) -> dt.datetime:
    """Market Structure, Market Events and institutional-flow stores compare cutoff STRINGS
    lexically against IST-offset `retrieved_at` values, so the cutoff must be IST too - a UTC
    string for the same instant sorts 5h30m early and hides that evening's revisions."""
    from config import IST
    return candidate_as_of.astimezone(IST)


def _nearest_event(events: list, session: dt.date, calendar) -> tuple:
    """`(sessions_to_event, event_family)` for the nearest event by trading-session distance,
    or `(None, None)`."""
    best = None
    for ev in events:
        raw = getattr(ev, "data_as_of", None)
        if not raw:
            continue
        try:
            event_date = dt.date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        distance = metrics.trading_session_distance(calendar, session, event_date)
        if best is None or abs(distance) < abs(best[0]):
            best = (distance, getattr(ev, "family", None))
    return best if best is not None else (None, None)


def _evidence_summary(components: tuple) -> tuple:
    positive, risk = [], []
    for c in components:
        if c.status == "SCORED" and c.max_score > 0 and c.score >= c.max_score * 0.6 and c.evidence:
            positive.append(f"{c.component}: {c.evidence[0]}")
        elif c.status == "NOT_SCORABLE" or (c.max_score > 0 and c.score < c.max_score * 0.3):
            risk.append(f"{c.component}: {c.reason or (c.evidence[0] if c.evidence else '')}")
    return tuple(positive), tuple(risk)


def assess_candidate(repo, symbol: str, stored, *, replay: dict, reconcile_status: str,
                     session: dt.date, candidate_as_of: dt.datetime, sector_map: dict,
                     members_by_sector: dict, nsdl_by_sector: dict, regime_snaps: list,
                     benchmark_series: list, calendar, universe_company: dict,
                     policy: policy_mod.ScoringPolicy, capture_mode: str,
                     captured_at: dt.datetime) -> TradeCandidateAssessment:
    out_dir = repo.out_dir
    cutoff_iso = _ist_cutoff(candidate_as_of).isoformat()

    replay_symbol = (replay.get("symbols") or {}).get(symbol) if replay.get("status") == "OK" else None
    replay_candidate = (replay.get("candidates") or {}).get(symbol) if replay.get("status") == "OK" else None
    technical = (replay_symbol or {}).get("technical") or {}
    volume_anomaly = (replay_symbol or {}).get("volume_anomaly") or {}
    relative = (replay_symbol or {}).get("relative") or {}
    close = (replay_symbol or {}).get("close")
    price_change_pct = (replay_candidate or {}).get("price_change_pct")
    direction_sign = 0 if not price_change_pct else (1 if price_change_pct > 0 else -1)

    has_volume_anomaly = bool((replay_symbol or {}).get("volume_anomaly"))
    has_technical = bool((replay_symbol or {}).get("technical"))
    has_relative = bool((replay_symbol or {}).get("relative"))

    daily = context.daily_rows(repo, symbol, session)
    ohlcv_sessions = len(daily)
    atr = metrics.atr14(daily, policy.atr_window)
    atr_pct = (atr / close * 100) if (atr is not None and close) else None

    weekly = metrics.weekly_alignment(daily, price_change_pct, policy)
    struct_ref = metrics.structural_reference(technical.get("prior_20_low"),
                                              technical.get("prior_20_high"), price_change_pct)
    ext = metrics.extension_state(technical.get("distance_from_sma20_pct"), close, atr, policy)
    pullback = metrics.pullback_state(close, struct_ref["price"], atr, policy)
    risk_width = metrics.risk_width_label(close, struct_ref["price"], atr, policy)
    rp_pct = metrics.range_position_pct(close, technical.get("prior_20_low"),
                                        technical.get("prior_20_high"))
    vol_band = metrics.volatility_band(atr_pct, policy)
    avg_vol_20 = volume_anomaly.get("average_volume_20")
    if avg_vol_20 is None:
        avg_vol_20 = metrics.average_volume(daily, window=20)
    liq = metrics.liquidity_band(avg_vol_20, close, policy)

    sector = sector_map.get(symbol)
    sector_rel = context.sector_relative_performance(repo, symbol, sector, members_by_sector,
                                                      session, benchmark_series)
    sector_relative_20d_pp = (sector_rel or {}).get("sector_relative_20d_pp")
    nsdl_flow = nsdl_by_sector.get(sector) if sector else None
    sector_rotation_context = (f"{sector}: {nsdl_flow.direction}" if nsdl_flow is not None else None)

    try:
        events = context.stock_events(out_dir, symbol, session, cutoff_iso)
        events_error = False
    except Exception:
        events, events_error = [], True
    sessions_to_event, event_family = _nearest_event(events, session, calendar)
    if events_error:
        proximity = EventProximity.EVENT_DATA_UNAVAILABLE.value
    elif sessions_to_event is None:
        proximity = EventProximity.NO_KNOWN_EVENT.value
    elif abs(sessions_to_event) <= policy.event_window_sessions:
        proximity = EventProximity.EVENT_WITHIN_WINDOW.value
    else:
        proximity = EventProximity.NO_KNOWN_EVENT.value

    regime_now = next((s for s in regime_snaps if s.session_date == session.isoformat()), None)
    regime_label = regime_now.regime if regime_now is not None else None
    streak = metrics.regime_streak(regime_snaps, session.isoformat(), policy.regime_streak_lookback)

    detector_warnings = (list(volume_anomaly.get("warnings") or [])
                        + list(technical.get("warnings") or [])
                        + list(relative.get("warnings") or []))

    inputs = {
        "independent_signal_count": (replay_candidate or {}).get("independent_signal_count")
                                    or stored.independent_signal_count,
        "volume_level": volume_anomaly.get("level"),
        "technical_events": [e.get("event_type") for e in (technical.get("events") or [])],
        "regime_label": regime_label, "direction_sign": direction_sign,
        "regime_streak": streak, "sector_relative_20d_pp": sector_relative_20d_pp,
        "market_relative_20d_pp": relative.get("market_relative_20d_pp"),
        "persistence_state": relative.get("persistence_state"),
        "weekly_alignment": weekly["alignment"], "weekly_evidence": weekly["evidence"],
        "extension_state": ext["state"], "extension_atr_units": ext["atr_units"],
        "range_position_pct": rp_pct, "liquidity_band": liq["band"],
        "turnover_cr": liq["turnover_cr"], "volatility_band": vol_band, "atr14_pct": atr_pct,
        "event_proximity": proximity, "event_family": event_family,
        "sessions_to_event": sessions_to_event, "reconcile_status": reconcile_status,
        "detector_warnings": detector_warnings,
    }
    components = scoring.score_candidate(inputs, policy)
    score_total, score_max = scoring.total_score(components, policy)

    volume_family_active = "VOLUME" in (stored.active_families or ())
    elig = eligibility.assess(
        reconcile_status=reconcile_status, volume_family_active=volume_family_active,
        has_volume_anomaly=has_volume_anomaly, has_technical=has_technical,
        has_relative=has_relative, ohlcv_sessions=ohlcv_sessions, atr_available=atr is not None,
        score_total=score_total, score_max=score_max, extension_state=ext["state"],
        volatility_band=vol_band, policy=policy)
    positive_evidence, risk_evidence = _evidence_summary(components)
    missing_context = tuple(sorted({m for c in components for m in c.missing_data}))

    radar_direction = stored.direction_compatibility
    flags = elig["flags"]
    if ((radar_direction == "ALIGNED_POSITIVE" and direction_sign < 0)
            or (radar_direction == "ALIGNED_NEGATIVE" and direction_sign > 0)):
        flags = flags + (DAY_MOVE_AGAINST_RADAR_DIRECTION,)

    return TradeCandidateAssessment(
        schema_version=SCHEMA_VERSION, score_policy_version=policy.score_policy_version,
        session_date=session.isoformat(), symbol=symbol,
        company=universe_company.get(symbol), sector=sector, capture_mode=capture_mode,
        candidate_as_of=candidate_as_of.isoformat(), captured_at=captured_at.isoformat(),
        research_priority=elig["research_priority"],
        eligibility_reasons=elig["eligibility_reasons"],
        data_quality_tier=elig["data_quality_tier"], score_total=score_total,
        score_max=score_max, score_validation_status="PROVISIONAL",
        components=tuple(c.to_dict() for c in components),
        radar_reason_codes=tuple(stored.reason_codes), radar_active_families=tuple(stored.active_families),
        range_position_pct=rp_pct, extension_state=ext["state"], pullback_state=pullback["state"],
        structural_reference_price=struct_ref["price"], structural_reference_kind=struct_ref["kind"],
        risk_width_label=risk_width["label"], atr14=atr, atr14_pct=atr_pct,
        weekly_alignment=weekly["alignment"], volatility_band=vol_band, liquidity_band=liq["band"],
        event_proximity=proximity, event_family=event_family, sessions_to_event=sessions_to_event,
        regime_label=regime_label, regime_streak_sessions=streak, regime_alignment=next(
            (c.evidence[0].split(" -> ")[-1] for c in components if c.component == "MARKET_ALIGNMENT"
             and c.evidence), None),
        sector_relative_20d_pp=sector_relative_20d_pp, sector_rotation_context=sector_rotation_context,
        positive_evidence=positive_evidence, risk_evidence=risk_evidence,
        missing_context=missing_context, flags=flags, radar_direction=radar_direction)


def build_assessments(repo, session: dt.date, *, regime_dir: str | None = None,
                      policy: policy_mod.ScoringPolicy = policy_mod.DEFAULT_SCORING_POLICY,
                      candidate_as_of: dt.datetime | None = None,
                      capture_mode: str | None = None,
                      captured_at: dt.datetime | None = None) -> list:
    """One `TradeCandidateAssessment` per stored Radar candidate for `session` - the full,
    retained list (owner spec section 3: every Radar candidate is retained for auditability).

    See the module docstring for `candidate_as_of`/`capture_mode`/`captured_at` resolution."""
    from private_desk.replay import get_replay, reconcile

    stored = repo.candidates(session)
    if not stored:
        return []
    out_dir = repo.out_dir
    regime_dir = regime_dir or _default_regime_dir(out_dir)
    candidate_as_of, capture_mode, captured_at = resolve_capture(
        session, candidate_as_of, capture_mode, captured_at)
    cutoff = _ist_cutoff(candidate_as_of)
    cutoff_iso = cutoff.isoformat()

    replay = get_replay(repo, None, session)
    reconcile_map = reconcile(replay, stored) if replay.get("status") == "OK" else {
        c.instrument: "NOT_REPLAYED" for c in stored}

    ms_artifact = context.market_structure_as_of(out_dir, session, cutoff_iso)
    sector_map, members_by_sector = context.sector_map_and_members(ms_artifact)
    constituents = ((ms_artifact or {}).get("universe") or {}).get("constituents") or {}
    universe_company = {s: info.get("company") for s, info in constituents.items()}

    regime_snaps = context.regime_snapshots(repo, regime_dir, session,
                                            n=policy.regime_streak_lookback + 1)

    nsdl_flows = context.nsdl_sector_flows(out_dir, cutoff, on_or_before=session)
    nsdl_by_sector = {f.display_sector: f for f in nsdl_flows if f.display_sector}

    benchmark_series = repo.benchmark_series(session)
    from core.trading_calendar import SessionCalendar
    calendar = SessionCalendar.from_index_dates([r["date"] for r in benchmark_series])

    out = []
    for c in stored:
        out.append(assess_candidate(
            repo, c.instrument, c, replay=replay, reconcile_status=reconcile_map.get(
                c.instrument, "NOT_REPLAYED"), session=session, candidate_as_of=candidate_as_of,
            sector_map=sector_map, members_by_sector=members_by_sector,
            nsdl_by_sector=nsdl_by_sector, regime_snaps=regime_snaps,
            benchmark_series=benchmark_series, calendar=calendar,
            universe_company=universe_company, policy=policy, capture_mode=capture_mode,
            captured_at=captured_at))
    return out


def _correlation_lookup(repo, ranking: list, policy: policy_mod.ScoringPolicy,
                        session: dt.date) -> dict:
    by_sector: dict = {}
    for a in ranking:
        if a.sector:
            by_sector.setdefault(a.sector, []).append(a.symbol)
    lookup: dict = {}
    for sector, symbols in by_sector.items():
        if len(symbols) < 2:
            continue
        window = policy.correlation_window_sessions + 1      # N returns need N+1 closes
        rows_by_symbol = {s: context.daily_rows(repo, s, session,
                                                lookback_days=int(policy.correlation_window_sessions * 1.6) + 10)[-window:]
                         for s in symbols}
        for i, sym_a in enumerate(symbols):
            for sym_b in symbols[i + 1:]:
                corr = metrics.pairwise_return_correlation(
                    rows_by_symbol[sym_a], rows_by_symbol[sym_b],
                    min_sample=policy.correlation_min_sample)
                lookup[frozenset((sym_a, sym_b))] = corr
    return lookup


def direction_bucket(radar_direction: str | None) -> str:
    """UPSIDE / DOWNSIDE / MIXED from the Radar's own direction (MIXED also covers
    NON_DIRECTIONAL and a missing value)."""
    if radar_direction == "ALIGNED_POSITIVE":
        return "UPSIDE"
    if radar_direction == "ALIGNED_NEGATIVE":
        return "DOWNSIDE"
    return "MIXED"


def _repo_for(out_dir: str, repo):
    if repo is not None:
        return repo
    from private_desk.repository import DeskRepository
    return DeskRepository(out_dir)


def _board(repo, session: dt.date, assessments: list, policy: policy_mod.ScoringPolicy, *,
           view: str, source: str, capture_mode: str | None = None) -> dict:
    """Raw ranking + diversified shortlist over `assessments` (never mutated, never re-scored).
    The UPSIDE view ranks and shortlists only UPSIDE candidates, so a downside name can never
    take an upside name's sector slot; the other ranked candidates come back as `outside_view`."""
    full_ranking = raw_ranking(assessments)
    if view == UPSIDE_VIEW:
        ranking = [a for a in full_ranking if direction_bucket(a.radar_direction) == "UPSIDE"]
        outside_view = [a for a in full_ranking if direction_bucket(a.radar_direction) != "UPSIDE"]
    else:
        ranking, outside_view = full_ranking, []
    correlation_lookup = _correlation_lookup(repo, ranking, policy, session)
    sl = diversified_shortlist(ranking, policy=policy, correlation_lookup=correlation_lookup)

    direction_counts = {"UPSIDE": 0, "DOWNSIDE": 0, "MIXED": 0}
    for a in assessments:
        direction_counts[direction_bucket(a.radar_direction)] += 1
    modes = sorted({a.capture_mode for a in assessments})
    stamps = sorted(dt.datetime.fromisoformat(a.captured_at) for a in assessments if a.captured_at)
    return {"session_date": session.isoformat(), "score_policy_version": policy.score_policy_version,
           "capture_mode": " + ".join(modes) if modes else capture_mode,
           "candidate_count": len(assessments),
           "eligible_count": sum(1 for a in assessments if a.research_priority == "ELIGIBLE"),
           "assessments": assessments, "raw_ranking": ranking, "shortlist": sl["shortlist"],
           "demoted": sl["demoted"], "sector_unknown": sl["sector_unknown"], "view": view,
           "outside_view": outside_view, "direction_counts": direction_counts, "source": source,
           "captured_first": stamps[0].isoformat() if stamps else None,
           "captured_last": stamps[-1].isoformat() if stamps else None}


def run(out_dir: str, session: dt.date, *, repo=None, regime_dir: str | None = None,
       policy: policy_mod.ScoringPolicy = policy_mod.DEFAULT_SCORING_POLICY,
       candidate_as_of: dt.datetime | None = None, capture_mode: str | None = None,
       captured_at: dt.datetime | None = None, view: str = ALL_VIEW) -> dict:
    """The full pass: build every assessment, then the raw ranking and diversified shortlist.
    `repo` lets a caller that already holds a `DeskRepository` (e.g. `DeskService`) reuse it
    instead of constructing a second one - `DeskRepository` is a plain read-only wrapper, so
    either way is cheap, but reusing avoids a redundant instance."""
    repo = _repo_for(out_dir, repo)
    assessments = build_assessments(repo, session, regime_dir=regime_dir, policy=policy,
                                    candidate_as_of=candidate_as_of, capture_mode=capture_mode,
                                    captured_at=captured_at)
    return _board(repo, session, assessments, policy, view=view, source=LIVE_RECOMPUTE,
                  capture_mode=capture_mode)


def _stored(out_dir: str, session: dt.date, symbol: str,
            policy: policy_mod.ScoringPolicy) -> TradeCandidateAssessment | None:
    from . import store
    a = store.load_latest(out_dir, policy.score_policy_version, session, symbol)
    return a if a is not None and a.schema_version == SCHEMA_VERSION else None


def run_from_store(out_dir: str, session: dt.date, *, repo=None,
                   policy: policy_mod.ScoringPolicy = policy_mod.DEFAULT_SCORING_POLICY,
                   view: str = ALL_VIEW) -> dict | None:
    """The same board as `run()`, over the STORED snapshot (what the automatic capture recorded
    at its own cutoff) instead of a recompute. None when nothing is stored for this policy
    version and session, or when any stored revision is from an older schema - the caller then
    falls back to `run()`. Read-only: never writes a snapshot."""
    from . import store
    symbols = store.list_symbols(out_dir, policy.score_policy_version, session)
    assessments = [_stored(out_dir, session, s, policy) for s in symbols]
    if not assessments or any(a is None for a in assessments):
        return None
    return _board(_repo_for(out_dir, repo), session, sorted(assessments, key=lambda a: a.symbol),
                  policy, view=view, source=STORED_SNAPSHOT)


def stored_assessment(out_dir: str, session: dt.date, symbol: str, *,
                      policy: policy_mod.ScoringPolicy = policy_mod.DEFAULT_SCORING_POLICY
                      ) -> TradeCandidateAssessment | None:
    """One symbol's latest stored revision under the current schema, or None."""
    return _stored(out_dir, session, symbol.upper(), policy)


def persist(out_dir: str, assessments: list) -> list:
    """Writes one immutable snapshot revision per assessment. Returns the written paths.

    Unconditional (always appends a new revision) - callers that want idempotent, content-
    aware capture (never rewriting an unchanged historical record) use `capture.
    capture_latest_session` instead, which decides SKIP/WRITE per candidate before calling
    this."""
    from . import store
    return [store.write_snapshot(out_dir, a) for a in assessments]


__all__ = ["build_assessments", "assess_candidate", "run", "run_from_store", "stored_assessment",
          "persist", "resolve_capture", "direction_bucket", "POINT_IN_TIME", "RECONSTRUCTED",
          "ALL_VIEW", "UPSIDE_VIEW", "VIEWS", "LIVE_RECOMPUTE", "STORED_SNAPSHOT"]
