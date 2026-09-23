"""Market data (Yahoo Finance via yfinance) and technical analysis for the Nifty section."""
import io
import time
import datetime as dt

import numpy as np
import pandas as pd
import requests

from config import IST, now_ist

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

_SLUG = {"NIFTY50": "nifty50", "NIFTY100": "nifty100", "NIFTY200": "nifty200", "NIFTY500": "nifty500"}

# Used only if the NSE constituent CSV can't be downloaded. Constituents change twice a year.
FALLBACK_NIFTY50 = {
    "ADANIENT": "Adani Enterprises", "ADANIPORTS": "Adani Ports", "APOLLOHOSP": "Apollo Hospitals",
    "ASIANPAINT": "Asian Paints", "AXISBANK": "Axis Bank", "BAJAJ-AUTO": "Bajaj Auto",
    "BAJFINANCE": "Bajaj Finance", "BAJAJFINSV": "Bajaj Finserv", "BEL": "Bharat Electronics",
    "BHARTIARTL": "Bharti Airtel", "CIPLA": "Cipla", "COALINDIA": "Coal India", "DRREDDY": "Dr Reddy's",
    "EICHERMOT": "Eicher Motors", "ETERNAL": "Eternal", "GRASIM": "Grasim", "HCLTECH": "HCL Tech",
    "HDFCBANK": "HDFC Bank", "HDFCLIFE": "HDFC Life", "HINDALCO": "Hindalco", "HINDUNILVR": "Hindustan Unilever",
    "ICICIBANK": "ICICI Bank", "INDIGO": "InterGlobe Aviation", "INFY": "Infosys", "ITC": "ITC",
    "JIOFIN": "Jio Financial", "JSWSTEEL": "JSW Steel", "KOTAKBANK": "Kotak Mahindra Bank", "LT": "Larsen & Toubro",
    "M&M": "Mahindra & Mahindra", "MARUTI": "Maruti Suzuki", "MAXHEALTH": "Max Healthcare", "NESTLEIND": "Nestle India",
    "NTPC": "NTPC", "ONGC": "ONGC", "POWERGRID": "Power Grid", "RELIANCE": "Reliance Industries",
    "SBILIFE": "SBI Life", "SBIN": "State Bank of India", "SHRIRAMFIN": "Shriram Finance", "SUNPHARMA": "Sun Pharma",
    "TATACONSUM": "Tata Consumer", "TMPV": "Tata Motors PV", "TATASTEEL": "Tata Steel", "TCS": "TCS",
    "TECHM": "Tech Mahindra", "TITAN": "Titan", "TRENT": "Trent", "ULTRACEMCO": "UltraTech Cement", "WIPRO": "Wipro",
}


# ----------------------------------------------------------------------------- data
def get_universe(name: str) -> dict:
    slug = _SLUG.get(name, "nifty100")
    urls = [f"https://archives.nseindia.com/content/indices/ind_{slug}list.csv",
            f"https://www.niftyindices.com/IndexConstituent/ind_{slug}list.csv"]
    for url in urls:
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            uni = dict(zip(df["Symbol"].astype(str).str.strip(), df["Company Name"].astype(str).str.strip()))
            if len(uni) >= 40:
                print(f"[market] universe {name}: {len(uni)} stocks")
                return uni
        except Exception as e:
            print(f"[market] constituent list failed ({url}): {e}")
    print("[market] using built-in Nifty 50 fallback list")
    return dict(FALLBACK_NIFTY50)


def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert(IST).tz_localize(None)
    df.index = idx.normalize()
    return df[~df.index.duplicated(keep="last")]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_index(df.dropna(subset=["Close"]))
    now = now_ist()
    # never use a session that is still running
    if len(df) and df.index[-1].date() == now.date() and now.time() < dt.time(15, 40):
        df = df.iloc[:-1]
    return df


def _truncate_to_recap(d: pd.DataFrame, recap_date: dt.date) -> pd.DataFrame:
    """Drop any row dated AFTER `recap_date` from an already-`_clean`ed frame (Phase 4.2 Packet
    5.3B, look-ahead prevention).

    A symbol's own Yahoo series can already have advanced past `recap_date` - later sessions
    already published for that stock - independent of whatever session `recap_date` itself
    represents (verified 2026-09-23: while the Nifty INDEX chart still lagged at 21 Sep, most
    individual NIFTY200 stocks' own series already had 22/23 Sep rows). Acceptance of a recap
    session must be "does `recap_date` have a valid row in this stock's own series", never "is
    `recap_date` the LAST row" - a later, unrelated session existing must never invalidate an
    otherwise-valid recap-date reading (this previously produced 152 false `backfill_pending`
    results for a genuinely valid session), and must never be visible to any calculation FOR
    that recap date (RVOL's prior-volume window, technical SMA/range windows, day-over-day
    %). Every caller of this truncates immediately after `_clean()`, before any date-alignment
    check or calculation reads `d.index[-1]`/`d.index[-2]`/`d.Close`/`d.Volume` - the "last row"
    of the returned frame is then always `recap_date` itself when present, restoring the
    invariant the rest of this file's `iloc[-1]`/`iloc[-2]` logic already assumes.
    """
    return d[d.index.date <= recap_date]


def retry(fn, tries=3, wait=4):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(wait * (i + 1))
    raise last


def history(ticker: str, period: str = "1y") -> pd.DataFrame:
    import yfinance as yf

    def _get():
        d = _clean(yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False))
        if d.empty:
            raise RuntimeError(f"no data for {ticker}")
        return d
    return retry(_get)


def _nifty_history_with_backfill_check(period: str = "1y", max_extra_attempts: int = 2):
    """Nifty's own chart history can lag one session behind Yahoo's already-published live
    quote for the same index (verified 2026-09-23: `^NSEI`'s chart stayed at 21-Sep as its
    latest bar while `history_metadata.regularMarketTime` already showed a 22-Sep close, for
    well over ten minutes of repeated checking). This matters far more than an ordinary stale
    read: `get_market()`'s Nifty fetch sets `recap_date`, and every other date-alignment check
    in the pipeline (NSE indices, sectors, movers, FII/DII) is gated against that one date - a
    stale recap_date silently starves the whole run of same-day data that is, in fact, already
    published elsewhere (verified same day: NSE's own `/api/allIndices` already had all 12
    sector indices for 22-Sep, correctly rejected only because it didn't match this stale date).

    Detected by comparing the chart's last bar to the live quote's own timestamp - never a
    wall-clock guess - and given the same escalating-wait retry `get_movers` already uses for
    its analogous Close-backfill lag (30s, then 60s) before giving up and using whatever the
    chart already has. A lag that outlasts these retries is not fixed here, by design - the
    right recovery for a lag lasting minutes is a later run or the next scheduled cron, not
    inventing a session from the live quote's other metadata fields (verified separately that
    `chartPreviousClose`/`regularMarketPrice` disagree with verified chart data by several
    percentage points and must never be used as a data source)."""
    import yfinance as yf

    def _fetch():
        tk = yf.Ticker("^NSEI")
        d = _clean(tk.history(period=period, interval="1d", auto_adjust=False))
        if d.empty:
            raise RuntimeError("no data for ^NSEI")
        return d, (tk.history_metadata or {})

    d = meta = None
    for attempt in range(max_extra_attempts + 1):
        d, meta = retry(_fetch)
        live_time = meta.get("regularMarketTime")
        lagging = live_time is not None and live_time.date() > d.index[-1].date()
        if not lagging or attempt == max_extra_attempts:
            if lagging:
                print(f"[market] Nifty chart still lags the live quote after retries "
                      f"(chart last={d.index[-1].date()}, live={live_time.date()}) - "
                      "using the chart's own latest session")
            return d
        wait = 30 * (attempt + 1)
        print(f"[market] Nifty chart last bar is {d.index[-1].date()} but the live quote is "
              f"already {live_time.date()} - retrying in {wait}s "
              f"(attempt {attempt + 1}/{max_extra_attempts})")
        time.sleep(wait)
    return d


def get_market() -> dict:
    nifty = _nifty_history_with_backfill_check("1y")
    if len(nifty) < 60:
        raise RuntimeError("Not enough Nifty history returned")
    bank_pct = vix = None
    try:
        b = history("^NSEBANK", "1mo")
        if b.index[-1] == nifty.index[-1] and b.index[-2] == nifty.index[-2]:
            bank_pct = float((b.Close.iloc[-1] / b.Close.iloc[-2] - 1) * 100)
    except Exception as e:
        print(f"[market] Bank Nifty failed: {e}")
    try:
        vix = float(history("^INDIAVIX", "1mo").Close.iloc[-1])
    except Exception as e:
        print(f"[market] VIX failed: {e}")
    return analyze(nifty, bank_pct, vix)


# Relative volume, defined explicitly because "2.4x average volume" is meaningless without
# saying which average. Version 2.0 (Phase 3) fixed the window at 20 PRIOR sessions; version
# 1.x used 10 and is what every report generated before Phase 3 contains. Reports are not
# rewritten, so the definition travels with the number instead.
RELATIVE_VOLUME_LOOKBACK = 20
RELATIVE_VOLUME_DEFINITION = {
    "definition": "current_volume / mean_prior_volume",
    "lookback_sessions": RELATIVE_VOLUME_LOOKBACK,
    "minimum_required_sessions": RELATIVE_VOLUME_LOOKBACK,
    "includes_current_session": False,
    "definition_version": "2.0",
}


def relative_volume(volumes, lookback: int = RELATIVE_VOLUME_LOOKBACK):
    """Current session's volume over the mean of the `lookback` sessions immediately before it.

    The current session is excluded from the denominator: including it would damp exactly the
    spike the metric exists to show. With fewer than `lookback` prior sessions the answer is
    None rather than a mean over whatever happens to be available - silently computing over 7
    or 15 sessions and calling it the same metric is how a number stops meaning anything.
    """
    if volumes is None or len(volumes) < lookback + 1:
        return None
    prior = volumes.iloc[-(lookback + 1):-1].dropna()
    if len(prior) < lookback:
        return None
    mean_prior = float(prior.mean())
    current = volumes.iloc[-1]
    if mean_prior <= 0 or current is None or pd.isna(current):
        return None
    return float(current) / mean_prior


def get_movers(universe: dict, recap_date: dt.date, prev_date: dt.date, n: int = 5):
    """prev_date: the actual previous trading session, from the Nifty index's own calendar -
    every mover's day-over-day % is only trusted if ITS previous close lands on that same date.
    yfinance can silently drop a day for a specific stock (verified 2026-09-18: ADANIGREEN's
    history skipped 17 Sep entirely, so close[-1]/close[-2] quietly became a 2-day change
    mislabeled as 1-day, +5.06% shown vs the real +4.73%) - without this check that kind of
    gap is invisible, so a stock whose dates don't line up is dropped rather than mismeasured.

    Separately, the bulk `yf.download()` endpoint can return the latest session's row with
    Open/High/Low/Volume populated but Close still NaN (verified 2026-09-22: effectively the
    whole NIFTY100 universe showed this for 21 Sep, about an hour after that session closed -
    Yahoo's official-close backfill for the batch endpoint evidently hadn't finished). That
    silently pushed every stock's "latest row" back to the prior session, so recap_date
    matched nothing and the run aborted with zero movers. `_clean()` correctly drops a
    row with no Close, but this signature (recap_date's row exists pre-dropna, just with a
    NaN Close) is a backfill lag, not missing data, so it gets a few retries with a wait
    before giving up - a plain re-request, not `retry()`, since the fetch itself didn't
    raise anything."""
    import yfinance as yf
    syms = list(universe)

    def _fetch():
        # 3mo rather than 1mo: relative volume needs 20 prior sessions plus the current one,
        # and a one-month window lands right on that boundary, so a single missing day would
        # silently drop the metric for the whole universe.
        return yf.download([s + ".NS" for s in syms], period="3mo", interval="1d", group_by="ticker",
                           auto_adjust=False, progress=False, threads=True)

    raw = retry(_fetch)
    rows, skipped_gap, pending_backfill = [], [], []
    for attempt in range(3):
        rows, skipped_gap, pending_backfill = [], [], []
        for s in syms:
            try:
                full = raw[s + ".NS"][["Open", "High", "Low", "Close", "Volume"]]
            except Exception:
                continue
            has_recap_row = recap_date in {ts.date() for ts in _normalize_index(full).index}
            try:
                d = _truncate_to_recap(_clean(full), recap_date)
            except Exception:
                continue
            if len(d) < 3 or d.index[-1].date() != recap_date:
                if has_recap_row:
                    pending_backfill.append(s)
                continue
            if d.index[-2].date() != prev_date:
                skipped_gap.append(s)
                continue
            c, v = d.Close, d.Volume
            rows.append({"symbol": s, "name": universe[s], "close": float(c.iloc[-1]),
                         "pct": float((c.iloc[-1] / c.iloc[-2] - 1) * 100),
                         "volx": relative_volume(v)})
        if len(rows) >= 2 * n or not pending_backfill or attempt == 2:
            break
        wait = 30 * (attempt + 1)
        print(f"[market] {len(pending_backfill)} stocks have a {recap_date} row with Close not yet "
              f"backfilled by Yahoo - retrying in {wait}s (attempt {attempt + 1}/3)")
        time.sleep(wait)
        raw = _fetch()
    if skipped_gap:
        print(f"[market] dropped {len(skipped_gap)} stocks with a data gap vs {prev_date}: {skipped_gap}")
    if len(rows) < 2 * n:
        raise RuntimeError(f"Only {len(rows)} stocks had clean data for {recap_date}")
    df = pd.DataFrame(rows).sort_values("pct", ascending=False)
    return df.head(n).to_dict("records"), df.tail(n).iloc[::-1].to_dict("records")


def _bulk_download_universe_ohlcv(symbols: list, period: str = "3mo") -> pd.DataFrame:
    """The one multi-ticker `yf.download()` call, wrapped in `retry()`.

    Extracted fresh for `get_universe_relative_volume` rather than factored out of
    `get_movers` - `get_movers`'s own inline fetch/retry loop is untouched, so this
    function carries zero risk to its already-verified NaN-Close-backfill/date-alignment
    behaviour.
    """
    import yfinance as yf

    def _fetch():
        return yf.download([s + ".NS" for s in symbols], period=period, interval="1d",
                           group_by="ticker", auto_adjust=False, progress=False, threads=True)
    return retry(_fetch)


def get_universe_relative_volume(universe: dict, recap_date: dt.date, prev_date: dt.date,
                                 period: str = "3mo"):
    """Relative volume (the SAME `relative_volume()` formula `get_movers` uses, never a
    second one) for every symbol in `universe` - not just the top gainers/losers.

    No ranking and no n-truncation: every symbol that produces a usable >=20-prior-session
    reading is returned. Reuses `get_movers`'s exact NaN-Close backfill-retry pattern and
    its exact `prev_date` alignment check (duplicated here rather than shared, since that
    loop in `get_movers` is intertwined with gainer/loser row-building and its
    `len(rows) >= 2*n` contract, which this function has no equivalent of - a wide universe
    legitimately returning far fewer usable rows than requested is not a failure here the
    way it is for `get_movers`'s guaranteed top-n).

    Returns (rows, skip_reasons):
      rows: [{"symbol": str, "name": str, "volx": float}, ...]
      skip_reasons: {symbol: "no_recap_row" | "backfill_pending" | "date_gap" |
                             "insufficient_relative_volume_history"}
    """
    syms = list(universe)
    raw = _bulk_download_universe_ohlcv(syms, period=period)

    rows, skip_reasons, pending_backfill = [], {}, []
    for attempt in range(3):
        rows, skip_reasons, pending_backfill = [], {}, []
        for s in syms:
            try:
                full = raw[s + ".NS"][["Open", "High", "Low", "Close", "Volume"]]
            except Exception:
                skip_reasons[s] = "no_recap_row"
                continue
            has_recap_row = recap_date in {ts.date() for ts in _normalize_index(full).index}
            try:
                d = _truncate_to_recap(_clean(full), recap_date)
            except Exception:
                skip_reasons[s] = "no_recap_row"
                continue
            if len(d) < 3 or d.index[-1].date() != recap_date:
                if has_recap_row:
                    pending_backfill.append(s)
                    skip_reasons[s] = "backfill_pending"
                else:
                    skip_reasons[s] = "no_recap_row"
                continue
            if d.index[-2].date() != prev_date:
                skip_reasons[s] = "date_gap"
                continue
            volx = relative_volume(d.Volume)
            if volx is None:
                skip_reasons[s] = "insufficient_relative_volume_history"
                continue
            rows.append({"symbol": s, "name": universe[s], "volx": volx})
        if not pending_backfill or attempt == 2:
            break
        wait = 30 * (attempt + 1)
        print(f"[market] {len(pending_backfill)} universe stocks have a {recap_date} row with "
              f"Close not yet backfilled by Yahoo - retrying in {wait}s "
              f"(attempt {attempt + 1}/3)")
        time.sleep(wait)
        raw = _bulk_download_universe_ohlcv(syms, period=period)
    return rows, skip_reasons


# Minimum valid sessions (prior 20 + current) for even the shortest technical-structure
# detector (the 20-session range break) to be computable at all. Below this the symbol is
# skipped entirely rather than producing a detector that silently runs over a shorter window.
MIN_TECHNICAL_SESSIONS = 21


def get_universe_technical_series(universe: dict, recap_date: dt.date, prev_date: dt.date,
                                  period: str = "1y"):
    """Per-symbol OHLC session history for the Market Intelligence Radar's technical-structure
    detector (Phase 4.2 Packet 2) - close/high/low across the whole bulk-fetched window, not
    just today's row.

    Reuses the SAME bulk fetch, NaN-Close backfill retry and `prev_date` alignment checks as
    `get_universe_relative_volume` - duplicated rather than shared, for the same reason
    `get_movers`/`get_universe_relative_volume` already duplicate this loop: each caller
    returns a different shape, and touching the shared loop risks the others' already-verified
    behaviour.

    Returns (series, skip_reasons):
      series: {symbol: [{"date": date, "open": float|None, "high": float, "low": float,
                         "close": float, "volume": float|None}, ...]}
              oldest first, ending at `recap_date` inclusive. A session with invalid OHLC
              (high < low, or high/low/close <= 0) is dropped from the series rather than
              discarding the whole symbol - real data, just not usable for that one row.
              `open`/`volume` are carried through (None if NaN) so callers needing volume too
              (Phase 4.2 Packet 5.3's shared OHLCV service, for RVOL) don't need a second
              fetch - the technical-structure windows below never read these two fields.
      skip_reasons: "no_recap_row" | "backfill_pending" | "date_gap" (same vocabulary as
                    `get_universe_relative_volume`) plus "insufficient_technical_history" when
                    fewer than `MIN_TECHNICAL_SESSIONS` valid sessions remain - too little even
                    for the 20-session detector, the shortest one this package implements.
    """
    syms = list(universe)
    raw = _bulk_download_universe_ohlcv(syms, period=period)

    series, skip_reasons, pending_backfill = {}, {}, []
    for attempt in range(3):
        series, skip_reasons, pending_backfill = {}, {}, []
        for s in syms:
            try:
                full = raw[s + ".NS"][["Open", "High", "Low", "Close", "Volume"]]
            except Exception:
                skip_reasons[s] = "no_recap_row"
                continue
            has_recap_row = recap_date in {ts.date() for ts in _normalize_index(full).index}
            try:
                d = _truncate_to_recap(_clean(full), recap_date)
            except Exception:
                skip_reasons[s] = "no_recap_row"
                continue
            if len(d) < 3 or d.index[-1].date() != recap_date:
                if has_recap_row:
                    pending_backfill.append(s)
                    skip_reasons[s] = "backfill_pending"
                else:
                    skip_reasons[s] = "no_recap_row"
                continue
            if d.index[-2].date() != prev_date:
                skip_reasons[s] = "date_gap"
                continue
            rows = [{"date": ts.date(),
                    "open": None if pd.isna(r.Open) else float(r.Open),
                    "high": float(r.High), "low": float(r.Low), "close": float(r.Close),
                    "volume": None if pd.isna(r.Volume) else float(r.Volume)}
                   for ts, r in d.iterrows()
                   if r.High >= r.Low and r.High > 0 and r.Low > 0 and r.Close > 0]
            if len(rows) < MIN_TECHNICAL_SESSIONS:
                skip_reasons[s] = "insufficient_technical_history"
                continue
            series[s] = rows
        if not pending_backfill or attempt == 2:
            break
        wait = 30 * (attempt + 1)
        print(f"[market] {len(pending_backfill)} universe stocks have a {recap_date} row with "
              f"Close not yet backfilled by Yahoo - retrying in {wait}s "
              f"(attempt {attempt + 1}/3)")
        time.sleep(wait)
        raw = _bulk_download_universe_ohlcv(syms, period=period)

    try:
        _write_through_ohlcv(raw, syms, recap_date)
    except Exception as exc:
        # Storage failure must never take down the acquisition it rides on (Phase 4.2
        # Packet 5.2, section 9) - `_write_through_ohlcv` already guards internally, this is
        # defense in depth so a future edit inside it can't regress that guarantee silently.
        print(f"[market] OHLCV write-through raised unexpectedly, ignoring: {exc}")
    return series, skip_reasons


def _write_through_ohlcv(raw, syms: list, recap_date: dt.date, source: str = "yahoo") -> None:
    """Persist every session this bulk fetch already downloaded for `syms` into the local
    OHLCV cache (`market_ohlcv.db`), using ONLY the bars `get_universe_technical_series`
    already has in memory - no second Yahoo call (Phase 4.2 Packet 5.2, section 10).

    Write-only from acquisition's point of view: nothing here is read back to influence
    `series`/`skip_reasons`, and this function is a pure log-and-return-on-failure - it must
    never raise and never change acquisition's returned values (section 9).
    """
    try:
        from config import OUT_DIR
        from storage.ohlcv_models import OHLCVBar, QualityStatus
        from storage.ohlcv_repository import OHLCVStore, default_db_path
    except Exception as exc:
        print(f"[market] OHLCV store unavailable, skipping write-through: {exc}")
        return

    retrieved_at = dt.datetime.now(dt.timezone.utc)
    bars = []
    for s in syms:
        try:
            full = raw[s + ".NS"][["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            # Yahoo returned nothing at all for this symbol in this fetch - an explicit
            # NO_DATA row records that this session/symbol was attempted, distinct from a
            # symbol this cache has simply never seen.
            bars.append(OHLCVBar(symbol=s, session_date=recap_date, open=None, high=None,
                                 low=None, close=None, volume=None, source=source,
                                 retrieved_at=retrieved_at, quality_status=QualityStatus.NO_DATA))
            continue
        for ts, r in _normalize_index(full).iterrows():
            close = None if pd.isna(r.Close) else float(r.Close)
            open_ = None if pd.isna(r.Open) else float(r.Open)
            high = None if pd.isna(r.High) else float(r.High)
            low = None if pd.isna(r.Low) else float(r.Low)
            volume = None if pd.isna(r.Volume) else float(r.Volume)
            if close is None:
                # Open/High/Low/Volume populated but Close still NaN: the known Yahoo
                # backfill-lag signature (see market.py module notes) - never a valid OK row.
                quality = QualityStatus.BACKFILL_PENDING
            elif high is not None and low is not None and high >= low and high > 0 and low > 0:
                quality = QualityStatus.OK
            else:
                continue  # not a usable or informative row - skip rather than guess a status
            bars.append(OHLCVBar(symbol=s, session_date=ts.date(), open=open_, high=high,
                                 low=low, close=close, volume=volume, source=source,
                                 retrieved_at=retrieved_at, quality_status=quality))

    try:
        store = OHLCVStore(default_db_path(OUT_DIR))
    except Exception as exc:
        print(f"[market] OHLCV store unavailable, skipping write-through: {exc}")
        return
    try:
        n = store.upsert_bars(bars)
        print(f"[market] OHLCV write-through: {n} bars persisted for {len(syms)} symbols")
    except Exception as exc:
        print(f"[market] OHLCV write-through failed: {exc}")
    finally:
        store.close()


# ----------------------------------------------------------------------------- analysis
def swing_points(high, low, w):
    hi, lo = [], []
    for i in range(w, len(high) - w):
        if high[i] == high[i - w:i + w + 1].max():
            hi.append(i)
        if low[i] == low[i - w:i + w + 1].min():
            lo.append(i)
    return hi, lo


def _cluster(prices, tol=0.006):
    out = []
    for p in sorted(prices):
        if out and abs(p - out[-1]["price"]) <= out[-1]["price"] * tol:
            out[-1]["pts"].append(p)
            out[-1]["price"] = float(np.mean(out[-1]["pts"]))
        else:
            out.append({"price": float(p), "pts": [p]})
    return out


def _spaced(levels, gap=0.008, k=2):
    picked = []
    for p in levels:
        if all(abs(p - q) / q > gap for q in picked):
            picked.append(p)
        if len(picked) == k:
            break
    return picked


def key_levels(cdf: pd.DataFrame) -> dict:
    H, L, C = cdf.High.values, cdf.Low.values, cdf.Close.values
    hi, lo = swing_points(H, L, 4)
    lv = [c["price"] for c in _cluster([H[i] for i in hi] + [L[i] for i in lo])]
    close = C[-1]
    res = _spaced(sorted(p for p in lv if p > close * 1.003))
    sup = _spaced(sorted((p for p in lv if p < close * 0.997), reverse=True))
    if not res and H.max() > close:
        res = [float(H.max())]
    if not sup and L.min() < close:
        sup = [float(L.min())]
    return {"res": res, "sup": sup}


def trendline(cdf: pd.DataFrame, lookback: int = 60):
    n = len(cdf)
    start = max(0, n - lookback)
    d = cdf.iloc[start:]
    H, L, C = d.High.values, d.Low.values, d.Close.values
    hi, lo = swing_points(H, L, 3)
    slope = np.polyfit(np.arange(len(C)), C, 1)[0]
    if slope > 0 and len(lo) >= 2:
        idx, y, kind = np.array(lo[-3:]), L[lo[-3:]], "support"
    elif slope < 0 and len(hi) >= 2:
        idx, y, kind = np.array(hi[-3:]), H[hi[-3:]], "resistance"
    else:
        return None
    m, b = np.polyfit(idx, y, 1)
    if kind == "support":
        if m <= 0:
            return None
        b -= max(0.0, float(np.max(m * idx + b - y)))      # keep line under the lows
    else:
        if m >= 0:
            return None
        b += max(0.0, float(np.max(y - (m * idx + b))))    # keep line over the highs
    x0, x1 = int(idx[0]), len(d) - 1
    return {"kind": kind, "x0": x0 + start, "y0": m * x0 + b, "x1": x1 + start,
            "y1": m * x1 + b, "slope": float(m), "now": float(m * x1 + b)}


def analyze(df: pd.DataFrame, bank_pct=None, vix=None) -> dict:
    df = df.copy()
    df["ema20"] = df.Close.ewm(span=20, adjust=False).mean()
    df["ema50"] = df.Close.ewm(span=50, adjust=False).mean()
    delta = df.Close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn)
    last, prev = df.iloc[-1], df.iloc[-2]
    cdf = df.tail(100)
    p = (last.High + last.Low + last.Close) / 3
    return {
        "chart_df": cdf,
        "recap_date": df.index[-1].date(),
        "prev_date": df.index[-2].date(),
        "open": float(last.Open), "high": float(last.High), "low": float(last.Low),
        "close": float(last.Close), "prev": float(prev.Close),
        "chg": float(last.Close - prev.Close), "pct": float((last.Close / prev.Close - 1) * 100),
        "ema20": float(last.ema20), "ema50": float(last.ema50), "rsi": float(last.rsi),
        "bank_pct": bank_pct, "vix": vix,
        "levels": key_levels(cdf), "trend": trendline(cdf),
        "pivot": {"P": float(p), "R1": float(2 * p - last.Low), "S1": float(2 * p - last.High)},
    }


# ----------------------------------------------------------------------------- NSE website (often blocks cloud IPs)
class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({**UA, "Accept": "application/json,text/plain,*/*",
                               "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"})
        self.warm = False

    def get(self, path):
        if not self.warm:
            self.s.get("https://www.nseindia.com/", timeout=10)
            self.warm = True
        r = self.s.get("https://www.nseindia.com" + path, timeout=12)
        r.raise_for_status()
        return r.json()


def _nse_date(txt):
    return dt.datetime.strptime(str(txt).strip()[:11], "%d-%b-%Y").date()


def _nse_timestamp(txt):
    """NSE's own 'as of' stamp, e.g. '18-Sep-2026 15:30:00', as an IST-aware datetime.

    This is the only market timestamp any source in this pipeline actually publishes, so it
    is kept rather than discarded: it becomes Observation.observed_at, which is what makes a
    freshness check on NSE data meaningful instead of a check on when we happened to fetch."""
    try:
        return dt.datetime.strptime(str(txt).strip()[:20], "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except Exception:
        return None


def nse_all_indices(nse: NSE, recap_date) -> dict:
    """{'NIFTY 50': {'last':..., 'pct':..., 'observed_at':...}, ...} only if NSE's timestamp
    is the recap session. Each row carries NSE's published timestamp so the acquisition layer
    does not have to reconstruct provenance it was given."""
    try:
        j = nse.get("/api/allIndices")
        stamp = j.get("timestamp", "")
        if _nse_date(stamp) != recap_date:
            print("[nse] allIndices timestamp is not the recap session, ignoring")
            return {}
        observed_at = _nse_timestamp(stamp)
        return {row["index"]: {"last": float(row["last"]), "pct": float(row["percentChange"]),
                               "observed_at": observed_at, "source_timestamp": str(stamp).strip()}
                for row in j.get("data", []) if row.get("index")}
    except Exception as e:
        print(f"[nse] allIndices unavailable: {e}")
        return {}


def fii_dii_nse(nse: NSE, recap_date):
    try:
        rows = nse.get("/api/fiidiiTradeReact")
        out = {}
        for r in rows:
            if _nse_date(r["date"]) != recap_date:
                continue
            val = float(str(r["netValue"]).replace(",", ""))
            out["fii" if "FII" in r["category"].upper() else "dii"] = val
        if "fii" in out and "dii" in out:
            return {**out, "source": "NSE"}
    except Exception as e:
        print(f"[nse] FII/DII unavailable: {e}")
    return None


# ----------------------------------------------------------------------------- sectors
SECTORS = [("Bank", "NIFTY BANK", "^NSEBANK"), ("IT", "NIFTY IT", "^CNXIT"),
           ("Auto", "NIFTY AUTO", "^CNXAUTO"), ("Pharma", "NIFTY PHARMA", "^CNXPHARMA"),
           ("FMCG", "NIFTY FMCG", "^CNXFMCG"), ("Metal", "NIFTY METAL", "^CNXMETAL"),
           ("Realty", "NIFTY REALTY", "^CNXREALTY"), ("Energy", "NIFTY ENERGY", "^CNXENERGY"),
           ("PSU Bank", "NIFTY PSU BANK", "^CNXPSUBANK"), ("Fin Serv", "NIFTY FINANCIAL SERVICES", "NIFTY_FIN_SERVICE.NS"),
           ("Media", "NIFTY MEDIA", "^CNXMEDIA"), ("Infra", "NIFTY INFRASTRUCTURE", "^CNXINFRA")]


def get_sectors(recap_date, prev_date, nse_idx: dict) -> list:
    """prev_date: same-calendar guard as get_movers() - a sector index with the same yfinance
    day-gap issue would otherwise silently report a multi-day move as if it were one day."""
    out = []
    for label, nse_name, yt in SECTORS:
        if nse_name in nse_idx:
            out.append({"name": label, "pct": nse_idx[nse_name]["pct"]})
            continue
        try:
            d = history(yt, "1mo")
            if len(d) < 2:
                # A confirmed real failure mode for some NSE sector-index tickers on Yahoo:
                # `.history()` returns exactly one row (a live/current spot quote) regardless
                # of period, never a daily-bar series - previously silent, so a real run could
                # lose most of its sectors with nothing in the logs to explain why.
                last = d.index[-1].date() if len(d) else None
                print(f"[market] sector {label} dropped: yfinance {yt} returned only "
                      f"{len(d)} daily bar(s) (last={last}), no usable day-over-day history")
            elif d.index[-1].date() == recap_date and d.index[-2].date() == prev_date:
                out.append({"name": label, "pct": float((d.Close.iloc[-1] / d.Close.iloc[-2] - 1) * 100)})
            elif d.index[-1].date() == recap_date:
                print(f"[market] sector {label} dropped: prev-close date {d.index[-2].date()} != {prev_date}")
            else:
                print(f"[market] sector {label} dropped: latest bar {d.index[-1].date()} "
                      f"!= recap session {recap_date}")
        except Exception as e:
            print(f"[market] sector {label} failed: {e}")
    return sorted(out, key=lambda x: -x["pct"])


# ----------------------------------------------------------------------------- global cues
# BRENT CRUDE deliberately excluded: yfinance's BZ=F ticker was verified (2026-09-18 session)
# to show -5.28% against an independently-reported -1.54% real move - a ~4x error, likely from
# tracking a thin/mismatched contract. It's sourced via news.ai_pass() (Gemini-verified,
# hidden if unconfirmed) instead of trusted blindly here. Re-verify before re-adding it to yfinance.
GLOBALS = [("DOW JONES", "^DJI", 0, ""), ("NASDAQ", "^IXIC", 0, ""),
           ("USD / INR", "INR=X", 2, ""), ("GOLD", "GC=F", 0, "$")]


def get_globals() -> list:
    import yfinance as yf
    out = []
    for label, t, dec, pre in GLOBALS:
        try:
            d = retry(lambda: yf.Ticker(t).history(period="7d", interval="1d")).dropna(subset=["Close"])
            last, prev = float(d.Close.iloc[-1]), float(d.Close.iloc[-2])
            out.append({"label": label, "value": last, "pct": (last / prev - 1) * 100, "dec": dec, "prefix": pre})
        except Exception as e:
            print(f"[market] global {label} failed: {e}")
    return out
