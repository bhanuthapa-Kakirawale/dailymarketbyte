"""Typed, read-only presentation DTOs - the ONLY shape `private_desk` may consume.

Built here, inside `private_candidates`, specifically so `private_desk`'s own source never
needs a literal `score`-shaped identifier to display an already-computed value - the rename
happens once, at this package boundary, as an explicit and documented field rename on a typed
contract, never via dynamic attribute access (`getattr`/`.get(...)`) inside `private_desk`
itself (owner hardening pass, section 5). `private_desk/services/trade_candidates.py` reads
these DTOs and nothing else; it never computes, recomputes, or persists a score.

    private_candidates.service / store
            |
            v
    private_candidates.presentation   (THIS module - typed DTOs, score->quality_points renamed)
            |
            v
    private_desk/services/trade_candidates.py   (pure pass-through)
            |
            v
    template
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import TradeCandidateAssessment
from .policy import DEFAULT_SCORING_POLICY

DEFAULT_POLICY_VERSION = DEFAULT_SCORING_POLICY.score_policy_version


@dataclass(frozen=True)
class ComponentPresentation:
    component: str
    quality_points: float
    quality_max: float
    status: str
    evidence: tuple
    missing_data: tuple
    reason: str

    @classmethod
    def from_component(cls, c) -> "ComponentPresentation":
        d = c if isinstance(c, dict) else c.to_dict()
        return cls(component=d["component"], quality_points=d["score"],
                  quality_max=d["max_score"], status=d["status"],
                  evidence=tuple(d.get("evidence") or ()),
                  missing_data=tuple(d.get("missing_data") or ()), reason=d.get("reason", ""))


@dataclass(frozen=True)
class CandidateRow:
    """One table row - the Trade Candidates list page (raw ranking / shortlist / full list)."""
    rank: int
    symbol: str
    company: str | None
    sector: str | None
    quality_points: float
    quality_max: float
    research_priority: str
    data_quality_tier: str
    extension_state: str
    pullback_state: str
    risk_width_label: str
    weekly_alignment: str
    volatility_band: str
    liquidity_band: str
    event_proximity: str
    regime_alignment: str | None
    radar_direction: str | None = None
    flags: tuple = ()

    @classmethod
    def from_assessment(cls, a: TradeCandidateAssessment, rank: int) -> "CandidateRow":
        return cls(rank=rank, symbol=a.symbol, company=a.company, sector=a.sector,
                  quality_points=a.score_total, quality_max=a.score_max,
                  research_priority=a.research_priority, data_quality_tier=a.data_quality_tier,
                  extension_state=a.extension_state, pullback_state=a.pullback_state,
                  risk_width_label=a.risk_width_label, weekly_alignment=a.weekly_alignment,
                  volatility_band=a.volatility_band, liquidity_band=a.liquidity_band,
                  event_proximity=a.event_proximity, regime_alignment=a.regime_alignment,
                  radar_direction=a.radar_direction, flags=tuple(a.flags))


@dataclass(frozen=True)
class CandidateDetailView:
    """The full candidate-detail page contract. Every field `TradeCandidateAssessment` carries
    unchanged, EXCEPT the score-shaped ones, renamed once here."""
    symbol: str
    company: str | None
    sector: str | None
    research_priority: str
    quality_points: float
    quality_max: float
    quality_policy_version: str
    quality_validation_status: str
    data_quality_tier: str
    capture_mode: str
    candidate_as_of: str
    captured_at: str
    eligibility_reasons: tuple
    flags: tuple
    components: tuple                       # ComponentPresentation
    radar_reason_codes: tuple
    radar_active_families: tuple
    range_position_pct: float | None
    extension_state: str
    pullback_state: str
    structural_reference_price: float | None
    structural_reference_kind: str | None
    risk_width_label: str
    atr14: float | None
    atr14_pct: float | None
    weekly_alignment: str
    volatility_band: str
    liquidity_band: str
    event_proximity: str
    event_family: str | None
    sessions_to_event: int | None
    regime_label: str | None
    regime_streak_sessions: int | None
    regime_alignment: str | None
    sector_relative_20d_pp: float | None
    sector_rotation_context: str | None
    positive_evidence: tuple
    risk_evidence: tuple
    missing_context: tuple
    notes: tuple
    radar_direction: str | None = None
    source: str | None = None              # STORED_SNAPSHOT | LIVE_RECOMPUTE
    sector_vs_market_20d_pp: float | None = None

    @classmethod
    def from_assessment(cls, a: TradeCandidateAssessment,
                        source: str | None = None) -> "CandidateDetailView":
        return cls(
            symbol=a.symbol, company=a.company, sector=a.sector,
            research_priority=a.research_priority, quality_points=a.score_total,
            quality_max=a.score_max, quality_policy_version=a.score_policy_version,
            quality_validation_status=a.score_validation_status,
            data_quality_tier=a.data_quality_tier, capture_mode=a.capture_mode,
            candidate_as_of=a.candidate_as_of, captured_at=a.captured_at,
            eligibility_reasons=a.eligibility_reasons, flags=a.flags,
            components=tuple(ComponentPresentation.from_component(c) for c in a.components),
            radar_reason_codes=a.radar_reason_codes, radar_active_families=a.radar_active_families,
            range_position_pct=a.range_position_pct, extension_state=a.extension_state,
            pullback_state=a.pullback_state,
            structural_reference_price=a.structural_reference_price,
            structural_reference_kind=a.structural_reference_kind,
            risk_width_label=a.risk_width_label, atr14=a.atr14, atr14_pct=a.atr14_pct,
            weekly_alignment=a.weekly_alignment, volatility_band=a.volatility_band,
            liquidity_band=a.liquidity_band, event_proximity=a.event_proximity,
            event_family=a.event_family, sessions_to_event=a.sessions_to_event,
            regime_label=a.regime_label, regime_streak_sessions=a.regime_streak_sessions,
            regime_alignment=a.regime_alignment,
            sector_relative_20d_pp=a.sector_relative_20d_pp,
            sector_rotation_context=a.sector_rotation_context,
            positive_evidence=a.positive_evidence, risk_evidence=a.risk_evidence,
            missing_context=a.missing_context, notes=a.notes,
            radar_direction=a.radar_direction, source=source,
            sector_vs_market_20d_pp=a.sector_vs_market_20d_pp)


@dataclass(frozen=True)
class CandidateBoardView:
    """The complete, pre-shaped Trade Candidates page contract."""
    session_date: str | None
    quality_policy_version: str | None
    capture_mode: str | None
    candidate_count: int
    eligible_count: int
    raw_ranking: tuple = field(default_factory=tuple)      # CandidateRow
    shortlist: tuple = field(default_factory=tuple)        # CandidateRow
    demoted: tuple = field(default_factory=tuple)          # plain dicts - already safe
    all_candidates: tuple = field(default_factory=tuple)   # CandidateRow
    view: str | None = None                                # ALL | UPSIDE
    outside_view: tuple = field(default_factory=tuple)     # CandidateRow - information only
    direction_counts: dict = field(default_factory=dict)   # UPSIDE / DOWNSIDE / MIXED
    sector_unknown: tuple = field(default_factory=tuple)   # symbols the sector cap never saw
    source: str | None = None                              # STORED_SNAPSHOT | LIVE_RECOMPUTE
    captured_first: str | None = None
    captured_last: str | None = None

    @classmethod
    def from_run(cls, run: dict) -> "CandidateBoardView":
        def ranked(items) -> tuple:
            return tuple(CandidateRow.from_assessment(a, i + 1) for i, a in enumerate(items or ()))
        return cls(session_date=run.get("session_date"),
                  quality_policy_version=run.get("score_policy_version"),
                  capture_mode=run.get("capture_mode"),
                  candidate_count=run.get("candidate_count") or 0,
                  eligible_count=run.get("eligible_count") or 0,
                  raw_ranking=ranked(run.get("raw_ranking")), shortlist=ranked(run.get("shortlist")),
                  demoted=tuple(run.get("demoted") or ()),
                  all_candidates=ranked(run.get("assessments")),
                  view=run.get("view"), outside_view=ranked(run.get("outside_view")),
                  direction_counts=dict(run.get("direction_counts") or {}),
                  sector_unknown=tuple(run.get("sector_unknown") or ()),
                  source=run.get("source"), captured_first=run.get("captured_first"),
                  captured_last=run.get("captured_last"))


__all__ = ["DEFAULT_POLICY_VERSION", "ComponentPresentation", "CandidateRow",
          "CandidateDetailView", "CandidateBoardView"]
