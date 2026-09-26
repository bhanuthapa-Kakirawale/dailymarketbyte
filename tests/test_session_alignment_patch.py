"""Data-reliability patch: holiday / session alignment.

Provider rows never define trading sessions. The canonical spine is NSE's published holiday
list + the benchmark index's own bars (`core.trading_calendar`). Covers the verified 2026
cases: holiday placeholders on 05-01, 05-28, 06-26, 09-14 (O=H=L=C = previous close, volume 0)
and 09-22, a REAL session every Yahoo Indian index feed is missing. Fully offline.
"""
import datetime as dt

import pandas as pd
import pytest

import market
from core import trading_calendar as tc
from core.trading_calendar import AlignmentAudit, SessionCalendar, align_rows
from radar import session_alignment

D = dt.date
PLACEHOLDER_DATES = [D(2026, 5, 1), D(2026, 5, 28), D(2026, 6, 26), D(2026, 9, 14)]
BENCHMARK_GAP = D(2026, 9, 22)
NEXT_SESSION = {D(2026, 5, 1): (D(2026, 5, 4), D(2026, 4, 30)),
                D(2026, 5, 28): (D(2026, 5, 29), D(2026, 5, 27)),
                D(2026, 6, 26): (D(2026, 6, 29), D(2026, 6, 25)),
                D(2026, 9, 14): (D(2026, 9, 15), D(2026, 9, 11))}


def _weekdays(end: dt.date, n: int) -> list:
    return [ts.date() for ts in pd.bdate_range(end=end, periods=n)]


def _canonical(end: dt.date, n: int) -> list:
    """The last `n` canonical 2026 sessions ending at `end` (calendar only)."""
    cal = SessionCalendar()
    return cal.sessions_between(end - dt.timedelta(days=n * 2 + 30), end)[-n:]


def _index_dates_without_gap(end: dt.date, n: int = 120) -> list:
    """Benchmark bars as Yahoo really has them: canonical sessions minus 2026-09-22."""
    return [d for d in _canonical(end, n) if d != BENCHMARK_GAP]


def _frame(dates, closes, volumes=None, placeholders=()):
    """A yfinance-shaped per-symbol frame on `dates`, plus a holiday placeholder row
    (O=H=L=C = previous close, volume 0 - the verified real signature) on each of
    `placeholders` that falls inside the range."""
    volumes = volumes or [1_000_000.0] * len(dates)
    rows = {d: (c, c * 1.01, c * 0.99, c, v) for d, c, v in zip(dates, closes, volumes)}
    for h in placeholders:
        prior = [d for d in dates if d < h]
        if prior and h < dates[-1]:
            pc = rows[prior[-1]][3]
            rows[h] = (pc, pc, pc, pc, 0.0)
    idx = sorted(rows)
    return pd.DataFrame({"Open": [rows[d][0] for d in idx], "High": [rows[d][1] for d in idx],
                         "Low": [rows[d][2] for d in idx], "Close": [rows[d][3] for d in idx],
                         "Volume": [rows[d][4] for d in idx]}, index=pd.DatetimeIndex(idx))


def _stub_download(monkeypatch, frames: dict):
    import yfinance as yf
    raw = pd.concat({f"{s}.NS": f for s, f in frames.items()}, axis=1, sort=False)
    monkeypatch.setattr(market.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(yf, "download", lambda *a, **k: raw)
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv", lambda syms, period="3mo": raw)
    return raw


def _universe(n: int) -> dict:
    return {f"STK{i:02d}": f"Stock {i}" for i in range(n)}


# --------------------------------------------------------------------------- 13: synthetic D1..D4
def test_synthetic_d1_d2_d3_d4_invalid_provider_row_excluded_and_audited():
    d1, d2, d3, d4 = D(2031, 3, 3), D(2031, 3, 4), D(2031, 3, 5), D(2031, 3, 6)
    # 2031 is not in NSE_TRADING_HOLIDAYS -> the benchmark-index spine is the canonical source.
    cal = SessionCalendar.from_index_dates([d1, d2, d4])
    provider = [{"date": d, "close": c} for d, c in ((d1, 100.0), (d2, 101.0), (d3, 101.0),
                                                     (d4, 103.0))]
    audit = AlignmentAudit()
    aligned = align_rows(provider, cal, audit=audit)
    assert [r["date"] for r in aligned] == [d1, d2, d4]
    prev_close = aligned[-2]["close"]
    assert aligned[-2]["date"] == d2 and prev_close == 101.0
    assert cal.previous_session(d4) == d2
    assert audit.non_session_rows_ignored == 1
    assert audit.non_session_dates == {d3.isoformat(): 1}
    assert audit.provider_rows_seen == 4 and audit.canonical_rows_used == 3
    assert audit.calendar_covered is False          # recorded: index-spine fallback year


# --------------------------------------------------------------------------- 12: known 2026 dates
@pytest.mark.parametrize("holiday", PLACEHOLDER_DATES)
def test_known_placeholder_dates_are_non_sessions_and_next_session_prev_is_correct(holiday):
    cal = SessionCalendar.from_index_dates(_index_dates_without_gap(D(2026, 9, 24), 200))
    assert cal.status(holiday) == tc.NON_SESSION
    nxt, prev = NEXT_SESSION[holiday]
    assert cal.previous_session(nxt) == prev


def test_2026_09_22_is_a_real_session_the_benchmark_is_missing():
    """NSE's holiday list does not close 22 Sep 2026; Yahoo's index has no bar for it."""
    cal = SessionCalendar.from_index_dates(_index_dates_without_gap(D(2026, 9, 24), 200))
    assert cal.status(BENCHMARK_GAP) == tc.SESSION
    assert cal.previous_session(D(2026, 9, 23)) == BENCHMARK_GAP
    assert BENCHMARK_GAP in cal.benchmark_gaps()
    spine = session_alignment.canonical_session_spine(
        [{"date": d, "close": 1.0} for d in _index_dates_without_gap(D(2026, 9, 24), 200)])
    assert BENCHMARK_GAP in spine
    assert not set(PLACEHOLDER_DATES) & spine


def test_index_alignment_check_passes_for_15_sep_and_fails_session_level_for_23_sep():
    ok = market.check_index_session_alignment(_index_dates_without_gap(D(2026, 9, 15)))
    assert ok["status"] == "ALIGNED"
    assert ok["canonical_previous_session"] == "2026-09-11"
    with pytest.raises(market.SessionAlignmentError, match="2026-09-22"):
        market.check_index_session_alignment(_index_dates_without_gap(D(2026, 9, 23)))
    # once the benchmark has the real 22 Sep bar, 23 Sep is fine
    full = _canonical(D(2026, 9, 23), 120)
    assert market.check_index_session_alignment(full)["canonical_previous_session"] == "2026-09-22"


def test_get_market_prev_date_is_canonical_and_records_alignment(monkeypatch):
    dates = _index_dates_without_gap(D(2026, 9, 15), 120)
    nifty = _frame(dates, [20000.0 + i for i in range(len(dates))])
    monkeypatch.setattr(market, "_nifty_history_with_backfill_check", lambda *a, **k: nifty)
    monkeypatch.setattr(market, "history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    m = market.get_market()
    assert (m["recap_date"], m["prev_date"]) == (D(2026, 9, 15), D(2026, 9, 11))
    assert m["session_alignment"]["status"] == "ALIGNED"
    assert "2026-09-22" not in m["session_alignment"]["benchmark_missing_sessions"]

    dates23 = _index_dates_without_gap(D(2026, 9, 23), 120)
    monkeypatch.setattr(market, "_nifty_history_with_backfill_check",
                        lambda *a, **k: _frame(dates23, [20000.0] * len(dates23)))
    with pytest.raises(market.SessionAlignmentError):
        market.get_market()


# --------------------------------------------------------------------------- 2/5/7/14: movers
def test_movers_15_sep_with_14_sep_placeholder_on_every_stock_no_longer_aborts(monkeypatch):
    universe = _universe(20)
    dates = _canonical(D(2026, 9, 15), 63)
    frames = {s: _frame(dates, [100.0 + i * 0.1 + k for i in range(len(dates))],
                        placeholders=PLACEHOLDER_DATES)
              for k, s in enumerate(universe)}
    _stub_download(monkeypatch, frames)
    session_dates = _index_dates_without_gap(D(2026, 9, 15))
    g, lo, audit = market.get_movers_audited(universe, D(2026, 9, 15), D(2026, 9, 11), n=5,
                                             session_dates=session_dates)
    assert audit["universe_observed"] == 20 and audit["skipped_date_gap"] == []
    # previous close = 11 Sep's real close, never the 14 Sep placeholder row
    assert all(r["prev_date"] == "2026-09-11" for r in g + lo)
    sa = audit["session_alignment"]
    assert sa["non_session_dates"]["2026-09-14"] == 20
    assert sa["status"] == "ALIGNED_WITH_EXCLUSIONS"
    # coverage counts aligned, validated symbols only - placeholders add nothing
    assert audit["coverage_pct"] == 100.0
    # the extreme-move guard runs on the aligned pair
    assert all(r["validation"]["status"] == "VALIDATED" for r in g + lo)


def test_movers_pre_patch_behaviour_would_have_aborted(monkeypatch):
    """Regression pin: without alignment, the 14 Sep placeholder is `[-2]` for every stock."""
    universe = _universe(12)
    dates = _canonical(D(2026, 9, 15), 63)
    frames = {s: _frame(dates, [100.0] * len(dates), placeholders=[D(2026, 9, 14)])
              for s in universe}
    _stub_download(monkeypatch, frames)
    monkeypatch.setattr(market, "_drop_non_sessions", lambda d, cal, audit=None: d)
    with pytest.raises(RuntimeError, match="clean data"):
        market.get_movers_audited(universe, D(2026, 9, 15), D(2026, 9, 11), n=5)


def test_movers_23_sep_uses_real_22_sep_close_as_previous(monkeypatch):
    """22 Sep stock rows are REAL; a 23 Sep move is 23/22, never a two-session 23/21 move."""
    universe = _universe(12)
    dates = _canonical(D(2026, 9, 23), 63)
    closes = {s: [100.0] * (len(dates) - 3) + [100.0, 110.0, 111.0] for s in universe}  # 21, 22, 23
    frames = {s: _frame(dates, closes[s]) for s in universe}
    _stub_download(monkeypatch, frames)
    g, _lo, audit = market.get_movers_audited(universe, D(2026, 9, 23), D(2026, 9, 22), n=5)
    assert all(r["prev_date"] == "2026-09-22" for r in g)
    assert all(r["pct"] == pytest.approx(111.0 / 110.0 * 100 - 100) for r in g)
    assert audit["session_alignment"]["non_session_rows_ignored"] == 0


def test_coverage_does_not_count_a_symbol_whose_only_recent_row_is_a_placeholder(monkeypatch):
    universe = _universe(12)
    dates = _canonical(D(2026, 9, 15), 63)
    frames = {s: _frame(dates, [100.0] * len(dates), placeholders=[D(2026, 9, 14)])
              for s in universe}
    stale = "STK00"   # has 11 Sep + the 14 Sep placeholder, but no 15 Sep session
    frames[stale] = _frame(dates[:-1] + [D(2026, 9, 14)], [100.0] * len(dates))
    _stub_download(monkeypatch, frames)
    _g, _l, audit = market.get_movers_audited(universe, D(2026, 9, 15), D(2026, 9, 11), n=5)
    assert audit["universe_observed"] == 11
    assert stale in audit["missing"]
    assert audit["coverage_pct"] == pytest.approx(100.0 * 11 / 12, abs=0.01)


# --------------------------------------------------------------------------- 8: one bad symbol
def test_one_malformed_symbol_does_not_abort_the_run(monkeypatch):
    universe = _universe(12)
    dates = _canonical(D(2026, 9, 15), 63)
    frames = {s: _frame(dates, [100.0 + k] * len(dates)) for k, s in enumerate(universe)}
    _stub_download(monkeypatch, frames)
    real_prepare = market._prepare_series
    calls = {"n": 0}

    def flaky(full, recap_date, calendar=None, audit=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ValueError("garbage provider frame")
        return real_prepare(full, recap_date, calendar, audit)
    monkeypatch.setattr(market, "_prepare_series", flaky)
    g, lo, audit = market.get_movers_audited(universe, D(2026, 9, 15), D(2026, 9, 11), n=5)
    assert len(audit["symbols_excluded"]["malformed"]) == 1
    assert audit["universe_observed"] == 11 and len(g) == 5 and len(lo) == 5


# --------------------------------------------------------------------------- 3/4: RVOL + technical
def test_rvol_prior_window_excludes_non_session_rows(monkeypatch):
    universe = _universe(1)
    s = next(iter(universe))
    dates = _canonical(D(2026, 9, 15), 63)
    vols = [1_000_000.0] * (len(dates) - 1) + [3_000_000.0]
    _stub_download(monkeypatch, {s: _frame(dates, [100.0] * len(dates), vols,
                                           placeholders=PLACEHOLDER_DATES)})
    rows, skips = market.get_universe_relative_volume(universe, D(2026, 9, 15), D(2026, 9, 11))
    assert skips == {}
    # a zero-volume placeholder in the prior-20 window would have given 3e6 / (19e6/20) = 3.16x
    assert rows[0]["volx"] == pytest.approx(3.0)


def test_technical_series_excludes_non_session_rows_and_keeps_order(monkeypatch):
    universe = _universe(1)
    s = next(iter(universe))
    dates = _canonical(D(2026, 9, 15), 90)
    _stub_download(monkeypatch, {s: _frame(dates, [100.0 + i for i in range(len(dates))],
                                           placeholders=PLACEHOLDER_DATES)})
    audit = AlignmentAudit()
    series, skips = market.get_universe_technical_series(universe, D(2026, 9, 15), D(2026, 9, 11),
                                                         audit=audit)
    got = [r["date"] for r in series[s]]
    assert got == dates                                    # canonical sessions, in order
    assert not set(PLACEHOLDER_DATES) & set(got)
    assert audit.non_session_rows_ignored == 3             # 05-28, 06-26, 09-14 in range


# --------------------------------------------------------------------------- 6: Radar evidence
def test_radar_spine_filter_drops_placeholders_but_keeps_real_22_sep_rows():
    bench = [{"date": d, "close": 1.0} for d in _index_dates_without_gap(D(2026, 9, 24), 150)]
    spine = session_alignment.canonical_session_spine(bench)
    stock = [{"date": d, "close": 1.0} for d in sorted(set(_canonical(D(2026, 9, 24), 150))
                                                      | set(PLACEHOLDER_DATES))]
    aligned, dropped = session_alignment.align_series_to_spine(stock, spine)
    dates = [r["date"] for r in aligned]
    assert BENCHMARK_GAP in dates
    assert dropped == sum(1 for h in PLACEHOLDER_DATES if h >= min(spine))
    assert not set(PLACEHOLDER_DATES) & set(dates)
    lst = session_alignment.canonical_session_list(bench)
    assert lst[lst.index(D(2026, 9, 23)) - 1] == BENCHMARK_GAP
    assert session_alignment.benchmark_missing_sessions(bench) == [BENCHMARK_GAP]


# --------------------------------------------------------------------------- 9/10/11
def test_duplicate_and_out_of_order_rows_are_deterministic():
    cal = SessionCalendar()
    rows = [{"date": D(2026, 9, 17), "close": 3.0}, {"date": D(2026, 9, 15), "close": 1.0},
            {"date": D(2026, 9, 16), "close": 2.0}, {"date": D(2026, 9, 16), "close": 2.5}]
    audit = AlignmentAudit()
    out = align_rows(rows, cal, audit=audit)
    assert [(r["date"], r["close"]) for r in out] == [
        (D(2026, 9, 15), 1.0), (D(2026, 9, 16), 2.5), (D(2026, 9, 17), 3.0)]
    assert audit.duplicate_rows_removed == 1 and audit.out_of_order_series == 1
    assert align_rows(list(reversed(rows)), cal)[1]["close"] == 2.0   # "last" = input order


def test_market_prepare_series_sorts_and_dedupes_provider_frame():
    dates = _canonical(D(2026, 9, 15), 10)
    f = _frame(dates, [float(i) for i in range(len(dates))])
    shuffled = pd.concat([f.iloc[[5]], f.iloc[::-1]])
    d = market._prepare_series(shuffled, D(2026, 9, 15), market.session_calendar())
    assert list(d.index.date) == dates
    assert d.index[-1].date() == D(2026, 9, 15)


def test_missing_canonical_session_is_never_filled(monkeypatch):
    universe = _universe(12)
    dates = _canonical(D(2026, 9, 15), 63)
    frames = {s: _frame(dates, [100.0] * len(dates)) for s in universe}
    gap = "STK03"
    frames[gap] = _frame([d for d in dates if d != D(2026, 9, 11)], [100.0] * (len(dates) - 1))
    _stub_download(monkeypatch, frames)
    _g, _l, audit = market.get_movers_audited(universe, D(2026, 9, 15), D(2026, 9, 11), n=5)
    assert audit["skipped_date_gap"] == [gap]              # symbol-level, not filled, not fatal
    out = align_rows([{"date": D(2026, 9, 10)}, {"date": D(2026, 9, 15)}], SessionCalendar())
    assert len(out) == 2                                    # alignment only ever removes rows


# --------------------------------------------------------------------------- calendar edges
def test_special_sessions_and_listed_holiday_index_bar():
    cal = SessionCalendar()
    assert cal.status(D(2025, 10, 21)) == tc.SESSION        # Muhurat, a listed holiday
    assert cal.status(D(2025, 2, 1)) == tc.SESSION          # Budget Saturday
    assert cal.status(D(2026, 9, 19)) == tc.NON_SESSION     # ordinary Saturday
    odd = SessionCalendar.from_index_dates([D(2026, 10, 2)])
    assert odd.status(D(2026, 10, 2)) == tc.SESSION         # index bar = session evidence
    assert odd.benchmark_bars_on_listed_holidays() == [D(2026, 10, 2)]


def test_uncovered_year_falls_back_to_index_spine_then_unknown():
    idx = [D(2031, 1, 6), D(2031, 1, 8)]
    cal = SessionCalendar.from_index_dates(idx)
    assert cal.status(D(2031, 1, 7)) == tc.NON_SESSION      # inside the index range, no bar
    assert cal.status(D(2031, 1, 9)) == tc.UNKNOWN          # beyond it: never guessed
    assert align_rows([{"date": D(2031, 1, 9)}], cal)       # UNKNOWN rows are kept
    assert SessionCalendar().previous_session(D(2031, 1, 8)) is None


def test_holiday_list_matches_the_verified_local_benchmark_gaps():
    """Every weekday Yahoo's ^NSEI skipped between 2025-09-23 and 2026-09-24 (verified against
    the local OHLCV store) is an official holiday, except 2026-09-22."""
    skipped = [D(2025, 10, 2), D(2025, 10, 22), D(2025, 11, 5), D(2025, 12, 25), D(2026, 1, 15),
               D(2026, 1, 26), D(2026, 3, 3), D(2026, 3, 26), D(2026, 3, 31), D(2026, 4, 3),
               D(2026, 4, 14), D(2026, 5, 1), D(2026, 5, 28), D(2026, 6, 26), D(2026, 9, 14),
               D(2026, 9, 22)]
    cal = SessionCalendar()
    assert [d for d in skipped if cal.status(d) == tc.SESSION] == [BENCHMARK_GAP]


# --------------------------------------------------------------------------- stale / in-progress rows
def test_write_through_never_persists_a_session_still_trading(monkeypatch):
    """The raw OHLCV cache must not store today's in-progress bar as a finished OK bar."""
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    import config
    dates = _canonical(D(2026, 9, 25), 5)
    raw = pd.concat({"STK00.NS": _frame(dates, [100.0] * 5)}, axis=1)
    monkeypatch.setattr(market, "now_ist",
                        lambda: dt.datetime(2026, 9, 25, 11, 44, tzinfo=config.IST))
    market._write_through_ohlcv(raw, ["STK00"], D(2026, 9, 24))
    store = OHLCVStore(default_db_path(config.OUT_DIR))
    got = {r.session_date for r in store.get_range(["STK00"], dates[0], dates[-1], source="yahoo")}
    store.close()
    assert D(2026, 9, 25) not in got and D(2026, 9, 24) in got

    monkeypatch.setattr(market, "now_ist",
                        lambda: dt.datetime(2026, 9, 25, 16, 30, tzinfo=config.IST))
    market._write_through_ohlcv(raw, ["STK00"], D(2026, 9, 25))
    store = OHLCVStore(default_db_path(config.OUT_DIR))
    got = {r.session_date for r in store.get_range(["STK00"], dates[0], dates[-1], source="yahoo")}
    store.close()
    assert D(2026, 9, 25) in got                      # after the close it is a finished bar
