from dataclasses import replace

from private_candidates.models import ResearchPriority, TradeCandidateAssessment
from private_candidates.policy import ScoringPolicy
from private_candidates.shortlist import diversified_shortlist, raw_ranking


def _a(symbol, sector, score, priority=ResearchPriority.ELIGIBLE.value) -> TradeCandidateAssessment:
    return TradeCandidateAssessment(
        schema_version="private-trade-candidate-1.0", score_policy_version="v1",
        session_date="2026-09-18", symbol=symbol, company=None, sector=sector,
        capture_mode="POINT_IN_TIME", candidate_as_of="2026-09-18T22:00:00+05:30",
        captured_at="2026-09-18T22:00:00+05:30",
        research_priority=priority, eligibility_reasons=(), data_quality_tier="COMPLETE",
        score_total=score, score_max=100.0, score_validation_status="PROVISIONAL",
        components=(), radar_reason_codes=(), radar_active_families=(),
        range_position_pct=None, extension_state="NORMAL", pullback_state="AT_STRUCTURE",
        structural_reference_price=None, structural_reference_kind=None,
        risk_width_label="RISK_WIDTH_UNKNOWN", atr14=None, atr14_pct=None,
        weekly_alignment="ALIGNED", volatility_band="NORMAL", liquidity_band="DEEP",
        event_proximity="NO_KNOWN_EVENT", event_family=None, sessions_to_event=None,
        regime_label="BULLISH", regime_streak_sessions=1, regime_alignment=None,
        sector_relative_20d_pp=None, sector_rotation_context=None, positive_evidence=(),
        risk_evidence=(), missing_context=())


def test_raw_ranking_excludes_not_actionable_and_data_incomplete():
    items = [_a("A", "Financials", 90), _a("B", "IT", 20, "NOT_ACTIONABLE"),
            _a("C", "IT", 10, "DATA_INCOMPLETE"), _a("D", "Healthcare", 50, "REVIEW")]
    ranking = raw_ranking(items)
    assert [a.symbol for a in ranking] == ["A", "D"]


def test_diversified_shortlist_demotes_beyond_sector_cap_with_explicit_reason():
    items = [_a("BANK_A", "Financials", 95), _a("BANK_B", "Financials", 90),
            _a("BANK_C", "Financials", 85), _a("IT_A", "IT", 80)]
    ranking = raw_ranking(items)
    policy = ScoringPolicy(sector_cap=2)
    out = diversified_shortlist(ranking, policy=policy)
    assert [a.symbol for a in out["shortlist"]] == ["BANK_A", "BANK_B", "IT_A"]
    assert out["demoted"] == [{"symbol": "BANK_C", "demotion_reason": "SECTOR_CONCENTRATION",
                              "detail": {"sector": "Financials", "sector_cap": 2}}]


def test_diversified_shortlist_never_mutates_raw_ranking_order():
    items = [_a("BANK_A", "Financials", 95), _a("BANK_B", "Financials", 90),
            _a("BANK_C", "Financials", 85)]
    ranking = raw_ranking(items)
    before = [a.symbol for a in ranking]
    diversified_shortlist(ranking, policy=ScoringPolicy(sector_cap=1))
    assert [a.symbol for a in ranking] == before


def test_high_correlation_demotes_with_insufficient_sample_never_demoting():
    items = [_a("A", "Financials", 95), _a("B", "Financials", 90)]
    ranking = raw_ranking(items)
    policy = ScoringPolicy(sector_cap=5, correlation_high_threshold=0.8)
    lookup_known = {frozenset(("A", "B")): 0.9}
    out = diversified_shortlist(ranking, policy=policy, correlation_lookup=lookup_known)
    assert [a.symbol for a in out["shortlist"]] == ["A"]
    assert out["demoted"][0]["demotion_reason"] == "HIGH_CORRELATION"

    lookup_unknown = {frozenset(("A", "B")): None}
    out2 = diversified_shortlist(ranking, policy=policy, correlation_lookup=lookup_unknown)
    assert [a.symbol for a in out2["shortlist"]] == ["A", "B"]


def test_raw_ranking_puts_eligible_before_a_higher_scoring_review():
    """A REVIEW capped for PARTIAL data must not outrank complete data on score alone."""
    items = [_a("PARTIAL_HIGH", "IT", 80, "REVIEW"), _a("ELIG", "Financials", 65),
            _a("REV_LOW", "Energy", 40, "REVIEW")]
    assert [a.symbol for a in raw_ranking(items)] == ["ELIG", "PARTIAL_HIGH", "REV_LOW"]


def test_unknown_sector_is_never_pooled_under_the_sector_cap():
    """2026-10-16: every candidate lost its sector, and pooling them as one 'UNKNOWN' sector
    shrank the shortlist to `sector_cap` names. Missing data is not concentration."""
    items = [_a("U1", None, 90), _a("U2", None, 85), _a("U3", None, 80),
            _a("BANK_A", "Financials", 75), _a("BANK_B", "Financials", 70)]
    ranking = raw_ranking(items)
    lookup = {frozenset(("U1", "U2")): 0.99, frozenset(("BANK_A", "BANK_B")): None}
    out = diversified_shortlist(ranking, policy=ScoringPolicy(sector_cap=1),
                                correlation_lookup=lookup)
    assert [a.symbol for a in out["shortlist"]] == ["U1", "U2", "U3", "BANK_A"]
    assert out["sector_unknown"] == ["U1", "U2", "U3"]
    assert out["demoted"] == [{"symbol": "BANK_B", "demotion_reason": "SECTOR_CONCENTRATION",
                              "detail": {"sector": "Financials", "sector_cap": 1}}]
