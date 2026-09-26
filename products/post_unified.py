"""POST_UNIFIED - the ONE public POST product (production cut-over).

Scheduled production (`main.run`, reached from `python main.py [--upload]` through
`products.route`) and the manual / review renderer (`render_daily_market_byte.py`,
`render_public_intelligence_v1.py`) build the POST with these same functions. They differ only
in operational inputs (which report, whether to fetch the official lists, upload or not).

    canonical MarketReport + IntelligenceSnapshot
            │  plan_for_post            editorial.plan_short under the publication profile
            ▼
    PublicIntelligence                  presentation.public_intelligence.load_public_intelligence
            │  build_post_storyboard    daily_video.build_storyboard -> presentation.post_plan
            ▼                           (publication gate, claims, provenance - all inside)
    render_post                         daily_video.Composer: freeze-frame QA + silent MP4
            ▼
    post_metadata / audit_storyboard    title-description + publication_audit.json

The legacy `video.py` Short (scenes_from_plan / video.render) is LEGACY and NON-PRODUCTION:
nothing in the scheduled path reaches it.
"""
from __future__ import annotations

import datetime as dt
import json
import os

PRODUCT = "POST_UNIFIED"
PLANNER = ("editorial.plan_short -> daily_video.storyboard.build_storyboard -> "
           "presentation.post_plan.plan_post_sections (+ presentation.public_intelligence)")
RENDERER = "daily_video.composer.Composer"
DEMO_WATERMARK = "DEMO DATA - NOT REAL"


def plan_for_post(report, snapshot, now=None, profile=None):
    """The editorial plan the storyboard is built from (content-safety-checked hook candidates,
    the publication gate applied)."""
    import editorial
    from core.content_safety import SafetyStatus, classify_text
    return editorial.plan_short(report, snapshot, now=now, profile=profile,
                                is_safe=lambda s: classify_text(s).status is not SafetyStatus.BLOCKED)


def load_radar_inputs(radar_dir: str, session: str) -> tuple:
    """(radar_presentation, radar_result, visual_evidence, paths) for one session - the Radar
    artifacts the REPORT job wrote. Public output never shows a Radar story; PRIVATE_ANALYTICS
    renders do."""
    from radar.visual_evidence import build_visual_evidence_for_presentation
    pres_path = os.path.join(radar_dir, "presentation", f"radar_presentation_{session}.json")
    result_path = os.path.join(radar_dir, f"daily_radar_{session}.json")
    rp = json.load(open(pres_path, encoding="utf-8")) if os.path.exists(pres_path) else None
    rr = json.load(open(result_path, encoding="utf-8")) if os.path.exists(result_path) else None
    ev = build_visual_evidence_for_presentation(rp, rr) if rp and rr else {}
    return rp, rr, ev, {"radar_presentation": pres_path, "radar_result": result_path}


def build_post_storyboard(report, plan, *, profile=None, intelligence=None, hook_ai=False,
                          hook_client=None, radar_dir=None, sources=None):
    """The unified POST storyboard. The Radar artifacts are read for PRIVATE_ANALYTICS only in
    effect (the public gate refuses every Radar story)."""
    from daily_video import build_storyboard
    from presentation import ReportPresentation
    pres = ReportPresentation(report)
    session = (report.session_date or report.report_date).isoformat()
    rp = rr = None
    ev = {}
    src = dict(sources or {})
    if radar_dir:
        rp, rr, ev, paths = load_radar_inputs(radar_dir, session)
        src.update(paths)
    universe = (report.metadata or {}).get("universe") or "Nifty 100"
    kwargs = {"hook_client": hook_client} if hook_client is not None else {}
    sb = build_storyboard(plan, pres, rp, rr, ev, universe, src, hook_ai=hook_ai,
                          profile=profile, intelligence=intelligence, **kwargs)
    return sb, pres


def post_metadata(sb, pres, info) -> dict:
    """Title / description / tags built ONLY from what the storyboard shows: no stock names, no
    rankings, the standard disclaimer; each section's own SOURCE line."""
    from config import fmt_in
    from publication import disclaimer
    m = pres.m
    d = pres.session_date.strftime("%d %b")
    title = (f"Nifty {fmt_in(m['close'])} ({m['pct']:+.2f}%): What Changed in India's Market, "
             f"{d} #shorts")[:100]
    lines = [f"Daily Market Byte - Indian market recap for {info['recap_str']}.", "",
             f"Nifty 50: {fmt_in(m['close'], 2)} ({m['pct']:+.2f}%)"]
    srcs = []
    for s in sb.scenes:
        tx = s.texts or {}
        if s.kind == "SECTORS":
            lines.append("Sectors: " + " | ".join(f"{r['name']} {r['value']}" for r in tx["rows"]))
        elif s.kind == "FLOWS":
            lines.append(f"{s.headline} ({s.subline}): " +
                         " | ".join(f"{b['name']} {b['value']}" for b in tx.get("bars", [])))
        elif s.kind == "STRUCTURE":
            lines.append(f"Under the surface: {s.headline} ({tx.get('hero_value', '')} "
                         f"{tx.get('hero_label', '').lower()})")
        elif s.kind == "EXCHANGE_WATCH":
            lines.append(f"Exchange watch: {s.headline}")
        elif s.kind in ("IPO_WATCH", "IPO_BOARD"):
            lines.append(f"IPO watch: {s.headline}")
        prov = tx.get("provenance")
        if isinstance(prov, dict) and prov.get("source") not in srcs:
            srcs.append(prov.get("source"))
    lines += ["", "Sources on screen: " + "; ".join(s.title() for s in srcs if s) + ".",
              "", disclaimer.DESCRIPTION, "",
              "#stockmarket #nifty #sensex #sharemarket #indianstockmarket #dailymarketbyte #shorts"]
    tags = ["stock market", "nifty", "nifty 50", "sensex", "share market", "fii dii data",
            "sector performance", "stock market today", "indian stock market", "daily byte",
            "market recap"]
    return {"title": title, "description": "\n".join(lines), "tags": tags}


def render_post(sb, out_path: str, frames_dir: str, watermark: str | None = None) -> dict:
    """Freeze-frame QA (the layout gate: safe areas, overlaps, required marks, provenance) and
    the silent MP4. Returns {"frames_qa": {...}, "render": {...}}."""
    from daily_video import Composer
    comp = Composer(sb, watermark=watermark)
    names = [f"{i:02d}_{s.kind.lower()}" for i, s in enumerate(sb.scenes)]
    qa = comp.export_freeze_frames(frames_dir, names)
    render = comp.render(out_path)
    return {"frames_qa": {"passed": qa["passed"],
                          "issues": {e["scene"]: e["qa"]["issues"] for e in qa["scenes"]
                                     if not e["qa"]["passed"]}},
            "render": render}


def manifest(*, entry_point, report_source, report_id, session, sb, audit, upload_status,
             video_path, qa) -> dict:
    return {"product": PRODUCT, "entry_point": entry_point, "planner": PLANNER,
            "renderer": RENDERER, "legacy_renderer_invoked": False,
            "report_source": report_source, "report_id": report_id,
            "session_date": str(session), "publication_profile": sb.publication_profile,
            "scenes": [s.kind for s in sb.scenes],
            "sections": [s[1] or s[0] for s in sb.sections()],
            "duration": sb.total_duration, "video": video_path, "qa": qa,
            "optional_sections": (sb.public_audit or {}).get("omitted_sections", {}),
            "publication_audit": {"final": audit["final"],
                                  "failed_checks": audit["failed_checks"]},
            "rights_policy": audit.get("rights_policy"), "upload": upload_status,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}


__all__ = ["PRODUCT", "PLANNER", "RENDERER", "plan_for_post", "load_radar_inputs",
           "build_post_storyboard", "post_metadata", "render_post", "manifest",
           "DEMO_WATERMARK"]
