"""Phase 4.2 Packet 5.4C, packet spec section 29: production selector vs `HYBRID_RESERVED_
DIVERSITY` (Policy F) equivalence, over the real 60-session validation dataset.

This is the one test in this packet that runs over REAL recorded market data rather than
hand-built synthetic fixtures, because the packet requires proving equivalence against the
ACTUAL 60-session dataset Packet 5.4B.2 validated, not a synthetic stand-in. PK-B (test
reproducibility) froze that real dataset into a tracked fixture
(`tests/fixtures/editorial_selector/ohlcv_60_session_2026-09-21.csv`, see
`_generate_fixture.py` in the same directory for provenance) so this test no longer depends on
a developer's locally-accumulated `output/data/market_ohlcv.db` or on a live NSE/Yahoo call -
it is real data, just a frozen snapshot instead of a live, continuously-accumulating one.
Expected: 100% `(session_date, symbol)` selection match.
"""
import csv
import datetime as dt
from pathlib import Path

import radar.editorial_policy_hybrid as eph
import radar.editorial_policy_validation_60 as v60
import radar.editorial_selector as es
import radar.historical_validation as hv
from radar.novelty import classify_history
from radar.novelty_validation import combined_records
from storage.ohlcv_models import OHLCVBar, QualityStatus
from storage.ohlcv_repository import OHLCVStore

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "editorial_selector" / "ohlcv_60_session_2026-09-21.csv"
BENCHMARK_SYMBOL = "^NSEI"


def _load_fixture_rows() -> list[dict]:
    with open(FIXTURE_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _run_60_session_pipeline(tmp_path):
    rows = _load_fixture_rows()

    store = OHLCVStore(str(tmp_path / "ohlcv.db"))
    bars = [OHLCVBar(symbol=r["symbol"], session_date=dt.date.fromisoformat(r["session_date"]),
                     open=float(r["open"]) if r["open"] else None,
                     high=float(r["high"]) if r["high"] else None,
                     low=float(r["low"]) if r["low"] else None,
                     close=float(r["close"]) if r["close"] else None,
                     volume=float(r["volume"]) if r["volume"] else None,
                     source=r["source"], retrieved_at=dt.datetime.fromisoformat(r["retrieved_at"]),
                     quality_status=QualityStatus(r["quality_status"]))
            for r in rows]
    store.upsert_bars(bars)

    frozen_symbols = {r["symbol"] for r in rows}
    universe = {s: s for s in frozen_symbols if s != BENCHMARK_SYMBOL}

    benchmark_rows = sorted((r for r in rows if r["symbol"] == BENCHMARK_SYMBOL),
                           key=lambda r: r["session_date"])
    benchmark_series_full = [{"date": dt.date.fromisoformat(r["session_date"]),
                              "close": float(r["close"])} for r in benchmark_rows]

    selected, prev_dates, _warnings = v60.select_60_sessions(benchmark_series_full)

    results = []
    for session_date, prev_date in zip(selected, prev_dates):
        r = hv.run_one_session(universe, session_date, prev_date, benchmark_series_full, store)
        results.append(r)
    store.close()
    return results


def test_matches_policy_f_over_60_session_dataset(tmp_path):
    results = _run_60_session_pipeline(tmp_path)

    # Policy F's own dict-record simulation, exactly as Packet 5.4B.2 runs it - frozen,
    # untouched by this packet.
    sessions_for_novelty = [(r["session_date"], r["composite_snapshot"].candidates) for r in results]
    novelty_by_date = classify_history(sessions_for_novelty)
    all_records = combined_records(results, novelty_by_date)
    by_date: dict = {}
    for rec in all_records:
        by_date.setdefault(rec["date"], []).append(rec)
    dict_sessions = [(r["session_date"], by_date.get(r["session_date"].isoformat(), [])) for r in results]

    sim_hybrid = eph.simulate_hybrid_history(dict_sessions)
    policy_f_keys = {(d.isoformat(), s["symbol"])
                     for d, stories in sim_hybrid["stories"][eph.POLICY_HYBRID_RESERVED_DIVERSITY].items()
                     for s in stories}

    # New production selector, run chronologically over the SAME 60 sessions using the REAL
    # typed StockRadarCandidate/CandidateNovelty objects (never the dict-record shape).
    history: dict = {}
    selector_keys: set = set()
    mismatched_sessions = []
    for i, r in enumerate(results):
        session_date = r["session_date"]
        candidates = r["composite_snapshot"].candidates
        novelties = novelty_by_date[session_date]
        pairs = list(zip(candidates, novelties))
        recently_selected = {sym: idx for sym, idx in history.items()
                             if i - idx <= es.PUBLICATION_COOLDOWN_SESSIONS}

        result = es.select_session(session_date, pairs, recently_selected)
        session_keys = {(session_date.isoformat(), s.instrument) for s in result.selected}
        selector_keys |= session_keys

        expected_keys = {k for k in policy_f_keys if k[0] == session_date.isoformat()}
        if session_keys != expected_keys:
            mismatched_sessions.append({
                "session_date": session_date.isoformat(),
                "selector_only": sorted(k[1] for k in session_keys - expected_keys),
                "policy_f_only": sorted(k[1] for k in expected_keys - session_keys),
            })

        for s in result.selected:
            history[s.instrument] = i

    assert mismatched_sessions == [], (
        f"{len(mismatched_sessions)}/60 sessions mismatched between the production selector "
        f"and Policy F: {mismatched_sessions}")
    assert selector_keys == policy_f_keys
    assert len(selector_keys) == 300  # 60 sessions x <=5 stories; both policies filled every session
