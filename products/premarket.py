"""PRE-MARKET product runner (V1): build, plan, render and QA the "before the bell" Short.

    canonical report of the previous session (MarketHistory -> JSON artifact, never rebuilt)
    + providers.premarket (pre-open readings, dated/timestamped)
    + core.event_calendar (verified schedules only)
    + the previous session's published Radar stories (move-guarded)
        -> presentation.pre_plan.build_pre_brief -> PreEditorialPlanner -> PreSectionPlan
        -> daily_video.pre_storyboard -> Composer -> MP4 + freeze frames + QA artifacts

PRE never writes canonical history: the previous session's report is read, not re-saved, and
the pre-open readings are written as this run's own JSON artifact. V1 does not upload - an
`--upload` request is refused (upload comes after human visual approval).

The previous-session report is the one the REPORT job built the evening before
(`python main.py --mode report`); PRE never builds it. It must describe EXACTLY the canonical
session before the PRE date and be publication-ready - otherwise PRE is BLOCKED (never an older
report). `--shadow` runs the whole live pipeline into output/pre_shadow/<date>/ with a manifest
and a GIFT audit, and can never upload. GIFT display is behind `operations.gift_policy`
(default OFF): publication mode does not even fetch it until display rights are approved.

Every PRE run is recorded in the operational run history (`products.pre_run_history`,
`publication_runs` mode PRE_MARKET) as SUCCESS / DEGRADED / BLOCKED / FAILED with its reasons.
GIFT Nifty comes from the exchange itself (`providers.gift_nifty`, NSE IX) and is shown only
when fresh and consistent; an unavailable GIFT is omitted and never blocks the Short.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import config
from config import EXPIRY_WEEKDAY, now_ist
from core.event_calendar import event_calendar_audit, scheduled_events_for
from core.trading_calendar import SessionCalendar
from presentation.pre_plan import (MAX_RUNTIME, PreMarketBlocked, build_pre_brief,
                                   plan_pre_sections, pre_language_issues)

RADAR_PUBLISH_LIMIT = 3          # the POST Short's published set - carried forward as-is
DEFAULT_CUTOFF = dt.time(7, 45)  # reconstructions of a past morning use this IST cutoff

SCENE_FOLDERS = {"DYNAMIC_HOOK": "hook", "PRE_OVERNIGHT": "global_scene",
                 "PULSE": "previous_session_scene", "NIFTY": "previous_session_scene",
                 "PRE_EVENT": "event_scene", "PRE_WATCH": "watch_open_scene",
                 "PRE_VIX": "vix_scene", "FLOWS": "flows_scene", "SECTORS": "sector_scene",
                 "PRE_STOCKS": "stock_watch_scene", "CLOSING": "closing"}


# --------------------------------------------------------------------------- inputs
def find_previous_report(prev: dt.date, db_path: str | None = None):
    """(MarketReport, path) for the canonical, non-demo report of session `prev`, or (None, None).
    Reads the stored JSON artifact - PRE never rebuilds or re-saves a report."""
    lookup = previous_report_lookup(prev, db_path)
    return (lookup.report, lookup.path) if lookup.found else (None, None)


def previous_report_lookup(prev: dt.date, db_path: str | None = None):
    from operations.report_lookup import find_canonical_report
    return find_canonical_report(prev, db_path=db_path or _default_db())


def _default_db() -> str:
    from storage import default_db_path
    return default_db_path(config.OUT_DIR)


_LOOKUP_BLOCK = {"MISSING": "PREVIOUS_SESSION_MISSING",
                 "ARTIFACT_MISSING": "PREVIOUS_REPORT_UNUSABLE",
                 "UNREADABLE": "PREVIOUS_REPORT_UNUSABLE",
                 "SESSION_MISMATCH": "PREVIOUS_REPORT_UNUSABLE",
                 "HISTORY_UNAVAILABLE": "PREVIOUS_REPORT_UNAVAILABLE"}


def require_previous_report(pre_date: dt.date, cal, db_path: str | None = None):
    """(prev_session, report, path) for the PRE date, or raise PreMarketBlocked.

    requested PRE date D -> expected prior session = the canonical calendar's previous session
    -> the stored report must describe exactly that session and be publication-ready. A missing,
    unusable or unfit report BLOCKS PRE; an older session's report is never substituted."""
    if cal.is_session(pre_date) is False:
        raise PreMarketBlocked("NOT_A_SESSION", f"{pre_date} is not an NSE trading session")
    prev = cal.previous_session(pre_date)
    if prev is None:
        raise PreMarketBlocked("NO_PREVIOUS_SESSION", f"no canonical session before {pre_date}")
    lookup = previous_report_lookup(prev, db_path)
    if not lookup.found:
        code = _LOOKUP_BLOCK.get(lookup.status, "PREVIOUS_SESSION_MISSING")
        msg = (f"no canonical report for the previous session {prev} - the REPORT job "
               "(`python main.py --mode report`) has not built it; PRE never describes an older "
               "session" if code == "PREVIOUS_SESSION_MISSING" else
               f"canonical report for {prev} is unusable ({lookup.status}): {lookup.reason}")
        raise PreMarketBlocked(code, msg)
    if lookup.report.session_date != prev:          # defence in depth - lookup checks this too
        raise PreMarketBlocked("PREVIOUS_SESSION_MISMATCH",
                               f"report describes {lookup.report.session_date}, expected {prev}")
    if not lookup.report.publication_ready:
        raise PreMarketBlocked(
            "PREVIOUS_REPORT_NOT_PUBLICATION_READY",
            f"the canonical report for {prev} ({lookup.report_id}) failed data validation: "
            + "; ".join(lookup.report.validation_summary.blocking_issues))
    return prev, lookup.report, lookup.path


def radar_artifact_dirs(prev: dt.date) -> list:
    return [os.path.join(config.OUT_DIR, "radar"),
            os.path.join(config.OUT_DIR, "benchmark_gap_recovery", prev.isoformat(), "radar")]


def published_radar_stories(prev: dt.date, prev_prev: dt.date | None, dirs=None,
                            published=None):
    """The previous session's PUBLISHED Radar stories - those a completed, QA-passed POST
    artifact actually showed (`products.radar_publication` ledger), in on-screen order. A story
    the Radar merely SELECTED is never "published": selected-but-not-shown (POST failed, or the
    POST renderer has no Radar section) gives an empty stock watch. The Radar artifacts supply
    the story models + chart evidence only. `published` (a list of instruments) overrides the
    ledger lookup (tests). No new selection."""
    from presentation.radar_guard import guard_radar_stories
    from radar.visual_evidence import build_visual_evidence_for_presentation
    if published is None:
        from products.radar_publication import published_instruments
        published, status = published_instruments(prev, config.OUT_DIR)
    else:
        status = "CONFIRMED" if published else "NONE_CONFIRMED"
    published = list(published)
    if not published:
        return [], {}, [], {"radar_publication": f"{status}: no Radar story for {prev} appeared "
                                                 "in a completed, QA-passed POST artifact"}
    for d in dirs or radar_artifact_dirs(prev):
        pres_path = os.path.join(d, "presentation", f"radar_presentation_{prev}.json")
        res_path = os.path.join(d, f"daily_radar_{prev}.json")
        if not (os.path.exists(pres_path) and os.path.exists(res_path)):
            continue
        rp = json.load(open(pres_path, encoding="utf-8"))
        rr = json.load(open(res_path, encoding="utf-8"))
        if rp.get("status") != "OK":
            return [], {}, [], {"radar_presentation": pres_path, "status": rp.get("status")}
        by_inst = {s["instrument"]: s for s in rr.get("stories") or []}
        stories = [(sc["story"], by_inst[sc["story"]["instrument"]])
                   for sc in rp.get("scenes") or []
                   if sc.get("role") == "STORY" and sc.get("story")
                   and sc["story"]["instrument"] in by_inst]
        evidence = build_visual_evidence_for_presentation(rp, rr)
        kept, audit = guard_radar_stories(stories, evidence, prev, prev_prev)
        rank = {inst: i for i, inst in enumerate(published)}
        kept = sorted((s for s in kept if s[0]["instrument"] in rank),
                      key=lambda s: rank[s[0]["instrument"]])
        return (kept[:RADAR_PUBLISH_LIMIT], evidence, audit,
                {"radar_presentation": pres_path, "radar_result": res_path,
                 "radar_publication": f"{status}: " + ", ".join(published)})
    return [], {}, [], {"radar_publication": f"{status}: " + ", ".join(published)
                        + " (Radar artifacts missing - story models unavailable)"}


def _gift_not_fetched(policy):
    def fn(pre_date, as_of):
        return None
    fn.not_fetched_reason = policy.reason
    return fn


def build_real_brief(pre_date: dt.date, as_of: dt.datetime, history_fn=None, gift_fn=None,
                     calendar=None, db_path: str | None = None, shadow: bool = False,
                     gift_policy=None):
    """(brief, acquisition) from REAL data. Raises PreMarketBlocked when the previous session
    cannot be described honestly. GIFT goes through the publication gate
    (`operations.gift_policy`): fetched only in shadow or once approved, displayed only once
    approved; the verdict is in `brief.gift_policy`."""
    from operations.gift_policy import gift_publication_policy, should_fetch
    from presentation.report_adapter import ReportPresentation
    from providers.premarket import fetch_premarket_quotes
    cal = calendar or SessionCalendar()
    prev, report, report_path = require_previous_report(pre_date, cal, db_path)
    pres_m = ReportPresentation(report).m
    report_vix = pres_m.get("vix")
    policy = gift_policy or gift_publication_policy()
    fetch_gift = should_fetch(policy, shadow)
    if not fetch_gift:
        gift_fn = _gift_not_fetched(policy)
    elif gift_fn is None:
        # the exchange that lists GIFT Nifty; the canonical close is only a plausibility bound
        from providers.gift_nifty import gift_fn_for
        gift_fn = gift_fn_for(nifty_close=pres_m.get("close"))
    acq = fetch_premarket_quotes(pre_date, as_of, prev, cal, report_vix=report_vix,
                                 history_fn=history_fn, gift_fn=gift_fn)
    if not fetch_gift:
        acq.gift_status = {"status": "POLICY_DISABLED",
                           "reason": f"not fetched - {policy.reason}"}
        acq.log.append(f"GIFT NIFTY: POLICY_DISABLED - not fetched ({policy.reason})")
    events = scheduled_events_for(pre_date, cal, EXPIRY_WEEKDAY)
    stories, evidence, audit, radar_src = published_radar_stories(prev, cal.previous_session(prev))
    brief = build_pre_brief(report, pre_date, as_of, cal, acq, events, stories, evidence, audit,
                            sources={"market_report": report_path, **radar_src,
                                     "global_cues": "yahoo_finance (dated bars / 5m intraday)",
                                     "gift_nifty": "nseix_market_rate + nseix_settlement_file",
                                     "events": "core.event_calendar + data/official_events.json"})
    brief.sources["market_report_id"] = report.report_id
    brief.gift_policy = {"fetched": fetch_gift, "publication_allowed": policy.publication_allowed,
                         "withheld": False, "reason": policy.reason, "shadow": bool(shadow)}
    if brief.gift is not None and not policy.publication_allowed:
        # Validated for engineering evaluation (shadow) but never shown: removed from the
        # brief so no scene, watch card or hook can display it.
        brief.gift = None
        brief.gift_policy["withheld"] = True
        brief.notes.append(f"GIFT Nifty withheld by publication policy: {policy.reason}")
    gen = getattr(report, "generated_at", None)
    if gen is not None and gen > as_of:
        brief.notes.append(f"RECONSTRUCTION: the previous-session report was generated {gen:%Y-%m-%d %H:%M} "
                           f"(after this cutoff); only its {prev} session facts are used")
    return brief, acq


# --------------------------------------------------------------------------- render + QA
def _contact_sheet(paths: list, out_path: str, cols: int = 4, width: int = 300, label=None):
    from PIL import Image, ImageDraw
    ims = [Image.open(p).convert("RGB") for p in paths if os.path.exists(p)]
    if not ims:
        return None
    h = int(ims[0].height * width / ims[0].width)
    rows = -(-len(ims) // cols)
    top = 60 if label else 0
    sheet = Image.new("RGB", (cols * width + (cols + 1) * 10, top + rows * h + (rows + 1) * 10),
                      (12, 14, 24))
    if label:
        from daily_video.typography import font
        ImageDraw.Draw(sheet).text((12, 14), label, font=font(28), fill=(255, 255, 255))
    for i, im in enumerate(ims):
        sheet.paste(im.resize((width, h)), (10 + (i % cols) * (width + 10),
                                            top + 10 + (i // cols) * (h + 10)))
    sheet.save(out_path)
    return out_path


def render_pre(brief, out_dir: str, watermark: str | None = None, frames_only: bool = False,
               hook_ai: bool = False, hook_client=None, acquisition=None) -> dict:
    """Plan -> storyboard -> language + content gates -> artifacts -> frames -> MP4 -> QA."""
    from core.content_safety import SafetyStatus, scan_publication
    from daily_video import Composer
    from daily_video.pre_storyboard import build_pre_storyboard
    from presentation.pre_provenance import pre_provenance_audit
    from qa.video_qa import probe_media

    os.makedirs(out_dir, exist_ok=True)
    tag = brief.pre_date.isoformat()
    plan = plan_pre_sections(brief)
    sb = build_pre_storyboard(brief, plan, hook_ai=hook_ai, hook_client=hook_client)
    public = sb.public_text()
    scan = scan_publication(public)
    language = pre_language_issues(public)
    result = {"pre_date": tag, "previous_session": brief.previous_session.isoformat(),
              "synthetic": brief.synthetic, "total_duration": sb.total_duration,
              "scenes": [(s.kind, s.section, s.duration) for s in sb.scenes],
              "order": plan.order, "content_safety": scan.status.value,
              "language_issues": language, "hook": None, "ok": False,
              "plan": {"reasons": plan.reasons, "omitted": plan.omitted}}
    provenance = pre_provenance_audit(brief, plan)
    result["provenance"] = {k: provenance[k] for k in ("ok", "ai_violations", "displayed")}
    result["gift_displayed"] = bool(plan.show_gift_nifty)
    if sb.hook_plan:
        hp = sb.hook_plan
        result["hook"] = {"archetype": hp["archetype"], "source": hp["source"],
                          "curiosity_line": hp["curiosity_line"], "summary_line": hp["summary_line"],
                          "fallback_reason": hp["fallback_reason"]}

    def dump(name, obj):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)

    dump(f"pre_section_plan_{tag}.json", plan.to_dict())
    dump(f"pre_brief_{tag}.json", brief.to_dict())
    dump(f"storyboard_{tag}.json", sb.to_dict())
    dump(f"pre_provenance_{tag}.json", provenance)
    if acquisition is not None:
        dump(f"pre_acquisition_{tag}.json", acquisition.to_dict())
    if sb.hook_plan:
        dump(f"hook_plan_{tag}.json", sb.hook_plan)

    if scan.status is not SafetyStatus.SAFE or language:
        result["blocked"] = "content/language gate: " + "; ".join(
            list(scan.blocked_fields or []) + language)
        dump(f"pre_result_{tag}.json", result)
        return result
    if not provenance["ok"]:
        result["blocked"] = "provenance gate: displayed fact(s) with AI provenance: " + ", ".join(
            str(r.get("fact")) for r in provenance["ai_violations"])
        dump(f"pre_result_{tag}.json", result)
        return result
    if sb.total_duration > MAX_RUNTIME:
        result["blocked"] = f"runtime {sb.total_duration:.1f}s over the {MAX_RUNTIME:.0f}s ceiling"
        dump(f"pre_result_{tag}.json", result)
        return result

    comp = Composer(sb, watermark=watermark)
    names = []
    seen = {}
    for s in sb.scenes:
        base = SCENE_FOLDERS.get(s.kind, s.kind.lower())
        seen[base] = seen.get(base, 0) + 1
        names.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    qa = comp.export_freeze_frames(os.path.join(out_dir, "freeze_frames"), names)
    # per-scene folders: the entry frame and the settled (freeze) frame
    for i, (name, spec) in enumerate(zip(names, sb.scenes)):
        folder = os.path.join(out_dir, name)
        os.makedirs(folder, exist_ok=True)
        img, _, _ = comp.freeze(i)
        img.convert("RGB").save(os.path.join(folder, "settled.png"))
        img2, _, _ = comp.freeze(i, min(0.7, spec.duration * 0.2))
        img2.convert("RGB").save(os.path.join(folder, "entry.png"))
    result["freeze_frame_qa"] = {"passed": qa["passed"],
                                 "issues": {e["scene"]: e["qa"]["issues"] for e in qa["scenes"]
                                            if not e["qa"]["passed"]}}
    label = (("SYNTHETIC - " if brief.synthetic else "REAL - ") +
             f"PRE {tag} (prev {brief.previous_session}) · {sb.total_duration:.1f}s")
    result["contact_sheet"] = _contact_sheet(
        [e["image"] for e in qa["scenes"]], os.path.join(out_dir, f"pre_contact_sheet_{tag}.png"),
        label=label)
    if frames_only:
        result["ok"] = qa["passed"]
        dump(f"pre_result_{tag}.json", result)
        return result
    out = os.path.join(out_dir, f"full_pre_{tag}.mp4")
    render = comp.render(out)
    probe = probe_media(out) if render["ok"] else {}
    dur = probe.get("duration")
    result.update(render=render, video=out if render["ok"] else None, probed_duration=dur,
                  probed_video=probe.get("video"))
    result["ok"] = bool(render["ok"] and qa["passed"] and dur is not None
                        and abs(dur - sb.total_duration) < 0.5 and dur <= MAX_RUNTIME)
    dump(f"pre_result_{tag}.json", result)
    return result


# --------------------------------------------------------------------------- product entry
def _request_dict(request) -> dict:
    return {"frames_only": bool(request.frames_only), "hook_ai": bool(request.hook_ai),
            "demo": bool(request.demo),
            "session_date": request.session_date.isoformat() if request.session_date else None,
            "as_of": request.as_of.isoformat() if request.as_of else None}


SHADOW_DIR = "pre_shadow"


def run_premarket(request, db_path: str | None = None, history_fn=None, gift_fn=None,
                  hook_client=None, run_db_path: str | None = None, gift_policy=None) -> dict | None:
    """`python main.py --mode premarket [--shadow] [--session-date D] [--as-of ISO] [--frames-only]`.
    Every run - rendered, degraded, blocked, skipped or failed - is recorded in the run history.
    `db_path` is the history the previous-session report is read from; `run_db_path` (default:
    the same database) is where the run is recorded."""
    run_db_path = run_db_path or db_path
    from operations.gift_policy import gift_audit, gift_publication_policy
    from .pre_run_history import PRE_DEMO_MODE, PRE_RUN_MODE, PreRunRecorder
    shadow = bool(getattr(request, "shadow", False))
    if request.upload:
        print("PRE-MARKET does not upload" + (" in shadow mode" if shadow else
              " (V1): upload follows human visual approval") + ". Nothing sent.")
        return None
    if shadow and request.demo:
        print("--shadow runs live data only; --demo is refused in shadow mode. Nothing run.")
        return None
    out_dir = request.out_dir or os.path.join(config.OUT_DIR, SHADOW_DIR if shadow else "premarket")
    if request.demo:
        from .pre_fixtures import synthetic_brief
        brief = synthetic_brief("QUIET")
        rec = PreRunRecorder(run_db_path, mode=PRE_DEMO_MODE)
        rec.start(brief.pre_date, brief.as_of, _request_dict(request))
        demo_dir = os.path.join(out_dir, "DEMO")
        res = render_pre(brief, demo_dir, watermark="SYNTHETIC DATA - NOT REAL",
                         frames_only=request.frames_only)
        res["run"] = rec.finished(res, brief, provenance=res.get("provenance"),
                                  out_dir=demo_dir)["run_status"]
        print(json.dumps({k: res.get(k) for k in ("ok", "video", "total_duration", "blocked", "run")},
                         indent=2))
        return res
    as_of = request.as_of or now_ist()
    if as_of.tzinfo is None:
        from core.freshness import IST
        as_of = as_of.replace(tzinfo=IST)
    pre_date = request.session_date or as_of.date()
    if request.session_date and request.as_of is None:
        from core.freshness import IST
        as_of = dt.datetime.combine(pre_date, DEFAULT_CUTOFF, tzinfo=IST)
    run_dir = os.path.join(out_dir, pre_date.isoformat())
    cal = SessionCalendar()
    policy = gift_policy or gift_publication_policy()
    rec = PreRunRecorder(run_db_path, mode=PRE_RUN_MODE, shadow=shadow)
    rec.start(pre_date, as_of, _request_dict(request), source_session=cal.previous_session(pre_date))
    events_audit = event_calendar_audit(pre_date, cal, EXPIRY_WEEKDAY)
    base_details = {"events": events_audit, "gift_policy": policy.to_dict()}
    try:
        brief, acq = build_real_brief(pre_date, as_of, history_fn=history_fn, gift_fn=gift_fn,
                                      calendar=cal, db_path=db_path, shadow=shadow,
                                      gift_policy=policy)
    except PreMarketBlocked as exc:
        print(f"PRE-MARKET blocked - {exc}")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"pre_blocked_{pre_date}.json"), "w", encoding="utf-8") as fh:
            json.dump({"pre_date": pre_date.isoformat(), "as_of": as_of.isoformat(),
                       "code": exc.code, "message": exc.message}, fh, indent=2)
        if exc.code == "NOT_A_SESSION":
            record = rec.skipped(exc.code, exc.message, out_dir=run_dir, details=base_details)
        else:
            record = rec.blocked(exc.code, exc.message, out_dir=run_dir, details=base_details)
        if shadow:
            write_shadow_manifest(run_dir, pre_date, record, None, None)
        return None
    except Exception as exc:
        record = rec.failed(exc, "ACQUISITION", out_dir=run_dir, details=base_details)
        if shadow:
            write_shadow_manifest(run_dir, pre_date, record, None, None)
        raise
    for line in acq.log:
        print("   ", line)
    try:
        res = render_pre(brief, run_dir, frames_only=request.frames_only,
                         hook_ai=request.hook_ai, hook_client=hook_client, acquisition=acq)
    except Exception as exc:
        record = rec.failed(exc, "RENDER", out_dir=run_dir, details=base_details)
        if shadow:
            write_shadow_manifest(run_dir, pre_date, record, None, None)
        raise
    audit = gift_audit(acq, policy, shadow=shadow,
                       fetched=bool((brief.gift_policy or {}).get("fetched")),
                       displayed=bool(res.get("gift_displayed")))
    _dump(run_dir, f"gift_audit_{pre_date}.json", audit)
    record = rec.finished(res, brief, acq, events_audit, res.get("provenance"),
                          hook_ai=request.hook_ai, out_dir=run_dir,
                          extra={"gift_audit": audit, "gift_policy": policy.to_dict()})
    res["run"] = {k: record.get(k) for k in ("run_id", "run_status", "stage", "record_path",
                                             "history_error")}
    res["degradations"] = record["details"]["degradations"]
    res["gift_audit"] = audit
    if shadow:
        res["shadow_manifest"] = write_shadow_manifest(run_dir, pre_date, record, res, audit)
    print(json.dumps({k: res.get(k) for k in ("ok", "video", "total_duration", "order", "blocked",
                                              "run")}, indent=2, default=str))
    return res


def _dump(folder: str, name: str, obj) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    return path


SHADOW_ARTIFACTS = {            # what every shadow morning retains (docs/PRODUCTION_SCHEDULE.md)
    "pre_plan": "pre_section_plan_{d}.json",
    "provenance": "pre_provenance_{d}.json",
    "gift_audit": "gift_audit_{d}.json",
    "run_record": "pre_run_{d}.json",
    "mp4": "full_pre_{d}.mp4",
    "qa_result": "pre_result_{d}.json",
    "brief": "pre_brief_{d}.json",
    "acquisition": "pre_acquisition_{d}.json",
    "contact_sheet": "pre_contact_sheet_{d}.png",
}


def write_shadow_manifest(run_dir: str, pre_date: dt.date, record: dict, result: dict | None,
                          audit: dict | None) -> str:
    """output/pre_shadow/<D>/shadow_manifest.json: one index per shadow morning - which
    artifacts exist, the run verdict, QA, omitted/degraded reasons and the GIFT verdict."""
    d = pre_date.isoformat()
    files = {k: (os.path.join(run_dir, v.format(d=d))
                 if os.path.exists(os.path.join(run_dir, v.format(d=d))) else None)
             for k, v in SHADOW_ARTIFACTS.items()}
    details = record.get("details") or {}
    res = result or {}
    manifest = {
        "shadow": True, "never_uploaded": True, "pre_date": d,
        "previous_session": details.get("previous_session"),
        "run_id": record.get("run_id"), "run_status": record.get("run_status"),
        "stage": record.get("stage"), "failure_stage": record.get("failure_stage"),
        "failure_reason": record.get("failure_reason"),
        "artifacts": files,
        "missing_artifacts": sorted(k for k, v in files.items() if v is None),
        "qa": {"ok": res.get("ok"), "blocked": res.get("blocked"),
               "content_safety": res.get("content_safety"),
               "language_issues": res.get("language_issues"),
               "freeze_frame_qa": (res.get("freeze_frame_qa") or {}).get("passed"),
               "probed_duration": res.get("probed_duration"),
               "total_duration": res.get("total_duration")},
        "omitted": (res.get("plan") or {}).get("omitted"),
        "degradations": details.get("degradations"),
        "gift": ({k: audit.get(k) for k in ("gift_data_available", "gift_data_valid",
                                              "gift_publication_allowed", "gift_displayed",
                                              "reason")} if audit else None),
    }
    return _dump(run_dir, "shadow_manifest.json", manifest)


__all__ = ["run_premarket", "render_pre", "build_real_brief", "find_previous_report",
           "require_previous_report", "published_radar_stories", "write_shadow_manifest",
           "RADAR_PUBLISH_LIMIT", "SCENE_FOLDERS", "SHADOW_DIR", "SHADOW_ARTIFACTS"]
