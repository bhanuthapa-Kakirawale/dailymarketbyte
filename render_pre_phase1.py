"""PRE-MARKET V1 validation renders -> output/pre_phase1/

REAL scenarios reconstruct a past morning at a 07:45 IST cutoff from real data: the canonical
report of the previous session (as stored), Yahoo dated daily bars (US closes) and 5-minute bars
(Asia, the last bar completed before the cutoff), India VIX by canonical date, the verified
event calendar and the previous session's published Radar stories. Nothing dated after the
cutoff is read. GIFT Nifty is read live from NSE IX (providers.gift_nifty); in a
reconstruction of a past morning the live reading is after the cutoff (FUTURE) and omitted.

SYNTHETIC scenarios cover the morning types no stored real session covers. Their folders are
prefixed SYNTHETIC_, every frame carries "SYNTHETIC DATA - NOT REAL", every stock is a
placeholder, and the event card says "Source: synthetic fixture".

    python render_pre_phase1.py                 # all scenarios, MP4 + frames
    python render_pre_phase1.py --frames-only   # frames + QA, no MP4
    python render_pre_phase1.py --only SYNTHETIC_QUIET
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from config import OUT_DIR
from core.freshness import IST
from presentation.pre_plan import PreMarketBlocked
from products.pre_fixtures import synthetic_brief
from products.premarket import _contact_sheet, build_real_brief, render_pre

OUT = os.path.join(OUT_DIR, "pre_phase1")
CUTOFF = dt.time(7, 45)

# id -> (kind, date or fixture, what it demonstrates)
SCENARIOS = {
    "REAL_2026-09-25": ("REAL", dt.date(2026, 9, 25),
                        "E: previous-session large Nifty move (-1.64%, below 20-day low), FII/DII, "
                        "VIX jump, Radar carry-forward; quiet US night"),
    "REAL_2026-09-22": ("REAL", dt.date(2026, 9, 22),
                        "C: strong global risk-on night (Nasdaq +2.26%), Nikkei closed (holiday), "
                        "weekly F&O expiry; quiet previous session"),
    "REAL_2026-09-24": ("REAL", dt.date(2026, 9, 24),
                        "risk-off US night (Nasdaq -1.13%) - expected BLOCKED: no canonical report "
                        "for the previous session 23 Sep"),
    "REAL_2026-09-23": ("REAL", dt.date(2026, 9, 23),
                        "expected BLOCKED: previous session 22 Sep has no canonical report (the "
                        "latest stored report is 21 Sep - never shown as 'previous')"),
    "SYNTHETIC_QUIET": ("SYNTHETIC", "QUIET", "A: quiet morning"),
    "SYNTHETIC_RISK_OFF": ("SYNTHETIC", "RISK_OFF", "B: strong global risk-off morning + GIFT"),
    "SYNTHETIC_RISK_ON": ("SYNTHETIC", "RISK_ON", "C (synthetic twin): risk-on + GIFT"),
    "SYNTHETIC_EVENT": ("SYNTHETIC", "EVENT", "D: event-led morning (RBI policy at 10:00 AM)"),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-only", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    summary = {"generated_at": dt.datetime.now(IST).isoformat(), "scenarios": {}}
    sheets = []
    for sid, (kind, what, desc) in SCENARIOS.items():
        if args.only and sid not in args.only:
            continue
        out = os.path.join(OUT, sid)
        print(f"== {sid}: {desc}")
        entry = {"kind": kind, "description": desc}
        try:
            if kind == "REAL":
                as_of = dt.datetime.combine(what, CUTOFF, tzinfo=IST)
                brief, acq = build_real_brief(what, as_of)
                for line in acq.log:
                    print("   ", line)
                res = render_pre(brief, out, frames_only=args.frames_only, acquisition=acq)
            else:
                brief = synthetic_brief(what)
                res = render_pre(brief, out, watermark="SYNTHETIC DATA - NOT REAL",
                                 frames_only=args.frames_only)
        except PreMarketBlocked as exc:
            print(f"   BLOCKED {exc.code}: {exc.message}")
            os.makedirs(out, exist_ok=True)
            entry.update(status="BLOCKED", code=exc.code, message=exc.message)
            with open(os.path.join(out, "pre_blocked.json"), "w", encoding="utf-8") as fh:
                json.dump(entry, fh, indent=2)
            summary["scenarios"][sid] = entry
            continue
        entry.update(status="RENDERED" if res.get("ok") else ("BLOCKED" if res.get("blocked") else "QA_FAILED"),
                     **{k: res.get(k) for k in ("total_duration", "probed_duration", "order", "hook",
                                                "freeze_frame_qa", "language_issues",
                                                "content_safety", "video", "contact_sheet",
                                                "blocked")})
        print(f"   {entry['status']} {res.get('total_duration')}s order={res.get('order')}")
        if res.get("hook"):
            print(f"   hook {res['hook']['archetype']} ({res['hook']['source']}): "
                  f"{res['hook']['curiosity_line']}")
        if res.get("freeze_frame_qa") and not res["freeze_frame_qa"]["passed"]:
            print("   QA issues:", json.dumps(res["freeze_frame_qa"]["issues"], indent=1)[:2000])
        if res.get("contact_sheet"):
            sheets.append(res["contact_sheet"])
        summary["scenarios"][sid] = entry
    if sheets:
        from PIL import Image
        ims = [Image.open(p) for p in sheets]
        w = max(i.width for i in ims)
        big = Image.new("RGB", (w, sum(i.height for i in ims) + 10 * len(ims)), (12, 14, 24))
        y = 0
        for im in ims:
            big.paste(im, (0, y))
            y += im.height + 10
        big.save(os.path.join(OUT, "pre_contact_sheet.png"))
    with open(os.path.join(OUT, "validation_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
