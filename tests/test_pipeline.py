"""End-to-end run of the production (non-demo) branch, fully offline.

The demo branch skips collect()'s real path, and the live path cannot be exercised in tests,
so every provider call is stubbed here instead. That keeps the non-demo wiring - which is
what actually publishes - under test rather than only verified by hand on a good data day.
"""
import datetime as dt
import json
import os

import pytest

from conftest import IST, PREV_SESSION, SESSION

import main
from core import MarketReport, Metric, ValidationStatus


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
    monkeypatch.setattr(main.market, "get_movers", lambda *a, **k: (list(gainers), list(losers)))
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
    return rendered


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


def test_rendered_scenes_are_fed_from_the_report(offline_pipeline, tmp_path):
    """Everything the renderer received must match what the report stored."""
    main.run(_Args())
    report_path = next((tmp_path / "reports").iterdir())
    report = MarketReport.from_json(report_path.read_text(encoding="utf-8"))

    nifty_scene = next(s for s in offline_pipeline["scenes"] if type(s).__name__ == "NiftyScene")
    assert nifty_scene.m["close"] == report.nifty["close"]
    assert nifty_scene.m["pct"] == report.nifty["change_pct"]

    movers_scene = next(s for s in offline_pipeline["scenes"] if type(s).__name__ == "MoversScene")
    assert [r["symbol"] for r in movers_scene.rows] == [r["symbol"] for r in report.gainers]
    assert [r["pct"] for r in movers_scene.rows] == [r["change_pct"] for r in report.gainers]


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
    assert report.content_safety["final_scan"]["status"] == "SAFE"


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
