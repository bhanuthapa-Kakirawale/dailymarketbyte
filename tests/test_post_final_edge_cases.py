"""POST final edge-case patch. Fully offline.

1. Radar stories with no validated technical event (verified 24 Sep 2026: COROMANDEL, UNUSUAL
   volume 3.0x + relative performance, no STRUCTURE event, -0.69% on the day) rendered an empty
   chart card reading "Something changed on the chart with unusually high volume." They now get
   the VOLUME (or SESSION) presentation: neutral candles + evidence, no claimed chart event.
2. Direction wording/colour on a Radar scene follows the session's validated move, never the
   composite's internal `direction` ("ALIGNED_POSITIVE" on a stock that fell).
3. Recap-session benchmark fallback: every Yahoo Indian index feed lacks Tue 2026-09-22, so the
   23 Sep 07:40 run saw 21 Sep as the latest session. NSE's official end-of-day file now supplies
   the recap session itself - only once it is final, validated, with provenance - and a real
   session neither source can establish blocks publication.
"""
import argparse
import datetime as dt
import re

import pandas as pd
import pytest

import config
import market
from core.trading_calendar import SessionCalendar
from daily_video import Composer, theme
from daily_video.radar_story_scene import VOL_STRIP, _dir_color
from daily_video.storyboard import Storyboard, _radar_story
from presentation.radar_story import build_radar_story_model, move_clause
from radar.visual_evidence import RadarVisualEvidence

D = dt.date
IST = config.IST
PREV, GAP, NEXT, AFTER = D(2026, 9, 21), D(2026, 9, 22), D(2026, 9, 23), D(2026, 9, 24)
C21, C22, C23 = 23414.3, 23329.0, 23446.8

# Anything that would claim a chart event, or leak an internal direction label, onto the screen.
EVENT_WORDING = re.compile(r"on the chart|broke|breakout|broke out|crossed|moved (above|below)|"
                           r"fell (below|out)|tightened|recent range|alignment|positive-direction|"
                           r"negative-direction|quiet signal", re.I)


# --------------------------------------------------------------------------- radar fixtures
def _ev(n=38, last_vol=300.0, base_vol=100.0, chg=-0.69):
    d0 = dt.date(2026, 8, 3)
    dates = [d0 + dt.timedelta(days=i) for i in range(n)]
    closes = [1950 + (i % 7) for i in range(n)]
    closes[-1] = round(closes[-2] * (1 + chg / 100), 2)
    o = [closes[0]] + closes[:-1]
    h = [max(a, b) + 4 for a, b in zip(o, closes)]
    lo = [min(a, b) - 4 for a, b in zip(o, closes)]
    return RadarVisualEvidence(
        instrument="STOCK-V", session_date=dates[-1], calculation_version="t", window_dates=dates,
        close_series=closes, volume_series=[base_vol] * (n - 1) + [last_vol],
        sma20_series=[None] * n, sma50_series=[None] * n,
        range_20_high=max(closes[-21:-1]), range_20_low=min(closes[-21:-1]),
        range_50_high=max(closes[:-1]), range_50_low=min(closes[:-1]), highlight_events=[],
        rvol=last_vol / base_vol, rvol_level="UNUSUAL", stock_return_5d_pct=None,
        stock_return_20d_pct=None, market_relative_5d_pp=1.5, market_relative_20d_pp=4.4,
        relative_window_sessions=20, relative_dates=None, relative_stock_normalized=[1.0],
        relative_benchmark_normalized=[1.0], benchmark_symbol="^NSEI", source_session_dates=dates,
        open_series=o, high_series=h, low_series=lo)


def _story(chg=-0.69, level="UNUSUAL", rvol=3.0, events=None, direction="ALIGNED_POSITIVE"):
    """COROMANDEL's real shape: VOLUME + RELATIVE_PERFORMANCE, no technical context, and the
    composite's direction ALIGNED_POSITIVE (5d/20d outperformance) on a down day."""
    sp = {"instrument": "STOCK-V", "company_name": "Placeholder Ltd.",
          "price_change_display": f"{chg:+.1f}%",
          "context_line": "Quiet Signal; Positive Alignment; 5D vs Nifty +1.5 pp",
          "direction_label": "Positive Alignment",
          "selection_reason": "Selected to provide an available positive-direction development."}
    st = {"instrument": "STOCK-V", "price_change_pct": chg, "direction": direction,
          "technical_context": {"events": events} if events else None,
          "volume_context": {"level": level, "relative_volume": rvol} if level else None,
          "relative_context": {"persistence_state": "PERSISTENT_POSITIVE"},
          "editorial_selection_reason": sp["selection_reason"]}
    return sp, st


def _scene(sp, st, ev):
    spec = _radar_story(2, 3, sp, st, ev)
    sb = Storyboard(session_date=D(2026, 9, 24), date_label="THU 24 SEP 2026",
                    kicker="SESSION RECAP", scenes=[spec])
    return spec, Composer(sb)


# --------------------------------------------------------------------------- 1. volume-only
def test_volume_only_story_is_a_volume_story():
    m = build_radar_story_model(*_story(), _ev(), 2, 3)
    assert m.event_family == "VOLUME" and m.event_type is None
    assert m.takeaway == "Trading volume was unusually high while price finished 0.7% lower."
    assert m.support["kind"] == "VOLUME" and m.support["label"] == "3.0× normal volume"
    assert m.support["average"] == pytest.approx(100.0)          # 300 / 3.0 = prior-20 mean
    # no fake event: no callout, no reference level, no overlay; one price (the close)
    assert (m.event_callout, m.reference_label, m.reference_display) == ("", "", "")
    assert m.band is None and m.ma_series is None
    assert m.price_values == [m.latest_close_display]
    assert m.candles is not None and len(m.support["volume"]) == len(m.candles["close"])


def test_volume_levels_are_worded_as_the_detector_level_never_upgraded():
    for level, words in (("ELEVATED", "above normal"), ("UNUSUAL", "unusually high"),
                         ("EXTREME", "exceptionally high")):
        m = build_radar_story_model(*_story(level=level), _ev(), 1, 3)
        assert m.takeaway.startswith(f"Trading volume was {words} while")


def test_average_line_only_when_the_window_reproduces_the_detector_multiple():
    # the detector said 3.0x but the local window implies 4.0x -> no line, a warning instead
    m = build_radar_story_model(*_story(rvol=3.0), _ev(last_vol=400.0), 1, 3)
    assert m.support["average"] is None and m.support["average_label"] == ""
    assert any("average line omitted" in w for w in m.warnings)
    assert "support_average" not in m.strings()


def test_no_event_story_never_draws_an_empty_panel():
    spec, comp = _scene(*_story(), _ev())
    img, _, qa = comp.freeze(0)
    assert qa["passed"], qa["issues"]
    marks = set(qa["marks"])
    assert {"candles", "volume_bars", "volume_spike", "volume_average", "price_tag"} <= marks
    assert "text_card" not in marks
    # nothing claims an event on the chart
    assert not marks & {"event_candle", "callout", "range_band", "ma_line"}
    # the volume strip actually carries pixels (not an empty panel)
    strip = img.convert("L").crop(VOL_STRIP)
    assert strip.getextrema()[1] - strip.getextrema()[0] > 60


def test_session_story_without_event_or_volume_uses_the_day_range():
    sp, st = _story(level=None, rvol=None)
    m = build_radar_story_model(sp, st, _ev(), 1, 3)
    assert m.event_family == "SESSION" and m.support["kind"] == "DAY_RANGE"
    assert m.takeaway.startswith("Price finished 0.7% lower")
    spec, comp = _scene(sp, st, _ev())
    _, _, qa = comp.freeze(0)
    assert qa["passed"], qa["issues"]
    assert {"candles", "day_range", "price_tag"} <= set(qa["marks"])
    assert not set(qa["marks"]) & {"event_candle", "callout", "range_band", "ma_line", "text_card"}


def test_no_unsupported_technical_wording_on_any_no_event_story():
    evs = {"VOLUME": _ev(), "SESSION": _ev(), "TEXT": None}
    for fam, ev in evs.items():
        kw = {} if fam == "VOLUME" else {"level": None, "rvol": None}
        if fam == "TEXT":
            kw = {}                                   # volume flagged, but no chart evidence
        spec = _radar_story(1, 3, *_story(**kw), ev)
        text = " | ".join(spec.public_text("s").values())
        assert not EVENT_WORDING.search(text), (fam, text)
        assert "Something changed" not in text
    # the no-chart TEXT card never reuses the planner's context_line
    m = build_radar_story_model(*_story(), None, 1, 3)
    assert m.event_family == "TEXT"
    assert m.takeaway == "Trading volume was unusually high while price finished 0.7% lower."


def test_event_stories_keep_their_event_path():
    """A real event is still drawn as an event (normal Phase 2 scene, unchanged)."""
    m = build_radar_story_model(*_story(events=["BREAK_ABOVE_20D_RANGE"], chg=2.1), _ev(chg=2.1),
                                1, 3)
    assert m.event_family == "RANGE_UP"
    assert m.takeaway == "Price broke above its recent range with unusually high volume."
    assert m.event_callout == "Broke out here"


# --------------------------------------------------------------------------- 2. direction
@pytest.mark.parametrize("chg,direction,word,positive", [
    (-0.69, "ALIGNED_POSITIVE", "lower", False),      # COROMANDEL: outperformer, down day
    (1.24, "ALIGNED_NEGATIVE", "higher", True),
    (-2.5, "MIXED", "lower", False),
])
def test_direction_follows_the_session_move_not_the_internal_label(chg, direction, word, positive):
    m = build_radar_story_model(*_story(chg=chg, direction=direction), _ev(chg=chg), 1, 3)
    assert m.change_positive is positive
    assert f"% {word}." in m.takeaway
    assert m.display_change.startswith("+" if positive else "-")
    assert _dir_color(m.to_dict()) == (theme.POSITIVE if positive else theme.NEGATIVE)
    spec = _radar_story(1, 3, *_story(chg=chg, direction=direction), _ev(chg=chg))
    assert not re.search(r"alignment|direction", " ".join(spec.public_text("s").values()), re.I)


def test_move_clause_matches_the_badge_rounding():
    assert move_clause(-0.69) == "finished 0.7% lower"
    assert move_clause(0.04) == move_clause(-0.04) == "finished virtually unchanged"
    assert move_clause(0.25) == f"finished {abs(0.25):.1f}% higher"
    assert move_clause(None) == ""


# --------------------------------------------------------------------------- 3. benchmark
def _canonical(end, n):
    return SessionCalendar().sessions_between(end - dt.timedelta(days=n * 2 + 30), end)[-n:]


def _series(end, n=120, drop=(GAP,), closes=None):
    dates = [d for d in _canonical(end, n) if d not in drop]
    close = {d: 23000.0 + i for i, d in enumerate(dates)}
    close.update({PREV: C21, NEXT: C23})
    close.update(closes or {})
    c = [close[d] for d in dates]
    return pd.DataFrame({"Open": c, "High": [x * 1.004 for x in c], "Low": [x * 0.996 for x in c],
                         "Close": c, "Volume": [2e5] * len(c)}, index=pd.DatetimeIndex(dates))


def _row(name="Nifty 50", day=GAP, o=23454.05, h=23489.0, lo=23285.75, c=C22, chg=-85.3,
         pct=-0.36):
    return {"Index Name": name, "Index Date": day.strftime("%d-%m-%Y"), "Open Index Value": o,
            "High Index Value": h, "Low Index Value": lo, "Closing Index Value": c,
            "Points Change": chg, "Change(%)": pct}


def _fetch(rows=None, calls=None):
    rows = rows if rows is not None else {GAP: [_row()]}

    def fetch(day):
        if calls is not None:
            calls.append(day)
        if day not in rows:
            return {"error": f"404 for {day}"}
        return {"rows": {r["Index Name"].upper(): r for r in rows[day]},
                "url": f"https://nsearchives.nseindia.com/content/indices/ind_close_all_"
                       f"{day.strftime('%d%m%Y')}.csv",
                "retrieved_at": "2026-09-23T02:05:00+00:00",
                "published_at": "Tue, 22 Sep 2026 12:40:00 GMT"}
    return fetch


def _at(day, hh, mm):
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


MORNING_AFTER = _at(NEXT, 7, 40)            # the scheduled 23 Sep run


def test_recap_session_recovered_after_the_session_is_final():
    d = _series(PREV)                                         # Yahoo ends 21 Sep
    out, recs = market.recover_recap_session(d, "^NSEI", now=MORNING_AFTER, fetch=_fetch())
    assert out.index[-1].date() == GAP and len(out) == len(d) + 1
    row = out.iloc[-1]
    assert (row.Open, row.High, row.Low, row.Close) == (23454.05, 23489.0, 23285.75, C22)
    assert pd.isna(row.Volume)
    (rec,) = recs
    assert rec["validation_status"] == "VALIDATED" and rec["role"] == "RECAP_SESSION"
    assert rec["source"] == "NSE_INDEX_CLOSE_ARCHIVE" and rec["session_date"] == "2026-09-22"
    assert rec["fallback_reason"].startswith("PRIMARY_MISSING_RECAP_SESSION")
    assert rec["session_final_check"]["final"] is True
    assert rec["anchor_session"] == "2026-09-21" and rec["anchor_close"] == C21
    assert rec["checks"]["implied_previous_close"] == pytest.approx(C21)
    for key in ("source_url", "retrieval_time", "source_published_at"):
        assert rec[key]


@pytest.mark.parametrize("now", [_at(GAP, 10, 0), _at(GAP, 15, 39)])
def test_recap_fallback_is_forbidden_intraday(now):
    calls = []
    d = _series(PREV)
    out, recs = market.recover_recap_session(d, "^NSEI", now=now, fetch=_fetch(calls=calls))
    assert calls == []                                        # never even fetched
    assert out.index[-1].date() == PREV and len(out) == len(d)
    assert recs == []             # not missing - its end-of-day value does not exist yet


def test_recap_fallback_allowed_once_final_on_the_same_day():
    out, recs = market.recover_recap_session(_series(PREV), "^NSEI", now=_at(GAP, 15, 41),
                                             fetch=_fetch())
    assert out.index[-1].date() == GAP and recs[0]["validation_status"] == "VALIDATED"


def test_recap_fallback_rejects_an_unanchored_or_inconsistent_row():
    bad = {GAP: [_row(chg=-10.0)]}                            # implied prev close != 21 Sep close
    out, recs = market.recover_recap_session(_series(PREV), "^NSEI", now=MORNING_AFTER,
                                             fetch=_fetch(bad))
    assert out.index[-1].date() == PREV
    assert recs[0]["validation_status"] == "REJECTED_CONTINUITY_MISMATCH"
    wrong_day = {GAP: [_row(day=PREV)]}
    _, recs = market.recover_recap_session(_series(PREV), "^NSEI", now=MORNING_AFTER,
                                           fetch=_fetch(wrong_day))
    assert recs[0]["validation_status"] == "REJECTED_DATE_MISMATCH"


def test_recap_fallback_never_fills_a_stale_primary():
    calls = []
    d = _series(D(2026, 9, 17))                               # 18, 21, 22 missing: broken feed
    out, recs = market.recover_recap_session(d, "^NSEI", now=MORNING_AFTER,
                                             fetch=_fetch(calls=calls))
    assert calls == [] and len(out) == len(d)
    assert {r["validation_status"] for r in recs} == {"NOT_ATTEMPTED_PRIMARY_STALE"}


def test_recap_fallback_never_reaches_past_until():
    calls = []
    out, recs = market.recover_recap_session(_series(PREV), "^NSEBANK", now=_at(AFTER, 7, 40),
                                             fetch=_fetch(calls=calls), until=PREV)
    assert calls == [] and recs == [] and out.index[-1].date() == PREV


def _market_env(monkeypatch, nifty, now, fetch):
    monkeypatch.setattr(market, "_nifty_history_with_backfill_check", lambda *a, **k: nifty)
    monkeypatch.setattr(market, "history",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no yahoo")))
    monkeypatch.setattr(market, "nse_index_close_archive", fetch)
    monkeypatch.setattr(market, "now_ist", lambda: now)


def test_get_market_22_sep_uses_the_official_eod_close_with_provenance(monkeypatch):
    _market_env(monkeypatch, _series(PREV), MORNING_AFTER, _fetch())
    m = market.get_market()
    assert (m["recap_date"], m["prev_date"]) == (GAP, PREV)
    assert m["close"] == C22 and m["prev"] == C21
    assert round(m["pct"], 2) == -0.36 and m["chg"] == pytest.approx(-85.3)
    a = m["session_alignment"]
    assert a["status"] == "ALIGNED_WITH_FALLBACK"
    assert a["recap_close_source"] == m["close_source"] == "NSE_INDEX_CLOSE_ARCHIVE"
    assert a["previous_close_source"] == m["prev_source"] == "yahoo"
    assert a["benchmark_recovered_sessions"] == ["2026-09-22"]
    assert a["benchmark_recovery"][0]["role"] == "RECAP_SESSION"
    assert m["vix"] is None                                   # no dated VIX bar -> none shown


def test_fallback_provenance_reaches_the_canonical_observations():
    from adapters.market_adapter import MarketAdapter
    from core import Metric, SourceType
    from core.sources import independence_group_for
    m = {"close": C22, "pct": -0.364, "prev": C21, "chg": -85.3, "prev_date": PREV,
         "close_source": "NSE_INDEX_CLOSE_ARCHIVE", "prev_source": "yahoo"}
    obs = MarketAdapter(GAP, dt.datetime(2026, 9, 23, 7, 45, tzinfo=IST)).observe_nifty(m)
    nifty = [o for o in obs if o.instrument == "NIFTY 50"]
    assert {o.metric for o in nifty} == {Metric.INDEX_CLOSE, Metric.INDEX_CHANGE_PCT}
    for o in nifty:
        assert o.source_name == "nse_index_close_archive" and o.source_type is SourceType.PRIMARY
        assert o.metadata["close_source"] == "NSE_INDEX_CLOSE_ARCHIVE"
    # one exchange, one witness: the archive never corroborates NSE's own API
    assert independence_group_for("nse_index_close_archive") == independence_group_for("nse_website")
    # the Yahoo path is untouched
    y = MarketAdapter(PREV, dt.datetime(2026, 9, 22, 7, 45, tzinfo=IST)).observe_nifty(
        {"close": C21, "pct": 0.1, "prev": 23390.0, "chg": 24.3, "prev_source": "yahoo"})
    assert {o.source_name for o in y} == {"yahoo_finance"}


def test_yahoo_and_nse_both_failing_blocks_publication(monkeypatch):
    import main
    _market_env(monkeypatch, _series(PREV), MORNING_AFTER, _fetch(rows={}))
    m = market.get_market()
    assert m["recap_date"] == PREV                            # nothing invented, nothing inferred
    rec = [r for r in m["session_alignment"]["benchmark_recovery"] if r["session_date"] == "2026-09-22"]
    assert rec[0]["validation_status"] == "REJECTED_SOURCE_UNAVAILABLE"
    monkeypatch.setattr(market, "get_market", lambda: m)
    args = argparse.Namespace(demo=False, upload=True, force=False)
    with pytest.raises(market.SessionAlignmentError,
                       match=r"BENCHMARK_MISSING_RECAP_SESSION.*REJECTED_SOURCE_UNAVAILABLE"):
        main.collect(args, NEXT)


def test_a_real_holiday_is_still_a_quiet_skip(monkeypatch):
    """Gandhi Jayanti 2026-10-02 (Fri) is on NSE's list: the Monday run skips, never blocks."""
    import main
    oct1 = D(2026, 10, 1)
    _market_env(monkeypatch, _series(oct1, drop=()), _at(D(2026, 10, 5), 7, 40), _fetch(rows={}))
    m = market.get_market()
    assert m["recap_date"] == oct1
    monkeypatch.setattr(market, "get_market", lambda: m)
    args = argparse.Namespace(demo=False, upload=True, force=False)
    assert main.collect(args, D(2026, 10, 5)) is None


# --------------------------------------------------------------------------- 4. 22-24 Sep regression
def test_22_sep_regression_then_23_and_24_unchanged(monkeypatch):
    # 22 Sep POST (run 23 Sep 07:40): Yahoo ends 21 Sep -> recap 22 Sep from NSE's EOD file
    _market_env(monkeypatch, _series(PREV), MORNING_AFTER, _fetch())
    m22 = market.get_market()
    assert (m22["recap_date"], m22["close"], m22["close_source"]) == (GAP, C22,
                                                                      "NSE_INDEX_CLOSE_ARCHIVE")
    # 23 Sep POST (run 24 Sep 07:40): Yahoo has 23 but not 22 -> internal gap recovery, as before
    _market_env(monkeypatch, _series(NEXT), _at(AFTER, 7, 40), _fetch())
    m23 = market.get_market()
    assert (m23["recap_date"], m23["prev_date"]) == (NEXT, GAP)
    assert m23["prev"] == C22 and round(m23["pct"], 3) == 0.505
    assert m23["close_source"] == "yahoo" and m23["prev_source"] == "NSE_INDEX_CLOSE_ARCHIVE"
    assert all(r.get("role") != "RECAP_SESSION"
               for r in m23["session_alignment"]["benchmark_recovery"])
    # 24 Sep POST (run 25 Sep 07:40): Yahoo's own bars for both 23 and 24 - no fallback in use
    _market_env(monkeypatch, _series(AFTER, closes={AFTER: 23500.0}), _at(D(2026, 9, 25), 7, 40),
                _fetch())
    m24 = market.get_market()
    assert (m24["recap_date"], m24["prev_date"]) == (AFTER, NEXT)
    assert m24["close_source"] == m24["prev_source"] == "yahoo"
    assert m24["session_alignment"]["status"] == "ALIGNED"


def test_sector_recap_close_from_the_official_file_says_so(monkeypatch):
    sector = market.SECTORS[1]                                # ("IT", "NIFTY IT", "^CNXIT")
    it = _series(PREV, closes={PREV: 30000.0})
    monkeypatch.setattr(market, "history", lambda t, *a, **k: it if t == sector[2] else
                        (_ for _ in ()).throw(RuntimeError("no data")))
    monkeypatch.setattr(market, "now_ist", lambda: MORNING_AFTER)
    monkeypatch.setattr(market, "nse_index_close_archive", _fetch(
        {GAP: [_row(name="Nifty IT", o=30010, h=30100, lo=29850, c=29940.0, chg=-60.0,
                    pct=-0.2)]}))
    rows = market.get_sectors(GAP, PREV, {}, session_dates=tuple(_series(PREV).index.date) + (GAP,))
    assert rows == [{"name": "IT", "pct": pytest.approx(-0.2), "close_source": "NSE_INDEX_CLOSE_ARCHIVE"}]
