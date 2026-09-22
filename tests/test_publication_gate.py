"""publication_ready is authoritative: a report unfit to publish stops the run."""
import pytest

from conftest import NOW, SESSION, build_test_report, observation

import main
from core import Fact, Metric, SourceType, ValidationStatus
from core.content_safety import SafetyStatus, scan_publication


class _Args:
    def __init__(self, demo=False, upload=False, force=False):
        self.demo, self.upload, self.force = demo, upload, force


# --------------------------------------------------------------------- the gate
def test_publication_ready_report_passes(market_dict):
    report = build_test_report(market_dict)
    assert report.publication_ready
    assert main.check_publication(report, demo=False) is True


def test_conflicting_critical_fact_blocks_publication(market_dict):
    """Reproduces the legacy Nifty safeguard as a report verdict: sources that disagree
    beyond tolerance must not be published."""
    report = build_test_report(market_dict,
                               nse_idx={"NIFTY 50": {"last": 25900.0, "pct": 3.4,
                                                     "observed_at": None}})
    assert report.validation_summary.conflict_count >= 1
    assert not report.publication_ready
    assert main.check_publication(report, demo=False) is False


def test_missing_required_fact_blocks_publication(market_dict):
    report = build_test_report(market_dict)
    report.facts = [f for f in report.facts if f.metric is not Metric.INDEX_CLOSE]
    assert not report.publication_ready
    assert main.check_publication(report, demo=False) is False
    assert any("INDEX_CLOSE" in issue for issue in report.validation_summary.blocking_issues)


def test_missing_optional_source_does_not_block(market_dict):
    """NSE blocked and Gemini unavailable is an ordinary day on a cloud runner, not a
    failure - the report is single-sourced but still publishable."""
    report = build_test_report(market_dict, tiles=[], sectors=[], flows=None, ai_facts={},
                               nse_idx={})
    close = report.facts_for(Metric.INDEX_CLOSE)[0]
    assert close.validation_status is ValidationStatus.SINGLE_SOURCE
    assert report.publication_ready
    assert main.check_publication(report, demo=False) is True


def test_demo_is_exempt_from_the_gate(market_dict):
    """Synthetic data is never published anywhere, so a demo render is not a publication."""
    report = build_test_report(market_dict, demo=True)
    report.facts = []
    assert not report.publication_ready
    assert main.check_publication(report, demo=True) is True


def test_blocked_report_reports_its_reasons(market_dict, capsys):
    report = build_test_report(market_dict,
                               nse_idx={"NIFTY 50": {"last": 25900.0, "pct": 3.4,
                                                     "observed_at": None}})
    main.check_publication(report, demo=False)
    printed = capsys.readouterr().out
    assert "BLOCKED" in printed and "INDEX_CLOSE" in printed


# --------------------------------------------------------------------- content safety still binds
def test_content_safety_still_blocks_unsafe_publication():
    """Phase 1.1 must survive Phase 2: validation and content safety are separate gates and
    both have to pass."""
    scan = scan_publication({"youtube_title": "Top stocks to buy tomorrow"})
    assert scan.status is SafetyStatus.BLOCKED
    assert "youtube_title" in scan.blocked_fields


def test_unsafe_text_never_reaches_the_report(market_dict):
    """Content safety runs before the report is built, so blocked text is not stored and
    cannot be presented."""
    gainers = [{"symbol": "ABC", "name": "ABC Ltd", "close": 100.0, "pct": 4.0, "volx": 2.0,
                "reason": "Top stocks to buy tomorrow", "reason_source": "GOOGLE_NEWS_RSS",
                "reason_publisher": "Somewhere"}]
    events = [{"tag": "IPO", "text": "Should you buy, sell or hold this stock?",
               "source": "GEMINI_SEARCH", "publisher": None}]
    reason, gainers, losers, events, findings = main.apply_content_safety(
        {"text": "Strong BUY with target Rs 500", "source": "GEMINI", "publisher": None},
        gainers, [], events)

    report = build_test_report(market_dict, gainers=gainers, losers=losers, events=events,
                               nifty_reason=reason)
    assert report.nifty["move_summary"] == ""
    assert report.gainers[0]["catalyst"]["text"] == \
        "No major company-specific news; moved with sector trend."
    assert all("buy" not in (e["text"] or "").lower() for e in report.events)
    assert len(findings) == 3


def test_blocked_catalyst_loses_its_original_attribution(market_dict):
    """If the text was dropped, the publisher it came from no longer describes what is
    shown - keeping the attribution would credit a headline the viewer never sees."""
    gainers = [{"symbol": "ABC", "name": "ABC Ltd", "close": 100.0, "pct": 4.0, "volx": 2.0,
                "reason": "Top stocks to buy tomorrow", "reason_source": "GOOGLE_NEWS_RSS",
                "reason_publisher": "Somewhere"}]
    _, gainers, _, _, _ = main.apply_content_safety(None, gainers, [], [])
    assert gainers[0]["reason_source"] == "NO_VERIFIED_CATALYST"
    assert gainers[0]["reason_publisher"] is None


def test_canonical_safety_summary_covers_only_pre_report_sanitisation():
    """Stage A is canonical and belongs in the report; the final publication scan is an
    operational outcome and deliberately has no place here."""
    summary = main.summarize_content_safety([])
    assert summary["stage"] == "PRE_REPORT_SANITISATION"
    assert summary["status"] == "SAFE"
    assert "final_scan" not in summary


def test_operational_content_qa_is_reported_separately():
    from core.content_safety import scan_publication

    scan = scan_publication({"youtube_title": "Top stocks to buy tomorrow"})
    operational = main.operational_content_qa(scan)
    assert operational["stage"] == "FINAL_PUBLICATION_SCAN"
    assert operational["passed"] is False
    assert operational["blocked_fields"] == ["youtube_title"]


# --------------------------------------------------------------------- ordering
def test_report_is_built_before_presentation():
    """The Phase 2 dependency inversion, asserted against main.run's actual source: the
    report must be built and gated before any scene or render call."""
    import inspect
    source = inspect.getsource(main.run)
    order = [source.index(marker) for marker in
             ("build_report(", "check_publication(", "ReportPresentation(", "build_scenes(",
              "video.render(")]
    assert order == sorted(order), "report must precede presentation and rendering"
