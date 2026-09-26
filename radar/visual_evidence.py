"""Visual Evidence Contract for the Radar video renderer (Phase 4.2 Packet 6.2V).

Packets 6.2/6.2R gave each selected Radar story a text-and-badge card (a visual-type badge, an
evidence-family dot indicator, 2-3 short evidence lines). This module is the layer that turns a
story's already-validated facts into an actual chart: real price/volume/range/SMA/relative-
performance series, read from local canonical OHLCV data, so the renderer can draw evidence
instead of a label.

## Hard boundary

`radar/video_renderer.py`/`radar/video_scenes.py` never import this module's dependencies
(`storage.ohlcv_repository`, `radar.technical`, `radar.relative`) directly - they only receive
the finished `RadarVisualEvidence` this module builds. All acquisition (reading
`market_ohlcv.db`) and all calculation (SMA, range highs/lows, returns, normalization) happens
here, upstream of rendering, exactly mirroring how `radar/presentation_planner.py` is the only
place that turns a `RadarStory` into viewer text.

## No network calls

`build_visual_evidence` reads ONLY `storage.ohlcv_repository.OHLCVStore` - it never calls
`market.history`, `yfinance`, or `radar.relative_acquisition.build_market_benchmark_series`.
The market benchmark (`^NSEI`) is available locally because
`radar.relative_acquisition.build_market_benchmark_series` now write-throughs its own,
already-fetched benchmark OHLCV into the same store as a side effect of the EXISTING Radar
acquisition step (see that module's docstring) - this module is purely a reader of that cache,
proven by a structural test (`tests/test_radar_visual_evidence.py`) asserting zero calls to
`market.history` during a build.

## No lookahead

Every store read is bounded `end_date=session_date` at the SQL level
(`OHLCVStore.get_range`) - the same enforcement `radar/ohlcv_service.py` already relies on.
A row for a date after `session_date` cannot reach this module's calculations even if present
in the store (e.g. from a later Radar run's own acquisition).

## Canonical session alignment

The benchmark's own stored dates ARE the canonical NSE trading-session spine (see
`radar/session_alignment.py`'s module docstring for why an index's own history is never subject
to the per-stock holiday-placeholder artifact) - built fresh from real persisted data here via
`radar.session_alignment.canonical_session_spine`, then used to filter the stock's own stored
rows via `radar.session_alignment.align_series_to_spine`, reusing both functions verbatim.

## Reused detector math, never re-implemented

Range highs/lows and moving averages reuse `radar.technical`'s own pure `_window_high_low`/
`_sma` functions and windowing convention; stock returns reuse `radar.relative`'s own pure
`_return_pct`. Nothing here is a second implementation of Radar's math with a chance to drift
from the live detectors' own numbers. `highlight_events`, `rvol`/`rvol_level`, and
`market_relative_5d_pp`/`market_relative_20d_pp` are never recomputed at all - they are copied
verbatim from the story's own already-validated `technical_context`/`volume_context`/
`relative_context` (the exact same dicts `radar/presentation_planner.py` already builds viewer
text from), so a rendered chart can never silently disagree with what Radar itself detected.

## Never fabricate

If local benchmark history is unavailable at all, or the stock's own local history doesn't
reach `session_date` with enough sessions, `build_visual_evidence` returns `None` - never a
partial or guessed chart. The caller (`radar/video_renderer.py`) falls back to the existing
text-card scene for that story; the rest of the video is unaffected.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import config
import market
from storage.ohlcv_repository import OHLCVStore, default_db_path

from .relative import _return_pct
from .relative_acquisition import BENCHMARK_SYMBOL
from .session_alignment import align_series_to_spine, canonical_session_spine
from .technical import _sma, _window_high_low
from .thresholds import DEFAULT_TECHNICAL_THRESHOLDS

RADAR_VISUAL_EVIDENCE_VERSION = "1.0"
CHART_WINDOW_SESSIONS = 38          # visible chart window
INDICATOR_LOOKBACK_SESSIONS = 55    # enough for SMA50/range50 at the window's first point
RELATIVE_WINDOW_SESSIONS = 20       # matches radar.relative.WINDOWS's own 20D window
STORE_QUERY_WINDOW_DAYS = 400       # matches radar/ohlcv_service.py's own store-query convention

BENCHMARK_VISUAL_EVIDENCE_UNAVAILABLE = "BENCHMARK_VISUAL_EVIDENCE_UNAVAILABLE"


@dataclass
class RadarVisualEvidence:
    """Everything the renderer needs to draw one story's chart - already validated, already
    calculated. The renderer draws pixels from these fields; it computes nothing."""
    instrument: str
    session_date: dt.date
    calculation_version: str
    window_dates: list
    close_series: list
    volume_series: list
    sma20_series: list
    sma50_series: list
    range_20_high: float | None
    range_20_low: float | None
    range_50_high: float | None
    range_50_low: float | None
    highlight_events: list
    rvol: float | None
    rvol_level: str | None
    stock_return_5d_pct: float | None
    stock_return_20d_pct: float | None
    market_relative_5d_pp: float | None
    market_relative_20d_pp: float | None
    relative_window_sessions: int
    relative_dates: list | None
    relative_stock_normalized: list | None
    relative_benchmark_normalized: list | None
    benchmark_symbol: str
    source_session_dates: list
    warnings: list = field(default_factory=list)
    # Candle bodies/wicks for the visible window (Phase 2 candlestick chart). Same aligned,
    # `end_date=session_date`-bounded rows as `close_series` - index i is the same session in
    # every series. None when built from an older artifact that did not carry them.
    open_series: list | None = None
    high_series: list | None = None
    low_series: list | None = None

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "session_date": self.session_date.isoformat(),
            "calculation_version": self.calculation_version,
            "window_dates": [d.isoformat() for d in self.window_dates],
            "close_series": list(self.close_series), "volume_series": list(self.volume_series),
            "sma20_series": list(self.sma20_series), "sma50_series": list(self.sma50_series),
            "range_20_high": self.range_20_high, "range_20_low": self.range_20_low,
            "range_50_high": self.range_50_high, "range_50_low": self.range_50_low,
            "highlight_events": list(self.highlight_events),
            "rvol": self.rvol, "rvol_level": self.rvol_level,
            "stock_return_5d_pct": self.stock_return_5d_pct,
            "stock_return_20d_pct": self.stock_return_20d_pct,
            "market_relative_5d_pp": self.market_relative_5d_pp,
            "market_relative_20d_pp": self.market_relative_20d_pp,
            "relative_window_sessions": self.relative_window_sessions,
            "relative_dates": ([d.isoformat() for d in self.relative_dates]
                               if self.relative_dates else None),
            "relative_stock_normalized": self.relative_stock_normalized,
            "relative_benchmark_normalized": self.relative_benchmark_normalized,
            "benchmark_symbol": self.benchmark_symbol,
            "source_session_dates": [d.isoformat() for d in self.source_session_dates],
            "warnings": list(self.warnings),
            "open_series": list(self.open_series) if self.open_series is not None else None,
            "high_series": list(self.high_series) if self.high_series is not None else None,
            "low_series": list(self.low_series) if self.low_series is not None else None,
        }


def build_visual_evidence(instrument: str, session_date: dt.date, story_fields: dict, *,
                          out_dir: str | None = None,
                          store: "OHLCVStore | None" = None) -> "RadarVisualEvidence | None":
    """Build one instrument's chart evidence for `session_date`, or `None` if it cannot be
    safely built (never a partial guess). `story_fields` is the ALREADY-VALIDATED
    `technical_context`/`volume_context`/`relative_context` dicts from that story's own
    `RadarStory` (`radar/daily_pipeline.py`) - the same shape `radar/presentation_planner.py`
    already reads to build viewer text.

    `store` lets a caller building evidence for multiple stories reuse one `OHLCVStore`
    connection instead of opening/closing one per instrument; when omitted, this function opens
    and closes its own.
    """
    out_dir = out_dir or config.OUT_DIR
    owns_store = store is None
    if store is None:
        try:
            store = OHLCVStore(default_db_path(out_dir))
        except Exception as exc:
            print(f"[radar.visual_evidence] OHLCV store unavailable for {instrument}: {exc}")
            return None
    try:
        return _build(instrument, session_date, story_fields or {}, store)
    finally:
        if owns_store:
            store.close()


def _build(instrument: str, session_date: dt.date, story_fields: dict,
          store: OHLCVStore) -> "RadarVisualEvidence | None":
    lookback_start = session_date - dt.timedelta(days=STORE_QUERY_WINDOW_DAYS)

    benchmark_rows = sorted(
        (r for r in store.get_range([BENCHMARK_SYMBOL], lookback_start, session_date,
                                    source="yahoo") if r.quality_status.value == "OK"),
        key=lambda r: r.session_date)
    if not benchmark_rows:
        print(f"[radar.visual_evidence] {BENCHMARK_VISUAL_EVIDENCE_UNAVAILABLE}: no local "
             f"benchmark history for {instrument} {session_date}")
        return None
    benchmark_series = [{"date": r.session_date, "close": r.close} for r in benchmark_rows]
    spine = canonical_session_spine(benchmark_series)

    stock_rows = sorted(
        (r for r in store.get_range([instrument], lookback_start, session_date, source="yahoo")
         if r.quality_status.value == "OK"), key=lambda r: r.session_date)
    stock_dicts = [{"date": r.session_date, "open": r.open, "high": r.high, "low": r.low,
                    "close": r.close, "volume": r.volume} for r in stock_rows]
    aligned, _dropped = align_series_to_spine(stock_dicts, spine, date_key="date")

    if not aligned or aligned[-1]["date"] != session_date:
        print(f"[radar.visual_evidence] no recap-session row locally for {instrument} "
             f"{session_date}")
        return None
    if len(aligned) < market.MIN_TECHNICAL_SESSIONS:
        print(f"[radar.visual_evidence] insufficient local history for {instrument} "
             f"{session_date} ({len(aligned)} sessions)")
        return None

    series = aligned
    warnings: list = []

    prior = series[:-1]
    prior_20 = (prior[-DEFAULT_TECHNICAL_THRESHOLDS.range_window_short:]
               if len(prior) >= DEFAULT_TECHNICAL_THRESHOLDS.range_window_short else None)
    prior_50 = (prior[-DEFAULT_TECHNICAL_THRESHOLDS.range_window_long:]
               if len(prior) >= DEFAULT_TECHNICAL_THRESHOLDS.range_window_long else None)
    range_20_high, range_20_low = _window_high_low(prior_20)
    range_50_high, range_50_low = _window_high_low(prior_50)

    stock_return_5d_pct, _, _ = _return_pct(series, 5)
    stock_return_20d_pct, _, _ = _return_pct(series, 20)

    display = series[-CHART_WINDOW_SESSIONS:]
    display_start_idx = len(series) - len(display)
    window_dates = [p["date"] for p in display]
    close_series = [p["close"] for p in display]
    volume_series = [p["volume"] for p in display]
    ohlc_complete = all(p.get(k) is not None for p in display for k in ("open", "high", "low"))
    if not ohlc_complete:
        warnings.append("candles unavailable: a stored bar in the chart window lacks open/high/low")
    sma20_series = [_sma(series[:display_start_idx + i + 1], DEFAULT_TECHNICAL_THRESHOLDS.sma_short)
                    for i in range(len(display))]
    sma50_series = [_sma(series[:display_start_idx + i + 1], DEFAULT_TECHNICAL_THRESHOLDS.sma_long)
                    for i in range(len(display))]

    benchmark_by_date = {r["date"]: r["close"] for r in benchmark_series}
    trailing_stock = series[-(RELATIVE_WINDOW_SESSIONS + 1):]
    shared = [(p["date"], p["close"]) for p in trailing_stock if p["date"] in benchmark_by_date]
    relative_dates = relative_stock_normalized = relative_benchmark_normalized = None
    if len(shared) >= 2 and shared[0][1] and benchmark_by_date[shared[0][0]]:
        relative_dates = [d for d, _ in shared]
        stock_first = shared[0][1]
        bench_first = benchmark_by_date[relative_dates[0]]
        relative_stock_normalized = [c / stock_first * 100 for _, c in shared]
        relative_benchmark_normalized = [benchmark_by_date[d] / bench_first * 100
                                         for d in relative_dates]
    else:
        warnings.append("relative comparison unavailable: insufficient shared trading dates "
                        "with local benchmark history")

    technical_context = story_fields.get("technical_context") or {}
    volume_context = story_fields.get("volume_context") or {}
    relative_context = story_fields.get("relative_context") or {}

    return RadarVisualEvidence(
        instrument=instrument, session_date=session_date,
        calculation_version=RADAR_VISUAL_EVIDENCE_VERSION,
        window_dates=window_dates, close_series=close_series, volume_series=volume_series,
        sma20_series=sma20_series, sma50_series=sma50_series,
        range_20_high=range_20_high, range_20_low=range_20_low,
        range_50_high=range_50_high, range_50_low=range_50_low,
        highlight_events=list(technical_context.get("events") or []),
        rvol=volume_context.get("relative_volume"), rvol_level=volume_context.get("level"),
        stock_return_5d_pct=stock_return_5d_pct, stock_return_20d_pct=stock_return_20d_pct,
        market_relative_5d_pp=relative_context.get("market_relative_5d_pp"),
        market_relative_20d_pp=relative_context.get("market_relative_20d_pp"),
        relative_window_sessions=RELATIVE_WINDOW_SESSIONS, relative_dates=relative_dates,
        relative_stock_normalized=relative_stock_normalized,
        relative_benchmark_normalized=relative_benchmark_normalized,
        benchmark_symbol=BENCHMARK_SYMBOL,
        source_session_dates=[p["date"] for p in series], warnings=warnings,
        open_series=[p["open"] for p in display] if ohlc_complete else None,
        high_series=[p["high"] for p in display] if ohlc_complete else None,
        low_series=[p["low"] for p in display] if ohlc_complete else None)


def build_visual_evidence_for_presentation(presentation, radar_result: dict, *,
                                           out_dir: str | None = None) -> dict:
    """Convenience orchestration for `radar/video_renderer.py`: build
    `{instrument: RadarVisualEvidence | None}` for every STORY scene in `presentation`, reusing
    one `OHLCVStore` connection across all of them. `radar_result` is a
    `radar.daily_pipeline.DailyRadarResult` (or its `to_dict()`/loaded-JSON equivalent) - the
    source of each story's `technical_context`/`volume_context`/`relative_context`. A story
    whose instrument is absent from `radar_result` simply gets no evidence (`None`), same as any
    other unavailable case - it is never invented.
    """
    data = presentation.to_dict() if hasattr(presentation, "to_dict") else dict(presentation)
    session_date = dt.date.fromisoformat(data["session_date"])
    story_instruments = [
        (s.get("story") or {}).get("instrument") for s in data.get("scenes") or []
        if s.get("role") == "STORY" and (s.get("story") or {}).get("instrument")]

    stories = (radar_result.to_dict() if hasattr(radar_result, "to_dict")
              else dict(radar_result)).get("stories") or []
    fields_by_instrument = {
        s["instrument"]: {"technical_context": s.get("technical_context"),
                          "volume_context": s.get("volume_context"),
                          "relative_context": s.get("relative_context")}
        for s in stories if s.get("instrument")}

    out_dir = out_dir or config.OUT_DIR
    try:
        store = OHLCVStore(default_db_path(out_dir))
    except Exception as exc:
        print(f"[radar.visual_evidence] OHLCV store unavailable, no visual evidence for any "
             f"story: {exc}")
        return {instrument: None for instrument in story_instruments}

    result: dict = {}
    try:
        for instrument in story_instruments:
            story_fields = fields_by_instrument.get(instrument)
            if story_fields is None:
                result[instrument] = None
                continue
            result[instrument] = build_visual_evidence(
                instrument, session_date, story_fields, out_dir=out_dir, store=store)
    finally:
        store.close()
    return result


__all__ = ["RADAR_VISUAL_EVIDENCE_VERSION", "CHART_WINDOW_SESSIONS",
          "INDICATOR_LOOKBACK_SESSIONS", "RELATIVE_WINDOW_SESSIONS",
          "BENCHMARK_VISUAL_EVIDENCE_UNAVAILABLE", "RadarVisualEvidence",
          "build_visual_evidence", "build_visual_evidence_for_presentation"]
