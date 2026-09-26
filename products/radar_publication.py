"""RADAR_SELECTED vs RADAR_PUBLISHED - the one place a Radar story becomes "published".

    RADAR_SELECTED   the Radar editorial selector chose it for session D (REPORT job,
                     `radar.daily_pipeline` -> `editorial_selections`). Intelligence, not
                     publication: no viewer has seen it.
    RADAR_PUBLISHED  it appeared in a COMPLETED POST artifact for D that passed every QA gate
                     (video, readability, content). Recorded here, after the render + QA, in
                     `radar_publications`; the matching selection rows move to lifecycle
                     PUBLISHED. The publication cooldown and PRE's stock watch read THIS.

Rules:
* The REPORT job never calls `confirm_radar_publication`.
* A failed / QA-blocked POST publishes nothing: selected stories stay SELECTED.
* Only the stories actually rendered count (selector picked 5, POST showed 3 -> 3 published).
* Exactly once per session: a rerun is a no-op (`ALREADY_CONFIRMED`, with a flag if the rerun's
  artifact showed a different set) - history and cooldown never advance twice.
* Demo runs never publish.

"Published" here means "in a completed, QA-passed POST artifact" (owner definition); whether the
artifact was also uploaded to YouTube is recorded alongside (`uploaded`, `youtube_video_id`) but
is not the trigger.
"""
from __future__ import annotations

import datetime as dt
import json
import os

NOT_CONFIRMED = "NOT_CONFIRMED"          # QA failed / render incomplete: nothing written
NO_RADAR_STORIES = "NO_RADAR_STORIES"    # the artifact contains no Radar story
DEMO_NOT_RECORDED = "DEMO_NOT_RECORDED"
LEDGER_ERROR = "LEDGER_ERROR"
CONFIRMED, ALREADY_CONFIRMED = "CONFIRMED", "ALREADY_CONFIRMED"

# The legacy production POST (`main.run` -> `video.scenes_from_plan`) has no Radar section.
LEGACY_RENDERER_NOTE = ("the production POST renderer (main.run -> video.scenes_from_plan) has "
                        "no Radar section - no Radar story is shown, so none is published")


def rendered_radar_stories(storyboard) -> list:
    """The Radar stories a storyboard actually contains, in on-screen order."""
    out = []
    for spec in getattr(storyboard, "scenes", []) or []:
        if getattr(spec, "kind", None) == "RADAR_STORY":
            sym = (getattr(spec, "data", None) or {}).get("symbol") or \
                  (getattr(spec, "texts", None) or {}).get("symbol")
            if sym:
                out.append(sym)
    return list(dict.fromkeys(out))


def _store(out_dir: str, create: bool = True):
    """The editorial store. A reader (`create=False`) never creates the database: no file means
    nothing was ever selected or published, which the caller reports as such."""
    from storage.editorial_repository import EditorialStore, default_db_path
    path = default_db_path(out_dir)
    if not create and not os.path.exists(path):
        raise FileNotFoundError(f"no editorial database at {path}")
    return EditorialStore(path)


def confirm_radar_publication(session_date: dt.date, rendered: list, *, qa_passed: bool,
                              out_dir: str, run_id: str | None = None,
                              artifact_path: str | None = None, qa: dict | None = None,
                              demo: bool = False, uploaded: bool = False,
                              youtube_video_id: str | None = None, confirmed_by: str = "",
                              confirmed_at: dt.datetime | None = None) -> dict:
    """Mark `rendered` (on-screen order) PUBLISHED for `session_date` - only when the artifact
    passed QA. Never raises: a ledger failure is reported, never allowed to cost the run."""
    confirmed_at = confirmed_at or dt.datetime.now(dt.timezone.utc)
    base = {"session_date": str(session_date), "rendered": list(rendered),
            "artifact_path": artifact_path, "qa_passed": bool(qa_passed), "qa": qa or {},
            "run_id": run_id, "uploaded": bool(uploaded), "youtube_video_id": youtube_video_id,
            "confirmed_by": confirmed_by}
    if demo:
        return {**base, "status": DEMO_NOT_RECORDED, "published": [],
                "reason": "demo runs never publish"}
    if not qa_passed:
        return {**base, "status": NOT_CONFIRMED, "published": [],
                "reason": "the POST artifact did not pass QA - selected stories stay SELECTED"}
    if not rendered:
        return {**base, "status": NO_RADAR_STORIES, "published": [],
                "reason": "the POST artifact contains no Radar story"}
    try:
        store = _store(out_dir)
        try:
            res = store.record_publication(
                session_date, rendered, post_run_id=run_id, artifact_path=artifact_path,
                qa=qa, confirmed_at=confirmed_at,
                metadata={"uploaded": bool(uploaded), "youtube_video_id": youtube_video_id,
                          "confirmed_by": confirmed_by})
        finally:
            store.close()
    except Exception as exc:
        return {**base, "status": LEDGER_ERROR, "published": [],
                "reason": f"{type(exc).__name__}: {exc}"}
    status = res["status"]
    out = {**base, "status": status, "published": res["published"],
           "confirmed_at": res["first_confirmed_at"]}
    if status == ALREADY_CONFIRMED:
        out["matches_prior"] = res["matches_prior"]
        out["reason"] = ("session already confirmed - nothing re-recorded" +
                         ("" if res["matches_prior"] else
                          "; WARNING: this artifact showed a different story set"))
    return out


def radar_selection(session_date: dt.date, out_dir: str) -> dict:
    """What the Radar SELECTED for a session, in the selector's order (the daily Radar artifact),
    cross-checked with the stored selection rows. Read-only, never raises."""
    session = str(session_date)
    selected, source = [], None
    path = os.path.join(out_dir, "radar", f"daily_radar_{session}.json")
    if os.path.exists(path):
        try:
            selected = [s["instrument"] for s in json.load(open(path, encoding="utf-8"))
                        .get("stories") or []]
            source = path
        except (OSError, ValueError, KeyError, TypeError):
            pass
    stored = []
    try:
        store = _store(out_dir, create=False)
        try:
            stored = [s.instrument for s in store.get_session_selections(session_date)]
        finally:
            store.close()
    except Exception:
        pass
    if not selected and stored:
        selected, source = sorted(stored), "editorial_selections"
    return {"session_date": session, "selected_count": len(selected),
            "selected_symbols": selected, "source": source}


def radar_publication(session_date: dt.date, out_dir: str) -> dict:
    """What was PUBLISHED for a session (the ledger), in on-screen order. Never raises."""
    from storage.editorial_repository import default_db_path
    if not os.path.exists(default_db_path(out_dir)):
        return {"session_date": str(session_date), "status": "NONE_CONFIRMED",
                "published_count": 0, "published_symbols": [], "confirmed_at": None,
                "artifact_path": None, "post_run_id": None, "qa": None,
                "note": "no editorial database - nothing was ever published"}
    try:
        store = _store(out_dir, create=False)
        try:
            rows = store.get_session_publications(session_date)
        finally:
            store.close()
    except Exception as exc:
        return {"session_date": str(session_date), "status": LEDGER_ERROR, "published_count": 0,
                "published_symbols": [], "error": f"{type(exc).__name__}: {exc}"}
    return {"session_date": str(session_date),
            "status": CONFIRMED if rows else "NONE_CONFIRMED",
            "published_count": len(rows), "published_symbols": [r["instrument"] for r in rows],
            "confirmed_at": rows[0]["confirmed_at"] if rows else None,
            "artifact_path": rows[0]["artifact_path"] if rows else None,
            "post_run_id": rows[0]["post_run_id"] if rows else None,
            "qa": rows[0]["qa"] if rows else None}


def published_instruments(session_date: dt.date, out_dir: str) -> tuple:
    """(instruments in on-screen order, status) - PRE stock watch's source of truth."""
    pub = radar_publication(session_date, out_dir)
    return pub["published_symbols"], pub["status"]


def audit(out_dir: str) -> dict:
    """Selected vs published, per session with any Radar editorial state."""
    sessions = []
    try:
        store = _store(out_dir, create=False)
        try:
            dates = store.get_selection_sessions()
        finally:
            store.close()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "sessions": []}
    for d in dates:
        sel, pub = radar_selection(d, out_dir), radar_publication(d, out_dir)
        sessions.append({"session_date": str(d), **{k: sel[k] for k in
                                                    ("selected_count", "selected_symbols")},
                         "published_count": pub["published_count"],
                         "published_symbols": pub["published_symbols"],
                         "selected_not_published": [s for s in sel["selected_symbols"]
                                                    if s not in pub["published_symbols"]],
                         "publication_status": pub["status"],
                         "confirmed_at": pub.get("confirmed_at"),
                         "artifact_path": pub.get("artifact_path"), "qa": pub.get("qa")})
    return {"sessions": sessions}


__all__ = ["confirm_radar_publication", "rendered_radar_stories", "radar_selection",
           "radar_publication", "published_instruments", "audit", "LEGACY_RENDERER_NOTE",
           "CONFIRMED", "ALREADY_CONFIRMED", "NOT_CONFIRMED", "NO_RADAR_STORIES",
           "DEMO_NOT_RECORDED", "LEDGER_ERROR"]
