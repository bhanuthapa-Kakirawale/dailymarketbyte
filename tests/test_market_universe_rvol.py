"""market.get_universe_relative_volume: bulk RVOL across a wide universe, not just movers.
Offline only - market._bulk_download_universe_ohlcv is monkeypatched, never a live yfinance
call.
"""
import pandas as pd
import pytest

import market
from conftest_universe import RECAP_DATE, fake_bulk_ohlcv, synthetic_universe, universe_sessions


def test_returns_rows_for_the_full_universe_not_just_movers(monkeypatch):
    universe = synthetic_universe(30)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    rows, skip_reasons = market.get_universe_relative_volume(universe, recap_date, prev_date)
    assert len(rows) == 30
    assert skip_reasons == {}
    assert {r["symbol"] for r in rows} == set(universe)


def test_relative_volume_formula_matches_market_relative_volume_directly(monkeypatch):
    """No second RVOL formula - the same market.relative_volume() answer for the same series."""
    universe = synthetic_universe(1)
    symbol = next(iter(universe))
    volumes = [1_000_000.0] * 62 + [6_000_000.0]
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, volume_map={symbol: volumes})

    rows, _ = market.get_universe_relative_volume(universe, recap_date, prev_date)
    expected = market.relative_volume(pd.Series(volumes))
    assert rows[0]["volx"] == pytest.approx(expected)
    assert rows[0]["volx"] == pytest.approx(6.0)


def test_skip_reason_no_recap_row(monkeypatch):
    universe = synthetic_universe(3)
    absent = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, missing=[absent])

    _, skip_reasons = market.get_universe_relative_volume(universe, recap_date, prev_date)
    assert skip_reasons[absent] == "no_recap_row"


def test_skip_reason_backfill_pending(monkeypatch):
    universe = synthetic_universe(3)
    lagging = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, nan_close=[lagging])

    _, skip_reasons = market.get_universe_relative_volume(universe, recap_date, prev_date)
    assert skip_reasons[lagging] == "backfill_pending"


def test_skip_reason_date_gap(monkeypatch):
    universe = synthetic_universe(3)
    gapped = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, date_gap=[gapped])

    _, skip_reasons = market.get_universe_relative_volume(universe, recap_date, prev_date)
    assert skip_reasons[gapped] == "date_gap"


def test_skip_reason_insufficient_relative_volume_history(monkeypatch):
    universe = synthetic_universe(3)
    ipo = next(iter(universe))
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe, short_history={ipo: 8})

    rows, skip_reasons = market.get_universe_relative_volume(universe, recap_date, prev_date)
    assert skip_reasons[ipo] == "insufficient_relative_volume_history"
    assert ipo not in {r["symbol"] for r in rows}


def test_no_ranking_or_truncation_every_usable_symbol_is_returned():
    """Unlike get_movers, there is no top-n cutoff - a wide universe keeps every valid row."""
    import inspect
    source = inspect.getsource(market.get_universe_relative_volume)
    assert ".head(" not in source and ".tail(" not in source


def test_get_movers_is_untouched_by_this_change(monkeypatch):
    """Regression guard: the new bulk path must carry zero risk to get_movers's own
    already-verified NaN-Close-backfill/date-alignment behaviour."""
    universe = synthetic_universe(12)
    idx, recap_date, prev_date = universe_sessions(sessions=63, recap_date=RECAP_DATE)
    monkeypatch.setattr(market.time, "sleep", lambda *_a, **_k: None)

    frames = {}
    for symbol in universe:
        close = pd.Series([100.0] * 63, index=idx)
        vol = pd.Series([1_000_000.0] * 63, index=idx)
        frames[f"{symbol}.NS"] = pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": vol})
    raw = pd.concat(frames, axis=1)
    monkeypatch.setattr("yfinance.download", lambda *a, **k: raw)

    gainers, losers = market.get_movers(universe, recap_date, prev_date, n=5)
    assert len(gainers) == 5 and len(losers) == 5
