"""Benchmark gap recovery (second index source), cached non-final bar repair, minimal Radar
history rebuild. Fully offline: the NSE end-of-day index file and every Yahoo re-fetch are fakes.

Real case these encode: Yahoo's ^NSEI (and every Indian index feed) has no bar for Tue
2026-09-22, a real NSE session. NSE's own file has Nifty 50 22-Sep O 23454.05 H 23489
L 23285.75 C 23329.0, points change -85.3 -> implied previous close 23414.3 = Yahoo's 21-Sep
close. 23-Sep then closed 23446.8 (+0.505%), not the +0.139% two-session change.
"""
import datetime as dt

import pandas as pd
import pytest

import config
import market
from core.trading_calendar import SessionCalendar

D = dt.date
GAP, PREV, NEXT = D(2026, 9, 22), D(2026, 9, 21), D(2026, 9, 23)
C21, C22, C23 = 23414.3, 23329.0, 23446.8


def _canonical(end, n):
    return SessionCalendar().sessions_between(end - dt.timedelta(days=n * 2 + 30), end)[-n:]


def _nifty(end=NEXT, n=120, drop=(GAP,), closes=None):
    """A Yahoo-shaped ^NSEI frame on canonical sessions minus `drop`; 21/23 Sep carry the real
    closes so the continuity arithmetic is the verified one."""
    dates = [d for d in _canonical(end, n) if d not in drop]
    close = {d: 23000.0 + i for i, d in enumerate(dates)}
    close.update({PREV: C21, NEXT: C23})
    close.update(closes or {})
    c = [close[d] for d in dates]
    return pd.DataFrame({"Open": c, "High": [x * 1.004 for x in c], "Low": [x * 0.996 for x in c],
                         "Close": c, "Volume": [2e5] * len(c)}, index=pd.DatetimeIndex(dates))


def _archive_row(name="Nifty 50", day=GAP, o=23454.05, h=23489.0, lo=23285.75, c=C22,
                 chg=-85.3, pct=-0.36):
    return {"Index Name": name, "Index Date": day.strftime("%d-%m-%Y"), "Open Index Value": o,
            "High Index Value": h, "Low Index Value": lo, "Closing Index Value": c,
            "Points Change": chg, "Change(%)": pct}


def _fetch(rows=None, calls=None):
    rows = rows if rows is not None else {GAP: [_archive_row()]}

    def fetch(day):
        if calls is not None:
            calls.append(day)
        if day not in rows:
            return {"error": f"404 for {day}"}
        return {"rows": {r["Index Name"].upper(): r for r in rows[day]},
                "url": f"https://nsearchives.nseindia.com/content/indices/ind_close_all_"
                       f"{day.strftime('%d%m%Y')}.csv",
                "retrieved_at": "2026-09-25T12:00:00+00:00", "published_at": "Tue, 22 Sep 2026"}
    return fetch


# --------------------------------------------------------------------------- benchmark recovery
def test_missing_primary_session_is_recovered_with_full_provenance():
    d = _nifty()
    out, recs = market.recover_index_gaps(d, "^NSEI", fetch=_fetch())
    assert GAP not in set(d.index.date)                            # input untouched
    row = out[out.index.date == GAP].iloc[0]
    assert (row.Open, row.High, row.Low, row.Close) == (23454.05, 23489.0, 23285.75, C22)
    assert pd.isna(row.Volume)                                     # NSE volume is another unit
    assert list(out.index) == sorted(out.index) and len(out) == len(d) + 1
    (rec,) = recs
    for key in ("source", "session_date", "retrieval_time", "validation_status", "fallback_reason"):
        assert rec[key]
    assert rec["source"] == "NSE_INDEX_CLOSE_ARCHIVE" and rec["validation_status"] == "VALIDATED"
    assert rec["session_date"] == "2026-09-22" and rec["anchor_session"] == "2026-09-21"
    assert rec["checks"]["implied_previous_close"] == pytest.approx(C21)
    assert "PRIMARY_MISSING_CANONICAL_SESSION" in rec["fallback_reason"]


def test_get_market_23_sep_uses_recovered_22_sep_close_and_says_so(monkeypatch):
    monkeypatch.setattr(market, "_nifty_history_with_backfill_check", lambda *a, **k: _nifty())
    monkeypatch.setattr(market, "history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(market, "nse_index_close_archive", _fetch())
    m = market.get_market()
    assert (m["recap_date"], m["prev_date"]) == (NEXT, GAP)
    assert m["prev"] == C22 and m["pct"] == pytest.approx((C23 / C22 - 1) * 100)
    assert round(m["pct"], 3) == 0.505                              # not the 2-session 0.139
    a = m["session_alignment"]
    assert a["status"] == "ALIGNED_WITH_FALLBACK"
    assert a["previous_close_source"] == m["prev_source"] == "NSE_INDEX_CLOSE_ARCHIVE"
    assert a["benchmark_recovered_sessions"] == ["2026-09-22"]
    assert a["benchmark_recovery"][0]["validation_status"] == "VALIDATED"


def test_report_observation_records_the_fallback_previous_close(market_dict):
    from tests.conftest import build_test_report
    from core import Metric
    plain = build_test_report(market_dict)
    obs = [o for f in plain.facts_for(Metric.INDEX_CHANGE_PCT) if f.instrument == "NIFTY 50"
           for o in f.observations]
    assert obs and all("previous_close_source" not in o.metadata for o in obs)
    rec = build_test_report({**market_dict, "prev_source": "NSE_INDEX_CLOSE_ARCHIVE"})
    obs = [o for f in rec.facts_for(Metric.INDEX_CHANGE_PCT) if f.instrument == "NIFTY 50"
           for o in f.observations]
    assert obs[0].metadata["previous_close_source"] == "NSE_INDEX_CLOSE_ARCHIVE"


def test_fallback_is_used_only_for_a_real_canonical_session(declare_nse_holiday):
    calls = []
    # a weekday declared an NSE holiday is not a gap: nothing is fetched, nothing added
    declare_nse_holiday(GAP)
    d = _nifty()
    out, recs = market.recover_index_gaps(d, "^NSEI", fetch=_fetch(calls=calls))
    assert calls == [] and recs == [] and len(out) == len(d)
    # weekends are never gaps either (the synthetic series spans many of them)
    assert all(day.weekday() < 5 for day in calls)


def test_complete_primary_series_never_calls_the_fallback():
    calls = []
    d = _nifty(drop=())
    out, recs = market.recover_index_gaps(d, "^NSEI", fetch=_fetch(calls=calls))
    assert calls == [] and recs == [] and out is d


def test_recap_session_itself_is_never_supplied_by_the_fallback():
    calls = []
    d = _nifty(end=GAP, drop=(GAP,))                     # primary's last bar is 21 Sep
    out, _ = market.recover_index_gaps(d, "^NSEI", fetch=_fetch(calls=calls))
    assert calls == [] and out.index[-1].date() == PREV


def test_both_sources_missing_blocks_publication_without_interpolating(monkeypatch):
    monkeypatch.setattr(market, "_nifty_history_with_backfill_check", lambda *a, **k: _nifty())
    monkeypatch.setattr(market, "history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    # conftest's default fallback: unavailable
    with pytest.raises(market.SessionAlignmentError,
                       match="REJECTED_SOURCE_UNAVAILABLE") as exc:
        market.get_market()
    assert "2026-09-22" in str(exc.value)
    out, recs = market.recover_index_gaps(_nifty(), "^NSEI")
    assert GAP not in set(out.index.date)                 # no interpolated / fabricated bar
    assert recs[0]["validation_status"] == "REJECTED_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("row,status", [
    (_archive_row(chg=-40.0), "REJECTED_CONTINUITY_MISMATCH"),     # anchored to a different close
    (_archive_row(day=NEXT), "REJECTED_DATE_MISMATCH"),            # the file's row is another day
    (_archive_row(lo=23400.0), "REJECTED_INVALID_OHLC"),           # low above the close
    (_archive_row(pct=-1.2), "REJECTED_CHANGE_INCONSISTENT"),      # stated % disagrees
])
def test_invalid_fallback_rows_are_rejected_and_the_run_still_stops(monkeypatch, row, status):
    out, recs = market.recover_index_gaps(_nifty(), "^NSEI", fetch=_fetch({GAP: [row]}))
    assert recs[0]["validation_status"] == status and GAP not in set(out.index.date)
    monkeypatch.setattr(market, "_nifty_history_with_backfill_check", lambda *a, **k: _nifty())
    monkeypatch.setattr(market, "history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(market, "nse_index_close_archive", _fetch({GAP: [row]}))
    with pytest.raises(market.SessionAlignmentError, match=status):
        market.get_market()


def test_unanchored_gap_is_rejected_not_guessed():
    """Two consecutive primary gaps whose first cannot be recovered: the second has no adjacent
    close to tie to, so it is rejected too."""
    d = _nifty(drop=(PREV, GAP))
    out, recs = market.recover_index_gaps(d, "^NSEI", fetch=_fetch())
    by = {r["session_date"]: r["validation_status"] for r in recs}
    assert by == {"2026-09-21": "REJECTED_SOURCE_UNAVAILABLE",
                  "2026-09-22": "REJECTED_NO_CONTINUITY_ANCHOR"}
    assert len(out) == len(d)


def test_sector_recovery_records_provenance(monkeypatch):
    bank = _nifty(closes={PREV: 56000.0, NEXT: 56548.9})
    rows = {GAP: [_archive_row(name="Nifty Bank", o=56100, h=56300, lo=56000.5, c=56216.0,
                               chg=216.0, pct=0.39)]}
    monkeypatch.setattr(market, "nse_index_close_archive", _fetch(rows))
    monkeypatch.setattr(market, "history", lambda t, p="1y": bank if t == "^NSEBANK"
                        else (_ for _ in ()).throw(RuntimeError("no data")))
    log = []
    sec = market.get_sectors(NEXT, GAP, {}, recovery_log=log)
    assert sec == [{"name": "Bank", "pct": pytest.approx((56548.9 / 56216.0 - 1) * 100),
                    "prev_close_source": "NSE_INDEX_CLOSE_ARCHIVE"}]
    assert [r["validation_status"] for r in log] == ["VALIDATED"]


def test_radar_benchmark_carries_recovered_row_but_cache_stays_yahoo_only(monkeypatch):
    from radar import relative_acquisition
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    monkeypatch.setattr(market, "history", lambda t, p="1y": _nifty())
    monkeypatch.setattr(market, "nse_index_close_archive", _fetch())
    series = relative_acquisition.build_market_benchmark_series()
    row = next(r for r in series if r["date"] == GAP)
    assert row["close"] == C22 and row["source"] == "NSE_INDEX_CLOSE_ARCHIVE"
    assert row["provenance"]["validation_status"] == "VALIDATED"
    assert all("source" not in r for r in series if r["date"] != GAP)
    assert relative_acquisition.last_benchmark_recovery()[0]["session_date"] == "2026-09-22"
    store = OHLCVStore(default_db_path(config.OUT_DIR))
    try:
        cached = {r.session_date for r in store.get_range(["^NSEI"], D(2026, 1, 1), NEXT)}
    finally:
        store.close()
    assert PREV in cached and GAP not in cached


def test_pipeline_issue_codes_make_fallback_use_visible():
    from radar.daily_pipeline import _classify_warning
    assert _classify_warning("benchmark fallback used: ^NSEI 2026-09-22 from X") == (
        "BENCHMARK_SESSION_RECOVERED_FROM_FALLBACK", "INFO")
    assert _classify_warning("benchmark fallback rejected: ^NSEI 2026-09-22")[0] == \
        "BENCHMARK_SESSION_FALLBACK_REJECTED"


def test_upload_blocks_a_missing_real_session_not_a_holiday(monkeypatch):
    """POST final edge-case patch: a real session that neither the primary nor NSE's official
    end-of-day file could establish BLOCKS publication (was: a silent holiday-style skip)."""
    import argparse
    import main
    m = market.analyze(_nifty(end=GAP))                          # benchmark ends 21 Sep
    m["session_dates"] = tuple(ts.date() for ts in _nifty(end=GAP).index)
    monkeypatch.setattr(market, "get_market", lambda: m)
    args = argparse.Namespace(demo=False, upload=True, force=False)
    with pytest.raises(market.SessionAlignmentError, match="BENCHMARK_MISSING_RECAP_SESSION"):
        main.collect(args, NEXT)


# --------------------------------------------------------------------------- cache repair
IST = config.IST


def _store():
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    return OHLCVStore(default_db_path(config.OUT_DIR))


def _bar(sym, day, close, retrieved, quality="OK", volume=1e6):
    from storage.ohlcv_models import OHLCVBar, QualityStatus
    return OHLCVBar(symbol=sym, session_date=day, open=close, high=None if close is None else close * 1.01,
                    low=None if close is None else close * 0.99, close=close, volume=volume,
                    source="yahoo", retrieved_at=retrieved, quality_status=QualityStatus(quality))


def _t(day, hh, mm):
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST).astimezone(dt.timezone.utc)


FINAL = {("AAA", D(2026, 9, 24)): (200.0, 201.0, 199.0, 200.5, 9e6),
         ("AAA", D(2026, 9, 25)): (300.0, 303.0, 297.0, 301.0, 2e7),
         ("BBB", D(2026, 9, 25)): (50.0, 51.0, 49.0, 50.2, 5e6),
         ("CCC", D(2026, 9, 23)): (10.0, 11.0, 9.0, 99.0, 1e6)}      # would differ if written


def _fake_fetch(final=FINAL, calls=None):
    def fetch(symbols, sessions, period):
        if calls is not None:
            calls.append((tuple(symbols), tuple(sorted(sessions)), period))
        return {(s, d): final.get((s, d)) for s in symbols for d in sessions}
    return fetch


def _seed():
    from radar import cache_repair  # noqa: F401
    st = _store()
    st.upsert_bars([
        _bar("AAA", D(2026, 9, 25), 298.0, _t(D(2026, 9, 25), 11, 44), volume=6e6),  # intraday
        _bar("BBB", D(2026, 9, 25), 49.0, _t(D(2026, 9, 25), 15, 20)),               # intraday
        _bar("AAA", D(2026, 9, 24), None, _t(D(2026, 9, 25), 11, 44), "BACKFILL_PENDING"),
        _bar("AAA", D(2026, 9, 23), 150.0, _t(D(2026, 9, 23), 16, 5)),              # final same day
        _bar("CCC", D(2026, 9, 23), 12.0, _t(D(2026, 9, 25), 11, 44)),              # final next day
        _bar("CCC", D(2026, 9, 14), 12.0, _t(D(2026, 9, 14), 11, 0)),               # holiday placeholder
    ])
    return st


def _bhav(day):
    return {"AAA": {"close": 301.0, "volume": 2e7}, "BBB": {"close": 50.2, "volume": 5e6}} \
        if day == D(2026, 9, 25) else {"AAA": {"close": 200.5, "volume": 9e6}}


def test_cache_repair_replaces_only_non_final_rows_and_verifies_them():
    from radar import cache_repair
    st = _seed()
    try:
        before = {(r.symbol, r.session_date): r for r in st.get_range(["AAA", "BBB", "CCC"],
                                                                      D(2026, 9, 1), D(2026, 9, 30))}
        calls = []
        res = cache_repair.repair_non_final_rows(
            st, now=dt.datetime(2026, 9, 25, 18, 0, tzinfo=IST), fetch=_fake_fetch(calls=calls),
            bhavcopy=_bhav)
        assert (res.targets, res.replaced, res.unresolved, res.skipped_non_session) == (3, 3, 0, 1)
        assert res.by_reason == {"INTRADAY_SNAPSHOT": 2, "BACKFILL_PENDING": 1}
        assert set(calls[0][0]) == {"AAA", "BBB"}                   # final rows never re-fetched
        after = {(r.symbol, r.session_date): r for r in st.get_range(["AAA", "BBB", "CCC"],
                                                                     D(2026, 9, 1), D(2026, 9, 30))}
        for key in (("AAA", D(2026, 9, 25)), ("BBB", D(2026, 9, 25)), ("AAA", D(2026, 9, 24))):
            o, h, lo, c, v = FINAL[key]
            r = after[key]
            assert (r.open, r.high, r.low, r.close, r.volume, r.quality_status.value) == \
                (o, h, lo, c, v, "OK")
        # final EOD bars and the holiday placeholder: byte-identical
        for key in (("AAA", D(2026, 9, 23)), ("CCC", D(2026, 9, 23)), ("CCC", D(2026, 9, 14))):
            assert after[key] == before[key]
        replaced = [r for r in res.rows if r["action"] == "REPLACED"]
        assert all(r["readback_ok"] for r in replaced)
        assert {r["verification"]["status"] for r in replaced} == {"MATCHES_NSE_CLOSE"}
        assert res.verification["readback_failures"] == 0
        # idempotent: a second pass finds nothing left to repair
        again = cache_repair.repair_non_final_rows(
            st, now=dt.datetime(2026, 9, 25, 18, 5, tzinfo=IST), fetch=_fake_fetch(), bhavcopy=_bhav)
        assert again.targets == 0
    finally:
        st.close()


def test_cache_repair_defers_a_session_still_trading():
    from radar import cache_repair
    st = _seed()
    try:
        res = cache_repair.repair_non_final_rows(
            st, now=dt.datetime(2026, 9, 25, 14, 0, tzinfo=IST), fetch=_fake_fetch(),
            bhavcopy=_bhav)
        assert res.deferred_in_progress == 2 and res.replaced == 1       # only the 24 Sep row
        assert st.get_bar("AAA", D(2026, 9, 25), "yahoo").close == 298.0
    finally:
        st.close()


def test_cache_repair_never_overwrites_a_row_that_became_final_after_the_scan():
    from radar import cache_repair
    st = _seed()
    try:
        def racing_fetch(symbols, sessions, period):
            # another run writes the real post-close bar between scan and write
            st.upsert_bars([_bar("AAA", D(2026, 9, 25), 301.5, _t(D(2026, 9, 25), 16, 10))])
            return _fake_fetch()(symbols, sessions, period)
        res = cache_repair.repair_non_final_rows(
            st, now=dt.datetime(2026, 9, 25, 18, 0, tzinfo=IST), fetch=racing_fetch, bhavcopy=_bhav)
        assert res.conflicts_left_alone == 1
        assert st.get_bar("AAA", D(2026, 9, 25), "yahoo").close == 301.5
    finally:
        st.close()


def test_cache_repair_leaves_row_when_no_final_bar_and_dry_run_writes_nothing():
    from radar import cache_repair
    st = _seed()
    try:
        res = cache_repair.repair_non_final_rows(
            st, now=dt.datetime(2026, 9, 25, 18, 0, tzinfo=IST), fetch=_fake_fetch(final={}),
            bhavcopy=_bhav)
        assert res.unresolved == 3 and res.replaced == 0
        assert st.get_bar("AAA", D(2026, 9, 25), "yahoo").close == 298.0
        dry = cache_repair.repair_non_final_rows(
            st, now=dt.datetime(2026, 9, 25, 18, 0, tzinfo=IST), fetch=_fake_fetch(),
            bhavcopy=_bhav, dry_run=True)
        assert dry.replaced == 0 and {r["action"] for r in dry.rows} >= {"WOULD_REPLACE"}
        assert st.get_bar("AAA", D(2026, 9, 25), "yahoo").close == 298.0
    finally:
        st.close()


def test_classify_uses_the_single_session_final_time():
    from radar import cache_repair
    from storage.ohlcv_repository import OHLCVRow
    from storage.ohlcv_models import QualityStatus

    def row(t):
        return OHLCVRow("X", D(2026, 9, 25), "yahoo", 1, 1, 1, 1, 1, t, QualityStatus.OK)
    assert cache_repair.classify(row(_t(D(2026, 9, 25), 15, 39))) == "INTRADAY_SNAPSHOT"
    assert cache_repair.classify(row(_t(D(2026, 9, 25), 15, 40))) is None
    assert market.SESSION_FINAL_TIME == dt.time(15, 40)


# --------------------------------------------------------------------------- Radar history rebuild
def test_spine_repair_range_is_minimal():
    from radar.candidate_history_backfill import spine_repair_range
    new = _canonical(D(2026, 9, 25), 15)
    old = [d for d in new if d != GAP]
    assert spine_repair_range(old, new, D(2026, 9, 24)) == [GAP, NEXT, D(2026, 9, 24)]
    assert spine_repair_range(new, new, D(2026, 9, 24)) == []
    assert spine_repair_range(old, new, D(2026, 9, 21)) == []   # difference is after the target


def test_invalidate_sessions_removes_only_the_named_sessions_and_returns_them():
    from storage.candidate_history_models import RunStatus, StoredCandidateState
    from storage.candidate_history_repository import CandidateHistoryStore, default_db_path

    def state(day, sym):
        return StoredCandidateState(session_date=day, instrument=sym, active_families=("VOLUME",),
                                    reason_codes=("X",), independent_signal_count=1,
                                    direction_compatibility=None, attention_level=None,
                                    persistence_state=None, price_change_pct=1.0,
                                    calculation_version="1.0")
    st = CandidateHistoryStore(default_db_path(config.OUT_DIR))
    try:
        for day in (PREV, NEXT):
            st.save_candidates(day, [state(day, "AAA"), state(day, "BBB")])
            st.mark_run(day, "1.0", RunStatus.COMPLETE, 2)
        removed = st.invalidate_sessions([NEXT, GAP])
        assert len(removed["candidates"]) == 2 and removed["runs"][0]["session_date"] == "2026-09-23"
        assert not st.is_session_complete(NEXT, "1.0") and st.get_session_candidates(NEXT) == []
        assert st.is_session_complete(PREV, "1.0") and len(st.get_session_candidates(PREV)) == 2
        # the session can now be recomputed (save is insert-or-ignore, so it had to be removed)
        assert st.save_candidates(NEXT, [state(NEXT, "CCC")]) == 1
    finally:
        st.close()
