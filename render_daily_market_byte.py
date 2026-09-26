"""Render the unified Daily Market Byte video (main sections + Market Radar) from EXISTING,
already-validated artifacts. No network access: the report, the Radar artifacts and the local
OHLCV cache are all read from disk.

    python render_daily_market_byte.py                       # 2026-09-21 validation artifacts
    python render_daily_market_byte.py --frames-only         # freeze frames + QA, no MP4
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import intelligence
from config import OUT_DIR
from core import MarketReport
from core.content_safety import SafetyStatus, scan_publication
from daily_video import Composer
from daily_video.typography import font_report
from presentation import ReportPresentation
from products import post_unified as PU
from storage import MarketHistory, default_db_path

FREEZE_NAMES = {"HOOK": "hook", "DYNAMIC_HOOK": "hook", "PULSE": "market_overview", "NIFTY": "nifty", "FLOWS": "fii_dii",
                "SECTORS": "sectors", "MOVERS": "movers", "RADAR_INTRO": "radar_intro",
                "AHEAD": "look_ahead", "CLOSING": "closing"}


def load_report_and_plan(report_path, profile=None):
    """(report, plan) through the SAME planner the scheduled POST uses
    (products.post_unified.plan_for_post)."""
    report = MarketReport.from_json(open(report_path, encoding="utf-8").read())
    history = MarketHistory(default_db_path(OUT_DIR))
    try:
        snapshot = intelligence.build_snapshot(report, history)
    finally:
        history.close()
    return report, PU.plan_for_post(report, snapshot, now=report.generated_at, profile=profile)


def load_inputs(report_path, radar_dir, session: str):
    """Compatibility tuple for the historical phase renderers; built from the shared
    products.post_unified functions (no planner / Radar loading of its own)."""
    report, plan = load_report_and_plan(report_path)
    pres = ReportPresentation(report)
    radar_pres, radar_result, evidence, paths = PU.load_radar_inputs(radar_dir, session)
    universe = (report.metadata or {}).get("universe") or "Nifty 100"
    sources = {"market_report": report_path, **paths, "ohlcv_store": default_db_path(OUT_DIR)}
    return plan, pres, radar_pres, radar_result, evidence, universe, sources


def freeze_names(sb):
    out = []
    for s in sb.scenes:
        if s.kind == "RADAR_STORY":
            out.append("radar_" + s.texts["symbol"].lower())
        else:
            out.append(FREEZE_NAMES.get(s.kind, s.kind.lower()))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=os.path.join(OUT_DIR, "reports", "premarket_2026-09-23.json"))
    ap.add_argument("--radar-dir", default=os.path.join(OUT_DIR, "radar"))
    ap.add_argument("--out-dir", default=os.path.join(OUT_DIR, "daily_market_byte_redesign"))
    ap.add_argument("--frames-only", action="store_true")
    ap.add_argument("--out", default=None, help="MP4 path (default: output/"
                    "daily_market_byte_redesign_<session>.mp4)")
    ap.add_argument("--no-hook-ai", action="store_true",
                    help="open with the deterministic hook; never call Gemini")
    ap.add_argument("--profile", choices=("PUBLIC_UNREGISTERED", "PRIVATE_ANALYTICS"),
                    default=None, help="publication profile (default PUBLIC_UNREGISTERED: no Radar "
                                       "stock story, Market Structure / Exchange / IPO Watch instead)")
    ap.add_argument("--fetch-public", action="store_true",
                    help="fetch today's official exchange lists (F&O ban, ASM/GSM) and NSE IPO "
                         "lists; otherwise only stored artifacts are read")
    ap.add_argument("--confirm-publication", action="store_true",
                    help="after a successful render + QA, mark the Radar stories this MP4 shows "
                         "as PUBLISHED (products.radar_publication). Off by default: previews "
                         "and validation renders must never advance publication history")
    args = ap.parse_args(argv)

    from publication import resolve_profile
    profile = resolve_profile(args.profile)
    report, plan = load_report_and_plan(args.report, profile=profile)
    session = (report.session_date or report.report_date).isoformat()
    # Gemini chooses among approved hook candidates when a key is configured; any failure
    # falls back to the deterministic hook inside the engine.
    from operations.sessions import next_session
    from presentation.public_intelligence import load_public_intelligence
    list_date = next_session(report.session_date) or report.report_date
    intel = load_public_intelligence(report.session_date, list_date, OUT_DIR,
                                     fetch=args.fetch_public,
                                     now_iso=dt.datetime.now(dt.timezone.utc).isoformat())
    sb, pres = PU.build_post_storyboard(
        report, plan, profile=profile, intelligence=intel, hook_ai=not args.no_hook_ai,
        radar_dir=args.radar_dir,
        sources={"market_report": args.report, "ohlcv_store": default_db_path(OUT_DIR)})
    if sb.hook_plan:
        hp = sb.hook_plan
        print(f"hook: {hp['archetype']} via {hp['source']}"
              + (f" ({hp['fallback_reason']})" if hp["fallback_reason"] else "")
              + f" - \"{hp['curiosity_line']}\"")

    scan = scan_publication(sb.public_text())
    if scan.status is not SafetyStatus.SAFE:
        print("Content safety BLOCKED:", scan.blocked_fields)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, f"storyboard_{session}.json"), "w", encoding="utf-8") as fh:
        json.dump(sb.to_dict(), fh, indent=2, ensure_ascii=False, default=str)
    if sb.hook_plan:
        with open(os.path.join(args.out_dir, f"hook_plan_{session}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(sb.hook_plan, fh, indent=2, ensure_ascii=False, default=str)

    from daily_video.public_storyboard import audit_storyboard
    from publication import write_publication_audit
    comp = Composer(sb)
    names = freeze_names(sb)
    progressive = {}
    for name, spec in zip(names, sb.scenes):
        if spec.kind == "RADAR_STORY":
            progressive[name] = {"first_frame": 0.02, "chart_settled": 2.65}
    qa = comp.export_freeze_frames(os.path.join(args.out_dir, "freeze_frames"), names, progressive)
    print(f"storyboard: {len(sb.scenes)} scenes, {sb.total_duration:.1f}s | freeze-frame QA "
          f"{'PASS' if qa['passed'] else 'FAIL'}")
    for e in qa["scenes"]:
        if not e["qa"]["passed"]:
            print("  ", e["scene"], e["qa"]["issues"])
    if args.frames_only:
        audit = audit_storyboard(sb, "POST_UNIFIED")
        write_publication_audit(audit, args.out_dir)
        print(f"publication audit ({sb.publication_profile}): {audit['final']}"
              + (f" - failed {audit['failed_checks']}" if audit["failed_checks"] else ""))
        return 0

    out = args.out or os.path.join(OUT_DIR, f"daily_market_byte_redesign_{session}.mp4")
    result = comp.render(out)
    audit = audit_storyboard(sb, "POST_UNIFIED", video_path=out if result.get("ok") else None)
    audit_path = write_publication_audit(audit, args.out_dir)
    print(f"publication audit ({sb.publication_profile}): {audit['final']}"
          + (f" - failed {audit['failed_checks']}" if audit["failed_checks"] else ""))
    manifest = {"render": result, "session_date": session, "total_duration": sb.total_duration,
                "scene_count": len(sb.scenes), "sections": sb.sections(), "sources": sources,
                "omitted": sb.omitted, "fonts": font_report(), "hook_plan": sb.hook_plan,
                "content_safety": scan.status.value, "publication_profile": sb.publication_profile,
                "publication_audit": {"final": audit["final"], "path": audit_path,
                                      "failed_checks": audit["failed_checks"]},
                "rendered_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if args.confirm_publication:
        # RADAR_PUBLISHED: only the stories this completed artifact shows, only after QA.
        from products.radar_publication import confirm_radar_publication, rendered_radar_stories
        qa_ok = bool(result.get("ok")) and bool(qa.get("passed")) and             scan.status is SafetyStatus.SAFE
        manifest["radar_publication"] = confirm_radar_publication(
            report.session_date, rendered_radar_stories(sb), qa_passed=qa_ok, out_dir=OUT_DIR,
            artifact_path=out, qa={"render_ok": bool(result.get("ok")),
                                   "freeze_frame_qa": bool(qa.get("passed")),
                                   "content_safety": scan.status.value},
            confirmed_by="render_daily_market_byte.py --confirm-publication")
        rp = manifest["radar_publication"]
        print(f"radar publication: {rp['status']} {rp.get('published')}")
    with open(os.path.join(args.out_dir, f"render_manifest_{session}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, default=str)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
