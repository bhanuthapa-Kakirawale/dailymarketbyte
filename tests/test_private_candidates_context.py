"""Regression tests: `private_candidates/context.py`'s OHLCV read helpers must exclude
holiday-placeholder rows (OHLCV integrity audit 2026-10-26, `PRIVATE-D1-LIQUIDITY-CONTAMINATION`).

A holiday placeholder is a provider artifact persisted with `quality_status=OK` (the write path
has no calendar filter, by design - see `market._write_through_ohlcv`): `open=high=low=close`
= the prior session's close, `volume=0`. Radar already excludes these via
`radar/ohlcv_service.py::apply_session_spine`; `private_candidates/context.py::daily_rows` and
`sector_member_rows` did not, so a 20-session lookback window spanning one of these dates silently
pulled a zero-volume/flat-close row into LIQUIDITY, VOLATILITY_FIT, MTF_CONFIRMATION and
SECTOR_ALIGNMENT inputs. Real 2026 holiday dates are used throughout, from
`core.trading_calendar.NSE_TRADING_HOLIDAYS` - never a hand-rolled list.
"""
import datetime as dt

import pytest

from core.trading_calendar import NSE_TRADING_HOLIDAYS, SessionCalendar
from private_candidates import context, metrics
from storage.ohlcv_models import QualityStatus
from storage.ohlcv_repository import OHLCVRow

HOLIDAY_OCT = dt.date(2026, 10, 2)     # confirmed in the audit: 183 OK placeholders, 17 BACKFILL_PENDING
HOLIDAY_JAN = dt.date(2026, 1, 15)     # confirmed in the audit: 189 OK placeholders, 11 BACKFILL_PENDING
assert HOLIDAY_OCT in NSE_TRADING_HOLIDAYS[2026]
assert HOLIDAY_JAN in NSE_TRADING_HOLIDAYS[2026]

_RETRIEVED = dt.datetime(2026, 10, 2, 16, 0, tzinfo=dt.timezone.utc)


class _FakeRepo:
    """Stands in for `DeskRepository` - `context.py` only ever calls `.ohlcv_rows(...)`."""

    def __init__(self, rows: list):
        self._rows = rows

    def ohlcv_rows(self, symbols, start: dt.date, end: dt.date) -> list:
        symbols = set(symbols)
        return [r for r in self._rows
               if r.symbol in symbols and start <= r.session_date <= end]


def _bar(symbol, date, *, close, open_=None, high=None, low=None, volume=0.0,
        status=QualityStatus.OK) -> OHLCVRow:
    open_ = close if open_ is None else open_
    high = close if high is None else high
    low = close if low is None else low
    return OHLCVRow(symbol=symbol, session_date=date, source="yahoo", open=open_, high=high,
                    low=low, close=close, volume=volume, retrieved_at=_RETRIEVED,
                    quality_status=status)


def _placeholder(symbol, date, prior_close) -> OHLCVRow:
    """The exact audit signature: O=H=L=C=prior close, volume=0, status OK."""
    return _bar(symbol, date, close=prior_close, volume=0.0, status=QualityStatus.OK)


def _backfill_pending(symbol, date) -> OHLCVRow:
    return OHLCVRow(symbol=symbol, session_date=date, source="yahoo", open=None, high=None,
                    low=None, close=None, volume=None, retrieved_at=_RETRIEVED,
                    quality_status=QualityStatus.BACKFILL_PENDING)


def _genuine_sessions(symbol, n, end, *, start_close=100.0, volume=1_000_000.0) -> list:
    """`n` consecutive canonical trading-calendar sessions ending on or before `end`,
    closes rising by 1/session, oldest first."""
    cal = SessionCalendar()
    days = []
    d = end
    while len(days) < n:
        if cal.status(d) == "SESSION":
            days.append(d)
        d -= dt.timedelta(days=1)
    days.reverse()
    return [_bar(symbol, day, close=start_close + i, volume=volume) for i, day in enumerate(days)]


# ------------------------------------------------------------------ daily_rows()
def test_daily_rows_excludes_ok_status_holiday_placeholder():
    rows = _genuine_sessions("IDEA", 5, HOLIDAY_OCT - dt.timedelta(days=1))
    rows.append(_placeholder("IDEA", HOLIDAY_OCT, prior_close=rows[-1].close))
    rows.append(_bar("IDEA", HOLIDAY_OCT + dt.timedelta(days=1), close=110.0))
    repo = _FakeRepo(rows)
    result = context.daily_rows(repo, "IDEA", HOLIDAY_OCT + dt.timedelta(days=1))
    assert HOLIDAY_OCT not in [r["date"] for r in result]


def test_daily_rows_still_excludes_backfill_pending():
    session = dt.date(2026, 10, 5)     # genuine Monday session
    rows = _genuine_sessions("IDEA", 5, session - dt.timedelta(days=1))
    rows.append(_backfill_pending("IDEA", session))
    repo = _FakeRepo(rows)
    result = context.daily_rows(repo, "IDEA", session)
    assert session not in [r["date"] for r in result]
    assert len(result) == 5


def test_daily_rows_keeps_genuine_trading_sessions():
    session = dt.date(2026, 10, 5)
    rows = _genuine_sessions("IDEA", 10, session)
    repo = _FakeRepo(rows)
    result = context.daily_rows(repo, "IDEA", session)
    assert len(result) == 10
    assert result[-1]["date"] == session


def test_daily_rows_mixed_dataset_both_audited_holidays():
    session = dt.date(2026, 10, 5)
    rows = _genuine_sessions("IDEA", 15, session)
    rows.append(_placeholder("IDEA", HOLIDAY_JAN, prior_close=99.0))
    rows.append(_placeholder("IDEA", HOLIDAY_OCT, prior_close=104.0))
    repo = _FakeRepo(rows)
    result = context.daily_rows(repo, "IDEA", session, lookback_days=400)
    dates = [r["date"] for r in result]
    assert HOLIDAY_JAN not in dates
    assert HOLIDAY_OCT not in dates
    assert len(result) == 15


def test_daily_rows_lookback_window_counts_only_real_sessions():
    """20 genuine prior sessions + the recap session + one placeholder inside the window ->
    the raw store has 22 rows, but exactly 21 genuine sessions must come back (never 22, and
    the placeholder must never be mistaken for one of the 20 real priors)."""
    session = dt.date(2026, 10, 5)
    rows = _genuine_sessions("IDEA", 21, session)      # 20 priors + the recap session itself
    oct2_prior_close = next(r.close for r in rows if r.session_date == HOLIDAY_OCT - dt.timedelta(days=1))
    rows.append(_placeholder("IDEA", HOLIDAY_OCT, prior_close=oct2_prior_close))
    repo = _FakeRepo(rows)
    result = context.daily_rows(repo, "IDEA", session, lookback_days=60)
    assert len(result) == 21
    assert HOLIDAY_OCT not in [r["date"] for r in result]
    avg = metrics.average_volume(result, window=20)
    assert avg == pytest.approx(1_000_000.0)


def test_average_volume_reproduces_and_then_fixes_placeholder_contamination():
    """The IDEA-shaped mechanism: a zero-volume placeholder inside the 20-session window pulls
    the average down when read unfiltered (the pre-fix code path), and the contamination is
    gone once read through `context.daily_rows()` (the fixed path)."""
    session = dt.date(2026, 10, 5)
    rows = _genuine_sessions("IDEA", 21, session, volume=1_000_000.0)
    oct2_prior_close = next(r.close for r in rows if r.session_date == HOLIDAY_OCT - dt.timedelta(days=1))
    rows.append(_placeholder("IDEA", HOLIDAY_OCT, prior_close=oct2_prior_close))
    repo = _FakeRepo(rows)

    # Pre-fix code path: the same OK-status filter `daily_rows` used to apply, nothing else.
    raw_rows = repo.ohlcv_rows(["IDEA"], session - dt.timedelta(days=60), session)
    unfiltered = [{"date": r.session_date, "open": r.open, "high": r.high, "low": r.low,
                  "close": r.close, "volume": r.volume}
                 for r in sorted(raw_rows, key=lambda r: r.session_date)
                 if r.quality_status == QualityStatus.OK]
    contaminated_avg = metrics.average_volume(unfiltered, window=20)

    fixed = context.daily_rows(repo, "IDEA", session, lookback_days=60)
    clean_avg = metrics.average_volume(fixed, window=20)

    assert len(unfiltered) == 22       # includes the placeholder - the pre-fix bug
    assert len(fixed) == 21            # placeholder excluded - the fix
    assert contaminated_avg < clean_avg
    assert clean_avg == pytest.approx(1_000_000.0)
    distortion_pct = (contaminated_avg - clean_avg) / clean_avg * 100
    assert distortion_pct < 0           # contaminated average is pulled down, matching the audit


def test_daily_rows_consistent_across_symbols_same_holiday_pattern():
    session = dt.date(2026, 10, 5)
    rows = []
    for symbol in ("IDEA", "KOTAKBANK"):
        symbol_rows = _genuine_sessions(symbol, 10, session)
        prior_close = next(r.close for r in symbol_rows if r.session_date == HOLIDAY_OCT - dt.timedelta(days=1))
        symbol_rows.append(_placeholder(symbol, HOLIDAY_OCT, prior_close=prior_close))
        rows.extend(symbol_rows)
    repo = _FakeRepo(rows)
    for symbol in ("IDEA", "KOTAKBANK"):
        result = context.daily_rows(repo, symbol, session)
        assert HOLIDAY_OCT not in [r["date"] for r in result]
        assert len(result) == 10


def test_daily_rows_missing_data_stays_none_not_zero():
    session = dt.date(2026, 10, 5)
    rows = _genuine_sessions("NEWLIST", 3, session)    # below the 5-session floor
    repo = _FakeRepo(rows)
    result = context.daily_rows(repo, "NEWLIST", session)
    assert metrics.average_volume(result, window=20) is None
    assert metrics.atr14(result, window=14) is None


# ------------------------------------------------------------------ sector_member_rows()
def test_sector_member_rows_excludes_holiday_placeholder_for_each_member():
    session = dt.date(2026, 10, 5)
    rows = []
    for symbol in ("TCS", "INFY"):
        symbol_rows = _genuine_sessions(symbol, 8, session)
        prior_close = next(r.close for r in symbol_rows if r.session_date == HOLIDAY_OCT - dt.timedelta(days=1))
        symbol_rows.append(_placeholder(symbol, HOLIDAY_OCT, prior_close=prior_close))
        rows.extend(symbol_rows)
    repo = _FakeRepo(rows)
    result = context.sector_member_rows(repo, ["TCS", "INFY"], session)
    assert set(result) == {"TCS", "INFY"}
    for symbol in ("TCS", "INFY"):
        dates = [r["date"] for r in result[symbol]]
        assert HOLIDAY_OCT not in dates
        assert len(dates) == 8


def test_sector_member_rows_genuine_sessions_only_unchanged():
    session = dt.date(2026, 10, 5)
    rows = _genuine_sessions("TCS", 8, session) + _genuine_sessions("INFY", 8, session)
    repo = _FakeRepo(rows)
    result = context.sector_member_rows(repo, ["TCS", "INFY"], session)
    assert len(result["TCS"]) == 8
    assert len(result["INFY"]) == 8


def test_sector_member_rows_empty_members_returns_empty_dict():
    repo = _FakeRepo([])
    assert context.sector_member_rows(repo, [], dt.date(2026, 10, 5)) == {}
