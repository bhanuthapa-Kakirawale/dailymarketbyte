"""intelligence_refresh.registry: the 10 (+1 derived helper) classifications must match the
documented capability registry exactly - this is the single source of truth every other module
reads from."""
from __future__ import annotations

from intelligence_refresh import registry as R


def test_price_chain_components_are_reconstructable():
    for key in R.PRICE_CHAIN_KEYS:
        assert R.COMPONENTS[key].backfill_mode == R.RECONSTRUCTABLE


def test_latest_session_only_components():
    expected = {"institutional_flow", "market_events", "official_snapshots",
               "canonical_report", "intelligence_snapshot", "trade_candidates"}
    assert set(R.LATEST_ONLY_KEYS) == expected


def test_trade_candidates_requires_no_network_and_has_no_repair_fn():
    """PRIVATE_ANALYTICS only; the one lever is the latest-session catch-up
    (private_candidates.capture.capture_latest_session) - no network, no generic 30-day
    repair path (docs/PRIVATE_TRADE_CANDIDATES.md)."""
    spec = R.COMPONENTS["trade_candidates"]
    assert spec.requires_network is False
    assert spec.repair_fn is None
    assert spec.backfill_mode == R.LATEST_SESSION_ONLY


def test_static_components():
    static = {k: R.COMPONENTS[k].backfill_mode for k in R.STATIC_KEYS}
    assert static["radar_editorial_selections"] == R.UNSUPPORTED_HISTORICALLY
    assert static["market_regime"] == R.NOT_APPLICABLE


def test_all_keys_classified_exactly_once():
    assert set(R.ALL_KEYS) == (set(R.PRICE_CHAIN_KEYS) | set(R.LATEST_ONLY_KEYS)
                               | set(R.STATIC_KEYS))
    assert len(R.ALL_KEYS) == 11


def test_market_regime_requires_no_network_and_has_no_repair_fn():
    spec = R.COMPONENTS["market_regime"]
    assert spec.requires_network is False
    assert spec.repair_fn is None


def test_radar_editorial_selections_has_no_repair_fn():
    assert R.COMPONENTS["radar_editorial_selections"].repair_fn is None
