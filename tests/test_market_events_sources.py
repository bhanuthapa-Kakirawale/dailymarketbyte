"""Market Events Engine V1 - the acquisition engine itself: store round-trip (immutable,
revisioned, exclusive-create), `MarketEventsService.capture()`'s change detection
(NEW_EVENT/UNCHANGED/REVISED/CANCELLED_CHANGE), and how a family with no verified-reachable
source (`market_events.sources.DEFAULT_FETCHERS`), a parse failure, or an unreachable source is
each handled - never a crash, never an invented event.

No family has a real adapter in this pass (none has been verified reachable from this
environment - see docs/MARKET_EVENTS_ENGINE.md), so every "happy path" test here exercises the
engine with a synthetic fetcher standing in for a future real one; these are deliberately NOT
fixtures of any real NSE/BSE/RBI payload.
"""
import datetime as dt

import pytest

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 10, 6, 19, 30, tzinfo=IST)


def _ev(status="SCHEDULED", facts=None, event_key="EARNINGS:ABC:board1", symbol="ABC"):
    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    return MarketEvent(schema_version=SCHEMA_VERSION, family=EARNINGS, event_key=event_key,
                       symbol=symbol, company="ABC Ltd", status=status, sub_type=None,
                       data_as_of="2026-10-06", source_name="nse_corp_announcements",
                       source_reference="https://nseindia.com/corp/1",
                       facts=facts or [{"label": "reporting_period", "value": "Q2 FY27"}],
                       status_capture=SUCCESS)


# --------------------------------------------------------------------------- store round-trip
def test_write_revision_is_exclusive_create_never_overwrites(tmp_path):
    from market_events.store import load_revision, write_revision
    path1 = write_revision(str(tmp_path), _ev())
    assert load_revision(path1).revision == 1
    path2 = write_revision(str(tmp_path), _ev(facts=[{"label": "reporting_period",
                                                       "value": "Q3 FY27"}]))
    assert path2 != path1
    assert load_revision(path2).revision == 2
    assert load_revision(path1).revision == 1   # the first file is untouched


def test_load_latest_returns_the_highest_revision_per_event_key(tmp_path):
    from market_events.store import load_latest, write_revision
    write_revision(str(tmp_path), _ev(event_key="EARNINGS:ABC:board1"))
    write_revision(str(tmp_path), _ev(event_key="EARNINGS:ABC:board1",
                                      facts=[{"label": "reporting_period", "value": "Q3 FY27"}]))
    write_revision(str(tmp_path), _ev(event_key="EARNINGS:XYZ:board2"))
    latest = load_latest(str(tmp_path), "EARNINGS")
    assert len(latest) == 2
    by_key = {e.event_key: e for e in latest}
    assert by_key["EARNINGS:ABC:board1"].revision == 2
    assert by_key["EARNINGS:ABC:board1"].facts[0]["value"] == "Q3 FY27"


def test_load_latest_filters_by_symbol(tmp_path):
    from market_events.store import load_latest, write_revision
    write_revision(str(tmp_path), _ev(event_key="EARNINGS:ABC:board1", symbol="ABC"))
    write_revision(str(tmp_path), _ev(event_key="EARNINGS:XYZ:board2", symbol="XYZ"))
    assert [e.event_key for e in load_latest(str(tmp_path), "EARNINGS", symbol="ABC")] == \
        ["EARNINGS:ABC:board1"]


# --------------------------------------------------------------------------- change detection
def test_capture_writes_new_event_then_unchanged_then_revised(tmp_path):
    from market_events.models import FamilyFetchResult, SUCCESS
    from market_events.service import MarketEventsService

    calls = {"n": 0}

    def fetch(now_iso):
        calls["n"] += 1
        facts = [{"label": "reporting_period", "value": "Q2 FY27" if calls["n"] < 3 else "Q3 FY27"}]
        return FamilyFetchResult(status=SUCCESS, events=[_ev(facts=facts)])

    svc = MarketEventsService(str(tmp_path), fetchers={"EARNINGS": fetch})
    r1 = svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    assert r1["results"]["EARNINGS"]["events"][0]["change_state"] == "NEW_EVENT"
    r2 = svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    assert r2["results"]["EARNINGS"]["events"][0]["change_state"] == "UNCHANGED"
    r3 = svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    assert r3["results"]["EARNINGS"]["events"][0]["change_state"] == "REVISED"


def test_an_unchanged_capture_never_writes_a_new_revision(tmp_path):
    from market_events.models import FamilyFetchResult, SUCCESS
    from market_events.service import MarketEventsService
    from market_events.store import load_latest

    def fetch(now_iso):
        return FamilyFetchResult(status=SUCCESS, events=[_ev()])

    svc = MarketEventsService(str(tmp_path), fetchers={"EARNINGS": fetch})
    svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    assert load_latest(str(tmp_path), "EARNINGS")[0].revision == 1


def test_a_cancelled_filing_produces_cancelled_change_state(tmp_path):
    from market_events.models import FamilyFetchResult, SUCCESS
    from market_events.service import MarketEventsService

    calls = {"n": 0}

    def fetch(now_iso):
        calls["n"] += 1
        status = "SCHEDULED" if calls["n"] == 1 else "CANCELLED"
        return FamilyFetchResult(status=SUCCESS, events=[_ev(status=status)])

    svc = MarketEventsService(str(tmp_path), fetchers={"EARNINGS": fetch})
    svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    r2 = svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    assert r2["results"]["EARNINGS"]["events"][0]["change_state"] == "CANCELLED_CHANGE"


def test_bootstrap_flag_set_only_on_the_very_first_event_of_a_family(tmp_path):
    from market_events.models import FamilyFetchResult, SUCCESS
    from market_events.service import MarketEventsService
    from market_events.store import load_latest

    def fetch(now_iso):
        return FamilyFetchResult(status=SUCCESS, events=[_ev()])

    svc = MarketEventsService(str(tmp_path), fetchers={"EARNINGS": fetch})
    r1 = svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    assert r1["results"]["EARNINGS"]["events"][0]["bootstrap"] is True
    assert load_latest(str(tmp_path), "EARNINGS")[0].bootstrap is True


# --------------------------------------------------------------------------- failure handling
def test_parse_error_is_recorded_and_never_written(tmp_path):
    from market_events.models import FamilyFetchResult, PARSE_ERROR
    from market_events.service import MarketEventsService
    from market_events.store import load_latest

    def fetch(now_iso):
        return FamilyFetchResult(status=PARSE_ERROR, events=[], reason="unexpected table shape")

    svc = MarketEventsService(str(tmp_path), fetchers={"EARNINGS": fetch})
    res = svc.capture(NOW, "REPORT_JOB", families=("EARNINGS",))
    assert res["results"]["EARNINGS"]["status"] == PARSE_ERROR
    assert load_latest(str(tmp_path), "EARNINGS") == []


def test_not_supported_yet_every_family_by_default(tmp_path):
    """EARNINGS (P2A), OFS (P2B), BUYBACK (P2C), OPEN_OFFER (P2D) and DELISTING (P2E) are now
    the five exceptions with real, live-calling fetchers; IPO's stub stays NOT_SUPPORTED_YET-
    shaped by design (it is never acquired by this engine - see its own reworded reason string)
    and every other family is still genuinely unsupported. EARNINGS/OFS/BUYBACK/OPEN_OFFER/
    DELISTING are excluded from this capture specifically so this test never makes a live
    network call (tests/ stays fully offline) - see each adapter's own section further down
    this file for its offline, fixture-based coverage."""
    from market_events.models import ALL_FAMILIES, BUYBACK, DELISTING, EARNINGS, OFS, OPEN_OFFER
    from market_events.service import MarketEventsService
    from market_events.sources import DEFAULT_FETCHERS

    assert not getattr(DEFAULT_FETCHERS[EARNINGS], "not_supported_yet", False)
    assert not getattr(DEFAULT_FETCHERS[OFS], "not_supported_yet", False)
    assert not getattr(DEFAULT_FETCHERS[BUYBACK], "not_supported_yet", False)
    assert not getattr(DEFAULT_FETCHERS[OPEN_OFFER], "not_supported_yet", False)
    assert not getattr(DEFAULT_FETCHERS[DELISTING], "not_supported_yet", False)

    svc = MarketEventsService(str(tmp_path))
    still_unsupported = tuple(f for f in ALL_FAMILIES
                              if f not in (EARNINGS, OFS, BUYBACK, OPEN_OFFER, DELISTING))
    res = svc.capture(NOW, "REPORT_JOB", families=still_unsupported)
    for family in still_unsupported:
        assert res["results"][family]["status"] == "NOT_SUPPORTED_YET"
        assert res["results"][family]["events"] == []


# --------------------------------------------------------------------------- EARNINGS adapter
# Fixtures below are literal shapes from REAL rows pulled live from
# https://www.nseindia.com/api/corporate-board-meetings?index=equities this session (2026-10-06)
# - never re-fetched during a test run.
_BM_RESULTS_ROW = {
    "bm_symbol": "STLTECH", "bm_date": "23-Oct-2026", "bm_purpose": "Board Meeting Intimation",
    "bm_desc": "STERLITE TECHNOLOGIES LIMITED has informed the Exchange about Board Meeting to "
              "be held on 23-Oct-2026 to inter-alia consider and approve the Unaudited "
              "Financial results of the Company for the Half Yearly ended September 2026 .",
    "sm_name": "Sterlite Technologies Limited", "sm_isin": "INE089C01029",
    "bm_timestamp": "06-Oct-2026 19:35:16", "oriiginalMeetingDate": None, "proposedMeetingDate": None,
}
_BM_OTHER_BUSINESS_ROW = {
    "bm_symbol": "BALLARPUR", "bm_date": "09-Oct-2026", "bm_purpose": "Board Meeting Intimation",
    "bm_desc": "BALLARPUR INDUSTRIES LIMITED has informed the Exchange about Board Meeting to "
              "be held on 09-Oct-2026 to consider Other business.",
    "sm_name": "Ballarpur Industries Limited", "sm_isin": "INE294A01037",
    "bm_timestamp": "06-Oct-2026 19:35:34",
}
_NOW_ISO = "2026-10-06T19:40:00+05:30"


def test_earnings_adapter_classifies_results_board_meeting():
    from market_events.sources.earnings import parse_board_meetings
    events, notes = parse_board_meetings([_BM_RESULTS_ROW], _NOW_ISO)
    assert len(events) == 1
    ev = events[0]
    assert ev.symbol == "STLTECH" and ev.company == "Sterlite Technologies Limited"
    assert ev.data_as_of == "2026-10-23" and ev.status == "SCHEDULED"
    assert ev.family == "EARNINGS"


def test_earnings_adapter_rejects_non_results_board_meeting():
    from market_events.sources.earnings import parse_board_meetings
    events, notes = parse_board_meetings([_BM_OTHER_BUSINESS_ROW], _NOW_ISO)
    assert events == []     # "to consider Other business" never mentions financial results


def test_earnings_adapter_reporting_period_unknown_when_not_stated():
    from market_events.sources.earnings import parse_board_meetings
    events, _ = parse_board_meetings([_BM_RESULTS_ROW], _NOW_ISO)
    facts = {f["label"]: f["value"] for f in events[0].facts}
    assert facts["reporting_period"] == "UNKNOWN"     # real row states no explicit Q/H/FY token


def test_earnings_adapter_reporting_period_extracted_when_explicit_token_present():
    from market_events.sources.earnings import parse_board_meetings
    row = dict(_BM_RESULTS_ROW, bm_desc=_BM_RESULTS_ROW["bm_desc"].replace(
        "Half Yearly ended September 2026", "quarter ended September 2026 (Q2 FY27)"))
    events, _ = parse_board_meetings([row], _NOW_ISO)
    facts = {f["label"]: f["value"] for f in events[0].facts}
    assert facts["reporting_period"] == "Q2FY27"


def test_earnings_adapter_drops_row_with_no_company_name_and_no_sentence_match():
    from market_events.sources.earnings import parse_board_meetings
    row = {"bm_symbol": "XYZ", "bm_date": "23-Oct-2026", "bm_purpose": "x",
          "bm_desc": "to consider and approve the Financial results."}   # no stated company
    events, notes = parse_board_meetings([row], _NOW_ISO)
    assert events == []
    assert any("no company name stated" in n for n in notes)


def test_earnings_adapter_dedupes_duplicate_symbol_rows_within_one_fetch():
    from market_events.sources.earnings import parse_board_meetings
    events, _ = parse_board_meetings([_BM_RESULTS_ROW, dict(_BM_RESULTS_ROW)], _NOW_ISO)
    assert len(events) == 1


def test_earnings_adapter_source_unavailable_on_network_exception():
    from market_events.sources.earnings import fetch_earnings

    class FailingNSE:
        def get(self, path):
            raise ConnectionError("blocked")

    res = fetch_earnings(_NOW_ISO, nse=FailingNSE())
    assert res.status == "SOURCE_UNAVAILABLE"
    assert res.events == []


def test_earnings_adapter_parse_error_on_non_list_payload():
    from market_events.sources.earnings import fetch_earnings

    class BadShapeNSE:
        def get(self, path):
            return {"not": "a list"}

    res = fetch_earnings(_NOW_ISO, nse=BadShapeNSE())
    assert res.status == "PARSE_ERROR"


def test_earnings_adapter_success_with_zero_events_is_not_a_failure():
    """Reachable, zero qualifying rows today - healthy SUCCESS+[], never NOT_SUPPORTED_YET."""
    from market_events.sources.earnings import fetch_earnings

    class EmptyTodayNSE:
        def get(self, path):
            return [_BM_OTHER_BUSINESS_ROW]      # real shape, but none is results-qualifying

    res = fetch_earnings(_NOW_ISO, nse=EmptyTodayNSE())
    assert res.status == "SUCCESS"
    assert res.events == []


def test_earnings_adapter_reschedule_reuses_event_key_as_revised_date():
    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.sources.earnings import parse_board_meetings

    prior = MarketEvent(schema_version=SCHEMA_VERSION, family=EARNINGS,
                        event_key="EARNINGS:STLTECH:abc123", symbol="STLTECH",
                        company="Sterlite Technologies Limited", status="SCHEDULED",
                        sub_type=None, data_as_of="2026-10-20", source_name="nse_corp_board_meetings",
                        source_reference="x", first_retrieved_at="2026-10-01T10:00:00+05:30",
                        status_capture=SUCCESS)

    def lookup_fn(family, symbol):
        return prior if symbol == "STLTECH" else None

    events, _ = parse_board_meetings([_BM_RESULTS_ROW], _NOW_ISO, lookup_fn=lookup_fn)
    assert events[0].event_key == "EARNINGS:STLTECH:abc123"   # reused, not a new key
    assert events[0].status == "REVISED_DATE"                  # date differs from prior


def test_earnings_adapter_new_cycle_mints_new_key_when_no_open_event():
    from market_events.sources.earnings import parse_board_meetings

    def lookup_fn(family, symbol):
        return None

    events, _ = parse_board_meetings([_BM_RESULTS_ROW], _NOW_ISO, lookup_fn=lookup_fn)
    assert events[0].event_key.startswith("EARNINGS:STLTECH:")
    assert events[0].status == "SCHEDULED"


# --------------------------------------------------------------------------- OFS adapter (P2B)
# Fixtures below are literal shapes from REAL rows pulled live from
# https://www.nseindia.com/api/live-ofs-active-issues (confirmed empty: {"data": []} - field
# names for a populated row are taken from NSE's own frontend script, upcoming-ipo.js, since
# no OFS was active when this was built - official evidence, documented in core/sources.py)
# and https://www.nseindia.com/api/live-ofs-past-issues (471 real historical records) this
# session (2026-10-06) - never re-fetched during a test run.
_OFS_PAST_ROW = {
    "allocatePrice": "120", "allocatePriceGeneral": "-", "allocatePriceRetail": "120",
    "allocatedQty": "48600000", "category": "GENERAL",
    "companyName": "Sustainable Energy Infra Trust", "floorPrice": "120",
    "methodology": "Multiple", "noOfTimes": "      1.03", "noOfshareOffered": "48600000",
    "offerDate": "24-Sep-2026", "symbol": "SEITINVITCUMU", "sr_no": 1,
}
_OFS_ACTIVE_ROW = {   # shape per upcoming-ipo.js's own field reads - not an observed live row
    "symbol": "DEMOOFS", "series": "EQ", "companyName": "Demo OFS Ltd",
    "ofsStartDate": "06-Oct-2026", "ofsEndDate": "07-Oct-2026", "floorPrice": "250",
    "status": "Active",
}


def _ofs_payload(active_rows=(), past_rows=()):
    class FakeNSE:
        def get(self, path):
            if "active" in path:
                return {"data": list(active_rows)}
            return {"data": list(past_rows)}
    return FakeNSE()


def test_ofs_adapter_every_row_is_ofs_by_construction_no_text_classification():
    """Unlike EARNINGS, nothing here is filtered by text - the source IS the OFS listing."""
    from market_events.sources.ofs import fetch_ofs
    res = fetch_ofs("2026-09-28T19:00:00+05:30", nse=_ofs_payload(past_rows=[_OFS_PAST_ROW]))
    assert res.status == "SUCCESS"
    assert len(res.events) == 1
    assert res.events[0].family == "OFS"


def test_ofs_adapter_normalizes_past_issue_fields():
    from market_events.sources.ofs import fetch_ofs
    res = fetch_ofs("2026-09-28T19:00:00+05:30", nse=_ofs_payload(past_rows=[_OFS_PAST_ROW]))
    ev = res.events[0]
    assert ev.symbol == "SEITINVITCUMU" and ev.company == "Sustainable Energy Infra Trust"
    assert ev.status == "COMPLETED" and ev.data_as_of == "2026-09-24"
    facts = {f["label"]: f["value"] for f in ev.facts}
    assert facts["floor_price"] == "120" and facts["category"] == "GENERAL"


def test_ofs_adapter_past_issue_event_key_is_stable_and_natural():
    """No lookup needed for a completed record - the key comes straight from the source's own
    symbol/date/category, never a sequence number this pipeline assigns."""
    from market_events.sources.ofs import fetch_ofs
    res = fetch_ofs("2026-09-28T19:00:00+05:30", nse=_ofs_payload(past_rows=[_OFS_PAST_ROW]))
    assert res.events[0].event_key == "OFS:SEITINVITCUMU:2026-09-24-GENERAL"


def test_ofs_adapter_old_past_issue_outside_recent_window_is_dropped():
    from market_events.sources.ofs import fetch_ofs
    old_row = dict(_OFS_PAST_ROW, offerDate="01-Jan-2026")
    res = fetch_ofs("2026-10-06T19:00:00+05:30", nse=_ofs_payload(past_rows=[old_row]))
    assert res.events == []


def test_ofs_adapter_active_row_opens_today_then_closes_on_end_date():
    from market_events.sources.ofs import fetch_ofs
    nse = _ofs_payload(active_rows=[_OFS_ACTIVE_ROW])
    on_open = fetch_ofs("2026-10-06T09:00:00+05:30", nse=nse)
    assert on_open.events[0].data_as_of == "2026-10-06" and on_open.events[0].status == "OPEN"
    on_close = fetch_ofs("2026-10-07T09:00:00+05:30", nse=nse)
    assert on_close.events[0].data_as_of == "2026-10-07" and on_close.events[0].status == "CLOSED"


def test_ofs_adapter_active_row_reschedule_reuses_event_key():
    from market_events.models import OFS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.sources.ofs import fetch_ofs
    prior = MarketEvent(schema_version=SCHEMA_VERSION, family=OFS, event_key="OFS:DEMOOFS:xyz1",
                        symbol="DEMOOFS", company="Demo OFS Ltd", status="ANNOUNCED",
                        sub_type=None, data_as_of="2026-10-05", source_name="nse_ofs_live",
                        source_reference="x", first_retrieved_at="2026-10-01T10:00:00+05:30",
                        status_capture=SUCCESS)

    def lookup_fn(family, symbol):
        return prior if symbol == "DEMOOFS" else None

    res = fetch_ofs("2026-10-06T09:00:00+05:30",
                    nse=_ofs_payload(active_rows=[_OFS_ACTIVE_ROW]), lookup_fn=lookup_fn)
    assert res.events[0].event_key == "OFS:DEMOOFS:xyz1"


def test_ofs_adapter_source_unavailable_on_network_exception():
    from market_events.sources.ofs import fetch_ofs

    class FailingNSE:
        def get(self, path):
            raise ConnectionError("blocked")

    res = fetch_ofs("2026-10-06T09:00:00+05:30", nse=FailingNSE())
    assert res.status == "SOURCE_UNAVAILABLE"


def test_ofs_adapter_parse_error_on_schema_drift():
    """Every active row missing the fields NSE's own frontend reads -> fails closed, never a
    guessed mapping."""
    from market_events.sources.ofs import fetch_ofs
    res = fetch_ofs("2026-10-06T09:00:00+05:30",
                    nse=_ofs_payload(active_rows=[{"unexpected": "shape"}]))
    assert res.status == "PARSE_ERROR"


def test_ofs_adapter_empty_active_and_past_is_healthy_success():
    """Confirmed live shape: reachable, {"data": []} - never NOT_SUPPORTED_YET."""
    from market_events.sources.ofs import fetch_ofs
    res = fetch_ofs("2026-10-06T09:00:00+05:30", nse=_ofs_payload())
    assert res.status == "SUCCESS"
    assert res.events == []


def test_ofs_source_defaults_to_review_required_rights():
    from core.sources import SRC_NSE_OFS
    from publication.rights import rights_for
    assert rights_for(SRC_NSE_OFS).status.value == "REVIEW_REQUIRED"


# --------------------------------------------------------------------------- BUYBACK adapter (P2C)
# Fixtures below are literal shapes from REAL rows pulled live from NSE's own
# corporates-corporateActions / corporate-announcements / corporates-daily-buyback endpoints
# this session (2026-10-07) - never re-fetched during a test run. Company/text details (Gandhi
# Special Tubes' tender price/quantity, SIS's open-market route, PVR INOX's closure, SIS/Emami's
# daily disclosure) are the real examples found during discovery; wording is trimmed to the
# sentence that carries the explicit fact, never paraphrased into a different claim.
_CA_ROW_FAIRCHEM = {
    "comp": "Fairchem Organics Limited", "symbol": "FAIRCHEMOR", "isin": "INE0DNW01011",
    "subject": "Buy Back", "recDate": "05-Jan-2026", "exDate": "05-Jan-2026", "faceVal": "10",
    "series": "EQ", "bcStartDate": "-", "bcEndDate": "-",
}
_CA_ROW_NON_BUYBACK = {
    "comp": "XYZ Corp Ltd", "symbol": "XYZCORP", "isin": "INE000X00011", "subject": "Dividend",
    "recDate": "-", "exDate": "-", "faceVal": "-",
}
_ANN_ROW_GANDHI_TENDER = {
    "symbol": "GANDHITUBE", "sm_name": "Gandhi Special Tubes Ltd",
    "desc": "Public Announcement - Buyback of Shares",
    "attchmntText": ("The Company proposes to buyback up to 8,68,100 Equity Shares through "
                     "the tender offer route at a price of Rs. 900/- per Equity Share."),
    "an_dt": "01-Jul-2026 18:00:00",
}
_ANN_ROW_SIS_OPEN_MARKET = {
    "symbol": "SIS", "sm_name": "SIS Limited", "desc": "Buyback",
    "attchmntText": "The Company announces a buyback through the open market route.",
    "an_dt": "15-Jul-2026 10:00:00",
}
_ANN_ROW_PVRINOX_CLOSURE = {
    "symbol": "PVRINOX", "sm_name": "PVR INOX Ltd", "desc": "Closure of Buy Back",
    "attchmntText": "The buyback has closed; the extinguishment of shares is complete.",
    "an_dt": "01-Oct-2026 10:00:00",
}
_ANN_ROW_WITHDRAWN = {
    "symbol": "WDSTOCK", "sm_name": "Withdrawn Buyback Ltd", "desc": "Buyback",
    "attchmntText": "The Company has decided to withdraw the proposed buyback of shares.",
    "an_dt": "10-Aug-2026 10:00:00",
}
_DAILY_ROW_SIS = {"symbol": "SIS", "sm_name": "SIS Limited", "an_dt": "06-Oct-2026 18:00:00"}


def _buyback_payload(ca_rows=(), ann_rows=(), daily_rows=()):
    class FakeNSE:
        def get(self, path):
            if "corporateActions" in path:
                return list(ca_rows)
            if "daily-buyback" in path:
                return {"data": list(daily_rows)}
            return list(ann_rows)
    return FakeNSE()


def test_buyback_adapter_classifies_exact_subject_buy_back():
    """`subject` is an exact categorical match - never a free-text search."""
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-07T19:00:00+05:30",
                        nse=_buyback_payload(ca_rows=[_CA_ROW_FAIRCHEM, _CA_ROW_NON_BUYBACK]))
    assert res.status == "SUCCESS"
    assert len(res.events) == 1
    assert res.events[0].symbol == "FAIRCHEMOR"


def test_buyback_adapter_normalizes_corporate_action_fields():
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-07T19:00:00+05:30", nse=_buyback_payload(ca_rows=[_CA_ROW_FAIRCHEM]))
    ev = res.events[0]
    assert ev.company == "Fairchem Organics Limited" and ev.status == "ANNOUNCED"
    assert ev.data_as_of == "2026-01-05"
    facts = {f["label"]: f["value"] for f in ev.facts}
    assert facts["record_date"] == "05-Jan-2026" and facts["ex_date"] == "05-Jan-2026"
    assert facts["face_value"] == "10"


def test_buyback_adapter_route_price_quantity_extracted_when_explicit():
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-07-02T19:00:00+05:30",
                        nse=_buyback_payload(ann_rows=[_ANN_ROW_GANDHI_TENDER]))
    ev = res.events[0]
    facts = {f["label"]: f["value"] for f in ev.facts}
    assert facts["route"] == "TENDER"
    assert facts["buyback_price"] == "900"
    assert facts["shares_offered"] == "868100"


def test_buyback_adapter_open_market_route_extracted_price_absent_when_not_stated():
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-07-16T19:00:00+05:30",
                        nse=_buyback_payload(ann_rows=[_ANN_ROW_SIS_OPEN_MARKET]))
    ev = res.events[0]
    facts = {f["label"]: f["value"] for f in ev.facts}
    assert facts["route"] == "OPEN_MARKET"
    assert "buyback_price" not in facts and "shares_offered" not in facts


def test_buyback_adapter_closure_announcement_sets_completed_status():
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-02T19:00:00+05:30",
                        nse=_buyback_payload(ann_rows=[_ANN_ROW_PVRINOX_CLOSURE]))
    assert res.events[0].status == "COMPLETED"


def test_buyback_adapter_daily_disclosure_sets_open_status():
    """An ongoing daily purchase disclosure is itself explicit proof of OPEN, not an inference."""
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-06T19:00:00+05:30",
                        nse=_buyback_payload(ann_rows=[_ANN_ROW_SIS_OPEN_MARKET],
                                             daily_rows=[_DAILY_ROW_SIS]))
    assert res.events[0].status == "OPEN"


def test_buyback_adapter_withdrawn_status_from_explicit_text():
    """No live example was seen during discovery - fixture-tested only, per the plan."""
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-08-11T19:00:00+05:30",
                        nse=_buyback_payload(ann_rows=[_ANN_ROW_WITHDRAWN]))
    assert res.events[0].status == "WITHDRAWN"


def test_buyback_adapter_event_key_reused_across_two_filings_same_cycle():
    """Real example: TeamLease Services filed a Public Announcement (01-Jul-2026) then a Letter
    of Offer (07-Jul-2026) for the SAME buyback - a second filing must reuse the open event's key."""
    from market_events.models import BUYBACK, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.sources.buyback import fetch_buyback
    prior = MarketEvent(schema_version=SCHEMA_VERSION, family=BUYBACK,
                        event_key="BUYBACK:TEAMLEASE:abc123", symbol="TEAMLEASE",
                        company="TeamLease Services Ltd", status="ANNOUNCED", sub_type=None,
                        data_as_of="2026-07-01", source_name="nse_corporate_actions",
                        source_reference="x", first_retrieved_at="2026-07-01T19:00:00+05:30",
                        status_capture=SUCCESS)

    def lookup_fn(family, symbol):
        return prior if symbol == "TEAMLEASE" else None

    ann_row = {"symbol": "TEAMLEASE", "sm_name": "TeamLease Services Ltd",
              "desc": "Public Announcement - Buyback of Shares",
              "attchmntText": "Letter of Offer filed for the ongoing buyback.",
              "an_dt": "07-Jul-2026 12:00:00"}
    res = fetch_buyback("2026-07-07T19:00:00+05:30",
                        nse=_buyback_payload(ann_rows=[ann_row]), lookup_fn=lookup_fn)
    assert res.events[0].event_key == "BUYBACK:TEAMLEASE:abc123"


def test_buyback_adapter_new_cycle_mints_new_key_when_no_open_event():
    """Mirrors the EARNINGS/OFS terminal-filter behaviour: when `_lookup_open_event` finds no
    non-terminal event (e.g. the prior cycle is COMPLETED), a fresh key is minted."""
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-07T19:00:00+05:30",
                        nse=_buyback_payload(ca_rows=[_CA_ROW_FAIRCHEM]),
                        lookup_fn=lambda family, symbol: None)
    assert res.events[0].event_key.startswith("BUYBACK:FAIRCHEMOR:")
    assert res.events[0].event_key != "BUYBACK:TEAMLEASE:abc123"


def test_buyback_adapter_duplicate_rows_same_symbol_collapsed():
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-07T19:00:00+05:30",
                        nse=_buyback_payload(ca_rows=[_CA_ROW_FAIRCHEM, dict(_CA_ROW_FAIRCHEM)]))
    assert len(res.events) == 1


def test_buyback_adapter_source_unavailable_on_network_exception():
    from market_events.sources.buyback import fetch_buyback

    class FailingNSE:
        def get(self, path):
            raise ConnectionError("blocked")

    res = fetch_buyback("2026-10-07T19:00:00+05:30", nse=FailingNSE())
    assert res.status == "SOURCE_UNAVAILABLE"


def test_buyback_adapter_secondary_source_failure_does_not_fail_whole_fetch():
    """The announcements/daily-buyback feeds are supplementary - a failure there never fails
    the primary corporateActions-backed fetch."""
    from market_events.sources.buyback import fetch_buyback

    class PartialNSE:
        def get(self, path):
            if "corporateActions" in path:
                return [_CA_ROW_FAIRCHEM]
            raise ConnectionError("blocked")

    res = fetch_buyback("2026-10-07T19:00:00+05:30", nse=PartialNSE())
    assert res.status == "SUCCESS"
    assert len(res.events) == 1


def test_buyback_adapter_parse_error_on_non_list_payload():
    from market_events.sources.buyback import fetch_buyback

    class FakeNSE:
        def get(self, path):
            if "corporateActions" in path:
                return {"unexpected": "shape"}
            return []

    res = fetch_buyback("2026-10-07T19:00:00+05:30", nse=FakeNSE())
    assert res.status == "PARSE_ERROR"


def test_buyback_adapter_parse_error_on_schema_drift():
    """Every row missing the 'subject' key NSE's own frontend reads -> fails closed, never a
    guessed mapping."""
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-07T19:00:00+05:30",
                        nse=_buyback_payload(ca_rows=[{"unexpected": "shape"}]))
    assert res.status == "PARSE_ERROR"


def test_buyback_adapter_empty_payload_is_healthy_success():
    """Confirmed live reachable, zero-matching-row shape -> SUCCESS+[], never NOT_SUPPORTED_YET."""
    from market_events.sources.buyback import fetch_buyback
    res = fetch_buyback("2026-10-07T19:00:00+05:30", nse=_buyback_payload())
    assert res.status == "SUCCESS"
    assert res.events == []


def test_buyback_source_defaults_to_review_required_rights():
    from core.sources import SRC_NSE_CORPORATE_ACTIONS
    from publication.rights import rights_for
    assert rights_for(SRC_NSE_CORPORATE_ACTIONS).status.value == "REVIEW_REQUIRED"


# ---------------------------------------------------------------------- OPEN_OFFER adapter (P2D)
# Fixtures below are literal shapes from REAL rows pulled live from NSE's own
# corporate-announcements endpoint this session (2026-10-07), filtered to
# desc == "Public Announcement-Open Offer" - never re-fetched during a test run. Company/text
# details (G-TEC JAINX's same-cycle PA+DPS, Shankara's offer-opening corrigendum, Niraj's
# post-offer advertisement, Bliss GVS's acquirer-named addendum) are the real examples found
# during discovery; wording is trimmed to the sentence that carries the explicit fact, never
# paraphrased into a different claim.
_OO_ROW_GTEC_PA = {
    "symbol": "GTECJAINX", "sm_name": "G-TEC JAINX EDUCATION LIMITED",
    "desc": "Public Announcement-Open Offer",
    "attchmntText": ("Navigant Corporate Advisors Limited has Submitted to the Exchange a copy "
                     "of Public Announcement under Regulation 3(1) and 3(3) read with "
                     "Regulations 13, 14 and 15(1) of the SEBI (SAST) Regulations, 2011."),
    "an_dt": "29-Sep-2026 11:21:17",
}
_OO_ROW_GTEC_DPS = {
    "symbol": "GTECJAINX", "sm_name": "G-TEC JAINX EDUCATION LIMITED",
    "desc": "Public Announcement-Open Offer",
    "attchmntText": ("Navigant Corporate Advisors Limited has Submitted to the Exchange a copy "
                     "of detailed public statement to the shareholders of G-TEC JAINX EDUCATION "
                     "LIMITED (Target Company)."),
    "an_dt": "30-Sep-2026 11:31:03",
}
_OO_ROW_SHANKARA_PA = {
    "symbol": "SHANKARA", "sm_name": "Shankara Building Products Limited",
    "desc": "Public Announcement-Open Offer",
    "attchmntText": ("Corporate Professionals Capital Private Ltd has submitted to the Exchange "
                     "a copy of Public Announcement under Regulation 3(1) read with Regulation "
                     "3(3), Regulation 3(2) and Regulation 4 of SEBI (SAST) Regulations, 2011."),
    "an_dt": "15-Jul-2026 19:22:10",
}
_OO_ROW_SHANKARA_OPENING = {
    "symbol": "SHANKARA", "sm_name": "Shankara Building Products Limited",
    "desc": "Public Announcement-Open Offer",
    "attchmntText": ("Corporate Professionals Capital Private Limited has Submitted to the "
                     "Exchange a copy of offer opening public announcement and corrigendum to "
                     "the detailed public statement under Regulation 18(7) of SEBI (SAST) "
                     "Regulations, 2011 for the attention of the shareholders of the Shankara "
                     "Building Products Limited (Target Company)."),
    "an_dt": "04-Sep-2026 12:15:23",
}
_OO_ROW_NIRAJ_POST_OFFER = {
    "symbol": "NIRAJ", "sm_name": "Niraj Cement Structurals Limited",
    "desc": "Public Announcement-Open Offer",
    "attchmntText": ("Navigant Corporate Advisors Limited has Submitted to the Exchange a copy "
                     "of post offer advertisement in terms of Regulation 18(12) of SEBI SAST "
                     "(Regulations), 2011 of Niraj Cement Structurals Limited (Target Company)."),
    "an_dt": "20-Aug-2026 12:22:21",
}
_OO_ROW_BLISSGVS_ACQUIRER = {
    "symbol": "BLISSGVS", "sm_name": "Bliss GVS Pharma Limited",
    "desc": "Public Announcement-Open Offer",
    "attchmntText": ("SBI Capital Markets Limited has informed to the Exchange about Addendum "
                     "to Public Announcement and Detailed Public Statement for the Open Offer "
                     "made by Anupam Rasayan Limited, the acquirer for the addition of PAC."),
    "an_dt": "20-Jul-2026 12:54:09",
}
_OO_ROW_ACQUISITION_NON_OPEN_OFFER = {
    "symbol": "XYZCORP", "sm_name": "XYZ Corp Ltd", "desc": "Acquisition",
    "attchmntText": "XYZ Corp Ltd has acquired a 30% stake in ABC Ltd.",
    "an_dt": "01-Oct-2026 10:00:00",
}
_OO_ROW_SAST_DISCLOSURE_NON_OPEN_OFFER = {
    "symbol": "XYZCORP", "sm_name": "XYZ Corp Ltd",
    "desc": "Disclosure under SEBI Takeover Regulations",
    "attchmntText": "Disclosure of change in shareholding under Regulation 29(1).",
    "an_dt": "01-Oct-2026 10:00:00",
}
_OO_ROW_WITHDRAWN = {
    "symbol": "WDOFFER", "sm_name": "Withdrawn Offer Ltd",
    "desc": "Public Announcement-Open Offer",
    "attchmntText": "The acquirer has announced the withdrawal of the open offer.",
    "an_dt": "10-Aug-2026 10:00:00",
}


def _oo_payload(rows=()):
    class FakeNSE:
        def get(self, path):
            return list(rows)
    return FakeNSE()


def test_open_offer_adapter_classifies_exact_desc_public_announcement_open_offer():
    """`desc` is an exact categorical match - never a free-text search."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-10-07T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]))
    assert res.status == "SUCCESS"
    assert len(res.events) == 1
    assert res.events[0].symbol == "GTECJAINX"


def test_open_offer_adapter_rejects_generic_acquisition_and_sast_disclosure_rows():
    """Generic 'Acquisition'/'Disclosure under SEBI Takeover Regulations' desc values are never
    classified as OPEN_OFFER - only the exact category is."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-10-07T19:00:00+05:30",
                           nse=_oo_payload([_OO_ROW_ACQUISITION_NON_OPEN_OFFER,
                                           _OO_ROW_SAST_DISCLOSURE_NON_OPEN_OFFER]))
    assert res.status == "SUCCESS"
    assert res.events == []


def test_open_offer_adapter_normalizes_symbol_and_company():
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-10-07T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]))
    ev = res.events[0]
    assert ev.symbol == "GTECJAINX"
    assert ev.company == "G-TEC JAINX EDUCATION LIMITED"


def test_open_offer_adapter_acquirer_parsed_when_explicitly_stated():
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-07-20T19:00:00+05:30",
                           nse=_oo_payload([_OO_ROW_BLISSGVS_ACQUIRER]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert facts["acquirer"] == "Anupam Rasayan Limited"


def test_open_offer_adapter_acquirer_absent_when_not_explicitly_stated():
    """The GTEC PA row names only the filing intermediary - never guessed as the acquirer."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-09-29T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert "acquirer" not in facts


def test_open_offer_adapter_price_shares_percentage_dates_absent_never_fabricated():
    """None of these fields are stated in the feed's one-line text - they must stay absent,
    never a guessed/plausible value."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-09-29T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    for label in ("offer_price", "shares_offered", "percentage_sought", "offer_open_date",
                 "offer_close_date"):
        assert label not in facts


def test_open_offer_adapter_event_key_stable_across_two_calls_with_same_lookup():
    from market_events.models import OPEN_OFFER, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.sources.open_offer import fetch_open_offer
    prior = MarketEvent(schema_version=SCHEMA_VERSION, family=OPEN_OFFER,
                        event_key="OPEN_OFFER:GTECJAINX:fixedkey", symbol="GTECJAINX",
                        company="G-TEC JAINX EDUCATION LIMITED", status="ANNOUNCED",
                        sub_type=None, data_as_of="2026-09-29", source_name="x",
                        source_reference="x", first_retrieved_at="2026-09-29T19:00:00+05:30",
                        status_capture=SUCCESS)
    lookup_fn = lambda family, symbol: prior if symbol == "GTECJAINX" else None
    res1 = fetch_open_offer("2026-09-29T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]),
                           lookup_fn=lookup_fn)
    res2 = fetch_open_offer("2026-09-29T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]),
                           lookup_fn=lookup_fn)
    assert res1.events[0].event_key == res2.events[0].event_key == "OPEN_OFFER:GTECJAINX:fixedkey"


def test_open_offer_adapter_multi_filing_same_cycle_reuses_event_key():
    """Real example: G-TEC JAINX EDUCATION filed a Public Announcement (29-Sep-2026) then a
    Detailed Public Statement the very next day - the SAME cycle, same event_key."""
    from market_events.models import OPEN_OFFER, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.sources.open_offer import fetch_open_offer
    prior = MarketEvent(schema_version=SCHEMA_VERSION, family=OPEN_OFFER,
                        event_key="OPEN_OFFER:GTECJAINX:abc123", symbol="GTECJAINX",
                        company="G-TEC JAINX EDUCATION LIMITED", status="ANNOUNCED",
                        sub_type=None, data_as_of="2026-09-29", source_name="x",
                        source_reference="x", first_retrieved_at="2026-09-29T19:00:00+05:30",
                        status_capture=SUCCESS,
                        facts=[{"label": "stage_evidence", "value": "ANNOUNCED"}])
    res = fetch_open_offer("2026-09-30T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_DPS]),
                           lookup_fn=lambda family, symbol: prior if symbol == "GTECJAINX" else None)
    assert res.events[0].event_key == "OPEN_OFFER:GTECJAINX:abc123"


def test_open_offer_adapter_offer_opening_signal_advances_status_and_never_regresses():
    """Real example: Shankara Building Products filed a Public Announcement (15-Jul-2026) then
    an offer-opening announcement/corrigendum under Regulation 18(7) (04-Sep-2026) - the SAME
    cycle, 51 days later; status advances ANNOUNCED -> OPEN and a later run that only re-sees
    the older PA must never regress it back."""
    from market_events.models import OPEN_OFFER, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.sources.open_offer import fetch_open_offer

    res1 = fetch_open_offer("2026-07-15T19:00:00+05:30", nse=_oo_payload([_OO_ROW_SHANKARA_PA]))
    assert res1.events[0].status == "ANNOUNCED"
    stored = res1.events[0]

    res2 = fetch_open_offer("2026-09-04T19:00:00+05:30",
                            nse=_oo_payload([_OO_ROW_SHANKARA_OPENING]),
                            lookup_fn=lambda family, symbol: stored if symbol == "SHANKARA" else None)
    assert res2.events[0].status == "OPEN"
    assert res2.events[0].event_key == stored.event_key
    advanced = res2.events[0]

    # A later run that only re-sees the OLDER PA filing (e.g. a re-fetched window) must never
    # downgrade the cycle back to ANNOUNCED.
    res3 = fetch_open_offer("2026-09-05T19:00:00+05:30", nse=_oo_payload([_OO_ROW_SHANKARA_PA]),
                            lookup_fn=lambda family, symbol: advanced if symbol == "SHANKARA" else None)
    assert res3.events[0].status == "OPEN"


def test_open_offer_adapter_post_offer_advertisement_sets_completed_status():
    """Regulation 18(12) is literally the SAST post-offer-advertisement regulation - an explicit
    completion signal, never inferred."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-08-20T19:00:00+05:30",
                           nse=_oo_payload([_OO_ROW_NIRAJ_POST_OFFER]))
    assert res.events[0].status == "COMPLETED"


def test_open_offer_adapter_withdrawn_status_from_explicit_text():
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-08-10T19:00:00+05:30", nse=_oo_payload([_OO_ROW_WITHDRAWN]))
    assert res.events[0].status == "WITHDRAWN"


def test_open_offer_adapter_two_filings_in_same_window_collapse_to_one_event():
    """G-TEC JAINX's PA and DPS both fall inside a single fetch call's window (1 day apart) -
    must still be ONE event, not two."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-09-30T19:00:00+05:30",
                           nse=_oo_payload([_OO_ROW_GTEC_PA, _OO_ROW_GTEC_DPS]))
    assert len(res.events) == 1
    assert res.events[0].symbol == "GTECJAINX"


def test_open_offer_adapter_new_cycle_mints_new_key_when_prior_is_terminal():
    """Mirrors the EARNINGS/OFS/BUYBACK terminal-filter behaviour: when `lookup_fn` finds no
    non-terminal event (the prior cycle is COMPLETED/WITHDRAWN and so is filtered out by
    `_lookup_open_event` before this adapter is even called), a fresh key is minted - a
    genuinely new open offer for the same company is never merged into the old cycle."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-10-07T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]),
                           lookup_fn=lambda family, symbol: None)
    assert res.events[0].event_key.startswith("OPEN_OFFER:GTECJAINX:")
    assert res.events[0].event_key != "OPEN_OFFER:GTECJAINX:abc123"


def test_open_offer_adapter_duplicate_identical_row_fetched_twice_is_same_event():
    """Re-fetching the SAME filing twice (the rolling lookback window re-covers it) must not
    create two different event representations - the service layer's checksum dedup is what
    turns this into UNCHANGED; the adapter itself must at least be deterministic/idempotent."""
    from market_events.sources.open_offer import fetch_open_offer
    res1 = fetch_open_offer("2026-09-29T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]))
    res2 = fetch_open_offer("2026-09-29T19:00:00+05:30", nse=_oo_payload([_OO_ROW_GTEC_PA]))
    assert res1.events[0].to_dict() == res2.events[0].to_dict()


def test_open_offer_adapter_source_unavailable_on_network_exception():
    from market_events.sources.open_offer import fetch_open_offer

    class FailingNSE:
        def get(self, path):
            raise ConnectionError("blocked")

    res = fetch_open_offer("2026-10-07T19:00:00+05:30", nse=FailingNSE())
    assert res.status == "SOURCE_UNAVAILABLE"


def test_open_offer_adapter_parse_error_on_non_list_payload():
    from market_events.sources.open_offer import fetch_open_offer

    class FakeNSE:
        def get(self, path):
            return {"unexpected": "shape"}

    res = fetch_open_offer("2026-10-07T19:00:00+05:30", nse=FakeNSE())
    assert res.status == "PARSE_ERROR"


def test_open_offer_adapter_parse_error_on_schema_drift():
    """Every row missing the 'desc' key NSE's own feed carries -> fails closed, never a
    guessed mapping."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-10-07T19:00:00+05:30",
                           nse=_oo_payload([{"unexpected": "shape"}]))
    assert res.status == "PARSE_ERROR"


def test_open_offer_adapter_empty_payload_is_healthy_success():
    """Confirmed live reachable, zero-matching-row shape -> SUCCESS+[], never NOT_SUPPORTED_YET."""
    from market_events.sources.open_offer import fetch_open_offer
    res = fetch_open_offer("2026-10-07T19:00:00+05:30", nse=_oo_payload())
    assert res.status == "SUCCESS"
    assert res.events == []


def test_open_offer_source_defaults_to_review_required_rights():
    from core.sources import SRC_NSE_SAST_ANNOUNCEMENTS
    from publication.rights import rights_for
    assert rights_for(SRC_NSE_SAST_ANNOUNCEMENTS).status.value == "REVIEW_REQUIRED"


# ------------------------------------------------------------------------ DELISTING adapter (P2E)
# Fixtures below are literal shapes from REAL rows pulled live from NSE's own
# corporate-announcements endpoint this session (2026-10-07, a 365-day pull returned 17 rows
# under desc in {"Delisting","Voluntary Delisting"}) - never re-fetched during a test run.
# Wording is trimmed to the sentence that carries the explicit fact, never paraphrased into a
# different claim. Two rows (KEI, FEDDERELEC's Nov-2025 filing) are real, confirmed mismatches
# between NSE's own `desc` tag and its `attchmntText` - kept as the false-positive-guard fixture.
_DL_ROW_ATCOM_COMPULSORY_BSE = {
    "symbol": "ATCOM", "sm_name": "Atcom Technologies Limited", "desc": "Delisting",
    "attchmntText": ("Members of the Exchange are hereby informed about the Compulsory "
                     "Delisting of Equity Shares of Atcom Technologies Limited w.e.f. "
                     "September 02, 2026, in terms of Rule 21(2)(b) of the Securities "
                     "Contracts (Regulations) Rules 1957 which has been compulsorily "
                     "delisted by BSE Limited."),
    "an_dt": "31-Aug-2026 19:42:26",
}
_DL_ROW_IZMO_VOLUNTARY_CSE = {
    "symbol": "IZMO", "sm_name": "IZMO Limited", "desc": "Voluntary Delisting",
    "attchmntText": ("IZMO Limited has informed the Exchange about Voluntary Delisting from "
                     "the Calcutta Stock Exchange Limited (\"CSE\")."),
    "an_dt": "14-Aug-2026 17:37:09",
}
_DL_ROW_JINDALPHOT_VOLUNTARY_BARE = {
    "symbol": "JINDALPHOT", "sm_name": "Jindal Photo Limited", "desc": "Voluntary Delisting",
    "attchmntText": "Jindal Photo Limited has informed the Exchange about Voluntary Delisting",
    "an_dt": "16-Jul-2026 20:09:41",
}
_DL_ROW_JPASSOCIAT_NCLT = {
    "symbol": "JPASSOCIAT", "sm_name": "Jaiprakash Associates Limited", "desc": "Delisting",
    "attchmntText": ("Members of the Exchange are hereby informed about the Delisting of "
                     "Equity shares of Jaiprakash Associates Limited w.e.f. June 18, 2026, "
                     "pursuant to Resolution plan approved by Hon'ble National Company Law "
                     "Tribunal (NCLT), Allahabad Bench, Prayagraj, under section 30(6) read "
                     "with section 31 of Insolvency Bankruptcy Code, 2016."),
    "an_dt": "11-Jun-2026 18:51:18",
}
_DL_ROW_HERCULES_VOLUNTARY_REG56 = {
    "symbol": "HERCULES", "sm_name": "Hercules Investments Limited", "desc": "Delisting",
    "attchmntText": ("Members of the Exchange are hereby informed about the Delisting of "
                     "Equity shares of Hercules Investments Limited w.e.f. January 09, 2026, "
                     "pursuant to Voluntary Delisting application under Regulation 5 and 6 of "
                     "SEBI (Delisting of Equity Shares) Regulations, 2021."),
    "an_dt": "19-Dec-2025 16:11:01",
}
_DL_ROW_GAMMONIND_WITHDRAWN = {
    "symbol": "GAMMONIND", "sm_name": "Gammon India Limited", "desc": "Delisting",
    "attchmntText": ("Members of the Exchange are hereby informed about the withdrawal of "
                     "delisting of Equity Shares of Gammon India Limited (GAMMONIND) w.e.f. "
                     "February 25, 2026, as per the Hon'ble Securities Appellate Tribunal "
                     "(SAT), Mumbai, order dated February 19, 2026, to restore the listing of "
                     "the Company."),
    "an_dt": "25-Feb-2026 15:52:08",
}
_DL_ROW_KEI_MISTAGGED = {
    "symbol": "KEI", "sm_name": "KEI Industries Limited", "desc": "Voluntary Delisting",
    "attchmntText": ("KEI Industries Limited has informed the Exchange about Outcome of "
                     "Board Meeting - Disclosure/ Announcements pursuant to Regulation 30 "
                     "and 33 of SEBI (Listing Obligations and Disclosures Requirements) "
                     "Regulations, 2015."),
    "an_dt": "21-Jan-2026 17:42:56",
}
_DL_ROW_HITECHCORP_DUP1 = {
    "symbol": "HITECHCORP", "sm_name": "Hitech Corporation Limited", "desc": "Voluntary Delisting",
    "attchmntText": "Hitech Corporation Limited has informed the Exchange about Voluntary Delisting",
    "an_dt": "09-Jun-2026 19:53:26",
}
_DL_ROW_HITECHCORP_DUP2 = {
    "symbol": "HITECHCORP", "sm_name": "Hitech Corporation Limited", "desc": "Voluntary Delisting",
    "attchmntText": "Hitech Corporation Limited has informed the Exchange about Voluntary Delisting",
    "an_dt": "09-Jun-2026 20:05:36",
}
_DL_ROW_ACQUISITION_NON_DELISTING = {
    "symbol": "XYZCORP", "sm_name": "XYZ Corp Ltd", "desc": "Acquisition",
    "attchmntText": "XYZ Corp Ltd has acquired a 30% stake in ABC Ltd.",
    "an_dt": "01-Oct-2026 10:00:00",
}
# Synthetic (not live-observed): exercises the desc-fallback branch of type classification for a
# hypothetical terse re-filing whose own text happens not to repeat the word "voluntary" - the
# `desc` category itself is still an explicit NSE classification, so this is not an inference.
_DL_ROW_SYNTHETIC_TERSE_VOLUNTARY = {
    "symbol": "TERSECO", "sm_name": "Terse Co Limited", "desc": "Voluntary Delisting",
    "attchmntText": "Terse Co Limited has informed the Exchange about its Delisting process.",
    "an_dt": "01-Oct-2026 10:00:00",
}


def _dl_payload(rows=()):
    class FakeNSE:
        def get(self, path):
            return list(rows)
    return FakeNSE()


def test_delisting_adapter_classifies_exact_desc_values():
    """desc in {"Delisting","Voluntary Delisting"} - never a free-text search."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    assert res.status == "SUCCESS"
    assert len(res.events) == 1
    assert res.events[0].symbol == "ATCOM"


def test_delisting_adapter_rejects_generic_non_delisting_desc():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-10-07T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ACQUISITION_NON_DELISTING]))
    assert res.status == "SUCCESS"
    assert res.events == []


def test_delisting_adapter_false_positive_text_guard_rejects_mistagged_row():
    """Real, confirmed live: KEI Industries carries desc=="Voluntary Delisting" but its
    attchmntText is a plain board-meeting disclosure with no delisting wording at all - the
    desc tag alone must never be trusted."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-01-21T19:00:00+05:30", nse=_dl_payload([_DL_ROW_KEI_MISTAGGED]))
    assert res.status == "SUCCESS"
    assert res.events == []
    assert "KEI" in res.reason
    assert "mistagged" in res.reason or "doesn't mention delisting" in res.reason


def test_delisting_adapter_normalizes_symbol_and_company():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    ev = res.events[0]
    assert ev.symbol == "ATCOM"
    assert ev.company == "Atcom Technologies Limited"


def test_delisting_adapter_compulsory_type_from_explicit_text():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert facts["delisting_type"] == "COMPULSORY"


def test_delisting_adapter_voluntary_type_from_explicit_text():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-08-14T19:00:00+05:30", nse=_dl_payload([_DL_ROW_IZMO_VOLUNTARY_CSE]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert facts["delisting_type"] == "VOLUNTARY"


def test_delisting_adapter_voluntary_type_from_regulation_5_6_phrase():
    """Hercules Investments: text never uses the bare word "voluntary" in isolation from the
    regulation citation - "Voluntary Delisting application under Regulation 5 and 6" is still
    an explicit statement."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-01-09T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_HERCULES_VOLUNTARY_REG56]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert facts["delisting_type"] == "VOLUNTARY"


def test_delisting_adapter_voluntary_type_from_desc_when_text_is_terse():
    """Synthetic: desc=="Voluntary Delisting" is itself an explicit NSE classification, used
    when the filing's own terse text happens not to repeat the word."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-10-01T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_SYNTHETIC_TERSE_VOLUNTARY]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert facts["delisting_type"] == "VOLUNTARY"


def test_delisting_adapter_unknown_type_for_nclt_resolution_plan_rows():
    """Real example: Jaiprakash Associates' delisting is pursuant to an NCLT-approved
    Resolution Plan under the Insolvency and Bankruptcy Code - neither "voluntary" nor
    "compulsory" is stated, so type must stay UNKNOWN rather than being guessed."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-06-20T19:00:00+05:30", nse=_dl_payload([_DL_ROW_JPASSOCIAT_NCLT]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert facts["delisting_type"] == "UNKNOWN"
    assert facts["mechanism"] == "NCLT_RESOLUTION_PLAN"


def test_delisting_adapter_mechanism_absent_when_not_nclt():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert "mechanism" not in facts


def test_delisting_adapter_other_exchange_captured_when_named():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert facts["other_exchange"] == "BSE"

    res2 = fetch_delisting("2026-08-14T19:00:00+05:30", nse=_dl_payload([_DL_ROW_IZMO_VOLUNTARY_CSE]))
    facts2 = {f["label"]: f["value"] for f in res2.events[0].facts}
    assert facts2["other_exchange"] == "CSE"


def test_delisting_adapter_other_exchange_absent_when_not_named():
    """No other exchange is named in Jaiprakash Associates' filing - must never be assumed to
    be "NSE" by default; the fact is simply absent."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-06-20T19:00:00+05:30", nse=_dl_payload([_DL_ROW_JPASSOCIAT_NCLT]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    assert "other_exchange" not in facts


def test_delisting_adapter_withdrawn_status_from_explicit_text():
    """Real example: Gammon India's delisting was reversed by a SAT order restoring the
    listing - an explicit reversal, mapped to WITHDRAWN even though the row's own desc is just
    "Delisting"."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-02-25T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_GAMMONIND_WITHDRAWN]))
    assert res.events[0].status == "WITHDRAWN"


def test_delisting_adapter_effective_date_in_future_sets_scheduled_status():
    """ATCOM's own filing (31-Aug-2026) states the delisting takes effect "w.e.f. September 02,
    2026" - captured one day before that date, the event is still pending, never already
    COMPLETED."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-08-31T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    assert res.events[0].status == "SCHEDULED"


def test_delisting_adapter_effective_date_passed_sets_completed_status():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    assert res.events[0].status == "COMPLETED"


def test_delisting_adapter_announced_status_when_no_effective_date_stated():
    """Real example: Jindal Photo's filing states only that voluntary delisting was informed -
    no "w.e.f." date at all, so the event stays ANNOUNCED rather than guessing a stage."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-07-16T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_JINDALPHOT_VOLUNTARY_BARE]))
    assert res.events[0].status == "ANNOUNCED"


def test_delisting_adapter_price_dates_absent_never_fabricated():
    """None of these fields are stated anywhere in this feed's text - must stay absent, never
    a guessed/plausible value."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    facts = {f["label"]: f["value"] for f in res.events[0].facts}
    for label in ("floor_price", "exit_price", "discovered_price", "bidding_open_date",
                 "bidding_close_date", "shareholder_approval_date", "exchange_approval_date"):
        assert label not in facts


def test_delisting_adapter_event_key_stable_across_two_calls_with_same_lookup():
    from market_events.models import DELISTING, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.sources.delisting import fetch_delisting
    prior = MarketEvent(schema_version=SCHEMA_VERSION, family=DELISTING,
                        event_key="DELISTING:ATCOM:fixedkey", symbol="ATCOM",
                        company="Atcom Technologies Limited", status="SCHEDULED",
                        sub_type=None, data_as_of="2026-08-31", source_name="x",
                        source_reference="x", first_retrieved_at="2026-08-31T19:00:00+05:30",
                        status_capture=SUCCESS,
                        facts=[{"label": "stage_evidence", "value": "SCHEDULED"},
                              {"label": "delisting_type", "value": "COMPULSORY"}])
    lookup_fn = lambda family, symbol: prior if symbol == "ATCOM" else None
    res1 = fetch_delisting("2026-09-03T19:00:00+05:30",
                           nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]), lookup_fn=lookup_fn)
    res2 = fetch_delisting("2026-09-03T19:00:00+05:30",
                           nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]), lookup_fn=lookup_fn)
    assert res1.events[0].event_key == res2.events[0].event_key == "DELISTING:ATCOM:fixedkey"


def test_delisting_adapter_multi_filing_same_cycle_advances_status_and_never_regresses():
    """SCHEDULED -> COMPLETED as the effective date passes must reuse the same event_key; a
    later run that only re-sees the OLDER (still-SCHEDULED-looking) filing must never regress
    an already-COMPLETED cycle back to SCHEDULED."""
    from market_events.sources.delisting import fetch_delisting

    res1 = fetch_delisting("2026-08-31T19:00:00+05:30",
                           nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    assert res1.events[0].status == "SCHEDULED"
    stored = res1.events[0]

    res2 = fetch_delisting("2026-09-03T19:00:00+05:30",
                           nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]),
                           lookup_fn=lambda family, symbol: stored if symbol == "ATCOM" else None)
    assert res2.events[0].status == "COMPLETED"
    assert res2.events[0].event_key == stored.event_key
    advanced = res2.events[0]

    res3 = fetch_delisting("2026-09-04T19:00:00+05:30",
                           nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]),
                           lookup_fn=lambda family, symbol: advanced if symbol == "ATCOM" else None)
    assert res3.events[0].status == "COMPLETED"


def test_delisting_adapter_two_filings_in_same_window_collapse_to_one_event():
    """Real example: Hitech Corporation filed two identical same-day Voluntary Delisting
    notices - must still be ONE event, not two."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-06-09T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_HITECHCORP_DUP1, _DL_ROW_HITECHCORP_DUP2]))
    assert len(res.events) == 1
    assert res.events[0].symbol == "HITECHCORP"


def test_delisting_adapter_withdrawal_after_prior_completed_mints_new_event():
    """A withdrawal notice arriving after a prior COMPLETED record never finds that record via
    `_lookup_open_event` (COMPLETED is terminal) - it mints a fresh, separate event rather than
    mutating history. This is a deliberate design choice (see module docstring), not a bug."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-02-25T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_GAMMONIND_WITHDRAWN]),
                          lookup_fn=lambda family, symbol: None)
    assert res.events[0].event_key.startswith("DELISTING:GAMMONIND:")
    assert res.events[0].status == "WITHDRAWN"


def test_delisting_adapter_new_cycle_mints_new_key_when_prior_is_terminal():
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-09-03T19:00:00+05:30",
                          nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]),
                          lookup_fn=lambda family, symbol: None)
    assert res.events[0].event_key.startswith("DELISTING:ATCOM:")
    assert res.events[0].event_key != "DELISTING:ATCOM:fixedkey"


def test_delisting_adapter_duplicate_identical_row_fetched_twice_is_same_event():
    from market_events.sources.delisting import fetch_delisting
    res1 = fetch_delisting("2026-09-03T19:00:00+05:30",
                           nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    res2 = fetch_delisting("2026-09-03T19:00:00+05:30",
                           nse=_dl_payload([_DL_ROW_ATCOM_COMPULSORY_BSE]))
    assert res1.events[0].to_dict() == res2.events[0].to_dict()


def test_delisting_adapter_source_unavailable_on_network_exception():
    from market_events.sources.delisting import fetch_delisting

    class FailingNSE:
        def get(self, path):
            raise ConnectionError("blocked")

    res = fetch_delisting("2026-10-07T19:00:00+05:30", nse=FailingNSE())
    assert res.status == "SOURCE_UNAVAILABLE"


def test_delisting_adapter_parse_error_on_non_list_payload():
    from market_events.sources.delisting import fetch_delisting

    class FakeNSE:
        def get(self, path):
            return {"unexpected": "shape"}

    res = fetch_delisting("2026-10-07T19:00:00+05:30", nse=FakeNSE())
    assert res.status == "PARSE_ERROR"


def test_delisting_adapter_parse_error_on_schema_drift():
    """Every row missing the 'desc' key NSE's own feed carries -> fails closed, never a
    guessed mapping."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-10-07T19:00:00+05:30",
                          nse=_dl_payload([{"unexpected": "shape"}]))
    assert res.status == "PARSE_ERROR"


def test_delisting_adapter_empty_payload_is_healthy_success():
    """Confirmed live reachable, zero-matching-row shape -> SUCCESS+[], never NOT_SUPPORTED_YET."""
    from market_events.sources.delisting import fetch_delisting
    res = fetch_delisting("2026-10-07T19:00:00+05:30", nse=_dl_payload())
    assert res.status == "SUCCESS"
    assert res.events == []


def test_delisting_source_defaults_to_review_required_rights():
    from core.sources import SRC_NSE_DELISTING_ANNOUNCEMENTS
    from publication.rights import rights_for
    assert rights_for(SRC_NSE_DELISTING_ANNOUNCEMENTS).status.value == "REVIEW_REQUIRED"
