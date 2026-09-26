"""PRE-MARKET run history: every PRE run is a row in the SAME operational history as POST runs
(`MarketHistory.publication_runs`, schema v2) - not a second system.

    mode         PRE_MARKET (PRE_MARKET_DEMO for --demo; excluded when querying PRE_MARKET)
    job_type     PRE_MARKET (schema v3 - never confused with a POST or REPORT_BUILD run)
    target_date  the session about to open
    source_session_date  the previous canonical session whose report PRE reads
    started_at / completed_at
    run_status   SUCCESS  rendered, every gate passed, every expected source fresh
                 DEGRADED rendered, every gate passed, but an optional source was missing,
                          stale or rejected (GIFT, a global cue, VIX, the event file, the
                          Gemini hook, an AI-only fact dropped) - each reason recorded
                 BLOCKED  refused by a rule (no canonical previous session, content/language/
                          provenance/runtime gate) - nothing wrong with the code, the Short
                          would not have been honest
                 FAILED   an error or a failed render/QA
                 SKIPPED  the PRE date is not an NSE session (nothing to preview)
    stage, data/content/video QA, artifact_path, failure_stage/failure_reason (blocking reason)
    details_json section plan (order, reasons, omitted), source freshness summary, GIFT
                 provenance, event-calendar audit, degradations, hook status, output paths,
                 previous-session report id/path, provenance audit verdict

`publication_status` is NOT_ATTEMPTED (PRE V1 never uploads) or SHADOW for a `--shadow` run. `report_id` stays NULL -
a PRE run describes no canonical report of its own (the previous session's report it READ is
in details), so querying a POST report's runs never returns PRE runs.

Recording is never fatal: PRE publishes nothing, so a history failure is logged into the run's
own JSON (`pre_run_<date>.json`, written alongside the artifacts) instead of blocking.
"""
from __future__ import annotations

import datetime as dt
import json
import os

PRE_RUN_MODE = "PRE_MARKET"
PRE_DEMO_MODE = "PRE_MARKET_DEMO"
RUN_HISTORY_VERSION = "pre-runs-1.0"

SUCCESS, DEGRADED, BLOCKED, FAILED = "SUCCESS", "DEGRADED", "BLOCKED", "FAILED"
SKIPPED = "SKIPPED"
# A GIFT reading withheld/not fetched by the publication policy is a decision, not a source
# failure - it never makes a run DEGRADED.
POLICY_GIFT_STATUSES = frozenset({"POLICY_DISABLED"})


# --------------------------------------------------------------------------- pure summaries
def freshness_summary(acq) -> list:
    """One line per acquired reading: what it is, its freshness and why."""
    if acq is None:
        return []
    out = []
    for q in acq.global_cues:
        out.append({"source": q.name, "kind": q.kind.value, "provider": q.source,
                    "freshness": q.freshness.value, "validation": q.validation_status,
                    "market_date": q.market_date.isoformat() if q.market_date else None,
                    "market_timestamp": q.market_timestamp.isoformat() if q.market_timestamp else None,
                    "usable": q.fresh, "reason": q.freshness_reason})
    g = acq.gift
    gs = dict(acq.gift_status or {})
    out.append({"source": "GIFT NIFTY", "kind": "LIVE", "provider": g.source if g else None,
                "freshness": g.freshness.value if g else None,
                "validation": g.validation_status if g else None,
                "market_timestamp": g.market_timestamp.isoformat() if g and g.market_timestamp else None,
                "usable": bool(g and g.fresh), "status": gs.get("status", "NO_SOURCE"),
                "reason": gs.get("reason", "")})
    v = acq.vix
    out.append({"source": "INDIA VIX", "kind": "SESSION_CLOSE", "provider": v.source if v else None,
                "freshness": v.freshness.value if v else None,
                "validation": v.validation_status if v else None,
                "market_date": v.session.isoformat() if v else None,
                "usable": bool(v and v.fresh),
                "reason": v.freshness_reason if v else "no VIX bar for the previous session"})
    return out


def degradations(acq, brief=None, events_audit=None, hook=None, hook_ai: bool = False,
                 expected_cues=None) -> list:
    """Every expected input that did not arrive usable, with the exact reason. Empty = the
    run had everything it looks for."""
    from providers.premarket import GLOBAL_UNIVERSE
    out = []
    if acq is not None:
        got = {q.key for q in acq.global_cues}
        for key, name, _t, _r in (expected_cues or GLOBAL_UNIVERSE):
            if key not in got:
                why = next((line for line in acq.log if line.startswith(f"{name}:")),
                           f"{name}: not acquired")
                out.append({"source": name, "status": "UNAVAILABLE", "reason": why})
        for q in acq.global_cues:
            if not q.fresh:
                out.append({"source": q.name, "status": q.freshness.value,
                            "reason": q.freshness_reason})
        gs = acq.gift_status or {"status": "NO_SOURCE", "reason": "no GIFT source wired"}
        if not (acq.gift and acq.gift.fresh) and gs.get("status") not in POLICY_GIFT_STATUSES:
            out.append({"source": "GIFT NIFTY", "status": gs.get("status"),
                        "reason": gs.get("reason")})
        if acq.vix is None or not acq.vix.fresh:
            out.append({"source": "INDIA VIX",
                        "status": acq.vix.freshness.value if acq.vix else "UNAVAILABLE",
                        "reason": acq.vix.freshness_reason if acq.vix else
                        "no VIX bar for the previous session"})
    if events_audit is not None:
        f = events_audit.get("official_file") or {}
        if f.get("status") != "OK":
            out.append({"source": "OFFICIAL EVENT FILE", "status": f.get("status"),
                        "reason": f.get("error") or f"rejected entries: {f.get('rejected')}"})
    if brief is not None:
        for note in brief.notes:
            if "backed only by an AI source" in note:
                out.append({"source": "PROVENANCE", "status": "AI_ONLY_DROPPED", "reason": note})
    if hook_ai and hook is not None and hook.get("source") != "GEMINI":
        out.append({"source": "GEMINI HOOK", "status": "FALLBACK",
                    "reason": hook.get("fallback_reason") or "deterministic hook used"})
    return out


def classify(result: dict | None, degr: list, blocked: str | None = None,
             error: str | None = None) -> str:
    if error:
        return FAILED
    if blocked or (result or {}).get("blocked"):
        return BLOCKED
    if not (result or {}).get("ok"):
        return FAILED
    return DEGRADED if degr else SUCCESS


# --------------------------------------------------------------------------- recorder
class PreRunRecorder:
    """Start at the top of a PRE run, finish exactly once (success, blocked or failed)."""

    def __init__(self, db_path: str | None = None, mode: str = PRE_RUN_MODE, shadow: bool = False):
        from config import OUT_DIR
        from storage import MarketHistory, default_db_path
        self.mode = mode
        self.shadow = bool(shadow)
        self.publication_status = "SHADOW" if self.shadow else "NOT_ATTEMPTED"
        self.db_path = db_path or default_db_path(OUT_DIR)
        self.history, self.run_id, self.error = None, None, None
        self.started_at = dt.datetime.now(dt.timezone.utc)
        self.record: dict = {}
        try:
            self.history = MarketHistory(self.db_path)
        except Exception as exc:
            self.error = f"history unavailable: {type(exc).__name__}: {exc}"

    def start(self, pre_date: dt.date, as_of: dt.datetime, request: dict | None = None,
              source_session: dt.date | None = None) -> str | None:
        from storage import JOB_PRE_MARKET
        self.record = {"version": RUN_HISTORY_VERSION, "mode": self.mode,
                       "job_type": JOB_PRE_MARKET, "shadow": self.shadow,
                       "publication_status": self.publication_status,
                       "target_date": pre_date.isoformat(),
                       "source_session_date": source_session.isoformat() if source_session else None,
                       "as_of": as_of.isoformat(),
                       "started_at": self.started_at.isoformat(), "request": request or {}}
        if self.history is not None:
            try:
                self.run_id = self.history.start_run(self.mode, target_date=pre_date,
                                                     stage="STARTED", job_type=JOB_PRE_MARKET,
                                                     source_session_date=source_session)
            except Exception as exc:
                self.error = f"could not start run: {type(exc).__name__}: {exc}"
        self.record["run_id"] = self.run_id
        return self.run_id

    def _finish(self, run_status: str, stage: str, out_dir: str | None = None, **fields) -> dict:
        details = fields.pop("details", {})
        self.record.update(run_status=run_status, stage=stage,
                           completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                           **{k: v for k, v in fields.items()}, details=details)
        if self.history is not None and self.run_id:
            try:
                self.history.finish_run(self.run_id, stage, self.publication_status,
                                        run_status=run_status, details=details, **fields)
            except Exception as exc:
                self.error = f"could not finish run: {type(exc).__name__}: {exc}"
        self.record["history_db"] = self.db_path
        self.record["history_error"] = self.error
        if out_dir:
            try:
                os.makedirs(out_dir, exist_ok=True)
                path = os.path.join(out_dir, f"pre_run_{self.record['target_date']}.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(self.record, fh, indent=2, ensure_ascii=False, default=str)
                self.record["record_path"] = path
            except OSError as exc:
                self.record["record_error"] = str(exc)
        if self.history is not None:
            self.history.close()
            self.history = None
        return self.record

    def blocked(self, code: str, message: str, out_dir: str | None = None,
                details: dict | None = None) -> dict:
        return self._finish(BLOCKED, "BRIEF_BLOCKED", out_dir, data_qa_status="BLOCKED",
                            failure_stage=code, failure_reason=message,
                            details=dict(details or {}, blocking={"code": code, "message": message}))

    def skipped(self, code: str, message: str, out_dir: str | None = None,
                details: dict | None = None) -> dict:
        return self._finish(SKIPPED, "NOT_A_SESSION", out_dir, failure_stage=code,
                            failure_reason=message,
                            details=dict(details or {}, skipped={"code": code, "message": message}))

    def failed(self, exc: BaseException, stage: str, out_dir: str | None = None,
               details: dict | None = None) -> dict:
        return self._finish(FAILED, "FAILED", out_dir, failure_stage=stage,
                            failure_reason=f"{type(exc).__name__}: {exc}", details=details or {})

    def finished(self, result: dict, brief, acq=None, events_audit=None, provenance=None,
                 hook_ai: bool = False, out_dir: str | None = None, extra: dict | None = None) -> dict:
        degr = degradations(acq, brief, events_audit, result.get("hook"), hook_ai)
        status = classify(result, degr)
        blocked = result.get("blocked")
        qa = result.get("freeze_frame_qa") or {}
        if blocked:
            stage = "GATE_BLOCKED"
        elif result.get("video"):
            stage = "RENDERED"
        elif result.get("ok"):
            stage = "FRAMES_ONLY"
        else:
            stage = "RENDER_FAILED"
        video_qa = ("NOT_RUN" if blocked else
                    "PASSED" if result.get("ok") else "FAILED")
        content_ok = result.get("content_safety") == "SAFE" and not result.get("language_issues")
        plan = result.get("plan") or {}
        details = {
            "pre_date": brief.pre_date.isoformat(), "as_of": brief.as_of.isoformat(),
            "shadow": self.shadow, "publication_status": self.publication_status,
            "previous_session": brief.previous_session.isoformat(),
            "previous_report_id": (brief.sources or {}).get("market_report_id"),
            "previous_report_path": (brief.sources or {}).get("market_report"),
            "synthetic": brief.synthetic,
            "section_plan": {"order": result.get("order"), "reasons": plan.get("reasons"),
                             "omitted": plan.get("omitted"),
                             "total_duration": result.get("total_duration")},
            "freshness": freshness_summary(acq),
            "data_readiness": {"previous_report": (brief.sources or {}).get("market_report_id"),
                               "previous_session": brief.previous_session.isoformat(),
                               "degraded_sources": sorted({d["source"] for d in degr})},
            "gift": {"status": (acq.gift_status if acq else {}) or {},
                     "reading": acq.gift.to_dict() if acq and acq.gift else None},
            "events": events_audit, "degradations": degr,
            "omitted_sections": [k for k, v in (plan.get("reasons") or {}).items()
                                 if isinstance(v, str) and v.startswith("omitted")],
            "hook": dict(result.get("hook") or {}, gemini_requested=bool(hook_ai)),
            "outputs": {"out_dir": out_dir, "video": result.get("video"),
                        "contact_sheet": result.get("contact_sheet")},
            "qa": {"content_safety": result.get("content_safety"),
                   "language_issues": result.get("language_issues"),
                   "freeze_frame_qa": qa, "probed_duration": result.get("probed_duration")},
            "provenance": {"ok": (provenance or {}).get("ok"),
                           "ai_violations": (provenance or {}).get("ai_violations"),
                           "displayed": (provenance or {}).get("displayed")},
            "brief_notes": list(brief.notes),
            **(extra or {}),
        }
        return self._finish(
            status, stage, out_dir, data_qa_status="PASSED",
            source_session_date=brief.previous_session,
            content_qa_status="PASSED" if content_ok else "BLOCKED", video_qa_status=video_qa,
            artifact_path=result.get("video") or out_dir,
            failure_stage=("GATE" if blocked else None if result.get("ok") else "RENDER_OR_QA"),
            failure_reason=blocked or (None if result.get("ok") else "render or video QA failed"),
            details=details)


__all__ = ["PreRunRecorder", "freshness_summary", "degradations", "classify", "PRE_RUN_MODE",
           "PRE_DEMO_MODE", "SUCCESS", "DEGRADED", "BLOCKED", "FAILED", "SKIPPED",
           "RUN_HISTORY_VERSION", "POLICY_GIFT_STATUSES"]
