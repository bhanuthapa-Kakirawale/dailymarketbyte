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
    # Duplicates keep the LAST provider occurrence, then oldest-first order: every caller reads
    # `index[-1]`/`index[-2]` positionally, so an out-of-order provider frame must not reach it.
    return df[~df.index.duplicated(keep="last")].sort_index(kind="stable")


# A session's daily bar is treated as final only at/after this IST time on the session's own
# date (NSE's close is 15:30; the closing-price calculation settles by ~15:40). Single constant
# shared by `_clean`, the OHLCV write-through guard and `radar.cache_repair`.
SESSION_FINAL_TIME = dt.time(15, 40)


def session_in_progress(session_date: dt.date, now: dt.datetime | None = None) -> bool:
    """True while `session_date`'s bar can still change (its own date, before
    `SESSION_FINAL_TIME` IST)."""
    now = now or now_ist()
    return session_date == now.date() and now.time() < SESSION_FINAL_TIME


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = _normalize_index(df.dropna(subset=["Close"]))
    # never use a session that is still running
    if len(df) and session_in_progress(df.index[-1].date()):
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


class SessionAlignmentError(RuntimeError):
    """A SESSION-level alignment failure: the benchmark index's own series cannot establish the
    requested session or its canonical previous session, so no day-over-day figure for that
    session can be trusted. Distinct from a symbol-level problem (one stock's gap/placeholder),
    which only ever removes that symbol."""


def session_calendar(session_dates=None):
    """The canonical NSE session rule (`core.trading_calendar`), informed by the benchmark
    index's own bar dates when the caller has them (`get_market()`'s `m["session_dates"]`)."""
    from core.trading_calendar import SessionCalendar
    return SessionCalendar.from_index_dates(session_dates or ())


def _drop_non_sessions(d: pd.DataFrame, calendar, audit=None) -> pd.DataFrame:
    """Drop every row the canonical calendar calls NON_SESSION - a provider placeholder on an
    NSE holiday (verified: 2026-05-01/05-28/06-26/09-14, O=H=L=C = previous close, volume 0 on
    ~200 stocks). Must run BEFORE any positional `iloc[-2]`/`index[-2]` read: otherwise the
    placeholder IS the "previous session" and every real stock looks like it has a date gap.
    A missing canonical session is never filled - a gap stays a gap."""
    from core.trading_calendar import NON_SESSION, UNKNOWN
    if calendar is None or d.empty:
        return d
    status = [calendar.status(ts.date()) for ts in d.index]
    keep = [s != NON_SESSION for s in status]
    if audit is not None:
        dropped = [ts.date().isoformat() for ts, s in zip(d.index, status) if s == NON_SESSION]
        audit.non_session_rows_ignored += len(dropped)
        for iso in dropped:
            audit.non_session_dates[iso] = audit.non_session_dates.get(iso, 0) + 1
        if dropped:
            audit.symbols_with_non_session_rows += 1
        audit.unknown_date_rows_kept += sum(1 for s in status if s == UNKNOWN)
        if any(not calendar.covers(ts.date()) for ts in d.index):
            audit.calendar_covered = False
    return d[keep]


def _prepare_series(full: pd.DataFrame, recap_date: dt.date, calendar=None,
                    audit=None) -> pd.DataFrame:
    """Provider frame -> the session-aligned frame every day-over-day/window calculation reads:
    `_clean` (NaN-Close rows out, dates normalised, duplicates keep-last, in-progress session
    out) -> `_truncate_to_recap` (no look-ahead) -> `_drop_non_sessions` (no placeholder can
    become a "previous session"). The raw frame is untouched (OHLCV write-through keeps raw)."""
    d = _truncate_to_recap(_clean(full), recap_date)
    if audit is not None:
        audit.provider_rows_seen += len(full)
        audit.duplicate_rows_removed += int(
            pd.to_datetime(full.index).normalize().duplicated().sum()) if len(full) else 0
    d = _drop_non_sessions(d, calendar, audit)
    if audit is not None:
        audit.canonical_rows_used += len(d)
    return d


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


# ----------------------------------------------------------------------------- benchmark gap recovery
# Second source for an INDEX bar the primary (Yahoo) feed is missing on a real session.
# Verified 2026-09-25: every Yahoo Indian index feed lacks Tue 2026-09-22, a real NSE session, so
# 23 Sep's one-session Nifty change could not be established and the run stopped. NSE's own
# end-of-day index file (one CSV per session, every NSE index's OHLC + points change) carries it:
# Nifty 50 22-Sep O 23454.05 H 23489 L 23285.75 C 23329.0, change -85.3 - and 23329.0 + 85.3 is
# exactly Yahoo's 21-Sep close, which is what the continuity check below requires.
# Static archive files, not the cookie-gated /api/ endpoints the `NSE` class needs.
NSE_INDEX_ARCHIVE_URLS = (
    "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv",
    "https://archives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv",
)
INDEX_FALLBACK_SOURCE = "NSE_INDEX_CLOSE_ARCHIVE"
# Yahoo ticker -> NSE's index name (compared case-insensitively). Only these can be recovered.
INDEX_ARCHIVE_NAMES = {"^NSEI": "NIFTY 50", "^NSEBANK": "NIFTY BANK"}
# The implied previous close (close - NSE's own points change) must match the adjacent session's
# close we already hold within this fraction - a wrong-day or wrong-index row can't pass it.
RECOVERY_ANCHOR_TOLERANCE = 0.0005
# NSE's index-level circuit breaker halts the market at 20%: nothing beyond is a real session move.
RECOVERY_MAX_ABS_PCT = 20.0
# Most-recent-first cap on archive fetches per series; older gaps stay gaps (recorded, not fatal).
RECOVERY_MAX_FETCHES = 5

_ARCHIVE_CACHE: dict = {}


def nse_index_close_archive(session_date: dt.date) -> dict:
    """NSE's end-of-day file for every index on `session_date` -> `{"rows": {NAME: row},
    "url", "retrieved_at", "published_at"}`, or `{"error": ...}`. One fetch per date per process
    (a run recovering Nifty, Bank Nifty and sectors for the same gap reuses it). Never raises."""
    if session_date in _ARCHIVE_CACHE:
        return _ARCHIVE_CACHE[session_date]
    errors = []
    out = None
    for tmpl in NSE_INDEX_ARCHIVE_URLS:
        url = tmpl.format(ddmmyyyy=session_date.strftime("%d%m%Y"))
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            rows = {}
            for rec in df.to_dict("records"):
                name = str(rec.get("Index Name", "")).strip()
                if name:
                    rows[name.upper()] = rec
            if not rows:
                raise ValueError("no index rows")
            out = {"rows": rows, "url": url,
                   "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "published_at": r.headers.get("Last-Modified")}
            break
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:120]}")
    out = out or {"error": "; ".join(errors)}
    _ARCHIVE_CACHE[session_date] = out
    return out


def _archive_float(rec: dict, key: str):
    try:
        v = float(str(rec.get(key, "")).replace(",", "").strip())
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _recover_one(ticker: str, name: str, day: dt.date, anchor_date, anchor_close,
                 fetch) -> dict:
    """One gap -> a provenance record. `validation_status` is VALIDATED only when every check
    passes; the row is used only then."""
    rec = {"index": ticker, "archive_index_name": name, "session_date": day.isoformat(),
           "source": INDEX_FALLBACK_SOURCE, "primary_source": "yahoo",
           "fallback_reason": (f"PRIMARY_MISSING_CANONICAL_SESSION: the canonical NSE calendar "
                               f"has a session on {day} and the primary {ticker} series has no "
                               f"bar for it"),
           "anchor_session": anchor_date.isoformat() if anchor_date else None,
           "anchor_close": anchor_close}
    archive = fetch(day)
    rec["source_url"] = archive.get("url")
    rec["retrieval_time"] = archive.get("retrieved_at")
    rec["source_published_at"] = archive.get("published_at")
    if "error" in archive:
        rec["validation_status"] = "REJECTED_SOURCE_UNAVAILABLE"
        rec["detail"] = archive["error"]
        return rec
    row = archive["rows"].get(name.upper())
    if row is None:
        rec["validation_status"] = "REJECTED_INDEX_NOT_IN_SOURCE"
        return rec
    try:
        row_date = dt.datetime.strptime(str(row.get("Index Date", "")).strip(), "%d-%m-%Y").date()
    except ValueError:
        row_date = None
    o, h, l, c = (_archive_float(row, k) for k in ("Open Index Value", "High Index Value",
                                                    "Low Index Value", "Closing Index Value"))
    chg, chg_pct = _archive_float(row, "Points Change"), _archive_float(row, "Change(%)")
    rec["values"] = {"open": o, "high": h, "low": l, "close": c,
                     "points_change": chg, "change_pct": chg_pct}
    if row_date != day:
        rec["validation_status"] = "REJECTED_DATE_MISMATCH"
        rec["detail"] = f"source row is dated {row_date}"
        return rec
    if None in (o, h, l, c) or min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
        rec["validation_status"] = "REJECTED_INVALID_OHLC"
        return rec
    if chg is None or anchor_close is None:
        # Without an adjacent close to tie it to, a plausible wrong number would pass.
        rec["validation_status"] = "REJECTED_NO_CONTINUITY_ANCHOR"
        return rec
    implied_prev = c - chg
    anchor_diff = abs(implied_prev / anchor_close - 1)
    pct = (c / anchor_close - 1) * 100
    rec["checks"] = {"implied_previous_close": round(implied_prev, 4),
                     "anchor_relative_diff": round(anchor_diff, 7),
                     "anchor_tolerance": RECOVERY_ANCHOR_TOLERANCE,
                     "pct_vs_anchor": round(pct, 4),
                     "source_change_pct": chg_pct}
    if anchor_diff > RECOVERY_ANCHOR_TOLERANCE:
        rec["validation_status"] = "REJECTED_CONTINUITY_MISMATCH"
        return rec
    if abs(pct) >= RECOVERY_MAX_ABS_PCT or (chg_pct is not None and abs(chg_pct - pct) > 0.01):
        rec["validation_status"] = "REJECTED_CHANGE_INCONSISTENT"
        return rec
    rec["validation_status"] = "VALIDATED"
    return rec


def recover_index_gaps(d: pd.DataFrame, ticker: str, calendar=None, *, fetch=None,
                       name: str | None = None,
                       max_fetches: int = RECOVERY_MAX_FETCHES) -> tuple:
    """Fill a primary index series' missing CANONICAL sessions from NSE's end-of-day index file.

    Used only when all three hold: the canonical calendar (`core.trading_calendar`, built from
    NSE's holiday list) says the session existed, the primary series has no bar for it, and the
    fallback row passes validation. Validation: the source row's own date equals the session,
    the OHLC is internally consistent, and the row is anchored - its implied previous close
    (close - NSE's points change) matches the adjacent canonical session's close already held
    (primary, or an earlier recovered row) within `RECOVERY_ANCHOR_TOLERANCE`, and the stated %
    change agrees with it. Never infers a close from constituents, never interpolates, and never
    extends the series past the primary's own last bar (the recap session stays the primary's).
    Only gaps strictly inside the series' range are candidates; volume stays NaN (NSE's index
    volume is a different unit from Yahoo's). A rejected or unattempted gap stays a gap - the
    session-level check downstream then stops the run if that gap is the previous session.

    Returns `(frame, records)`; `records` is one provenance dict per gap (source, session_date,
    retrieval_time, validation_status, fallback_reason, ...), empty when nothing was missing.
    """
    from core.trading_calendar import as_date
    name = name or INDEX_ARCHIVE_NAMES.get(ticker)
    if d is None or d.empty or name is None:
        return d, []
    calendar = calendar or session_calendar([ts.date() for ts in d.index])
    have = {ts.date() for ts in d.index}
    first, last = min(have), max(have)
    gaps = [g for g in calendar.sessions_between(first, last)
            if g not in have and calendar.covers(g)]
    if not gaps:
        return d, []
    fetch = fetch or nse_index_close_archive
    closes = {ts.date(): float(v) for ts, v in d.Close.items()}
    records, new_rows = [], {}
    attempted = sorted(gaps)[-max_fetches:]
    for g in sorted(gaps):
        if g not in attempted:
            records.append({"index": ticker, "session_date": g.isoformat(),
                            "source": INDEX_FALLBACK_SOURCE,
                            "validation_status": "NOT_ATTEMPTED_FETCH_CAP",
                            "fallback_reason": "older than the most recent "
                                               f"{max_fetches} gaps", "retrieval_time": None})
            continue
        prev = calendar.previous_session(g)
        anchor = closes.get(prev) if prev else None
        rec = _recover_one(ticker, name, as_date(g), prev, anchor, fetch)
        records.append(rec)
        if rec["validation_status"] == "VALIDATED":
            v = rec["values"]
            closes[g] = v["close"]
            new_rows[pd.Timestamp(g)] = {"Open": v["open"], "High": v["high"], "Low": v["low"],
                                         "Close": v["close"], "Volume": np.nan}
    for rec in records:
        print(f"[market] {ticker} {rec['session_date']} missing from primary -> "
              f"{rec['source']}: {rec['validation_status']}")
    if not new_rows:
        return d, records
    add = pd.DataFrame.from_dict(new_rows, orient="index")
    add = add[[c for c in d.columns if c in add.columns]]
    out = pd.concat([d, add]).sort_index(kind="stable")
    return out, records


# Recap-session recovery (POST final edge-case patch). `recover_index_gaps` never extends a
# series past its own last bar, so when Yahoo lacks the RECAP session itself (verified: every
# Yahoo Indian index feed has no 2026-09-22 bar, so the 23 Sep 07:40 run could only see 21 Sep
# and skipped 22 Sep entirely) nothing could publish it. The same NSE end-of-day file, under the
# same validation, may now supply the latest completed canonical session(s) after the primary's
# last bar. Extra gates, all required: the calendar covers and calls the date a session; the
# session is FINAL (`SESSION_FINAL_TIME` passed on its own date, or an earlier date) - never an
# intraday value; the row anchors to the previous canonical close we already hold; chained
# oldest-first and stopping at the first failure. More than this many trailing sessions missing
# means the primary is broken, not lagging - nothing is filled and the run must stop.
RECAP_RECOVERY_MAX_SESSIONS = 2
RECAP_FALLBACK_ROLE = "RECAP_SESSION"


def recover_recap_session(d: pd.DataFrame, ticker: str, calendar=None, *, now=None, fetch=None,
                          name: str | None = None, until: dt.date | None = None,
                          max_sessions: int = RECAP_RECOVERY_MAX_SESSIONS) -> tuple:
    """Fill the primary's MISSING TRAILING canonical sessions - the recap day itself - from NSE's
    official end-of-day index file, only after the session is final.

    Candidates: canonical sessions after the primary's last bar up to `until` (default: today),
    each of which the calendar covers (NSE holiday list) and calls a SESSION, and whose close is
    FINAL. A session still in progress (`session_in_progress`) is not a candidate at all - it is
    not missing, its end-of-day value does not exist yet - so it is never fetched, never used and
    never recorded (a morning run would otherwise log today's session as "missing" every day). Each fetched row passes `_recover_one`'s full
    validation (source date = session, sane OHLC, implied previous close within
    `RECOVERY_ANCHOR_TOLERANCE` of the previous canonical close we hold, stated % agrees).
    Never interpolates, never infers from constituents, volume stays NaN.

    Returns `(frame, records)`; each record carries `role: RECAP_SESSION` plus the finality check.
    An unrecovered recap stays missing: the caller must then refuse to publish that session."""
    from core.trading_calendar import as_date
    name = name or INDEX_ARCHIVE_NAMES.get(ticker)
    if d is None or d.empty or name is None:
        return d, []
    now = now or now_ist()
    calendar = calendar or session_calendar([ts.date() for ts in d.index])
    last = max(ts.date() for ts in d.index)
    end = min(as_date(until), now.date()) if until else now.date()
    if end <= last:
        return d, []
    candidates = [s_ for s_ in calendar.sessions_between(last + dt.timedelta(days=1), end)
                  if calendar.covers(s_)]
    if not candidates:
        return d, []

    final_check = {"now_ist": now.isoformat(),
                   "session_final_time": SESSION_FINAL_TIME.isoformat(), "final": True}
    # never intraday: a session whose close is not final yet is not a candidate
    final = [c for c in candidates if not session_in_progress(c, now) and c <= now.date()]
    if not final:
        return d, []
    if len(final) > max_sessions:
        records = [{"index": ticker, "archive_index_name": name, "session_date": c.isoformat(),
                    "source": INDEX_FALLBACK_SOURCE, "primary_source": "yahoo",
                    "role": RECAP_FALLBACK_ROLE, "validation_status": "NOT_ATTEMPTED_PRIMARY_STALE",
                    "fallback_reason": f"primary {ticker} is missing {len(final)} trailing "
                                       f"sessions (limit {max_sessions}) - a broken feed, not a "
                                       "lag; nothing filled",
                    "retrieval_time": None, "session_final_check": dict(final_check)}
                   for c in final]
        for rec in records:
            print(f"[market] {ticker} {rec['session_date']} recap fallback: "
                  f"{rec['validation_status']}")
        return d, records
    fetch = fetch or nse_index_close_archive
    closes = {ts.date(): float(v) for ts, v in d.Close.items()}
    new_rows, out_recs = {}, []
    for day in final:
        prev = calendar.previous_session(day)
        rec = _recover_one(ticker, name, as_date(day), prev, closes.get(prev) if prev else None,
                           fetch)
        rec["role"] = RECAP_FALLBACK_ROLE
        rec["fallback_reason"] = (f"PRIMARY_MISSING_RECAP_SESSION: the canonical NSE calendar has "
                                  f"a completed session on {day} and the primary {ticker} series "
                                  f"ends at {last}")
        rec["session_final_check"] = dict(final_check)
        out_recs.append(rec)
        if rec["validation_status"] != "VALIDATED":
            break                               # a later session could not be anchored anyway
        v = rec["values"]
        closes[day] = v["close"]
        new_rows[pd.Timestamp(day)] = {"Open": v["open"], "High": v["high"], "Low": v["low"],
                                       "Close": v["close"], "Volume": np.nan}
    records = out_recs
    for rec in records:
        print(f"[market] {ticker} {rec['session_date']} recap session missing from primary -> "
              f"{rec['source']}: {rec['validation_status']}")
    if not new_rows:
        return d, records
    add = pd.DataFrame.from_dict(new_rows, orient="index")
    add = add[[c for c in d.columns if c in add.columns]]
    return pd.concat([d, add]).sort_index(kind="stable"), records


def check_index_session_alignment(index_dates, calendar=None, recovery=None) -> dict:
    """Session-level check on the benchmark index's own series: its last two bars must be the
    recap session and that session's CANONICAL previous session.

    Raises `SessionAlignmentError` when the index skips a canonical session between them -
    verified 2026-09-25: every Yahoo Indian index series (^NSEI, ^NSEBANK, ^CNXIT, ^BSESN,
    ^INDIAVIX) has no bar for Tue 2026-09-22, a real NSE session (not on NSE's holiday list;
    every stock traded). `analyze()` would then report 21 Sep -> 23 Sep, a two-session change,
    as the 23 Sep day's move. That is a wrong headline number, not a symbol problem, so it stops
    the run rather than degrading. A later run (after Yahoo backfills) or a second index source
    is the recovery, never a relabelled multi-session change.

    `recovery` (benchmark gap recovery, `recover_index_gaps`): the provenance records for any
    canonical session the primary lacked. `index_dates` then already include VALIDATED recovered
    sessions; a previous session supplied that way is reported as ALIGNED_WITH_FALLBACK, and a
    rejected fallback is named in the error so the stop is explained."""
    from core.trading_calendar import as_date
    recovery = list(recovery or [])
    dates = sorted({as_date(d) for d in index_dates})
    calendar = calendar or session_calendar(dates)
    recap = dates[-1]
    canonical_prev = calendar.previous_session(recap)
    index_prev = dates[-2] if len(dates) >= 2 else None
    info = {"recap_date": recap.isoformat(),
            "canonical_previous_session": canonical_prev.isoformat() if canonical_prev else None,
            "index_previous_bar": index_prev.isoformat() if index_prev else None,
            "calendar_covered": calendar.covers(recap),
            "benchmark_missing_sessions": [d.isoformat() for d in calendar.benchmark_gaps()],
            "benchmark_bars_on_listed_holidays":
                [d.isoformat() for d in calendar.benchmark_bars_on_listed_holidays()],
            "benchmark_recovered_sessions": [r["session_date"] for r in recovery
                                             if r.get("validation_status") == "VALIDATED"],
            "benchmark_recovery": recovery}
    if canonical_prev is None:
        info["status"] = "PREVIOUS_SESSION_UNKNOWN"
        raise SessionAlignmentError(f"cannot establish the canonical previous session for "
                                    f"{recap}: {info}")
    if index_prev != canonical_prev:
        info["status"] = "BENCHMARK_MISSING_PREVIOUS_SESSION"
        tried = [r for r in recovery if r.get("session_date") == canonical_prev.isoformat()]
        fallback = (f"; fallback {tried[0].get('source')}: {tried[0].get('validation_status')}"
                    if tried else "; no fallback attempted")
        raise SessionAlignmentError(
            f"benchmark index has no bar for canonical session {canonical_prev} (its previous "
            f"bar is {index_prev}){fallback}; the {recap} one-session index change cannot be "
            f"established - not publishing a multi-session change as a daily move")
    recovered = set(info["benchmark_recovered_sessions"])
    info["status"] = ("ALIGNED_WITH_FALLBACK"
                      if {canonical_prev.isoformat(), recap.isoformat()} & recovered
                      else "ALIGNED")
    info["previous_close_source"] = (INDEX_FALLBACK_SOURCE
                                     if canonical_prev.isoformat() in recovered else "yahoo")
    # The recap session's own close: primary, or NSE's official EOD file (recap-session recovery).
    info["recap_close_source"] = (INDEX_FALLBACK_SOURCE if recap.isoformat() in recovered
                                  else "yahoo")
    return info


def get_market() -> dict:
    nifty = _nifty_history_with_backfill_check("1y")
    if len(nifty) < 60:
        raise RuntimeError("Not enough Nifty history returned")
    calendar = session_calendar([ts.date() for ts in nifty.index])
    nifty = _drop_non_sessions(nifty, calendar)
    # A real session the primary lacks -> NSE's own end-of-day index file, validated, with
    # provenance (never interpolated, never inferred from stocks). Unrecovered = still a gap.
    nifty, recovery = recover_index_gaps(nifty, "^NSEI", calendar)
    # The recap session itself missing from the primary -> the same official EOD file, only once
    # that session is final. Unrecovered = the benchmark still ends earlier; `main.collect`
    # then refuses to publish a real session it cannot establish.
    nifty, recap_recovery = recover_recap_session(nifty, "^NSEI", calendar)
    alignment = check_index_session_alignment([ts.date() for ts in nifty.index], calendar,
                                              recovery=recovery + recap_recovery)
    recap = nifty.index[-1].date()
    bank_pct = vix = None
    bank_close_source = "yahoo"
    try:
        b = _drop_non_sessions(history("^NSEBANK", "1mo"), calendar)
        b, bank_recovery = recover_index_gaps(b, "^NSEBANK", calendar)
        b, bank_recap = recover_recap_session(b, "^NSEBANK", calendar, until=recap)
        alignment["index_recovery"] = bank_recovery + bank_recap
        if b.index[-1] == nifty.index[-1] and b.index[-2] == nifty.index[-2]:
            bank_pct = float((b.Close.iloc[-1] / b.Close.iloc[-2] - 1) * 100)
            if any(r["validation_status"] == "VALIDATED" and r["session_date"] == recap.isoformat()
                   for r in bank_recap):
                bank_close_source = INDEX_FALLBACK_SOURCE
    except Exception as e:
        print(f"[market] Bank Nifty failed: {e}")
    try:
        # Dated like every other reading: a VIX bar from an earlier session is not this one's.
        v = _drop_non_sessions(history("^INDIAVIX", "1mo"), calendar)
        if v.index[-1].date() == recap:
            vix = float(v.Close.iloc[-1])
        else:
            print(f"[market] VIX dropped: latest bar {v.index[-1].date()} != recap {recap}")
    except Exception as e:
        print(f"[market] VIX failed: {e}")
    m = analyze(nifty, bank_pct, vix)
    m["session_dates"] = tuple(ts.date() for ts in nifty.index)
    m["session_alignment"] = alignment
    m["prev_source"] = alignment["previous_close_source"]
    m["close_source"] = alignment["recap_close_source"]
    m["bank_close_source"] = bank_close_source
    return m


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


def get_movers(universe: dict, recap_date: dt.date, prev_date: dt.date, n: int = 5,
               session_dates=None):
    """Top-n gainers and losers. See `get_movers_audited` for the rules; this keeps the
    original two-value return for existing callers."""
    gainers, losers, _ = get_movers_audited(universe, recap_date, prev_date, n,
                                            session_dates=session_dates)
    return gainers, losers


def get_movers_audited(universe: dict, recap_date: dt.date, prev_date: dt.date, n: int = 5,
                       session_dates=None):
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
    raise anything.

    POST freeze: returns `(gainers, losers, audit)`. Every clean row goes through
    `core.move_guard.validate_move`; only publishable moves are ranked, and a held move stays in
    `audit["excluded"]` with its raw values (never deleted). `audit` also records the universe
    coverage the ranking rests on - universe_expected, universe_observed (clean, date-aligned
    rows), universe_validated (those that passed the guard) and coverage_pct =
    validated / expected - so the editorial layer can refuse to publish a ranking built on a
    partial universe.

    Session alignment (data-reliability patch): every symbol's frame goes through
    `_prepare_series`, which drops provider rows on dates the canonical NSE calendar calls
    non-sessions BEFORE the `[-2] == prev_date` check - a holiday placeholder can never be a
    previous close, and can never make a real stock look date-gapped (2026-09-15 aborted with
    ~0 clean rows because ~198 stocks carried a 2026-09-14 placeholder). `session_dates` is the
    benchmark index's own bar dates (`m["session_dates"]`), extra session evidence on top of
    NSE's holiday list. The move guard runs AFTER alignment, on aligned closes. A symbol that
    raises while its row is built is excluded (`audit["symbols_excluded"]`), never fatal."""
    import yfinance as yf
    from core.move_guard import validate_move
    from core.trading_calendar import AlignmentAudit
    syms = list(universe)
    calendar = session_calendar(session_dates)

    def _fetch():
        # 3mo rather than 1mo: relative volume needs 20 prior sessions plus the current one,
        # and a one-month window lands right on that boundary, so a single missing day would
        # silently drop the metric for the whole universe.
        return yf.download([s + ".NS" for s in syms], period="3mo", interval="1d", group_by="ticker",
                           auto_adjust=False, progress=False, threads=True)

    raw = retry(_fetch)
    rows, skipped_gap, pending_backfill, malformed = [], [], [], []
    align = AlignmentAudit()
    for attempt in range(3):
        rows, skipped_gap, pending_backfill, malformed = [], [], [], []
        align = AlignmentAudit()
        for s in syms:
            try:
                full = raw[s + ".NS"][["Open", "High", "Low", "Close", "Volume"]]
            except Exception:
                continue
            try:
                has_recap_row = recap_date in {ts.date() for ts in _normalize_index(full).index}
                d = _prepare_series(full, recap_date, calendar, align)
            except Exception:
                malformed.append(s)
                continue
            if len(d) < 3 or d.index[-1].date() != recap_date:
                if has_recap_row:
                    pending_backfill.append(s)
                continue
            if d.index[-2].date() != prev_date:
                skipped_gap.append(s)
                continue
            try:
                c, v = d.Close, d.Volume
                last = d.iloc[-1]
                rows.append({"symbol": s, "name": universe[s], "close": float(c.iloc[-1]),
                             "pct": float((c.iloc[-1] / c.iloc[-2] - 1) * 100),
                             "volx": relative_volume(v),
                             "open": float(last.Open), "high": float(last.High),
                             "low": float(last.Low), "prev_close": float(c.iloc[-2]),
                             "prev_date": d.index[-2].date().isoformat()})
            except Exception:
                malformed.append(s)
        if len(rows) >= 2 * n or not pending_backfill or attempt == 2:
            break
        wait = 30 * (attempt + 1)
        print(f"[market] {len(pending_backfill)} stocks have a {recap_date} row with Close not yet "
              f"backfilled by Yahoo - retrying in {wait}s (attempt {attempt + 1}/3)")
        time.sleep(wait)
        raw = _fetch()
    if align.non_session_rows_ignored:
        print(f"[market] session alignment ignored {align.non_session_rows_ignored} provider "
              f"row(s) on non-session dates {align.non_session_dates}")
    if skipped_gap:
        print(f"[market] dropped {len(skipped_gap)} stocks with a data gap vs {prev_date}: {skipped_gap}")
    if malformed:
        print(f"[market] excluded {len(malformed)} malformed stock series: {malformed}")
    if len(rows) < 2 * n:
        raise RuntimeError(f"Only {len(rows)} stocks had clean data for {recap_date}")
    valid, excluded = [], []
    for r in rows:
        verdict = validate_move(close=r["close"], prev_close=r["prev_close"], open_=r["open"],
                                high=r["high"], low=r["low"], change_pct=r["pct"],
                                relative_volume=r["volx"], prev_date=r["prev_date"],
                                expected_prev_date=prev_date).to_dict()
        r["validation"] = verdict
        (valid if verdict["publishable"] else excluded).append(r)
    if excluded:
        print(f"[market] move guard held {len(excluded)} stocks: "
              f"{[(r['symbol'], round(r['pct'], 2), r['validation']['status']) for r in excluded]}")
    expected = len(syms)
    audit = {"universe_expected": expected, "universe_observed": len(rows),
             "universe_validated": len(valid),
             "coverage_pct": round(100.0 * len(valid) / expected, 2) if expected else 0.0,
             "missing": sorted(set(syms) - {r["symbol"] for r in rows}),
             "skipped_date_gap": sorted(skipped_gap),
             "excluded": [dict(r) for r in excluded],
             "session_date": recap_date.isoformat(), "previous_session_date": prev_date.isoformat(),
             "coverage_basis": "validated / expected",
             "symbols_excluded": {"date_gap": sorted(skipped_gap), "malformed": sorted(malformed),
                                  "backfill_pending": sorted(pending_backfill)},
             "session_alignment": {**align.to_dict(),
                                   "status": _alignment_status(align, skipped_gap, malformed)}}
    if not valid:
        return [], [], audit
    df = pd.DataFrame(valid).sort_values("pct", ascending=False)
    return df.head(n).to_dict("records"), df.tail(n).iloc[::-1].to_dict("records"), audit


def _alignment_status(align, skipped_gap, malformed) -> str:
    if not align.calendar_covered:
        return "ALIGNED_INDEX_SPINE_FALLBACK"
    if align.non_session_rows_ignored or skipped_gap or malformed:
        return "ALIGNED_WITH_EXCLUSIONS"
    return "ALIGNED"


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
                                 period: str = "3mo", session_dates=None, audit=None):
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
    calendar = session_calendar(session_dates)
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
                # Non-session provider rows out BEFORE the [-2] == prev_date check and before
                # any window (RVOL's prior 20, SMA/range) counts sessions.
                d = _prepare_series(full, recap_date, calendar,
                                    audit if attempt == 0 else None)
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
                                  period: str = "1y", session_dates=None, audit=None):
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
    calendar = session_calendar(session_dates)
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
                # Non-session provider rows out BEFORE the [-2] == prev_date check and before
                # any window (RVOL's prior 20, SMA/range) counts sessions.
                d = _prepare_series(full, recap_date, calendar,
                                    audit if attempt == 0 else None)
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
    # `_clean`'s rule, applied to the cache too: a session still trading is never persisted as a
    # finished OK bar. Verified 2026-09-25: a 11:44 IST run stored 200 intraday 25 Sep bars as
    # OK (RELIANCE 1221.0 / 6.4M vs the real close 1226.0 / 13.1M), which the next run's
    # warm-cache read would have treated as that session's final closes.
    now = now_ist()
    in_progress = now.date() if session_in_progress(now.date(), now) else None
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
            if ts.date() == in_progress:
                continue
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
# Sector indices can be gap-recovered from the same NSE end-of-day file as the benchmark.
INDEX_ARCHIVE_NAMES.update({yt: nse_name for _label, nse_name, yt in SECTORS})


def get_sectors(recap_date, prev_date, nse_idx: dict, session_dates=None,
                recovery_log: list | None = None) -> list:
    """prev_date: same-calendar guard as get_movers() - a sector index with the same yfinance
    day-gap issue would otherwise silently report a multi-day move as if it were one day.
    Rows on canonical non-session dates are dropped before that check (session alignment).
    A canonical session the Yahoo sector series lacks is recovered from NSE's end-of-day index
    file (`recover_index_gaps`); its provenance records are appended to `recovery_log`, and a
    sector whose previous close came from it says so in `prev_close_source`."""
    calendar = session_calendar(session_dates)
    out = []
    for label, nse_name, yt in SECTORS:
        if nse_name in nse_idx:
            out.append({"name": label, "pct": nse_idx[nse_name]["pct"]})
            continue
        try:
            d = _drop_non_sessions(history(yt, "1mo"), calendar)
            d, recovered = recover_index_gaps(d, yt, calendar)
            d, recap_rec = recover_recap_session(d, yt, calendar, until=recap_date)
            recovered = recovered + recap_rec
            if recovery_log is not None:
                recovery_log.extend(recovered)
            fallback_prev = any(r["validation_status"] == "VALIDATED"
                                and r["session_date"] == prev_date.isoformat() for r in recovered)
            if len(d) < 2:
                # A confirmed real failure mode for some NSE sector-index tickers on Yahoo:
                # `.history()` returns exactly one row (a live/current spot quote) regardless
                # of period, never a daily-bar series - previously silent, so a real run could
                # lose most of its sectors with nothing in the logs to explain why.
                last = d.index[-1].date() if len(d) else None
                print(f"[market] sector {label} dropped: yfinance {yt} returned only "
                      f"{len(d)} daily bar(s) (last={last}), no usable day-over-day history")
            elif d.index[-1].date() == recap_date and d.index[-2].date() == prev_date:
                row = {"name": label, "pct": float((d.Close.iloc[-1] / d.Close.iloc[-2] - 1) * 100)}
                if fallback_prev:
                    row["prev_close_source"] = INDEX_FALLBACK_SOURCE
                if any(r["validation_status"] == "VALIDATED" and r["session_date"] ==
                       recap_date.isoformat() for r in recap_rec):
                    row["close_source"] = INDEX_FALLBACK_SOURCE
                out.append(row)
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
