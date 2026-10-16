"""Point-in-time safety + the immutable snapshot store."""
import datetime as dt

import pytest

from conftest import SESSION
from private_candidates import service, store
from test_private_desk import desk_out  # noqa: F401


def _assessment_by_symbol(out_dir):
    from private_desk.repository import DeskRepository
    repo = DeskRepository(out_dir)
    return {a.symbol: a for a in service.build_assessments(repo, SESSION)}


def test_a_later_market_event_revision_never_leaks_into_an_earlier_assessment(desk_out):
    before = _assessment_by_symbol(desk_out)

    from market_events.models import EARNINGS, SCHEMA_VERSION, SUCCESS, MarketEvent
    from market_events.store import write_revision
    future_retrieved = MarketEvent(
        schema_version=SCHEMA_VERSION, family=EARNINGS, event_key="EARNINGS:SYMA:late-filing",
        symbol="SYMA", company="Synthetic A Ltd.", status="SCHEDULED", sub_type=None,
        data_as_of=(SESSION + dt.timedelta(days=1)).isoformat(),
        source_name="nse_corp_announcements", source_reference="https://example.invalid/x",
        facts=[], retrieved_at="2026-09-19T08:00:00+05:30",
        first_retrieved_at="2026-09-19T08:00:00+05:30", status_capture=SUCCESS)
    write_revision(desk_out, future_retrieved)

    future_bar_date = SESSION + dt.timedelta(days=3)
    from storage.ohlcv_models import OHLCVBar, QualityStatus
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    s = OHLCVStore(default_db_path(desk_out))
    s.upsert_bars([OHLCVBar(symbol="SYMA", session_date=future_bar_date, open=1, high=1, low=1,
                            close=1, volume=999_999_999, source="yahoo",
                            retrieved_at=dt.datetime(2026, 9, 21, 16, 30,
                                                     tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))),
                            quality_status=QualityStatus.OK)])
    s.close()

    after = _assessment_by_symbol(desk_out)
    for symbol, assessment in before.items():
        later = after[symbol]
        assert assessment.event_proximity == later.event_proximity
        assert assessment.sessions_to_event == later.sessions_to_event
        assert assessment.atr14 == later.atr14
        assert assessment.score_total == later.score_total
        assert assessment.research_priority == later.research_priority


def _make_assessment(out_dir, symbol="SYMA"):
    repo_assessments = _assessment_by_symbol(out_dir)
    return repo_assessments[symbol]


def test_write_snapshot_is_exclusive_create_never_overwrites(desk_out, tmp_path):
    out_dir = str(tmp_path / "store_out")
    a = _make_assessment(desk_out)
    import dataclasses
    a = dataclasses.replace(a, session_date=SESSION.isoformat())
    path1 = store.write_snapshot(out_dir, a)
    path2 = store.write_snapshot(out_dir, a)
    assert path1 != path2
    assert path1.endswith(".json") and not path1.endswith(".r2.json")
    assert path2.endswith(".r2.json")
    loaded = store.load_latest(out_dir, a.score_policy_version, SESSION, "SYMA")
    assert loaded == a


def test_snapshot_store_isolates_policy_versions(desk_out, tmp_path):
    import dataclasses
    out_dir = str(tmp_path / "store_out2")
    a_v1 = _make_assessment(desk_out)
    a_v2 = dataclasses.replace(a_v1, score_policy_version="trade-candidate-score-2.0")
    store.write_snapshot(out_dir, a_v1)
    store.write_snapshot(out_dir, a_v2)
    assert store.load_latest(out_dir, "trade-candidate-score-1.0", SESSION, "SYMA") == a_v1
    assert store.load_latest(out_dir, "trade-candidate-score-2.0", SESSION, "SYMA") == a_v2
    # rewriting v1 after v2 exists never touches v2's file
    store.write_snapshot(out_dir, a_v1)
    assert store.load_latest(out_dir, "trade-candidate-score-2.0", SESSION, "SYMA") == a_v2
