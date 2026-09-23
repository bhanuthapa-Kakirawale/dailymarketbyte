"""SQLite schema for `radar_candidate_history.db` (Phase 4.2 Packet 5.4D).

A third, independent database - separate from both `market_history.db` (canonical validated
market facts) and `editorial_selections.db` (editorial/publication decisions). Its ONLY job is
to let a daily production run reproduce `radar.novelty`'s validated "compare against the most
recent candidate within the last N valid trading sessions" semantics across process restarts -
Packets 5.4A/5.4B's own novelty/policy simulations could do this in memory because they saw all
20/60 sessions in one process; a real daily cron run sees exactly one session per process, so
the small amount of state novelty needs from PRIOR sessions must survive between runs somewhere.

Deliberately NOT canonical history and NOT reused from `market_history.db`'s own facts table:
what novelty compares is composite-candidate STATE (`active_families`, structural reason codes,
persistence/direction/attention), which is a Radar-specific derived concept with no Fact/Metric
representation anywhere in `core/`, not "what the market did". Deliberately NOT folded into
`editorial_selections.db` either (packet spec section 10) - candidate history answers "was this
symbol Radar-interesting recently" (an input to novelty), editorial history answers "did we
publish this symbol recently" (an input to cooldown); conflating the two would let an unrelated
symbol's editorial suppression quietly change another symbol's novelty classification.

Minimal by design (packet spec section 11): only the fields `radar.novelty` actually reads are
stored - no OHLCV, no full detector snapshots, no duplicated market data.
"""
from __future__ import annotations

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS radar_candidate_history (
    session_date               TEXT NOT NULL,
    instrument                  TEXT NOT NULL,
    active_families_json          TEXT,
    reason_codes_json               TEXT,
    independent_signal_count          INTEGER,
    direction_compatibility             TEXT,
    attention_level                      TEXT,
    persistence_state                     TEXT,
    price_change_pct                       REAL,
    calculation_version                     TEXT,
    created_at                               TEXT NOT NULL,
    PRIMARY KEY (session_date, instrument)
);

CREATE INDEX IF NOT EXISTS idx_candidate_history_session
    ON radar_candidate_history (session_date);
CREATE INDEX IF NOT EXISTS idx_candidate_history_instrument
    ON radar_candidate_history (instrument, session_date);

-- Session-level processing marker (Phase 4.2 Packet 5.4E). A valid trading session can
-- legitimately produce ZERO composite candidates - that must never be confused with "this
-- session was never processed". `radar_candidate_history` row EXISTENCE alone cannot make this
-- distinction (a zero-candidate session inserts no rows at all), so this table is the
-- authoritative record of "was session X actually run through the candidate-history pipeline,
-- and under which calculation_version". Keyed on (session_date, calculation_version) rather
-- than session_date alone: a session already processed under an OLDER calculation_version must
-- remain distinguishable from - and reprocessable under - a newer one, never silently treated
-- as equivalent (packet spec section 15). `status` is `COMPLETE` only once candidate persistence
-- for that session has actually succeeded; a crash or exception between computing candidates and
-- persisting this marker must never leave a false `COMPLETE` row - see
-- `radar.candidate_history_backfill` for the write ordering this depends on.
CREATE TABLE IF NOT EXISTS candidate_history_runs (
    session_date         TEXT NOT NULL,
    calculation_version    TEXT NOT NULL,
    status                   TEXT NOT NULL,
    candidate_count            INTEGER NOT NULL,
    processed_at                 TEXT NOT NULL,
    PRIMARY KEY (session_date, calculation_version)
);

CREATE INDEX IF NOT EXISTS idx_candidate_history_runs_session
    ON candidate_history_runs (session_date);
"""

__all__ = ["SCHEMA_SQL", "SCHEMA_VERSION"]
