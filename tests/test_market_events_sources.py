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
    """EARNINGS (P2A) is now the one exception with a real, live-calling fetcher; IPO's stub
    stays NOT_SUPPORTED_YET-shaped by design (it is never acquired by this engine - see its
    own reworded reason string) and every other family is still genuinely unsupported.
    EARNINGS is excluded from this capture specifically so this test never makes a live
    network call (tests/ stays fully offline) - see the EARNINGS adapter tests further down
    this file for its own offline, fixture-based coverage."""
    from market_events.models import ALL_FAMILIES, EARNINGS
    from market_events.service import MarketEventsService
    from market_events.sources import DEFAULT_FETCHERS

    assert not getattr(DEFAULT_FETCHERS[EARNINGS], "not_supported_yet", False)

    svc = MarketEventsService(str(tmp_path))
    still_unsupported = tuple(f for f in ALL_FAMILIES if f != EARNINGS)
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
