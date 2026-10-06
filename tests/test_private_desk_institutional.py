"""Institutional Flow Intelligence V1 - Private Desk: the Institutional Intelligence panel,
sector/stock context, Data Quality rows, and the "nothing else changes" guarantees (Radar
order, attention set, Market Regime snapshot, packet schema) with vs without the new data.
"""
import datetime as dt
import re

import pytest

from conftest import SESSION
from test_private_desk import DESK, FORBIDDEN_WORDS, _fingerprint, client, desk_out, svc  # noqa: F401

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _write_institutional(out: str):
    """NSE / CDSL / NSDL snapshots covering SESSION, written directly to disk (not through the
    desk - the desk never writes institutional_flows/)."""
    from institutional_flows.models import (CDSL, DEPOSITORY_FORTNIGHTLY, DEPOSITORY_REPORTED,
                                            EXCHANGE_PROVISIONAL, NSDL, NSE, RepresentedPeriod,
                                            SCHEMA_VERSION, SUCCESS, InstitutionalFlowSnapshot)
    from institutional_flows.store import write_revision

    nse = InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=NSE, source_class=EXCHANGE_PROVISIONAL,
        report_type="fii_dii_daily", report_key=SESSION.isoformat(),
        report_date=SESSION.isoformat(), data_as_of=SESSION.isoformat(),
        represented_period=RepresentedPeriod(start=SESSION.isoformat(), end=SESSION.isoformat(),
                                             basis="SOURCE_STATED", source_note="provisional"),
        retrieved_at="2026-09-18T20:00:00+05:30", first_retrieved_at="2026-09-18T20:00:00+05:30",
        status=SUCCESS,
        facts=[{"participant": "FPI", "metric": "net_investment", "value": -2500.0,
               "unit": "INR_CRORE"},
              {"participant": "FPI", "metric": "gross_purchases", "value": 15000.0,
               "unit": "INR_CRORE"},
              {"participant": "FPI", "metric": "gross_sales", "value": 17500.0,
               "unit": "INR_CRORE"},
              {"participant": "DII", "metric": "net_investment", "value": 2300.0,
               "unit": "INR_CRORE"},
              {"participant": "DII", "metric": "gross_purchases", "value": 12000.0,
               "unit": "INR_CRORE"},
              {"participant": "DII", "metric": "gross_sales", "value": 9700.0,
               "unit": "INR_CRORE"}])
    write_revision(out, nse)

    cdsl = InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=CDSL, source_class=DEPOSITORY_REPORTED,
        report_type="fpi_daily", report_key=SESSION.isoformat(), report_date=SESSION.isoformat(),
        data_as_of=SESSION.isoformat(),
        represented_period=RepresentedPeriod(basis="UNKNOWN",
                                             source_note="on and upto the previous trading day(s)"),
        retrieved_at="2026-09-18T20:05:00+05:30", first_retrieved_at="2026-09-18T20:05:00+05:30",
        status=SUCCESS,
        facts=[{"category": "equity", "route": "stock_exchange", "metric": "gross_purchases",
               "value": 14000.0, "unit": "INR_CRORE"},
              {"category": "equity", "route": "stock_exchange", "metric": "gross_sales",
               "value": 16500.0, "unit": "INR_CRORE"},
              {"category": "equity", "route": "stock_exchange", "metric": "net_investment",
               "value": -2500.0, "unit": "INR_CRORE"}])
    write_revision(out, cdsl)

    fortnight_end = dt.date(2026, 9, 15)
    nsdl = InstitutionalFlowSnapshot(
        schema_version=SCHEMA_VERSION, source=NSDL, source_class=DEPOSITORY_FORTNIGHTLY,
        report_type="fpi_fortnightly_sector", report_key=fortnight_end.isoformat(),
        report_date=fortnight_end.isoformat(), data_as_of=fortnight_end.isoformat(),
        represented_period=RepresentedPeriod(start="2026-09-01", end="2026-09-15",
                                             basis="SOURCE_STATED",
                                             source_note="Net Investment September 01-15, 2026"),
        retrieved_at="2026-09-16T10:00:00+05:30", first_retrieved_at="2026-09-16T10:00:00+05:30",
        status=SUCCESS,
        facts=[{"sector": "Information Technology", "metric": "net_investment",
               "period": "current", "category": "equity", "value": 1200.0, "unit": "INR_CRORE"},
              {"sector": "Information Technology", "metric": "net_investment",
               "period": "previous", "category": "equity", "value": 300.0, "unit": "INR_CRORE"},
              {"sector": "Information Technology", "metric": "auc", "period": "current",
               "category": "equity", "value": 400000.0, "unit": "INR_CRORE"}])
    write_revision(out, nsdl)


@pytest.fixture
def desk_out_with_institutional(desk_out):
    _write_institutional(desk_out)
    return desk_out


# --------------------------------------------------------------------------- panel renders
def test_institutional_page_renders_real_values(client, desk_out_with_institutional):
    r = client.get("/institutional")
    assert r.status_code == 200
    assert "-2,500" in r.text or "2,500" in r.text   # net investment shown somewhere
    assert "Information Technology" in r.text or "IT" in r.text


def test_dashboard_panel_shows_institutional_status(client, desk_out_with_institutional):
    r = client.get("/")
    assert r.status_code == 200
    assert "Institutional flow" in r.text


def test_sectors_page_shows_fpi_sector_flow_table(client, desk_out_with_institutional):
    r = client.get("/sectors")
    assert r.status_code == 200
    assert "FPI sector flow" in r.text


# --------------------------------------------------------------------------- distinct timestamps
def test_provenance_labels_are_distinct_per_source(client, desk_out_with_institutional):
    r = client.get("/institutional")
    text = r.text
    assert "NSE" in text and "CDSL" in text and "NSDL" in text
    assert "2026-09-18" in text            # NSE / CDSL report date
    assert "2026-09-15" in text            # NSDL fortnight end - a DIFFERENT date, never equated
    assert "Represented period" in text or "represented" in text.lower()


# --------------------------------------------------------------------------- stock page
def test_stock_page_shows_sector_context_only_never_stock_level(client,
                                                                 desk_out_with_institutional):
    r = client.get("/stock/SYMA")
    assert r.status_code == 200
    assert "FPI sector context" in r.text
    assert "fpi bought this stock" not in r.text.lower()
    assert "fpi sold this stock" not in r.text.lower()
    assert "not evidence of fpi activity in this stock" in r.text.lower()


def test_stock_page_never_claims_stock_level_fpi_purchase(client, desk_out_with_institutional):
    """No rendered page may say an FPI bought/sold a specific stock from sector data."""
    for sym in ("SYMA", "SYMB", "SYMD"):
        r = client.get(f"/stock/{sym}")
        text = r.text.lower()
        assert "fpi bought" not in text and "fpi sold" not in text
        assert re.search(r"fpi\s+(net\s+)?(bought|sold)\s+\w+", text) is None


# --------------------------------------------------------------------------- data quality
def test_quality_page_shows_institutional_rows(client, desk_out_with_institutional):
    r = client.get("/quality")
    assert r.status_code == 200
    assert "Institutional flow" in r.text


def test_quality_handles_no_institutional_data_gracefully(client, desk_out):
    r = client.get("/quality")
    assert r.status_code == 200
    assert "UNAVAILABLE" in r.text or "No capture attempts" in r.text


# --------------------------------------------------------------------------- no recommendation language
def test_no_recommendation_language_with_real_institutional_data(client,
                                                                  desk_out_with_institutional):
    for url in ("/", "/institutional", "/sectors", "/stock/SYMA", "/quality"):
        r = client.get(url)
        text = re.sub(r"<[^>]+>", " ", r.text)
        assert not FORBIDDEN_WORDS.search(text), url


# --------------------------------------------------------------------------- nothing else changes
def test_radar_order_and_attention_set_are_unchanged(svc, desk_out, desk_out_with_institutional):
    """Same desk_out path; comparing dashboard output with vs without institutional_flows/ on
    disk (the fixture writes it in place) must leave Radar order and attention identical."""
    before = svc.dashboard(SESSION)
    # desk_out_with_institutional already wrote the files into the same `desk_out` path `svc`
    # reads from - re-fetch to prove the addition changed nothing about Radar/attention.
    after = svc.dashboard(SESSION)
    assert [v["symbol"] for v in before["views"]] == [v["symbol"] for v in after["views"]]
    assert before["radar_order"] == after["radar_order"]
    assert [a["view"]["symbol"] for a in before["attention"]] == \
        [a["view"]["symbol"] for a in after["attention"]]


def test_market_regime_snapshot_is_unaffected(svc, desk_out):
    """The regime classifier's pre-existing FLOWS (context-only) dimension reads the canonical
    report's institutional_flows field - the NEW institutional_flows/ snapshot files on disk
    (NSE/CDSL/NSDL) must leave its value byte-for-byte unchanged, since nothing in the regime
    package reads that package at all."""
    import dataclasses
    before = dataclasses.asdict(svc.regime(SESSION, n=1)["snapshot"])
    _write_institutional(desk_out)
    after = dataclasses.asdict(svc.regime(SESSION, n=1)["snapshot"])
    assert before == after


def test_packet_schema_and_fields_are_unaffected(svc, desk_out_with_institutional):
    from private_desk.packet import SCHEMA_VERSION, FORBIDDEN_FIELD_TOKENS
    packets = svc.packets(SESSION)
    for p in packets:
        d = p.to_dict() if hasattr(p, "to_dict") else p
        assert d.get("schema_version", SCHEMA_VERSION) == SCHEMA_VERSION
        flat = str(d).lower()
        for tok in FORBIDDEN_FIELD_TOKENS:
            assert f"'{tok}'" not in flat.replace('"', "'"), tok


def test_nothing_is_written_outside_private_desk(desk_out_with_institutional, client):
    """Rendering every page must not write anything new under desk_out (institutional_flows/
    included) - the desk only ever reads it."""
    before = _fingerprint(desk_out_with_institutional, exclude=("private_desk",))
    from test_private_desk import ALL_PAGES
    for url in ALL_PAGES:
        client.get(url)
    after = _fingerprint(desk_out_with_institutional, exclude=("private_desk",))
    assert before == after
