"""Historical persistence: atomic, idempotent, and never polluted by demo fixtures."""
import datetime as dt
import sqlite3

import pytest

from conftest import REPORT_DATE, SESSION, build_test_report

from core import Metric, ValidationStatus
from storage import MarketHistory, SchemaVersionError, current_version
from storage.migrations import SCHEMA_VERSION
from storage.schema import SCHEMA_SQL


@pytest.fixture
def db(tmp_path):
    history = MarketHistory(str(tmp_path / "history.db"))
    yield history
    history.close()


@pytest.fixture
def report(market_dict, movers, sectors, tiles, ai_facts, events):
    gainers, losers = movers
    return build_test_report(market_dict, gainers=gainers, losers=losers, sectors=sectors,
                             tiles=tiles, ai_facts=ai_facts, events=events,
                             flows={"fii": -1240.5, "dii": 2105.3, "source": "NSE"},
                             nifty_reason={"text": "Broad-based buying lifted the index.",
                                           "source": "GEMINI", "publisher": None},
                             content_safety={"status": "SAFE", "sanitized_count": 0,
                                             "blocked_count": 0, "findings": []})


# --------------------------------------------------------------------- schema
def test_fresh_database_initialises_at_current_version(tmp_path):
    with MarketHistory(str(tmp_path / "fresh.db")) as history:
        assert history.schema_version == SCHEMA_VERSION
        assert current_version(history.conn) == SCHEMA_VERSION


def test_existing_database_reopens_without_change(tmp_path, report):
    path = str(tmp_path / "reopen.db")
    with MarketHistory(path) as first:
        first.save_report(report, artifact_path="a.json")
    with MarketHistory(path) as second:
        assert second.schema_version == SCHEMA_VERSION
        assert second.get_report(report.report_id) is not None


def test_newer_schema_is_refused(tmp_path):
    """Writing into a database written by a newer build would corrupt history quietly, which
    is exactly the failure worth real code to prevent."""
    path = str(tmp_path / "future.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaVersionError):
        MarketHistory(path)


def test_foreign_keys_are_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        with db.conn:
            db.conn.execute(
                """INSERT INTO facts (report_id, fact_id, metric, instrument, validation_status)
                   VALUES ('missing_report','f1','INDEX_CLOSE','NIFTY 50','VERIFIED')""")


# --------------------------------------------------------------------- persistence
def test_report_persists_with_artifact_linkage(db, report):
    assert db.save_report(report, artifact_path="output/reports/premarket_2026-09-21.json")
    stored = db.get_report(report.report_id)
    assert stored.report_id == report.report_id
    assert stored.report_type == "PRE_MARKET"
    assert stored.session_date == SESSION.isoformat()
    assert stored.json_artifact_path.endswith("premarket_2026-09-21.json")
    assert stored.schema_version == "2.0"
    assert stored.validation_summary["total_facts"] == len(report.facts)


def test_facts_persist_with_canonical_identity(db, report):
    db.save_report(report, artifact_path="a.json")
    facts = db.get_facts(report_id=report.report_id, include_demo=True)
    assert len(facts) == len(report.facts)
    close = next(f for f in facts if f.metric == "INDEX_CLOSE")
    assert close.fact_id == report.facts_for(Metric.INDEX_CLOSE)[0].fact_id
    assert close.value == report.nifty["close"]


def test_observations_persist_with_source_and_independence(db, report):
    db.save_report(report, artifact_path="a.json")
    close = report.facts_for(Metric.INDEX_CLOSE)[0]
    observations = db.get_observations(close.fact_id, close.fact_id and report.report_id)
    assert len(observations) == len(close.observations)
    by_source = {o.source_name: o for o in observations}
    assert "yahoo_finance" in by_source
    assert by_source["yahoo_finance"].independence_group == "YAHOO"
    assert all(o.retrieved_at for o in observations)


def test_source_metadata_persists(db, report):
    db.save_report(report, artifact_path="a.json")
    yahoo = db.get_source("yahoo_finance")
    assert yahoo["source_type"] == "SECONDARY"
    assert yahoo["source_family"] == "MARKET_DATA_AGGREGATOR"
    assert yahoo["independence_group"] == "YAHOO"
    assert yahoo["first_seen_at"] and yahoo["last_seen_at"]


def test_validation_results_persist_with_details(db, report):
    """The verdict alone is not auditable - a CONFLICT is only re-arguable if the compared
    values and tolerances survive with it."""
    db.save_report(report, artifact_path="a.json")
    close = report.facts_for(Metric.INDEX_CLOSE)[0]
    results = db.get_validation_results(close.fact_id, report.report_id)
    assert results
    cross = next(r for r in results if r.validator == "cross_source")
    assert cross.status == close.validation_status.value
    assert "values" in cross.details and "independence_groups" in cross.details


def test_catalysts_persist_with_provenance(db, report):
    db.save_report(report, artifact_path="a.json")
    catalysts = db.get_catalysts(report.report_id)
    assert catalysts
    gainer = next(c for c in catalysts if c["bucket"] == "gainer")
    assert gainer["origin"] == "GOOGLE_NEWS_RSS"
    assert gainer["source_name"] == "google_news_rss"
    assert gainer["publisher"] == "Wire"
    assert gainer["independence_group"] == "PUBLISHER:wire"


def test_events_persist_with_provenance_and_order(db, report):
    db.save_report(report, artifact_path="a.json")
    events = db.get_events(report.report_id)
    assert [e["position"] for e in events] == list(range(len(events)))
    assert events[0]["origin"] == "RULE_FNO_EXPIRY"
    assert all(e["provenance_resolved"] for e in events)


def test_relative_volume_definition_persists(db, report):
    """The metric changed definition in Phase 3, so the version has to travel with the data."""
    db.save_report(report, artifact_path="a.json")
    fact = next(f for f in db.get_facts(metric="STOCK_RELATIVE_VOLUME", include_demo=True))
    observations = db.get_observations(fact.fact_id, fact.report_id)
    meta = observations[0].metadata
    assert meta["definition_version"] == "2.0"
    assert meta["lookback_sessions"] == 20
    assert meta["includes_current_session"] is False


# --------------------------------------------------------------------- idempotency
def test_saving_the_same_report_twice_is_a_no_op(db, report):
    assert db.save_report(report, artifact_path="a.json") is True
    assert db.save_report(report, artifact_path="a.json") is False

    facts = db.get_facts(report_id=report.report_id, include_demo=True)
    assert len(facts) == len(report.facts)
    close = report.facts_for(Metric.INDEX_CLOSE)[0]
    assert len(db.get_observations(close.fact_id, report.report_id)) == len(close.observations)
    assert len(db.get_validation_results(close.fact_id, report.report_id)) == \
        len(close.validation_results)


def test_reruns_do_not_duplicate_children(db, report):
    for _ in range(4):
        db.save_report(report, artifact_path="a.json")
    counts = {
        "reports": db.conn.execute("SELECT count(*) FROM reports").fetchone()[0],
        "facts": db.conn.execute("SELECT count(*) FROM facts").fetchone()[0],
        "observations": db.conn.execute("SELECT count(*) FROM observations").fetchone()[0],
        "catalysts": db.conn.execute("SELECT count(*) FROM catalysts").fetchone()[0],
        "events": db.conn.execute("SELECT count(*) FROM events").fetchone()[0],
    }
    assert counts["reports"] == 1
    assert counts["facts"] == len(report.facts)
    assert counts["events"] == len(report.events)


def test_explicit_replace_rewrites_without_duplicating(db, report):
    db.save_report(report, artifact_path="a.json")
    report.content_safety = {"status": "SANITIZED", "sanitized_count": 1, "blocked_count": 0}
    assert db.save_report(report, artifact_path="b.json", replace=True) is True

    assert db.conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    assert db.conn.execute("SELECT count(*) FROM facts").fetchone()[0] == len(report.facts)
    stored = db.get_report(report.report_id)
    assert stored.json_artifact_path == "b.json"
    assert stored.content_safety["status"] == "SANITIZED"


# --------------------------------------------------------------------- transactions
def test_partial_failure_rolls_back_the_whole_report(tmp_path, report, monkeypatch):
    """A half-written report is worse than a missing one: it looks complete to later queries."""
    history = MarketHistory(str(tmp_path / "rollback.db"))
    original = history._insert_events

    def _explode(*a, **k):
        raise sqlite3.OperationalError("simulated failure midway through the report")

    monkeypatch.setattr(history, "_insert_events", _explode)
    with pytest.raises(sqlite3.OperationalError):
        history.save_report(report, artifact_path="a.json")

    assert history.get_report(report.report_id) is None
    assert history.conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    assert history.conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    assert history.conn.execute("SELECT count(*) FROM catalysts").fetchone()[0] == 0

    # The connection is still usable afterwards, and a retry succeeds cleanly.
    monkeypatch.setattr(history, "_insert_events", original)
    assert history.save_report(report, artifact_path="a.json") is True
    assert history.get_report(report.report_id) is not None
    history.close()


# --------------------------------------------------------------------- queries
def test_unpublishable_report_is_still_persisted(db, market_dict):
    """"Why wasn't the 22 Sep report published?" is only answerable if the unfit report was
    kept, so validation failure must never mean losing the report."""
    conflicted = build_test_report(market_dict,
                                   nse_idx={"NIFTY 50": {"last": 25900.0, "pct": 3.4,
                                                         "observed_at": None}})
    assert not conflicted.publication_ready
    assert db.save_report(conflicted, artifact_path="a.json")

    stored = db.get_report(conflicted.report_id)
    assert stored.publication_ready is False
    assert stored.validation_summary["conflict_count"] >= 1
    close = conflicted.facts_for(Metric.INDEX_CLOSE)[0]
    results = db.get_validation_results(close.fact_id, conflicted.report_id)
    assert any(r.status == ValidationStatus.CONFLICT.value for r in results)


def test_demo_reports_do_not_contaminate_production_history(db, market_dict, report):
    demo = build_test_report(market_dict, demo=True, report_date=dt.date(2026, 9, 22))
    db.save_report(report, artifact_path="real.json", is_demo=False)
    db.save_report(demo, artifact_path="demo.json", is_demo=True)

    production = db.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert [r.report_id for r in production] == [report.report_id]
    assert all(not f.report_id.startswith(demo.report_id) for f in db.get_facts())

    everything = db.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31),
                                        include_demo=True)
    assert len(everything) == 2


def test_date_range_query(db, report, market_dict):
    other = build_test_report(market_dict, report_date=dt.date(2026, 10, 15))
    db.save_report(report, artifact_path="a.json")
    db.save_report(other, artifact_path="b.json")

    september = db.get_reports_between(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert [r.report_id for r in september] == [report.report_id]
    assert len(db.get_reports_between(dt.date(2026, 9, 1), dt.date(2026, 10, 31))) == 2


def test_fact_history_query_across_reports(db, market_dict):
    first = build_test_report(market_dict, report_date=dt.date(2026, 9, 21))
    second_dict = dict(market_dict, recap_date=dt.date(2026, 9, 21), close=25500.0)
    second = build_test_report(second_dict, report_date=dt.date(2026, 9, 22))
    db.save_report(first, artifact_path="a.json")
    db.save_report(second, artifact_path="b.json")

    history = db.get_facts(metric="INDEX_CLOSE", instrument="NIFTY 50")
    assert len(history) == 2
    assert {f.value for f in history} == {25140.35, 25500.0}


def test_fact_query_filters_by_date_window(db, report):
    db.save_report(report, artifact_path="a.json")
    assert db.get_facts(metric="INDEX_CLOSE", start_date=dt.date(2026, 9, 18),
                        end_date=dt.date(2026, 9, 18))
    assert not db.get_facts(metric="INDEX_CLOSE", start_date=dt.date(2026, 10, 1),
                            end_date=dt.date(2026, 10, 31))


def test_report_exists(db, report):
    assert not db.report_exists(report.report_id)
    db.save_report(report, artifact_path="a.json")
    assert db.report_exists(report.report_id)


# --------------------------------------------------------------------- publication runs
def test_run_lifecycle_is_recorded(db, report):
    run_id = db.start_run("PRODUCTION", report_id=report.report_id)
    db.update_run(run_id, stage="REPORT_BUILT", data_qa_status="PASSED")
    db.finish_run(run_id, "PUBLISHED", "PUBLISHED", youtube_video_id="abc123",
                  artifact_path="out.mp4")

    run = db.get_publication_runs(report.report_id)[0]
    assert run.mode == "PRODUCTION"
    assert run.stage == "PUBLISHED"
    assert run.publication_status == "PUBLISHED"
    assert run.youtube_video_id == "abc123"
    assert run.completed_at and run.started_at


def test_failed_run_records_the_stage_that_blocked_it(db):
    run_id = db.start_run("PRODUCTION")
    db.finish_run(run_id, "VIDEO_QA_FAILED", "BLOCKED", failure_stage="VIDEO_QA",
                  failure_reason="resolution: wrong resolution")
    run = db.get_publication_runs()[0]
    assert run.failure_stage == "VIDEO_QA"
    assert "resolution" in run.failure_reason
    assert run.publication_status == "BLOCKED"


def test_a_run_without_an_upload_is_still_recorded(db):
    """Not uploading is an outcome worth keeping, not an absence of one."""
    run_id = db.start_run("LOCAL")
    db.finish_run(run_id, "NO_UPLOAD", "NOT_ATTEMPTED")
    run = db.get_publication_runs()[0]
    assert run.youtube_video_id is None
    assert run.publication_status == "NOT_ATTEMPTED"


def test_runs_are_appended_not_deduplicated(db, report):
    for _ in range(3):
        db.start_run("PRODUCTION", report_id=report.report_id)
    assert len(db.get_publication_runs(report.report_id)) == 3
