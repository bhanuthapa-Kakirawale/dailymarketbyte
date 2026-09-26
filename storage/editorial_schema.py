"""SQLite schema for `editorial_selections.db` (Phase 4.2 Packet 5.4C).

Deliberately a SEPARATE database from both `market_history.db` (canonical, immutable market
facts) and `market_ohlcv.db` (raw provider bars) - an editorial selection is neither. It is an
operational decision this pipeline made ("show these 5 stories today"), and unlike a canonical
report it has a LIFECYCLE (`SELECTED` -> `RENDERED` -> `PUBLISHED`/`FAILED`) that legitimately
changes after the row is written - exactly the kind of in-place mutation CLAUDE.md's "canonical
history is immutable" rule forbids for `market_history.db`. Keeping it in its own file means a
schema bug or corruption here can never touch a single published market fact or raw bar, and
this database can be deleted and rebuilt from the next scheduled Radar run with zero effect on
canonical history (same isolation guarantee `market_ohlcv.db` already has - see
docs/OHLCV_STORE.md).

One row per (session_date, instrument, selector_version) - see `radar.editorial_selector` for
why the selector version is part of identity: a future selector version's rules are a genuinely
different decision, not a correction of the old one, so both remain queryable side by side.
"""
from __future__ import annotations

# v2 (PRE shadow readiness): `radar_publications` - which selected stories actually appeared in
# a completed, QA-passed POST artifact. Selection (editorial_selections) is NOT publication.
SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS editorial_selections (
    selection_id              TEXT PRIMARY KEY,
    session_date               TEXT NOT NULL,
    instrument                  TEXT NOT NULL,
    novelty_type                 TEXT NOT NULL,
    active_families_json          TEXT,
    independent_signal_count       INTEGER,
    direction_compatibility         TEXT,
    attention_level                  TEXT,
    selection_bucket                  TEXT NOT NULL,
    reserved_3family                   INTEGER NOT NULL DEFAULT 0,
    diversity_role                      TEXT,
    cooldown_status                      TEXT,
    cooldown_override_reason              TEXT,
    reason_codes_json                      TEXT,
    selection_reason                        TEXT,
    selector_version                         TEXT NOT NULL,
    lifecycle_state                           TEXT NOT NULL DEFAULT 'SELECTED',
    selected_at                                TEXT NOT NULL,
    updated_at                                  TEXT,
    metadata_json                                TEXT,
    UNIQUE (session_date, instrument, selector_version)
);

CREATE INDEX IF NOT EXISTS idx_editorial_selections_session
    ON editorial_selections (session_date);
CREATE INDEX IF NOT EXISTS idx_editorial_selections_instrument
    ON editorial_selections (instrument, session_date);

-- v2: RADAR_PUBLISHED. One row per (session, instrument) that appeared in a completed POST
-- artifact which passed every QA gate. Written ONCE per session by the first confirmation
-- (a rerun is a no-op), never by the REPORT job. Publication cooldown reads THIS table; the
-- selection table above only says what the selector chose.
CREATE TABLE IF NOT EXISTS radar_publications (
    publication_id    TEXT PRIMARY KEY,
    session_date      TEXT NOT NULL,
    instrument        TEXT NOT NULL,
    story_rank        INTEGER NOT NULL,
    selection_id      TEXT,
    post_run_id       TEXT,
    artifact_path     TEXT,
    qa_json           TEXT,
    confirmed_at      TEXT NOT NULL,
    metadata_json     TEXT,
    UNIQUE (session_date, instrument)
);

CREATE INDEX IF NOT EXISTS idx_radar_publications_session
    ON radar_publications (session_date, story_rank);
"""

__all__ = ["SCHEMA_SQL", "SCHEMA_VERSION"]
