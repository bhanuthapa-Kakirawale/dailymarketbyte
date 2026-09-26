"""Preview the Dynamic Hook Engine: every archetype, synthetic and real, as frames + MP4s.

    python render_hook_previews.py                 # all examples, frames + mp4
    python render_hook_previews.py --frames-only   # frames and contact sheets only
    python render_hook_previews.py --only real_post pre_event

Synthetic examples use placeholder stocks and carry a "SYNTHETIC DATA - NOT REAL" badge on
every frame. Real examples read the repository's validated 2026-09-21 artifacts (no network).
The "gemini_sim_*" records run the engine against SCRIPTED Gemini replies - labelled as such -
to show the accept / partial / reject paths without calling the API.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from PIL import Image, ImageDraw

from config import OUT_DIR
from core.content_safety import SafetyStatus, scan_publication
from daily_video import Composer
from daily_video.storyboard import Storyboard, dynamic_hook_spec
from daily_video.typography import font
from hooks import (custom_stock_sheet, plan_hook, post_market_sheet, pre_market_inputs_from_plan,
                   pre_market_sheet)
from hooks.candidates import build_candidates
from hooks.fixtures import CUSTOM_EXAMPLES, POST_EXAMPLES, PRE_EXAMPLES

REPORT = os.path.join(OUT_DIR, "reports", "premarket_2026-09-23.json")
RADAR_DIR = os.path.join(OUT_DIR, "radar")
KICKER = {"POST_MARKET": "SESSION RECAP", "PRE_MARKET": "BEFORE THE BELL",
          "CUSTOM_SINGLE_STOCK": "STOCK FOCUS"}
SYNTH_BADGE = "SYNTHETIC DATA - NOT REAL"
LABELS = {
    "post_quiet": "POST - quiet market", "post_sector_contrast": "POST - strong sector contrast",
    "post_radar_unusual": "POST - unusual Radar activity", "post_big_move": "POST - big move",
    "pre_overnight": "PRE - overnight/global cue", "pre_event": "PRE - event-led",
    "custom_breakout": "CUSTOM - breakout stock",
    "custom_weak_tech_strong_fund": "CUSTOM - weak technical / strong fundamentals",
    "real_post": "REAL POST - 21 Sep 2026", "real_pre": "REAL PRE - from the 21 Sep report",
}


def _real_inputs():
    import render_daily_market_byte as r
    plan, pres, rp, rr, ev, uni, _ = r.load_inputs(REPORT, RADAR_DIR, "2026-09-21")
    by = {s["instrument"]: s for s in rr["stories"]}
    stories = [(sc["story"], by[sc["story"]["instrument"]]) for sc in rp["scenes"]
               if sc.get("role") == "STORY" and sc["story"]["instrument"] in by]
    return plan, pres, stories, ev, uni


def sheets():
    """name -> (sheet, synthetic?)"""
    out = {}
    for name, fn in POST_EXAMPLES.items():
        b = fn()
        out[name] = (post_market_sheet(b["plan"], b["pres"], b["stories"], b["evidence"],
                                       b["sections"], b["universe"]), True)
    for name, fn in PRE_EXAMPLES.items():
        out[name] = (pre_market_sheet(fn()), True)
    for name, fn in CUSTOM_EXAMPLES.items():
        out[name] = (custom_stock_sheet(fn()), True)
    if os.path.exists(REPORT):
        plan, pres, stories, ev, uni = _real_inputs()
        present = ["PULSE", "NIFTY", "SECTORS", "MOVERS"] + (["RADAR"] if stories else []) + ["AHEAD"]
        out["real_post"] = (post_market_sheet(plan, pres, stories, ev, present, uni), False)
        from core import MarketReport
        rep = MarketReport.from_json(open(REPORT, encoding="utf-8").read())
        out["real_pre"] = (pre_market_sheet(pre_market_inputs_from_plan(plan, pres, rep.report_date)),
                           False)
    return out


def storyboard_for(sheet, hp) -> Storyboard:
    spec = dynamic_hook_spec(hp, sheet)
    return Storyboard(session_date=sheet.session_date,
                      date_label=sheet.session_date.strftime("%a %d %b %Y").upper(),
                      kicker=KICKER[sheet.mode.value], scenes=[spec], hook_plan=hp.to_dict())


def _caption(img, text):
    d = ImageDraw.Draw(img)
    f = font(22)
    d.rectangle((0, img.height - 34, img.width, img.height), fill=(0, 0, 0))
    d.text((10, img.height - 30), text, font=f, fill=(255, 255, 255))
    return img


def export(name, sheet, hp, out_dir, synthetic, video=True):
    sb = storyboard_for(sheet, hp)
    scan = scan_publication(sb.public_text())
    comp = Composer(sb, watermark=SYNTH_BADGE if synthetic else None)
    d = os.path.join(out_dir, name)
    os.makedirs(d, exist_ok=True)
    tm = hp.timing
    shots = []
    for i, bid in enumerate(hp.teaser_beats):
        t = i * tm.beat_seconds + tm.beat_seconds * 0.62
        img = comp.frame(t).convert("RGB")
        img.save(os.path.join(d, f"beat{i + 1}.png"))
        shots.append((img, f"beat {i + 1} @ {t:.2f}s  {bid}"))
    img, rec, qa = comp.freeze(0)
    img = img.convert("RGB")
    img.save(os.path.join(d, "settled.png"))
    shots.append((img, f"settled @ {sb.scenes[0].freeze['t']:.2f}s (freeze frame)"))
    first = comp.frame(0.0).convert("RGB")
    first.save(os.path.join(d, "frame0.png"))
    w, h = 540, 960
    sheet_img = Image.new("RGB", (w * len(shots), h), (0, 0, 0))
    for i, (im, cap) in enumerate(shots):
        sheet_img.paste(_caption(im.resize((w, h), Image.LANCZOS), cap), (i * w, 0))
    sheet_img.save(os.path.join(d, "contact_sheet.png"))
    result = None
    if video:
        result = comp.render(os.path.join(d, f"hook_{name}.mp4"))
    record = {"example": name, "label": LABELS.get(name, name), "synthetic": synthetic,
              "hook_plan": hp.to_dict(), "freeze_qa": qa, "content_safety": scan.status.value,
              "blocked_fields": scan.blocked_fields,
              "candidates": [{"id": c.candidate_id, "archetype": c.archetype.value, "score": c.score,
                              "curiosity_line": c.curiosity_line, "summary_line": c.summary_line,
                              "heroes": [h.value for h in c.heroes],
                              "default_beats": list(c.default_beats), "rationale": c.rationale}
                             for c in build_candidates(sheet)],
              "facts": [f.to_dict() for f in sheet.facts],
              "beats": [{"beat_id": b.beat_id, "kind": b.kind.value, "shows": b.description}
                        for b in sheet.beats],
              "render": result}
    with open(os.path.join(d, "hook_record.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False, default=str)
    return record


# --------------------------------------------------------------------------- scripted Gemini
def gemini_simulations(sheet):
    """Run the engine against scripted replies (NOT the real API) to show each outcome."""
    cands = build_candidates(sheet)
    alt = next((c for c in cands if c.archetype.value == "UNUSUAL_ACTIVITY"), cands[0])
    good = {"candidate_id": alt.candidate_id, "archetype": alt.archetype.value,
            "hero_visual": list(alt.heroes)[0].value,
            "teaser_beats": ["NIFTY_CLOSE", "SECTOR_CONTRAST", "RADAR_SWEEP"],
            "curiosity_line": "Nifty +0.29%. MANKIND +6.0% on 4.1× normal volume.",
            "summary_line": "Inside: every sector, top movers and 5 Radar stocks.",
            "fact_ids_used": ["nifty.move", "radar.MANKIND.move", "radar.MANKIND.volume"]}
    cases = {
        "accepted": good,
        "invented_number": dict(good, curiosity_line="Nifty +0.3%. MANKIND jumped 6% on 4x volume."),
        "causal_and_prediction": dict(good, curiosity_line="MANKIND rallied on hopes and could run further."),
        "recommendation": dict(good, summary_line="Inside: 5 Radar stocks worth buying today."),
        "unapproved_visual": dict(good, hero_visual="NEON_ROCKET"),
        "unknown_beat": dict(good, teaser_beats=["NIFTY_CLOSE", "SENSEX_CHART"]),
        "not_json": "Sure! Here is a great hook for today: Nifty flat, stocks wild!",
    }
    out = {}
    for name, reply in cases.items():
        raw = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        hp = plan_hook(sheet, client=lambda prompt, schema, raw=raw: raw)
        out[name] = {"scripted_reply": reply, "result_source": hp.source.value,
                     "fallback_reason": hp.fallback_reason, "validation_issues": hp.validation_issues,
                     "curiosity_line": hp.curiosity_line, "summary_line": hp.summary_line,
                     "archetype": hp.archetype.value, "hero": hp.hero_visual.value,
                     "beats": hp.teaser_beats}
    return out, plan_hook(sheet, client=lambda p, s: json.dumps(good, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(OUT_DIR, "hook_previews"))
    ap.add_argument("--frames-only", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    summary = []
    all_sheets = sheets()
    for name, (sheet, synthetic) in all_sheets.items():
        if args.only and name not in args.only:
            continue
        hp = plan_hook(sheet, use_ai=False)
        rec = export(name, sheet, hp, args.out_dir, synthetic, video=not args.frames_only)
        summary.append(rec)
        print(f"{name:32s} {hp.archetype.value:28s} {hp.hero_visual.value:20s} "
              f"QA {'PASS' if rec['freeze_qa']['passed'] else 'FAIL'}  safety {rec['content_safety']}")
        print(f"{'':32s} \"{hp.curiosity_line}\"  /  \"{hp.summary_line}\"")
    if "real_post" in all_sheets and (not args.only or "real_post_gemini_sim" in args.only):
        sheet = all_sheets["real_post"][0]
        sims, hp = gemini_simulations(sheet)
        with open(os.path.join(args.out_dir, "gemini_simulations_real_post.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"note": "SCRIPTED replies - the Gemini API was not called", "cases": sims},
                      fh, indent=2, ensure_ascii=False, default=str)
        rec = export("real_post_gemini_sim", sheet, hp, args.out_dir, False,
                     video=not args.frames_only)
        summary.append(rec)
        for k, v in sims.items():
            print(f"gemini-sim {k:24s} -> {v['result_source']:28s} {v['fallback_reason'] or ''}")
    # one overview of every settled frame
    tiles = [(r["example"], os.path.join(args.out_dir, r["example"], "settled.png")) for r in summary]
    if tiles:
        cols = 5
        w, h = 432, 768
        rows = -(-len(tiles) // cols)
        ov = Image.new("RGB", (cols * w, rows * h), (0, 0, 0))
        for i, (n, p) in enumerate(tiles):
            im = _caption(Image.open(p).convert("RGB").resize((w, h), Image.LANCZOS), LABELS.get(n, n))
            ov.paste(im, ((i % cols) * w, (i // cols) * h))
        ov.save(os.path.join(args.out_dir, "overview_settled.png"))
    with open(os.path.join(args.out_dir, "preview_index.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: r[k] for k in ("example", "label", "synthetic", "content_safety")} |
                   {"archetype": r["hook_plan"]["archetype"], "hero": r["hook_plan"]["hero_visual"],
                    "curiosity_line": r["hook_plan"]["curiosity_line"],
                    "summary_line": r["hook_plan"]["summary_line"],
                    "beats": r["hook_plan"]["teaser_beats"], "source": r["hook_plan"]["source"],
                    "qa_passed": r["freeze_qa"]["passed"]} for r in summary], fh, indent=2,
                  ensure_ascii=False)
    return 0 if all(r["freeze_qa"]["passed"] and r["content_safety"] == "SAFE" for r in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
