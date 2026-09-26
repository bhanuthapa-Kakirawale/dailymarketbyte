"""Reachability diagnostic for the external sources the scheduled jobs depend on.

Built to run on a GitHub Actions runner (cloud IPs are often blocked by NSE's websites) as well
as locally. It answers ONE question per source - can this machine reach it and read what comes
back - and never feeds a value into a report or a video:

    NSEIX_MARKET_RATE     GIFT Nifty live quote        (PRE, optional)
    NSEIX_SETTLEMENT      GIFT Nifty settlement file   (PRE, optional)
    NSE_INDEX_ARCHIVE     NSE end-of-day index file    (REPORT, benchmark gap fallback)
    YAHOO_*               yfinance daily / 5-minute bars (REPORT + PRE, required)

Statuses: REACHABLE, BLOCKED (HTTP 401/403/429 or the connection refused/reset), TIMEOUT,
PARSE_ERROR (answered, but not in the shape the pipeline parses), HTTP_ERROR (any other status),
NO_DATA (answered with nothing usable). Fails safe: every probe is wrapped, one failure never
stops the others, and the run always produces a JSON result. No request carries a credential and
nothing from the environment except non-secret runner facts is written out.

Publication never depends on this diagnostic, and GIFT reachability never gates PRE.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import time

REACHABLE, BLOCKED, TIMEOUT, PARSE_ERROR = "REACHABLE", "BLOCKED", "TIMEOUT", "PARSE_ERROR"
HTTP_ERROR, NO_DATA = "HTTP_ERROR", "NO_DATA"
DIAGNOSTIC_VERSION = "connectivity-1.0"
_BLOCKED_CODES = {401, 403, 407, 429, 451}
REQUIRED = {"YAHOO_NIFTY_DAILY", "YAHOO_INDIAVIX_DAILY", "YAHOO_US_DAILY", "YAHOO_ASIA_5M"}


def _http_get(url: str, timeout: float = 15.0) -> tuple:
    import requests
    from providers.gift_nifty import HEADERS
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    return r.status_code, r.text


def _classify_exception(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timed out" in text:
        return TIMEOUT
    if any(k in name for k in ("connection", "ssl", "proxy")) or "refused" in text or "reset" in text:
        return BLOCKED
    return HTTP_ERROR


def _probe(name: str, url: str, fn, required: bool) -> dict:
    """Run one probe. `fn()` returns (status, detail); any exception is classified here."""
    t0 = time.time()
    try:
        status, detail = fn()
    except Exception as exc:
        status, detail = _classify_exception(exc), f"{type(exc).__name__}: {str(exc)[:160]}"
    return {"check": name, "url": url, "status": status, "detail": detail,
            "required": required, "elapsed_ms": round((time.time() - t0) * 1000)}


NOT_ATTEMPTED = "NOT_ATTEMPTED"     # the request was never made (not a failure of the source)


def classify_exception(exc: BaseException) -> str:
    """Connectivity verdict for an exception raised while READING a source: an HTTP status the
    server sent (BLOCKED / HTTP_ERROR), a timeout, a refused/reset connection (BLOCKED) - or
    REACHABLE when the server answered but the body could not be decoded (a parse problem, not
    a connectivity one)."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return _http_status(code) or REACHABLE
    if isinstance(exc, ValueError):                 # json / payload decode: the source answered
        return REACHABLE
    return _classify_exception(exc)


def _http_status(code: int) -> str | None:
    if code == 200:
        return None
    return BLOCKED if code in _BLOCKED_CODES else HTTP_ERROR


def check_nseix_market_rate(get) -> tuple:
    from providers.gift_nifty import MARKET_RATE_URL, parse_market_rate
    code, text = get(MARKET_RATE_URL)
    if _http_status(code):
        return _http_status(code), f"HTTP {code}"
    try:
        contracts = parse_market_rate(text)
    except Exception as exc:
        return PARSE_ERROR, f"{type(exc).__name__}: {str(exc)[:120]}"
    if not contracts:
        return NO_DATA, "JSON parsed, no NIFTY FUTIDX rows"
    latest = max((c.timestamp for c in contracts if c.timestamp), default=None)
    return REACHABLE, (f"{len(contracts)} NIFTY future contract(s); latest trade "
                       f"{latest.isoformat() if latest else 'n/a'}")


def check_nseix_settlement(get, today: dt.date) -> tuple:
    from providers.gift_nifty import DSP_URL, parse_settlement_file
    tried = []
    for back in range(1, 8):
        day = today - dt.timedelta(days=back)
        if day.weekday() >= 5:
            continue
        code, text = get(DSP_URL.format(d=day))
        tried.append(f"{day}:{code}")
        if code in _BLOCKED_CODES:
            return BLOCKED, f"HTTP {code} for {day}"
        if code != 200:
            continue
        try:
            prices = parse_settlement_file(text, day)
        except Exception as exc:
            return PARSE_ERROR, f"{day}: {type(exc).__name__}: {str(exc)[:120]}"
        return REACHABLE, f"settlement file {day} parsed ({len(prices)} NIFTY expiries)"
    return HTTP_ERROR, "no settlement file in the last 7 weekdays (" + ", ".join(tried) + ")"


def check_nse_archive(get, session: dt.date) -> tuple:
    from market import NSE_INDEX_ARCHIVE_URLS
    url = NSE_INDEX_ARCHIVE_URLS[0].format(ddmmyyyy=session.strftime("%d%m%Y"))
    code, text = get(url)
    if _http_status(code):
        return _http_status(code), f"HTTP {code} for {session}"
    head = (text or "").splitlines()[:1]
    if not head or "Index Name" not in head[0]:
        return PARSE_ERROR, "response is not the ind_close_all CSV"
    rows = [ln for ln in text.splitlines()[1:] if ln.strip()]
    has_nifty = any(ln.upper().startswith("NIFTY 50,") for ln in rows)
    if not has_nifty:
        return NO_DATA, f"{len(rows)} rows, no NIFTY 50 row"
    return REACHABLE, f"{session}: {len(rows)} index rows incl. NIFTY 50"


def check_yahoo(history_fn, ticker: str, interval: str, today: dt.date) -> tuple:
    days = 10 if interval == "1d" else 4
    df = history_fn(ticker, today - dt.timedelta(days=days), today + dt.timedelta(days=1), interval)
    if df is None or len(df) == 0:
        return NO_DATA, "no rows returned (yfinance returns empty on a blocked/failed request)"
    if "Close" not in getattr(df, "columns", []):
        return PARSE_ERROR, "no Close column"
    return REACHABLE, f"{len(df)} {interval} bar(s), last {df.index[-1]}"


def run_diagnostic(get=None, history_fn=None, now: dt.datetime | None = None,
                   calendar=None) -> dict:
    """All probes -> one JSON-able result. `get(url) -> (status_code, text)` and
    `history_fn(ticker, start, end, interval) -> DataFrame` are the injectable network seams."""
    from config import IST
    from core.trading_calendar import SessionCalendar
    from operations.sessions import latest_final_session
    get = get or _http_get
    if history_fn is None:
        from providers.premarket import yahoo_history as history_fn
    now = now or dt.datetime.now(IST)
    today = now.astimezone(IST).date() if now.tzinfo else now.date()
    cal = calendar or SessionCalendar()
    session = latest_final_session(now, cal) or cal.previous_session(today) or today
    from providers.gift_nifty import DSP_URL, MARKET_RATE_URL
    from market import NSE_INDEX_ARCHIVE_URLS
    checks = [
        _probe("NSEIX_MARKET_RATE", MARKET_RATE_URL, lambda: check_nseix_market_rate(get), False),
        _probe("NSEIX_SETTLEMENT", DSP_URL.replace("{d:%d%m%Y}", "<DDMMYYYY>"),
               lambda: check_nseix_settlement(get, today), False),
        _probe("NSE_INDEX_ARCHIVE", NSE_INDEX_ARCHIVE_URLS[0].replace("{ddmmyyyy}", "<DDMMYYYY>"),
               lambda: check_nse_archive(get, session), False),
    ]
    for name, ticker, interval in (("YAHOO_NIFTY_DAILY", "^NSEI", "1d"),
                                   ("YAHOO_INDIAVIX_DAILY", "^INDIAVIX", "1d"),
                                   ("YAHOO_STOCK_DAILY", "RELIANCE.NS", "1d"),
                                   ("YAHOO_US_DAILY", "^GSPC", "1d"),
                                   ("YAHOO_ASIA_5M", "^N225", "5m")):
        checks.append(_probe(name, f"yfinance:{ticker}:{interval}",
                             lambda t=ticker, i=interval: check_yahoo(history_fn, t, i, today),
                             name in REQUIRED))
    required_ok = all(c["status"] == REACHABLE for c in checks if c["required"])
    gift_ok = all(c["status"] == REACHABLE for c in checks if c["check"].startswith("NSEIX"))
    return {
        "version": DIAGNOSTIC_VERSION, "checked_at": now.isoformat(),
        "reference_session": session.isoformat(),
        "runner": {"github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
                   "runner_os": os.environ.get("RUNNER_OS"), "platform": platform.platform()},
        "checks": checks,
        "summary": {"required_sources_reachable": required_ok,
                    "gift_sources_reachable": gift_ok,
                    "nse_archive_reachable": next(c["status"] for c in checks
                                                  if c["check"] == "NSE_INDEX_ARCHIVE") == REACHABLE,
                    "by_status": {s: sum(1 for c in checks if c["status"] == s)
                                  for s in sorted({c["status"] for c in checks})}},
        "policy": "diagnostic only - never gates publication; GIFT unreachable = PRE DEGRADED",
    }


def write_diagnostic(result: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False, default=str)
    return path


__all__ = ["run_diagnostic", "write_diagnostic", "classify_exception", "REACHABLE", "BLOCKED",
           "TIMEOUT", "PARSE_ERROR", "HTTP_ERROR", "NO_DATA", "NOT_ATTEMPTED",
           "DIAGNOSTIC_VERSION"]
