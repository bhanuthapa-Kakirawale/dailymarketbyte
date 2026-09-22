"""Relative volume: exactly 20 prior sessions, current excluded, None when short."""
import pandas as pd
import pytest

import market
from market import RELATIVE_VOLUME_DEFINITION, RELATIVE_VOLUME_LOOKBACK, relative_volume


def _volumes(prior, current):
    """A volume series: the `prior` sessions, followed by the current one."""
    return pd.Series(list(prior) + [current])


# --------------------------------------------------------------------- the window
def test_uses_exactly_twenty_prior_sessions():
    """A flat 100-lot history with a 300 current session is exactly 3x average."""
    assert relative_volume(_volumes([100] * 20, 300)) == pytest.approx(3.0)


def test_current_session_is_excluded_from_the_denominator():
    """Including the current session would damp the very spike the metric exists to show."""
    series = _volumes([100] * 20, 2000)
    excluded = 2000 / 100                                  # correct: mean of the 20 priors
    included = 2000 / float(series.mean())                 # wrong: ~9.5, the spike self-damped

    assert relative_volume(series) == pytest.approx(excluded)
    assert relative_volume(series) != pytest.approx(included)


def test_only_the_most_recent_twenty_priors_are_used():
    """A longer history must not widen the window - 30 prior sessions still means 20."""
    old_noise = [1_000_000] * 10          # ancient, must be ignored
    recent = [100] * 20
    assert relative_volume(_volumes(old_noise + recent, 250)) == pytest.approx(2.5)


def test_twenty_one_prior_sessions_still_uses_twenty():
    series = _volumes([500] + [100] * 20, 200)
    assert relative_volume(series) == pytest.approx(2.0)


# --------------------------------------------------------------------- insufficient history
def test_fewer_than_twenty_priors_returns_none():
    """Silently averaging over 19 sessions and calling it the same metric is how a number
    stops meaning anything."""
    assert relative_volume(_volumes([100] * 19, 300)) is None


def test_exactly_twenty_priors_is_the_boundary():
    assert relative_volume(_volumes([100] * 19, 300)) is None
    assert relative_volume(_volumes([100] * 20, 300)) is not None


def test_empty_or_missing_series_returns_none():
    assert relative_volume(None) is None
    assert relative_volume(pd.Series([], dtype="float64")) is None


def test_nan_priors_that_drop_below_twenty_return_none():
    volumes = [100] * 20
    volumes[5] = float("nan")
    assert relative_volume(_volumes(volumes, 300)) is None


def test_zero_average_volume_returns_none():
    assert relative_volume(_volumes([0] * 20, 300)) is None


def test_nan_current_volume_returns_none():
    assert relative_volume(_volumes([100] * 20, float("nan"))) is None


# --------------------------------------------------------------------- versioned semantics
def test_definition_metadata_is_explicit():
    assert RELATIVE_VOLUME_LOOKBACK == 20
    assert RELATIVE_VOLUME_DEFINITION == {
        "definition": "current_volume / mean_prior_volume",
        "lookback_sessions": 20,
        "minimum_required_sessions": 20,
        "includes_current_session": False,
        "definition_version": "2.0",
    }


def test_definition_reaches_the_observation(movers):
    """Reports written before Phase 3 used a 10-session window and are not rewritten, so a
    reader must be able to tell which definition produced the number in front of them."""
    from conftest import NOW, REPORT_DATE, SESSION

    from adapters.market_adapter import MarketAdapter
    from core import Metric

    gainers, _ = movers
    adapter = MarketAdapter(SESSION, NOW, report_date=REPORT_DATE)
    obs = next(o for o in adapter.observe_movers(gainers, "gainer")
               if o.metric is Metric.STOCK_RELATIVE_VOLUME)
    assert obs.metadata["definition_version"] == "2.0"
    assert obs.metadata["lookback_sessions"] == 20
    assert obs.metadata["includes_current_session"] is False


def test_fetch_window_is_long_enough_for_the_definition():
    """A one-month download lands right on the 21-session boundary, so a single missing day
    would drop the metric for the whole universe."""
    import inspect
    source = inspect.getsource(market.get_movers)
    assert 'period="3mo"' in source
