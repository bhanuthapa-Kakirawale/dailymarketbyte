from private_candidates import eligibility as elig
from private_candidates.policy import DEFAULT_SCORING_POLICY as POLICY


def test_untrusted_replay_is_data_incomplete():
    out = elig.assess(reconcile_status="NOT_REPLAYED", volume_family_active=True,
                      has_volume_anomaly=True, has_technical=True, has_relative=True,
                      ohlcv_sessions=60, atr_available=True, score_total=90, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["research_priority"] == "DATA_INCOMPLETE"
    assert out["data_quality_tier"] == "INSUFFICIENT"


def test_volume_family_active_but_no_anomaly_reading_is_data_incomplete():
    """A real inconsistency: the stored candidate says VOLUME was active, but replay produced
    no anomaly for it."""
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=True,
                      has_volume_anomaly=False, has_technical=True, has_relative=True,
                      ohlcv_sessions=60, atr_available=True, score_total=90, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["research_priority"] == "DATA_INCOMPLETE"


def test_no_volume_anomaly_is_fine_when_volume_was_never_an_active_family():
    """The ORDINARY case: a candidate flagged via STRUCTURE + RELATIVE_PERFORMANCE alone never
    had a volume anomaly to begin with - this must never be treated as missing data."""
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=False,
                      has_volume_anomaly=False, has_technical=True, has_relative=True,
                      ohlcv_sessions=60, atr_available=True, score_total=90, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["research_priority"] == "ELIGIBLE"
    assert out["data_quality_tier"] == "COMPLETE"


def test_missing_technical_structure_is_data_incomplete():
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=False,
                      has_volume_anomaly=False, has_technical=False, has_relative=True,
                      ohlcv_sessions=60, atr_available=True, score_total=90, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["research_priority"] == "DATA_INCOMPLETE"


def test_short_ohlcv_history_is_data_incomplete():
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=True,
                      has_volume_anomaly=True, has_technical=True, has_relative=True,
                      ohlcv_sessions=5, atr_available=False, score_total=90, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["research_priority"] == "DATA_INCOMPLETE"


def test_compounding_risk_veto_demotes_to_not_actionable_regardless_of_score():
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=True,
                      has_volume_anomaly=True, has_technical=True, has_relative=True,
                      ohlcv_sessions=60, atr_available=True, score_total=95, score_max=100,
                      extension_state="DEEPLY_STRETCHED", volatility_band="EXTREME",
                      policy=POLICY)
    assert out["research_priority"] == "NOT_ACTIONABLE"
    assert "COMPOUNDING_RISK_VETO" in out["flags"]


def test_high_score_complete_data_is_eligible():
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=True,
                      has_volume_anomaly=True, has_technical=True, has_relative=True,
                      ohlcv_sessions=60, atr_available=True, score_total=70, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["research_priority"] == "ELIGIBLE"
    assert out["data_quality_tier"] == "COMPLETE"


def test_missing_optional_relative_reading_is_partial_and_at_most_review():
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=True,
                      has_volume_anomaly=True, has_technical=True, has_relative=False,
                      ohlcv_sessions=60, atr_available=True, score_total=90, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["data_quality_tier"] == "PARTIAL"
    assert out["research_priority"] == "REVIEW"


def test_low_score_complete_data_is_not_actionable():
    out = elig.assess(reconcile_status="MATCHED", volume_family_active=True,
                      has_volume_anomaly=True, has_technical=True, has_relative=True,
                      ohlcv_sessions=60, atr_available=True, score_total=10, score_max=100,
                      extension_state="NORMAL", volatility_band="NORMAL", policy=POLICY)
    assert out["research_priority"] == "NOT_ACTIONABLE"
