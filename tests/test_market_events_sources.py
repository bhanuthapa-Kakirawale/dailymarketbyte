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
    """EARNINGS (P2A), OFS (P2B) and BUYBACK (P2C) are now the three exceptions with real,
    live-calling fetchers; IPO's stub stays NOT_SUPPORTED_YET-shaped by design (it is never
    acquired by this engine - see its own reworded reason string) and every other family is
    still genuinely unsupported. EARNINGS/OFS/BUYBACK are excluded from this capture
    specifically so this test never makes a live network call (tests/ stays fully offline) -
    see each adapter's own section further down this file for its offline, fixture-based
    coverage."""
    from market_events.models import ALL_FAMILIES, BUYBACK, EARNINGS, OFS
    from market_events.service import MarketEventsService
    from market_events.sources import DEFAULT_FETCHERS

    assert not getattr(DEFAULT_FETCHERS[EARNINGS], "not_supported_yet", False)
    assert not getattr(DEFAULT_FETCHERS[OFS], "not_supported_yet", False)
    assert not getattr(DEFAULT_FETCHERS[BUYBACK], "not_supported_yet", False)

    svc = MarketEventsService(str(tmp_path))
    still_unsupported = tuple(f for f in ALL_FAMILIES if f not in (EARNINGS, OFS, BUYBACK))
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
