"""Phase 3 previews: the POST editorial plan, the new middle sections and the full video.

    python render_post_phase3.py                # everything
    python render_post_phase3.py --frames-only  # stills, plan and timeline only

Writes output/post_phase3/:
    post_section_plan_2026-09-21.json           the planner's decisions + reasons
    market_pulse/ sectors/                      contact_sheet.png + scene.mp4
    movers/                                     scene.mp4 if selected, else NOT_SELECTED.txt
    full_post_phase3.mp4                        the complete POST Short
    post_phase2_vs_phase3_timeline.txt          scene-by-scene before/after
    synthetic_optional_scenes.png               optional scenes on SYNTHETIC sessions (QA only)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace

from PIL import Image, ImageDraw

import render_daily_market_byte as r
from config import OUT_DIR
from core.content_safety import SafetyStatus, scan_publication
from daily_video import Composer, build_storyboard
from daily_video import storyboard as sbm
from daily_video.storyboard import Storyboard
from daily_video.typography import font

REPORT = os.path.join(OUT_DIR, "reports", "premarket_2026-09-23.json")
SESSION = "2026-09-21"
PHASE2_RECORD = os.path.join(OUT_DIR, "radar_phase2", "phase2_record.json")
SYNTH = "SYNTHETIC DATA - NOT REAL"


def _label(img, text):
    d = ImageDraw.Draw(img)
    d.rectangle((0, img.height - 34, img.width, img.height), fill=(0, 0, 0))
    d.text((10, img.height - 30), text, font=font(22), fill=(255, 255, 255))
    return img


def _sheet(images, path):
    sheet = Image.new("RGB", (540 * len(images), 960))
    for k, (im, cap) in enumerate(images):
        sheet.paste(_label(im.convert("RGB").resize((540, 960), Image.LANCZOS), cap), (k * 540, 0))
    sheet.save(path)


def _timeline(scenes):
    out, t = [], 0.0
    for kind, dur in scenes:
        out.append((t, t + dur, kind, dur))
        t += dur
    return out, round(t, 2)


def _synthetic_optional(out_path):
    """The optional scenes, rendered on synthetic sessions that make each one qualify."""
    import tests.test_post_phase3 as fx       # the same synthetic sessions the tests use
    from presentation.post_plan import plan_post_sections
    specs = []
    p1 = plan_post_sections(fx._pres(pct=0.4, cross="up"), fx._plan(), fx._stories())
    specs.append(("NIFTY chart story (MA cross)", sbm._structure_scene(p1.structure)))
    p2 = plan_post_sections(fx._pres(), fx._plan(gainers=(("STOCK-E", 6.2),),
                                                 losers=(("STOCK-F", -5.4),)), fx._stories())
    specs.append(("MOVERS", sbm._movers_scene(p2.movers)))
    p3 = plan_post_sections(fx._pres(pct=-1.6), fx._plan(globals_=(("NASDAQ", -2.4),
                                                                   ("DOW JONES", -1.1))), fx._stories())
    specs.append(("GLOBAL CONTEXT", sbm._global_scene(p3.global_context)))
    shots = []
    for label, spec in specs:
        sb = Storyboard(session_date=p1 and fx._pres().session_date, date_label="FRI 06 MAR 2026",
                        kicker="SESSION RECAP", scenes=[spec])
        img, _, qa = Composer(sb, watermark=SYNTH).freeze(0)
        shots.append((img, f"{label}  QA {'PASS' if qa['passed'] else 'FAIL'}"))
    _sheet(shots, out_path)
    return [c for c, _ in [(s[1], 0) for s in shots]]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(OUT_DIR, "post_phase3"))
    ap.add_argument("--frames-only", action="store_true")
    args = ap.parse_args(argv)
    od = args.out_dir
    os.makedirs(od, exist_ok=True)

    plan, pres, rp, rr, ev, uni, src = r.load_inputs(REPORT, os.path.join(OUT_DIR, "radar"), SESSION)
    sb = build_storyboard(plan, pres, rp, rr, ev, uni, src)
    comp = Composer(sb)
    scan = scan_publication(sb.public_text())

    with open(os.path.join(od, f"post_section_plan_{SESSION}.json"), "w", encoding="utf-8") as fh:
        json.dump({"session": SESSION, "post_plan": sb.post_plan, "omitted": sb.omitted,
                   "hook": {k: sb.hook_plan[k] for k in ("archetype", "curiosity_line",
                                                         "summary_line", "source")}},
                  fh, indent=2, ensure_ascii=False, default=str)

    starts = sb.starts()
    for kind, folder, times in (("PULSE", "market_pulse", (0.4, 1.0, 1.9, None)),
                                ("SECTORS", "sectors", (0.5, 0.9, 1.4, None)),
                                ("MOVERS", "movers", (0.6, 1.2, None))):
        d = os.path.join(od, folder)
        os.makedirs(d, exist_ok=True)
        idx = next((i for i, s in enumerate(sb.scenes) if s.kind == kind), None)
        if idx is None:
            with open(os.path.join(d, "NOT_SELECTED.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"{kind} was not selected by the POST planner for {SESSION}.\n"
                         f"Reason: {sb.post_plan['reasons'].get(kind, '')}\n")
            continue
        spec = sb.scenes[idx]
        shots = []
        for t in times:
            tt = spec.freeze["t"] if t is None else t
            img, _, qa = comp.freeze(idx, tt)
            shots.append((img, f"{kind} @ {tt:.2f}s" + (" (settled)" if t is None else "")))
        _sheet(shots, os.path.join(d, "contact_sheet.png"))
        shots[-1][0].convert("RGB").save(os.path.join(d, "settled.png"))
        if not args.frames_only:
            one = Storyboard(session_date=sb.session_date, date_label=sb.date_label,
                             kicker=sb.kicker, scenes=[spec])
            Composer(one).render(os.path.join(d, "scene.mp4"))

    # timeline before / after
    p2 = json.load(open(PHASE2_RECORD, encoding="utf-8"))["scenes"] if os.path.exists(PHASE2_RECORD) else []
    before, tb = _timeline([(k, d) for k, d in p2])
    after, ta = _timeline([(s.kind, s.duration) for s in sb.scenes])
    lines = [f"POST 2026-09-21 - Phase 2 vs Phase 3 timeline", "",
             f"PHASE 2  ({tb:.1f}s)", *[f"  {a:5.1f} - {b:5.1f}  {k:12s} {d:.1f}s" for a, b, k, d in before],
             "", f"PHASE 3  ({ta:.1f}s)",
             *[f"  {a:5.1f} - {b:5.1f}  {k:12s} {d:.1f}s" for a, b, k, d in after], "",
             f"Change: {ta - tb:+.1f}s", "", "Planner decisions:",
             *[f"  {k:8s} {v}" for k, v in sb.post_plan["reasons"].items()]]
    with open(os.path.join(od, "post_phase2_vs_phase3_timeline.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    _synthetic_optional(os.path.join(od, "synthetic_optional_scenes.png"))

    # a still for every scene of the full Short, in order - the storyboard at a glance
    _sheet([(comp.freeze(i)[0], f"{starts[i]:.1f}s {s.kind}") for i, s in enumerate(sb.scenes)],
           os.path.join(od, "storyboard_settled_frames.png"))

    result = None
    if not args.frames_only:
        result = comp.render(os.path.join(od, "full_post_phase3.mp4"))
    print(json.dumps({"total": sb.total_duration, "order": sb.post_plan["order"],
                      "content_safety": scan.status.value, "render": result}, ensure_ascii=False))
    print("\n".join(lines))
    return 0 if scan.status is SafetyStatus.SAFE else 1


if __name__ == "__main__":
    sys.exit(main())
