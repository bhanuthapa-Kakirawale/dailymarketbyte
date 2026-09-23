"""radar/acquisition.py: universe-wide RVOL persisted as a distinct RADAR_SCAN report, never
the day's real PRE_MARKET report, never STOCK_CLOSE/STOCK_CHANGE_PCT. Offline only.
"""
import datetime as dt

import pytest

from conftest import NOW
from conftest_radar import BASE_SESSION, movers, seed, session_report, trading_sessions
from conftest_universe import RECAP_DATE, fake_bulk_ohlcv, synthetic_universe

from core import Metric, ReportType
from intelligence import movers as intelligence_movers
from intelligence.history import HistoricalWindow
from radar.acquisition import acquire_universe_scan_report
from storage import MarketHistory


@pytest.fixture
def db(tmp_path):
    history = MarketHistory(str(tmp_path / "history.db"))
    yield history
    history.close()


def _report_date(session):
    return session + dt.timedelta(days=1)


# =============================================================== persistence path
def test_acquire_universe_scan_report_persists_via_existing_save_report_path(db, monkeypatch):
    universe = synthetic_universe(10)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    scan_report, skip_reasons, persisted = acquire_universe_scan_report(
        db, universe, report_date=_report_date(recap_date), recap_date=recap_date,
        prev_date=prev_date, now=NOW)

    assert persisted is True
    assert scan_report.report_type is ReportType.RADAR_SCAN
    stored = db.get_report(scan_report.report_id)
    assert stored is not None
    assert stored.json_artifact_path is None            # DB-only, no JSON artifact
    assert skip_reasons == {}


def test_rerun_is_idempotent_like_every_other_canonical_report(db, monkeypatch):
    universe = synthetic_universe(5)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)

    _, _, first = acquire_universe_scan_report(
        db, universe, report_date=_report_date(recap_date), recap_date=recap_date,
        prev_date=prev_date, now=NOW)
    _, _, second = acquire_universe_scan_report(
        db, universe, report_date=_report_date(recap_date), recap_date=recap_date,
        prev_date=prev_date, now=NOW)

    assert first is True
    assert second is False                               # already stored - not re-inserted


def test_scan_report_id_is_distinct_from_a_premarket_report_on_the_same_date(db, monkeypatch):
    premarket = session_report(RECAP_DATE)
    seed(db, [premarket])

    universe = synthetic_universe(5)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)
    scan_report, _, _ = acquire_universe_scan_report(
        db, universe, report_date=premarket.report_date, recap_date=recap_date,
        prev_date=prev_date, now=NOW)

    assert scan_report.report_id != premarket.report_id
    assert scan_report.report_id.endswith("_RADAR_SCAN")


# =============================================================== double-counting guard
def test_already_covered_symbols_are_excluded_no_duplicate_fact_for_the_same_session(db, monkeypatch):
    universe = synthetic_universe(5)                     # SYM000..SYM004
    mover_symbol = next(iter(universe))                  # SYM000
    premarket = session_report(RECAP_DATE, gainers=movers([mover_symbol], volx=3.0))
    seed(db, [premarket])

    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)
    scan_report, _, _ = acquire_universe_scan_report(
        db, universe, report_date=premarket.report_date, recap_date=recap_date,
        prev_date=prev_date, already_covered={mover_symbol}, now=NOW)

    assert mover_symbol not in {f.instrument for f in scan_report.facts}
    assert len(scan_report.facts) == 4


# =============================================================== RVOL-only scope
def test_scan_report_contains_only_stock_relative_volume_facts(db, monkeypatch):
    universe = synthetic_universe(6)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)
    scan_report, _, _ = acquire_universe_scan_report(
        db, universe, report_date=_report_date(recap_date), recap_date=recap_date,
        prev_date=prev_date, now=NOW)

    metrics = {f.metric for f in scan_report.facts}
    assert metrics == {Metric.STOCK_RELATIVE_VOLUME}
    assert scan_report.facts, "expected at least one usable RVOL fact"


def test_scan_report_validation_status_matches_the_movers_path_for_the_same_conditions(db, monkeypatch):
    """A non-mover's RVOL fact reaches the same status a mover's does today - same source,
    same policy, no second weaker validation path."""
    universe = synthetic_universe(1)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, universe)
    scan_report, _, _ = acquire_universe_scan_report(
        db, universe, report_date=_report_date(recap_date), recap_date=recap_date,
        prev_date=prev_date, now=NOW)

    mover_report = session_report(RECAP_DATE, gainers=movers(list(universe), volx=2.0))
    scan_status = scan_report.facts[0].validation_status
    mover_status = mover_report.facts_for(Metric.STOCK_RELATIVE_VOLUME)[0].validation_status
    assert scan_status == mover_status


# =============================================================== the corruption guard
def test_analyse_recurrence_is_unaffected_by_scan_report_facts(db, monkeypatch):
    """The regression that proves the RVOL-only boundary actually closes the risk found while
    planning this packet: intelligence/movers.py::analyse_recurrence infers "was a tracked
    mover" purely from the EXISTENCE of a STOCK_CHANGE_PCT fact, with no bucket check. Seeding
    hundreds of scan-report facts must not change its output at all."""
    sessions = trading_sessions(6, last=BASE_SESSION)
    history_sessions, today_session = sessions[:-1], sessions[-1]

    history_reports = [session_report(s, gainers=movers(["ABC"] if i % 2 == 0 else ["XYZ"], volx=1.5))
                       for i, s in enumerate(history_sessions)]
    seed(db, history_reports)
    today_report = session_report(today_session, gainers=movers(["ABC"], volx=2.0))

    baseline = intelligence_movers.analyse_recurrence(
        today_report, HistoricalWindow(db, today_report.session_date, report_id=today_report.report_id))
    assert baseline, "the fixture must actually exercise analyse_recurrence"

    for s in history_sessions:
        wide_universe = synthetic_universe(15)
        recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, wide_universe, recap_date=s, sessions=63)
        acquire_universe_scan_report(db, wide_universe, report_date=_report_date(s),
                                     recap_date=recap_date, prev_date=prev_date, now=NOW)

    after = intelligence_movers.analyse_recurrence(
        today_report, HistoricalWindow(db, today_report.session_date, report_id=today_report.report_id))

    assert [i.to_dict() for i in after] == [i.to_dict() for i in baseline]
