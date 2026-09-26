"""GIFT Nifty from the exchange that lists it: NSE International Exchange (NSE IX, GIFT City).

"GIFT Nifty" is the NIFTY 50 index future traded on NSE IX. This module reads it from the
venue itself - never from a model, never from a news snippet - and records everything needed
to audit the number:

    live quote   GET https://www.nseix.com/api/market-rate?type=derivative
                 one row per NIFTY FUTIDX contract: LASTPRICE, DAYCHANGE, PERCHANGE and
                 TIMESTMP ("25-Sep-2026 21:28:56", IST) = that contract's last-trade time
    reference    GET https://www.nseix.com/api/content/daily_report/G_T_DSP_PRICE_<DDMMYYYY>.CSV
                 NSE IX's official Daily Settlement Price file, one row per contract, dated

The change shown is `last price / previous daily settlement price - 1` for the NEAR-MONTH
contract (earliest expiry on/after the session date). It is GIFT Nifty's own move since its
last settlement - NOT a comparison with Nifty's spot close (a future vs spot comparison
carries the futures basis and reads as an opening call; PRE never makes one).

Validation, all deterministic and fail-closed (a failed check omits GIFT, never guesses):
- the settlement file must carry the date it is named for, and be the latest one before the
  session (a missing file - NSE IX holiday - steps back at most DSP_LOOKBACK_DAYS);
- the exchange's own DAYCHANGE must imply the same reference (LTP - DAYCHANGE == DSP within
  CHANGE_CONSISTENCY_POINTS). Verified 2026-09-25 21:29 IST: Sep contract LTP 23183.0,
  DAYCHANGE -5.50 -> 23188.5 == DSP(25-Sep) 23188.5. If the two exchange figures disagree the
  reference is ambiguous -> CONFLICT, omitted;
- |change| <= MAX_ABS_CHANGE_PCT and, when the canonical Nifty close is known, the future is
  within MAX_BASIS_VS_NIFTY_PCT of it (a plausibility check, not a display) -> else REJECTED;
- freshness: core.freshness.live_freshness with the GIFT limit (20 min) at the cutoff, and the
  reading must be taken before the 09:15 IST open of the session (an evening/intraday print
  on the session date is not a pre-open reading -> STALE).

The parsers are pure (tests run offline); `fetch_gift_nifty` wires them to HTTP through an
injectable `http_get(url) -> (status_code, text)`.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import dataclass, field

from core.freshness import IST, FreshnessStatus, QuoteKind, live_freshness, to_ist
from core.sources import GROUP_NSEIX, SRC_NSEIX_DSP, SRC_NSEIX_LIVE

from .premarket import PreMarketQuote

GIFT_PROVIDER_VERSION = "gift-nseix-1.0"

NSEIX_BASE = "https://www.nseix.com"
MARKET_RATE_URL = NSEIX_BASE + "/api/market-rate?type=derivative"
DSP_URL = NSEIX_BASE + "/api/content/daily_report/G_T_DSP_PRICE_{d:%d%m%Y}.CSV"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Referer": NSEIX_BASE + "/", "Accept": "application/json, text/csv, */*"}
TIMEOUT_S = 15

GIFT_SYMBOL = "NIFTY"
GIFT_INSTRUMENT = "FUTIDX"
CHANGE_CONSISTENCY_POINTS = 0.51     # exchange DAYCHANGE vs LTP - DSP (prices tick in 0.5)
MAX_ABS_CHANGE_PCT = 10.0            # beyond this a "move" is a bad print, not a morning
MAX_BASIS_VS_NIFTY_PCT = 5.0         # GIFT future vs canonical Nifty close - plausibility only
DSP_LOOKBACK_DAYS = 7
# A live run fetches GIFT a little after its cutoff (the Yahoo reads come first). Within this
# window the reading is judged at retrieval time; beyond it the run is a reconstruction of a
# past morning and a reading taken after the cutoff is look-ahead (FUTURE).
LIVE_RUN_WINDOW = dt.timedelta(minutes=10)
CLOCK_SKEW = dt.timedelta(seconds=60)
INDIA_OPEN = dt.time(9, 15)          # a GIFT reading taken after NSE opens is not pre-open


class GiftSourceError(RuntimeError):
    """The exchange's response could not be read as what it claims to be."""


@dataclass(frozen=True)
class GiftContract:
    symbol: str
    instrument: str
    expiry: dt.date
    last_price: float | None
    day_change: float | None
    pct_change: float | None
    timestamp: dt.datetime | None       # IST, the contract's last-trade time
    contracts_traded: int | None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "instrument": self.instrument,
                "expiry": self.expiry.isoformat(), "last_price": self.last_price,
                "day_change": self.day_change, "pct_change": self.pct_change,
                "timestamp": self.timestamp.isoformat() if self.timestamp else None,
                "contracts_traded": self.contracts_traded}


# --------------------------------------------------------------------------- pure parsers
def _num(x) -> float | None:
    """NSE IX numbers arrive as "23,183.50", ".16", "-.02", 37, "-" or None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if s in ("", "-", "--", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(text: str) -> dt.date:
    """"29-Sep-2026" / "29-SEP-2026" -> date."""
    return dt.datetime.strptime(text.strip().title(), "%d-%b-%Y").date()


def parse_nseix_timestamp(text) -> dt.datetime | None:
    """"25-Sep-2026 21:28:56" (the exchange's IST clock) -> aware IST datetime."""
    if not text:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return dt.datetime.strptime(str(text).strip().title(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def parse_market_rate(payload) -> list:
    """Every NIFTY FUTIDX contract in a market-rate response. Other symbols are ignored; a
    response that is not the documented shape raises (it is not a quote)."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise GiftSourceError("market-rate response has no 'data' list")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("SYMBOL", "")).strip().upper() != GIFT_SYMBOL:
            continue
        if str(r.get("INSTRUMENTTYPE", "")).strip().upper() != GIFT_INSTRUMENT:
            continue
        try:
            expiry = _date(str(r.get("EXPIRYDATE", "")))
        except ValueError:
            continue
        vol = _num(r.get("CONTRACTSTRADED"))
        out.append(GiftContract(GIFT_SYMBOL, GIFT_INSTRUMENT, expiry, _num(r.get("LASTPRICE")),
                                _num(r.get("DAYCHANGE")), _num(r.get("PERCHANGE")),
                                parse_nseix_timestamp(r.get("TIMESTMP")),
                                int(vol) if vol is not None else None, dict(r)))
    return sorted(out, key=lambda c: c.expiry)


def select_near_month(contracts, on: dt.date) -> GiftContract | None:
    """The near-month contract: the earliest expiry on or after `on`. That is what "GIFT
    Nifty" means; the most-traded contract can be the next month during rollover, but a
    rule that switches contracts by volume would switch the reference silently."""
    live = [c for c in contracts if c.expiry >= on]
    return min(live, key=lambda c: c.expiry) if live else None


def parse_settlement_file(text: str, expected_date: dt.date) -> dict:
    """{expiry: settlement price} for NIFTY FUTIDX from one DSP file. The file must carry the
    date it was requested for - a file that describes another day is not that day's
    settlement (raises)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = [f.strip().upper() for f in (reader.fieldnames or [])]
    need = {"DATE", "INSTRUMENT TYPE", "SYMBOL", "EXPIRY DATE", "SETTLEMENT PRICE"}
    if not need.issubset(fields):
        raise GiftSourceError(f"settlement file header {fields} lacks {sorted(need - set(fields))}")
    out = {}
    for raw in reader:
        r = {k.strip().upper(): (v or "").strip() for k, v in raw.items() if k}
        if r.get("SYMBOL", "").upper() != GIFT_SYMBOL or \
                r.get("INSTRUMENT TYPE", "").upper() != GIFT_INSTRUMENT:
            continue
        try:
            day = _date(r["DATE"])
            expiry = _date(r["EXPIRY DATE"])
        except (ValueError, KeyError):
            continue
        if day != expected_date:
            raise GiftSourceError(f"settlement file for {expected_date} carries a row dated {day}")
        price = _num(r.get("SETTLEMENT PRICE"))
        if price is not None and price > 0:
            out[expiry] = price
    return out


# --------------------------------------------------------------------------- the reading
def judging_cutoff(as_of: dt.datetime, retrieved_at: dt.datetime) -> dt.datetime:
    """The moment freshness is judged at: retrieval for a live run (fetched within
    LIVE_RUN_WINDOW after the cutoff), the cutoff itself for a reconstruction."""
    a, r = to_ist(as_of), to_ist(retrieved_at)
    return r if a <= r <= a + LIVE_RUN_WINDOW else a


def gift_quote(contract: GiftContract, settlement: float | None, settlement_date: dt.date | None,
               pre_date: dt.date, as_of: dt.datetime, retrieved_at: dt.datetime,
               nifty_close: float | None = None, provenance: dict | None = None,
               settlement_url: str = "") -> PreMarketQuote:
    """Judge one contract reading. Never raises: every failure is a recorded verdict."""
    prov = dict(provenance or {})
    cut = judging_cutoff(as_of, retrieved_at)
    live = cut == to_ist(retrieved_at)
    ts = contract.timestamp
    if live and ts is not None and cut < ts <= cut + CLOCK_SKEW:
        prov["clock_skew_seconds"] = round((ts - cut).total_seconds(), 1)
        cut = ts                     # exchange clock marginally ahead of ours on a live run
    status, reason = live_freshness(ts, pre_date, cut, "GIFT")
    if status is FreshnessStatus.FRESH and ts is not None and to_ist(ts).time() >= INDIA_OPEN:
        status, reason = FreshnessStatus.STALE, (
            f"reading at {to_ist(ts):%H:%M} IST is after the {INDIA_OPEN:%H:%M} open of "
            f"{pre_date} - not a pre-open reading")
    prov.update({
        "venue": "NSE IX (NSE International Exchange, GIFT City)",
        "instrument": f"{GIFT_INSTRUMENT} {GIFT_SYMBOL}",
        "contract_expiry": contract.expiry.isoformat(),
        "contract_rule": "near month: earliest expiry on/after the session date",
        "last_trade_timestamp": ts.isoformat() if ts else None,
        "last_price": contract.last_price,
        "exchange_day_change": contract.day_change,
        "exchange_pct_change": contract.pct_change,
        "contracts_traded": contract.contracts_traded,
        "settlement_price": settlement,
        "settlement_date": settlement_date.isoformat() if settlement_date else None,
        "settlement_source": SRC_NSEIX_DSP, "settlement_url": settlement_url,
        "judged_at": cut.isoformat(), "live_source_url": MARKET_RATE_URL,
        "provider_version": GIFT_PROVIDER_VERSION,
    })
    vstatus, vreason, pct = "SINGLE_SOURCE", "", None
    last = contract.last_price
    if last is None or last <= 0:
        vstatus, vreason = "REJECTED", "no last traded price for the near-month contract"
    elif settlement is None or settlement_date is None:
        vstatus, vreason = "UNAVAILABLE", "no NSE IX settlement price before the session"
    elif settlement_date >= pre_date:
        vstatus, vreason = "REJECTED", (f"settlement dated {settlement_date} is not before the "
                                        f"session {pre_date}")
    else:
        pct = (last / settlement - 1) * 100
        prov["change_definition"] = "last price / previous daily settlement price - 1"
        if contract.day_change is not None:
            implied = last - contract.day_change
            prov["implied_reference"] = round(implied, 2)
            if abs(implied - settlement) > CHANGE_CONSISTENCY_POINTS:
                prov["consistency"] = "INCONSISTENT"
                vstatus = "CONFLICT"
                vreason = (f"the exchange's DAYCHANGE implies a reference of {implied:.2f}, but "
                           f"the {settlement_date} settlement is {settlement:.2f} - the "
                           "reference is ambiguous, so GIFT is omitted")
                pct = None
            else:
                prov["consistency"] = "CONSISTENT"
        else:
            prov["consistency"] = "NOT_CHECKED (no DAYCHANGE)"
        if pct is not None and abs(pct) > MAX_ABS_CHANGE_PCT:
            vstatus, vreason = "REJECTED", f"change {pct:+.2f}% is outside +/-{MAX_ABS_CHANGE_PCT:.0f}%"
            pct = None
        if pct is not None and nifty_close:
            basis = (last / nifty_close - 1) * 100
            prov["vs_canonical_nifty_close_pct"] = round(basis, 3)   # audit only, never shown
            if abs(basis) > MAX_BASIS_VS_NIFTY_PCT:
                vstatus, vreason = "REJECTED", (f"GIFT {last:.1f} is {basis:+.2f}% from the "
                                                f"canonical Nifty close {nifty_close:.1f}")
                pct = None
    return PreMarketQuote(
        key="GIFT", name="GIFT NIFTY", region="GIFT", kind=QuoteKind.LIVE, value=last,
        change_pct=pct, reference_value=settlement, reference_date=settlement_date,
        market_date=ts.date() if ts else None, market_timestamp=ts,
        retrieved_at=retrieved_at, freshness=status, freshness_reason=reason,
        source=SRC_NSEIX_LIVE, source_type="PRIMARY", independence_group=GROUP_NSEIX,
        validation_status=vstatus, ticker=f"NSEIX:{GIFT_SYMBOL} {GIFT_INSTRUMENT} {contract.expiry:%d-%b-%Y}",
        reference_label="its previous settlement", validation_reason=vreason,
        source_reference=MARKET_RATE_URL, provenance=prov)


# --------------------------------------------------------------------------- network wiring
def http_get(url: str) -> tuple:
    import requests
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
    return r.status_code, r.text


def latest_settlement(pre_date: dt.date, expiry: dt.date, get=http_get) -> tuple:
    """(price, date, url, attempts) from the latest DSP file dated before `pre_date`."""
    attempts = []
    for back in range(1, DSP_LOOKBACK_DAYS + 1):
        day = pre_date - dt.timedelta(days=back)
        if day.weekday() >= 5:
            continue
        url = DSP_URL.format(d=day)
        try:
            code, text = get(url)
        except Exception as exc:
            attempts.append({"date": day.isoformat(), "url": url,
                             "result": f"{type(exc).__name__}: {exc}"})
            continue
        if code != 200 or not text or "SETTLEMENT" not in text.upper():
            attempts.append({"date": day.isoformat(), "url": url, "result": f"HTTP {code}"})
            continue
        try:
            prices = parse_settlement_file(text, day)
        except GiftSourceError as exc:
            attempts.append({"date": day.isoformat(), "url": url, "result": f"invalid: {exc}"})
            return None, None, url, attempts          # a mis-dated file is not skipped past
        attempts.append({"date": day.isoformat(), "url": url, "result": "OK"})
        if expiry not in prices:
            attempts[-1]["result"] = f"OK but no settlement for the {expiry} contract"
            return None, None, url, attempts
        return prices[expiry], day, url, attempts
    return None, None, "", attempts


def fetch_gift_nifty(pre_date: dt.date, as_of: dt.datetime, get=None,
                     retrieved_at: dt.datetime | None = None,
                     nifty_close: float | None = None) -> PreMarketQuote:
    """The `gift_fn` for providers.premarket.fetch_premarket_quotes. Raises GiftSourceError
    when the exchange cannot be read at all (-> UNAVAILABLE, omitted); otherwise returns a
    judged quote whose freshness/validation decide whether it is shown."""
    get = get or http_get
    code, text = get(MARKET_RATE_URL)
    retrieved_at = retrieved_at or dt.datetime.now(IST)
    if code != 200:
        raise GiftSourceError(f"NSE IX market-rate returned HTTP {code}")
    try:
        contracts = parse_market_rate(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise GiftSourceError(f"NSE IX market-rate response is not JSON: {exc}") from exc
    near = select_near_month(contracts, pre_date)
    if near is None:
        raise GiftSourceError(f"no NIFTY futures contract expiring on/after {pre_date} "
                              f"in the NSE IX response ({len(contracts)} NIFTY contracts)")
    price, sday, url, attempts = latest_settlement(pre_date, near.expiry, get)
    return gift_quote(near, price, sday, pre_date, as_of, retrieved_at, nifty_close,
                      provenance={"contracts_seen": [c.to_dict() for c in contracts],
                                  "settlement_attempts": attempts},
                      settlement_url=url)


def gift_fn_for(nifty_close: float | None = None, get=None):
    """A `gift_fn(pre_date, as_of)` bound to the canonical Nifty close (plausibility check)."""
    def fn(pre_date, as_of):
        return fetch_gift_nifty(pre_date, as_of, get=get, nifty_close=nifty_close)
    return fn


__all__ = ["GiftContract", "GiftSourceError", "parse_market_rate", "parse_settlement_file",
           "parse_nseix_timestamp", "select_near_month", "gift_quote", "judging_cutoff",
           "latest_settlement", "fetch_gift_nifty", "gift_fn_for", "MARKET_RATE_URL", "DSP_URL",
           "GIFT_PROVIDER_VERSION", "CHANGE_CONSISTENCY_POINTS", "LIVE_RUN_WINDOW"]
