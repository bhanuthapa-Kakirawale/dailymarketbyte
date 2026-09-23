"""Unusual-volume radar: classification thresholds, historical context, traceability,
determinism, and the boundaries it must not cross - no acquisition, no mutation, no
fabrication, no price-direction bias. Offline only; no network, no video, no editorial.
"""
import datetime as dt
import hashlib
import json
import os

import pytest

from conftest import NOW
from conftest_radar import (BASE_SESSION, duplicate_relative_volume_observations,
                            historical_rvol_series, movers, seed, session_report,
                            trading_sessions, universe, _rewrite_relative_volume_version)
from conftest_universe import RECAP_DATE, fake_bulk_ohlcv, synthetic_universe

from core import Metric
from intelligence import movers as intelligence_movers
from intelligence.history import HistoricalWindow
from radar import (AnomalyLevel, DEFAULT_VOLUME_THRESHOLDS, VolumeRadarSnapshot,
                   VolumeThresholds, classify_anomaly, scan_universe)
from radar.acquisition import acquire_universe_scan_report
from radar.engine import save_snapshot
from storage import MarketHistory

_ROW = {"name": "x", "close": 100.0, "reason": "", "reason_source": "NO_VERIFIED_CATALYST"}


def _gainer(symbol, pct, volx):
    return {"symbol": symbol, "pct": pct, "volx": volx, **_ROW}


@pytest.fixture
def db(tmp_path):
    history = MarketHistory(str(tmp_path / "history.db"))
    yield history
    history.close()


# =============================================================== classify_anomaly (pure)
def test_below_elevated_threshold_is_not_flagged():
    assert classify_anomaly(1.5, None, 0) is None
    assert classify_anomaly(None, None, 0) is None


def test_elevated_band():
    assert classify_anomaly(2.0, None, 0) is AnomalyLevel.ELEVATED
    assert classify_anomaly(2.9, None, 0) is AnomalyLevel.ELEVATED


def test_unusual_band():
    assert classify_anomaly(3.0, None, 0) is AnomalyLevel.UNUSUAL
    assert classify_anomaly(4.9, None, 0) is AnomalyLevel.UNUSUAL


def test_extreme_band():
    assert classify_anomaly(5.0, None, 0) is AnomalyLevel.EXTREME
    assert classify_anomaly(10.0, None, 0) is AnomalyLevel.EXTREME


def test_boundary_values_are_inclusive():
    assert classify_anomaly(2.0, None, 0) is AnomalyLevel.ELEVATED
    assert classify_anomaly(1.999, None, 0) is None
    assert classify_anomaly(3.0, None, 0) is AnomalyLevel.UNUSUAL
    assert classify_anomaly(5.0, None, 0) is AnomalyLevel.EXTREME


def test_percentile_upgrades_elevated_to_unusual():
    assert classify_anomaly(2.2, 90.0, 20) is AnomalyLevel.UNUSUAL
    assert classify_anomaly(2.2, 89.9, 20) is AnomalyLevel.ELEVATED


def test_percentile_upgrades_unusual_to_extreme():
    assert classify_anomaly(3.5, 97.0, 20) is AnomalyLevel.EXTREME
    assert classify_anomaly(3.5, 96.9, 20) is AnomalyLevel.UNUSUAL


def test_percentile_never_downgrades():
    """A weak historical percentile cannot demote a level RVOL alone already earned."""
    assert classify_anomaly(3.5, 0.0, 25) is AnomalyLevel.UNUSUAL
    assert classify_anomaly(6.0, 0.0, 25) is AnomalyLevel.EXTREME


def test_percentile_ignored_below_min_sample():
    assert classify_anomaly(2.2, 99.0, 5) is AnomalyLevel.ELEVATED


def test_thresholds_are_overridable():
    custom = VolumeThresholds(elevated_rvol=1.2, unusual_rvol=1.8, extreme_rvol=2.5,
                              unusual_percentile=80.0, extreme_percentile=95.0,
                              min_percentile_sample=10)
    assert classify_anomaly(1.3, None, 0, custom) is AnomalyLevel.ELEVATED
    assert classify_anomaly(1.1, None, 0, custom) is None
    assert classify_anomaly(1.3, 80.0, 10, custom) is AnomalyLevel.UNUSUAL
    # the default thresholds are untouched by a custom instance
    assert classify_anomaly(1.3, None, 0, DEFAULT_VOLUME_THRESHOLDS) is None


# =============================================================== RVOL reuse, not redefinition
def test_relative_volume_field_equals_canonical_fact_value(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 3.0, 3.3)])
    snapshot = scan_universe(today, db, universe("ABC"))
    fact = today.facts_for(Metric.STOCK_RELATIVE_VOLUME)[0]
    assert snapshot.anomalies[0].relative_volume == fact.value == pytest.approx(3.3)


def test_radar_never_recomputes_relative_volume_from_raw_volumes():
    import inspect

    import radar.volume
    source = inspect.getsource(radar.volume)
    assert "market.relative_volume(" not in source
    assert "import market" not in source


def test_radar_never_acquires_market_data():
    """The layer reads canonical report + history and nothing else."""
    import inspect

    import radar
    from radar import engine, models, thresholds, volume
    forbidden = ("yfinance", "requests", "ask_gemini", "google_news", "urllib",
                "providers", "market.get_", "news.ai_pass")
    for module in (radar, engine, models, thresholds, volume):
        source = inspect.getsource(module)
        for term in forbidden:
            assert term not in source, f"{module.__name__} must not reference {term}"


def test_universe_is_never_hardcoded():
    import inspect

    import radar.volume
    source = inspect.getsource(radar.volume)
    for banned in ("NIFTY50", "NIFTY_50", "nifty50", "RELIANCE", "HDFCBANK", "TCS"):
        assert banned not in source


# =============================================================== historical percentile/rank
def test_percentile_matches_count_below_definition(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    seed(db, historical_rvol_series("ABC", [1.0] * 24, today=BASE_SESSION))

    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    assert anomaly.prior_sessions_available == 24
    assert anomaly.rvol_historical_rank == 24               # all 24 priors are < 4.1
    assert anomaly.rvol_percentile == pytest.approx(100.0)
    assert anomaly.level is AnomalyLevel.EXTREME             # UNUSUAL by band, EXTREME by pct


def test_percentile_omitted_below_min_sample(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 2.5)])
    seed(db, historical_rvol_series("ABC", [1.0] * 6, today=BASE_SESSION))

    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    assert anomaly.prior_sessions_available == 6
    assert anomaly.rvol_percentile is None
    assert anomaly.level is AnomalyLevel.ELEVATED            # RVOL-only fallback
    assert any("percentile omitted" in w for w in anomaly.warnings)


# =============================================================== highest RVOL wording fields
def test_highest_rvol_in_n_sessions_incomplete_window(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 6.0)])
    seed(db, historical_rvol_series("ABC", [1.0, 2.0, 5.5, 1.0, 3.0, 1.5, 2.0], today=BASE_SESSION))

    anomaly = scan_universe(today, db, universe("ABC"), highest_rvol_window=20).anomalies[0]
    highest = anomaly.highest_rvol_in_n_sessions
    assert highest["n_requested"] == 20
    assert highest["n_recorded"] == 7
    assert highest["is_complete_window"] is False
    assert highest["value"] == pytest.approx(5.5)


def test_highest_rvol_in_n_sessions_complete_window(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 6.0)])
    seed(db, historical_rvol_series("ABC", [1.0] * 19 + [4.5], today=BASE_SESSION))

    anomaly = scan_universe(today, db, universe("ABC"), highest_rvol_window=20).anomalies[0]
    highest = anomaly.highest_rvol_in_n_sessions
    assert highest["n_requested"] == 20
    assert highest["n_recorded"] == 20
    assert highest["is_complete_window"] is True
    assert highest["value"] == pytest.approx(4.5)


def test_highest_rvol_is_none_with_zero_history(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 2.5)])
    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    assert anomaly.highest_rvol_in_n_sessions is None
    assert anomaly.prior_sessions_available == 0


# =============================================================== definition-version compatibility
def test_today_v1_reading_is_excluded_from_classification(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 6.0)], definition_version="1.0")
    snapshot = scan_universe(today, db, universe("ABC"))
    assert snapshot.anomalies == []
    assert "ABC" in snapshot.scanned
    assert any("older definition" in w for w in snapshot.warnings)


def test_historical_v1_readings_excluded_from_sample(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    v1_history = historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION)
    for report in v1_history:
        _rewrite_relative_volume_version(report, "1.0")
    seed(db, v1_history)

    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    assert anomaly.prior_sessions_available == 0
    assert anomaly.rvol_percentile is None
    assert anomaly.level is AnomalyLevel.UNUSUAL              # RVOL-only fallback, no v1 leakage


# =============================================================== canonical-fact-identity dedup
def test_duplicate_observations_do_not_inflate_sample(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    history = historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION)
    for report in history:
        duplicate_relative_volume_observations(report, copies=3)
    seed(db, history)

    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    assert anomaly.prior_sessions_available == 20              # not 80


def test_dedup_matches_movers_semantics(db):
    """Radar's canonical-fact-identity collapse must agree with intelligence/movers.py's,
    guarding the two independent implementations against drift."""
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    seed(db, historical_rvol_series("ABC", [1.0] * 20 + [2.0] * 3, today=BASE_SESSION))

    radar_anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]

    window = HistoricalWindow(db, today.session_date, report_id=today.report_id)
    insight = next(i for i in intelligence_movers.analyse_relative_volume(today, window)
                  if i.subject == "ABC")
    assert radar_anomaly.prior_sessions_available == insight.metadata["comparable_sample"]
    assert radar_anomaly.rvol_historical_rank == insight.metadata["higher_than_readings"]


# =============================================================== traceability
def test_supporting_fact_ids_resolve_to_stored_facts(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    seed(db, historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION))

    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    stored = {f.fact_id for f in db.get_facts(metric=Metric.STOCK_RELATIVE_VOLUME.value)}
    historical_ids = [fid for fid in anomaly.supporting_fact_ids if fid in stored]
    assert len(historical_ids) == 20                          # today's own fact was never seeded
    assert anomaly.supporting_fact_ids[0] not in stored


def test_why_flagged_is_nonempty_plain_language_and_free_of_banned_wording(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    seed(db, historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION))

    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    assert anomaly.why_flagged
    banned = ("buy", "sell ", "should", "will ", "expect", "likely", "target",
             "opportunity", "momentum", "crash", "rally", "recommend")
    lowered = anomaly.why_flagged.lower()
    for term in banned:
        assert term not in lowered, anomaly.why_flagged


# =============================================================== determinism
def test_snapshot_is_byte_identical_across_runs(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    seed(db, historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION))

    first = scan_universe(today, db, universe("ABC"), as_of=NOW).to_json()
    second = scan_universe(today, db, universe("ABC"), as_of=NOW).to_json()
    assert first == second
    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(second.encode()).hexdigest()


def test_snapshot_round_trips(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    seed(db, historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION))

    snapshot = scan_universe(today, db, universe("ABC"), as_of=NOW)
    restored = VolumeRadarSnapshot.from_json(snapshot.to_json())
    assert restored.to_dict() == snapshot.to_dict()


def test_anomalies_are_sorted_by_relative_volume_then_instrument(db):
    gainers = [_gainer("LOW", 1.0, 2.5), _gainer("HIGH", 1.0, 8.0), _gainer("MID", 1.0, 4.0)]
    today = session_report(BASE_SESSION, gainers=gainers)
    snapshot = scan_universe(today, db, universe("LOW", "HIGH", "MID"))
    assert [a.instrument for a in snapshot.anomalies] == ["HIGH", "MID", "LOW"]


def test_anomalies_tie_break_alphabetically(db):
    gainers = [_gainer("ZZZ", 1.0, 6.0), _gainer("AAA", 1.0, 6.0)]
    today = session_report(BASE_SESSION, gainers=gainers)
    snapshot = scan_universe(today, db, universe("ZZZ", "AAA"))
    assert [a.instrument for a in snapshot.anomalies] == ["AAA", "ZZZ"]


# =============================================================== no mutation
def test_scan_universe_does_not_mutate_canonical_history(db):
    seed(db, historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION))

    def snapshot_tables():
        tables = ("reports", "facts", "observations", "validation_results", "catalysts", "events")
        return {t: [dict(r) for r in db.conn.execute(f"SELECT * FROM {t}").fetchall()] for t in tables}

    before = snapshot_tables()
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    canonical_before = today.to_json()

    scan_universe(today, db, universe("ABC"))

    assert snapshot_tables() == before, "canonical rows must be untouched"
    assert today.to_json() == canonical_before, "the report object must be untouched"


# =============================================================== universe / skip semantics
def test_universe_member_outside_todays_movers_is_skipped_with_reason(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    snapshot = scan_universe(today, db, universe("ABC", "NOTAMOVER"))
    assert snapshot.skipped["NOTAMOVER"] == "no STOCK_RELATIVE_VOLUME fact in today's report"
    assert "NOTAMOVER" not in snapshot.scanned


def test_batch_history_load_is_a_single_query_across_the_universe(db, monkeypatch):
    seed(db, historical_rvol_series("AAA", [1.0] * 20, today=BASE_SESSION))
    today = session_report(BASE_SESSION,
                           gainers=[_gainer("AAA", 1.0, 4.1), _gainer("BBB", 1.0, 3.0),
                                    _gainer("CCC", 1.0, 2.5)])

    calls = []
    original = db.get_recent_metric_points

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "get_recent_metric_points", spy)
    scan_universe(today, db, universe("AAA", "BBB", "CCC"))
    assert len(calls) == 1, "expected one batched query, not one per instrument"


def test_price_change_pct_does_not_affect_classification(db):
    """The spec's own example: a large price move with normal volume must not be flagged,
    and a modest price move with extreme volume must be - proving the detector finds
    unusual participation rather than simply chasing price gainers."""
    gainers = [_gainer("STOCKA", 1.2, 4.1), _gainer("STOCKB", 7.8, 1.1)]
    today = session_report(BASE_SESSION, gainers=gainers)
    snapshot = scan_universe(today, db, universe("STOCKA", "STOCKB"))

    by_symbol = {a.instrument: a for a in snapshot.anomalies}
    assert "STOCKB" not in by_symbol                          # +7.8% price, 1.1x volume: normal
    assert "STOCKB" in snapshot.scanned
    assert by_symbol["STOCKA"].level is AnomalyLevel.UNUSUAL   # +1.2% price, 4.1x volume: unusual
    assert by_symbol["STOCKA"].price_change_pct == pytest.approx(1.2)


def test_current_volume_and_average_volume_are_none_with_data_quality_note(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    seed(db, historical_rvol_series("ABC", [1.0] * 20, today=BASE_SESSION))

    anomaly = scan_universe(today, db, universe("ABC")).anomalies[0]
    assert anomaly.current_volume is None
    assert anomaly.average_volume_20 is None
    assert any("raw_volume_unavailable" in note for note in anomaly.data_quality)


def test_cold_start_empty_history_still_classifies_on_rvol_alone(db):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 2.5)])
    snapshot = scan_universe(today, db, universe("ABC"))
    assert snapshot.anomalies[0].level is AnomalyLevel.ELEVATED
    assert snapshot.anomalies[0].prior_sessions_available == 0
    json.loads(snapshot.to_json())                            # still a valid artifact


def test_no_historical_database_is_not_a_failure():
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    snapshot = scan_universe(today, None, universe("ABC"))
    assert snapshot.anomalies
    assert any("no historical database" in w for w in snapshot.warnings)


# =============================================================== artifact writer
def test_artifact_is_written_and_versioned(db, tmp_path):
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    snapshot = scan_universe(today, db, universe("ABC"), as_of=NOW)

    path = save_snapshot(snapshot, str(tmp_path))
    payload = json.loads(open(path, encoding="utf-8").read())
    assert os.path.basename(path) == f"volume_{BASE_SESSION:%Y-%m-%d}.json"
    assert payload["radar_schema_version"]
    assert payload["calculation_version"]


# =============================================================== the spec's own demonstration
def test_flags_unusual_participation_not_just_price_gainers(db):
    """STOCK-A: modest price move, extreme relative volume with a full comparable history ->
    flagged. STOCK-B: a large price move on ordinary volume -> not flagged. This is the
    detector's whole point: unusual participation, not a gainers list with a volume column."""
    gainers = [_gainer("STOCKA", 1.2, 4.1), _gainer("STOCKB", 7.8, 1.1)]
    today = session_report(BASE_SESSION, gainers=gainers)
    seed(db, historical_rvol_series("STOCKA", [1.0] * 47, today=BASE_SESSION))

    snapshot = scan_universe(today, db, universe("STOCKA", "STOCKB"), as_of=NOW)
    by_symbol = {a.instrument: a for a in snapshot.anomalies}

    assert by_symbol["STOCKA"].level is AnomalyLevel.EXTREME
    assert by_symbol["STOCKA"].prior_sessions_available == 47
    assert by_symbol["STOCKA"].rvol_percentile == pytest.approx(100.0)
    assert by_symbol["STOCKA"].highest_rvol_in_n_sessions["is_complete_window"] is True
    assert "STOCKB" not in by_symbol
    assert "STOCKB" in snapshot.scanned


# =============================================================== Packet 1.1: universe-wide RVOL
def test_scan_universe_stays_backward_compatible_without_scan_report(db):
    """Packet 1's original call shape (no scan_report/acquisition_skip_reasons) behaves
    exactly as it did before this packet - every prior test in this file already proves this
    implicitly; this makes the guarantee explicit."""
    today = session_report(BASE_SESSION, gainers=[_gainer("ABC", 1.0, 4.1)])
    snapshot = scan_universe(today, db, universe("ABC"))
    assert snapshot.anomalies[0].instrument == "ABC"


def test_non_mover_with_high_rvol_produces_anomaly_via_the_full_pipeline(db, monkeypatch):
    """The required regression: a stock nowhere near today's gainers/losers, with a modest
    price move and high relative volume, still produces a VolumeAnomaly once the universe-wide
    scan report is supplied - proving the Radar no longer depends on top-mover selection."""
    today_report = session_report(RECAP_DATE, gainers=[_gainer("MOVERX", 1.0, 1.2)])

    wide_universe = synthetic_universe(20)
    target = next(iter(wide_universe))
    volumes = [1_000_000.0] * 62 + [6_000_000.0]
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, wide_universe, volume_map={target: volumes})
    scan_report, skip_reasons, _ = acquire_universe_scan_report(
        db, wide_universe, report_date=today_report.report_date, recap_date=recap_date,
        prev_date=prev_date, already_covered={"MOVERX"}, now=NOW)

    assert target not in {f.instrument for f in today_report.facts}, \
        "the fixture must prove the target is genuinely not a mover"

    snapshot = scan_universe(today_report, db, universe("MOVERX", target),
                             scan_report=scan_report, acquisition_skip_reasons=skip_reasons, as_of=NOW)

    by_symbol = {a.instrument: a for a in snapshot.anomalies}
    assert target in by_symbol
    assert by_symbol[target].level is AnomalyLevel.EXTREME
    assert by_symbol[target].relative_volume == pytest.approx(6.0)
    assert "MOVERX" not in by_symbol                     # 1.2x RVOL: not anomalous


def test_universe_coverage_grows_from_movers_only_to_full_universe(db, monkeypatch):
    today_report = session_report(RECAP_DATE, gainers=[_gainer("MOVERX", 1.0, 1.2)])
    wide_universe = synthetic_universe(30)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, wide_universe)
    scan_report, skip_reasons, _ = acquire_universe_scan_report(
        db, wide_universe, report_date=today_report.report_date, recap_date=recap_date,
        prev_date=prev_date, already_covered={"MOVERX"}, now=NOW)

    full_universe = universe("MOVERX", *wide_universe.keys())
    without_scan = scan_universe(today_report, db, full_universe)
    with_scan = scan_universe(today_report, db, full_universe,
                              scan_report=scan_report, acquisition_skip_reasons=skip_reasons)

    assert len(without_scan.skipped) == 30, "only MOVERX has a fact without the scan report"
    assert len(with_scan.skipped) == 0
    assert len(with_scan.scanned) == 31


def test_batch_history_load_stays_a_single_query_with_a_scan_report(db, monkeypatch):
    today_report = session_report(RECAP_DATE, gainers=[_gainer("MOVERX", 1.0, 1.2)])
    wide_universe = synthetic_universe(50)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, wide_universe)
    scan_report, skip_reasons, _ = acquire_universe_scan_report(
        db, wide_universe, report_date=today_report.report_date, recap_date=recap_date,
        prev_date=prev_date, already_covered={"MOVERX"}, now=NOW)

    calls = []
    original = db.get_recent_metric_points

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "get_recent_metric_points", spy)
    scan_universe(today_report, db, universe("MOVERX", *wide_universe.keys()),
                 scan_report=scan_report, acquisition_skip_reasons=skip_reasons)
    assert len(calls) == 1, "one batched query must cover the whole scanned universe"


def test_real_premarket_report_is_unchanged_after_a_universe_scan_run(db, monkeypatch):
    """Immutability: acquiring a wider scan report never rewrites the day's existing
    canonical PRE_MARKET rows - it only appends a distinct report."""
    premarket = session_report(RECAP_DATE, gainers=[_gainer("MOVERX", 1.0, 1.2)])
    seed(db, [premarket])

    def premarket_rows():
        tables = ("reports", "facts", "observations", "validation_results")
        return {t: [dict(r) for r in db.conn.execute(
            f"SELECT * FROM {t} WHERE report_id = ?", (premarket.report_id,)).fetchall()]
                for t in tables}

    before = premarket_rows()
    wide_universe = synthetic_universe(10)
    recap_date, prev_date = fake_bulk_ohlcv(monkeypatch, wide_universe)
    acquire_universe_scan_report(db, wide_universe, report_date=premarket.report_date,
                                 recap_date=recap_date, prev_date=prev_date,
                                 already_covered={"MOVERX"}, now=NOW)

    assert premarket_rows() == before
