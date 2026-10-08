"""POST_RENDER stage: did the RENDERED edition pass QA? Separate from PREFLIGHT ("may generation
start") on purpose - "safe to render" is never "the MP4 passed QA".

Reads only what the run already wrote (POST `production_manifest.json`; PRE
`shadow_manifest.json` + `pre_result_<D>.json`) and reuses `operations.daily_check`'s own
verdicts (`post_content_verdict`, `audit_verdict`). Nothing is re-probed or re-rendered.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from .checks import mk
from .evidence import post_manifest
from .models import FAIL, PASS, POST_RENDER, PUBLISH, WARN, ReadinessResult


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _qa_row(edition, cid, ok, msg_ok, msg_bad, **kw):
    return mk(edition, cid, "FINAL_QA", "PUBLICATION", PASS if ok else FAIL,
              msg_ok if ok else msg_bad, **kw)


def _audit_row(edition, intent, final, failed):
    from operations.daily_check import audit_verdict
    verdict = audit_verdict(final, failed)
    ok = verdict == "PASS" or (verdict == "RIGHTS_BLOCK_ONLY" and intent != PUBLISH)
    return mk(edition, "PUBLICATION_AUDIT", "FINAL_QA", "PUBLICATION", PASS if ok else FAIL,
              f"publication audit {final} -> {verdict}" +
              (" (expected in shadow - no upload)" if ok and verdict == "RIGHTS_BLOCK_ONLY" else ""),
              observed={"final": final, "failed_checks": failed, "verdict": verdict})


def _duration_row(edition, dur):
    from editorial.config import MAX_SHORT_DURATION, MIN_SHORT_DURATION
    ok = isinstance(dur, (int, float)) and MIN_SHORT_DURATION <= dur <= MAX_SHORT_DURATION
    return mk(edition, "DURATION", "FINAL_QA", "PUBLICATION", PASS if ok else FAIL,
              f"probed duration {dur}s" + ("" if ok else f" outside {MIN_SHORT_DURATION:.0f}-"
                                           f"{MAX_SHORT_DURATION:.0f}s"), observed=dur)


def evaluate_post_render(edition: str, session: dt.date, *, out_dir: str | None = None,
                         intent: str = "SHADOW") -> ReadinessResult:
    import config
    out_dir = out_dir or config.OUT_DIR
    res = ReadinessResult(edition=edition, stage=POST_RENDER, intent=intent, session_date=session)
    if edition == "POST":
        path, m = post_manifest(out_dir, session)
        if m is None:
            res.checks = [mk(edition, "ARTIFACT", "FINAL_QA", "PUBLICATION", FAIL,
                             f"no rendered POST manifest for {session}",
                             remediation="render first (scripts\\run_evening_full.bat)")]
        else:
            qa = m.get("qa") or {}
            pa = m.get("publication_audit") or {}
            res.checks = [
                mk(edition, "ARTIFACT", "FINAL_QA", "PUBLICATION", PASS,
                   f"manifest {path} ({m.get('report_source')})", source=path),
                _qa_row(edition, "VIDEO_QA", qa.get("video_qa") == "PASS", "video QA PASS",
                        f"video QA {qa.get('video_qa')}: {qa.get('video_qa_blocking')}"),
                _qa_row(edition, "FRAMES_QA", (qa.get("frames_qa") or {}).get("passed"),
                        "freeze-frame QA passed",
                        f"freeze-frame QA failed: {(qa.get('frames_qa') or {}).get('issues')}"),
                _qa_row(edition, "CONTENT_QA", qa.get("content_qa") == "SAFE",
                        "final content scan SAFE", f"final content scan {qa.get('content_qa')}"),
                _audit_row(edition, intent, pa.get("final"), pa.get("failed_checks")),
                _duration_row(edition, m.get("duration"))]
            res.edition_date = None
        return res
    run_dir = os.path.join(out_dir, "pre_shadow", session.isoformat())
    m = _load(os.path.join(run_dir, "shadow_manifest.json"))
    if m is None:
        res.checks = [mk(edition, "ARTIFACT", "FINAL_QA", "PUBLICATION", FAIL,
                         f"no PRE shadow manifest for {session}",
                         remediation="render first (scripts\\run_morning_pre.bat)")]
        return res
    qa = m.get("qa") or {}
    result = _load(os.path.join(run_dir, f"pre_result_{session}.json")) or {}
    pa = result.get("publication_audit") or {}
    run_status = m.get("run_status")
    res.checks = [
        mk(edition, "ARTIFACT", "FINAL_QA", "PUBLICATION",
           PASS if run_status == "SUCCESS" else (WARN if run_status == "DEGRADED" else FAIL),
           f"PRE run {run_status} ({m.get('stage')})" +
           (f": {m.get('failure_reason')}" if m.get("failure_reason") else ""),
           observed={"run_status": run_status, "degradations": m.get("degradations")}),
        _qa_row(edition, "VIDEO_QA", bool(qa.get("ok")), "render + probe OK",
                f"render / probe failed (blocked: {qa.get('blocked')})"),
        _qa_row(edition, "FRAMES_QA", bool(qa.get("freeze_frame_qa")), "freeze-frame QA passed",
                "freeze-frame QA failed"),
        _qa_row(edition, "CONTENT_QA", qa.get("content_safety") == "SAFE" and
                not qa.get("language_issues"), "content + language SAFE",
                f"content {qa.get('content_safety')}, language {qa.get('language_issues')}"),
        _audit_row(edition, intent, pa.get("final"), pa.get("failed_checks")),
        _duration_row(edition, qa.get("probed_duration"))]
    return res


__all__ = ["evaluate_post_render"]
