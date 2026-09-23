"""Phase 4.2 Packet 5.4C, packet spec section 29: production selector vs `HYBRID_RESERVED_
DIVERSITY` (Policy F) equivalence, over the real 60-session local validation dataset.

This is the one test in this packet that touches real local data (store-only, zero Yahoo
universe calls, one Yahoo benchmark call, one NSE constituent call - same network footprint as
`python -m radar.editorial_policy_validation_60`) rather than hand-built fixtures, because the
packet requires proving equivalence against the ACTUAL 60-session dataset Packet 5.4B.2
validated, not a synthetic stand-in. Expected: 100% `(session_date, symbol)` selection match.
"""
import datetime as dt

import market
import radar.editorial_policy_hybrid as eph
import radar.editorial_policy_validation_60 as v60
import radar.editorial_selector as es
import radar.historical_validation as hv
import radar.relative_acquisition as relative_acquisition
from config import OUT_DIR
from radar.novelty import classify_history
from radar.novelty_validation import combined_records
from storage.ohlcv_repository import OHLCVStore, default_db_path


def _run_60_session_pipeline():
    universe_full = market.get_universe("NIFTY200")
    store = OHLCVStore(default_db_path(OUT_DIR))
    store_symbols = {row.symbol for row in store.get_range(
        list(universe_full), dt.date(2000, 1, 1), dt.date(2100, 1, 1))}
    universe = {s: universe_full[s] for s in universe_full if s in store_symbols}

    benchmark_series_full = relative_acquisition.build_market_benchmark_series(period="1y")
    selected, prev_dates, _warnings = v60.select_60_sessions(benchmark_series_full)

    results = []
    for session_date, prev_date in zip(selected, prev_dates):
        r = hv.run_one_session(universe, session_date, prev_date, benchmark_series_full, store)
        results.append(r)
    store.close()
    return results


def test_matches_policy_f_over_60_session_dataset():
    results = _run_60_session_pipeline()

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
