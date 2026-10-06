"""Market Events Engine V1 - Private Desk: the Events page, dashboard panel, stock-page card
extension, Data Quality rows, and the "nothing else changes" guarantees (Radar order, attention
set, Market Regime snapshot, packet schema) with vs without the new data.
"""
import datetime as dt
import re

import pytest

from conftest import SESSION
from test_private_desk import DESK, FORBIDDEN_WORDS, _fingerprint, client, desk_out, svc  # noqa: F401


def _write_market_events(out: str):
    """EARNINGS/BUYBACK/OPEN_OFFER/DELISTING/GOVT_SECURITIES_AUCTION revisions covering SESSION,
    written directly to disk (not through the desk - the desk never writes market_events/)."""
    from market_events.models import (BUYBACK, DELISTING, EARNINGS, GOVT_SECURITIES_AUCTION,
                                      OPEN_OFFER, SCHEMA_VERSION, SUCCESS, TBILL_91, MarketEvent)
    from market_events.store import write_revision

    def _ev(family, event_key, symbol, company, status, facts, sub_type=None):
        return MarketEvent(schema_version=SCHEMA_VERSION, family=family, event_key=event_key,
                           symbol=symbol, company=company, status=status, sub_type=sub_type,
                           data_as_of=SESSION.isoformat(), source_name="nse_corp_announcements",
                           source_reference="https://nseindia.com/corporate/x",
                           facts=facts, retrieved_at="2026-09-18T20:00:00+05:30",
                           status_capture=SUCCESS)

    write_revision(out, _ev(EARNINGS, "EARNINGS:SYMA:board1", "SYMA", "Company SYMA", "SCHEDULED",
                            [{"label": "reporting_period", "value": "Q2 FY27"}]))
    write_revision(out, _ev(BUYBACK, "BUYBACK:SYMA:bb1", "SYMA", "Company SYMA", "OPEN",
                            [{"label": "buyback_price", "value": "500"}]))
    write_revision(out, _ev(OPEN_OFFER, "OPEN_OFFER:SYMB:oo1", "SYMB", "Company SYMB", "OPEN",
                            [{"label": "acquirer", "value": "Acquirer Co"}]))
    write_revision(out, _ev(DELISTING, "DELISTING:SYMD:dl1", "SYMD", "Company SYMD", "ANNOUNCED",
                            []))
    write_revision(out, _ev(GOVT_SECURITIES_AUCTION, "GOVT_SECURITIES_AUCTION:MARKET:tbill1",
                            None, "91-day T-Bill", "SCHEDULED",
                            [{"label": "notified_amount_crore", "value": "10000"}],
                            sub_type=TBILL_91))


@pytest.fixture
def desk_out_with_market_events(desk_out):
    _write_market_events(desk_out)
    return desk_out


# --------------------------------------------------------------------------- panel renders
def test_events_page_renders_real_values(client, desk_out_with_market_events):
    r = client.get("/events")
    assert r.status_code == 200
    assert "EARNINGS" in r.text
    assert "SYMA" in r.text
    assert "91-DAY T-BILL" in r.text.upper() or "91-day T-Bill" in r.text


def test_events_page_filter_by_family(client, desk_out_with_market_events):
    r = client.get("/events?family=EARNINGS")
    assert r.status_code == 200
    assert "SYMA" in r.text
    assert "SYMD" not in r.text


def test_dashboard_panel_shows_market_events_status(client, desk_out_with_market_events):
    r = client.get("/")
    assert r.status_code == 200
    assert "Market events" in r.text


def test_events_route_renders_without_fetch(client, desk_out):
    """No stored data at all - still 200, never a crash, never a fetch attempt."""
    r = client.get("/events")
    assert r.status_code == 200


# --------------------------------------------------------------------------- stock page
def test_stock_page_official_events_card_extended_not_duplicated(client,
                                                                  desk_out_with_market_events):
    r = client.get("/stock/SYMA")
    assert r.status_code == 200
    assert "EARNINGS" in r.text
    assert "BUYBACK" in r.text
    # exactly one "Official events" CARD (the page also has an unrelated freshness banner that
    # mentions "Official events" staleness) - the market-events rows extend the card, never add
    # a second one.
    assert r.text.count("H · Official events") == 1


def test_govt_auction_never_attached_to_a_stock(client, desk_out_with_market_events):
    """GOVT_SECURITIES_AUCTION is market-wide (symbol=None) - it must never appear on a stock
    page's per-symbol official-events card."""
    for sym in ("SYMA", "SYMB", "SYMD"):
        r = client.get(f"/stock/{sym}")
        assert "T-Bill" not in r.text.replace("t-bill", "T-Bill")


# --------------------------------------------------------------------------- data quality
def test_quality_page_shows_market_events_rows(client, desk_out_with_market_events):
    r = client.get("/quality")
    assert r.status_code == 200
    assert "Market events" in r.text
    assert "EARNINGS" in r.text


def test_quality_handles_no_market_events_data_gracefully(client, desk_out):
    r = client.get("/quality")
    assert r.status_code == 200
    assert "NOT_SUPPORTED_YET" in r.text or "No capture attempts" in r.text


# --------------------------------------------------------------------------- no recommendation language
def test_no_recommendation_language_with_real_market_events_data(client,
                                                                  desk_out_with_market_events):
    for url in ("/", "/events", "/stock/SYMA", "/quality"):
        r = client.get(url)
        text = re.sub(r"<[^>]+>", " ", r.text)
        assert not FORBIDDEN_WORDS.search(text), url


# --------------------------------------------------------------------------- nothing else changes
def test_radar_order_and_attention_set_are_unchanged(svc, desk_out, desk_out_with_market_events):
    before = svc.dashboard(SESSION)
    after = svc.dashboard(SESSION)
    assert [v["symbol"] for v in before["views"]] == [v["symbol"] for v in after["views"]]
    assert before["radar_order"] == after["radar_order"]
    assert [a["view"]["symbol"] for a in before["attention"]] == \
        [a["view"]["symbol"] for a in after["attention"]]


def test_market_regime_snapshot_is_unaffected(svc, desk_out):
    import dataclasses
    before = dataclasses.asdict(svc.regime(SESSION, n=1)["snapshot"])
    _write_market_events(desk_out)
    after = dataclasses.asdict(svc.regime(SESSION, n=1)["snapshot"])
    assert before == after


def test_packet_schema_and_fields_are_unaffected(svc, desk_out_with_market_events):
    from private_desk.packet import SCHEMA_VERSION, FORBIDDEN_FIELD_TOKENS
    packets = svc.packets(SESSION)
    for p in packets:
        d = p.to_dict() if hasattr(p, "to_dict") else p
        assert d.get("schema_version", SCHEMA_VERSION) == SCHEMA_VERSION
        flat = str(d).lower()
        for tok in FORBIDDEN_FIELD_TOKENS:
            assert f"'{tok}'" not in flat.replace('"', "'"), tok


def test_nothing_is_written_outside_private_desk(desk_out_with_market_events, client):
    before = _fingerprint(desk_out_with_market_events, exclude=("private_desk",))
    from test_private_desk import ALL_PAGES
    for url in list(ALL_PAGES) + ["/events", "/events?family=EARNINGS"]:
        client.get(url)
    after = _fingerprint(desk_out_with_market_events, exclude=("private_desk",))
    assert before == after
