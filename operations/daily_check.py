"""Operator summary for the manual daily runs - READ-ONLY (it reads artifacts the pipeline
already wrote; it acquires nothing, renders nothing, uploads nothing, changes no state).

    python -m operations.daily_check setup      is this machine ready? (no secret values shown)
    python -m operations.daily_check evening    the latest REPORT job
    python -m operations.daily_check pre        the latest PRE shadow morning
    python -m operations.daily_check post       the latest POST run (+ writes its contact sheet)
    python -m operations.daily_check all        evening + pre + post

Each prints the session, where every artifact is, and a one-line VERDICT. Exit code: 0 = fine
to review, 2 = STOP (a content / QA / data failure the operator must look at), 1 = nothing to
check. During the shadow trial a publication audit that fails ONLY `publication_rights` is the
expected RIGHTS BLOCK (docs/USER_GUIDE.md) - it is reported separately from a content failure.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import sys

OK, STOP, NOTHING = 0, 2, 1
RIGHTS_ONLY = {"publication_rights"}


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _latest(pattern: str) -> str | None:
    paths = glob.glob(pattern)
    return max(paths, key=os.path.getmtime) if paths else None


def _rel(path, out_dir):
    if not path:
        return "-"
    try:
        return os.path.relpath(path, os.path.dirname(out_dir))
    except ValueError:
        return path


def _line(label, value):
    print(f"  {label:<28} {value}")


def audit_verdict(final: str | None, failed_checks) -> str:
    """CONTENT_FAILURE / RIGHTS_BLOCK_ONLY / PASS / UNKNOWN - the distinction the operator needs."""
    failed = set(failed_checks or [])
    if final == "PASS" and not failed:
        return "PASS"
    if failed and failed <= RIGHTS_ONLY:
        return "RIGHTS_BLOCK_ONLY"
    if failed:
        return "CONTENT_FAILURE"
    return "UNKNOWN"


# --------------------------------------------------------------------------- setup
def check_setup(out_dir: str) -> int:
    import importlib
    import platform
    print("SETUP")
    _line("python", f"{platform.python_version()} ({sys.executable})")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    _line("virtual environment", "active" if in_venv else "NOT active - activate venv first")
    missing = []
    for mod in ("yfinance", "pandas", "numpy", "matplotlib", "PIL", "requests", "dotenv",
                "imageio_ffmpeg"):
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(mod)
    _line("required packages", "OK" if not missing else f"MISSING: {', '.join(missing)}")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _line(".env file", "present" if os.path.exists(os.path.join(root, ".env")) else
          "absent (optional - Gemini features off)")
    import config  # noqa: F401  (loads .env exactly as the pipeline does)
    _line("GEMINI_API_KEY", "set (value hidden)" if os.getenv("GEMINI_API_KEY") else
          "not set (optional: deterministic hook, no AI facts)")
    _line("rights policy", os.getenv("PUBLIC_REVIEW_REQUIRED_POLICY") or "BLOCK (default)")
    _line("GIFT Nifty display", "ENABLED" if os.getenv("GIFT_NIFTY_PUBLICATION_ENABLED", "")
          .lower() == "true" else "off (default)")
    _line("state backend", os.getenv("DMB_STATE_BACKEND") or "local (default)")
    _line("output directory", out_dir)
    db = os.path.join(out_dir, "data", "market_history.db")
    _line("history database", "present" if os.path.exists(db) else "absent (first run creates it)")
    for f in ("token.json",):
        _line(f, "present (NOT used by the manual scripts)" if os.path.exists(os.path.join(root, f))
              else "absent (fine - the manual scripts never upload)")
    try:
        import imageio_ffmpeg
        _line("ffmpeg", "OK" if os.path.exists(imageio_ffmpeg.get_ffmpeg_exe()) else "MISSING")
    except Exception:
        _line("ffmpeg", "MISSING (pip install -r requirements.txt)")
    ready = not missing
    print(f"VERDICT: {'READY' if ready else 'NOT READY - install requirements'}")
    return OK if ready else STOP


# --------------------------------------------------------------------------- evening
def check_evening(out_dir: str) -> int:
    path = _latest(os.path.join(out_dir, "report_jobs", "*", "report_job_*.json"))
    print("EVENING - REPORT job")
    if not path:
        print("  no REPORT job record found (output/report_jobs/<session>/)")
        return NOTHING
    rec = _load(path) or {}
    det = rec.get("details") or {}
    session = rec.get("target_session")
    _line("session", session)
    _line("run status", rec.get("run_status"))
    _line("report", f"{det.get('report_id') or rec.get('report_id')} ({det.get('report_source')})")
    _line("canonical report", _rel(det.get("report_path") or rec.get("artifact_path"), out_dir))
    readiness = det.get("readiness") or {}
    _line("publication ready", readiness.get("publication_ready"))
    if readiness.get("blocking_issues"):
        _line("blocking issues", "; ".join(readiness["blocking_issues"]))
    if readiness.get("movers_coverage_pct") is not None:
        _line("movers coverage", f"{readiness['movers_coverage_pct']}%")
    if readiness.get("fallbacks"):
        _line("index fallbacks", "; ".join(readiness["fallbacks"]))
    radar = det.get("radar") or {}
    _line("private Radar", f"{radar.get('status')} ({radar.get('pipeline_status') or '-'})"
          + (f", {radar['stories']} stories" if radar.get("stories") is not None else ""))
    ms_path = det.get("market_structure_snapshot") or (
        os.path.join(out_dir, "market_structure", f"market_structure_{session}.json")
        if session else None)
    ms = _load(ms_path) if ms_path and os.path.exists(ms_path) else None
    if ms:
        snap = ms["snapshot"]
        _line("Market Structure", f"{snap['universe_label']} · {snap['constituent_count']} "
                                  f"constituents -> {_rel(ms_path, out_dir)}")
        for k, m in snap["metrics"].items():
            _line(f"  {k}", f"{m['numerator']} / {m['denominator']} "
                            f"(coverage {m['coverage_pct']}%, {m['status']})")
    else:
        _line("Market Structure", "NOT FOUND for this session")
    manifest_path = det.get("snapshot_manifest_path") or (
        os.path.join(out_dir, "official_snapshots", session, "official_snapshot_manifest.json")
        if session else None)
    man = _load(manifest_path) if manifest_path and os.path.exists(manifest_path) else None
    _line("official snapshots", f"{det.get('official_snapshot_status')} -> "
                                f"{_rel(manifest_path, out_dir) if man else 'no manifest'}")
    for kind, e in ((man or {}).get("snapshots") or {}).items():
        _line(f"  {kind}", f"{e.get('status')} | connectivity {e.get('connectivity_status')} | "
                           f"{e.get('record_count')} records | list date {e.get('source_date')}")
    _line("state store", f"{det.get('state_store_backend')} persisted="
                         f"{det.get('persisted_to_state_store')}")
    for d in det.get("degradations") or []:
        _line("DEGRADED", f"{d.get('source')}: {d.get('status')} {d.get('reason') or ''}"[:160])
    _line("run record", _rel(path, out_dir))
    status = rec.get("run_status")
    verdict = {"SUCCESS": "ACCEPT", "DEGRADED": "ACCEPT WITH NOTES (see DEGRADED lines)"}.get(
        status, "STOP - see docs/USER_GUIDE.md section 6")
    print(f"VERDICT: {verdict}")
    return OK if status in ("SUCCESS", "DEGRADED") else STOP


# --------------------------------------------------------------------------- PRE
def check_pre(out_dir: str) -> int:
    path = _latest(os.path.join(out_dir, "pre_shadow", "*", "shadow_manifest.json"))
    print("MORNING - PRE (shadow)")
    if not path:
        print("  no PRE shadow manifest found (output/pre_shadow/<date>/)")
        return NOTHING
    m = _load(path) or {}
    run_dir = os.path.dirname(path)
    d = m.get("pre_date")
    _line("PRE date / previous session", f"{d} / {m.get('previous_session')}")
    _line("run status", f"{m.get('run_status')} ({m.get('stage')})")
    if m.get("failure_reason"):
        _line("failure", m["failure_reason"][:200])
    qa = m.get("qa") or {}
    _line("QA", f"ok={qa.get('ok')} freeze-frame={qa.get('freeze_frame_qa')} "
                f"content={qa.get('content_safety')} duration={qa.get('probed_duration')}s")
    if qa.get("language_issues"):
        _line("language issues", qa["language_issues"])
    result = _load(os.path.join(run_dir, f"pre_result_{d}.json")) or {}
    pa = result.get("publication_audit") or {}
    verdict_audit = audit_verdict(pa.get("final"), pa.get("failed_checks"))
    _line("publication audit", f"{pa.get('final')} {pa.get('failed_checks') or ''} -> "
                               f"{verdict_audit}")
    arts = m.get("artifacts") or {}
    _line("video", _rel(arts.get("mp4"), out_dir))
    _line("contact sheet", _rel(arts.get("contact_sheet"), out_dir))
    _line("audit file", _rel(pa.get("path") or os.path.join(run_dir, "publication_audit.json"),
                             out_dir))
    _line("manifest", _rel(path, out_dir))
    for x in m.get("degradations") or []:
        _line("DEGRADED", str(x)[:160])
    content_ok = (m.get("run_status") in ("SUCCESS", "DEGRADED") and qa.get("ok")
                  and verdict_audit in ("PASS", "RIGHTS_BLOCK_ONLY"))
    print("VERDICT: " + ("REVIEW THE VIDEO (content checks passed"
                         + ("; rights BLOCK is expected in the trial)" if
                            verdict_audit == "RIGHTS_BLOCK_ONLY" else ")")
                         if content_ok else "STOP - content / QA / data failure"))
    return OK if content_ok else STOP


# --------------------------------------------------------------------------- POST
def post_contact_sheet(run_dir: str) -> str | None:
    """A contact sheet of the POST's freeze frames (the frames QA already rendered)."""
    frames = sorted(glob.glob(os.path.join(run_dir, "freeze_frames", "*.png")))
    if not frames:
        return None
    from products.premarket import _contact_sheet
    return _contact_sheet(frames, os.path.join(run_dir, "post_contact_sheet.png"))


def check_post(out_dir: str) -> int:
    path = _latest(os.path.join(out_dir, "post", "*", "production_manifest.json"))
    print("MORNING - POST (POST_UNIFIED)")
    if not path:
        print("  no POST manifest found (output/post/<date>/)")
        return NOTHING
    m = _load(path) or {}
    run_dir = os.path.dirname(path)
    _line("session", f"{m.get('session_date')} (run folder {os.path.basename(run_dir)})")
    _line("product / profile", f"{m.get('product')} / {m.get('publication_profile')}")
    _line("report", f"{m.get('report_id')} ({m.get('report_source')})")
    _line("scenes", " > ".join(m.get("scenes") or []))
    _line("duration", f"{m.get('duration')}s")
    qa = m.get("qa") or {}
    frames_ok = (qa.get("frames_qa") or {}).get("passed")
    _line("QA", f"video={qa.get('video_qa')} freeze-frame={frames_ok} content={qa.get('content_qa')}")
    for k, v in (m.get("optional_sections") or {}).items():
        _line(f"  {k}", f"{v.get('code')} (input {v.get('input_source')}, snapshot "
                        f"{v.get('snapshot_status')}, connectivity {v.get('connectivity_status')})")
    pa = m.get("publication_audit") or {}
    verdict_audit = audit_verdict(pa.get("final"), pa.get("failed_checks"))
    _line("publication audit", f"{pa.get('final')} {pa.get('failed_checks') or ''} -> "
                               f"{verdict_audit}")
    _line("upload", m.get("upload"))
    _line("video", _rel(m.get("video"), out_dir))
    sheet = post_contact_sheet(run_dir)
    _line("contact sheet", _rel(sheet, out_dir))
    tag = os.path.basename(run_dir)
    _line("audit file", _rel(os.path.join(out_dir, "publication", tag, "publication_audit.json"),
                             out_dir))
    _line("manifest", _rel(path, out_dir))
    content_ok = (qa.get("video_qa") == "PASS" and frames_ok and qa.get("content_qa") == "SAFE"
                  and verdict_audit in ("PASS", "RIGHTS_BLOCK_ONLY"))
    print("VERDICT: " + ("REVIEW THE VIDEO (content checks passed"
                         + ("; rights BLOCK is expected in the trial)" if
                            verdict_audit == "RIGHTS_BLOCK_ONLY" else ")")
                         if content_ok else "STOP - content / QA / data failure"))
    return OK if content_ok else STOP


def main(argv=None) -> int:
    import config
    argv = list(sys.argv[1:] if argv is None else argv)
    what = argv[0] if argv else "all"
    out_dir = config.OUT_DIR
    print(f"Daily Market Byte - {what} check at {dt.datetime.now():%Y-%m-%d %H:%M} "
          f"(output: {out_dir})")
    fns = {"setup": check_setup, "evening": check_evening, "pre": check_pre, "post": check_post}
    if what == "all":
        codes = []
        for k in ("evening", "pre", "post"):
            codes.append(fns[k](out_dir))
            print()
        return STOP if STOP in codes else OK
    if what not in fns:
        print(f"unknown check {what!r}: use setup | evening | pre | post | all")
        return NOTHING
    return fns[what](out_dir)


if __name__ == "__main__":
    sys.exit(main())
