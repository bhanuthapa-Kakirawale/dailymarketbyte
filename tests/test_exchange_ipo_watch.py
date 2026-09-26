"""Packets E + F: EXCHANGE WATCH (official exchange events) and IPO WATCH (official primary-market
facts) - parsers on synthetic, structurally identical payloads, validation, selection, the
official-evidence resolver, and the public rules (no Apply/Avoid, no GMP, no listing-gain
prediction, missing data omitted, Gemini never a source)."""
import datetime as dt
import json

import pytest

import exchange_watch as ew
import ipo_watch as iw
from exchange_watch.models import Change, EventFamily
from products import public_fixtures as PF
from publication import PublicationGate, evaluate
from publication.classification import ContentClass, Origin, Scope

D = dt.date(2026, 9, 28)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- F&O ban
def test_fo_ban_parser_reads_the_trade_date_and_symbols():
    r = ew.parse_fo_ban(PF.fo_ban_text(D, ["STOCK-011", "STOCK-042", "M&M"]), "t")
    assert r.status == "OK" and r.list_date == D
    assert [e.symbol for e in r.events] == ["STOCK-011", "STOCK-042", "M&M"]
    assert all(e.family is EventFamily.FNO_BAN and e.data_as_of == D for e in r.events)


@pytest.mark.parametrize("text", ["", "garbage header\n1,ABC", "Securities in Ban For Trade "
                                  "Date 28-SEP-2026:\n1,ABC\nthis is not a row"])
def test_fo_ban_parser_fails_closed(text):
    r = ew.parse_fo_ban(text, "t")
    assert r.status == "INVALID" and r.events == []


def test_empty_ban_list_is_valid_and_shows_nothing():
    r = ew.parse_fo_ban(PF.fo_ban_text(D, []), "t")
    assert r.status == "OK" and r.events == []
    assert ew.build_model([], "PRE") is None


def test_stale_ban_file_is_rejected_never_relabelled():
    r = ew.parse_fo_ban(PF.fo_ban_text(D - dt.timedelta(days=3), ["STOCK-011"]), "t")
    ok, bad = ew.validate(r.events, D)
    assert ok == [] and "expected 2026-09-28" in bad[0]["reason"]


def test_new_is_claimed_only_against_a_stored_previous_list():
    today = ew.parse_fo_ban(PF.fo_ban_text(D, ["A-1", "B-2"]), "t").events
    ok, _ = ew.validate(today, D)
    assert {e.change for e in ew.mark_changes(ok, [])} == {Change.UNKNOWN}
    prev = ew.parse_fo_ban(PF.fo_ban_text(D - dt.timedelta(days=1), ["B-2"]), "t").events
    marked = {e.symbol: e.change for e in ew.mark_changes(ok, prev)}
    assert marked == {"A-1": Change.NEW, "B-2": Change.CONTINUING}


def test_event_store_round_trip_and_previous_lookup(tmp_path):
    prev = ew.parse_fo_ban(PF.fo_ban_text(D - dt.timedelta(days=3), ["B-2"]), "t").events
    ew.save_events(prev, str(tmp_path), D - dt.timedelta(days=3))
    got = ew.load_previous(str(tmp_path), D)
    assert [e.symbol for e in got] == ["B-2"]
    assert ew.load_previous(str(tmp_path), D - dt.timedelta(days=3)) == []   # never same day


# --------------------------------------------------------------------------- surveillance
def test_asm_and_gsm_parsers():
    asm = {"longterm": {"data": [{"asmSurvIndicator": "Stage I", "asmTime": "25-Sep-2026",
                                  "companyName": "Demo Co Ltd", "symbol": "DEMO-1",
                                  "survDesc": "Long Term ASM - Stage I"}]},
           "shortterm": {"data": []}}
    r = ew.parse_asm(asm, "t")
    assert r.status == "OK" and r.events[0].status == "Long-term ASM · Stage I"
    g = ew.parse_gsm([{"symbol": "DEMO-2", "gsmTime": "25-Sep-2026 08:08:02", "gsmStage": "I",
                       "companyName": "Demo Two", "survDesc": "GSM stage I"}], "t")
    assert g.status == "OK" and g.events[0].family is EventFamily.SURVEILLANCE_GSM
    assert ew.parse_asm([], "t").status == "INVALID"
    assert ew.parse_gsm({"x": 1}, "t").status == "INVALID"


def test_surveillance_outside_the_index_universe_is_not_published():
    events = PF.exchange_events(D, "SURVEILLANCE")
    chosen, omitted = ew.select_events(events, PF.universe().symbols())
    assert [e.symbol for e in chosen] == ["STOCK-077"]
    assert any("outside the index universe" in o["reason"] for o in omitted)


def test_selection_order_is_factual_and_capped():
    events = PF.exchange_events(D, "ALL")
    chosen, _ = ew.select_events(events, PF.universe().symbols())
    assert [e.family for e in chosen][:2] == [EventFamily.FNO_BAN, EventFamily.FNO_BAN]
    from exchange_watch.watch import MAX_CARDS
    assert len(chosen) <= MAX_CARDS and len({e.symbol for e in chosen}) == len(chosen)


# --------------------------------------------------------------------------- resolver
def test_radar_only_stock_stays_private_official_event_may_be_named():
    events = PF.exchange_events(D, "FNO_BAN")
    res = ew.OfficialEvidenceResolver(events)
    private = res.resolve("STOCK-001")
    public = res.resolve("STOCK-011")
    assert not private.public and "PRIVATE" in private.reason
    assert public.public and "FNO_BAN" in public.reason


def test_exchange_facts_pass_the_gate_with_factual_wording():
    events = PF.exchange_events(D, "FNO_BAN")
    gate = PublicationGate("PUBLIC_UNREGISTERED", PF.universe().companies())
    assert all(gate.admit(f) for f in ew.exchange_facts(events))
    model = ew.build_model(events, "PRE")
    assert model["headline"] == "2 securities in the F&O ban period"
    assert model["provenance_lines"] == ["SOURCE: NSE", "LIST DATE: 28 SEP 2026"]
    line = model["cards"][0]["line"].lower()
    for banned in ("avoid", "sell", "buy", "fall", "rise", "expected"):
        assert banned not in line


def test_corporate_event_is_an_allowed_official_event():
    ev = PF.exchange_events(D, "CORPORATE")
    gate = PublicationGate("PUBLIC_UNREGISTERED")
    assert all(gate.admit(f) for f in ew.exchange_facts(ev))


# --------------------------------------------------------------------------- IPO parsing
NSE_ROWS = [
    {"companyName": "Demo Alpha Limited", "issueEndDate": "29-Sep-2026",
     "issuePrice": "Rs.258 to Rs.272", "issueSize": "14976743", "issueStartDate": "25-Sep-2026",
     "series": "EQ", "status": "Active", "symbol": "DEMOALPHA", "category": "Total",
     "noOfTime": "1.31"},
    {"companyName": "Demo SME Limited", "issueEndDate": "30-Sep-2026", "issuePrice": "Rs.51 to Rs.54",
     "issueStartDate": "26-Sep-2026", "series": "SME", "symbol": "DEMOSME"},
    {"companyName": "", "issueEndDate": "30-Sep-2026", "issueStartDate": "26-Sep-2026",
     "series": "EQ"},
]


def test_nse_issue_parser_keeps_official_fields_and_drops_bad_rows():
    evs, notes = iw.parse_nse_issues(NSE_ROWS, "CURRENT", dt.date(2026, 9, 29), "t")
    by = {e.symbol: e for e in evs}
    a = by["DEMOALPHA"]
    assert (a.price_band_low, a.price_band_high) == (258.0, 272.0)
    assert a.board_type is iw.BoardType.MAINBOARD and by["DEMOSME"].board_type is iw.BoardType.SME
    assert a.issue_size_crore is None                   # shares are never converted to rupees
    assert a.subscription is None                       # no exchange timestamp -> not published
    assert any("no update timestamp" in n for n in a.notes)
    assert any("dropped row" in n for n in notes)
    assert iw.parse_nse_issues({"not": "a list"}, "CURRENT", D)[0] == []


def test_offer_document_loader_fails_closed(tmp_path):
    good = {"company_name": "DEMO", "symbol": "DEMO", "document_type": "RHP",
            "document_date": "2026-09-18", "source_url": "https://www.sebi.gov.in/filings/x",
            "issue_structure": [{"key": "fresh_issue_crore", "value_crore": 700,
                                 "page_reference": "p. 2"}],
            "financials": [{"label": "Revenue", "period": "FY26", "value_crore": 1.0,
                            "page_reference": "p. 9"}]}
    bad_host = dict(good, company_name="BAD1", source_url="https://ipo-gmp-today.example/x")
    no_page = dict(good, company_name="BAD2", financials=[{"label": "Revenue", "period": "FY26",
                                                          "value_crore": 1.0}])
    p = tmp_path / "docs.json"
    p.write_text(json.dumps({"entries": [good, bad_host, no_page]}), encoding="utf-8")
    entries, audit = iw.load_offer_documents(str(p))
    assert [e["company_name"] for e in entries] == ["DEMO"]
    assert {r["company_name"] for r in audit["rejected"]} == {"BAD1", "BAD2"}
    assert iw.load_offer_documents(str(tmp_path / "missing.json"))[1]["status"] == "UNAVAILABLE"


def test_repo_offer_document_file_is_valid_and_empty():
    entries, audit = iw.load_offer_documents()
    assert audit["status"] == "OK" and entries == [] and audit["rejected"] == []


# --------------------------------------------------------------------------- IPO rules
@pytest.mark.parametrize("scenario,kind", [("OPENS_TODAY", "OPENS_TODAY"),
                                           ("CLOSES_TODAY", "CLOSES_TODAY"),
                                           ("LISTING_DAY", "LISTS_TODAY")])
def test_dated_events_qualify(scenario, kind):
    chosen, _ = iw.select_ipos(PF.ipos(D, scenario), D)
    assert [k for _, k in chosen] == [kind]


def test_ipo_with_no_dated_event_is_not_shown():
    chosen, omitted = iw.select_ipos(PF.ipos(D + dt.timedelta(days=10), "CLOSES_TODAY"), D)
    assert chosen == [] and "no dated event" in omitted[0]["reason"]


def test_closes_today_card_has_official_facts_and_timestamped_bids():
    chosen, _ = iw.select_ipos(PF.ipos(D, "CLOSES_TODAY"), D)
    m = iw.build_model(chosen, "PRE")
    labels = [r["label"] for r in m["card"]["rows"]]
    assert labels == ["PRICE BAND", "BIDDING", "LOT SIZE", "ISSUE", "TOTAL BIDS"]
    assert m["provenance_lines"][1] == "DATA AS OF: 27 SEP 2026 · 5:00 PM IST"
    assert m["provenance_lines"][0] == "ISSUE DATA: NSE · OFFER DOCUMENT: SEBI FILING"


def test_listing_prices_only_after_the_listing_session():
    pre, _ = iw.select_ipos(PF.ipos(D, "LISTING_DAY"), D)                     # morning
    rows = [r["label"] for r in iw.build_model(pre, "PRE")["card"]["rows"]]
    assert "LISTING PRICE" not in rows and "CLOSE" not in rows
    post, _ = iw.select_ipos(PF.ipos(D, "LISTING_DAY"), D, include_listed_session=True)
    m = iw.build_model(post, "POST")
    assert m["card"]["chip"] == "LISTED"
    assert m["card"]["rows"][1]["value"].endswith("(+7.59% vs issue)")


def test_the_same_issue_in_two_nse_lists_is_shown_once():
    evs, _ = iw.parse_nse_issues(NSE_ROWS[:1], "CURRENT", dt.date(2026, 9, 29), "t")
    evs2, _ = iw.parse_nse_issues(NSE_ROWS[:1], "UPCOMING", dt.date(2026, 9, 29), "t")
    chosen, _ = iw.select_ipos(evs + evs2, dt.date(2026, 9, 29))
    assert len(chosen) == 1


def test_plural_headlines_are_grammatical():
    two = [PF.ipos(D, "CLOSES_TODAY")[0], PF.ipos(D, "MISSING_SUBSCRIPTION")[0]]
    chosen, _ = iw.select_ipos(two, D)
    assert iw.build_model(chosen, "PRE")["headline"] == "Two IPOs close today"
    assert iw.build_model(chosen, "POST")["headline"] == "Two IPOs closed for bidding"


def test_board_orders_factually_and_never_says_top():
    chosen, _ = iw.select_ipos(PF.ipos(D, "BOARD"), D)
    m = iw.build_model(chosen, "PRE")
    assert m["layout"] == "BOARD" and "top" not in m["headline"].lower()
    assert [r["chip"] for r in m["rows"]] == ["LISTS TODAY", "CLOSES TODAY", "OPENS TODAY"]


def test_ipo_facts_pass_the_gate_and_the_model_has_no_gmp_field():
    chosen, _ = iw.select_ipos(PF.ipos(D, "CLOSES_TODAY"), D)
    gate = PublicationGate("PUBLIC_UNREGISTERED")
    assert all(gate.admit(f) for f in iw.ipo_facts(chosen))
    fields = set(iw.IPOEvent.__dataclass_fields__)
    for forbidden in ("gmp", "grey_market_premium", "rating", "fair_value", "expected_listing",
                      "score", "recommendation"):
        assert not any(forbidden in f for f in fields)


@pytest.mark.parametrize("text", ["Apply for DEMO IPO A LTD", "Avoid this IPO",
                                  "GMP Rs 45 - strong listing expected", "Expected listing gain 20%",
                                  "Best IPO this month", "DEMO IPO A LTD looks cheap",
                                  "Fair value Rs 400", "Subscribe to the IPO before it closes"])
def test_ipo_recommendation_attempts_are_blocked(text):
    from publication.classification import Orientation, PublishableFact, RightsStatus
    f = PublishableFact(fact_id="x", text=text, scope=Scope.IPO, origin=Origin.OFFICIAL_EXCHANGE,
                        content_class=ContentClass.IPO_EVENT, orientation=Orientation.CURRENT_FACT,
                        source_name="nse_ipo_issues", source_reference="synthetic://x",
                        data_as_of=D, security="DEMO IPO A LTD",
                        publication_rights_status=RightsStatus.REVIEW_REQUIRED)
    assert not evaluate(f, "PUBLIC_UNREGISTERED").allowed


def test_gemini_cannot_be_an_ipo_source():
    from publication.classification import Orientation, PublishableFact, RightsStatus
    f = PublishableFact(fact_id="x", text="DEMO IPO A LTD bids 5.2x", scope=Scope.IPO,
                        origin=Origin.AI, content_class=ContentClass.IPO_EVENT,
                        orientation=Orientation.CURRENT_FACT, source_name="gemini",
                        data_as_of=D, security="DEMO IPO A LTD",
                        publication_rights_status=RightsStatus.RESTRICTED)
    d = evaluate(f, "PUBLIC_UNREGISTERED")
    assert {"AI_NOT_A_SOURCE", "IPO_UNOFFICIAL_SOURCE"} <= set(d.reasons)


def test_ipo_audit_fields():
    chosen, omitted = iw.select_ipos(PF.ipos(D, "BOARD"), D)
    a = iw.ipo_audit(chosen, omitted)
    for k in ("ipo_content_present", "ipo_companies", "ipo_sources", "subscription_data_as_of",
              "official_facts_used"):
        assert k in a
    assert a["subscription_data_as_of"]["DEMO IPO A LTD"].startswith("2026-09-27T17:00")
