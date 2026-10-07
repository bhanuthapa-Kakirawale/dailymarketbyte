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
    """OPEN_OFFER (not EARNINGS/OFS/BUYBACK - each of those now has a real, live-calling
    fetcher since P2A/P2B/P2C, and this test must never make a network call)."""
    from market_events.service import MarketEventsService
    from market_events.store import latest_attempts

    svc = MarketEventsService(str(tmp_path))     # default fetchers - OPEN_OFFER still NOT_SUPPORTED_YET
    res = svc.capture(dt.datetime(2026, 10, 5, 19, 30, tzinfo=IST), "REPORT_JOB",
                      families=("OPEN_OFFER",))
    assert res["results"]["OPEN_OFFER"]["status"] == "NOT_SUPPORTED_YET"
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
