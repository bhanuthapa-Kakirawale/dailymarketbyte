"""MarketReport assembly, the publication gate, and JSON round-tripping."""
import datetime as dt
import json

from conftest import NOW, REPORT_DATE, SESSION, observation

from adapters.report_builder import build_premarket_report, facts_from, save_report
from core import (Fact, MarketReport, Metric, ReportType, SourceType, ValidationStatus,
                  summarize)


def _report(**over):
    """Build a report from the standard fixtures, with per-test overrides."""
    kwargs = dict(m=over.pop("m"), tiles=over.pop("tiles", []), fd=over.pop("fd", None),
                  sec=over.pop("sec", []), gainers=over.pop("gainers", []),
                  losers=over.pop("losers", []), events=over.pop("events", []),
                  nifty_reason=over.pop("nifty_reason", "Broad-based buying lifted the index."),
                  ai_facts=over.pop("ai_facts", {}), nse_idx=over.pop("nse_idx", {}),
                  report_date=over.pop("report_date", REPORT_DATE),
                  universe_label=over.pop("universe_label", "Nifty 100"),
                  demo=over.pop("demo", False), now=NOW)
    kwargs.update(over)
    return build_premarket_report(**kwargs)


# --------------------------------------------------------------------- assembly
def test_report_identifies_edition_and_both_dates(market_dict):
    report = _report(m=market_dict)
    assert report.report_type is ReportType.PRE_MARKET
    assert report.report_date == REPORT_DATE
    assert report.session_date == SESSION, "the session described is not the day it is published"
    assert report.report_id == "20260921_PRE_MARKET"


def test_report_preserves_published_values(market_dict):
    """The artifact must record what the video actually showed, not a 'better' number."""
    report = _report(m=market_dict)
    assert report.nifty["close"] == market_dict["close"]
    assert report.nifty["change_pct"] == market_dict["pct"]


def test_nifty_close_carries_all_three_sources(market_dict, ai_facts):
    report = _report(m=market_dict, ai_facts=ai_facts,
                     nse_idx={"NIFTY 50": {"last": 25141.10, "pct": 0.29}})
    fact = report.facts_for(Metric.INDEX_CLOSE)[0]
    assert set(fact.sources) == {"yahoo_finance", "nse_website", "gemini"}
    assert fact.validation_status is ValidationStatus.VERIFIED
    assert fact.metadata["published_value_source"] == "yahoo_finance"
    assert fact.value == market_dict["close"]


def test_yahoo_alone_leaves_the_close_uncorroborated(market_dict):
    """The everyday case on GitHub runners: NSE blocks the IP and Gemini returns nothing."""
    fact = _report(m=market_dict).facts_for(Metric.INDEX_CLOSE)[0]
    assert fact.validation_status is ValidationStatus.SINGLE_SOURCE


def test_sections_reference_their_facts(market_dict, movers, sectors, tiles, ai_facts):
    gainers, losers = movers
    report = _report(m=market_dict, gainers=gainers, losers=losers, sec=sectors,
                     tiles=tiles, ai_facts=ai_facts)
    assert report.nifty["fact_ids"] and all(report.fact(i) for i in report.nifty["fact_ids"])
    assert all(report.fact(s["fact_id"]) for s in report.sectors)
    for row in report.gainers:
        assert len(row["fact_ids"]) == 3 and all(report.fact(i) for i in row["fact_ids"])


def test_gift_nifty_is_not_double_counted_as_two_sources(market_dict, tiles, ai_facts):
    """GIFT Nifty reaches the builder twice - as a display tile and as a Gemini fact. Two
    copies of one reading must not look like two agreeing sources."""
    report = _report(m=market_dict, tiles=tiles, ai_facts=ai_facts)
    gift = next(f for f in report.facts if f.instrument == "GIFT NIFTY")
    assert len(gift.observations) == 1
    assert gift.validation_status is ValidationStatus.PROVISIONAL


def test_catalysts_reach_the_report(market_dict, movers):
    gainers, losers = movers
    report = _report(m=market_dict, gainers=gainers, losers=losers)
    assert report.gainers[0]["catalyst"]["source"] == "google_news_rss"
    assert report.losers[0]["catalyst"]["type"] == "NO_VERIFIED_CATALYST"


def test_demo_report_is_labelled_and_sourced_to_the_fixture(market_dict):
    report = _report(m=market_dict, demo=True)
    assert report.metadata["demo"] is True
    assert {s for f in report.facts for s in f.sources} == {"demo_fixture"}


# --------------------------------------------------------------------- publication gate
def test_publication_ready_when_required_facts_are_present(market_dict):
    report = _report(m=market_dict)
    assert report.publication_ready
    assert report.validation_summary.blocking_issues == []


def test_conflicting_critical_fact_blocks_publication(market_dict):
    """Reproduces the existing safeguard: disagreeing Nifty closes must not be published."""
    report = _report(m=market_dict, nse_idx={"NIFTY 50": {"last": 25900.0, "pct": 3.4}})
    summary = report.validation_summary
    assert summary.conflict_count >= 1
    assert not summary.publication_ready
    assert any("INDEX_CLOSE" in issue for issue in summary.blocking_issues)


def test_missing_optional_fact_does_not_block(market_dict):
    """NSE being unreachable and Gemini returning nothing is a normal day, not a failure."""
    report = _report(m=market_dict, fd=None, tiles=[], sec=[], ai_facts={})
    assert report.publication_ready


def test_missing_required_fact_blocks_publication():
    facts = [Fact.from_observations([observation(0.29, metric=Metric.INDEX_CHANGE_PCT)])]
    facts[0].validation_status = ValidationStatus.SINGLE_SOURCE
    summary = summarize(facts)
    assert not summary.publication_ready
    assert any("INDEX_CLOSE" in issue for issue in summary.blocking_issues)


def test_summary_counts_every_status():
    def fact(status, metric=Metric.SECTOR_CHANGE_PCT, instrument="IT"):
        f = Fact.from_observations([observation(1.0, metric=metric, instrument=instrument)])
        f.validation_status = status
        return f

    summary = summarize([fact(ValidationStatus.VERIFIED), fact(ValidationStatus.PROVISIONAL, instrument="Bank"),
                         fact(ValidationStatus.SINGLE_SOURCE, instrument="Metal"),
                         fact(ValidationStatus.MISSING, instrument="Auto")])
    assert (summary.verified_count, summary.provisional_count,
            summary.single_source_count, summary.missing_count) == (1, 1, 1, 1)
    assert summary.total_facts == 4


# --------------------------------------------------------------------- serialisation
def test_report_serialises_to_json(market_dict, movers, sectors, tiles, ai_facts, events):
    gainers, losers = movers
    report = _report(m=market_dict, gainers=gainers, losers=losers, sec=sectors, tiles=tiles,
                     ai_facts=ai_facts, events=events, fd={"fii": -1240.5, "dii": 2105.3,
                                                           "source": "NSE"})
    payload = json.loads(report.to_json())
    assert payload["report_schema_version"] == "1.1"
    assert payload["validation_summary"]["total_facts"] == len(report.facts)
    assert payload["facts"][0]["observations"][0]["source_type"] in {
        "PRIMARY", "SECONDARY", "AI", "DERIVED", "NEWS", "BROKER"}


def test_report_round_trips_through_json(market_dict, movers, ai_facts):
    gainers, losers = movers
    report = _report(m=market_dict, gainers=gainers, losers=losers, ai_facts=ai_facts)
    restored = MarketReport.from_json(report.to_json())
    assert restored.report_id == report.report_id
    assert restored.session_date == report.session_date
    assert len(restored.facts) == len(report.facts)
    assert restored.validation_summary.to_dict() == report.validation_summary.to_dict()


def test_round_trip_preserves_provenance_down_to_the_observation(market_dict, ai_facts):
    report = _report(m=market_dict, ai_facts=ai_facts)
    restored = MarketReport.from_json(report.to_json())
    original = report.facts_for(Metric.INDEX_CLOSE)[0]
    recovered = restored.facts_for(Metric.INDEX_CLOSE)[0]
    assert [o.to_dict() for o in recovered.observations] == [o.to_dict() for o in original.observations]
    assert recovered.validation_results[-1].details == original.validation_results[-1].details


def test_saved_artifact_is_named_by_edition_and_date(tmp_path, market_dict):
    path = save_report(_report(m=market_dict), str(tmp_path))
    assert path.endswith(f"premarket_{REPORT_DATE:%Y-%m-%d}.json")
    assert json.loads(open(path, encoding="utf-8").read())["report_id"] == "20260921_PRE_MARKET"


def test_demo_artifact_cannot_overwrite_a_real_one(tmp_path, market_dict):
    real = save_report(_report(m=market_dict), str(tmp_path), demo=False)
    demo = save_report(_report(m=market_dict, demo=True), str(tmp_path), demo=True)
    assert real != demo and demo.endswith("_DEMO.json")


# --------------------------------------------------------------------- grouping
def test_facts_from_groups_by_metric_and_instrument():
    facts = facts_from([observation(25140.35, "yahoo_finance"),
                        observation(25141.10, "nse_website", SourceType.PRIMARY),
                        observation(1.24, "yahoo_finance", metric=Metric.SECTOR_CHANGE_PCT,
                                    instrument="IT")], now=NOW)
    assert len(facts) == 2
    close = next(f for f in facts if f.metric is Metric.INDEX_CLOSE)
    assert len(close.observations) == 2


def test_facts_from_deduplicates_identical_observations():
    facts = facts_from([observation(25140.35), observation(25140.35)], now=NOW)
    assert len(facts[0].observations) == 1
    assert facts[0].validation_status is ValidationStatus.SINGLE_SOURCE


def test_report_date_and_session_date_may_differ(market_dict):
    """A Monday report describes Friday's session; dating facts to the report day would
    silently misattribute the whole session."""
    report = _report(m=market_dict, report_date=dt.date(2026, 9, 21))
    close = report.facts_for(Metric.INDEX_CLOSE)[0]
    assert close.market_date == SESSION != report.report_date
