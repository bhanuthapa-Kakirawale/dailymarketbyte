"""Historical index: what has happened across many runs.

This is deliberately NOT the source of truth for any single run - the MarketReport JSON
artifact is, and it stays immutable. SQLite is the queryable index over those artifacts, and
every stored report carries the path of the JSON it came from so the authoritative file is
always locatable. Nothing in the rendering path reads from here.
"""
from .migrations import SCHEMA_VERSION, SchemaVersionError, current_version, initialise
from .repository import (DEFAULT_DB_RELPATH, MarketHistory, StoredFact, StoredObservation,
                         StoredReport, StoredRun, StoredValidationResult, default_db_path)

__all__ = ["MarketHistory", "default_db_path", "DEFAULT_DB_RELPATH", "SCHEMA_VERSION",
           "SchemaVersionError", "current_version", "initialise", "StoredReport", "StoredFact",
           "StoredObservation", "StoredValidationResult", "StoredRun"]
