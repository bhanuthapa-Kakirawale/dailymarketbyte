"""Models/policy: weight sum, enum round-trips, packet forbidden-field safety."""
from private_candidates.models import (ComponentScore, ResearchPriority, TradeCandidateAssessment)
from private_candidates.policy import DEFAULT_SCORING_POLICY, component_weights


def test_component_weights_sum_to_max_total():
    weights = component_weights(DEFAULT_SCORING_POLICY)
    assert sum(weights.values()) == DEFAULT_SCORING_POLICY.max_total == 100.0


def test_component_score_round_trip():
    c = ComponentScore("SETUP_QUALITY", 12.0, 20.0, "SCORED", ("ev1",), (), "why")
    assert ComponentScore.from_dict(c.to_dict()) == c


def _assessment(**overrides) -> TradeCandidateAssessment:
    base = dict(
        schema_version="private-trade-candidate-1.0", score_policy_version="trade-candidate-score-1.0",
        session_date="2026-09-18", symbol="SYMA", company="Synthetic A Ltd.", sector="Healthcare",
        capture_mode="POINT_IN_TIME", candidate_as_of="2026-09-18T22:00:00+05:30",
        captured_at="2026-09-18T22:00:00+05:30",
        research_priority=ResearchPriority.ELIGIBLE.value, eligibility_reasons=("score 70.0/100",),
        data_quality_tier="COMPLETE", score_total=70.0, score_max=100.0,
        score_validation_status="PROVISIONAL", components=(),
        radar_reason_codes=("VOLUME_EXTREME",), radar_active_families=("VOLUME",),
        range_position_pct=80.0, extension_state="NORMAL", pullback_state="AT_STRUCTURE",
        structural_reference_price=95.0, structural_reference_kind="PRIOR_20_LOW",
        risk_width_label="NARROW", atr14=2.0, atr14_pct=2.0, weekly_alignment="ALIGNED",
        volatility_band="NORMAL", liquidity_band="DEEP", event_proximity="NO_KNOWN_EVENT",
        event_family=None, sessions_to_event=None, regime_label="BULLISH",
        regime_streak_sessions=3, regime_alignment="CANDIDATE_ALIGNS_WITH_REGIME",
        sector_relative_20d_pp=4.0, sector_rotation_context="Healthcare: INFLOW_CONTINUES",
        positive_evidence=(), risk_evidence=(), missing_context=())
    base.update(overrides)
    return TradeCandidateAssessment(**base)


def test_assessment_round_trip():
    a = _assessment()
    assert TradeCandidateAssessment.from_dict(a.to_dict()) == a


def test_radar_direction_round_trips():
    a = _assessment(radar_direction="ALIGNED_POSITIVE",
                    flags=("DAY_MOVE_AGAINST_RADAR_DIRECTION",))
    assert TradeCandidateAssessment.from_dict(a.to_dict()) == a


def test_a_pre_1_1_snapshot_without_radar_direction_still_loads():
    d = _assessment().to_dict()
    d.pop("radar_direction")
    assert TradeCandidateAssessment.from_dict(d).radar_direction is None


def test_assessment_fields_never_collide_with_forbidden_packet_tokens():
    from private_desk.packet import FORBIDDEN_FIELD_TOKENS
    a = _assessment()

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from keys(v)

    names = list(TradeCandidateAssessment.__dataclass_fields__) + list(keys(a.to_dict()))
    for name in names:
        tokens = set(str(name).lower().split("_"))
        assert not tokens & set(FORBIDDEN_FIELD_TOKENS), name


def test_score_validation_status_is_always_provisional():
    assert _assessment().score_validation_status == "PROVISIONAL"
