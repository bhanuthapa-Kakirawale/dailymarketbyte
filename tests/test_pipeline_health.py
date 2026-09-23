"""Phase 4.2 Packet 5.4E, Part B: pipeline health severity semantics (INFO/WARNING/ERROR ->
SUCCESS/DEGRADED/FAILED). Offline only - reuses `tests/test_daily_pipeline.py`'s stubbed
detector pipeline. No detector/composite/novelty/editorial logic is exercised differently here
than in 5.4D - only `DailyRadarResult.pipeline_status`/`issues` derivation.
"""
import radar.daily_pipeline as dp
from test_daily_pipeline import BENCHMARK, SPINE, UNIVERSE, _date, _stub_pipeline, make_candidate


# --------------------------------------------------------------------------- pure classification
def test_placeholder_row_filtering_classified_info():
    code, severity = dp._classify_warning(
        "session spine filter: 3 provider row(s) for confirmed non-trading dates excluded "
        "before completeness validation")
    assert severity == dp.IssueSeverity.INFO
    assert code == "SESSION_PLACEHOLDER_ROWS_FILTERED"


def test_rvol_percentile_history_absence_classified_info():
    code, severity = dp._classify_warning(
        "no historical database available; classification limited to today's reading, "
        "percentile omitted")
    assert severity == dp.IssueSeverity.INFO
    assert code == "RVOL_PERCENTILE_HISTORY_UNAVAILABLE"


def test_benchmark_unavailable_classified_warning():
    code, severity = dp._classify_warning(
        "benchmark series empty or unavailable; market-relative unavailable for every symbol")
    assert severity == dp.IssueSeverity.WARNING


def test_unrecognised_warning_defaults_to_warning_never_info():
    code, severity = dp._classify_warning("some brand-new failure mode nobody classified yet")
    assert severity == dp.IssueSeverity.WARNING
    assert code == dp._DEFAULT_ISSUE_CODE


# --------------------------------------------------------------------------- status derivation
def test_status_info_only_is_success():
    issues = [dp.PipelineIssue(code="X", severity=dp.IssueSeverity.INFO.value, message="m", stage="s")]
    assert dp._status_from_issues(issues) == "SUCCESS"


def test_status_no_issues_is_success():
    assert dp._status_from_issues([]) == "SUCCESS"


def test_status_any_warning_is_degraded():
    issues = [dp.PipelineIssue(code="A", severity=dp.IssueSeverity.INFO.value, message="m", stage="s"),
             dp.PipelineIssue(code="B", severity=dp.IssueSeverity.WARNING.value, message="m", stage="s")]
    assert dp._status_from_issues(issues) == "DEGRADED"


def test_status_any_error_is_failed():
    issues = [dp.PipelineIssue(code="A", severity=dp.IssueSeverity.WARNING.value, message="m", stage="s"),
             dp.PipelineIssue(code="B", severity=dp.IssueSeverity.ERROR.value, message="m", stage="s")]
    assert dp._status_from_issues(issues) == "FAILED"


def test_status_not_derived_from_warning_count_alone():
    """Multiple INFO issues must not accumulate into DEGRADED - only severity matters, never
    len(warnings) > 0 (packet spec section 24)."""
    issues = [dp.PipelineIssue(code="X", severity=dp.IssueSeverity.INFO.value, message=f"m{i}", stage="s")
             for i in range(5)]
    assert dp._status_from_issues(issues) == "SUCCESS"


# --------------------------------------------------------------------------- DailyRadarResult shape
def test_issue_counts_tally_correctly():
    result = dp.DailyRadarResult(
        session_date="2026-01-01", generated_at="2026-01-01T00:00:00", pipeline_status="DEGRADED",
        dry_run=False, universe_requested=0, universe_usable=0, volume_event_count=0,
        technical_event_count=0, relative_count=0, composite_candidate_count=0,
        novel_candidate_count=0, continuation_count=0, editorial_selection_count=0,
        issues=[dp.PipelineIssue(code="A", severity="INFO", message="m", stage="s"),
               dp.PipelineIssue(code="B", severity="INFO", message="m", stage="s"),
               dp.PipelineIssue(code="C", severity="WARNING", message="m", stage="s")])
    counts = result.issue_counts()
    assert counts == {"INFO": 2, "WARNING": 1, "ERROR": 0}


def test_warnings_field_preserved_for_backward_compatibility():
    """`warnings` (plain strings) must still exist and carry the same text `issues` classifies -
    packet spec section 28: do not casually delete the field."""
    result = dp._failed_result(_date(19), __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc), "some failure reason", dry_run=False)
    assert result.warnings == ["some failure reason"]
    assert len(result.issues) == 1
    assert result.issues[0].message == "some failure reason"
    assert result.issues[0].severity == "ERROR"


def test_failed_result_has_error_issue_with_specific_code():
    result = dp._failed_result(_date(19), __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc), "boom", dry_run=False, issue_code="MY_CODE")
    assert result.pipeline_status == "FAILED"
    assert result.issues[0].code == "MY_CODE"
    assert result.issues[0].severity == dp.IssueSeverity.ERROR.value


# --------------------------------------------------------------------------- integration: real run
def test_clean_stubbed_run_status_is_success_not_ok(tmp_path, monkeypatch):
    """The stubbed detector snapshots used across `test_daily_pipeline.py` never carry a
    WARNING-severity message on their own (only the always-present, by-design INFO conditions
    from real `radar.volume`/`radar.ohlcv_service` code, if any leak through the stubs - here
    they don't, since volume/technical/relative/composite snapshots are fully replaced), so a
    clean run must report SUCCESS - the renamed, explicit status this packet introduces (not the
    prior implicit "OK")."""
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result.pipeline_status == "SUCCESS"
    assert result.issue_counts()["ERROR"] == 0


def test_universe_constituent_failure_surfaces_as_warning_issue_and_degrades(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})
    monkeypatch.setattr(dp.market, "get_universe",
                        lambda name: (_ for _ in ()).throw(RuntimeError("simulated: NSE unreachable")))
    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result.pipeline_status == "DEGRADED"
    assert any(i.code == "UNIVERSE_CONSTITUENTS_UNAVAILABLE" and i.severity == "WARNING"
              for i in result.issues)


def test_critical_failure_still_reports_failed(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, {_date(19): [make_candidate("AAA", _date(19))]})

    class BrokenStore:
        def __init__(self, *a, **k):
            raise RuntimeError("simulated: db unavailable")
    monkeypatch.setattr(dp, "CandidateHistoryStore", BrokenStore)

    result = dp.run_daily_radar(_date(19), out_dir=str(tmp_path))
    assert result.pipeline_status == "FAILED"
    assert result.stories == []
    assert any(i.severity == "ERROR" for i in result.issues)


# --------------------------------------------------------------------------- no intelligence change
def test_no_change_to_detector_composite_novelty_editorial_source(tmp_path):
    """A textual guard: Part B must not have touched any of the intelligence-selection modules
    themselves (packet spec sections 29/40) - only `radar/daily_pipeline.py` and the new
    `radar/candidate_history_backfill.py` were expected to change in this packet."""
    import inspect
    import radar.composite as composite_mod
    import radar.editorial_selector as es_mod
    import radar.novelty as novelty_mod
    import radar.technical as technical_mod
    import radar.volume as volume_mod
    for mod in (composite_mod, es_mod, novelty_mod, technical_mod, volume_mod):
        source = inspect.getsource(mod)
        assert "IssueSeverity" not in source
        assert "PipelineIssue" not in source
        assert "candidate_history_backfill" not in source
