"""NSDL fortnightly sector-wise FPI net investment - discovered, never hard-coded.

The selection page `Reports/FPI_Fortnightly_Selection.aspx` lists every published fortnight in
a plain `<select>`; this module always discovers the latest and previous ACTUALLY PUBLISHED
report from that list rather than assuming a calendar fortnight or a fixed URL. Verified live
2026-10-05: 352 options, newest-first, each `value` a `~/StaticReports/...html` path and each
label a date such as "SEP 15, 2026".

Each report's own table carries four column blocks - [AUC as of the PRIOR period end, Net
Investment for the PRIOR period, Net Investment for the CURRENT period, AUC as of the CURRENT
period end] - each split into IN INR Cr. / IN USD Mn, each split into 12 fixed sub-categories
(Equity, Debt General Limit, Debt VRR, Debt-FAR, Hybrid, then the same five Mutual-Funds
sub-categories, AIF, Total). The parser locates these blocks STRUCTURALLY (by walking the
header rows) rather than assuming fixed column indices, so a genuine NSDL layout change is
reported as PARSE_ERROR instead of silently misreading a different column as a sector's flow.
"""
from __future__ import annotations

import datetime as dt
import re

from ..html_tables import extract_select_options, extract_tables
from ..models import (DEPOSITORY_FORTNIGHTLY, NSDL as SRC_KEY, PARSE_ERROR, RepresentedPeriod,
                      SCHEMA_VERSION, SOURCE_STATED, SOURCE_UNAVAILABLE, SUCCESS,
                      VALIDATION_FAILED, InstitutionalFlowSnapshot)
from ..numeric import parse_number

SELECTION_URL = "https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx"
BASE_URL = "https://www.fpi.nsdl.co.in/web/"
SELECT_ID = "ddlfortnighly"
PARSER_VERSION = "nsdl-fortnightly-1.0"

_VALUE_PATH_RE = re.compile(r"^~/StaticReports/.+\.html?$", re.I)
_LABEL_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$")
_AUC_RE = re.compile(r"^AUC as on\s+(.+)$", re.I)
_NI_RE = re.compile(r"^Net Investment\s+(.+)$", re.I)
# "September 01-15, 2026" / "August 16-31, 2026" - a same-month day range
_PERIOD_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2})-(\d{1,2}),\s*(\d{4})$")
_NOT_A_SECTOR = {"grand total", ""}

# 12 fixed sub-category labels, in order, within one currency half of one block.
_CATEGORY_LABELS = ["Equity", "Debt General Limit", "Debt VRR", "Debt-FAR", "Hybrid",
                    "Equity", "Debt General Limit", "Hybrid", "Solution oriented", "Other",
                    "AIF", "Total"]
_CATEGORY_KEYS = ["equity", "debt_general_limit", "debt_vrr", "debt_far", "hybrid",
                 "mf_equity", "mf_debt_general_limit", "mf_hybrid", "mf_solution_oriented",
                 "mf_other", "aif", "total"]
_BLOCK_WIDTH = 24     # 12 categories x 2 currencies
_N_BLOCKS = 4
_LEAD_COLS = 2        # Sr. No., Sectors


def _parse_label_date(label: str) -> dt.date | None:
    m = _LABEL_RE.match(label.strip())
    if not m:
        return None
    month, day, year = m.group(1)[:3].title(), int(m.group(2)), int(m.group(3))
    try:
        return dt.datetime.strptime(f"{month} {day} {year}", "%b %d %Y").date()
    except ValueError:
        return None


def discover(html: str) -> dict:
    """{'status': SUCCESS|PARSE_ERROR, 'reason', 'options': [(date, url, label), ...] sorted
    newest-first by parsed date}. Never returns a URL the page did not list."""
    raw = extract_select_options(html, SELECT_ID)
    parsed = []
    for value, label in raw:
        if not _VALUE_PATH_RE.match(value.strip()):
            continue
        day = _parse_label_date(label)
        if day is None:
            continue
        url = BASE_URL + value.strip()[2:]   # "~/X" -> "https://.../web/X"
        parsed.append((day, url, label.strip()))
    if len(raw) < 1 or len(parsed) < 2:
        return {"status": PARSE_ERROR, "options": [],
               "reason": f"selection page had {len(raw)} <option>s, {len(parsed)} parseable "
                        f"(select id {SELECT_ID!r} missing or reshaped?)"}
    # A date that resolves to two different URLs is never guessed at - it is dropped entirely,
    # never arbitrarily resolved to "the first one seen". Verified live 2026-10-05: NSDL's own
    # dropdown carries exactly one such historical duplicate ("Feb 28, 2022" -> two different
    # report files), far from the latest/previous fortnight that V1 actually needs.
    by_date: dict = {}
    ambiguous: set = set()
    for day, url, label in parsed:
        if day in by_date and by_date[day][0] != url:
            ambiguous.add(day)
            continue
        by_date[day] = (url, label)
    options = sorted(((d, u, l) for d, (u, l) in by_date.items() if d not in ambiguous),
                     key=lambda t: t[0], reverse=True)
    if len(options) < 2:
        return {"status": PARSE_ERROR, "options": [],
               "reason": f"only {len(options)} unambiguous fortnight(s) after dropping "
                        f"{len(ambiguous)} duplicate date(s) - need at least 2"}
    reason = f"{len(options)} fortnights discovered"
    if ambiguous:
        reason += f"; {len(ambiguous)} duplicate date(s) dropped: {sorted(d.isoformat() for d in ambiguous)}"
    return {"status": SUCCESS, "options": options, "reason": reason,
           "ambiguous_dates": sorted(d.isoformat() for d in ambiguous)}


def _col(block: int, currency: int, category: int) -> int:
    return _LEAD_COLS + block * _BLOCK_WIDTH + currency * 12 + category


def _validate_shape(grid: list) -> str | None:
    """Returns an error string, or None if the 4-header-row / 98-column shape is intact."""
    if len(grid) < 6:
        return f"only {len(grid)} rows - expected 4 header rows + sector rows + a grand total"
    width = len(grid[0])
    if width != _LEAD_COLS + _N_BLOCKS * _BLOCK_WIDTH:
        return f"table is {width} columns wide, expected {_LEAD_COLS + _N_BLOCKS * _BLOCK_WIDTH}"
    currency_row, category_row = grid[1], grid[3]
    for b in range(_N_BLOCKS):
        for c, expect in enumerate(("IN INR Cr.", "IN USD Mn")):
            if currency_row[_col(b, c, 0)] != expect:
                return (f"block {b} currency {c}: expected {expect!r}, got "
                       f"{currency_row[_col(b, c, 0)]!r}")
            for k, label in enumerate(_CATEGORY_LABELS):
                got = category_row[_col(b, c, k)]
                if got != label:
                    return (f"block {b} currency {c} category {k}: expected {label!r}, got "
                           f"{got!r} (NSDL layout change?)")
    return None


def _block_period(text: str) -> dict:
    """One block-header cell -> {'kind': 'auc'|'net_investment', 'date'|'start'/'end'}."""
    m = _AUC_RE.match(text.strip())
    if m:
        day = _parse_label_date(m.group(1))
        return {"kind": "auc", "date": day, "text": text.strip()}
    m = _NI_RE.match(text.strip())
    if m:
        pm = _PERIOD_RE.match(m.group(1).strip())
        if pm:
            month, d1, d2, year = pm.group(1)[:3].title(), int(pm.group(2)), int(pm.group(3)), \
                int(pm.group(4))
            try:
                start = dt.datetime.strptime(f"{month} {d1} {year}", "%b %d %Y").date()
                end = dt.datetime.strptime(f"{month} {d2} {year}", "%b %d %Y").date()
                return {"kind": "net_investment", "start": start, "end": end, "text": text.strip()}
            except ValueError:
                pass
        return {"kind": "net_investment", "start": None, "end": None, "text": text.strip()}
    return {"kind": "unknown", "text": text.strip()}


def parse_report(html: str, expected_end: dt.date, now_iso: str) -> InstitutionalFlowSnapshot:
    """`expected_end` is the fortnight-end date resolved from the selection dropdown for this
    report - the identity the report's OWN "Net Investment <...-end>, <year>" header must agree
    with, else the page does not actually match what was requested."""
    base = dict(schema_version=SCHEMA_VERSION, source=SRC_KEY, source_class=DEPOSITORY_FORTNIGHTLY,
               report_type="fpi_fortnightly_sector", report_key=expected_end.isoformat(),
               report_date=None, data_as_of=None, represented_period=RepresentedPeriod(),
               retrieved_at=now_iso, first_retrieved_at=now_iso, status=PARSE_ERROR,
               source_url="", source_reference="", parser_version=PARSER_VERSION,
               connectivity_status="REACHABLE")
    tables = extract_tables(html)
    if not tables:
        return InstitutionalFlowSnapshot(**base, reason="no table on the NSDL report page")
    grid = tables[0]
    err = _validate_shape(grid)
    if err:
        return InstitutionalFlowSnapshot(**base, reason=f"NSDL table shape: {err}")

    blocks = [_block_period(grid[0][_col(b, 0, 0)]) for b in range(_N_BLOCKS)]
    expected_kinds = ["auc", "net_investment", "net_investment", "auc"]
    if [b["kind"] for b in blocks] != expected_kinds:
        return InstitutionalFlowSnapshot(**base,
            reason=f"unexpected block kinds {[b['kind'] for b in blocks]} (NSDL layout change?)")
    prev_auc, prev_ni, cur_ni, cur_auc = blocks
    if cur_ni.get("end") != expected_end:
        return InstitutionalFlowSnapshot(
            **{**base, "status": VALIDATION_FAILED},
            reason=(f"current-period header ends {cur_ni.get('end')}, expected {expected_end} "
                   f"(dropdown date)"))
    if cur_auc.get("date") != expected_end:
        return InstitutionalFlowSnapshot(
            **{**base, "status": VALIDATION_FAILED},
            reason=f"current AUC date {cur_auc.get('date')} != expected {expected_end}")
    if prev_auc.get("date") != prev_ni.get("end"):
        return InstitutionalFlowSnapshot(
            **{**base, "status": VALIDATION_FAILED},
            reason=(f"previous-period AUC date {prev_auc.get('date')} != previous Net "
                   f"Investment period end {prev_ni.get('end')}"))

    # sector rows: Sr. No. numeric, then rows stop at the first non-numeric Sr. No. (the Grand
    # Total row, whose Sr. No. cell is blank).
    sector_rows, grand_total = [], None
    for row in grid[4:]:
        sr = row[0].strip()
        name = row[1].strip()
        if sr.isdigit():
            sector_rows.append(row)
        elif name.strip().lower() == "grand total" or not sr:
            grand_total = row
            break
    if not sector_rows:
        return InstitutionalFlowSnapshot(**base, reason="no numbered sector rows found")

    def cell(row, block, currency, category_key) -> float | None:
        # "equity" appears twice in _CATEGORY_KEYS (direct + Mutual Funds), so the column index
        # is resolved explicitly rather than via .index(), which would always find the first.
        idx = {"equity": 0, "debt_general_limit": 1, "debt_vrr": 2, "debt_far": 3, "hybrid": 4,
              "mf_equity": 5, "mf_debt_general_limit": 6, "mf_hybrid": 7,
              "mf_solution_oriented": 8, "mf_other": 9, "aif": 10, "total": 11}[category_key]
        return parse_number(row[_col(block, currency, idx)])

    facts, rejected = [], []
    sum_check = {"net_investment_current_equity": 0.0, "net_investment_current_total": 0.0,
                "auc_current_equity": 0.0}
    for row in sector_rows:
        sector = row[1].strip()
        if sector.strip().lower() in _NOT_A_SECTOR:
            continue
        values = {}
        ok = True
        for label, block, currency in (("net_investment_previous", 1, 0),
                                       ("net_investment_current", 2, 0),
                                       ("auc_previous", 0, 0), ("auc_current", 3, 0)):
            for cat in ("equity", "total") if "net_investment" in label else ("equity",):
                v = cell(row, block, currency, cat)
                if v is None:
                    ok = False
                    continue
                values[f"{label}_{cat}"] = v
        if not ok:
            rejected.append({"sector": sector, "reason": "non-numeric cell"})
            continue
        sum_check["net_investment_current_equity"] += values.get("net_investment_current_equity", 0.0)
        sum_check["net_investment_current_total"] += values.get("net_investment_current_total", 0.0)
        sum_check["auc_current_equity"] += values.get("auc_current_equity", 0.0)
        for metric_key, metric, period in (
                ("net_investment_previous_equity", "net_investment", "previous"),
                ("net_investment_current_equity", "net_investment", "current"),
                ("net_investment_previous_total", "net_investment_total", "previous"),
                ("net_investment_current_total", "net_investment_total", "current"),
                ("auc_previous_equity", "auc", "previous"), ("auc_current_equity", "auc", "current")):
            if metric_key in values:
                facts.append({"sector": sector, "metric": metric, "period": period,
                             "category": "equity", "value": values[metric_key],
                             "unit": "INR_CRORE"})

    if grand_total is not None:
        tol = len(sector_rows) * 0.5 + 1
        for metric_key, sum_val in sum_check.items():
            label, block, currency = {
                "net_investment_current_equity": ("net_investment_current", 2, 0),
                "net_investment_current_total": ("net_investment_current", 2, 0),
                "auc_current_equity": ("auc_current", 3, 0)}[metric_key]
            cat = "total" if metric_key.endswith("_total") else "equity"
            gt = cell(grand_total, block, currency, cat)
            if gt is not None and abs(sum_val - gt) > tol:
                return InstitutionalFlowSnapshot(
                    **{**base, "status": VALIDATION_FAILED},
                    reason=(f"sector rows sum to {sum_val:.1f} for {metric_key}, Grand Total "
                           f"says {gt:.1f} (tolerance {tol:.1f})"))

    return InstitutionalFlowSnapshot(
        **{**base, "status": SUCCESS, "report_date": expected_end.isoformat(),
          "data_as_of": expected_end.isoformat(),
          "represented_period": RepresentedPeriod(
              start=cur_ni["start"].isoformat() if cur_ni.get("start") else None,
              end=expected_end.isoformat(), basis=SOURCE_STATED, source_note=cur_ni["text"]),
          "facts": facts, "extra": {"rejected": rejected, "sector_count": len(sector_rows),
                                    "previous_period_label": prev_ni["text"]}},
        reason=f"{len(facts)} facts across {len(sector_rows)} sectors for fortnight ending "
               f"{expected_end.isoformat()}")


def fetch_nsdl_fortnightly(now_iso: str, get=None) -> dict:
    """Selection page -> discover -> fetch latest + previous -> parse both.

    Returns {'latest': InstitutionalFlowSnapshot, 'previous': InstitutionalFlowSnapshot|None,
    'discovery': {...}}. Never falls back to a hard-coded URL or period."""
    from operations.connectivity import classify_exception

    def _get(url):
        if get is not None:
            return get(url)
        import requests
        from market import UA
        headers = {**UA, "Accept": "text/html,application/xhtml+xml"}
        r = requests.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        return r.text

    def _unavailable(url, exc) -> InstitutionalFlowSnapshot:
        return InstitutionalFlowSnapshot(
            schema_version=SCHEMA_VERSION, source=SRC_KEY, source_class=DEPOSITORY_FORTNIGHTLY,
            report_type="fpi_fortnightly_sector", report_key="", report_date=None,
            data_as_of=None, represented_period=RepresentedPeriod(), retrieved_at=now_iso,
            first_retrieved_at=now_iso, status=SOURCE_UNAVAILABLE, source_url=url,
            source_reference=url, parser_version=PARSER_VERSION,
            connectivity_status=classify_exception(exc) if exc else "NOT_ATTEMPTED",
            reason=f"{type(exc).__name__}: {str(exc)[:200]}" if exc else "")

    try:
        sel_html = _get(SELECTION_URL)
    except Exception as exc:
        return {"latest": _unavailable(SELECTION_URL, exc), "previous": None,
               "discovery": {"status": SOURCE_UNAVAILABLE, "options": [], "reason": str(exc)[:200]}}

    disc = discover(sel_html)
    if disc["status"] != SUCCESS:
        snap = InstitutionalFlowSnapshot(
            schema_version=SCHEMA_VERSION, source=SRC_KEY, source_class=DEPOSITORY_FORTNIGHTLY,
            report_type="fpi_fortnightly_sector", report_key="", report_date=None,
            data_as_of=None, represented_period=RepresentedPeriod(), retrieved_at=now_iso,
            first_retrieved_at=now_iso, status=PARSE_ERROR, source_url=SELECTION_URL,
            source_reference=SELECTION_URL, parser_version=PARSER_VERSION,
            connectivity_status="REACHABLE", reason=disc["reason"])
        return {"latest": snap, "previous": None, "discovery": disc}

    options = disc["options"]
    latest_day, latest_url, _ = options[0]
    try:
        latest_html = _get(latest_url)
        latest = parse_report(latest_html, latest_day, now_iso)
        latest.source_url, latest.source_reference = latest_url, latest_url
    except Exception as exc:
        latest = _unavailable(latest_url, exc)

    previous = None
    if len(options) > 1:
        prev_day, prev_url, _ = options[1]
        try:
            prev_html = _get(prev_url)
            previous = parse_report(prev_html, prev_day, now_iso)
            previous.source_url, previous.source_reference = prev_url, prev_url
        except Exception as exc:
            previous = _unavailable(prev_url, exc)

    return {"latest": latest, "previous": previous, "discovery": disc}


__all__ = ["SELECTION_URL", "BASE_URL", "discover", "parse_report", "fetch_nsdl_fortnightly"]
