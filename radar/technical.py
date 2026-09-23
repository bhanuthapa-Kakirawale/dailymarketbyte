"""Technical-structure detection: did a stock's PRICE STRUCTURE materially change - never a
prediction, a rating, or a BUY/SELL/bullish/bearish label (Phase 4.2 Packet 2).

Two hard boundaries, mirrored from `radar/volume.py`:

* **No acquisition.** The only input is `series_by_symbol` - per-symbol OHLC session history
  already fetched by `radar.technical_acquisition`. Nothing here calls a provider, a website
  or a model.
* **No mutation.** Inputs are read and never written; every reading is a fresh `TechnicalStructure`.

Two window conventions are used side by side, on purpose:

* Range windows (20/50-session high/low) are PRIOR-N-EXCLUSIVE, matching `market.relative_
  volume`'s convention: the current session never participates in its own comparison range.
* Moving averages (SMA20/SMA50) are the conventional INCLUSIVE rolling mean: the current
  session's own close counts toward its own SMA. A cross is a change in the close/SMA
  relationship versus the immediately prior session, not "close is above SMA" restated daily.

No composite score. Every symbol gets discrete, independently-true evidence; nothing here
ranks symbols against each other or decides what matters - see docs/MARKET_INTELLIGENCE_RADAR.md.
"""
from __future__ import annotations

import datetime as dt
from statistics import median as _median

from .models import TechnicalEvent, TechnicalEventType, TechnicalRadarSnapshot, TechnicalStructure
from .thresholds import DEFAULT_TECHNICAL_THRESHOLDS, TechnicalThresholds


def scan_technical_universe(universe: list, series_by_symbol: dict, session_date: dt.date, *,
                            skip_reasons: dict | None = None,
                            thresholds: TechnicalThresholds = DEFAULT_TECHNICAL_THRESHOLDS,
                            as_of: dt.datetime | None = None) -> TechnicalRadarSnapshot:
    """Detect structural events within `universe` for `session_date`.

    `universe` is entirely caller-supplied, exactly like `radar.volume.scan_universe` - never
    defaulted to or hardcoded as a particular index's constituents, and never filtered by
    price move, RVOL or editorial mover selection (technical detection is independent of every
    other detector, by construction: it never reads a `VolumeAnomaly`).

    `series_by_symbol[symbol]` is oldest-first OHLC session history ending at `session_date`
    inclusive - see `radar.technical_acquisition.build_universe_technical_series`. A symbol
    absent (or with fewer than 2 sessions) is recorded in `skipped`, never guessed.
    """
    universe_list = list(dict.fromkeys(universe))
    snapshot = TechnicalRadarSnapshot(
        session_date=session_date, generated_at=as_of or dt.datetime.now(dt.timezone.utc),
        universe_size=len(universe_list), universe_source="caller-supplied universe list")

    for symbol in universe_list:
        series = series_by_symbol.get(symbol)
        if not series or len(series) < 2:
            snapshot.skipped[symbol] = (skip_reasons or {}).get(
                symbol, "no technical series available")
            continue
        snapshot.scanned.append(symbol)
        structure = _build_structure(symbol, series, thresholds)
        if structure.events:
            snapshot.flagged.append(structure)

    snapshot.flagged.sort(key=lambda s: s.instrument)
    return snapshot


def _build_structure(symbol: str, series: list, thresholds: TechnicalThresholds) -> TechnicalStructure:
    """One instrument's structural reading. Always returned (unlike `radar.volume`'s
    `_build_anomaly`) - a symbol with no events is legitimate output, not a skip; the caller
    decides whether to surface it based on `events`."""
    current = series[-1]
    prior = series[:-1]
    close = current["close"]
    events: list = []

    prior_20 = prior[-thresholds.range_window_short:] if len(prior) >= thresholds.range_window_short else None
    prior_50 = prior[-thresholds.range_window_long:] if len(prior) >= thresholds.range_window_long else None

    p20_high, p20_low = _window_high_low(prior_20)
    p50_high, p50_low = _window_high_low(prior_50)

    _range_break_event(events, symbol, close, p20_high, p20_low,
                       TechnicalEventType.BREAK_ABOVE_20D_RANGE,
                       TechnicalEventType.BREAK_BELOW_20D_RANGE, "20", thresholds.range_window_short)
    _range_break_event(events, symbol, close, p50_high, p50_low,
                       TechnicalEventType.BREAK_ABOVE_50D_RANGE,
                       TechnicalEventType.BREAK_BELOW_50D_RANGE, "50", thresholds.range_window_long)

    sma20 = _sma(series, thresholds.sma_short)
    sma50 = _sma(series, thresholds.sma_long)
    distance_from_sma20_pct = _pct(close, sma20)
    distance_from_sma50_pct = _pct(close, sma50)

    _cross_event(events, symbol, series, thresholds.sma_short,
                TechnicalEventType.CROSS_ABOVE_SMA20, TechnicalEventType.CROSS_BELOW_SMA20)
    _cross_event(events, symbol, series, thresholds.sma_long,
                TechnicalEventType.CROSS_ABOVE_SMA50, TechnicalEventType.CROSS_BELOW_SMA50)

    recent_5d_range_pct, median_5d_range_pct = _compression(events, symbol, series, thresholds)

    supporting_dates = [p["date"] for p in (prior_50 or prior_20 or [])] + [current["date"]]

    data_quality = []
    if prior_20 is None:
        data_quality.append(f"insufficient history for a {thresholds.range_window_short}-session range "
                            f"({len(prior)} prior session(s) available)")
    if prior_50 is None:
        data_quality.append(f"insufficient history for a {thresholds.range_window_long}-session range "
                            f"({len(prior)} prior session(s) available)")

    return TechnicalStructure(
        instrument=symbol, market_date=current["date"], close=close,
        prior_20_high=p20_high, prior_20_low=p20_low, prior_50_high=p50_high, prior_50_low=p50_low,
        sma20=sma20, sma50=sma50, distance_from_sma20_pct=distance_from_sma20_pct,
        distance_from_sma50_pct=distance_from_sma50_pct, recent_5d_range_pct=recent_5d_range_pct,
        median_5d_range_pct=median_5d_range_pct, sessions_available=len(prior), events=events,
        supporting_session_dates=supporting_dates, data_quality=data_quality)


def _window_high_low(window: list | None):
    if not window:
        return None, None
    return max(p["high"] for p in window), min(p["low"] for p in window)


def _pct(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference == 0:
        return None
    return (value / reference - 1) * 100


def _range_break_event(events: list, symbol: str, close: float, high: float | None, low: float | None,
                       above_type: TechnicalEventType, below_type: TechnicalEventType,
                       window_label: str, window_n: int) -> None:
    """Sign convention (section 3 of the packet spec): `break_distance_pct` is
    `(close / prior_high - 1) * 100` for an upper break (positive - close is above the range)
    and `(close / prior_low - 1) * 100` for a lower break (negative - close is below the
    range). A close exactly equal to the prior high/low is NOT a break - `>`/`<`, never `>=`/
    `<=`."""
    if high is not None and close > high:
        distance = _pct(close, high)
        events.append(TechnicalEvent(
            event_type=above_type,
            evidence={"close": close, "prior_high": high, "window_sessions": window_n,
                     "break_distance_pct": distance},
            why=(f"{symbol}: close {close:.2f} is above its prior {window_label}-session high of "
                f"{high:.2f} ({distance:+.2f}%).")))
    elif low is not None and close < low:
        distance = _pct(close, low)
        events.append(TechnicalEvent(
            event_type=below_type,
            evidence={"close": close, "prior_low": low, "window_sessions": window_n,
                     "break_distance_pct": distance},
            why=(f"{symbol}: close {close:.2f} is below its prior {window_label}-session low of "
                f"{low:.2f} ({distance:+.2f}%).")))


def _sma(series: list, n: int) -> float | None:
    if len(series) < n:
        return None
    window = series[-n:]
    return sum(p["close"] for p in window) / n


def _cross_event(events: list, symbol: str, series: list, n: int,
                 above_type: TechnicalEventType, below_type: TechnicalEventType) -> None:
    """A cross is a TRANSITION versus the immediately prior session, not "close > SMA" restated
    every day - needs `n` sessions ending at the prior session too, so `n + 1` sessions total."""
    if len(series) < n + 1:
        return
    current_sma = _sma(series, n)
    previous_sma = _sma(series[:-1], n)
    if current_sma is None or previous_sma is None:
        return
    current_close, previous_close = series[-1]["close"], series[-2]["close"]
    was_above = previous_close > previous_sma
    is_above = current_close > current_sma
    if was_above == is_above:
        return
    label = str(n)
    event_type = above_type if is_above else below_type
    direction = "above" if is_above else "below"
    events.append(TechnicalEvent(
        event_type=event_type,
        evidence={"previous_close": previous_close, "previous_sma": previous_sma,
                 "current_close": current_close, "current_sma": current_sma},
        why=(f"{symbol}: close crossed {direction} its {label}-session average (previous close "
            f"{previous_close:.2f} vs SMA{label} {previous_sma:.2f}; now {current_close:.2f} vs "
            f"SMA{label} {current_sma:.2f}).")))


def _window_range_pct(window: list) -> float | None:
    """(max high - min low) over the window, normalised by the window's OWN last close - the
    same "percent of price" convention `break_distance_pct` uses, so ranges of differently-
    priced stocks are comparable."""
    denom = window[-1]["close"]
    if not denom:
        return None
    high, low = _window_high_low(window)
    return (high - low) / denom * 100


def _compression(events: list, symbol: str, series: list, thresholds: TechnicalThresholds):
    """RANGE_COMPRESSION (section 6): the most recent `compression_window`-session range versus
    the median of `compression_lookback_windows` PRIOR non-overlapping windows of the same
    size. Flagged only when genuinely tight (`<= compression_ratio * median`) and only once
    the full prior-window sample exists - a partial sample never silently substitutes for it.
    Returns (recent_5d_range_pct, median_5d_range_pct) - the first may be reported as context
    even when the second (and so the event) is unavailable.
    """
    w = thresholds.compression_window
    if len(series) < w:
        return None, None
    recent_window = series[-w:]
    recent_pct = _window_range_pct(recent_window)

    prior_windows_pct = []
    end = len(series) - w
    for _ in range(thresholds.compression_lookback_windows):
        start = end - w
        if start < 0:
            break
        window = series[start:end]
        pct = _window_range_pct(window)
        if pct is not None:
            prior_windows_pct.append(pct)
        end = start

    if len(prior_windows_pct) < thresholds.compression_lookback_windows:
        return recent_pct, None

    median_pct = _median(prior_windows_pct)
    if (recent_pct is not None and median_pct and
            recent_pct <= thresholds.compression_ratio * median_pct):
        events.append(TechnicalEvent(
            event_type=TechnicalEventType.RANGE_COMPRESSION,
            evidence={"recent_range_pct": recent_pct, "median_range_pct": median_pct,
                     "window_sessions": w, "lookback_windows": thresholds.compression_lookback_windows},
            why=(f"{symbol}: {w}-session trading range of {recent_pct:.2f}% is compressed versus "
                f"its median {w}-session range of {median_pct:.2f}% over the prior "
                f"{thresholds.compression_lookback_windows} windows.")))
    return recent_pct, median_pct


__all__ = ["scan_technical_universe"]
