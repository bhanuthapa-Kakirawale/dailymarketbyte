"""Shared builders for universe-wide RVOL acquisition tests (Phase 4.2 Packet 1.1).

Fabricates a synthetic multi-ticker `yf.download(group_by="ticker")`-shaped frame and
monkeypatches `market._bulk_download_universe_ohlcv` with it, so `market.get_universe_
relative_volume` runs its real logic (retry/backfill/date-alignment/relative_volume) against
known data - no network, no live yfinance call anywhere.
"""
import datetime as dt

import pandas as pd

import market

# Safely in the past relative to any real "now" this suite runs under, so market._clean's
# "never use a session still running" same-day guard never fires against fixture data.
RECAP_DATE = dt.date(2026, 8, 3)


def synthetic_universe(n: int = 30, prefix: str = "SYM") -> dict:
    return {f"{prefix}{i:03d}": f"{prefix}{i:03d} Ltd" for i in range(n)}


def universe_sessions(sessions: int = 63, recap_date: dt.date = RECAP_DATE):
    """Business-day session index ending at `recap_date`, matching how `get_movers`/
    `get_universe_relative_volume` derive `recap_date`/`prev_date` from real yfinance data."""
    idx = pd.bdate_range(end=recap_date, periods=sessions)
    return idx, idx[-1].date(), idx[-2].date()


def fake_bulk_ohlcv(monkeypatch, symbols, recap_date: dt.date = RECAP_DATE, sessions: int = 63,
                    volume_map: dict | None = None, missing=(), nan_close=(), date_gap=(),
                    short_history: dict | None = None, close_map: dict | None = None,
                    high_map: dict | None = None, low_map: dict | None = None,
                    future_sessions: dict | None = None, future_close_map: dict | None = None,
                    future_volume_map: dict | None = None):
    """Monkeypatch `market._bulk_download_universe_ohlcv` to return a synthetic frame shaped
    exactly like the real `yf.download([...], group_by="ticker")` result.

    `volume_map`: {symbol: [volumes...]} aligned oldest-first to the session index, defaulting
    to a flat 1,000,000-share series. `missing`: symbols entirely absent from the response
    (-> "no_recap_row"). `nan_close`: symbols whose recap-date row has Close still NaN
    (-> "backfill_pending", persisting for all 3 retry attempts since the fixture never
    resolves). `date_gap`: symbols missing the session immediately before `recap_date`
    (-> "date_gap"). `short_history`: {symbol: session_count} for symbols with fewer than
    `sessions` rows (an IPO with too little history for RVOL's 20-prior-session minimum ->
    "insufficient_relative_volume_history"), still ending at `recap_date`.

    `close_map`/`high_map`/`low_map`: {symbol: [values...]} aligned oldest-first, for tests
    that need real price structure (technical-radar range/SMA fixtures) rather than the flat
    100.0 series every other field defaults to. Omit a symbol from a map to keep the default.

    `future_sessions`: {symbol: n} appends `n` business-day sessions strictly AFTER
    `recap_date` to that symbol's series - simulating a symbol whose own Yahoo data has already
    advanced past the session being analyzed (Phase 4.2 Packet 5.3B's look-ahead scenario).
    `future_close_map`/`future_volume_map`: {symbol: [values...]} for those extra sessions;
    default to deliberately extreme values (5x the recap-date close, 50,000,000 volume) so a
    look-ahead bug that lets them leak into a recap-date calculation is impossible to miss.

    Also monkeypatches `market.time.sleep` to a no-op so the real 30s/60s backfill-retry waits
    never block a test.
    """
    monkeypatch.setattr(market.time, "sleep", lambda *_a, **_k: None)

    idx = pd.bdate_range(end=recap_date, periods=sessions)
    frames = {}
    for symbol in symbols:
        if symbol in missing:
            continue
        n = (short_history or {}).get(symbol, sessions)
        sym_idx = idx if n == sessions else pd.bdate_range(end=recap_date, periods=n)
        vols = (volume_map or {}).get(symbol, [1_000_000.0] * n)
        assert len(vols) == n, f"{symbol}: volume series must cover all {n} sessions"
        closes = (close_map or {}).get(symbol, [100.0] * n)
        highs = (high_map or {}).get(symbol, closes)
        lows = (low_map or {}).get(symbol, closes)
        assert len(closes) == n and len(highs) == n and len(lows) == n, \
            f"{symbol}: price series must cover all {n} sessions"
        close = pd.Series(closes, index=sym_idx)
        if symbol in nan_close:
            close.iloc[-1] = float("nan")
        df = pd.DataFrame({"Open": pd.Series(closes, index=sym_idx),
                           "High": pd.Series(highs, index=sym_idx),
                           "Low": pd.Series(lows, index=sym_idx), "Close": close,
                           "Volume": pd.Series(vols, index=sym_idx)})
        if symbol in date_gap:
            df = df.drop(df.index[-2])
        if future_sessions and symbol in future_sessions:
            n_future = future_sessions[symbol]
            fut_idx = pd.bdate_range(start=recap_date, periods=n_future + 1)[1:]
            fut_close = (future_close_map or {}).get(symbol, [closes[-1] * 5.0] * n_future)
            fut_vol = (future_volume_map or {}).get(symbol, [50_000_000.0] * n_future)
            assert len(fut_close) == n_future and len(fut_vol) == n_future, \
                f"{symbol}: future series must cover all {n_future} future sessions"
            fut_df = pd.DataFrame({"Open": pd.Series(fut_close, index=fut_idx),
                                   "High": pd.Series([c * 1.01 for c in fut_close], index=fut_idx),
                                   "Low": pd.Series([c * 0.99 for c in fut_close], index=fut_idx),
                                   "Close": pd.Series(fut_close, index=fut_idx),
                                   "Volume": pd.Series(fut_vol, index=fut_idx)})
            df = pd.concat([df, fut_df])
        frames[f"{symbol}.NS"] = df

    raw = pd.concat(frames, axis=1, sort=False) if frames else pd.DataFrame()
    monkeypatch.setattr(market, "_bulk_download_universe_ohlcv",
                        lambda syms, period="3mo": raw)
    return idx[-1].date(), idx[-2].date()
