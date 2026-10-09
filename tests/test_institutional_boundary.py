"""Institutional Flow Intelligence V1 - boundaries: replay never fetches current data, a
missing historical snapshot fails explicitly, rights stay fail-closed, writes stay guarded,
and the StateStore namespace is immutable (mirrors tests/test_official_snapshots.py).
"""
import datetime as dt
import os

import pytest

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- replay / historical
def test_replay_never_calls_the_capture_function(tmp_path):
    from institutional_flows.context import load_institutional

    calls = []

    def capture_fn(source):
        calls.append(source)
        return None

    ctx = load_institutional(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=False, on_or_before=dt.date(2026, 10, 4),
                             capture_fn=capture_fn)
    assert calls == []
    assert ctx.cdsl_status == "HISTORICAL_SNAPSHOT_UNAVAILABLE"
    assert ctx.nsdl_status == "HISTORICAL_SNAPSHOT_UNAVAILABLE"
    assert ctx.cdsl is None and ctx.nsdl_latest is None


def test_live_run_may_capture_a_missing_snapshot(tmp_path):
    from institutional_flows.context import load_institutional
    from institutional_flows.models import InstitutionalFlowSnapshot, RepresentedPeriod, SUCCESS

    def capture_fn(source):
        return InstitutionalFlowSnapshot(
            schema_version="x", source=source, source_class="DEPOSITORY_REPORTED",
            report_type="x", report_key="2026-10-05", report_date="2026-10-05",
            data_as_of="2026-10-05", represented_period=RepresentedPeriod(),
            retrieved_at="x", first_retrieved_at="x", status=SUCCESS, facts=[])

    ctx = load_institutional(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=True, on_or_before=dt.date(2026, 10, 5), capture_fn=capture_fn)
    assert ctx.cdsl_status == "CAPTURED_THIS_RUN"
    assert ctx.cdsl is not None and ctx.cdsl.report_key == "2026-10-05"


def test_a_live_run_without_a_stored_snapshot_and_no_capture_is_not_captured(tmp_path):
    from institutional_flows.context import load_institutional
    ctx = load_institutional(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=True, on_or_before=dt.date(2026, 10, 5), capture_fn=None)
    assert ctx.cdsl_status == "NOT_CAPTURED"
    assert ctx.nsdl_status == "NOT_CAPTURED"


def test_a_snapshot_retrieved_after_the_cutoff_is_never_shown_as_available(tmp_path):
    """A report fetched LATER must never appear to have been available at an earlier cutoff,
    even if its own report_key date is on or before the session."""
    from institutional_flows.models import InstitutionalFlowSnapshot, RepresentedPeriod, SUCCESS
    from institutional_flows.store import write_revision

    late = InstitutionalFlowSnapshot(
        schema_version="x", source="CDSL", source_class="DEPOSITORY_REPORTED",
        report_type="x", report_key="2026-10-05", report_date="2026-10-05",
        data_as_of="2026-10-05", represented_period=RepresentedPeriod(),
        retrieved_at="2026-10-05T23:00:00+05:30", first_retrieved_at="2026-10-05T23:00:00+05:30",
        status=SUCCESS, facts=[])
    write_revision(str(tmp_path), late)

    from institutional_flows.context import load_institutional
    ctx = load_institutional(str(tmp_path), cutoff=dt.datetime(2026, 10, 5, 7, 45, tzinfo=IST),
                             live=False, on_or_before=dt.date(2026, 10, 5))
    assert ctx.cdsl is None
    assert ctx.cdsl_status == "HISTORICAL_SNAPSHOT_UNAVAILABLE"


# ------------------------------------------ per-revision point-in-time correctness (mirrors
# market_events' P2F.1 fix). `first_retrieved_at` is carried forward unchanged across every
# revision of a report_key, so the OLD gate only ever answered "was this key known yet" - it
# always returned the absolute LATEST on-disk revision regardless of cutoff, letting a later
# restatement leak backward into an earlier replay. `list_snapshots`/`load_latest`'s `as_of`/
# `first_retrieved_before` now pick the revision whose OWN `retrieved_at` was actually active as
# of that timestamp.
def _cdsl_rev(report_key, retrieved_at, first_retrieved_at, net_value):
    from institutional_flows.models import CDSL, DEPOSITORY_REPORTED, RepresentedPeriod, \
        SUCCESS, InstitutionalFlowSnapshot
    return InstitutionalFlowSnapshot(
        schema_version="x", source=CDSL, source_class=DEPOSITORY_REPORTED, report_type="x",
        report_key=report_key, report_date=report_key, data_as_of=report_key,
        represented_period=RepresentedPeriod(), retrieved_at=retrieved_at,
        first_retrieved_at=first_retrieved_at, status=SUCCESS,
        facts=[{"label": "net_value_crore", "value": net_value}])


def test_list_snapshots_as_of_returns_the_revision_active_at_that_time_not_the_latest(tmp_path):
    from institutional_flows.store import list_snapshots, write_revision
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-05T19:30:00+05:30",
                                            "2026-10-05T19:30:00+05:30", 1000))
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-07T09:00:00+05:30",
                                            "2026-10-05T19:30:00+05:30", -500))

    from institutional_flows.models import CDSL
    before = [s for _k, _p, s in list_snapshots(str(tmp_path), CDSL,
                                                as_of="2026-10-06T00:00:00+05:30")]
    assert len(before) == 1 and before[0].facts[0]["value"] == 1000     # rev 1, not rev 2

    after = [s for _k, _p, s in list_snapshots(str(tmp_path), CDSL,
                                               as_of="2026-10-08T00:00:00+05:30")]
    assert len(after) == 1 and after[0].facts[0]["value"] == -500       # rev 2 now active

    at_exactly = [s for _k, _p, s in list_snapshots(str(tmp_path), CDSL,
                                                    as_of="2026-10-05T19:30:00+05:30")]
    assert len(at_exactly) == 1 and at_exactly[0].facts[0]["value"] == 1000
    # a revision captured EXACTLY at the cutoff is already known as of that instant (inclusive)

    not_yet = [s for _k, _p, s in list_snapshots(str(tmp_path), CDSL,
                                                 as_of="2026-10-05T19:29:59+05:30")]
    assert not_yet == []   # strictly before rev 1's own retrieved_at - not yet known at all


def test_list_snapshots_as_of_none_still_returns_the_absolute_latest_unchanged(tmp_path):
    """Regression safety: every EXISTING caller (service.py::_store_one, context.py's "older"
    lookup, private_desk/repository.py) never passes `as_of` - their behavior must be
    byte-identical to before this fix."""
    from institutional_flows.store import list_snapshots, write_revision
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-05T19:30:00+05:30",
                                            "2026-10-05T19:30:00+05:30", 1000))
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-07T09:00:00+05:30",
                                            "2026-10-05T19:30:00+05:30", -500))

    from institutional_flows.models import CDSL
    latest = [s for _k, _p, s in list_snapshots(str(tmp_path), CDSL)]
    assert len(latest) == 1 and latest[0].facts[0]["value"] == -500 and latest[0].revision == 2


def test_load_latest_first_retrieved_before_reflects_the_correct_historical_revision(tmp_path):
    """The actual regression test for the gap: a report_key first seen before a replay cutoff
    but RESTATED after it must show the pre-restatement figure at that cutoff, never today's
    absolute latest on-disk revision."""
    from institutional_flows.models import CDSL
    from institutional_flows.store import load_latest, write_revision
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-05T19:30:00+05:30",
                                            "2026-10-05T19:30:00+05:30", 1000))
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-07T09:00:00+05:30",
                                            "2026-10-05T19:30:00+05:30", -500))

    before_restatement = load_latest(str(tmp_path), CDSL,
                                     first_retrieved_before="2026-10-06T00:00:00+05:30")
    assert before_restatement is not None
    assert before_restatement.facts[0]["value"] == 1000

    after_restatement = load_latest(str(tmp_path), CDSL,
                                    first_retrieved_before="2026-10-08T00:00:00+05:30")
    assert after_restatement.facts[0]["value"] == -500

    exactly_at_rev1 = load_latest(str(tmp_path), CDSL,
                                  first_retrieved_before="2026-10-05T19:30:00+05:30")
    assert exactly_at_rev1 is not None and exactly_at_rev1.facts[0]["value"] == 1000
    # inclusive: a revision captured exactly at the cutoff already counts as known

    not_known_yet = load_latest(str(tmp_path), CDSL,
                                first_retrieved_before="2026-10-05T19:29:59+05:30")
    assert not_known_yet is None


def test_load_latest_cutoff_boundary_is_inclusive_not_exclusive(tmp_path):
    """Explicit A/B/C boundary proof: a revision captured strictly BEFORE the cutoff is
    visible (A); a revision captured EXACTLY AT the cutoff is visible (B, inclusive); a
    revision captured even 1 second AFTER the cutoff is invisible and never leaks backward (C)."""
    from institutional_flows.models import CDSL
    from institutional_flows.store import load_latest, write_revision
    write_revision(str(tmp_path), _cdsl_rev("2026-10-08", "2026-10-08T10:00:00+05:30",
                                            "2026-10-08T10:00:00+05:30", 1000))

    # A: cutoff strictly after the revision's retrieved_at - visible
    a = load_latest(str(tmp_path), CDSL, first_retrieved_before="2026-10-08T10:00:01+05:30")
    assert a is not None and a.facts[0]["value"] == 1000

    # B: cutoff exactly equal to the revision's retrieved_at - visible (inclusive)
    b = load_latest(str(tmp_path), CDSL, first_retrieved_before="2026-10-08T10:00:00+05:30")
    assert b is not None and b.facts[0]["value"] == 1000

    # C: cutoff 1 second before the revision's retrieved_at - invisible
    c = load_latest(str(tmp_path), CDSL, first_retrieved_before="2026-10-08T09:59:59+05:30")
    assert c is None


def test_load_latest_without_first_retrieved_before_is_unaffected(tmp_path):
    """No cutoff given at all -> absolute latest, exactly as every other existing caller
    already relies on."""
    from institutional_flows.models import CDSL
    from institutional_flows.store import load_latest, write_revision
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-05T19:30:00+05:30",
                                            "2026-10-05T19:30:00+05:30", 1000))
    write_revision(str(tmp_path), _cdsl_rev("2026-10-05", "2026-10-07T09:00:00+05:30",
                                            "2026-10-05T19:30:00+05:30", -500))
    latest = load_latest(str(tmp_path), CDSL)
    assert latest.facts[0]["value"] == -500 and latest.revision == 2


# --------------------------------------------------------------------------- rights (fail-closed)
def test_every_institutional_source_is_review_required_not_approved():
    from publication.rights import rights_for
    from core.sources import SRC_CDSL_FPI_DAILY, SRC_NSDL_FPI_FORTNIGHTLY, SRC_NSE_FIIDII_API
    for src in (SRC_NSE_FIIDII_API, SRC_CDSL_FPI_DAILY, SRC_NSDL_FPI_FORTNIGHTLY):
        assert rights_for(src).status.value == "REVIEW_REQUIRED"


def test_default_policy_is_still_block():
    from publication.rights import review_required_policy, BLOCK
    assert review_required_policy() == BLOCK


def test_audit_blocks_on_rights_alone_never_on_anything_else(monkeypatch):
    """A claim sourced only from an institutional-flow source fails `publication_rights` and
    nothing else - the same shape every other not-yet-reviewed official source produces."""
    monkeypatch.delenv("PUBLIC_REVIEW_REQUIRED_POLICY", raising=False)
    from publication.claims import claim
    from core.sources import SRC_CDSL_FPI_DAILY
    c = claim("00_flows", "FPIs were net sellers of equity", "bars.cdsl",
             fact_ids=["institutional:CDSL:2026-10-05:equity:stock_exchange"],
             sources=[SRC_CDSL_FPI_DAILY])
    assert c["publication_rights_status"] == "REVIEW_REQUIRED"


def test_institutional_origin_is_official_not_ai_or_news():
    from publication.classify import origin_for
    from publication.classification import Origin
    from core.sources import SRC_CDSL_FPI_DAILY, SRC_NSDL_FPI_FORTNIGHTLY, SRC_NSE_FIIDII_API
    assert origin_for(SRC_NSE_FIIDII_API) == Origin.MARKET_DATA
    assert origin_for(SRC_CDSL_FPI_DAILY) == Origin.OFFICIAL_DEPOSITORY
    assert origin_for(SRC_NSDL_FPI_FORTNIGHTLY) == Origin.OFFICIAL_DEPOSITORY


# --------------------------------------------------------------------------- guarded writes
def test_writes_are_refused_in_a_test_run_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DMB_RUN_CONTEXT", "test")
    # The real production output root - deliberately NOT config.OUT_DIR, which the autouse
    # `_isolate_config_out_dir` fixture (tests/conftest.py) points at this test's own tmp_path.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prod_out = os.path.join(root, "output")
    from operations.run_context import ProductionPathError
    from institutional_flows.models import InstitutionalFlowSnapshot, RepresentedPeriod, SUCCESS
    from institutional_flows.store import write_revision
    snap = InstitutionalFlowSnapshot(
        schema_version="x", source="NSE", source_class="EXCHANGE_PROVISIONAL", report_type="x",
        report_key="2026-10-05", report_date="2026-10-05", data_as_of="2026-10-05",
        represented_period=RepresentedPeriod(), retrieved_at="x", first_retrieved_at="x",
        status=SUCCESS, facts=[])
    with pytest.raises(ProductionPathError):
        write_revision(prod_out, snap)
    write_revision(str(tmp_path), snap)   # a non-production path is fine


# --------------------------------------------------------------------------- StateStore namespace
def test_namespace_is_registered_and_immutable():
    from state.sync import NAMESPACES
    ns = next(n for n in NAMESPACES if n.key == "institutional_flows")
    assert ns.local == "institutional_flows"
    assert ns.recursive is True
    assert ns.is_immutable("institutional_flows/NSE/2026-10-05.json") is True
    assert ns.is_immutable("institutional_flows/attempts/2026-10-05/x.json") is True


def test_dev_cleanup_protects_the_institutional_flows_folder():
    from operations.dev_cleanup import PROTECTED_OUTPUT
    assert "institutional_flows" in PROTECTED_OUTPUT


# --------------------------------------------------------------------------- never substitutes
def test_source_unavailable_never_becomes_an_invented_value():
    """A failed fetch must never be silently treated as 'no flow' (0) - it stays absent."""
    from institutional_flows.sources.nse import fetch_nse_fii_dii

    class FailingNSE:
        def get(self, path):
            raise ConnectionError("blocked")

    snap = fetch_nse_fii_dii("2026-10-05T10:00:00+05:30", nse=FailingNSE())
    assert snap.status == "SOURCE_UNAVAILABLE"
    assert snap.facts == []
