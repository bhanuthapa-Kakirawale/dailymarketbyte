"""Historical index: what has happened across many runs.

This is deliberately NOT the source of truth for any single run - the MarketReport JSON
artifact is, and it stays immutable. SQLite is the queryable index over those artifacts, and
every stored report carries the path of the JSON it came from so the authoritative file is
always locatable. Nothing in the rendering path reads from here.
"""
from .candidate_history_migrations import CandidateHistorySchemaVersionError
from .candidate_history_models import RunStatus, StoredCandidateState, StoredRunMarker
from .candidate_history_repository import (
    DEFAULT_DB_RELPATH as CANDIDATE_HISTORY_DEFAULT_DB_RELPATH, CandidateHistoryStore,
    default_db_path as candidate_history_default_db_path)
from .editorial_migrations import EditorialSchemaVersionError
from .editorial_models import EditorialSelection, SelectionLifecycle
from .editorial_repository import (DEFAULT_DB_RELPATH as EDITORIAL_DEFAULT_DB_RELPATH,
                                   EditorialStore, default_db_path as editorial_default_db_path)
from .migrations import SCHEMA_VERSION, SchemaVersionError, current_version, initialise
from .ohlcv_models import OHLCVBar, QualityStatus
from .ohlcv_repository import (DEFAULT_DB_RELPATH as OHLCV_DEFAULT_DB_RELPATH, OHLCVRow,
                               OHLCVStore, OhlcvSchemaVersionError,
                               default_db_path as ohlcv_default_db_path)
from .repository import (DEFAULT_DB_RELPATH, JOB_POST_MARKET, JOB_PRE_MARKET,
                         JOB_REPORT_BUILD, JOB_TYPES, MarketHistory, StoredFact,
                         StoredMetricPoint, StoredObservation, StoredReport, StoredRun, StoredValidationResult,
                         default_db_path, job_type_of)

__all__ = ["MarketHistory", "default_db_path", "DEFAULT_DB_RELPATH", "SCHEMA_VERSION",
           "SchemaVersionError", "current_version", "initialise", "StoredReport", "StoredFact",
           "StoredObservation", "StoredValidationResult", "StoredRun", "StoredMetricPoint",
           "OHLCVStore", "OHLCVRow", "OHLCVBar", "QualityStatus", "OhlcvSchemaVersionError",
           "ohlcv_default_db_path", "OHLCV_DEFAULT_DB_RELPATH",
           "EditorialStore", "EditorialSelection", "SelectionLifecycle",
           "EditorialSchemaVersionError", "editorial_default_db_path",
           "EDITORIAL_DEFAULT_DB_RELPATH",
           "CandidateHistoryStore", "StoredCandidateState", "CandidateHistorySchemaVersionError",
           "candidate_history_default_db_path", "CANDIDATE_HISTORY_DEFAULT_DB_RELPATH",
           "RunStatus", "StoredRunMarker", "job_type_of", "JOB_TYPES", "JOB_REPORT_BUILD",
           "JOB_POST_MARKET", "JOB_PRE_MARKET"]
