"""On-disk layout for Trade Candidate Intelligence snapshots - immutable, revisioned, mirroring
`institutional_flows/store.py` exactly:

    <out_dir>/private_candidates/<score_policy_version>/<session_date>/<symbol>.json     rev 1
    <out_dir>/private_candidates/<score_policy_version>/<session_date>/<symbol>.r2.json  rev 2

Keyed by (score_policy_version, session_date, symbol): a later policy version writes to its OWN
folder, so it structurally never touches an earlier version's files - a stored V1 snapshot stays
reproducible under its own policy even after the policy changes (owner spec section 39). Within
one (policy version, session, symbol), a re-run on the same day legitimately produces a new
revision (e.g. a detector value changed because the stored OHLCV was repaired) - a revision
file, once written, is never edited or replaced, only superseded by a new, separately-numbered
file, exactly like `institutional_flows`/`market_events`.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

from .models import TradeCandidateAssessment

ROOT_DIRNAME = "private_candidates"


def _safe(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in key) or "unknown"


def _session_dir(out_dir: str, score_policy_version: str, session_iso: str) -> str:
    return os.path.join(out_dir, ROOT_DIRNAME, _safe(score_policy_version), session_iso)


def _revision_path(out_dir: str, score_policy_version: str, session_iso: str, symbol: str,
                   n: int) -> str:
    stem = _safe(symbol)
    name = f"{stem}.json" if n == 1 else f"{stem}.r{n}.json"
    return os.path.join(_session_dir(out_dir, score_policy_version, session_iso), name)


def _existing_revisions(out_dir: str, score_policy_version: str, session_iso: str,
                        symbol: str) -> list:
    stem = _safe(symbol)
    folder = _session_dir(out_dir, score_policy_version, session_iso)
    paths = sorted(glob.glob(os.path.join(folder, f"{stem}.json")) +
                   glob.glob(os.path.join(folder, f"{stem}.r*.json")))
    return paths


def load_revision(path: str) -> TradeCandidateAssessment | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return TradeCandidateAssessment.from_dict(json.load(fh))


def write_snapshot(out_dir: str, assessment: TradeCandidateAssessment) -> str:
    """Append `assessment` as the next revision of (score_policy_version, session_date, symbol).
    Never refuses because an earlier revision already exists for the same key - only ever ADDS
    a new, never-reused file name (exclusive create)."""
    from operations.run_context import guard_write
    folder = _session_dir(out_dir, assessment.score_policy_version, assessment.session_date)
    guard_write(folder, "trade candidate snapshot")
    os.makedirs(folder, exist_ok=True)
    existing = _existing_revisions(out_dir, assessment.score_policy_version,
                                   assessment.session_date, assessment.symbol)
    n = len(existing) + 1
    path = _revision_path(out_dir, assessment.score_policy_version, assessment.session_date,
                          assessment.symbol, n)
    while os.path.exists(path):
        n += 1
        path = _revision_path(out_dir, assessment.score_policy_version, assessment.session_date,
                              assessment.symbol, n)
    with open(path, "x", encoding="utf-8") as fh:        # exclusive create - never overwrite
        json.dump(assessment.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    return path


def load_latest(out_dir: str, score_policy_version: str, session: dt.date,
                symbol: str) -> TradeCandidateAssessment | None:
    revisions = _existing_revisions(out_dir, score_policy_version, session.isoformat(), symbol)
    return load_revision(revisions[-1]) if revisions else None


def list_symbols(out_dir: str, score_policy_version: str, session: dt.date) -> list:
    folder = _session_dir(out_dir, score_policy_version, session.isoformat())
    if not os.path.isdir(folder):
        return []
    stems = set()
    for path in glob.glob(os.path.join(folder, "*.json")):
        name = os.path.basename(path)[:-5]
        stems.add(name.split(".r")[0])
    return sorted(stems)


def list_sessions(out_dir: str, score_policy_version: str) -> list:
    root = os.path.join(out_dir, ROOT_DIRNAME, _safe(score_policy_version))
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        try:
            out.append(dt.date.fromisoformat(name))
        except ValueError:
            continue
    return sorted(out)


__all__ = ["write_snapshot", "load_revision", "load_latest", "list_symbols", "list_sessions",
          "ROOT_DIRNAME"]
