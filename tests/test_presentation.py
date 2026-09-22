"""The Phase 2 presentation invariant: the renderer draws only what the report contains."""
import datetime as dt

import pytest

from conftest import NOW, REPORT_DATE, SESSION, build_test_report

from core import MarketReport, Metric, ValidationStatus
from presentation import ReportPresentation


@pytest.fixture
def report(market_dict, movers, sectors, tiles, ai_facts, events):
    gainers, losers = movers
    return build_test_report(market_dict, gainers=gainers, losers=losers, sectors=sectors,
                             tiles=tiles, ai_facts=ai_facts, events=events,
                             flows={"fii": -1240.5, "dii": 2105.3, "source": "NSE"},
                             nifty_reason={"text": "Broad-based buying lifted the index.",
                                           "source": "GEMINI", "publisher": None})


@pytest.fixture
def pres(report):
    return ReportPresentation(report)


# --------------------------------------------------------------------- the invariant
def test_every_displayed_number_exists_in_the_report(pres, report):
    """The whole point of Phase 2: a number on screen that is not in the report would be a
    number no validator ever saw and no reader could trace."""
    report_values = {f.value for f in report.facts if f.value is not None}
    for section in (report.nifty, report.technicals):
        report_values.update(v for v in section.values() if isinstance(v, (int, float)))
    for row in report.global_cues + report.sectors + report.gainers + report.losers:
        report_values.update(v for v in row.values() if isinstance(v, (int, float)))
    report_values.update(v for v in report.institutional_flows.values()
                         if isinstance(v, (int, float)))

    missing = {label: value for label, value in pres.displayed_numbers().items()
               if value not in report_values}
    assert not missing, f"displayed but absent from the report: {missing}"


def test_presentation_values_equal_report_values(pres, report):
    assert pres.m["close"] == report.nifty["close"]
    assert pres.m["pct"] == report.nifty["change_pct"]
    assert pres.m["chg"] == report.nifty["change_points"]
    assert pres.m["vix"] == report.nifty["india_vix"]
    assert pres.m["ema20"] == report.technicals["ema20"]
    assert pres.m["rsi"] == report.technicals["rsi14"]
    assert pres.m["levels"]["sup"] == report.technicals["support"]
    assert pres.nifty_reason == report.nifty["move_summary"]


def test_mover_rows_come_from_the_report(pres, report):
    for shown, stored in zip(pres.gainers, report.gainers):
        assert shown["symbol"] == stored["symbol"]
        assert shown["pct"] == stored["change_pct"]
        assert shown["close"] == stored["close"]
        assert shown["volx"] == stored["relative_volume"]
        assert shown["reason"] == stored["catalyst"]["text"]


def test_chart_frame_is_rebuilt_from_the_reports_candles(pres, report):
    """The chart renders these values, so they live in the report rather than only in an
    in-memory DataFrame - reshaping them back is presentation, not recomputation."""
    frame = pres.chart_frame()
    candles = report.technicals["candles"]
    assert len(frame) == len(candles)
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "ema20", "ema50"]
    assert float(frame.Close.iloc[-1]) == candles[-1]["close"]
    assert float(frame.ema20.iloc[-1]) == candles[-1]["ema20"]


def test_presentation_survives_a_json_round_trip(report):
    """A report read back from disk must render identically - proof the renderer depends on
    the report contract and not on anything still held in memory from acquisition."""
    restored = MarketReport.from_json(report.to_json())
    a, b = ReportPresentation(report), ReportPresentation(restored)
    assert a.displayed_numbers() == b.displayed_numbers()
    assert a.nifty_reason == b.nifty_reason
    assert [r["reason"] for r in a.gainers] == [r["reason"] for r in b.gainers]
    assert len(a.chart_frame()) == len(b.chart_frame())


def test_presentation_does_not_fetch_or_compute(pres):
    """Guard against the boundary eroding: nothing in the adapter may acquire data or
    recompute a market fact."""
    import inspect

    import presentation.report_adapter as adapter
    source = inspect.getsource(adapter)
    for forbidden in ("yfinance", "requests", "market.get_", "news.ai_pass", "ewm(", "rolling("):
        assert forbidden not in source, forbidden


# --------------------------------------------------------------------- scene selection
def test_scene_selection_uses_report_contents(pres):
    present = pres.present()
    assert {"intro", "nifty", "gainers", "losers", "events", "outro"} <= present
    assert ("global" in present) == (len(pres.tiles) >= 3)
    assert ("fii" in present) == bool(pres.fd)


def test_optional_sections_absent_from_report_are_absent_on_screen(market_dict):
    report = build_test_report(market_dict, nifty_reason="")
    pres = ReportPresentation(report)
    assert pres.tiles == [] and pres.sec == [] and pres.fd is None
    assert "global" not in pres.present() and "fii" not in pres.present()


def test_flows_reach_presentation_only_when_both_sides_are_known(market_dict):
    report = build_test_report(market_dict, flows={"fii": -1240.5, "dii": 2105.3, "source": "NSE"})
    assert ReportPresentation(report).fd == {"fii": -1240.5, "dii": 2105.3, "source": "NSE"}


def test_events_carry_text_from_the_report(report, pres):
    assert [e["text"] for e in pres.events] == [e["text"] for e in report.events if e["text"]]


def test_session_date_drives_the_recap_label(pres):
    """A Monday report describes Friday's session; presenting the report date would
    mislabel the whole recap."""
    assert pres.session_date == SESSION != REPORT_DATE
