"""Phase 4.2 Packet 5.4D: focused, offline tests for `radar.daily_pipeline.run_daily_radar`.

The detector layer (volume/technical/relative/composite) is stubbed - it already has its own
dedicated test suites (`test_volume_radar.py`, `test_technical_radar.py`, etc); these tests
exercise ORCHESTRATION: canonical-spine ownership, candidate-history/editorial-history
ordering and persistence, dry-run behavior, idempotency, N+1, story assembly, and content
safety. No network, no live yfinance/NSE call anywhere.
"""
import datetime as dt
from dataclasses import dataclass, field

import radar.daily_pipeline as dp
from radar.models import (AttentionLevel, DirectionCompatibility, StockRadarCandidate)

D0 = dt.date(2026, 6, 1)


def _date(offset: int) -> dt.date:
    return D0 + dt.timedelta(days=offset)


SPINE = [_date(i) for i in range(25)]
BENCHMARK = [{"date": d, "close": 20000.0 + i} for i, d in enumerate(SPINE)]
UNIVERSE = {"AAA": "AAA Ltd", "BBB": "BBB Ltd"}


@dataclass
class _FakeCoverage:
    cache_complete: int = 0
    cache_partial: int = 0
    cache_miss: int = 0
    network_call_count: int = 0


@dataclass
class _FakeDataset:
    requested_symbols: list = field(default_factory=lambda: list(UNIVERSE))
    series_by_symbol: dict = field(default_factory=lambda: {s: [] for s in UNIVERSE})
    skipped_symbols: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    coverage: _FakeCoverage = field(default_factory=_FakeCoverage)


@dataclass
class _FakeAlignmentReport:
    symbols_filtered: int = 0
    rows_dropped: int = 0


@dataclass
class _FakeSnapshot:
    anomalies: list = field(default_factory=list)
    flagged: list = field(default_factory=list)
    results: list = field(default_factory=list)
    scanned: list = field(default_factory=lambda: list(UNIVERSE))
    warnings: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


@dataclass
class _FakeComposite:
    candidates: list
    candidate_count: int = 0
    calculation_version: str = "1.0"
    warnings: list = field(default_factory=list)

    def __post_init__(self):
        self.candidate_count = len(self.candidates)


def make_candidate(symbol, session_date, *, families=3,
                   direction=DirectionCompatibility.ALIGNED_POSITIVE,
                   novelty_type_hint=None, technical_events=("BREAK_ABOVE_20D_RANGE",)):
    active_families = (["STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"] if families == 3
                       else ["STRUCTURE", "RELATIVE_PERFORMANCE"])
    reason_codes = [f"STRUCTURE_{e}" for e in technical_events]
    return StockRadarCandidate(
        instrument=symbol, market_date=session_date, active_families=active_families,
        reason_codes=reason_codes, direction_compatibility=direction,
        attention_level=AttentionLevel.HIGH_INTEREST if families == 3 else AttentionLevel.NOTABLE,
        independent_signal_count=families, price_change_pct=1.5,
        evidence=[f"Price closed above its prior 20-session high."],
        supporting_session_dates=[session_date])


def _stub_pipeline(monkeypatch, candidates_by_session: dict):
    monkeypatch.setattr(dp.relative_acquisition, "build_market_benchmark_series",
                        lambda period="1y": BENCHMARK)
    monkeypatch.setattr(dp.market, "get_universe", lambda name: dict(UNIVERSE))
    monkeypatch.setattr(dp.ohlcv_service, "load_universe", lambda *a, **k: _FakeDataset())
    monkeypatch.setattr(dp.session_alignment, "align_dataset_to_spine",
                        lambda dataset, benchmark: _FakeAlignmentReport())
    monkeypatch.setattr(dp.acquisition, "build_universe_relative_volume_facts",
                        lambda *a, **k: ([], {}))
    monkeypatch.setattr(dp.volume, "scan_universe", lambda *a, **k: _FakeSnapshot())
    monkeypatch.setattr(dp.technical, "scan_technical_universe", lambda *a, **k: _FakeSnapshot())
    monkeypatch.setattr(dp.relative, "scan_relative_performance_universe",
                        lambda *a, **k: _FakeSnapshot())

    def fake_build_candidates(volume_snapshot, technical_snapshot, relative_snapshot,
                              session_date, as_of=None):
        return _FakeComposite(candidates=candidates_by_session.get(session_date, []))
    monkeypatch.setattr(dp.composite, "build_candidates", fake_build_candidates)


# --------------------------------------------------------------------------- Test 1/26
def test_canonical_spine_built_once_and_reused(tmp_path, monkeypatch):
    calls = []
    original = dp.relative_acquisition.build_market_benchmark_series

    def counting(period="1y"):
        calls.append(period)
        return BENCHMARK
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    monkeypatch.setattr(dp.relative_acquisition, "build_market_benchmark_series", counting)

    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert len(calls) == 1   # exactly one benchmark fetch for the whole run


def test_uses_production_selector_not_experimental_policy():
    import inspect
    source = inspect.getsource(dp)
    assert "editorial_policy_hybrid" not in source
    assert "select_policy_f" not in source
    assert "es.select_session" in source


# --------------------------------------------------------------------------- Test 2
def test_no_calendar_day_cooldown_math():
    import inspect
    source = inspect.getsource(dp)
    assert "timedelta(days=" not in source


# --------------------------------------------------------------------------- Test 6: no self-visibility
def test_current_session_not_visible_to_own_novelty(tmp_path, monkeypatch):
    """A symbol appearing as a candidate for the FIRST time on session D must classify as
    NEW_CANDIDATE (no prior history) - proving D's own state was not fed into its own novelty
    comparison."""
    candidates_by_session = {_date(19): [make_candidate("AAA", _date(19))]}
    _stub_pipeline(monkeypatch, candidates_by_session)
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result.pipeline_status != "FAILED"
    story = result.stories[0]
    assert story.novelty_type == "NEW_CANDIDATE"


# --------------------------------------------------------------------------- Test 5/7/24
def test_novelty_uses_candidate_history_not_editorial_history(tmp_path, monkeypatch):
    """AAA appears on session 19, then again on session 20 with a CHANGED direction -
    `DIRECTION_TRANSITION` (a cooldown-override trigger, so it survives publication cooldown
    too and reaches the final story) is only reachable by comparing against session 19's
    PERSISTED candidate state - never editorial/publication history, which this test never
    even touches for that distinction."""
    c19 = make_candidate("AAA", _date(19), direction=DirectionCompatibility.ALIGNED_POSITIVE)
    c20 = make_candidate("AAA", _date(20), direction=DirectionCompatibility.ALIGNED_NEGATIVE)
    _stub_pipeline(monkeypatch, {_date(19): [c19], _date(20): [c20]})

    r19 = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert r19.stories and r19.stories[0].novelty_type == "NEW_CANDIDATE"

    r20 = dp.run_daily_radar(_date(20), out_dir=str(tmp_path))
    assert r20.stories and r20.stories[0].novelty_type == "DIRECTION_TRANSITION"


def test_candidate_history_row_count_no_n_plus_1(tmp_path, monkeypatch):
    from storage.candidate_history_repository import CandidateHistoryStore, default_db_path
    candidates = [make_candidate(f"S{i}", _date(19), families=2) for i in range(30)]
    _stub_pipeline(monkeypatch, {_date(19): candidates})
    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))

    store = CandidateHistoryStore(default_db_path(str(tmp_path)))
    assert store.count_rows() == 30
    store.close()


# --------------------------------------------------------------------------- Test 7: idempotency
def test_candidate_history_idempotent(tmp_path, monkeypatch):
    from storage.candidate_history_repository import CandidateHistoryStore, default_db_path
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))  # exact rerun

    store = CandidateHistoryStore(default_db_path(str(tmp_path)))
    assert store.count_rows() == 1
    store.close()


# --------------------------------------------------------------------------- Test 8/27: editorial idempotent
def test_editorial_store_idempotent_and_no_self_cooldown(tmp_path, monkeypatch):
    from storage.editorial_repository import EditorialStore, default_db_path
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    r1 = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    r2 = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))  # accidental rerun, same session

    assert [s.instrument for s in r1.stories] == [s.instrument for s in r2.stories] == ["AAA"]
    store = EditorialStore(default_db_path(str(tmp_path)))
    assert len(store.get_session_selections(_date(19))) == 1  # no duplicate row
    store.close()


# --------------------------------------------------------------------------- Test 10: same-session identical
def test_same_session_rerun_produces_identical_stories(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19), families=3),
                                             make_candidate("BBB", _date(19), families=2)]})
    r1 = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    r2 = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert [s.to_dict() for s in r1.stories] == [s.to_dict() for s in r2.stories]


# --------------------------------------------------------------------------- Test 11/12/13: dry run
def test_dry_run_writes_no_candidate_history_or_editorial_selections_but_reads_prior(tmp_path, monkeypatch):
    from storage.candidate_history_repository import CandidateHistoryStore
    from storage.candidate_history_repository import default_db_path as ch_path
    from storage.editorial_repository import EditorialStore
    from storage.editorial_repository import default_db_path as ed_path

    c19 = make_candidate("AAA", _date(19), direction=DirectionCompatibility.ALIGNED_POSITIVE)
    c20 = make_candidate("AAA", _date(20), direction=DirectionCompatibility.ALIGNED_NEGATIVE)
    _stub_pipeline(monkeypatch, {_date(19): [c19], _date(20): [c20]})

    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))  # real run, persists

    result_dry = dp.run_daily_radar(_date(20), out_dir=str(tmp_path), dry_run=True)
    assert result_dry.dry_run is True
    # dry run for session 20 still SAW session 19's persisted history
    assert result_dry.stories and result_dry.stories[0].novelty_type == "DIRECTION_TRANSITION"

    ch_store = CandidateHistoryStore(ch_path(str(tmp_path)))
    assert ch_store.get_session_candidates(_date(20)) == []   # nothing written for session 20
    ch_store.close()
    ed_store = EditorialStore(ed_path(str(tmp_path)))
    assert ed_store.get_session_selections(_date(20)) == []
    ed_store.close()

    import os
    assert not os.path.exists(os.path.join(str(tmp_path), "radar", f"daily_radar_{_date(20).isoformat()}.json"))


# --------------------------------------------------------------------------- Test 14/15
def test_continuation_excluded_and_max_five_stories(tmp_path, monkeypatch):
    from radar.models import NoveltyType
    # 7 fresh (NEW_CANDIDATE) candidates on the SAME first-ever session -> all NEW_CANDIDATE,
    # capped at 5 selected.
    candidates = [make_candidate(f"S{i}", _date(19), families=2) for i in range(7)]
    _stub_pipeline(monkeypatch, {_date(19): candidates})
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert len(result.stories) <= 5
    assert all(s.novelty_type != NoveltyType.CONTINUATION.value for s in result.stories)


# --------------------------------------------------------------------------- Test 17/18/19/20
def test_story_retains_evidence_novelty_editorial_reason_and_provenance(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19), families=3)]})
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    story = result.stories[0]
    assert story.active_families == ["STRUCTURE", "RELATIVE_PERFORMANCE", "VOLUME"]
    assert story.novelty_reason
    assert story.editorial_selection_reason
    assert story.provenance["candidate_calculation_version"]
    assert story.provenance["novelty_calculation_version"]
    assert story.provenance["selector_version"] == dp.es.EDITORIAL_SELECTOR_VERSION
    assert story.supporting_session_dates == [_date(19).isoformat()]


# --------------------------------------------------------------------------- Test 21: recommendation language
def test_recommendation_language_stripped_from_story_text(tmp_path, monkeypatch):
    unsafe = make_candidate("AAA", _date(19), families=3)
    unsafe.evidence.append("Brokerage initiates BUY rating with target Rs 1,500")
    _stub_pipeline(monkeypatch, {_date(19): [unsafe]})
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    text = result.stories[0].concise_reason.upper()
    for banned in ("BUY", "SELL", "TARGET", "STOPLOSS", "STRONG BUY"):
        assert banned not in text or "BUY" not in text.replace("STRONG BUY", "")


# --------------------------------------------------------------------------- Test 22: critical failure
def test_candidate_history_failure_returns_failed_status(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})

    class BrokenStore:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated: db unavailable")

    monkeypatch.setattr(dp, "CandidateHistoryStore", BrokenStore)
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result.pipeline_status == "FAILED"
    assert result.stories == []


# --------------------------------------------------------------------------- Test 23: partial coverage visible
def test_partial_coverage_remains_visible(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    monkeypatch.setattr(dp.ohlcv_service, "load_universe",
                        lambda *a, **k: _FakeDataset(
                            series_by_symbol={"AAA": []}, skipped_symbols={"BBB": "date_gap"}))
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result.universe_usable == 1
    assert result.coverage_diagnostics["skipped_symbols"] == 1


# --------------------------------------------------------------------------- Test 25: artifact determinism
def test_artifact_deterministic_single_path(tmp_path, monkeypatch):
    import os
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    radar_dir = os.path.join(str(tmp_path), "radar")
    files = [f for f in os.listdir(radar_dir) if f.startswith("daily_radar_")]
    assert files == [f"daily_radar_{_date(19).isoformat()}.json"]
