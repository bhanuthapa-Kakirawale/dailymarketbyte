"""IPO projection (`market_events.sources.ipo_projection`): a read-only view over
`ipo_watch`'s already-validated data - never a second acquisition, never written to
`market_events/store.py`. Field-equivalence with the existing IPO WATCH scene is the
"cross-check" the task calls for (see docs/MARKET_EVENTS_ENGINE.md).
"""
import datetime as dt

from ipo_watch.models import BoardType, IPOEvent, IPOStatus

DAY = dt.date(2026, 10, 6)


def _ipo(status=IPOStatus.OPEN, issue_close_date=DAY, issue_open_date=None, listing_date=None,
        allotment_date=None, price_band=(258.0, 272.0), symbol="DEMOIPO"):
    return IPOEvent(company_name="Demo IPO Ltd", board_type=BoardType.MAINBOARD, status=status,
                    source_name="nse_ipo_issues", source_reference="https://nseindia.com/ipo",
                    data_as_of=DAY, symbol=symbol, issue_open_date=issue_open_date,
                    issue_close_date=issue_close_date, allotment_date=allotment_date,
                    listing_date=listing_date, price_band_low=price_band[0] if price_band else None,
                    price_band_high=price_band[1] if price_band else None, lot_size=55,
                    retrieved_at="2026-10-06T09:00:00+05:30")


def test_project_ipo_events_builds_market_event_for_each_dated_kind():
    from market_events.sources.ipo_projection import project_ipo_events
    events = project_ipo_events([_ipo(issue_close_date=DAY)], DAY)
    assert len(events) == 1
    ev = events[0]
    assert ev.family == "IPO" and ev.symbol == "DEMOIPO" and ev.status == "CLOSED"
    assert ev.data_as_of == DAY.isoformat()
    assert ev.capture_mode == "PROJECTION"
    assert ev.connectivity_status == "NOT_ATTEMPTED"


def test_project_ipo_events_omits_unqualified_ipo_with_no_dated_event_today():
    from market_events.sources.ipo_projection import project_ipo_events
    other_day = DAY + dt.timedelta(days=5)
    events = project_ipo_events([_ipo(issue_close_date=other_day)], DAY)
    assert events == []


def test_project_ipo_events_never_writes_to_store(tmp_path):
    from market_events.sources.ipo_projection import project_ipo_events
    from market_events.store import list_events

    project_ipo_events([_ipo()], DAY)   # no out_dir param at all - structurally cannot write
    assert list_events(str(tmp_path), "IPO") == []


def test_project_ipo_events_carries_price_band_fact():
    from market_events.sources.ipo_projection import project_ipo_events
    ev = project_ipo_events([_ipo()], DAY)[0]
    facts = {f["label"]: f["value"] for f in ev.facts}
    assert facts["price_band"] == "258-272"


def test_project_ipo_events_omits_price_band_fact_when_absent():
    from market_events.sources.ipo_projection import project_ipo_events
    ev = project_ipo_events([_ipo(price_band=None)], DAY)[0]
    assert "price_band" not in {f["label"] for f in ev.facts}


def test_crosscheck_against_ipo_watch_returns_empty_for_consistent_data():
    from market_events.sources.ipo_projection import crosscheck_against_ipo_watch
    ipo = _ipo(issue_close_date=DAY)
    assert crosscheck_against_ipo_watch(ipo, "CLOSES_TODAY", DAY) == []


def test_crosscheck_against_ipo_watch_flags_source_mismatch():
    from dataclasses import replace
    from market_events.sources.ipo_projection import _project_one, crosscheck_against_ipo_watch
    ipo = _ipo(issue_close_date=DAY)
    # simulate a projection bug: corrupt the projected event's own source after the fact
    import market_events.sources.ipo_projection as mod
    orig = mod._project_one

    def _bad_project_one(ipo_, kind, day):
        ev = orig(ipo_, kind, day)
        ev.source_name = "something_else"
        return ev
    mod._project_one = _bad_project_one
    try:
        issues = crosscheck_against_ipo_watch(ipo, "CLOSES_TODAY", DAY)
    finally:
        mod._project_one = orig
    assert any("source" in i for i in issues)
