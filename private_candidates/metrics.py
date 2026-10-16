"""Small, new, clearly-documented deterministic calculations this package needs but the rest of
the repository does not already compute: ATR14, a weekly resample of daily OHLCV, structural
reference/extension/risk-width distance, a regime-streak count, and a trading-session event
window. Every one of these was confirmed absent from the codebase during design (no ATR, no
weekly bar aggregation, no stock-vs-sector wiring in production, no regime-streak helper, no
event-window helper anywhere) - see docs/PRIVATE_TRADE_CANDIDATES.md.

Pure functions only: every input here is data already acquired elsewhere (OHLCV rows, a
`MarketRegimeSnapshot` list, event dicts) - nothing in this module fetches or mutates.
"""
from __future__ import annotations

import datetime as dt

from .models import (ExtensionState, MTFAlignment, PullbackState, RiskWidthLabel, VolatilityBand)
from .policy import DEFAULT_SCORING_POLICY, ScoringPolicy


def _as_date(d) -> dt.date:
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d)[:10])


# ------------------------------------------------------------------ ATR14
def atr14(daily_rows: list, window: int = DEFAULT_SCORING_POLICY.atr_window) -> float | None:
    """Standard True Range average over the trailing `window` sessions.

    `TR_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)`;
    `ATR = mean(TR)` over the most recent `window` sessions. Needs `window + 1` rows (one prior
    close plus `window` sessions of TR) - `None` below that, never computed over a shorter
    window and reported as the requested one (same discipline `radar/relative.py` documents for
    its own windows)."""
    rows = [r for r in daily_rows if r.get("high") is not None and r.get("low") is not None
           and r.get("close") is not None]
    if len(rows) < window + 1:
        return None
    rows = rows[-(window + 1):]
    trs = []
    for i in range(1, len(rows)):
        high, low, prev_close = rows[i]["high"], rows[i]["low"], rows[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs) if trs else None


# ------------------------------------------------------------------ weekly resample (MTF)
def weekly_resample(daily_rows: list) -> list:
    """Group daily OHLCV rows into completed ISO weeks (Mon-Fri), oldest first.

    The week containing the LAST row in `daily_rows` (the session being evaluated) is always
    excluded - a partial, still-forming week must never be reported as a completed one. Each
    returned week is `{"week_start": date, "open", "high", "low", "close", "volume"}` from that
    week's own first/last/extreme daily rows."""
    rows = [r for r in daily_rows if r.get("date") is not None]
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: _as_date(r["date"]))
    last_week_start = _as_date(rows[-1]["date"]) - dt.timedelta(days=_as_date(rows[-1]["date"]).weekday())

    by_week: dict = {}
    for r in rows:
        d = _as_date(r["date"])
        wk_start = d - dt.timedelta(days=d.weekday())
        if wk_start == last_week_start:
            continue      # current, still-forming week - never a completed one
        by_week.setdefault(wk_start, []).append(r)

    out = []
    for wk_start in sorted(by_week):
        week_rows = sorted(by_week[wk_start], key=lambda r: _as_date(r["date"]))
        highs = [r["high"] for r in week_rows if r.get("high") is not None]
        lows = [r["low"] for r in week_rows if r.get("low") is not None]
        vols = [r["volume"] for r in week_rows if r.get("volume") is not None]
        out.append({"week_start": wk_start, "open": week_rows[0].get("open"),
                   "high": max(highs) if highs else None, "low": min(lows) if lows else None,
                   "close": week_rows[-1].get("close"), "volume": sum(vols) if vols else None})
    return out


def weekly_alignment(daily_rows: list, price_change_pct: float | None,
                     policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    """Is the daily candidate's direction consistent with the larger weekly structure?

    Weekly structure = the last completed week's close vs. a simple SMA over the trailing
    `policy.weekly_sma_window` completed weekly closes (inclusive of that last completed week,
    the same "current reading counts toward its own SMA" convention `radar/models.py`'s
    `TechnicalStructure.sma20/50` already documents). Needs at least
    `policy.weekly_min_completed_weeks` completed weeks; below that, insufficient."""
    weeks = weekly_resample(daily_rows)
    if len(weeks) < policy.weekly_min_completed_weeks or price_change_pct is None:
        return {"alignment": MTFAlignment.WEEKLY_INSUFFICIENT_DATA.value,
               "completed_weeks": len(weeks), "weekly_close": None, "weekly_sma": None,
               "evidence": f"{len(weeks)} completed week(s) available, "
                          f"{policy.weekly_min_completed_weeks} required for weekly context"}
    closes = [w["close"] for w in weeks if w.get("close") is not None]
    if len(closes) < policy.weekly_sma_window:
        return {"alignment": MTFAlignment.WEEKLY_INSUFFICIENT_DATA.value,
               "completed_weeks": len(weeks), "weekly_close": None, "weekly_sma": None,
               "evidence": "weekly closes incomplete for the SMA window"}
    weekly_sma = sum(closes[-policy.weekly_sma_window:]) / policy.weekly_sma_window
    weekly_close = closes[-1]
    weekly_sign = weekly_close - weekly_sma
    aligned = (price_change_pct > 0 and weekly_sign > 0) or (price_change_pct < 0 and weekly_sign < 0)
    neutral_daily = price_change_pct == 0 or weekly_sign == 0
    alignment = (MTFAlignment.ALIGNED if aligned and not neutral_daily
                else MTFAlignment.DIVERGENT if not neutral_daily
                else MTFAlignment.WEEKLY_INSUFFICIENT_DATA)
    return {"alignment": alignment.value, "completed_weeks": len(weeks),
           "weekly_close": weekly_close, "weekly_sma": weekly_sma,
           "evidence": f"weekly close {weekly_close:.2f} vs {policy.weekly_sma_window}-week "
                      f"average {weekly_sma:.2f} over {len(weeks)} completed weeks"}


# ------------------------------------------------------------------ structural reference
def structural_reference(prior_20_low: float | None, prior_20_high: float | None,
                         price_change_pct: float | None) -> dict:
    """The nearest already-computed structural level this package reuses as a reference
    (owner spec section 18; option (a) in the design doc - `prior_20_low`/`prior_20_high` from
    `radar.models.TechnicalStructure`, never a new swing/trendline indicator in V1).

    A positive `price_change_pct` (or an `ALIGNED_POSITIVE` candidate) reads as long-biased:
    the reference is `prior_20_low` (the level below which the recent constructive structure
    would be undermined). A negative reading is short-biased: the reference is
    `prior_20_high`. Direction-less input (`None` or exactly 0) has no bias to reference -> both
    `None`."""
    if price_change_pct is None or price_change_pct == 0:
        return {"price": None, "kind": None}
    if price_change_pct > 0:
        return {"price": prior_20_low, "kind": "PRIOR_20_LOW" if prior_20_low is not None else None}
    return {"price": prior_20_high, "kind": "PRIOR_20_HIGH" if prior_20_high is not None else None}


def range_position_pct(close: float | None, prior_20_low: float | None,
                       prior_20_high: float | None) -> float | None:
    """Where `close` sits between `prior_20_low` (0) and `prior_20_high` (100), clamped.
    `None` when either bound is missing or the range is degenerate (`high == low`)."""
    if close is None or prior_20_low is None or prior_20_high is None:
        return None
    if prior_20_high == prior_20_low:
        return None
    pct = (close - prior_20_low) / (prior_20_high - prior_20_low) * 100
    return max(0.0, min(100.0, pct))


def extension_state(distance_from_sma20_pct: float | None, close: float | None,
                    atr: float | None,
                    policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    """How far price has already moved from its 20-session mean, in ATR units - never a
    directional prediction, only a distance reading (owner spec section 17: ATR-normalized
    distance from a structural reference, not "stock has gone up too much")."""
    if distance_from_sma20_pct is None or close is None or atr is None or atr <= 0:
        return {"state": ExtensionState.EXTENSION_UNKNOWN.value, "atr_units": None}
    distance_price = abs(distance_from_sma20_pct) / 100.0 * close
    atr_units = distance_price / atr
    if atr_units < policy.extension_normal_atr:
        state = ExtensionState.NORMAL
    elif atr_units < policy.extension_stretched_atr:
        state = ExtensionState.STRETCHED
    else:
        state = ExtensionState.DEEPLY_STRETCHED
    return {"state": state.value, "atr_units": atr_units}


def pullback_state(close: float | None, structural_reference_price: float | None,
                   atr: float | None,
                   policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    """Distance from the structural reference level, in ATR units."""
    if close is None or structural_reference_price is None or atr is None or atr <= 0:
        return {"state": PullbackState.PULLBACK_UNKNOWN.value, "atr_units": None}
    atr_units = abs(close - structural_reference_price) / atr
    if atr_units < policy.pullback_at_structure_atr:
        state = PullbackState.AT_STRUCTURE
    elif atr_units < policy.pullback_near_structure_atr:
        state = PullbackState.NEAR_STRUCTURE
    else:
        state = PullbackState.EXTENDED_FROM_STRUCTURE
    return {"state": state.value, "atr_units": atr_units}


def risk_width_label(close: float | None, structural_reference_price: float | None,
                     atr: float | None,
                     policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    """A qualitative distance label, NEVER a reward:risk ratio - no profit target exists
    anywhere in this system to pair it with (owner spec section 19)."""
    if close is None or structural_reference_price is None or atr is None or atr <= 0:
        return {"label": RiskWidthLabel.RISK_WIDTH_UNKNOWN.value, "atr_units": None}
    atr_units = abs(close - structural_reference_price) / atr
    if atr_units < policy.risk_width_narrow_atr:
        label = RiskWidthLabel.NARROW
    elif atr_units < policy.risk_width_wide_atr:
        label = RiskWidthLabel.MODERATE
    else:
        label = RiskWidthLabel.WIDE
    return {"label": label.value, "atr_units": atr_units}


def volatility_band(atr_pct: float | None,
                    policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> str:
    if atr_pct is None:
        return VolatilityBand.VOLATILITY_UNKNOWN.value
    if atr_pct < policy.volatility_tight_below_pct:
        return VolatilityBand.TIGHT.value
    if atr_pct < policy.volatility_normal_below_pct:
        return VolatilityBand.NORMAL.value
    if atr_pct < policy.volatility_elevated_below_pct:
        return VolatilityBand.ELEVATED.value
    return VolatilityBand.EXTREME.value


def liquidity_band(average_volume_20: float | None, close: float | None,
                   policy: ScoringPolicy = DEFAULT_SCORING_POLICY) -> dict:
    """Average 20-session turnover (INR crore) from data this pipeline actually has
    (volume * price) - never a bid/ask spread claim, which this pipeline does not possess
    (owner spec section 8: NIFTY 200 membership alone is not treated as proof of liquidity)."""
    from .models import LiquidityBand
    if average_volume_20 is None or close is None:
        return {"band": LiquidityBand.LIQUIDITY_UNKNOWN.value, "turnover_cr": None}
    turnover_cr = (average_volume_20 * close) / 1e7
    if turnover_cr < policy.liquidity_thin_below_cr:
        band = LiquidityBand.THIN
    elif turnover_cr < policy.liquidity_deep_at_or_above_cr:
        band = LiquidityBand.ADEQUATE
    else:
        band = LiquidityBand.DEEP
    return {"band": band.value, "turnover_cr": turnover_cr}


# ------------------------------------------------------------------ regime streak
def regime_streak(snapshots: list, session_iso: str, lookback: int = DEFAULT_SCORING_POLICY.regime_streak_lookback) -> int:
    """Consecutive sessions (ending at `session_iso`, inclusive) with the IDENTICAL `.regime`
    label, walking `snapshots` (oldest first, as `RegimeStore.snapshots` returns) backward.
    A gap/mismatch (wrong ending session, or `INSUFFICIENT_DATA`/differing regime) breaks the
    streak at that point rather than skipping over it - the same "never step over a break"
    discipline the canonical-history streak rules in CLAUDE.md already require elsewhere."""
    ordered = [s for s in snapshots if s.session_date <= session_iso]
    ordered.sort(key=lambda s: s.session_date)
    if not ordered or ordered[-1].session_date != session_iso:
        return 0
    label = ordered[-1].regime
    streak = 0
    for snap in reversed(ordered[-lookback:]):
        if snap.regime != label:
            break
        streak += 1
    return streak


# ------------------------------------------------------------------ event window (trading sessions)
def trading_session_distance(calendar, from_date: dt.date, to_date: dt.date) -> int:
    """Signed count of canonical trading sessions between two dates (`to_date` after
    `from_date` -> positive) - counts SESSIONS, not calendar days, per CLAUDE.md's rule that
    every window in this codebase counts trading sessions."""
    from_date, to_date = _as_date(from_date), _as_date(to_date)
    if to_date == from_date:
        return 0
    lo, hi = (from_date, to_date) if from_date < to_date else (to_date, from_date)
    n = len(calendar.sessions_between(lo, hi)) - 1   # exclude the earlier endpoint itself
    n = max(n, 0)
    return n if to_date >= from_date else -n


def nearest_event_distance(events: list, session: dt.date, calendar) -> int | None:
    """The smallest absolute trading-session distance from `session` to any event's own
    `data_as_of`, or `None` if no event carries a parseable date. `events` is a list of dicts/
    objects exposing `data_as_of` (ISO date string or `None`)."""
    best = None
    for ev in events:
        raw = ev.get("data_as_of") if isinstance(ev, dict) else getattr(ev, "data_as_of", None)
        if not raw:
            continue
        try:
            event_date = dt.date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        distance = trading_session_distance(calendar, session, event_date)
        if best is None or abs(distance) < abs(best):
            best = distance
    return best


# ------------------------------------------------------------------ average volume (liquidity fallback)
def average_volume(daily_rows: list, window: int = 20) -> float | None:
    """Mean volume over the trailing `window` sessions - a plain average over data already
    read for ATR/weekly purposes, used ONLY as a fallback for the liquidity component when a
    candidate has no `VolumeAnomaly` object. `VolumeAnomaly.average_volume_20` is only ever
    constructed for a symbol whose volume actually cleared the ELEVATED anomaly threshold
    (`radar.thresholds.classify_anomaly`) - most candidates qualify via STRUCTURE +
    RELATIVE_PERFORMANCE alone and never get one, so liquidity must not silently go
    LIQUIDITY_UNKNOWN for them when the same average is trivially computable from the OHLCV
    already on hand."""
    vols = [r["volume"] for r in daily_rows if r.get("volume") is not None]
    if len(vols) < min(window, 5):
        return None
    return sum(vols[-window:]) / len(vols[-window:])


# ------------------------------------------------------------------ equal-weight sector series
def build_equal_weight_sector_series(members_series: dict) -> list:
    """A synthetic sector series from whichever sector members have OHLCV available, built as
    the equal-weight AVERAGE OF REBASED RETURNS (each member rebased to 100 at its own first
    available date, then averaged across members per date) - never a raw average of price
    levels, which would let a single high-priced member dominate the level the way a
    price-weighted (not cap-weighted) index would. This is a new, local, read-only
    approximation computed by this package alone for private research context; it is NOT NSE's
    or any official sector index, and it never feeds back into `radar.daily_pipeline`'s
    production wiring. `members_series`: `{symbol: [{"date","close",...}, ...]}` (oldest first).
    Returns `[{"date","close"}]` oldest first, or `[]` if no member has usable data."""
    rebased: dict = {}
    for sym, rows in members_series.items():
        rows = sorted((r for r in rows if r.get("close")), key=lambda r: _as_date(r["date"]))
        if not rows:
            continue
        base = rows[0]["close"]
        if not base:
            continue
        rebased[sym] = {_as_date(r["date"]): (r["close"] / base * 100) for r in rows}
    if not rebased:
        return []
    all_dates = sorted(set().union(*rebased.values()))
    out = []
    for d in all_dates:
        vals = [series[d] for series in rebased.values() if d in series]
        if vals:
            out.append({"date": d, "close": sum(vals) / len(vals)})
    return out


# ------------------------------------------------------------------ pairwise return correlation
def pairwise_return_correlation(rows_a: list, rows_b: list,
                                min_sample: int = DEFAULT_SCORING_POLICY.correlation_min_sample
                                ) -> float | None:
    """Pearson correlation of daily returns over dates both series share, over a TRAILING
    window of `rows_a`/`rows_b` (the caller bounds the window by how many rows it passes in -
    owner spec section 30: never on price levels, always on returns, and `None`/UNKNOWN below
    `min_sample` overlapping sessions rather than a false-precision correlation off too little
    data)."""
    def _returns(rows: list) -> dict:
        rows = sorted((r for r in rows if r.get("close")), key=lambda r: _as_date(r["date"]))
        out = {}
        for i in range(1, len(rows)):
            prev, cur = rows[i - 1]["close"], rows[i]["close"]
            if prev:
                out[_as_date(rows[i]["date"])] = (cur / prev) - 1
        return out

    ra, rb = _returns(rows_a), _returns(rows_b)
    shared = sorted(set(ra) & set(rb))
    if len(shared) < min_sample:
        return None
    xa = [ra[d] for d in shared]
    xb = [rb[d] for d in shared]
    mean_a, mean_b = sum(xa) / len(xa), sum(xb) / len(xb)
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(xa, xb))
    var_a = sum((a - mean_a) ** 2 for a in xa)
    var_b = sum((b - mean_b) ** 2 for b in xb)
    if var_a <= 0 or var_b <= 0:
        return None
    return cov / (var_a ** 0.5 * var_b ** 0.5)


__all__ = ["atr14", "average_volume", "weekly_resample", "weekly_alignment",
          "structural_reference", "range_position_pct", "extension_state", "pullback_state",
          "risk_width_label", "volatility_band", "liquidity_band", "regime_streak",
          "trading_session_distance", "nearest_event_distance",
          "build_equal_weight_sector_series", "pairwise_return_correlation"]
