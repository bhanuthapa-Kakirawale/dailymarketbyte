"""NSE provisional FII/FPI + DII cash-market flow - `/api/fiidiiTradeReact`.

This is a SECOND, richer read of the same NSE endpoint `market.fii_dii_nse` already uses for
the canonical report's net-only figures. It is additive: `market.fii_dii_nse` and the canonical
report path are untouched. This module additionally keeps gross buy/sell and NSE's own
timestamp, and stores them as an immutable `InstitutionalFlowSnapshot` rather than discarding
them after one run.
"""
from __future__ import annotations

import datetime as dt

from core import sources as S

from ..models import (EXCHANGE_PROVISIONAL, NSE as SRC_KEY, PARSE_ERROR, RepresentedPeriod,
                      SCHEMA_VERSION, SOURCE_STATED, SOURCE_UNAVAILABLE, SUCCESS,
                      VALIDATION_FAILED, InstitutionalFlowSnapshot)

FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
PARSER_VERSION = "nse-fiidii-1.0"
_TOLERANCE_CR = 0.05


def _nse_date(txt: str) -> dt.date:
    return dt.datetime.strptime(str(txt).strip()[:11], "%d-%b-%Y").date()


def parse_nse_fii_dii(payload, now_iso: str) -> InstitutionalFlowSnapshot:
    """`payload` is the raw list NSE's `/api/fiidiiTradeReact` returns: one row per category,
    each with `date`, `category`, `buyValue`, `sellValue`, `netValue` (Rs crore, as strings or
    numbers). Exactly one FII/FPI row and one DII row for the SAME date are required; anything
    else is a parse or validation failure rather than a best-effort guess."""
    base = dict(schema_version=SCHEMA_VERSION, source=SRC_KEY, source_class=EXCHANGE_PROVISIONAL,
               report_type="fii_dii_daily", report_key="", report_date=None, data_as_of=None,
               represented_period=RepresentedPeriod(), retrieved_at=now_iso,
               first_retrieved_at=now_iso, status=PARSE_ERROR, source_url=FII_DII_URL,
               source_reference=FII_DII_URL, parser_version=PARSER_VERSION,
               connectivity_status="REACHABLE")
    if not isinstance(payload, list) or not payload:
        return InstitutionalFlowSnapshot(**base, reason="payload is not a non-empty list")

    rows = {}
    dates = set()
    for r in payload:
        try:
            cat = str(r["category"]).upper()
            day = _nse_date(r["date"])
            buy = float(str(r["buyValue"]).replace(",", ""))
            sell = float(str(r["sellValue"]).replace(",", ""))
            net = float(str(r["netValue"]).replace(",", ""))
        except (KeyError, TypeError, ValueError) as exc:
            return InstitutionalFlowSnapshot(**base,
                reason=f"row unreadable: {type(exc).__name__}: {str(exc)[:160]}")
        key = "fpi" if "FII" in cat or "FPI" in cat else ("dii" if "DII" in cat else None)
        if key is None:
            return InstitutionalFlowSnapshot(**base, reason=f"unrecognised category {cat!r}")
        if key in rows:
            return InstitutionalFlowSnapshot(**base, reason=f"duplicate {key} row")
        rows[key] = {"date": day, "buy": buy, "sell": sell, "net": net}
        dates.add(day)

    if set(rows) != {"fpi", "dii"}:
        missing = {"fpi", "dii"} - set(rows)
        return InstitutionalFlowSnapshot(**base, reason=f"missing categories: {sorted(missing)}")
    if len(dates) != 1:
        return InstitutionalFlowSnapshot(**base,
            reason=f"FPI and DII rows disagree on date: {sorted(d.isoformat() for d in dates)}")
    day = dates.pop()

    facts = []
    for key in ("fpi", "dii"):
        row = rows[key]
        if abs(row["buy"] - row["sell"] - row["net"]) > _TOLERANCE_CR:
            return InstitutionalFlowSnapshot(
                **{**base, "status": VALIDATION_FAILED, "report_key": day.isoformat(),
                  "report_date": day.isoformat()},
                reason=(f"{key}: buy {row['buy']} - sell {row['sell']} != net {row['net']} "
                       f"(tolerance {_TOLERANCE_CR} cr)"))
        facts.append({"participant": key.upper(), "metric": "gross_purchases",
                     "value": row["buy"], "unit": "INR_CRORE"})
        facts.append({"participant": key.upper(), "metric": "gross_sales",
                     "value": row["sell"], "unit": "INR_CRORE"})
        facts.append({"participant": key.upper(), "metric": "net_investment",
                     "value": row["net"], "unit": "INR_CRORE"})

    return InstitutionalFlowSnapshot(
        **{**base, "status": SUCCESS, "report_key": day.isoformat(),
          "report_date": day.isoformat(), "data_as_of": day.isoformat(),
          "represented_period": RepresentedPeriod(start=day.isoformat(), end=day.isoformat(),
                                                  basis=SOURCE_STATED,
                                                  source_note="NSE's own trade-date row; "
                                                              "provisional, subject to revision"),
          "facts": facts},
        reason=f"{len(facts)} facts for {day.isoformat()}")


def fetch_nse_fii_dii(now_iso: str, nse=None) -> InstitutionalFlowSnapshot:
    """One GET through the existing `market.NSE` client (an injectable instance for tests).
    Never touches `market.fii_dii_nse` or the canonical report path."""
    from operations.connectivity import classify_exception
    base = dict(schema_version=SCHEMA_VERSION, source=SRC_KEY, source_class=EXCHANGE_PROVISIONAL,
               report_type="fii_dii_daily", report_key="", report_date=None, data_as_of=None,
               represented_period=RepresentedPeriod(), retrieved_at=now_iso,
               first_retrieved_at=now_iso, status=SOURCE_UNAVAILABLE, source_url=FII_DII_URL,
               source_reference=FII_DII_URL, parser_version=PARSER_VERSION)
    try:
        import market
        nse = nse or market.NSE()
        payload = nse.get("/api/fiidiiTradeReact")
    except Exception as exc:
        return InstitutionalFlowSnapshot(**base, connectivity_status=classify_exception(exc),
                                         reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    return parse_nse_fii_dii(payload, now_iso)


__all__ = ["FII_DII_URL", "parse_nse_fii_dii", "fetch_nse_fii_dii"]
