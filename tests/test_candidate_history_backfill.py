"""Phase 4.2 Packet 5.4E, Part A: candidate-history bootstrap and gap recovery. Offline only -
reuses `tests/test_daily_pipeline.py`'s stubbed detector pipeline (`_stub_pipeline`,
`make_candidate`, `SPINE`, `BENCHMARK`, `UNIVERSE`) so `radar.daily_pipeline._run_detectors` (the
SAME calculation path a live daily run uses) runs for real, with only network-facing calls
stubbed - no live yfinance/NSE call anywhere.
"""
import datetime as dt

import radar.candidate_history_backfill as chb
import radar.daily_pipeline as dp
from radar.models import DirectionCompatibility
from storage.candidate_history_models import RunStatus
from storage.candidate_history_repository import CandidateHistoryStore
from storage.candidate_history_repository import default_db_path as ch_path
from storage.editorial_repository import EditorialStore
from storage.editorial_repository import default_db_path as ed_path
from test_daily_pipeline import BENCHMARK, SPINE, UNIVERSE, _date, _stub_pipeline, make_candidate


# --------------------------------------------------------------------------- pure gap detection
def test_missing_sessions_use_canonical_spine_not_calendar_days():
    # SPINE = _date(0)..._date(24), one entry per CALENDAR day in this fixture (no gaps) - a
    # spine with an actual gap (weekend/holiday) proves the point directly: a date NOT on the
    # spine can never appear as "missing".
    sparse_spine = [_date(0), _date(1), _date(4), _date(5), _date(8)]  # _date(2),(3),(6),(7) skipped
    missing = chb.find_missing_candidate_sessions(sparse_spine, _date(8), set(), required_sessions=10)
    assert missing == [_date(0), _date(1), _date(4), _date(5)]
    assert _date(2) not in missing and _date(3) not in missing  # never "missing" - never on the spine


def test_missing_sessions_respects_required_lookback_window():
    missing = chb.find_missing_candidate_sessions(SPINE, _date(19), set(), required_sessions=5)
    assert missing == [_date(14), _date(15), _date(16), _date(17), _date(18)]


def test_missing_sessions_excludes_already_complete():
    complete = {_date(15), _date(17)}
    missing = chb.find_missing_candidate_sessions(SPINE, _date(19), complete, required_sessions=5)
    assert missing == [_date(14), _date(16), _date(18)]


def test_missing_sessions_target_not_on_spine_returns_empty():
    assert chb.find_missing_candidate_sessions(SPINE, dt.date(1999, 1, 1), set()) == []


def test_default_bootstrap_depth_matches_novelty_requirement():
    from radar.thresholds import DEFAULT_NOVELTY_THRESHOLDS
    assert chb.DEFAULT_BOOTSTRAP_DEPTH == DEFAULT_NOVELTY_THRESHOLDS.lookback_sessions == 5


# --------------------------------------------------------------------------- chronological replay
def test_backfill_processes_chronologically_oldest_first(tmp_path, monkeypatch):
    candidates = {_date(i): [make_candidate(f"S{i}", _date(i))] for i in (14, 15, 16, 17, 18)}
    _stub_pipeline(monkeypatch, candidates)

    result = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(17), _date(14), _date(18), _date(15), _date(16)],  # deliberately scrambled
        out_dir=str(tmp_path))

    assert result.processed_sessions == [_date(14), _date(15), _date(16), _date(17), _date(18)]


# --------------------------------------------------------------------------- zero-candidate sessions
def test_zero_candidate_session_marked_complete_not_reprocessed(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {})  # every session produces 0 candidates

    result1 = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(14)], out_dir=str(tmp_path))
    assert result1.processed_sessions == [_date(14)]
    assert result1.candidate_rows_inserted == 0

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    marker = store.get_run_status(_date(14), result1.calculation_version)
    assert marker is not None
    assert marker.status == RunStatus.COMPLETE.value
    assert marker.candidate_count == 0
    assert store.is_session_complete(_date(14), result1.calculation_version)
    store.close()

    result2 = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(14)], out_dir=str(tmp_path))
    assert result2.already_complete_sessions == [_date(14)]
    assert result2.processed_sessions == []


def test_gap_detection_treats_zero_candidate_session_as_processed(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {})
    chb.backfill_candidate_history(spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
                                   sessions=[_date(14)], out_dir=str(tmp_path))

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    complete = store.get_complete_sessions(chb.CALCULATION_VERSION, before=_date(19))
    store.close()
    missing = chb.find_missing_candidate_sessions(SPINE, _date(19), complete, required_sessions=5)
    assert _date(14) not in missing  # processed (even with 0 candidates), not "missing"
    assert missing == [_date(15), _date(16), _date(17), _date(18)]


# --------------------------------------------------------------------------- failure / retry
def test_failed_session_remains_eligible_for_retry(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(14): [make_candidate("AAA", _date(14))]})

    original = dp._run_detectors
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated: detector calculation failed")
        return original(*a, **k)
    monkeypatch.setattr(dp, "_run_detectors", flaky)

    result1 = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(14)], out_dir=str(tmp_path))
    assert result1.failed_sessions == [_date(14)]
    assert result1.processed_sessions == []

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    marker = store.get_run_status(_date(14), result1.calculation_version)
    assert marker.status == RunStatus.FAILED.value
    store.close()

    result2 = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(14)], out_dir=str(tmp_path))
    assert result2.processed_sessions == [_date(14)]  # retried, now succeeds

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    assert store.is_session_complete(_date(14), result2.calculation_version)
    store.close()


def test_candidates_persisted_before_complete_marker(tmp_path, monkeypatch):
    """If marking COMPLETE fails AFTER candidates were already saved, the candidate rows must
    still exist (never rolled back), but the session must NOT read as COMPLETE - the ordering
    the packet spec requires (section 13)."""
    _stub_pipeline(monkeypatch, {_date(14): [make_candidate("AAA", _date(14))]})

    original_mark_run = CandidateHistoryStore.mark_run
    calls = {"n": 0}

    def flaky_mark_run(self, session_date, calculation_version, status, candidate_count,
                       processed_at=None):
        calls["n"] += 1
        if calls["n"] == 1 and status == RunStatus.COMPLETE:
            raise RuntimeError("simulated: disk full while marking COMPLETE")
        return original_mark_run(self, session_date, calculation_version, status,
                                 candidate_count, processed_at)
    monkeypatch.setattr(CandidateHistoryStore, "mark_run", flaky_mark_run)

    result = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(14)], out_dir=str(tmp_path))
    assert result.failed_sessions == [_date(14)]

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    assert len(store.get_session_candidates(_date(14))) == 1  # candidates WERE persisted
    assert not store.is_session_complete(_date(14), result.calculation_version)  # but not COMPLETE
    store.close()


# --------------------------------------------------------------------------- version awareness
def test_calculation_version_recorded_on_run_marker(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(14): [make_candidate("AAA", _date(14))]})
    result = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(14)], out_dir=str(tmp_path))

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    marker = store.get_run_status(_date(14), result.calculation_version)
    assert marker.calculation_version == result.calculation_version
    store.close()


def test_old_calculation_version_not_treated_as_current(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(14): [make_candidate("AAA", _date(14))]})
    chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        sessions=[_date(14)], out_dir=str(tmp_path), calculation_version="0.9-old")

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    assert store.is_session_complete(_date(14), "0.9-old")
    assert not store.is_session_complete(_date(14), "1.0")  # a DIFFERENT version - never conflated
    complete_under_current = store.get_complete_sessions("1.0", before=_date(19))
    store.close()
    assert _date(14) not in complete_under_current
    missing = chb.find_missing_candidate_sessions(SPINE, _date(19), complete_under_current, 5)
    assert _date(14) in missing  # still needs (re)processing under the current version


# --------------------------------------------------------------------------- no side effects
def test_backfill_creates_no_editorial_selections(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(14): [make_candidate("AAA", _date(14))]})
    chb.backfill_candidate_history(spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
                                   sessions=[_date(14)], out_dir=str(tmp_path))

    store = EditorialStore(ed_path(str(tmp_path)))
    assert store.get_session_selections(_date(14)) == []
    store.close()


def test_backfill_creates_no_daily_artifacts(tmp_path, monkeypatch):
    import os
    _stub_pipeline(monkeypatch, {_date(14): [make_candidate("AAA", _date(14))]})
    chb.backfill_candidate_history(spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
                                   sessions=[_date(14)], out_dir=str(tmp_path))

    radar_dir = os.path.join(str(tmp_path), "radar")
    files = os.listdir(radar_dir) if os.path.isdir(radar_dir) else []
    assert not any(f.startswith("daily_radar_") for f in files)


# --------------------------------------------------------------------------- idempotency
def test_backfill_twice_over_same_range_is_idempotent(tmp_path, monkeypatch):
    candidates = {_date(14): [make_candidate("AAA", _date(14))],
                 _date(15): [make_candidate("BBB", _date(15))]}
    _stub_pipeline(monkeypatch, candidates)

    r1 = chb.backfill_candidate_history(spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
                                        sessions=[_date(14), _date(15)], out_dir=str(tmp_path))
    assert r1.candidate_rows_inserted == 2

    r2 = chb.backfill_candidate_history(spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
                                        sessions=[_date(14), _date(15)], out_dir=str(tmp_path))
    assert r2.already_complete_sessions == [_date(14), _date(15)]
    assert r2.candidate_rows_inserted == 0

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    assert store.count_rows() == 2  # no duplicates
    store.close()


# --------------------------------------------------------------------------- explicit range mode
def test_explicit_start_end_range_can_exceed_default_depth(tmp_path, monkeypatch):
    candidates = {_date(i): [make_candidate(f"S{i}", _date(i))] for i in range(0, 12)}
    _stub_pipeline(monkeypatch, candidates)

    result = chb.backfill_candidate_history(
        spine=SPINE, universe=UNIVERSE, benchmark_series=BENCHMARK,
        start_session=_date(1), end_session=_date(11), out_dir=str(tmp_path))
    # _date(0) has no prior spine session and is excluded by the range; 1..11 = 11 sessions,
    # comfortably more than DEFAULT_BOOTSTRAP_DEPTH (5) - proves the explicit utility has no
    # ceiling of its own (packet spec section 18).
    assert len(result.processed_sessions) == 11


# --------------------------------------------------------------------------- daily_pipeline integration
def test_run_daily_radar_marks_its_own_session_complete(tmp_path, monkeypatch):
    """A live daily run's OWN session must also get a `candidate_history_runs` COMPLETE marker,
    not just backfilled prior sessions - otherwise a future session's auto-bootstrap preflight
    would find yesterday's already-processed session "missing" and redundantly re-backfill it
    every subsequent run."""
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    marker = store.get_run_status(_date(19), result.calculation_versions["composite"])
    assert marker is not None
    assert marker.status == RunStatus.COMPLETE.value
    assert marker.candidate_count == result.composite_candidate_count
    store.close()

    # A later session's gap detection must now see _date(19) as already processed.
    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    complete = store.get_complete_sessions(result.calculation_versions["composite"], before=_date(24))
    store.close()
    assert _date(19) in complete


def test_run_daily_radar_auto_bootstraps_from_empty_history(tmp_path, monkeypatch):
    """Fresh deployment: empty candidate-history DB, existing OHLCV/benchmark coverage. Calling
    `run_daily_radar` alone must backfill the required prior sessions AND complete the normal
    run - no manual preparation (packet spec section 31)."""
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))

    assert result.pipeline_status != "FAILED"
    assert result.stories and result.stories[0].instrument == "AAA"

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    for d in (_date(14), _date(15), _date(16), _date(17), _date(18)):
        assert store.is_session_complete(d, "1.0"), f"{d} should have been auto-backfilled"
    store.close()


def test_run_daily_radar_recovers_from_multi_session_gap(tmp_path, monkeypatch):
    """Downtime recovery: candidate history exists through an early session, several canonical
    sessions are missing, target is a later session - the gap must be filled chronologically
    before the normal run proceeds (packet spec section 33)."""
    _stub_pipeline(monkeypatch, {_date(10): [make_candidate("ZZZ", _date(10))],
                                 _date(19): [make_candidate("AAA", _date(19))]})
    dp.run_daily_radar(_date(10), out_dir=str(tmp_path))  # seeds history through session 10

    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result.pipeline_status != "FAILED"

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    for d in (_date(14), _date(15), _date(16), _date(17), _date(18)):
        assert store.is_session_complete(d, "1.0")
    store.close()


def test_run_daily_radar_fails_safely_beyond_auto_backfill_limit(tmp_path, monkeypatch):
    """A gap larger than MAX_AUTO_BACKFILL_SESSIONS must fail safely, never silently replay a
    large amount of history during a normal scheduled run (packet spec section 34)."""
    long_spine = [_date(i) for i in range(40)]
    long_benchmark = [{"date": d, "close": 20000.0 + i} for i, d in enumerate(long_spine)]
    monkeypatch.setattr(dp.relative_acquisition, "build_market_benchmark_series",
                        lambda period="1y": long_benchmark)
    monkeypatch.setattr(dp.market, "get_universe", lambda name: dict(UNIVERSE))
    monkeypatch.setattr(dp.ohlcv_service, "load_universe", lambda *a, **k: __import__(
        "test_daily_pipeline")._FakeDataset())
    monkeypatch.setattr(dp.session_alignment, "align_dataset_to_spine",
                        lambda dataset, benchmark: __import__(
                            "test_daily_pipeline")._FakeAlignmentReport())
    monkeypatch.setattr(dp.acquisition, "build_universe_relative_volume_facts",
                        lambda *a, **k: ([], {}))
    monkeypatch.setattr(dp.volume, "scan_universe", lambda *a, **k: __import__(
        "test_daily_pipeline")._FakeSnapshot())
    monkeypatch.setattr(dp.technical, "scan_technical_universe", lambda *a, **k: __import__(
        "test_daily_pipeline")._FakeSnapshot())
    monkeypatch.setattr(dp.relative, "scan_relative_performance_universe", lambda *a, **k: __import__(
        "test_daily_pipeline")._FakeSnapshot())
    from test_daily_pipeline import _FakeComposite
    monkeypatch.setattr(dp.composite, "build_candidates",
                        lambda *a, **k: _FakeComposite(candidates=[]))

    # No prior candidate history at all, target session index 30 -> would need 20 prior sessions
    # (> MAX_AUTO_BACKFILL_SESSIONS=10) if the default lookback were larger; force a large gap
    # by requesting a target far beyond any existing coverage with the default 5-session
    # lookback still small, so instead simulate a real gap by lowering nothing and pre-seeding
    # NO history: with only 5 required, this alone will not exceed the limit, so directly
    # exercise the limit by monkeypatching the lookback threshold used for this run.
    monkeypatch.setattr(dp, "DEFAULT_NOVELTY_THRESHOLDS",
                        type(dp.DEFAULT_NOVELTY_THRESHOLDS)(lookback_sessions=15))

    result = dp.run_daily_radar(long_spine[30], out_dir=str(tmp_path))
    assert result.pipeline_status == "FAILED"
    assert any(i.code == "CANDIDATE_HISTORY_GAP_EXCEEDS_AUTO_BACKFILL_LIMIT" for i in result.issues)

    ed_store = EditorialStore(ed_path(str(tmp_path)))
    assert ed_store.get_session_selections(long_spine[30]) == []  # no partial editorial state
    ed_store.close()


def test_explicit_backfill_utility_can_resolve_what_auto_bootstrap_refused(tmp_path, monkeypatch):
    """After the excessive-gap failure above, the explicit utility (no ceiling) can still
    recover the range (packet spec section 18/34)."""
    long_spine = [_date(i) for i in range(40)]
    long_benchmark = [{"date": d, "close": 20000.0 + i} for i, d in enumerate(long_spine)]
    candidates = {}
    _stub_pipeline(monkeypatch, candidates)
    monkeypatch.setattr(dp.relative_acquisition, "build_market_benchmark_series",
                        lambda period="1y": long_benchmark)

    result = chb.backfill_candidate_history(
        spine=long_spine, universe=UNIVERSE, benchmark_series=long_benchmark,
        start_session=long_spine[15], end_session=long_spine[29], out_dir=str(tmp_path))
    assert len(result.processed_sessions) == 15  # far more than MAX_AUTO_BACKFILL_SESSIONS (10)


def test_same_session_rerun_after_bootstrap_reprocesses_nothing(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    count_after_first = store.count_rows()
    store.close()

    result2 = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result2.stories and result2.stories[0].instrument == "AAA"

    store = CandidateHistoryStore(ch_path(str(tmp_path)))
    assert store.count_rows() == count_after_first  # 0 duplicate rows
    store.close()
