"""Official daily snapshots: the complete validated state of each official list (NSE IPO issue
lists, F&O ban file, ASM / GSM) captured per trading session by the REPORT job - whether or not
anything on it is ever shown - stored as immutable revisions with a manifest, and read back by
POST / PRE / replay. Exchange Watch changes and IPO events are derived FROM the stored snapshots.
See docs/ARCHITECTURE.md (Acquisition -> Durable state -> Publication)."""
from .changes import detect, exchange_events, summarize
from .models import *  # noqa: F401,F403
from .models import __all__ as _models_all
from .service import OfficialDailySnapshotService, rollup
from .store import load_current, load_manifest, manifest_path, session_dir, write_revision

__all__ = list(_models_all) + ["OfficialDailySnapshotService", "rollup", "detect",
                               "exchange_events", "summarize", "load_current", "load_manifest",
                               "manifest_path", "session_dir", "write_revision"]
