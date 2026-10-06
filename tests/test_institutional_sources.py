"""Institutional Flow Intelligence V1 - source acquisition: pure parsers against synthetic,
structurally-identical fixtures (never a real NSE/CDSL/NSDL payload - docs/TESTING_GUIDE.md),
plus the store/service layer's change detection (NEW_REPORT / UNCHANGED / REVISED / bootstrap).
"""
import datetime as dt

import pytest

from institutional_flows.models import (CDSL, FAILED, NSDL, NSE, PARSE_ERROR, SOURCE_UNAVAILABLE,
                                        SUCCESS, UNCHANGED as UNCHANGED_STATE,
                                        VALIDATION_FAILED)
from institutional_flows.service import InstitutionalFlowService
from institutional_flows.sources.cdsl import parse_cdsl_daily
from institutional_flows.sources.nse import parse_nse_fii_dii
from institutional_flows.sources.nsdl import discover, parse_report

# Bound at COLLECTION time, before any test's autouse fixtures run - the module attribute these
# names point to gets monkeypatched per-test (tests/conftest.py `_offline_institutional_flows`),
# but these already-bound local names keep referring to the real implementation, which is what
# these tests (exercising the real fetch orchestration against injected fixtures) need.
from institutional_flows.sources.nse import fetch_nse_fii_dii as _real_fetch_nse_fii_dii
from institutional_flows.sources.nsdl import fetch_nsdl_fortnightly as _real_fetch_nsdl_fortnightly

NOW = "2026-10-05T10:00:00+05:30"


# --------------------------------------------------------------------------- 1-3. NSE
def _nse_payload(date="01-Oct-2026", fpi_net="-9569.57", dii_net="5000.00",
                 fpi_buy="17667.90", fpi_sell="27237.47", dii_buy="20000", dii_sell="15000"):
    return [
        {"date": date, "category": "FII/FPI", "buyValue": fpi_buy, "sellValue": fpi_sell,
         "netValue": fpi_net},
        {"date": date, "category": "DII", "buyValue": dii_buy, "sellValue": dii_sell,
         "netValue": dii_net},
    ]


def test_nse_happy_path():
    snap = parse_nse_fii_dii(_nse_payload(), NOW)
    assert snap.status == SUCCESS
    assert snap.report_key == "2026-10-01"
    assert snap.source_class == "EXCHANGE_PROVISIONAL"
    fpi_net = next(f for f in snap.facts if f["participant"] == "FPI" and f["metric"] == "net_investment")
    assert fpi_net["value"] == -9569.57


def test_nse_schema_change_missing_category_is_parse_error():
    payload = [_nse_payload()[0]]   # only FPI, no DII row at all
    snap = parse_nse_fii_dii(payload, NOW)
    assert snap.status == PARSE_ERROR
    assert "missing categories" in snap.reason


def test_nse_arithmetic_mismatch_is_validation_failed():
    snap = parse_nse_fii_dii(_nse_payload(fpi_net="0.00"), NOW)   # buy - sell != 0
    assert snap.status == VALIDATION_FAILED


def test_nse_fetch_unavailable_on_network_error():
    class FakeNSE:
        def get(self, path):
            raise ConnectionError("refused")

    snap = _real_fetch_nse_fii_dii(NOW, nse=FakeNSE())
    assert snap.status == SOURCE_UNAVAILABLE
    assert snap.connectivity_status in ("BLOCKED", "HTTP_ERROR", "TIMEOUT")


# --------------------------------------------------------------------------- 4-7. CDSL
_CDSL_HEADER = ("<tr><th>Reporting Date</th><th>Debt/Debt-VRR/Equity/Hybrid</th>"
               "<th>Investment Route</th><th>Gross Purchases(Rs. Crore)</th>"
               "<th>Gross Sales (Rs. Crore)</th><th>Net Investment (Rs. Crore)</th>"
               "<th>Net Investment US($) million</th><th>Conversion (1 USD TO INR)*</th></tr>")


def _cdsl_html(date="01-OCT-2026", note=True, header=_CDSL_HEADER):
    rows = [
        f"<tr><td>{date}</td><td>Equity</td><td>Stock Exchange</td><td>100</td><td>60</td>"
        "<td>40</td><td>4.0</td><td>95.0</td></tr>",
        f"<tr><td>{date}</td><td>Equity</td><td>Sub-total</td><td>100</td><td>60</td>"
        "<td>40</td><td>4.0</td><td>95.0</td></tr>",
        f"<tr><td>{date}</td><td>Debt</td><td>Stock Exchange</td><td>10</td><td>5</td>"
        "<td>5</td><td>0.5</td><td>95.0</td></tr>",
        f"<tr><td>{date}</td><td>Debt</td><td>Sub-total</td><td>10</td><td>5</td>"
        "<td>5</td><td>0.5</td><td>95.0</td></tr>",
        # CDSL's own merged cell always attaches the grand Total row to the LAST category.
        f"<tr><td>{date}</td><td>Debt</td><td>Total</td><td>110</td><td>65</td>"
        "<td>45</td><td>4.5</td><td>95.0</td></tr>",
    ]
    note_row = ("<tr><td colspan='8'>The data presented above is compiled on the basis of "
               f"reports submitted to depositories by custodians on {date} and constitutes "
               "trades conducted by FIIs/FPIs on and upto the previous trading day(s).</td></tr>"
               if note else "")
    return f"<html><body><table>{header}{''.join(rows)}{note_row}</table></body></html>"


def test_cdsl_happy_path():
    snap = parse_cdsl_daily(_cdsl_html(), NOW)
    assert snap.status == SUCCESS
    assert snap.report_key == "2026-10-01"
    equity_net = next(f for f in snap.facts if f["category"] == "equity"
                      and f["route"] == "stock_exchange" and f["metric"] == "net_investment"
                      and f["unit"] == "INR_CRORE")
    assert equity_net["value"] == 40.0


def test_cdsl_report_date_becomes_the_report_key():
    snap = parse_cdsl_daily(_cdsl_html(date="15-OCT-2026"), NOW)
    assert snap.report_key == "2026-10-15" == snap.report_date == snap.data_as_of


def test_cdsl_represented_period_stays_unknown_and_keeps_source_sentence():
    snap = parse_cdsl_daily(_cdsl_html(), NOW)
    assert snap.represented_period.basis == "UNKNOWN"
    assert snap.represented_period.start is None and snap.represented_period.end is None
    assert "on and upto the previous trading day(s)" in snap.represented_period.source_note


def test_cdsl_missing_note_still_stays_unknown_never_invented():
    snap = parse_cdsl_daily(_cdsl_html(note=False), NOW)
    assert snap.status == SUCCESS
    assert snap.represented_period.basis == "UNKNOWN"
    assert snap.represented_period.source_note == ""


def test_cdsl_schema_change_is_parse_error():
    bad_header = _CDSL_HEADER.replace("Gross Purchases(Rs. Crore)", "Purchases (New Format)")
    snap = parse_cdsl_daily(_cdsl_html(header=bad_header), NOW)
    assert snap.status == PARSE_ERROR
    assert "schema change" in snap.reason


def test_cdsl_arithmetic_mismatch_is_validation_failed():
    # Sub-total row (40) deliberately disagrees with its own Stock Exchange row (purchases -
    # sales = 100 - 60 = 40): changed to claim 999 instead.
    bad = _cdsl_html().replace(
        "<td>Equity</td><td>Sub-total</td><td>100</td><td>60</td><td>40</td>",
        "<td>Equity</td><td>Sub-total</td><td>100</td><td>60</td><td>999</td>")
    assert bad != _cdsl_html()   # the replacement actually matched something
    snap = parse_cdsl_daily(bad, NOW)
    assert snap.status == VALIDATION_FAILED


# --------------------------------------------------------------------------- 8-14. NSDL
def _select_html(options):
    opts = "".join(f'<option value="{v}">{l}</option>' for v, l in options)
    return f'<html><body><select id="ddlfortnighly" name="ddlfortnighly">{opts}</select></body></html>'

_OPTIONS = [
    ("~/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestSector_Sep152026.html",
     "SEP 15, 2026"),
    ("~/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestSector_Aug312026.html",
     "AUG 31, 2026"),
    ("~/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestSector_Aug152026.html",
     "AUG 15, 2026"),
]


def test_nsdl_discovers_latest_and_previous_from_the_dropdown():
    disc = discover(_select_html(_OPTIONS))
    assert disc["status"] == SUCCESS
    assert disc["options"][0][0] == dt.date(2026, 9, 15)
    assert disc["options"][1][0] == dt.date(2026, 8, 31)


def test_nsdl_discovery_is_order_independent():
    shuffled = [_OPTIONS[2], _OPTIONS[0], _OPTIONS[1]]
    disc = discover(_select_html(shuffled))
    assert disc["status"] == SUCCESS
    assert [d for d, _u, _l in disc["options"]] == [dt.date(2026, 9, 15), dt.date(2026, 8, 31),
                                                    dt.date(2026, 8, 15)]


def test_nsdl_duplicate_date_different_url_is_dropped_not_guessed():
    dup = _OPTIONS + [("~/StaticReports/.../FIIInvestSector_Sep152026_v2.html", "SEP 15, 2026")]
    disc = discover(_select_html(dup))
    assert disc["status"] == SUCCESS
    assert dt.date(2026, 9, 15) not in [d for d, _u, _l in disc["options"]]
    assert "2026-09-15" in disc["reason"]


def test_nsdl_too_few_unambiguous_options_is_parse_error():
    disc = discover(_select_html(_OPTIONS[:1]))
    assert disc["status"] == PARSE_ERROR


def test_nsdl_missing_select_is_parse_error():
    disc = discover("<html><body>no dropdown here</body></html>")
    assert disc["status"] == PARSE_ERROR


_SECTORS = [("1", "Healthcare"), ("2", "Information Technology"), ("3", "Sovereign")]


def _nsdl_block(values_by_sector, label):
    """One 24-col block (12 categories x IN INR Cr. / IN USD Mn) for every sector row, all
    categories zero except 'equity' (idx 0) and 'total' (idx 11), which carry `values_by_sector`."""
    header1 = f"<th colspan='24'>{label}</th>"
    header2 = "<th colspan='12'>IN INR Cr.</th><th colspan='12'>IN USD Mn</th>"
    cats = ("Equity", "Debt General Limit", "Debt VRR", "Debt-FAR", "Hybrid", "Equity",
           "Debt General Limit", "Hybrid", "Solution oriented", "Other", "AIF", "Total")
    header3 = "".join(f"<th>{c}</th>" for c in cats) * 2
    rows = []
    for sr, name in _SECTORS:
        equity, total = values_by_sector.get(name, (0, 0))
        inr = [equity, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, total]
        usd = [0] * 12
        rows.append((sr, name, inr + usd))
    return header1, header2, header3, rows


def _nsdl_report_html(current_label="Net Investment September 01-15, 2026",
                      previous_label="Net Investment August 16-31, 2026",
                      cur_auc_label="AUC as on September 15, 2026",
                      prev_auc_label="AUC as on August 31, 2026",
                      current_values=None, previous_values=None):
    current_values = current_values or {"Healthcare": (500, 520), "Information Technology": (-200, -210)}
    previous_values = previous_values or {"Healthcare": (300, 310), "Information Technology": (100, 110)}
    h1a, h2a, h3a, rows_prev_auc = _nsdl_block({}, prev_auc_label)
    h1b, h2b, h3b, rows_prev_ni = _nsdl_block(previous_values, previous_label)
    h1c, h2c, h3c, rows_cur_ni = _nsdl_block(current_values, current_label)
    h1d, h2d, h3d, rows_cur_auc = _nsdl_block({}, cur_auc_label)

    sr_th = "<th>Sr. No.</th><th>Sectors</th>"
    row0 = f"<tr><th></th><th></th>{h1a}{h1b}{h1c}{h1d}</tr>"
    row1 = f"<tr><th></th><th></th>{h2a}{h2b}{h2c}{h2d}</tr>"
    # row 2 (the Equity/Debt/Hybrid/Mutual-Funds supercategory band) is never read by the
    # parser - only its PRESENCE as a 98-wide row matters, so row 1 is reused for its content.
    row2 = f"<tr><th></th><th></th>{h2a}{h2b}{h2c}{h2d}</tr>"
    row3 = f"<tr>{sr_th}{h3a}{h3b}{h3c}{h3d}</tr>"

    body_rows = []
    gt_cur_eq = sum(v[0] for v in current_values.values())
    gt_cur_tot = sum(v[1] for v in current_values.values())
    for i, (sr, name) in enumerate(_SECTORS):
        a_cells = "".join(f"<td>{v}</td>" for v in rows_prev_auc[i][2])
        b_cells = "".join(f"<td>{v}</td>" for v in rows_prev_ni[i][2])
        c_cells = "".join(f"<td>{v}</td>" for v in rows_cur_ni[i][2])
        d_cells = "".join(f"<td>{v}</td>" for v in rows_cur_auc[i][2])
        body_rows.append(f"<tr><td>{sr}</td><td>{name}</td>{a_cells}{b_cells}{c_cells}{d_cells}</tr>")
    gt_inr = [gt_cur_eq, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, gt_cur_tot]
    zeros24 = "".join("<td>0</td>" for _ in range(24))
    cur_block = "".join(f"<td>{v}</td>" for v in gt_inr) + "".join("<td>0</td>" for _ in range(12))
    grand_total = (f"<tr><td></td><td>Grand Total</td>{zeros24}{zeros24}{cur_block}{zeros24}</tr>")
    table = f"<table>{row0}{row1}{row2}{row3}{''.join(body_rows)}{grand_total}</table>"
    return f"<html><body>{table}</body></html>"


def test_nsdl_report_parses_sector_rows():
    html = _nsdl_report_html()
    snap = parse_report(html, dt.date(2026, 9, 15), NOW)
    assert snap.status == SUCCESS, snap.reason
    hc_cur = next(f for f in snap.facts if f["sector"] == "Healthcare"
                 and f["metric"] == "net_investment" and f["period"] == "current")
    assert hc_cur["value"] == 500.0


def test_nsdl_report_date_mismatch_is_validation_failed():
    html = _nsdl_report_html()
    snap = parse_report(html, dt.date(2026, 8, 31), NOW)   # wrong expected end
    assert snap.status == VALIDATION_FAILED


def test_nsdl_malformed_shape_is_parse_error():
    html = "<html><body><table><tr><td>not a real NSDL table</td></tr></table></body></html>"
    snap = parse_report(html, dt.date(2026, 9, 15), NOW)
    assert snap.status == PARSE_ERROR


def test_nsdl_fetch_fails_is_source_unavailable():
    def get(url):
        raise TimeoutError("no response")

    res = _real_fetch_nsdl_fortnightly(NOW, get=get)
    assert res["latest"].status == SOURCE_UNAVAILABLE
    assert res["previous"] is None


def test_nsdl_latest_ok_previous_fetch_fails_independently():
    def get(url):
        if "FPI_Fortnightly_Selection" in url:
            return _select_html(_OPTIONS)
        if "Sep152026" in url:
            return _nsdl_report_html()
        raise ConnectionError("blocked")

    res = _real_fetch_nsdl_fortnightly(NOW, get=get)
    assert res["latest"].status == SUCCESS
    assert res["previous"].status == SOURCE_UNAVAILABLE


# --------------------------------------------------------------------------- 15-16. checksum / change
def test_records_checksum_is_stable_for_identical_facts():
    from institutional_flows.models import records_checksum
    facts = [{"a": 1, "b": 2.0}, {"a": 3}]
    assert records_checksum(facts) == records_checksum(list(facts))
    assert records_checksum(facts) != records_checksum([{"a": 1, "b": 2.1}])


def test_same_report_retrieved_twice_is_unchanged_not_new(tmp_path):
    def nse_fn(now_iso):
        return parse_nse_fii_dii(_nse_payload(), now_iso)

    svc = InstitutionalFlowService(str(tmp_path), nse_fn=nse_fn)
    now = dt.datetime(2026, 10, 5, 10, 0, 0)
    first = svc.capture(now, "REPORT_JOB", sources=(NSE,))
    second = svc.capture(now + dt.timedelta(minutes=10), "REPORT_JOB", sources=(NSE,))
    assert first["results"][NSE]["change_state"] == "NEW_REPORT"
    assert first["results"][NSE]["bootstrap"] is True
    assert second["results"][NSE]["change_state"] == UNCHANGED_STATE
    assert second["results"][NSE]["written"] is False


def test_a_revised_report_writes_a_new_revision_not_a_rewrite(tmp_path):
    calls = {"n": 0}

    def nse_fn(now_iso):
        calls["n"] += 1
        net = "-9569.57" if calls["n"] == 1 else "-9999.00"
        sell = "27237.47" if calls["n"] == 1 else "27666.90"   # buy(17667.90) - sell == net
        return parse_nse_fii_dii(_nse_payload(fpi_net=net, fpi_buy="17667.90", fpi_sell=sell),
                                 now_iso)

    svc = InstitutionalFlowService(str(tmp_path), nse_fn=nse_fn)
    now = dt.datetime(2026, 10, 5, 10, 0, 0)
    r1 = svc.capture(now, "REPORT_JOB", sources=(NSE,))
    r2 = svc.capture(now + dt.timedelta(minutes=10), "REPORT_JOB", sources=(NSE,))
    assert r1["results"][NSE]["change_state"] == "NEW_REPORT"
    assert r2["results"][NSE]["change_state"] == "REVISED"
    from institutional_flows.store import load_revision
    import os
    assert os.path.exists(r1["results"][NSE]["path"])   # revision 1 untouched on disk
    assert os.path.exists(r2["results"][NSE]["path"])
    assert r1["results"][NSE]["path"] != r2["results"][NSE]["path"]
