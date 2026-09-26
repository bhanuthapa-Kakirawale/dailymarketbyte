"""Phase 2 previews: the three published Market Radar stock stories + the full POST video.

    python render_radar_phase2.py                # everything (per-stock MP4s + full video)
    python render_radar_phase2.py --frames-only  # stills and contact sheets only

Reads only the repository's validated 2026-09-21 artifacts and the local OHLCV store (no
network). Writes to output/radar_phase2/:
    <symbol>/first_frame.png, chart_settled.png, final_settled.png, contact_sheet.png, scene.mp4
    radar_3_stocks_contact_sheet.png, radar_intro.png, full_post_phase2.mp4, phase2_record.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image, ImageDraw

import render_daily_market_byte as r
from config import OUT_DIR
from core.content_safety import SafetyStatus, scan_publication
from daily_video import Composer, build_storyboard
from daily_video.radar_story_scene import T_STRIP
from daily_video.storyboard import Storyboard
from daily_video.typography import font

REPORT = os.path.join(OUT_DIR, "reports", "premarket_2026-09-23.json")
SESSION = "2026-09-21"


def _label(img, text):
    d = ImageDraw.Draw(img)
    d.rectangle((0, img.height - 34, img.width, img.height), fill=(0, 0, 0))
    d.text((10, img.height - 30), text, font=font(22), fill=(255, 255, 255))
    return img


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(OUT_DIR, "radar_phase2"))
    ap.add_argument("--frames-only", action="store_true")
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    plan, pres, rp, rr, ev, uni, src = r.load_inputs(REPORT, os.path.join(OUT_DIR, "radar"), SESSION)
    sb = build_storyboard(plan, pres, rp, rr, ev, uni, src, profile="PRIVATE_ANALYTICS")
    scan = scan_publication(sb.public_text())
    comp = Composer(sb)
    record = {"total_duration": sb.total_duration, "content_safety": scan.status.value,
              "hook": sb.hook_plan["curiosity_line"] if sb.hook_plan else None,
              "scenes": [(s.kind, s.duration) for s in sb.scenes], "omitted": sb.omitted,
              "stories": []}

    finals = []
    for i, spec in enumerate(sb.scenes):
        if spec.kind == "RADAR_INTRO":
            img, _, _ = comp.freeze(i)
            img.convert("RGB").save(os.path.join(args.out_dir, "radar_intro.png"))
        if spec.kind != "RADAR_STORY":
            continue
        sym = spec.texts["symbol"]
        d = os.path.join(args.out_dir, sym.lower())
        os.makedirs(d, exist_ok=True)
        shots = {"first_frame": 0.02, "chart_settled": T_STRIP - 0.05,
                 "final_settled": spec.freeze["t"]}
        imgs = []
        qa = None
        for name, t in shots.items():
            img, rec, q = comp.freeze(i, t)
            img = img.convert("RGB")
            img.save(os.path.join(d, f"{name}.png"))
            imgs.append((img, f"{name} @ {t:.2f}s"))
            if name == "final_settled":
                qa = q
        sheet = Image.new("RGB", (540 * len(imgs), 960))
        for k, (im, cap) in enumerate(imgs):
            sheet.paste(_label(im.resize((540, 960), Image.LANCZOS), cap), (k * 540, 0))
        sheet.save(os.path.join(d, "contact_sheet.png"))
        finals.append((imgs[-1][0], f"{spec.counter}  {sym}"))
        render = None
        if not args.frames_only:
            one = Storyboard(session_date=sb.session_date, date_label=sb.date_label,
                             kicker=sb.kicker, scenes=[spec])
            render = Composer(one).render(os.path.join(d, "scene.mp4"))
        record["stories"].append({
            "symbol": sym, "counter": spec.counter, "duration": spec.duration,
            "event_family": spec.data["event_family"], "event_type": spec.data["event_type"],
            "reference": [spec.data["reference_label"], spec.data["reference_display"]],
            "close": spec.data["latest_close_display"], "support": {
                k: v for k, v in spec.data["support"].items() if k != "volume"},
            "takeaway": spec.data["takeaway"], "freeze_qa": qa, "render": render})

    sheet = Image.new("RGB", (540 * len(finals), 960))
    for k, (im, cap) in enumerate(finals):
        sheet.paste(_label(im.resize((540, 960), Image.LANCZOS), cap), (k * 540, 0))
    sheet.save(os.path.join(args.out_dir, "radar_3_stocks_contact_sheet.png"))

    if not args.frames_only:
        record["full_video"] = comp.render(os.path.join(args.out_dir, "full_post_phase2.mp4"))
    with open(os.path.join(args.out_dir, "phase2_record.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False, default=str)
    print(json.dumps({k: record[k] for k in ("total_duration", "content_safety", "hook")}, ensure_ascii=False))
    for s in record["stories"]:
        print(s["counter"], s["symbol"], s["event_family"], s["support"]["kind"],
              "QA", "PASS" if s["freeze_qa"]["passed"] else s["freeze_qa"]["issues"], "|", s["takeaway"])
    ok = scan.status is SafetyStatus.SAFE and all(s["freeze_qa"]["passed"] for s in record["stories"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
