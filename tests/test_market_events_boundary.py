"""Market Events Engine V1 - boundaries: replay never fetches, a missing historical revision
fails explicitly, rights stay fail-closed, writes stay guarded, the StateStore namespace is
immutable, and a failed/unsupported family is never invented as an absence of events becoming
a plausible value (mirrors tests/test_institutional_boundary.py).
"""
import datetime as dt
import os

import pytest

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _ev(event_key="EARNINGS:ABC:x", symbol="ABC", data_as_of="2026-10-05",
       first_retrieved_at="2026-10-05T20:00:00+05:30", status_capture=None):
    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    return MarketEvent(schema_version=SCHEMA_VERSION, family=EARNINGS, event_key=event_key,
                       symbol=symbol, company="ABC Ltd", status="SCHEDULED", sub_type=None,
                       data_as_of=data_as_of, source_name="nse_corp_announcements",
                       source_reference="https://nseindia.com/x", facts=[],
                       retrieved_at=first_retrieved_at, first_retrieved_at=first_retrieved_at,
                       status_capture=status_capture or SUCCESS)


# --------------------------------------------------------------------------- replay / historical
def test_replay_never_calls_capture_fn(tmp_path):
    from market_events.context import load_market_events

    calls = []

    def capture_fn(family):
        calls.append(family)
        return None

    ctx = load_market_events(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=False, on_or_before=dt.date(2026, 10, 4),
                             families=("EARNINGS",), capture_fn=capture_fn)
    assert calls == []
    assert ctx.status["EARNINGS"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"
    assert ctx.events["EARNINGS"] == []


def test_live_run_may_capture_a_missing_family(tmp_path):
    from market_events.context import load_market_events

    def capture_fn(family):
        return [_ev()]

    ctx = load_market_events(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=True, on_or_before=dt.date(2026, 10, 5),
                             families=("EARNINGS",), capture_fn=capture_fn)
    assert ctx.status["EARNINGS"] == "CAPTURED_THIS_RUN"
    assert ctx.events["EARNINGS"][0].event_key == "EARNINGS:ABC:x"


def test_a_live_run_without_a_stored_event_and_no_capture_is_not_captured(tmp_path):
    from market_events.context import load_market_events
    ctx = load_market_events(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=True, on_or_before=dt.date(2026, 10, 5),
                             families=("EARNINGS",), capture_fn=None)
    assert ctx.status["EARNINGS"] == "NOT_CAPTURED"


def test_first_retrieved_before_cutoff_gate_excludes_report_discovered_after_cutoff(tmp_path):
    """An event discovered LATER must never appear to have been available at an earlier
    cutoff, even if its own `data_as_of` is on or before the session."""
    from market_events.store import write_revision
    write_revision(str(tmp_path), _ev(first_retrieved_at="2026-10-05T23:00:00+05:30"))

    from market_events.context import load_market_events
    ctx = load_market_events(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=False, on_or_before=dt.date(2026, 10, 5),
                             families=("EARNINGS",))
    assert ctx.events["EARNINGS"] == []
    assert ctx.status["EARNINGS"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"


# ------------------------------------------ per-revision point-in-time correctness (P2F.1)
# `first_retrieved_at` is carried forward unchanged across every revision of a key, so the OLD
# gate only ever answered "was this key known yet" - it always returned the absolute LATEST
# on-disk revision regardless of cutoff, letting a later revision (reschedule/result/status
# change) leak backward into an earlier replay. `list_events`/`load_latest`'s `as_of`/
# `first_retrieved_before` now pick the revision whose OWN `retrieved_at` was actually active as
# of that timestamp - a generic store-level fix (every family benefits), proven here with
# GOVT_SECURITIES_AUCTION events since that's what surfaced the gap.
def _gsa_rev(event_key, data_as_of, status, retrieved_at, first_retrieved_at, facts=None):
    from market_events.models import GOVT_SECURITIES_AUCTION, SCHEMA_VERSION, SUCCESS, MarketEvent
    return MarketEvent(schema_version=SCHEMA_VERSION, family=GOVT_SECURITIES_AUCTION,
                       event_key=event_key, symbol=None, company="Government of India",
                       status=status, sub_type="TBILL_91", data_as_of=data_as_of,
                       source_name="rbi_govt_securities_auction_press_release",
                       source_reference="x", retrieved_at=retrieved_at,
                       first_retrieved_at=first_retrieved_at, status_capture=SUCCESS,
                       facts=facts or [])


def test_list_events_as_of_returns_the_revision_active_at_that_time_not_the_latest(tmp_path):
    from market_events.store import list_events, write_revision
    key = "GOVT_SECURITIES_AUCTION:MARKET:TBILL_91:2026-10-07"
    write_revision(str(tmp_path), _gsa_rev(key, "2026-10-07", "SCHEDULED",
                                           "2026-10-01T19:30:00+05:30",
                                           "2026-10-01T19:30:00+05:30"))
    write_revision(str(tmp_path), _gsa_rev(key, "2026-10-09", "REVISED_DATE",
                                           "2026-10-03T19:30:00+05:30",
                                           "2026-10-01T19:30:00+05:30"))

    before = [e for _, _, e in list_events(str(tmp_path), "GOVT_SECURITIES_AUCTION",
                                           as_of="2026-10-02T00:00:00+05:30")]
    assert len(before) == 1 and before[0].data_as_of == "2026-10-07"   # rev 1, not rev 2

    after = [e for _, _, e in list_events(str(tmp_path), "GOVT_SECURITIES_AUCTION",
                                          as_of="2026-10-04T00:00:00+05:30")]
    assert len(after) == 1 and after[0].data_as_of == "2026-10-09"     # rev 2 now active

    not_yet = [e for _, _, e in list_events(str(tmp_path), "GOVT_SECURITIES_AUCTION",
                                            as_of="2026-10-01T19:30:00+05:30")]
    assert not_yet == []    # strictly BEFORE rev 1's own retrieved_at - not yet known at all


def test_list_events_as_of_none_still_returns_the_absolute_latest_unchanged(tmp_path):
    """Regression safety: every EXISTING caller (service.py::_store_one,
    _lookup_open_event/_lookup_open_by_instrument, private_desk/repository.py) never passes
    `as_of` - their behavior must be byte-identical to before this fix."""
    from market_events.store import list_events, write_revision
    key = "GOVT_SECURITIES_AUCTION:MARKET:TBILL_91:2026-10-07"
    write_revision(str(tmp_path), _gsa_rev(key, "2026-10-07", "SCHEDULED",
                                           "2026-10-01T19:30:00+05:30",
                                           "2026-10-01T19:30:00+05:30"))
    write_revision(str(tmp_path), _gsa_rev(key, "2026-10-09", "REVISED_DATE",
                                           "2026-10-03T19:30:00+05:30",
                                           "2026-10-01T19:30:00+05:30"))
    latest = [e for _, _, e in list_events(str(tmp_path), "GOVT_SECURITIES_AUCTION")]
    assert len(latest) == 1 and latest[0].data_as_of == "2026-10-09" and latest[0].revision == 2


def test_load_latest_first_retrieved_before_reflects_the_correct_historical_revision(tmp_path):
    """The actual regression test for the gap: a key first seen before a replay cutoff but
    revised (rescheduled, then resulted) AFTER it must show the PRE-revision state at that
    cutoff, never today's absolute latest on-disk revision."""
    from market_events.store import load_latest, write_revision
    key = "GOVT_SECURITIES_AUCTION:MARKET:TBILL_91:2026-10-07"
    write_revision(str(tmp_path), _gsa_rev(
        key, "2026-10-07", "SCHEDULED", "2026-10-01T19:30:00+05:30",
        "2026-10-01T19:30:00+05:30", facts=[{"label": "notified_amount_crore", "value": 8000}]))
    write_revision(str(tmp_path), _gsa_rev(
        key, "2026-10-09", "REVISED_DATE", "2026-10-03T19:30:00+05:30",
        "2026-10-01T19:30:00+05:30", facts=[{"label": "notified_amount_crore", "value": 8000}]))
    write_revision(str(tmp_path), _gsa_rev(
        key, "2026-10-09", "COMPLETED", "2026-10-09T19:30:00+05:30",
        "2026-10-01T19:30:00+05:30", facts=[{"label": "notified_amount_crore", "value": 8000},
                                            {"label": "cutoff_yield_pct", "value": 5.52}]))

    before_reschedule = load_latest(str(tmp_path), "GOVT_SECURITIES_AUCTION",
                                    first_retrieved_before="2026-10-02T00:00:00+05:30")[0]
    assert before_reschedule.data_as_of == "2026-10-07" and before_reschedule.status == "SCHEDULED"

    after_reschedule_before_result = load_latest(
        str(tmp_path), "GOVT_SECURITIES_AUCTION",
        first_retrieved_before="2026-10-04T00:00:00+05:30")[0]
    assert after_reschedule_before_result.data_as_of == "2026-10-09"
    assert after_reschedule_before_result.status == "REVISED_DATE"
    assert not any(f["label"] == "cutoff_yield_pct"
                  for f in after_reschedule_before_result.facts)

    after_result = load_latest(str(tmp_path), "GOVT_SECURITIES_AUCTION",
                               first_retrieved_before="2026-10-10T00:00:00+05:30")[0]
    assert after_result.status == "COMPLETED"
    assert any(f["label"] == "cutoff_yield_pct" for f in after_result.facts)


def test_load_latest_without_first_retrieved_before_is_unaffected(tmp_path):
    """No cutoff given at all -> absolute latest, exactly as every other family's existing
    tests already rely on (e.g. test_load_latest_returns_the_highest_revision_per_event_key)."""
    from market_events.store import load_latest, write_revision
    key = "GOVT_SECURITIES_AUCTION:MARKET:TBILL_91:2026-10-07"
    write_revision(str(tmp_path), _gsa_rev(key, "2026-10-07", "SCHEDULED",
                                           "2026-10-01T19:30:00+05:30",
                                           "2026-10-01T19:30:00+05:30"))
    write_revision(str(tmp_path), _gsa_rev(key, "2026-10-09", "COMPLETED",
                                           "2026-10-09T19:30:00+05:30",
                                           "2026-10-01T19:30:00+05:30"))
    latest = load_latest(str(tmp_path), "GOVT_SECURITIES_AUCTION")[0]
    assert latest.status == "COMPLETED" and latest.data_as_of == "2026-10-09"


# ------------------------------------------- full reschedule+replay integration (P2F.1, 6-9)
# Drives the REAL MarketEventsService + the adapter's own needs_instrument_lookup contract
# through 3 sequential captures (SCHEDULED -> REVISED_DATE -> COMPLETED), then reads back via
# market_events.context.load_market_events (the actual PRE/POST entry point) at cutoffs
# straddling the reschedule and the result.
def _gsa_synthetic_fetcher():
    from market_events.models import (COMPLETED, GOVT_SECURITIES_AUCTION, REVISED_DATE,
                                      SCHEDULED, SUCCESS, TBILL_91, FamilyFetchResult)
    calls = {"n": 0}

    def fetch(now_iso, lookup_fn=None):
        calls["n"] += 1
        prior = lookup_fn(GOVT_SECURITIES_AUCTION, TBILL_91) if lookup_fn else None
        if calls["n"] == 1:
            auction_date, status, facts = "2026-10-07", SCHEDULED, [
                {"label": "notified_amount_crore", "value": 8000}]
        elif calls["n"] == 2:
            auction_date = "2026-10-09"
            status = REVISED_DATE if prior and prior.data_as_of != auction_date else SCHEDULED
            facts = [{"label": "notified_amount_crore", "value": 8000}]
        else:
            auction_date, status = "2026-10-09", COMPLETED
            facts = [{"label": "notified_amount_crore", "value": 8000},
                    {"label": "cutoff_yield_pct", "value": 5.52}]
        event_key = (prior.event_key if prior else
                    f"{GOVT_SECURITIES_AUCTION}:MARKET:{TBILL_91}:{auction_date}")
        ev = _gsa_rev(event_key, auction_date, status, now_iso, now_iso, facts=facts)
        return FamilyFetchResult(status=SUCCESS, events=[ev])
    fetch.needs_instrument_lookup = True
    return fetch


def test_gsa_replay_before_reschedule_shows_original_date_and_status(tmp_path):
    from market_events.context import load_market_events
    from market_events.service import MarketEventsService
    svc = MarketEventsService(str(tmp_path), fetchers={"GOVT_SECURITIES_AUCTION":
                                                        _gsa_synthetic_fetcher()})
    t0 = dt.datetime(2026, 10, 1, 19, 30, tzinfo=IST)
    svc.capture(t0, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))

    ctx = load_market_events(str(tmp_path), cutoff=t0 + dt.timedelta(hours=12), live=False,
                             on_or_before=dt.date(2026, 10, 9),
                             families=("GOVT_SECURITIES_AUCTION",))
    ev = ctx.events["GOVT_SECURITIES_AUCTION"][0]
    assert ev.data_as_of == "2026-10-07" and ev.status == "SCHEDULED"


def test_gsa_replay_after_reschedule_shows_revised_date(tmp_path):
    from market_events.context import load_market_events
    from market_events.service import MarketEventsService
    fetch = _gsa_synthetic_fetcher()
    svc = MarketEventsService(str(tmp_path), fetchers={"GOVT_SECURITIES_AUCTION": fetch})
    t0 = dt.datetime(2026, 10, 1, 19, 30, tzinfo=IST)
    t1 = dt.datetime(2026, 10, 3, 19, 30, tzinfo=IST)
    svc.capture(t0, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))
    r1 = svc.capture(t1, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))
    assert r1["results"]["GOVT_SECURITIES_AUCTION"]["events"][0]["event_key"] == \
        "GOVT_SECURITIES_AUCTION:MARKET:TBILL_91:2026-10-07"   # SAME key, not a new one

    ctx = load_market_events(str(tmp_path), cutoff=t1 + dt.timedelta(hours=12), live=False,
                             on_or_before=dt.date(2026, 10, 9),
                             families=("GOVT_SECURITIES_AUCTION",))
    ev = ctx.events["GOVT_SECURITIES_AUCTION"][0]
    assert ev.data_as_of == "2026-10-09" and ev.status == "REVISED_DATE"


def test_gsa_replay_before_result_shows_no_result_fields(tmp_path):
    from market_events.context import load_market_events
    from market_events.service import MarketEventsService
    fetch = _gsa_synthetic_fetcher()
    svc = MarketEventsService(str(tmp_path), fetchers={"GOVT_SECURITIES_AUCTION": fetch})
    t0 = dt.datetime(2026, 10, 1, 19, 30, tzinfo=IST)
    t1 = dt.datetime(2026, 10, 3, 19, 30, tzinfo=IST)
    svc.capture(t0, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))
    svc.capture(t1, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))

    ctx = load_market_events(str(tmp_path), cutoff=t1 + dt.timedelta(hours=12), live=False,
                             on_or_before=dt.date(2026, 10, 9),
                             families=("GOVT_SECURITIES_AUCTION",))
    ev = ctx.events["GOVT_SECURITIES_AUCTION"][0]
    assert not any(f["label"] == "cutoff_yield_pct" for f in ev.facts)


def test_gsa_replay_after_result_shows_completed_with_result_fields(tmp_path):
    from market_events.context import load_market_events
    from market_events.service import MarketEventsService
    fetch = _gsa_synthetic_fetcher()
    svc = MarketEventsService(str(tmp_path), fetchers={"GOVT_SECURITIES_AUCTION": fetch})
    t0 = dt.datetime(2026, 10, 1, 19, 30, tzinfo=IST)
    t1 = dt.datetime(2026, 10, 3, 19, 30, tzinfo=IST)
    t2 = dt.datetime(2026, 10, 9, 19, 30, tzinfo=IST)
    svc.capture(t0, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))
    svc.capture(t1, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))
    r2 = svc.capture(t2, "REPORT_JOB", families=("GOVT_SECURITIES_AUCTION",))
    assert r2["results"]["GOVT_SECURITIES_AUCTION"]["events"][0]["event_key"] == \
        "GOVT_SECURITIES_AUCTION:MARKET:TBILL_91:2026-10-07"   # still the SAME key throughout

    ctx = load_market_events(str(tmp_path), cutoff=t2 + dt.timedelta(hours=12), live=False,
                             on_or_before=dt.date(2026, 10, 9),
                             families=("GOVT_SECURITIES_AUCTION",))
    ev = ctx.events["GOVT_SECURITIES_AUCTION"][0]
    assert ev.status == "COMPLETED"
    assert any(f["label"] == "cutoff_yield_pct" for f in ev.facts)
    assert any(f["label"] == "notified_amount_crore" for f in ev.facts)   # carried forward


# --------------------------------------------------------------------------- rights (fail-closed)
def test_review_required_source_blocks_production_publication_by_default():
    from publication.rights import review_required_policy, BLOCK
    assert review_required_policy() == BLOCK


def test_market_event_fact_defaults_to_unknown_or_review_required_never_approved():
    """A `market_event_fact` built from an unregistered source name must never silently read as
    APPROVED - `strictest_rights` falls back to UNKNOWN, which is still rights-blocked."""
    from market_events.facts import market_event_fact
    from publication.classification import RightsStatus
    f = market_event_fact(_ev(), "ABC Ltd", "market_event.x.0")
    assert f.publication_rights_status in (RightsStatus.UNKNOWN, RightsStatus.REVIEW_REQUIRED)


# --------------------------------------------------------------------------- guarded writes
def test_writes_are_refused_in_a_test_run_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DMB_RUN_CONTEXT", "test")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prod_out = os.path.join(root, "output")
    from operations.run_context import ProductionPathError
    from market_events.store import write_revision
    with pytest.raises(ProductionPathError):
        write_revision(prod_out, _ev())
    write_revision(str(tmp_path), _ev())   # a non-production path is fine


# --------------------------------------------------------------------------- StateStore namespace
def test_market_events_namespace_is_registered_and_immutable():
    from state.sync import NAMESPACES
    ns = next(n for n in NAMESPACES if n.key == "market_events")
    assert ns.local == "market_events"
    assert ns.recursive is True
    assert ns.is_immutable("market_events/EARNINGS/x.json") is True
    assert ns.is_immutable("market_events/attempts/2026-10-05/x.json") is True


def test_dev_cleanup_protects_the_market_events_folder():
    from operations.dev_cleanup import PROTECTED_OUTPUT
    assert "market_events" in PROTECTED_OUTPUT


# --------------------------------------------------------------------------- never invented
def test_source_failure_never_invents_a_plausible_event(tmp_path):
    """A fetcher that raises must never be silently treated as 'no events' being replaced by a
    guessed one - it stays absent, and the attempt is recorded as SOURCE_UNAVAILABLE."""
    from market_events.service import MarketEventsService

    def failing_fetch(now_iso):
        raise ConnectionError("blocked")

    svc = MarketEventsService(str(tmp_path), fetchers={"EARNINGS": failing_fetch})
    res = svc.capture(dt.datetime(2026, 10, 5, 19, 30, tzinfo=IST), "REPORT_JOB",
                      families=("EARNINGS",))
    assert res["results"]["EARNINGS"]["status"] == "SOURCE_UNAVAILABLE"
    assert res["results"]["EARNINGS"]["events"] == []


def test_not_supported_yet_family_records_attempt_without_counting_as_failure(tmp_path):
    """"SDL" (State Development Loans - deferred out of GOVT_SECURITIES_AUCTION's P2F scope,
    see market_events/sources/govt_securities_auction.py's module docstring) has no registered
    fetcher at all - not EARNINGS/OFS/BUYBACK/OPEN_OFFER/DELISTING/GOVT_SECURITIES_AUCTION,
    each of which now has a real, live-calling fetcher since P2A-P2F, and this test must never
    make a network call."""
    from market_events.service import MarketEventsService
    from market_events.store import latest_attempts

    svc = MarketEventsService(str(tmp_path))     # default fetchers - "SDL" has none registered
    res = svc.capture(dt.datetime(2026, 10, 5, 19, 30, tzinfo=IST), "REPORT_JOB",
                      families=("SDL",))
    assert res["results"]["SDL"]["status"] == "NOT_SUPPORTED_YET"
    attempts = latest_attempts(str(tmp_path))
    assert any(a.get("status") == "NOT_SUPPORTED_YET" for a in attempts)


# --------------------------------------------------------------------------- OFS replay (P2B)
def test_ofs_replay_never_calls_capture_fn_and_respects_cutoff(tmp_path):
    """Same generic replay/point-in-time mechanism EARNINGS already proved above, demonstrated
    explicitly for OFS - a family-agnostic guarantee, not a per-family reimplementation."""
    from market_events.models import OFS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.store import write_revision

    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=OFS, event_key="OFS:DEMOOFS:x",
                     symbol="DEMOOFS", company="Demo OFS Ltd", status="OPEN", sub_type=None,
                     data_as_of="2026-10-05", source_name="nse_ofs_live", source_reference="x",
                     first_retrieved_at="2026-10-05T23:00:00+05:30", status_capture=SUCCESS)
    write_revision(str(tmp_path), ev)

    from market_events.context import load_market_events
    calls = []
    ctx = load_market_events(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=False, on_or_before=dt.date(2026, 10, 5),
                             families=("OFS",), capture_fn=lambda f: calls.append(f))
    assert calls == []
    assert ctx.events["OFS"] == []    # written AFTER the cutoff - never shown as available
    assert ctx.status["OFS"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"


def test_buyback_replay_never_calls_capture_fn_and_respects_cutoff(tmp_path):
    """Same generic replay/point-in-time mechanism, demonstrated explicitly for BUYBACK
    (P2C) - a family-agnostic guarantee, not a per-family reimplementation."""
    from market_events.models import BUYBACK, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.store import write_revision

    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=BUYBACK,
                     event_key="BUYBACK:DEMOBB:x", symbol="DEMOBB", company="Demo Buyback Ltd",
                     status="ANNOUNCED", sub_type=None, data_as_of="2026-10-05",
                     source_name="nse_corporate_actions", source_reference="x",
                     first_retrieved_at="2026-10-05T23:00:00+05:30", status_capture=SUCCESS)
    write_revision(str(tmp_path), ev)

    from market_events.context import load_market_events
    calls = []
    ctx = load_market_events(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=False, on_or_before=dt.date(2026, 10, 5),
                             families=("BUYBACK",), capture_fn=lambda f: calls.append(f))
    assert calls == []
    assert ctx.events["BUYBACK"] == []    # written AFTER the cutoff - never shown as available
    assert ctx.status["BUYBACK"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"


# --------------------------------------------------------------------------- OPEN_OFFER replay (P2D)
def test_open_offer_replay_never_calls_capture_fn_and_respects_cutoff(tmp_path):
    """Same generic replay/point-in-time mechanism EARNINGS/OFS/BUYBACK already prove above,
    demonstrated explicitly for OPEN_OFFER - a family-agnostic guarantee, not a per-family
    reimplementation."""
    from market_events.models import OPEN_OFFER, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.store import write_revision

    ev = MarketEvent(schema_version=SCHEMA_VERSION, family=OPEN_OFFER,
                     event_key="OPEN_OFFER:DEMOOO:x", symbol="DEMOOO", company="Demo Target Ltd",
                     status="ANNOUNCED", sub_type=None, data_as_of="2026-10-05",
                     source_name="nse_sast_open_offer_announcements", source_reference="x",
                     first_retrieved_at="2026-10-05T23:00:00+05:30", status_capture=SUCCESS)
    write_revision(str(tmp_path), ev)

    from market_events.context import load_market_events
    calls = []
    ctx = load_market_events(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=False, on_or_before=dt.date(2026, 10, 5),
                             families=("OPEN_OFFER",), capture_fn=lambda f: calls.append(f))
    assert calls == []
    assert ctx.events["OPEN_OFFER"] == []     # written AFTER the cutoff - never shown as available
    assert ctx.status["OPEN_OFFER"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"
