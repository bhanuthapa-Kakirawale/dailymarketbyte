"""Official event sources. Each adapter = a pure PARSER (tested on synthetic, structurally
identical payloads - real exchange payloads are never committed) + a thin FETCH that is only
ever called by a production job, fails closed, and records what it saw.

    FnoBanSource          nsearchives.nseindia.com/content/fo/fo_secban.csv  (archive file)
                          header: "Securities in Ban For Trade Date 28-SEP-2026:" then "n,SYMBOL"
    SurveillanceSource    www.nseindia.com/api/reportASM (longterm/shortterm .data[])
                          www.nseindia.com/api/reportGSM ([])  - undocumented website API

A payload whose shape or date does not validate yields status INVALID and NO events - never a
partially-read list.
"""
from __future__ import annotations

import datetime as dt
import re

from core import sources as S

from .models import EventFamily, ExchangeEvent, SourceResult

FO_BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
ASM_URL = "https://www.nseindia.com/api/reportASM"
GSM_URL = "https://www.nseindia.com/api/reportGSM"
_BAN_HEADER = re.compile(r"Securities in Ban For Trade Date\s+(\d{2}-[A-Za-z]{3}-\d{4})", re.I)
_SYM = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _date(txt: str) -> dt.date:
    return dt.datetime.strptime(txt.strip()[:11].title(), "%d-%b-%Y").date()


# --------------------------------------------------------------------------- F&O ban
def parse_fo_ban(text: str, retrieved_at: str | None = None,
                 url: str = FO_BAN_URL) -> SourceResult:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return SourceResult(S.SRC_NSE_FO_BAN, "INVALID", reason="empty file")
    m = _BAN_HEADER.search(lines[0])
    if not m:
        return SourceResult(S.SRC_NSE_FO_BAN, "INVALID", reason=f"unexpected header {lines[0][:60]!r}")
    trade_date = _date(m.group(1))
    events = []
    for ln in lines[1:]:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) != 2 or not parts[0].isdigit() or not _SYM.match(parts[1]):
            return SourceResult(S.SRC_NSE_FO_BAN, "INVALID", reason=f"unexpected row {ln[:60]!r}",
                                list_date=trade_date)
        sym = parts[1]
        events.append(ExchangeEvent(
            event_id=f"FNO_BAN:{trade_date}:{sym}", family=EventFamily.FNO_BAN, symbol=sym,
            company="", status="IN BAN PERIOD",
            detail="Security in the F&O ban period (market-wide position limit)",
            data_as_of=trade_date, source_name=S.SRC_NSE_FO_BAN, source_reference=url,
            retrieved_at=retrieved_at, validation_status="PARSED"))
    return SourceResult(S.SRC_NSE_FO_BAN, "OK", events=events, list_date=trade_date,
                        retrieved_at=retrieved_at,
                        reason=f"{len(events)} securities in ban for trade date {trade_date}")


# --------------------------------------------------------------------------- ASM / GSM
def parse_asm(payload, retrieved_at: str | None = None, url: str = ASM_URL) -> SourceResult:
    if not isinstance(payload, dict):
        return SourceResult(S.SRC_NSE_SURVEILLANCE, "INVALID", reason="ASM payload is not an object")
    events, dates = [], set()
    for bucket, label in (("longterm", "Long-term ASM"), ("shortterm", "Short-term ASM")):
        rows = (payload.get(bucket) or {}).get("data") if isinstance(payload.get(bucket), dict) else None
        if rows is None:
            continue
        for r in rows:
            try:
                sym, day = str(r["symbol"]).strip(), _date(str(r["asmTime"]))
                stage = str(r.get("asmSurvIndicator") or "").strip()
            except Exception as exc:
                return SourceResult(S.SRC_NSE_SURVEILLANCE, "INVALID",
                                    reason=f"ASM row unreadable: {type(exc).__name__}")
            dates.add(day)
            events.append(ExchangeEvent(
                event_id=f"ASM:{bucket}:{day}:{sym}", family=EventFamily.SURVEILLANCE_ASM,
                symbol=sym, company=str(r.get("companyName") or "").strip(),
                status=f"{label} · {stage}" if stage else label,
                detail=str(r.get("survDesc") or "").strip(), data_as_of=day,
                source_name=S.SRC_NSE_SURVEILLANCE, source_reference=url,
                retrieved_at=retrieved_at, validation_status="PARSED"))
    if not events:
        return SourceResult(S.SRC_NSE_SURVEILLANCE, "INVALID", reason="no ASM rows in payload")
    return SourceResult(S.SRC_NSE_SURVEILLANCE, "OK", events=events, list_date=max(dates),
                        retrieved_at=retrieved_at, reason=f"{len(events)} ASM rows")


def parse_gsm(payload, retrieved_at: str | None = None, url: str = GSM_URL) -> SourceResult:
    if not isinstance(payload, list):
        return SourceResult(S.SRC_NSE_SURVEILLANCE, "INVALID", reason="GSM payload is not a list")
    events, dates = [], set()
    for r in payload:
        try:
            sym, day = str(r["symbol"]).strip(), _date(str(r["gsmTime"]))
        except Exception as exc:
            return SourceResult(S.SRC_NSE_SURVEILLANCE, "INVALID",
                                reason=f"GSM row unreadable: {type(exc).__name__}")
        dates.add(day)
        stage = str(r.get("gsmStage") or "").strip()
        events.append(ExchangeEvent(
            event_id=f"GSM:{day}:{sym}", family=EventFamily.SURVEILLANCE_GSM, symbol=sym,
            company=str(r.get("companyName") or "").strip(),
            status=f"GSM · Stage {stage}" if stage else "GSM",
            detail=str(r.get("survDesc") or "").strip(), data_as_of=day,
            source_name=S.SRC_NSE_SURVEILLANCE, source_reference=url, retrieved_at=retrieved_at,
            validation_status="PARSED"))
    if not events:
        return SourceResult(S.SRC_NSE_SURVEILLANCE, "INVALID", reason="no GSM rows in payload")
    return SourceResult(S.SRC_NSE_SURVEILLANCE, "OK", events=events, list_date=max(dates),
                        retrieved_at=retrieved_at, reason=f"{len(events)} GSM rows")


# --------------------------------------------------------------------------- fetch (production only)
def fetch_fo_ban(now_iso: str, get=None) -> SourceResult:
    """One GET of the archive file. `get(url) -> text` is injectable; any failure -> UNAVAILABLE."""
    try:
        if get is None:
            import requests
            from market import UA
            r = requests.get(FO_BAN_URL, headers=UA, timeout=15)
            r.raise_for_status()
            text = r.text
        else:
            text = get(FO_BAN_URL)
    except Exception as exc:
        return SourceResult(S.SRC_NSE_FO_BAN, "UNAVAILABLE", reason=f"{type(exc).__name__}: {exc}")
    return parse_fo_ban(text, now_iso)


def fetch_surveillance(now_iso: str, nse=None) -> list:
    """ASM + GSM through the existing NSE website client. Each fails closed on its own."""
    out = []
    try:
        import market
        nse = nse or market.NSE()
    except Exception as exc:
        return [SourceResult(S.SRC_NSE_SURVEILLANCE, "UNAVAILABLE", reason=str(exc))]
    for path, parser, url in (("/api/reportASM", parse_asm, ASM_URL),
                              ("/api/reportGSM", parse_gsm, GSM_URL)):
        try:
            out.append(parser(nse.get(path), now_iso, url))
        except Exception as exc:
            out.append(SourceResult(S.SRC_NSE_SURVEILLANCE, "UNAVAILABLE",
                                    reason=f"{path}: {type(exc).__name__}: {exc}"))
    return out


__all__ = ["parse_fo_ban", "parse_asm", "parse_gsm", "fetch_fo_ban", "fetch_surveillance",
           "FO_BAN_URL", "ASM_URL", "GSM_URL"]
