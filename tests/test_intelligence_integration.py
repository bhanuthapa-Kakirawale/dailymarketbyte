"""Intelligence end to end: a realistic 22-session history, determinism, and the boundaries
it must not cross - no acquisition, no mutation, no fabrication, no prediction.
"""
import datetime as dt
import hashlib
import json
import os

import pytest

from conftest import NOW
from conftest_intelligence import seed, session_report, movers, trading_sessions

from core import MarketReport, Metric
from intelligence import (INTELLIGENCE_SCHEMA_VERSION, IntelligenceSnapshot, Strength,
                          build_snapshot, save_snapshot)
from intelligence.engine import describe
from storage import MarketHistory

SESSIONS = trading_sessions(23)
TODAY_SESSION = SESSIONS[-1]

# Controlled patterns, so the expected answers are known rather than whatever the data did:
#   FII   - net seller every session, deepening
#   DII   - net buyer every session
#   VIX   - rising steadily from 12.0
#   IT    - positive until the last four historical sessions, then negative
#   ABC   - a mover on every third session
IT_TURNS_NEGATIVE_AT = 18


def _history_reports():
    reports = []
    for i, session in enumerate(SESSIONS[:-1]):
        reports.append(session_report(
            session, pct=0.2 + (i % 5) * 0.1, fii=-100.0 - i * 10, dii=200.0 + i * 5,
            vix=12.0 + i * 0.2,
            sectors=[{"name": "IT", "pct": -0.5 if i >= IT_TURNS_NEGATIVE_AT else 0.6},
                     {"name": "Bank", "pct": 0.4}],
            gainers=movers(["ABC"] if i % 3 == 0 else ["XYZ"], volx=1.0 + (i % 4) * 0.2)))
    return reports


def _today():
    return session_report(TODAY_SESSION, pct=1.8, fii=-400.0, dii=350.0, vix=19.0,
                          sectors=[{"name": "IT", "pct": -0.9}, {"name": "Bank", "pct": 0.5}],
                          gainers=movers(["ABC"], volx=4.0))


@pytest.fixture
def populated(tmp_path):
    history = MarketHistory(str(tmp_path / "history.db"))
    seed(history, _history_reports())
    yield history
    history.close()


@pytest.fixture
def snapshot(populated):
    return build_snapshot(_today(), populated, now=NOW)


def _by_id(snapshot, insight_id):
    return next(i for i in snapshot.insights if i.insight_id == insight_id)


# --------------------------------------------------------------------- the fixture as a whole
def test_full_history_produces_the_expected_insights(snapshot):
    assert snapshot.available_history == 22
    ids = {i.insight_id for i in snapshot.insights}
    assert {"index-move-5", "index-move-20", "fii-flow-streak", "dii-flow-streak",
            "fii-flow-cumulative-20", "vix-rank-20", "vix-mean-5",
            "sector-streak-it", "mover-recurrence-abc"} <= ids


def test_index_move_against_a_known_distribution(snapshot):
    """Historical moves run 0.2-0.6%; today's 1.8% is larger than all twenty."""
    insight = _by_id(snapshot, "index-move-20")
    assert insight.metadata["larger_than_sessions"] == 20
    assert insight.current_value == pytest.approx(1.8)
    assert insight.strength is Strength.FULL_HISTORY


def test_fii_selling_streak_spans_the_whole_fixture(snapshot):
    insight = _by_id(snapshot, "fii-flow-streak")
    assert insight.metadata["streak_sessions"] == 23       # 22 historical + today
    assert insight.metadata["direction"] == "SELLING"


def test_dii_buying_streak_spans_the_whole_fixture(snapshot):
    assert _by_id(snapshot, "dii-flow-streak").metadata["direction"] == "BUYING"


def test_vix_is_at_the_top_of_its_recorded_range(snapshot):
    insight = _by_id(snapshot, "vix-rank-20")
    assert insight.metadata["higher_than_sessions"] == 20
    assert insight.current_value == pytest.approx(19.0)


def test_it_sector_decline_streak_matches_the_pattern(snapshot):
    """IT turns negative at index 18 of 22, so four historical sessions plus today."""
    insight = _by_id(snapshot, "sector-streak-it")
    assert insight.metadata["streak_sessions"] == 5
    assert insight.metadata["direction"] == "DOWN"
    assert "declined in 5 consecutive available sessions" in insight.statement


def test_repeated_mover_matches_the_pattern(snapshot):
    """ABC appears every third session across the 20-session window."""
    insight = _by_id(snapshot, "mover-recurrence-abc")
    assert insight.metadata["prior_appearances"] == 7
    assert insight.metadata["classification"] == "FREQUENT_MOVER"


def test_relative_volume_uses_comparable_readings_only(snapshot):
    insight = _by_id(snapshot, "relative-volume-abc")
    assert insight.metadata["definition_version"] == "2.0"
    assert insight.metadata["comparable_sample"] >= 5
    assert insight.current_value == pytest.approx(4.0)


# --------------------------------------------------------------------- traceability
def test_every_statement_is_traceable(snapshot):
    for insight in snapshot.displayable():
        assert insight.category and insight.subject, insight.insight_id
        assert insight.sample_size > 0, insight.insight_id
        assert insight.lookback_sessions, insight.insight_id
        assert insight.supporting_fact_ids, insight.insight_id
        assert insight.supporting_report_ids, insight.insight_id


def test_supporting_reports_exist_in_canonical_history(snapshot, populated):
    for insight in snapshot.displayable():
        for report_id in insight.supporting_report_ids:
            assert populated.get_report(report_id) is not None, report_id


def test_supporting_facts_resolve_to_stored_facts(snapshot, populated):
    insight = _by_id(snapshot, "index-move-20")
    stored = {f.fact_id for f in populated.get_facts(metric="INDEX_CHANGE_PCT")}
    historical = [f for f in insight.supporting_fact_ids if f in stored]
    assert len(historical) == 20


# --------------------------------------------------------------------- determinism
def test_snapshot_is_byte_identical_across_runs(populated):
    first = build_snapshot(_today(), populated, now=NOW).to_json()
    second = build_snapshot(_today(), populated, now=NOW).to_json()
    assert first == second
    assert hashlib.sha256(first.encode()).hexdigest() == \
        hashlib.sha256(second.encode()).hexdigest()


def test_only_the_timestamp_differs_when_now_is_not_injected(populated):
    a = build_snapshot(_today(), populated).to_dict()
    b = build_snapshot(_today(), populated).to_dict()
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


def test_selection_is_deterministic_and_capped(snapshot):
    assert [i.insight_id for i in snapshot.selected()] == \
        [i.insight_id for i in snapshot.selected()]
    assert len(snapshot.selected()) <= 3


def test_selection_shows_one_statement_per_subject(snapshot):
    subjects = [(i.category, i.subject) for i in snapshot.selected()]
    assert len(subjects) == len(set(subjects))


def test_snapshot_round_trips(snapshot):
    restored = IntelligenceSnapshot.from_json(snapshot.to_json())
    assert restored.to_dict() == snapshot.to_dict()
    assert restored.report_id == snapshot.report_id
    assert len(restored.insights) == len(snapshot.insights)


def test_artifact_is_written_and_versioned(snapshot, tmp_path):
    path = save_snapshot(snapshot, str(tmp_path))
    payload = json.loads(open(path, encoding="utf-8").read())
    assert os.path.basename(path) == f"intelligence_{TODAY_SESSION:%Y-%m-%d}.json"
    assert payload["intelligence_schema_version"] == INTELLIGENCE_SCHEMA_VERSION
    assert payload["calculation_version"]
    assert payload["report_id"] == snapshot.report_id
    assert payload["historical_cutoff"] == TODAY_SESSION.isoformat()
    assert payload["selected_insight_ids"]


# --------------------------------------------------------------------- boundaries
def test_intelligence_never_acquires_market_data():
    """The layer reads canonical history and nothing else - no provider, no network, no model."""
    import inspect

    import intelligence
    from intelligence import engine, flows, history, market_context, movers as movers_mod
    from intelligence import models, sectors, volatility

    forbidden = ("yfinance", "requests", "ask_gemini", "google_news", "urllib",
                 "providers", "market.get_", "news.ai_pass")
    for module in (intelligence, engine, flows, history, market_context, movers_mod,
                   models, sectors, volatility):
        source = inspect.getsource(module)
        for term in forbidden:
            assert term not in source, f"{module.__name__} must not reference {term}"


def test_intelligence_does_not_mutate_canonical_history(populated, tmp_path):
    """Phase 3.1 immutability is binding: derived context cannot touch canonical records."""
    def snapshot_tables():
        out = {}
        for table in ("reports", "facts", "observations", "validation_results",
                      "catalysts", "events"):
            out[table] = [dict(r) for r in
                          populated.conn.execute(f"SELECT * FROM {table}").fetchall()]
        return out

    before = snapshot_tables()
    report = _today()
    canonical_before = report.to_json()

    build_snapshot(report, populated, now=NOW)

    assert snapshot_tables() == before, "canonical rows must be untouched"
    assert report.to_json() == canonical_before, "the report object must be untouched"


def test_statements_carry_no_prediction_or_recommendation(snapshot):
    """The wording boundary, asserted rather than assumed."""
    banned = ("buy", "sell ", "should", "will ", "expect", "likely", "target",
              "opportunity", "momentum", "crash", "rally", "recommend")
    for insight in snapshot.displayable():
        lowered = insight.statement.lower()
        for term in banned:
            # "net sellers"/"net buyers" describe what happened and are allowed; the banned
            # forms are the imperative and predictive ones.
            if term in ("buy", "sell "):
                assert "net buyers" in lowered or "net sellers" in lowered or term not in lowered
                continue
            assert term not in lowered, f"{insight.insight_id}: {insight.statement}"


def test_statements_pass_the_publication_content_scan(snapshot):
    from core.content_safety import SafetyStatus, classify_text
    for insight in snapshot.displayable():
        assert classify_text(insight.statement).status is SafetyStatus.SAFE, insight.statement


# --------------------------------------------------------------------- degradation
def test_cold_start_produces_a_valid_empty_snapshot(tmp_path):
    with MarketHistory(str(tmp_path / "empty.db")) as history:
        snapshot = build_snapshot(_today(), history, now=NOW)
    assert snapshot.available_history == 0
    assert snapshot.displayable() == []
    assert snapshot.selected() == []
    assert any("no prior canonical sessions" in w for w in snapshot.warnings)
    json.loads(snapshot.to_json())                       # still a valid artifact


def test_partial_history_yields_short_windows_only(tmp_path):
    """Seven sessions: the 5-session statistics work, the 20-session ones do not."""
    with MarketHistory(str(tmp_path / "short.db")) as history:
        seed(history, _history_reports()[:7])
        snapshot = build_snapshot(_today(), history, now=NOW)

    assert _by_id(snapshot, "index-move-5").strength is Strength.FULL_HISTORY
    assert _by_id(snapshot, "index-move-20").strength is Strength.INSUFFICIENT_HISTORY
    assert _by_id(snapshot, "index-move-20").statement == ""
    assert any("20 sessions" in w for w in snapshot.warnings)


def test_no_history_database_is_not_a_failure():
    snapshot = build_snapshot(_today(), None, now=NOW)
    assert snapshot.insights == []
    assert any("no historical database" in w for w in snapshot.warnings)


def test_unreadable_history_degrades_without_fabricating(populated):
    """A storage failure must produce no insights, not plausible ones."""
    class _Broken:
        def get_recent_facts(self, *a, **k):
            raise RuntimeError("database disk image is malformed")

        def get_recent_metric_points(self, *a, **k):
            raise RuntimeError("database disk image is malformed")

    snapshot = build_snapshot(_today(), _Broken(), now=NOW)
    assert snapshot.insights == []
    assert any("unreadable" in w for w in snapshot.warnings)
    assert snapshot.available_history == 0


def test_demo_history_is_excluded_from_production_intelligence(tmp_path):
    """Synthetic fixtures must not become historical context for a real report."""
    with MarketHistory(str(tmp_path / "mixed.db")) as history:
        for report in _history_reports():
            history.save_report(report, artifact_path="x.json", is_demo=True)
        snapshot = build_snapshot(_today(), history, now=NOW, include_demo=False)

    assert snapshot.available_history == 0
    assert snapshot.displayable() == []


def test_describe_summarises_the_snapshot(snapshot):
    text = describe(snapshot)
    assert "insights" in text and "prior sessions" in text
