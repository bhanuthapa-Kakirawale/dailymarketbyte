"""Pre-open acquisition for the PRE-MARKET Short (V1): overnight global cues, India VIX's
previous-session change, and the GIFT Nifty seam.

Everything here is RECORDED AT ACQUISITION, never reconstructed downstream: every reading
carries its source, the moment it was retrieved, the market date/timestamp it describes and a
freshness verdict (`core.freshness`). Above this module nothing sees a provider frame.

Rules that matter:
- No LLM is a data source. Brent (verified-wrong Yahoo ticker, Gemini-only otherwise) is NOT
  acquired. GIFT Nifty goes through the `gift_fn` seam: the product runner wires the exchange
  itself (`providers.gift_nifty`, NSE IX); the offline default returns None -> GIFT omitted.
- US indices are SESSION_CLOSE cues: the close of the US session that ended overnight (bar
  date == the expected session, bar final at the cutoff). Asian indices are LIVE cues: the
  last completed 5-minute bar at or before the cutoff, compared with that market's previous
  close; a market that has not opened (holiday) has no fresh reading and is omitted.
- Day-over-day changes compare DATED bars - never "the previous row" without looking at its
  date (the CLAUDE.md rule for every day-over-day calculation).

The pure functions (`us_close_quote`, `live_quote`, `vix_reading`) take frames, so tests run
offline; `fetch_premarket_quotes` wires them to Yahoo through an injectable `history_fn`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from core.freshness import (IST, FreshnessStatus, QuoteKind, live_freshness,
                            session_freshness, to_ist, us_close_freshness)
from core.sources import GROUP_YAHOO, SRC_YAHOO

PREMARKET_PROVIDER_VERSION = "pre-acq-1.0"

# key, on-screen name, Yahoo ticker, region. No commodity/FX in V1: Brent's Yahoo ticker is
# verified wrong (CLAUDE.md), and 24-hour instruments have no unambiguous "previous close"
# for a pre-open snapshot - both are documented as V1 omissions, not guessed.
GLOBAL_UNIVERSE = (
    ("SP500", "S&P 500", "^GSPC", "US"),
    ("NASDAQ", "NASDAQ", "^IXIC", "US"),
    ("DOW", "DOW JONES", "^DJI", "US"),
    ("NIKKEI", "NIKKEI 225", "^N225", "ASIA"),
    ("HANGSENG", "HANG SENG", "^HSI", "ASIA"),
)
INTRADAY_INTERVAL = dt.timedelta(minutes=5)
MAX_PREV_GAP_DAYS = 6          # a "previous close" further back than this is not a previous session
VIX_CROSSCHECK_PCT = 1.0       # provider VIX vs the canonical report's VIX fact
# A reading whose own validation failed is never shown, however fresh its timestamp.
UNUSABLE_VALIDATION = frozenset({"CONFLICT", "REJECTED", "UNAVAILABLE"})


@dataclass(frozen=True)
class PreMarketQuote:
    """One pre-open reading, with everything needed to judge and audit it."""
    key: str
    name: str
    region: str                         # US / ASIA / GIFT / INDIA
    kind: QuoteKind
    value: float | None
    change_pct: float | None
    reference_value: float | None       # the close the change is measured from
    reference_date: dt.date | None
    market_date: dt.date | None         # the session the value belongs to
    market_timestamp: dt.datetime | None  # IST, when the value was true (LIVE readings)
    retrieved_at: dt.datetime
    freshness: FreshnessStatus
    freshness_reason: str
    source: str = SRC_YAHOO
    source_type: str = "SECONDARY"
    independence_group: str = GROUP_YAHOO
    validation_status: str = "SINGLE_SOURCE"
    ticker: str = ""
    reference_label: str = ""           # what the change is "vs" (GIFT: "its previous settlement")
    validation_reason: str = ""         # why validation_status is what it is (CONFLICT/REJECTED)
    source_reference: str = ""          # the URL/file the value was read from
    provenance: dict = field(default_factory=dict)   # acquisition audit (GIFT: contract, DSP file)

    @property
    def fresh(self) -> bool:
        return (self.freshness is FreshnessStatus.FRESH and self.change_pct is not None
                and self.validation_status not in UNUSABLE_VALIDATION)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "region": self.region, "kind": self.kind.value,
            "value": self.value, "change_pct": self.change_pct,
            "reference_value": self.reference_value,
            "reference_date": self.reference_date.isoformat() if self.reference_date else None,
            "market_date": self.market_date.isoformat() if self.market_date else None,
            "market_timestamp": self.market_timestamp.isoformat() if self.market_timestamp else None,
            "retrieved_at": self.retrieved_at.isoformat(), "freshness": self.freshness.value,
            "freshness_reason": self.freshness_reason, "source": self.source,
            "source_type": self.source_type, "independence_group": self.independence_group,
            "validation_status": self.validation_status, "ticker": self.ticker,
            "reference_label": self.reference_label,
            "validation_reason": self.validation_reason,
            "source_reference": self.source_reference,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class VixReading:
    value: float
    session: dt.date
    previous_value: float | None
    previous_session: dt.date | None
    change_pct: float | None
    freshness: FreshnessStatus
    freshness_reason: str
    source: str = SRC_YAHOO
    validation_status: str = "SINGLE_SOURCE"
    crosscheck: str = ""

    @property
    def fresh(self) -> bool:
        return self.freshness is FreshnessStatus.FRESH and self.change_pct is not None

    def to_dict(self) -> dict:
        return {"value": self.value, "session": self.session.isoformat(),
                "previous_value": self.previous_value,
                "previous_session": self.previous_session.isoformat() if self.previous_session else None,
                "change_pct": self.change_pct, "freshness": self.freshness.value,
                "freshness_reason": self.freshness_reason, "source": self.source,
                "validation_status": self.validation_status, "crosscheck": self.crosscheck}


@dataclass
class PreMarketAcquisition:
    global_cues: list = field(default_factory=list)
    vix: VixReading | None = None
    gift: PreMarketQuote | None = None
    log: list = field(default_factory=list)
    # {"status": FRESH/STALE/FUTURE/UNKNOWN/CONFLICT/REJECTED/UNAVAILABLE/NO_SOURCE, "reason"}
    gift_status: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"global_cues": [q.to_dict() for q in self.global_cues],
                "vix": self.vix.to_dict() if self.vix else None,
                "gift": self.gift.to_dict() if self.gift else None,
                "gift_status": dict(self.gift_status), "log": list(self.log),
                "provider_version": PREMARKET_PROVIDER_VERSION}


# --------------------------------------------------------------------------- pure readers
def _dated_closes(daily: pd.DataFrame) -> list:
    """[(date, close)] oldest first, NaN closes dropped, the exchange's own calendar date."""
    if daily is None or len(daily) == 0:
        return []
    d = daily.dropna(subset=["Close"])
    return [(ts.date(), float(c)) for ts, c in zip(d.index, d["Close"])]


def us_close_quote(key, name, ticker, daily, pre_date, as_of, retrieved_at) -> PreMarketQuote:
    """The overnight US close. Bars dated on/after `pre_date` are never read (look-ahead)."""
    rows = [(d, c) for d, c in _dated_closes(daily) if d < pre_date]
    if len(rows) < 2:
        return PreMarketQuote(key, name, "US", QuoteKind.SESSION_CLOSE, None, None, None, None,
                              None, None, retrieved_at, FreshnessStatus.UNKNOWN,
                              "fewer than two dated daily bars", ticker=ticker)
    (d0, c0), (d1, c1) = rows[-2], rows[-1]
    status, reason = us_close_freshness(d1, pre_date, as_of)
    pct = None
    if (d1 - d0).days > MAX_PREV_GAP_DAYS:
        status, reason = FreshnessStatus.STALE, f"previous bar {d0} is not the prior session of {d1}"
    elif c0:
        pct = (c1 / c0 - 1) * 100
    return PreMarketQuote(key, name, "US", QuoteKind.SESSION_CLOSE, c1, pct, c0, d0, d1, None,
                          retrieved_at, status, reason, ticker=ticker)


def live_quote(key, name, ticker, region, intraday, daily, pre_date, as_of,
               retrieved_at) -> PreMarketQuote:
    """The last COMPLETED intraday bar at or before the cutoff, vs the previous daily close."""
    cut = to_ist(as_of)
    last = None
    if intraday is not None and len(intraday):
        d = intraday.dropna(subset=["Close"])
        for ts, c in zip(d.index, d["Close"]):
            end = to_ist(ts.to_pydatetime()) + INTRADAY_INTERVAL
            if end <= cut:
                last = (end, float(c))
    ref = [(dd, c) for dd, c in _dated_closes(daily) if dd < pre_date]
    if last is None:
        return PreMarketQuote(key, name, region, QuoteKind.LIVE, None, None,
                              ref[-1][1] if ref else None, ref[-1][0] if ref else None, None,
                              None, retrieved_at, FreshnessStatus.UNKNOWN,
                              "no completed intraday bar before the cutoff (market closed or not yet open)",
                              ticker=ticker)
    ts, value = last
    status, reason = live_freshness(ts, pre_date, as_of, region)
    pct = None
    if not ref:
        status, reason = FreshnessStatus.UNKNOWN, "no previous daily close"
    else:
        rd, rc = ref[-1]
        if (pre_date - rd).days > MAX_PREV_GAP_DAYS:
            status, reason = FreshnessStatus.STALE, f"previous close {rd} is too old"
        elif rc:
            pct = (value / rc - 1) * 100
    return PreMarketQuote(key, name, region, QuoteKind.LIVE, value, pct,
                          ref[-1][1] if ref else None, ref[-1][0] if ref else None, ts.date(), ts,
                          retrieved_at, status, reason, ticker=ticker)


def vix_reading(daily, previous_session, calendar, report_vix=None) -> VixReading | None:
    """India VIX on the previous session and the session before it, both by CANONICAL date
    (a provider row that is not a session is ignored). Cross-checked against the report's own
    VIX fact; a disagreement drops the change rather than choose a side."""
    closes = {d: c for d, c in _dated_closes(daily)}
    if previous_session not in closes:
        return None
    value = closes[previous_session]
    before = calendar.previous_session(previous_session) if calendar else None
    prev_value = closes.get(before) if before else None
    status, reason = session_freshness(previous_session, previous_session)
    change = (value / prev_value - 1) * 100 if prev_value else None
    check = ""
    if report_vix is not None:
        diff = abs(value / report_vix - 1) * 100 if report_vix else 100.0
        check = f"provider {value:.2f} vs report {report_vix:.2f} ({diff:.2f}% apart)"
        if diff > VIX_CROSSCHECK_PCT:
            return VixReading(value, previous_session, prev_value, before, None,
                              FreshnessStatus.STALE, "disagrees with the report's VIX fact",
                              validation_status="CONFLICT", crosscheck=check)
    if prev_value is None:
        reason = f"no VIX close for the session before ({before})"
    return VixReading(value, previous_session, prev_value, before, change, status, reason,
                      crosscheck=check)


# --------------------------------------------------------------------------- network wiring
def yahoo_history(ticker: str, start: dt.date, end: dt.date, interval: str = "1d") -> pd.DataFrame:
    import yfinance as yf
    return yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(),
                                     interval=interval)


def no_gift_source(pre_date, as_of):
    """The offline default: no GIFT source wired. Returning None omits GIFT. The product
    runner wires the approved exchange source (`providers.gift_nifty.fetch_gift_nifty`)."""
    return None


def gift_status_of(quote) -> dict:
    """One-line verdict on the GIFT reading, for the acquisition log and run history."""
    if quote is None:
        return {"status": "NO_SOURCE", "reason": "no GIFT Nifty reading - omitted"}
    # timing first: a reading from the wrong moment is wrong whatever its validation says
    if quote.freshness is not FreshnessStatus.FRESH:
        return {"status": quote.freshness.value, "reason": quote.freshness_reason}
    if quote.validation_status in UNUSABLE_VALIDATION:
        return {"status": quote.validation_status, "reason": quote.validation_reason}
    if quote.change_pct is None:
        return {"status": "UNKNOWN", "reason": quote.validation_reason or "no change computed"}
    return {"status": quote.freshness.value, "reason": quote.freshness_reason}


def fetch_premarket_quotes(pre_date: dt.date, as_of: dt.datetime, previous_session: dt.date,
                           calendar=None, report_vix: float | None = None, history_fn=None,
                           gift_fn=None, retrieved_at: dt.datetime | None = None,
                           universe=GLOBAL_UNIVERSE) -> PreMarketAcquisition:
    """All pre-open readings for `pre_date` as of `as_of` (IST). Works for a live morning and
    for a reconstruction of a past one: nothing dated after the cutoff is ever read."""
    history_fn = history_fn or yahoo_history
    gift_fn = gift_fn or no_gift_source
    retrieved_at = retrieved_at or dt.datetime.now(IST)
    acq = PreMarketAcquisition()
    start = pre_date - dt.timedelta(days=14)
    for key, name, ticker, region in universe:
        try:
            daily = history_fn(ticker, start, pre_date + dt.timedelta(days=1), "1d")
            if region == "US":
                q = us_close_quote(key, name, ticker, daily, pre_date, as_of, retrieved_at)
            else:
                intra = history_fn(ticker, pre_date - dt.timedelta(days=1),
                                   pre_date + dt.timedelta(days=1), "5m")
                q = live_quote(key, name, ticker, region, intra, daily, pre_date, as_of,
                               retrieved_at)
        except Exception as exc:          # one market failing never costs the others
            acq.log.append(f"{name}: fetch failed ({type(exc).__name__}: {exc})")
            continue
        acq.global_cues.append(q)
        acq.log.append(f"{name}: {q.freshness.value} - {q.freshness_reason}")
    try:
        vdaily = history_fn("^INDIAVIX", previous_session - dt.timedelta(days=14),
                            previous_session + dt.timedelta(days=1), "1d")
        acq.vix = vix_reading(vdaily, previous_session, calendar, report_vix)
        acq.log.append("INDIA VIX: " + (f"{acq.vix.freshness.value} - {acq.vix.freshness_reason}"
                                        if acq.vix else "no bar for the previous session"))
    except Exception as exc:
        acq.log.append(f"INDIA VIX: fetch failed ({type(exc).__name__}: {exc})")
    try:
        acq.gift = gift_fn(pre_date, as_of)
        acq.gift_status = gift_status_of(acq.gift)
    except Exception as exc:          # an unavailable optional source never blocks the Short
        acq.gift_status = {"status": "UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}
        acq.log.append(f"GIFT NIFTY: source failed ({type(exc).__name__}: {exc})")
    if acq.gift is None:
        acq.log.append("GIFT NIFTY: " + ("no approved non-AI source - omitted"
                                         if acq.gift_status.get("status") == "NO_SOURCE"
                                         else "unavailable - omitted"))
    else:
        acq.log.append(f"GIFT NIFTY: {acq.gift_status['status']} - {acq.gift_status['reason']}")
    return acq


__all__ = ["PreMarketQuote", "VixReading", "PreMarketAcquisition", "GLOBAL_UNIVERSE",
           "us_close_quote", "live_quote", "vix_reading", "fetch_premarket_quotes",
           "no_gift_source", "gift_status_of", "yahoo_history", "PREMARKET_PROVIDER_VERSION",
           "UNUSABLE_VALIDATION"]
