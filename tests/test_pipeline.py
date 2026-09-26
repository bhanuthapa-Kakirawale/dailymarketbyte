"""End-to-end run of the production (non-demo) branch, fully offline.

The demo branch skips collect()'s real path, and the live path cannot be exercised in tests,
so every provider call is stubbed here instead. That keeps the non-demo wiring - which is
what actually publishes - under test rather than only verified by hand on a good data day.
"""
import datetime as dt
import json
import os

import pytest

from conftest import IST, PREV_SESSION, SESSION, full_movers_coverage

import main
from core import MarketReport, Metric, ValidationStatus
from storage import MarketHistory


class _Args:
    def __init__(self, demo=False, upload=False, force=False):
        self.demo, self.upload, self.force = demo, upload, force


@pytest.fixture
def offline_pipeline(monkeypatch, tmp_path, market_dict, movers, sectors, tiles, ai_facts, events):
    """Stub every network boundary and every expensive renderer call."""
    gainers, losers = movers
    rendered = {}

    monkeypatch.setattr(main, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(main.report_builder, "_now_ist",
                        lambda: dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST))
    monkeypatch.setattr(main, "now_ist", lambda: dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST))

    monkeypatch.setattr(main.market, "get_market", lambda: dict(market_dict))
    monkeypatch.setattr(main.market, "NSE", lambda: object())
    monkeypatch.setattr(main.market, "nse_all_indices",
                        lambda nse, d: {"NIFTY 50": {"last": 25141.10, "pct": 0.29,
                                                     "observed_at": dt.datetime(2026, 9, 18, 15, 30,
                                                                                tzinfo=IST),
                                                     "source_timestamp": "18-Sep-2026 15:30:00"}})
    monkeypatch.setattr(main.market, "fii_dii_nse",
                        lambda nse, d: {"fii": -1240.5, "dii": 2105.3, "source": "NSE"})
    monkeypatch.setattr(main.market, "get_sectors", lambda *a, **k: list(sectors))
    monkeypatch.setattr(main.market, "get_globals", lambda: list(tiles))
    monkeypatch.setattr(main.market, "get_universe", lambda name: {"STOCK-A": "Alpha Ltd"})
    monkeypatch.setattr(main.market, "get_movers_audited",
                        lambda *a, **k: (list(gainers), list(losers), full_movers_coverage()))
    monkeypatch.setattr(main.news, "ai_pass", lambda *a, **k: (
        dict(ai_facts),
        {"text": "Broad-based buying lifted the index.", "source": "GEMINI", "publisher": None},
        list(events)))

    monkeypatch.setattr(main.chart, "make_chart", lambda m, prefix: _stub_chart(tmp_path, m))
    monkeypatch.setattr(main.music, "get_music", lambda *a, **k: None)

    def _render(scenes, info, ticker, music_path, out_path, demo=False):
        rendered["scenes"] = scenes
        rendered["ticker"] = ticker
        rendered["info"] = info
        open(out_path, "wb").write(b"stub")

    monkeypatch.setattr(main.video, "render", _render)

    # Media probing is the mockable boundary: tests never invoke ffmpeg, but the production
    # QA implementation stays exactly as it ships.
    monkeypatch.setattr(main, "check_video", _stub_video_qa)
    return rendered


def _stub_video_qa(video_path, expected_duration, metadata_path=None, report_path=None, **kw):
    """A passing QA result, built through the real result type so the gate logic is real."""
    from qa.video_qa import QACheck, QAStatus, VideoQAResult
    result = VideoQAResult(checked_at=dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST),
                           video_path=video_path)
    result.add(QACheck("stubbed", QAStatus.PASS, expected="pass", actual="pass"))
    return result


def _failing_video_qa(video_path, expected_duration, metadata_path=None, report_path=None, **kw):
    from qa.video_qa import QACheck, QAStatus, VideoQAResult
    result = VideoQAResult(checked_at=dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST),
                           video_path=video_path)
    result.add(QACheck("resolution", QAStatus.FAIL, expected="1080x1920", actual="640x480",
                       message="wrong resolution for a vertical Short"))
    return result


def _stub_chart(tmp_path, m):
    """Three 1x1 PNGs, enough for NiftyScene to open as layers."""
    from PIL import Image
    paths = []
    for stage in range(3):
        p = str(tmp_path / f"chart_{stage}.png")
        Image.new("RGB", (980, 680), (13, 21, 48)).save(p)
        paths.append(p)
    return paths


# --------------------------------------------------------------------- the run
def test_production_run_writes_report_and_renders(offline_pipeline, tmp_path):
    out = main.run(_Args())
    assert out and os.path.exists(out)
    reports = os.listdir(tmp_path / "reports")
    assert reports and all(not r.endswith("_DEMO.json") for r in reports)


def _scene_types(rendered):
    return [s.plan.scene_type.value for s in rendered["scenes"]]


def _scene_of(rendered, scene_type):
    return next(s for s in rendered["scenes"] if s.plan.scene_type.value == scene_type)


def test_rendered_scenes_are_fed_from_the_report(offline_pipeline, tmp_path):
    """Everything the renderer received must match what the report stored.

    The renderer now draws an editorial plan rather than the report's raw sections, so the
    check follows the plan - which is still derived only from the report.
    """
    main.run(_Args())
    report_path = next((tmp_path / "reports").iterdir())
    report = MarketReport.from_json(report_path.read_text(encoding="utf-8"))

    nifty = _scene_of(offline_pipeline, "NIFTY").plan
    assert nifty.primary_numeric == report.nifty["change_pct"]
    assert nifty.primary_value == f"{report.nifty['change_pct']:+.2f}%"

    gainers = _scene_of(offline_pipeline, "GAINERS").plan
    losers = _scene_of(offline_pipeline, "LOSERS").plan
    stored = {r["symbol"]: r["change_pct"] for r in report.gainers + report.losers}
    for item in gainers.items + losers.items:
        assert item.title in stored
        assert item.numeric == stored[item.title]


def test_nse_timestamp_survives_into_the_written_report(offline_pipeline, tmp_path):
    main.run(_Args())
    report = MarketReport.from_json(next((tmp_path / "reports").iterdir()).read_text(encoding="utf-8"))
    close = report.facts_for(Metric.INDEX_CLOSE)[0]
    nse_obs = next(o for o in close.observations if o.source_name == "nse_website")
    assert nse_obs.observed_at is not None
    assert nse_obs.observed_at.hour == 15 and nse_obs.observed_at.minute == 30


def test_nse_and_yahoo_together_verify_the_close(offline_pipeline, tmp_path):
    main.run(_Args())
    report = MarketReport.from_json(next((tmp_path / "reports").iterdir()).read_text(encoding="utf-8"))
    close = report.facts_for(Metric.INDEX_CLOSE)[0]
    assert close.validation_status is ValidationStatus.VERIFIED
    assert {o.independence_group for o in close.observations} >= {"NSE", "YAHOO"}


def test_report_records_the_source_registry(offline_pipeline, tmp_path):
    main.run(_Args())
    report = MarketReport.from_json(next((tmp_path / "reports").iterdir()).read_text(encoding="utf-8"))
    assert {"nse_website", "yahoo_finance", "gemini"} <= set(report.sources)
    assert report.sources["nse_website"]["source_family"] == "EXCHANGE"
    assert report.sources["gemini"]["source_family"] == "AI_DISCOVERY"


def test_blocked_report_renders_nothing_and_publishes_nothing(offline_pipeline, monkeypatch, tmp_path):
    """The publication gate is binding: an unfit report stops the run before rendering."""
    monkeypatch.setattr(main, "check_publication", lambda report, demo=False: False)
    out = main.run(_Args(upload=True))
    assert out is None
    assert "scenes" not in offline_pipeline, "nothing should have been rendered"
    assert os.listdir(tmp_path / "reports"), "the report is still written for diagnosis"


def test_upload_is_never_attempted_without_an_upload_flag(offline_pipeline, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("publish() must not run without --upload")

    monkeypatch.setattr(main, "publish", _boom)
    assert main.run(_Args(upload=False)) is not None


def test_cold_start_renders_without_a_context_scene(offline_pipeline, tmp_path):
    """With no prior sessions there is no historical context to show, and the Short simply
    becomes shorter rather than carrying an empty scene."""
    from editorial import MAX_SHORT_DURATION, MIN_SHORT_DURATION

    main.run(_Args())
    assert "CONTEXT" not in _scene_types(offline_pipeline)
    assert _scene_types(offline_pipeline)[0] == "HOOK", "the Short opens on the hook"
    total = sum(s.dur for s in offline_pipeline["scenes"])
    assert MIN_SHORT_DURATION <= total <= MAX_SHORT_DURATION
    assert os.listdir(tmp_path / "intelligence"), "the artifact is written even when empty"


def test_intelligence_artifact_is_written_every_run(offline_pipeline, tmp_path):
    main.run(_Args())
    files = os.listdir(tmp_path / "intelligence")
    payload = json.loads((tmp_path / "intelligence" / files[0]).read_text(encoding="utf-8"))
    assert payload["report_id"] == "20260921_PRE_MARKET"
    assert payload["intelligence_schema_version"] == "1.0"


def test_context_scene_appears_once_history_exists(offline_pipeline, tmp_path):
    """Second run of a different session, with the first already in history."""
    from conftest_intelligence import seed, session_report, trading_sessions

    main.run(_Args())
    with MarketHistory(str(tmp_path / "data" / "market_history.db")) as history:
        sessions = trading_sessions(8, last=dt.date(2026, 9, 17))
        seed(history, [session_report(s, pct=0.1, fii=-100.0, dii=50.0, vix=12.0)
                       for s in sessions])

    main.run(_Args())
    assert "CONTEXT" in _scene_types(offline_pipeline)
    context = _scene_of(offline_pipeline, "CONTEXT").plan
    assert context.items and context.source_insight_ids


def test_durations_are_content_driven_not_a_fixed_budget(offline_pipeline):
    """The Short no longer targets 75s for compatibility; each scene is as long as it needs."""
    from editorial import MAX_SHORT_DURATION, MIN_SHORT_DURATION

    main.run(_Args())
    durations = [s.dur for s in offline_pipeline["scenes"]]
    total = sum(durations)
    assert MIN_SHORT_DURATION <= total <= MAX_SHORT_DURATION
    assert total != pytest.approx(75.0)
    assert len(set(durations)) > 1, "identical durations would mean nothing adapted"


def test_reading_heavy_scenes_hide_the_ticker(offline_pipeline):
    main.run(_Args())
    by_type = {s.plan.scene_type.value: s for s in offline_pipeline["scenes"]}
    assert by_type["NIFTY"].show_ticker is False
    assert by_type["HOOK"].show_ticker is True


def test_no_rendered_scene_cycles_captions(offline_pipeline):
    """The bottom caption no longer rotates analytical sentences under a reader."""
    main.run(_Args())
    for scene in offline_pipeline["scenes"]:
        assert len(scene.captions()) <= 1, type(scene).__name__


def test_every_word_a_scene_draws_was_declared_by_the_plan(offline_pipeline):
    """`plan.public_text()` is what the final content scan and the reading budget see, so a
    scene that draws a string of its own escapes both.

    This is not hypothetical: the outro drew its own "DAILY MARKET BYTE" / "New recap every
    trading day", and the hook drew a second wordmark - none of it known to the plan.
    """
    import video
    from PIL import Image, ImageDraw

    main.run(_Args())
    surface = ImageDraw.Draw(Image.new("RGBA", (video.W, video.H)))

    for scene in offline_pipeline["scenes"]:
        declared = " ".join(scene.plan.public_text().values()).lower()
        drawn = []
        surface.text = lambda xy, text, drawn=drawn, **kw: drawn.append(text)
        scene.draw(surface, Image.new("RGBA", (video.W, video.H)), scene.dur / 2)
        for fragment in drawn:
            assert fragment.strip().rstrip(".…").lower() in declared, (
                f"{type(scene).__name__} drew {fragment!r}, which the plan never declared")


def test_intelligence_failure_does_not_stop_the_run(offline_pipeline, monkeypatch):
    """Historical context is an enhancement; losing it must not cost a publication."""
    monkeypatch.setattr(main.intelligence, "build_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("history exploded")))
    out = main.run(_Args())
    assert out and os.path.exists(out)
    assert "CONTEXT" not in _scene_types(offline_pipeline)


def test_unsafe_upstream_text_is_neutralised_and_never_reaches_the_report(
        offline_pipeline, monkeypatch, tmp_path):
    """Unsafe input does not block the run - it is removed before the report exists, so the
    published video is safe and publication correctly proceeds."""
    monkeypatch.setattr(main.news, "ai_pass", lambda *a, **k: (
        {}, {"text": "Strong BUY with target Rs 500", "source": "GEMINI", "publisher": None},
        [{"tag": "IPO", "text": "Top stocks to buy tomorrow", "source": "GEMINI_SEARCH",
          "publisher": None}]))
    assert main.run(_Args()) is not None

    report = MarketReport.from_json(next((tmp_path / "reports").iterdir()).read_text(encoding="utf-8"))
    assert report.nifty["move_summary"] == ""
    assert all("to buy" not in (e["text"] or "").lower() for e in report.events)
    assert report.content_safety["blocked_count"] >= 1
    assert report.content_safety["stage"] == "PRE_REPORT_SANITISATION"

    # The final publication scan's verdict is operational and lives outside the report.
    qa_payload = json.loads(next((tmp_path / "qa").iterdir()).read_text(encoding="utf-8"))
    assert qa_payload["final_content_qa"]["status"] == "SAFE"


def test_report_persists_to_history_before_publication(offline_pipeline, tmp_path):
    """Persistence happens before the publication gate, so an unfit report is still kept."""
    main.run(_Args())
    with MarketHistory(str(tmp_path / "data" / "market_history.db")) as history:
        reports = history.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
        assert len(reports) == 1
        stored = reports[0]
        assert stored.publication_ready is True
        assert stored.json_artifact_path and os.path.exists(stored.json_artifact_path)
        assert history.get_facts(metric="INDEX_CLOSE")


def test_run_lifecycle_is_recorded_for_a_local_run(offline_pipeline, tmp_path):
    main.run(_Args())
    with MarketHistory(str(tmp_path / "data" / "market_history.db")) as history:
        run = history.get_publication_runs()[0]
        assert run.mode == "LOCAL"
        assert run.stage == "NO_UPLOAD"
        assert run.data_qa_status == "PASSED"
        assert run.video_qa_status == "PASS"
        assert run.content_qa_status == "PASSED"
        assert run.publication_status == "NOT_ATTEMPTED"
        assert run.youtube_video_id is None


def test_unpublishable_report_is_persisted_and_records_its_failure(offline_pipeline, monkeypatch,
                                                                   tmp_path):
    """Validation failure must not mean losing the report - the history is how anyone later
    answers why a given day was not published."""
    monkeypatch.setattr(main, "check_publication", lambda report, demo=False: False)
    assert main.run(_Args(upload=True)) is None

    with MarketHistory(str(tmp_path / "data" / "market_history.db")) as history:
        assert history.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
        run = history.get_publication_runs()[0]
        assert run.failure_stage == "DATA_QA"
        assert run.publication_status == "BLOCKED"


def test_persistence_failure_blocks_upload(offline_pipeline, monkeypatch):
    """Auditability is part of publication integrity: if the run cannot be recorded, it is
    not published."""
    monkeypatch.setattr(main, "persist_report",
                        lambda *a, **k: (False, "OperationalError: disk is full"))

    def _boom(*a, **k):
        raise AssertionError("must not publish when history could not be written")

    monkeypatch.setattr(main, "publish", _boom)
    assert main.run(_Args(upload=True)) is None
    assert "scenes" not in offline_pipeline, "nothing should have been rendered"


def test_video_qa_failure_blocks_upload_but_keeps_artifacts(offline_pipeline, monkeypatch,
                                                            tmp_path):
    monkeypatch.setattr(main, "check_video", _failing_video_qa)

    def _boom(*a, **k):
        raise AssertionError("must not publish when video QA failed")

    monkeypatch.setattr(main, "publish", _boom)
    out = main.run(_Args(upload=True))

    assert out and os.path.exists(out), "the failed video is preserved for diagnosis"
    assert os.listdir(tmp_path / "reports")
    assert os.listdir(tmp_path / "qa"), "the QA artifact is written even when QA fails"
    with MarketHistory(str(tmp_path / "data" / "market_history.db")) as history:
        run = history.get_publication_runs()[0]
        assert run.failure_stage == "VIDEO_QA"
        assert run.video_qa_status == "FAIL"
        assert run.publication_status == "BLOCKED"


def test_qa_artifact_is_written_for_every_render(offline_pipeline, tmp_path):
    main.run(_Args())
    qa_files = os.listdir(tmp_path / "qa")
    assert qa_files
    payload = json.loads((tmp_path / "qa" / qa_files[0]).read_text(encoding="utf-8"))
    assert payload["report_id"] and payload["overall_status"] == "PASS"
    assert payload["report_json_artifact"]


def test_all_gates_passing_publishes_only_with_upload_flag(offline_pipeline, monkeypatch):
    published = {}
    monkeypatch.setattr(main, "publish", lambda out, meta, d: published.setdefault("id", "vid123"))

    main.run(_Args(upload=False))
    assert not published, "a no-upload run must never publish"

    main.run(_Args(upload=True))
    assert published["id"] == "vid123"


def test_publish_records_the_video_id_in_history(offline_pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "publish", lambda out, meta, d: "vid123")
    main.run(_Args(upload=True))
    with MarketHistory(str(tmp_path / "data" / "market_history.db")) as history:
        run = history.get_publication_runs()[0]
        assert run.publication_status == "PUBLISHED"
        assert run.youtube_video_id == "vid123"


def test_rerunning_the_same_session_does_not_duplicate_history(offline_pipeline, tmp_path):
    """GitHub Actions retries and manual reruns must not multiply canonical history."""
    main.run(_Args())
    main.run(_Args())
    with MarketHistory(str(tmp_path / "data" / "market_history.db")) as history:
        assert len(history.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31))) == 1
        assert len(history.get_publication_runs()) == 2, "runs are operational history and append"


def test_unsafe_text_surviving_into_metadata_blocks_upload(offline_pipeline, monkeypatch):
    """The final gate is the backstop: if recommendation language ever reaches a finalized
    artifact despite sanitisation, publication stops rather than the text being rewritten."""
    real_metadata = main.build_metadata

    def _unsafe_metadata(*a, **k):
        meta = real_metadata(*a, **k)
        meta["title"] = "Top stocks to buy tomorrow #shorts"
        return meta

    def _boom(*a, **k):
        raise AssertionError("unsafe content must not be published")

    monkeypatch.setattr(main, "build_metadata", _unsafe_metadata)
    monkeypatch.setattr(main, "publish", _boom)
    assert main.run(_Args(upload=True)) is not None
