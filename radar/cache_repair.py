"""Repair of already-cached NON-FINAL bars in `market_ohlcv.db` (benchmark-gap / cache patch).

The write-through guard (`market.session_in_progress`, `market._write_through_ohlcv`) now stops a
still-trading session from being cached as a finished OK bar. Rows written BEFORE that guard are
still in the store - verified 2026-09-25: a 11:44 IST run stored 200 intraday 25-Sep bars as OK
(RELIANCE 1221.0 / 6.4M vs a later full-session reading), and a warm-cache read would take them
as that session's final closes. This module finds those rows and replaces them with the
session's final bar, re-fetched after the close.

What counts as non-final (`classify`), and nothing else is ever touched:
  - INTRADAY_SNAPSHOT: status OK, but retrieved (IST) before `market.SESSION_FINAL_TIME` on the
    session's own date - the bar was captured while the session was still trading;
  - BACKFILL_PENDING: Yahoo had O/H/L/V but no Close yet (never used by any reader, but it is a
    known-incomplete row for a real session, so it is repaired the same way).
A row retrieved after its session's final time is final by the same rule `_clean` applies, and
is never selected. Provider placeholder rows on canonical NON-SESSION dates are not repaired
(analytics already exclude them; the raw cache keeps them as provider evidence).

Safety:
  - a session still in progress at repair time is DEFERRED, never "repaired" with another
    intraday snapshot;
  - each write is conditional (`OHLCVStore.replace_non_final_bar`): it lands only if the row
    still has the exact `retrieved_at`/`quality_status` the scan saw, so a final bar some other
    run wrote in the meantime can't be overwritten;
  - only the targeted (symbol, session) keys are written - the re-fetch returns whole windows,
    and every other date in them is ignored;
  - the new bar must itself be complete (Close present, High >= Low > 0) or the old row stays;
  - after writing, the row is read back and compared, and (optionally) the new close/volume are
    checked against NSE's own bhavcopy for that session - an independent official source.
Every row considered is in the returned audit with its old and new values.
"""
from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass, field

import market
from config import IST, now_ist
from core.trading_calendar import NON_SESSION, SessionCalendar
from storage.ohlcv_models import OHLCVBar, QualityStatus

INTRADAY_SNAPSHOT = "INTRADAY_SNAPSHOT"
BACKFILL_PENDING = "BACKFILL_PENDING"

NSE_BHAVCOPY_URL = ("https://nsearchives.nseindia.com/products/content/"
                    "sec_bhavdata_full_{ddmmyyyy}.csv")
# Yahoo's NSE close is NSE's official closing price; anything beyond rounding is worth a flag.
BHAVCOPY_CLOSE_TOLERANCE = 0.001


def classify(row) -> str | None:
    """Why a stored row is not a final bar, or None if it is final."""
    if row.quality_status == QualityStatus.BACKFILL_PENDING:
        return BACKFILL_PENDING
    if row.quality_status != QualityStatus.OK:
        return None
    retrieved = row.retrieved_at
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=dt.timezone.utc)
    final_at = dt.datetime.combine(row.session_date, market.SESSION_FINAL_TIME, tzinfo=IST)
    return INTRADAY_SNAPSHOT if retrieved < final_at else None


def find_non_final_rows(store, calendar: SessionCalendar | None = None,
                        source: str = "yahoo") -> tuple[list, list]:
    """(targets, skipped): `targets` = [(row, reason)] to repair; `skipped` = rows that are
    non-final but on a canonical NON-SESSION date (holiday placeholders - left as they are)."""
    calendar = calendar or SessionCalendar()
    targets, skipped = [], []
    for row in store.get_possibly_non_final(source=source):
        reason = classify(row)
        if reason is None:
            continue
        if calendar.status(row.session_date) == NON_SESSION:
            skipped.append((row, reason))
            continue
        targets.append((row, reason))
    return targets, skipped


def _period_for(oldest: dt.date, today: dt.date) -> str:
    days = (today - oldest).days
    for period, span in (("1mo", 25), ("3mo", 85), ("6mo", 175), ("1y", 360)):
        if days <= span:
            return period
    return "2y"


def _fetch_final_rows(symbols: list, sessions: set, period: str) -> dict:
    """{(symbol, session_date): (open, high, low, close, volume) | None} from ONE Yahoo re-fetch
    per kind of symbol. Index symbols ('^...') go through `market.history`; stocks through the
    same bulk download acquisition uses. Nothing here writes anywhere."""
    import pandas as pd
    out: dict = {}
    stocks = [s for s in symbols if not s.startswith("^")]
    frames = {}
    if stocks:
        raw = market._bulk_download_universe_ohlcv(stocks, period=period)
        for s in stocks:
            try:
                frames[s] = market._normalize_index(
                    raw[s + ".NS"][["Open", "High", "Low", "Close", "Volume"]])
            except Exception:
                frames[s] = None
    for s in symbols:
        if s.startswith("^"):
            try:
                frames[s] = market.history(s, period)
            except Exception:
                frames[s] = None
    for s, f in frames.items():
        for d in sessions:
            if f is None:
                out[(s, d)] = None
                continue
            hit = f[f.index.date == d]
            if hit.empty:
                out[(s, d)] = None
                continue
            r = hit.iloc[-1]
            val = lambda x: None if pd.isna(x) else float(x)      # noqa: E731
            out[(s, d)] = (val(r.Open), val(r.High), val(r.Low), val(r.Close), val(r.Volume))
    return out


def nse_bhavcopy(session_date: dt.date) -> dict:
    """NSE's end-of-day equity bhavcopy for `session_date` -> {symbol: {"close", "volume"}}
    (EQ series), or {} if unavailable. Verification only."""
    import pandas as pd
    import requests
    url = NSE_BHAVCOPY_URL.format(ddmmyyyy=session_date.strftime("%d%m%Y"))
    try:
        r = requests.get(url, headers=market.UA, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), skipinitialspace=True)
        df.columns = [c.strip() for c in df.columns]
        df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
        return {str(s).strip(): {"close": float(c), "volume": float(v)}
                for s, c, v in zip(df["SYMBOL"], df["CLOSE_PRICE"], df["TTL_TRD_QNTY"])}
    except Exception as exc:
        print(f"[cache_repair] bhavcopy {session_date} unavailable: {exc}")
        return {}


@dataclass
class RepairResult:
    scanned_at: str
    targets: int = 0
    replaced: int = 0
    deferred_in_progress: int = 0
    unresolved: int = 0
    conflicts_left_alone: int = 0
    skipped_non_session: int = 0
    by_reason: dict = field(default_factory=dict)
    by_session: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _vals(row) -> dict:
    return {"open": row.open, "high": row.high, "low": row.low, "close": row.close,
            "volume": row.volume, "retrieved_at": row.retrieved_at.isoformat(),
            "quality_status": row.quality_status.value}


def repair_non_final_rows(store, *, now: dt.datetime | None = None,
                          calendar: SessionCalendar | None = None, source: str = "yahoo",
                          fetch=None, bhavcopy=None, verify: bool = True,
                          dry_run: bool = False) -> RepairResult:
    """Find every non-final row, re-fetch its session's final bar, replace it conditionally,
    read it back, and verify it. `fetch(symbols, sessions, period)` / `bhavcopy(date)` are the
    network seams (tests pass fakes). `dry_run` does everything except the write."""
    now = now or now_ist()
    fetch = fetch or _fetch_final_rows
    bhavcopy = bhavcopy or nse_bhavcopy
    targets, skipped = find_non_final_rows(store, calendar, source=source)
    res = RepairResult(scanned_at=now.isoformat(), targets=len(targets),
                       skipped_non_session=len(skipped))
    for row, reason in skipped:
        res.rows.append({"symbol": row.symbol, "session_date": row.session_date.isoformat(),
                         "reason": reason, "action": "SKIPPED_NON_SESSION", "old": _vals(row)})

    ready = []
    for row, reason in targets:
        res.by_reason[reason] = res.by_reason.get(reason, 0) + 1
        iso = row.session_date.isoformat()
        res.by_session.setdefault(iso, {"targets": 0, "replaced": 0})["targets"] += 1
        if market.session_in_progress(row.session_date, now):
            res.deferred_in_progress += 1
            res.rows.append({"symbol": row.symbol, "session_date": iso, "reason": reason,
                             "action": "DEFERRED_SESSION_IN_PROGRESS", "old": _vals(row)})
            continue
        ready.append((row, reason))
    if not ready:
        return res

    sessions = {r.session_date for r, _ in ready}
    symbols = sorted({r.symbol for r, _ in ready})
    fetched = fetch(symbols, sessions, _period_for(min(sessions), now.date()))
    fetched_at = dt.datetime.now(dt.timezone.utc)
    bhav = {d: bhavcopy(d) for d in sorted(sessions)} if verify else {}

    for row, reason in ready:
        iso = row.session_date.isoformat()
        entry = {"symbol": row.symbol, "session_date": iso, "reason": reason, "old": _vals(row)}
        new = fetched.get((row.symbol, row.session_date))
        o, h, lo, c, v = new if new else (None,) * 5
        if c is None or h is None or lo is None or not (h >= lo > 0 and c > 0):
            res.unresolved += 1
            entry.update(action="UNRESOLVED_NO_FINAL_BAR", fetched=new)
            res.rows.append(entry)
            continue
        bar = OHLCVBar(symbol=row.symbol, session_date=row.session_date, open=o, high=h, low=lo,
                       close=c, volume=v, source=row.source, retrieved_at=fetched_at,
                       quality_status=QualityStatus.OK)
        entry["new"] = {"open": o, "high": h, "low": lo, "close": c, "volume": v,
                        "retrieved_at": fetched_at.isoformat(), "quality_status": "OK"}
        if row.close:
            entry["close_change_pct"] = round((c / row.close - 1) * 100, 4)
        if row.volume:
            entry["volume_ratio"] = round(v / row.volume, 4) if v else None
        ref = (bhav.get(row.session_date) or {}).get(row.symbol)
        if ref:
            diff = abs(c / ref["close"] - 1)
            entry["verification"] = {
                "source": "NSE_BHAVCOPY", "nse_close": ref["close"], "nse_volume": ref["volume"],
                "close_relative_diff": round(diff, 6),
                "status": "MATCHES_NSE_CLOSE" if diff <= BHAVCOPY_CLOSE_TOLERANCE
                else "CLOSE_DIFFERS_FROM_NSE"}
        elif verify:
            entry["verification"] = {"source": "NSE_BHAVCOPY", "status": "NOT_IN_BHAVCOPY"}
        if dry_run:
            entry["action"] = "WOULD_REPLACE"
            res.rows.append(entry)
            continue
        if not store.replace_non_final_bar(bar, row.retrieved_at, row.quality_status):
            res.conflicts_left_alone += 1
            entry["action"] = "LEFT_ALONE_ROW_CHANGED_SINCE_SCAN"
            res.rows.append(entry)
            continue
        back = store.get_bar(row.symbol, row.session_date, row.source)
        entry["readback_ok"] = bool(back and back.close == c and back.high == h
                                    and back.low == lo and back.open == o and back.volume == v
                                    and back.quality_status == QualityStatus.OK)
        entry["action"] = "REPLACED"
        res.replaced += 1
        res.by_session[iso]["replaced"] += 1
        res.rows.append(entry)

    ver = [r.get("verification", {}).get("status") for r in res.rows if r.get("new")]
    res.verification = {s: ver.count(s) for s in sorted({x for x in ver if x})}
    res.verification["readback_failures"] = sum(1 for r in res.rows
                                                 if r.get("action") == "REPLACED"
                                                 and not r.get("readback_ok"))
    return res


__all__ = ["classify", "find_non_final_rows", "repair_non_final_rows", "RepairResult",
           "nse_bhavcopy", "INTRADAY_SNAPSHOT", "BACKFILL_PENDING"]
