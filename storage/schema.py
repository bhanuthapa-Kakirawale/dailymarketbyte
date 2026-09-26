"""SQLite schema for the historical index.

The schema follows the canonical domain (report -> fact -> observation -> source), not the
renderer's structures. The renderer's shapes are a presentation concern that has already
changed once; the domain is the thing worth indexing for years.

What this database is NOT: the source of truth for any single run. The JSON artifact is.
SQLite answers "what has happened across many runs"; JSON answers "what exactly did this run
produce". Rows here carry `json_artifact_path` so the authoritative file is always locatable.
"""
from __future__ import annotations

# v2 (PRE data-sources phase): publication_runs gains target_date + run_status so PRE-MARKET
# runs (which never upload) are auditable in the same operational history as POST runs.
# v3 (PRE production scheduling): publication_runs gains job_type (REPORT_BUILD / POST_MARKET /
# PRE_MARKET - `mode` alone was ambiguous: a POST run's mode is LOCAL/PRODUCTION/DEMO) and
# source_session_date (the canonical session whose report the job built or consumed).
SCHEMA_VERSION = 3

# Composite primary keys throughout: a fact_id is canonical *within* a report
# (fact_index-close-nifty-50-2026-09-18), but two different reports could legitimately
# describe the same session, so report_id is part of every canonical identity here.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    report_id          TEXT PRIMARY KEY,
    report_type        TEXT NOT NULL,
    report_date        TEXT NOT NULL,
    session_date       TEXT,
    generated_at       TEXT,
    schema_version     TEXT,
    publication_ready  INTEGER NOT NULL DEFAULT 0,
    is_demo            INTEGER NOT NULL DEFAULT 0,
    json_artifact_path TEXT,
    validation_summary_json TEXT,
    content_safety_json     TEXT,
    metadata_json      TEXT,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_dates ON reports (report_date, report_type);
CREATE INDEX IF NOT EXISTS idx_reports_session ON reports (session_date);

-- Registry of sources as last seen. Per-report independence is NOT read back from here:
-- every observation stores its own independence_group, so a later registry change can never
-- retroactively alter what an archived report was corroborated by. See docs/STORAGE.md.
CREATE TABLE IF NOT EXISTS sources (
    source_name          TEXT PRIMARY KEY,
    source_type          TEXT,
    source_family        TEXT,
    upstream_source      TEXT,
    independence_group   TEXT,
    retrieval_method     TEXT,
    reference            TEXT,
    display_rights_status TEXT,
    metadata_json        TEXT,
    first_seen_at        TEXT,
    last_seen_at         TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    report_id         TEXT NOT NULL,
    fact_id           TEXT NOT NULL,
    metric            TEXT NOT NULL,
    instrument        TEXT NOT NULL,
    value             REAL,
    unit              TEXT,
    market_date       TEXT,
    validation_status TEXT NOT NULL,
    metadata_json     TEXT,
    PRIMARY KEY (report_id, fact_id),
    FOREIGN KEY (report_id) REFERENCES reports (report_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_facts_metric ON facts (metric, instrument, market_date);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts (validation_status);

CREATE TABLE IF NOT EXISTS observations (
    report_id          TEXT NOT NULL,
    fact_id            TEXT NOT NULL,
    observation_id     TEXT NOT NULL,
    source_name        TEXT,
    source_type        TEXT,
    independence_group TEXT,
    value              REAL,
    unit               TEXT,
    market_date        TEXT,
    observed_at        TEXT,
    retrieved_at       TEXT,
    source_reference   TEXT,
    metadata_json      TEXT,
    PRIMARY KEY (report_id, fact_id, observation_id),
    FOREIGN KEY (report_id, fact_id) REFERENCES facts (report_id, fact_id) ON DELETE CASCADE,
    FOREIGN KEY (source_name) REFERENCES sources (source_name)
);

CREATE INDEX IF NOT EXISTS idx_observations_source ON observations (source_name);
CREATE INDEX IF NOT EXISTS idx_observations_group ON observations (independence_group);

-- Every check that ran, not just the verdict: a CONFLICT is only auditable if the compared
-- values and tolerances survive with it.
CREATE TABLE IF NOT EXISTS validation_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   TEXT NOT NULL,
    fact_id     TEXT NOT NULL,
    validator   TEXT NOT NULL,
    status      TEXT NOT NULL,
    message     TEXT,
    checked_at  TEXT,
    details_json TEXT,
    FOREIGN KEY (report_id, fact_id) REFERENCES facts (report_id, fact_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_validation_fact ON validation_results (report_id, fact_id);

CREATE TABLE IF NOT EXISTS catalysts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id      TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    bucket         TEXT,
    text           TEXT,
    catalyst_type  TEXT,
    origin         TEXT,
    source_name    TEXT,
    source_type    TEXT,
    independence_group TEXT,
    publisher      TEXT,
    headline_date  TEXT,
    metadata_json  TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (report_id, symbol),
    FOREIGN KEY (report_id) REFERENCES reports (report_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id      TEXT NOT NULL,
    position       INTEGER NOT NULL,
    tag            TEXT,
    text           TEXT,
    origin         TEXT,
    source_name    TEXT,
    source_type    TEXT,
    independence_group TEXT,
    publisher      TEXT,
    event_date     TEXT,
    provenance_resolved INTEGER,
    metadata_json  TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (report_id, position),
    FOREIGN KEY (report_id) REFERENCES reports (report_id) ON DELETE CASCADE
);

-- Operational history, deliberately append-only and separate from canonical history: a
-- report is a statement about the market, a run is a statement about one execution. This is
-- the table that answers "why wasn't the 22 Sep report published?".
CREATE TABLE IF NOT EXISTS publication_runs (
    run_id             TEXT PRIMARY KEY,
    report_id          TEXT,
    mode               TEXT NOT NULL,
    stage              TEXT,
    started_at         TEXT NOT NULL,
    completed_at       TEXT,
    data_qa_status     TEXT,
    content_qa_status  TEXT,
    video_qa_status    TEXT,
    publication_status TEXT,
    artifact_path      TEXT,
    youtube_video_id   TEXT,
    failure_stage      TEXT,
    failure_reason     TEXT,
    details_json       TEXT,
    target_date        TEXT,   -- v2: the session the run is FOR (PRE: the session about to open)
    run_status         TEXT,   -- v2: SUCCESS / DEGRADED / BLOCKED / FAILED / SKIPPED
    job_type           TEXT,   -- v3: REPORT_BUILD / POST_MARKET / PRE_MARKET (NULL = legacy row)
    source_session_date TEXT   -- v3: the canonical session whose report the job built/consumed
);

CREATE INDEX IF NOT EXISTS idx_runs_report ON publication_runs (report_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON publication_runs (started_at);
CREATE INDEX IF NOT EXISTS idx_runs_mode_target ON publication_runs (mode, target_date);
CREATE INDEX IF NOT EXISTS idx_runs_job_target ON publication_runs (job_type, target_date);
"""

__all__ = ["SCHEMA_SQL", "SCHEMA_VERSION"]
