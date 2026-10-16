from private_candidates import scoring
from private_candidates.models import NOT_SCORABLE, SCORED
from private_candidates.policy import DEFAULT_SCORING_POLICY as POLICY

STRONG_ALIGNED_INPUTS = {
    "independent_signal_count": 3, "volume_level": "EXTREME",
    "technical_events": ["BREAK_ABOVE_20D_RANGE"], "regime_label": "BULLISH",
    "direction_sign": 1, "regime_streak": 6, "sector_relative_20d_pp": 8.0,
    "market_relative_20d_pp": 6.0, "persistence_state": "PERSISTENT_POSITIVE",
    "weekly_alignment": "ALIGNED", "weekly_evidence": "weekly close above weekly average",
    "extension_state": "NORMAL", "extension_atr_units": 1.0, "range_position_pct": 70.0,
    "liquidity_band": "DEEP", "turnover_cr": 120.0, "volatility_band": "NORMAL",
    "atr14_pct": 2.0, "event_proximity": "NO_KNOWN_EVENT", "event_family": None,
    "sessions_to_event": None, "reconcile_status": "MATCHED", "detector_warnings": [],
}


def test_strong_aligned_candidate_scores_high_and_fully_scorable():
    components = scoring.score_candidate(STRONG_ALIGNED_INPUTS, POLICY)
    assert all(c.status == SCORED for c in components)
    total, max_total = scoring.total_score(components, POLICY)
    assert max_total == 100.0
    assert total > 80.0


def test_great_setup_but_deeply_stretched_demotes_only_the_extension_component():
    extended = dict(STRONG_ALIGNED_INPUTS, extension_state="DEEPLY_STRETCHED",
                    extension_atr_units=5.0)
    base = scoring.score_candidate(STRONG_ALIGNED_INPUTS, POLICY)
    stretched = scoring.score_candidate(extended, POLICY)
    by_component = {c.component: c for c in stretched}
    assert by_component["RANGE_POSITION_AND_EXTENSION"].score == 0.0
    # every other component's evidence is untouched by the extension change
    for a, b in zip(base, stretched):
        if a.component != "RANGE_POSITION_AND_EXTENSION":
            assert a.score == b.score


def test_weak_sector_lowers_only_the_sector_component():
    weak_sector = dict(STRONG_ALIGNED_INPUTS, sector_relative_20d_pp=-8.0)
    components = scoring.score_candidate(weak_sector, POLICY)
    by_component = {c.component: c for c in components}
    assert by_component["SECTOR_ALIGNMENT"].score == 0.0
    # the underlying stock evidence (setup quality, relative strength) is still visible
    assert by_component["SETUP_QUALITY"].score > 0
    assert by_component["RELATIVE_STRENGTH"].score > 0


def test_adverse_regime_lowers_alignment_without_erasing_stock_evidence():
    adverse = dict(STRONG_ALIGNED_INPUTS, regime_label="BEARISH", regime_streak=4)
    components = scoring.score_candidate(adverse, POLICY)
    by_component = {c.component: c for c in components}
    assert by_component["MARKET_ALIGNMENT"].score == 0.0
    assert by_component["SETUP_QUALITY"].score > 0


def test_event_within_window_is_flagged_not_zeroed():
    near_event = dict(STRONG_ALIGNED_INPUTS, event_proximity="EVENT_WITHIN_WINDOW",
                      event_family="EARNINGS", sessions_to_event=1)
    components = scoring.score_candidate(near_event, POLICY)
    by_component = {c.component: c for c in components}
    assert 0.0 < by_component["EVENT_RISK"].score < POLICY.weight_event_risk


def test_missing_data_marks_components_not_scorable_without_a_fake_score():
    missing = dict(STRONG_ALIGNED_INPUTS, sector_relative_20d_pp=None,
                  market_relative_20d_pp=None, weekly_alignment="WEEKLY_INSUFFICIENT_DATA",
                  liquidity_band="LIQUIDITY_UNKNOWN", volatility_band="VOLATILITY_UNKNOWN",
                  event_proximity="EVENT_DATA_UNAVAILABLE", reconcile_status="NOT_REPLAYED")
    components = scoring.score_candidate(missing, POLICY)
    not_scorable = {c.component for c in components if c.status == NOT_SCORABLE}
    assert {"SECTOR_ALIGNMENT", "RELATIVE_STRENGTH", "MTF_CONFIRMATION", "LIQUIDITY",
           "VOLATILITY_FIT", "EVENT_RISK", "DATA_QUALITY_INTEGRITY"} <= not_scorable
    assert all(c.score == 0.0 for c in components if c.status == NOT_SCORABLE)
    assert all(c.max_score > 0 for c in components)   # max never shrinks
