"""Development-artifact cleanup - DRY RUN by default (docs/CLEANUP_POLICY.md).

Deletes only what is unquestionably temporary, generated and reproducible; everything else is
kept. The rules are an ALLOW-list (a file must match a rule to be a candidate) filtered by a
DENY-list that always wins:

  candidates
    PYTHON_CACHE     __pycache__/ directories (outside venv/.venv/.git) and .pytest_cache/
    OUTPUT_TEMP      output/ root scratch: bg_music_*.wav (legacy music cache), nifty_chart_*.png
                     (legacy chart layers), the superseded redesign MP4, output/frames/ images
                     (logs and text are evidence and are kept)
    DEMO_RENDER      output/daily_byte_*_DEMO.mp4 (synthetic data by definition)
    REVIEW_MEDIA     MP4 / PNG / JPG / GIF / WAV inside SUPERSEDED review-render folders; their
                     JSON / DB / CSV / MD / TXT / LOG (the evidence) are kept

  never (whatever a rule says)
    - anything outside the project root, any symlink (never followed), .git / venv / .venv /
      .claude, every source, test, docs, data and asset file (only output/ and caches are
      ever candidates)
    - .env*, token.json, client_secret.json, any *.db / *.db-wal / *.db-shm / *.sqlite*
    - PROTECTED output folders: databases, canonical reports, Radar and every radar_* folder
      (private intelligence), Market Structure, official snapshots, intelligence, Exchange
      Watch lists, publication audits, QA records, run records, POST run folders, PRE shadow
      mornings, the readiness backups, the current approval reference renders, the state-store
      simulation's store
    - any file a non-demo run in the history database points at (its artifact_path / the
      report's json_artifact_path)
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import sqlite3
from dataclasses import dataclass

MEDIA_EXT = (".mp4", ".png", ".jpg", ".jpeg", ".gif", ".wav", ".mp3", ".webm")
PROTECTED_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")
PROTECTED_NAMES = ("token.json", "client_secret.json")
NEVER_DIRS = (".git", "venv", ".venv", ".claude", "node_modules")

# output/ folders that hold state, history, private intelligence or audit - never touched
PROTECTED_OUTPUT = (
    "data", "reports", "radar", "market_structure", "official_snapshots", "intelligence",
    "exchange_watch", "publication", "qa", "report_jobs", "post", "pre_shadow", "premarket",
    "pre_shadow_readiness", "pre_production_readiness", "pre_data_sources",
    "benchmark_gap_recovery", "session_alignment_validation", "public_intelligence_v1",
    "state_handoff_sim/store", "private_radar",
)
PROTECTED_OUTPUT_PREFIXES = ("radar",)          # radar, radar_phase2, radar_validation*, ...

# review-render folders superseded by later approved renders: media only is cleanable
SUPERSEDED_REVIEW = (
    "daily_market_byte_hook_phase1", "daily_market_byte_hook_phase1a",
    "daily_market_byte_redesign", "hook_previews", "hook_previews_phase1", "post_phase3",
    "post_validation", "post_freeze_validation", "post_final_edge_cases", "pre_phase1",
    "redesign_audit", "state_handoff_sim/runnerB",
)
OUTPUT_TEMP_PATTERNS = ("bg_music_*.wav", "nifty_chart_*.png", "daily_market_byte_redesign_*.mp4")


@dataclass
class Candidate:
    path: str
    rule: str
    size: int
    is_dir: bool = False


def _size(path: str) -> int:
    if os.path.isdir(path):
        total = 0
        for dp, _, files in os.walk(path, followlinks=False):
            for f in files:
                fp = os.path.join(dp, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
        return total
    return os.path.getsize(path)


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def referenced_artifacts(root: str) -> set:
    """Absolute, normalised paths (and bare file names under output/) that the history database
    records for NON-demo runs and canonical reports. Read-only; a missing / unreadable database
    protects nothing extra (the protected folders still apply)."""
    db = os.path.join(root, "output", "data", "market_history.db")
    refs = set()
    if not os.path.exists(db):
        return refs
    try:
        from storage.readonly import connect_readonly
        con = connect_readonly(db)                  # creates no WAL sidecar next to production
        try:
            rows = con.execute("select artifact_path from publication_runs "
                               "where artifact_path is not null and upper(coalesce(mode,'')) "
                               "not like '%DEMO%'").fetchall()
            rows += con.execute("select json_artifact_path from reports "
                                "where json_artifact_path is not null").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return refs
    for (p,) in rows:
        refs.add(os.path.normcase(os.path.abspath(p)))
        refs.add("name:" + os.path.basename(str(p).replace("\\", "/")).lower())
    return refs


def protected_reason(root: str, path: str, refs: set | None = None) -> str | None:
    """Why `path` must never be deleted, or None. The deny-list: it always wins."""
    root = os.path.abspath(root)
    ap = os.path.abspath(path)
    real = os.path.realpath(ap)
    if not (real + os.sep).startswith(root + os.sep) and real != root:
        return "outside the project"
    if os.path.islink(ap):
        return "symlink (never followed or removed)"
    rel = _rel(root, ap)
    parts = rel.split("/")
    if parts[0] in NEVER_DIRS or any(p in NEVER_DIRS for p in parts):
        return f"{parts[0]}/ is never cleaned"
    name = parts[-1].lower()
    if name.startswith(".env"):
        return "environment / secrets file"
    if name in PROTECTED_NAMES:
        return "credential file"
    if name.endswith(PROTECTED_SUFFIXES):
        return "database"
    is_cache = "__pycache__" in parts or parts[0] == ".pytest_cache"
    if parts[0] != "output" and not is_cache:
        return "source / tests / docs / data / assets"
    if parts[0] == "output" and len(parts) > 1:
        sub = "/".join(parts[1:])
        for p in PROTECTED_OUTPUT:
            if sub == p or sub.startswith(p + "/"):
                return f"protected state: output/{p}"
        if any(parts[1].startswith(pre) for pre in PROTECTED_OUTPUT_PREFIXES):
            return "private intelligence: output/" + parts[1]
    if refs:
        if os.path.normcase(ap) in refs or ("name:" + name) in refs:
            return "referenced by run history"
    return None


def plan(root: str) -> tuple:
    """(candidates, kept) - nothing is touched."""
    root = os.path.abspath(root)
    refs = referenced_artifacts(root)
    cands, kept = [], []

    def consider(path, rule, is_dir=False):
        why = protected_reason(root, path, refs)
        if why:
            kept.append((_rel(root, path), why))
        else:
            cands.append(Candidate(path, rule, _size(path), is_dir))

    # PYTHON_CACHE
    for dp, dirs, _ in os.walk(root, followlinks=False):
        rel_parts = _rel(root, dp).split("/")
        if rel_parts[0] in NEVER_DIRS or rel_parts[0] == "output":
            dirs[:] = []
            continue
        for d in list(dirs):
            full = os.path.join(dp, d)
            if os.path.islink(full):
                dirs.remove(d)
                continue
            if d in ("__pycache__", ".pytest_cache"):
                consider(full, "PYTHON_CACHE", is_dir=True)
                dirs.remove(d)
    out = os.path.join(root, "output")
    if os.path.isdir(out):
        # OUTPUT_TEMP + DEMO_RENDER at output/ root
        for f in sorted(os.listdir(out)):
            full = os.path.join(out, f)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            if any(fnmatch.fnmatch(f, pat) for pat in OUTPUT_TEMP_PATTERNS):
                consider(full, "OUTPUT_TEMP")
            elif fnmatch.fnmatch(f, "daily_byte_*_DEMO.mp4"):
                consider(full, "DEMO_RENDER")
        frames = os.path.join(out, "frames")
        if os.path.isdir(frames) and not os.path.islink(frames):
            for f in sorted(os.listdir(frames)):
                if f.lower().endswith(MEDIA_EXT):
                    consider(os.path.join(frames, f), "OUTPUT_TEMP")
        # REVIEW_MEDIA
        for sub in SUPERSEDED_REVIEW:
            base = os.path.join(out, *sub.split("/"))
            if not os.path.isdir(base) or os.path.islink(base):
                continue
            for dp, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(dp, d))]
                for f in sorted(files):
                    if f.lower().endswith(MEDIA_EXT):
                        consider(os.path.join(dp, f), "REVIEW_MEDIA")
    return cands, kept


def execute(root: str, cands) -> dict:
    """Delete the planned candidates - re-checking the deny-list for each one first."""
    refs = referenced_artifacts(root)
    removed, skipped, freed = [], [], 0
    for c in cands:
        why = protected_reason(root, c.path, refs)
        if why or not os.path.exists(c.path):
            skipped.append((c.path, why or "gone"))
            continue
        if c.is_dir:
            shutil.rmtree(c.path)
        else:
            os.remove(c.path)
        removed.append(c.path)
        freed += c.size
    # empty review folders left behind by media removal are removed too (never protected ones)
    for sub in SUPERSEDED_REVIEW:
        base = os.path.join(root, "output", *sub.split("/"))
        for dp, dirs, files in sorted(os.walk(base, topdown=False, followlinks=False),
                                      key=lambda t: -len(t[0])):
            if not os.listdir(dp) and not protected_reason(root, dp, refs):
                os.rmdir(dp)
    return {"removed": len(removed), "skipped": skipped, "freed_bytes": freed}


def summarize(cands, kept) -> dict:
    by_rule = {}
    for c in cands:
        n, b = by_rule.get(c.rule, (0, 0))
        by_rule[c.rule] = (n + 1, b + c.size)
    return {"candidates": len(cands), "bytes": sum(c.size for c in cands),
            "by_rule": by_rule, "kept_matches": len(kept)}


__all__ = ["plan", "execute", "summarize", "protected_reason", "referenced_artifacts",
           "Candidate", "PROTECTED_OUTPUT", "SUPERSEDED_REVIEW", "MEDIA_EXT"]
