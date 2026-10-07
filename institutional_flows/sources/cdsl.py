"""CDSL daily depository-reported FPI flow - `https://www.cdslindia.com/eservices/publications/fiidaily`.

CDSL's own page states the data "is compiled on the basis of reports submitted to depositories
by custodians on <date> and constitutes trades conducted by FIIs/FPIs on and upto the previous
trading day(s)" - an explicitly multi-day-capable window, not "yesterday" and not "final". We
therefore NEVER label the represented period as a single trading session; its basis stays
UNKNOWN and the source's own sentence is kept verbatim in `source_note`.

The legacy `https://www.cdslindia.com/publications/FIIDailyData.aspx` page is stale (verified
2026-10-05: its equity table still showed 30-AUG-2024) and is never used.
"""
from __future__ import annotations

import datetime as dt
import re

from ..html_tables import extract_tables
from ..models import (DEPOSITORY_REPORTED, PARSE_ERROR, CDSL as SRC_KEY, RepresentedPeriod,
                      SCHEMA_VERSION, SOURCE_UNAVAILABLE, SUCCESS, UNKNOWN, VALIDATION_FAILED,
                      InstitutionalFlowSnapshot)
from ..numeric import parse_number

CDSL_URL = "https://www.cdslindia.com/eservices/publications/fiidaily"
PARSER_VERSION = "cdsl-daily-1.0"
_DATE_RE = re.compile(r"^\d{2}-[A-Z]{3}-\d{4}$")
_EXPECTED_HEADER = ["Reporting Date", "Debt/Debt-VRR/Equity/Hybrid", "Investment Route",
                    "Gross Purchases(Rs. Crore)", "Gross Sales (Rs. Crore)",
                    "Net Investment (Rs. Crore)", "Net Investment US($) million",
                    "Conversion (1 USD TO INR)*"]
_TOLERANCE_CR = 0.1


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _date(txt: str) -> dt.date:
    return dt.datetime.strptime(txt, "%d-%b-%Y").date()


def _note(tables: list) -> str:
    for t in tables:
        for row in t:
            for cell in row:
                if "compiled on the basis of reports submitted to depositories by custodians" in cell:
                    return cell
    return ""


def parse_cdsl_daily(html: str, now_iso: str) -> InstitutionalFlowSnapshot:
    base = dict(schema_version=SCHEMA_VERSION, source=SRC_KEY, source_class=DEPOSITORY_REPORTED,
               report_type="fpi_daily", report_key="", report_date=None, data_as_of=None,
               represented_period=RepresentedPeriod(), retrieved_at=now_iso,
               first_retrieved_at=now_iso, status=PARSE_ERROR, source_url=CDSL_URL,
               source_reference=CDSL_URL, parser_version=PARSER_VERSION,
               connectivity_status="REACHABLE")
    tables = extract_tables(html)
    if not tables or not tables[0]:
        return InstitutionalFlowSnapshot(**base, reason="no table found on the CDSL daily page")
    header = tables[0][0]
    if [h.strip() for h in header[:8]] != _EXPECTED_HEADER:
        return InstitutionalFlowSnapshot(**base,
            reason=f"unexpected CDSL table header (schema change?): {header[:8]!r}")

    rows, dates = [], set()
    for raw in tables[0][1:]:
        if len(raw) < 8 or not _DATE_RE.match(raw[0].strip()):
            break   # footnote / "Notes" rows follow the data; stop at the first non-data row
        try:
            day = _date(raw[0].strip())
        except ValueError:
            return InstitutionalFlowSnapshot(**base, reason=f"unparsable reporting date {raw[0]!r}")
        nums = [parse_number(c) for c in raw[3:8]]
        if any(n is None for n in nums):
            return InstitutionalFlowSnapshot(**base, reason=f"non-numeric cell in row {raw!r}")
        gross_p, gross_s, net_inv, net_usd, conv = nums
        dates.add(day)
        rows.append({"date": day, "category": raw[1].strip(), "route": raw[2].strip(),
                    "gross_purchases": gross_p, "gross_sales": gross_s,
                    "net_investment": net_inv, "net_investment_usd_mn": net_usd,
                    "conversion_rate": conv})
    if not rows:
        return InstitutionalFlowSnapshot(**base, reason="no data rows under the CDSL header")
    if len(dates) != 1:
        return InstitutionalFlowSnapshot(**base,
            reason=f"multiple reporting dates in one page: {sorted(d.isoformat() for d in dates)}")
    day = dates.pop()

    for r in rows:
        if abs(r["gross_purchases"] - r["gross_sales"] - r["net_investment"]) > _TOLERANCE_CR:
            return InstitutionalFlowSnapshot(
                **{**base, "status": VALIDATION_FAILED, "report_key": day.isoformat()},
                reason=(f"{r['category']}/{r['route']}: purchases {r['gross_purchases']} - "
                       f"sales {r['gross_sales']} != net {r['net_investment']}"))

    # sub-total / total internal consistency: sum of a category's non-subtotal rows == its
    # own Sub-total row; sum of every Sub-total (incl. the AIFs-labelled grand Total row's
    # category, which CDSL's merged cell always leaves as the last category) == the Total row.
    by_cat: dict = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    total_row = next((r for r in rows if r["route"] == "Total"), None)
    subtotal_sum = 0.0
    for cat, cat_rows in by_cat.items():
        sub = next((r for r in cat_rows if r["route"] == "Sub-total"), None)
        detail = [r for r in cat_rows if r["route"] not in ("Sub-total", "Total")]
        if sub is not None and detail:
            s = sum(r["net_investment"] for r in detail)
            if abs(s - sub["net_investment"]) > _TOLERANCE_CR * (len(detail) + 1):
                return InstitutionalFlowSnapshot(
                    **{**base, "status": VALIDATION_FAILED, "report_key": day.isoformat()},
                    reason=f"{cat} rows sum to {s}, Sub-total says {sub['net_investment']}")
        if sub is not None:
            subtotal_sum += sub["net_investment"]
    if total_row is not None and abs(subtotal_sum - total_row["net_investment"]) > \
            _TOLERANCE_CR * (len(by_cat) + 1):
        return InstitutionalFlowSnapshot(
            **{**base, "status": VALIDATION_FAILED, "report_key": day.isoformat()},
            reason=f"sub-totals sum to {subtotal_sum}, Total row says {total_row['net_investment']}")

    facts = []
    for r in rows:
        cat_slug, route_slug = _slug(r["category"]), _slug(r["route"])
        for metric, key in (("gross_purchases", "gross_purchases"), ("gross_sales", "gross_sales"),
                            ("net_investment", "net_investment")):
            facts.append({"category": cat_slug, "route": route_slug, "metric": metric,
                         "value": r[key], "unit": "INR_CRORE"})
        facts.append({"category": cat_slug, "route": route_slug, "metric": "net_investment",
                     "value": r["net_investment_usd_mn"], "unit": "USD_MILLION"})

    extra = {"used_in_p1": False}
    if len(tables) > 1:
        extra["raw_derivatives"] = {"rows": tables[1], "note": "FPI derivative trades - "
                                    "captured for future work, not interpreted in V1"}

    return InstitutionalFlowSnapshot(
        **{**base, "status": SUCCESS, "report_key": day.isoformat(),
          "report_date": day.isoformat(), "data_as_of": day.isoformat(),
          "represented_period": RepresentedPeriod(start=None, end=None, basis=UNKNOWN,
                                                  source_note=_note(tables)),
          "facts": facts, "extra": extra},
        reason=f"{len(facts)} facts for reporting date {day.isoformat()}")


def fetch_cdsl_daily(now_iso: str, get=None) -> InstitutionalFlowSnapshot:
    from operations.connectivity import classify_exception
    base = dict(schema_version=SCHEMA_VERSION, source=SRC_KEY, source_class=DEPOSITORY_REPORTED,
               report_type="fpi_daily", report_key="", report_date=None, data_as_of=None,
               represented_period=RepresentedPeriod(), retrieved_at=now_iso,
               first_retrieved_at=now_iso, status=SOURCE_UNAVAILABLE, source_url=CDSL_URL,
               source_reference=CDSL_URL, parser_version=PARSER_VERSION)
    try:
        if get is None:
            import requests
            from market import UA
            r = requests.get(CDSL_URL, headers=UA, timeout=20)
            r.raise_for_status()
            html = r.text
        else:
            html = get(CDSL_URL)
    except Exception as exc:
        return InstitutionalFlowSnapshot(**base, connectivity_status=classify_exception(exc),
                                         reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    return parse_cdsl_daily(html, now_iso)


__all__ = ["CDSL_URL", "parse_cdsl_daily", "fetch_cdsl_daily"]
