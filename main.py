"""Daily Byte - ~75s YouTube Short recapping the previous Indian market session.

Usage:
    python main.py              # build today's video (real data)
    python main.py --upload     # build + upload to YouTube (skips if yesterday was a holiday)
    python main.py --demo       # offline test with synthetic data (marked DEMO on screen)
    python main.py --force      # ignore holiday / already-posted checks
"""
import argparse
import datetime as dt
import json
import os
import sys
import traceback

import numpy as np
import pandas as pd

import chart
import market
import music
import news
import video
from adapters import report_builder
from adapters.news_adapter import NO_CATALYST_TEXT
from config import OUT_DIR, ASSETS_DIR, UNIVERSE, UNIVERSE_LABEL, TOP_N, DURATION, now_ist, fmt_in
from core.content_safety import CONTENT_SAFETY_VERSION, SafetyStatus, sanitize_field, scan_publication
from presentation import ReportPresentation
from providers import GeminiProvider, NewsProvider, NseProvider, YahooProvider

RS = video.RS
BASE_DUR = {"intro": 2.0, "global": 7.0, "fii": 5.0, "nifty": 16.0, "sector": 8.0,
            "gainers": 13.5, "losers": 13.5, "events": 7.0, "outro": 3.0}
STRETCH = ("nifty", "gainers", "losers")


# ----------------------------------------------------------------------------- captions
def nifty_captions(m: dict, reason: str) -> list:
    up = m["chg"] >= 0
    caps = [f"Nifty 50 closed at {fmt_in(m['close'])}, {'up' if up else 'down'} "
            f"{abs(m['chg']):.0f} points ({m['pct']:+.2f}%)."]
    if reason:
        caps.append(f"What moved it: {reason}")
    rng = f"Day's range: {fmt_in(m['low'])} to {fmt_in(m['high'])}."
    if m.get("vix") is not None:
        rng += f" India VIX at {m['vix']:.1f}."
    caps.append(rng)
    c, e20, e50 = m["close"], m["ema20"], m["ema50"]
    if c > e20 and c > e50:
        ema = "Price is above its 20 and 50-day EMAs, short-term trend positive."
    elif c < e20 and c < e50:
        ema = "Price is below its 20 and 50-day EMAs, short-term trend weak."
    else:
        ema = "Price is between its 20 and 50-day EMAs, trend looks sideways."
    caps.append(f"{ema} RSI at {m['rsi']:.0f}.")
    lv = m["levels"]
    parts = []
    if lv["res"]:
        parts.append(f"resistance near {fmt_in(lv['res'][0])}")
    if lv["sup"]:
        parts.append(f"support near {fmt_in(lv['sup'][0])}")
    if parts:
        caps.append("Chart shows " + " and ".join(parts) + ".")
    t = m["trend"]
    if t:
        if t["kind"] == "support":
            caps.append(f"Rising trendline from recent lows is near {fmt_in(t['now'])}"
                        + (", price still above it." if c >= t["now"] else ", price slipped below it."))
        else:
            caps.append(f"Falling trendline from recent highs is near {fmt_in(t['now'])}"
                        + (", price still below it." if c <= t["now"] else ", price broke above it."))
    p = m["pivot"]
    caps.append(f"Today's pivot {fmt_in(p['P'])}, R1 {fmt_in(p['R1'])}, S1 {fmt_in(p['S1'])}.")
    if len(caps) > 6:  # keep the pivot line, drop the least important middle one
        caps = caps[:5] + caps[-1:]
    return caps


def global_captions(tiles: list) -> list:
    by = {t["label"]: t for t in tiles}
    caps = []
    us = [f"{k.title()} {by[k]['pct']:+.1f}%" for k in ("DOW JONES", "NASDAQ") if k in by]
    if us:
        caps.append("Overnight in the US: " + ", ".join(us) + ".")
    if "GIFT NIFTY" in by:
        g = by["GIFT NIFTY"]
        caps.append(f"GIFT Nifty at {fmt_in(g['value'])} ({g['pct']:+.2f}%) signals a "
                    f"{'positive' if g['pct'] > 0.1 else 'negative' if g['pct'] < -0.1 else 'flat'} start.")
    extra = []
    if "BRENT CRUDE" in by:
        extra.append(f"Brent crude ${by['BRENT CRUDE']['value']:.1f}")
    if "USD / INR" in by:
        extra.append(f"rupee at {by['USD / INR']['value']:.2f} per dollar")
    if extra:
        caps.append(" and ".join(extra).capitalize() + ".")
    return caps or ["Here's how global markets moved overnight."]


def fii_captions(fd: dict) -> list:
    f, d = fd["fii"], fd["dii"]
    return [f"FIIs net {'bought' if f >= 0 else 'sold'} {RS}{fmt_in(abs(f))} crore in cash.",
            f"DIIs net {'bought' if d >= 0 else 'sold'} {RS}{fmt_in(abs(d))} crore."]


def sector_captions(sec: list) -> list:
    up = sum(1 for s in sec if s["pct"] >= 0)
    return [f"Best sector: {sec[0]['name']} {sec[0]['pct']:+.1f}%. Weakest: {sec[-1]['name']} {sec[-1]['pct']:+.1f}%.",
            f"{up} of {len(sec)} sectors closed higher."]


def hook_line(m, fd, sec):
    parts = [f"Nifty {m['pct']:+.2f}%"]
    if fd:
        parts.append(f"FIIs {'bought' if fd['fii'] >= 0 else 'sold'} {RS}{fmt_in(abs(fd['fii']))} cr")
    if sec:
        parts.append(f"{sec[0]['name']} led")
    return "  ·  ".join(parts)


def ticker_items(m, tiles, sec, gainers, losers):
    items = [("NIFTY 50", fmt_in(m["close"]), m["pct"])]
    if m.get("bank_pct") is not None:
        items.append(("BANK NIFTY", "", m["bank_pct"]))
    items += [(t["label"], video.fmt_val(t["value"], t["dec"], t.get("prefix", "")), t["pct"]) for t in tiles]
    items += [(s["name"].upper(), "", s["pct"]) for s in sec[:3]]
    items += [(r["symbol"], fmt_in(r["close"], 1), r["pct"]) for r in gainers[:3] + losers[:3]]
    return items


# ----------------------------------------------------------------------------- metadata
def build_metadata(m, gainers, losers, events, info, fd, sec, tiles):
    d = m["recap_date"].strftime("%d %b")
    title = f"Daily Market Byte {d}: Nifty {fmt_in(m['close'])} ({m['pct']:+.2f}%) | Gainers, Losers, FII #shorts"[:100]
    L = [f"Daily Market Byte - Indian stock market recap for {info['recap_str']}.", "",
         f"Nifty 50: {fmt_in(m['close'], 2)} ({m['pct']:+.2f}%)",
         f"Resistance: {', '.join(fmt_in(x) for x in m['levels']['res']) or '-'} | "
         f"Support: {', '.join(fmt_in(x) for x in m['levels']['sup']) or '-'}"]
    if fd:
        L.append(f"FII net: {fd['fii']:+,.0f} cr | DII net: {fd['dii']:+,.0f} cr (provisional)")
    if tiles:
        L += ["", "Global cues: " + " | ".join(f"{t['label']} {t['pct']:+.2f}%" for t in tiles)]
    if sec:
        L += ["Sectors: " + " | ".join(f"{s['name']} {s['pct']:+.2f}%" for s in sec)]
    L += ["", "Top gainers:"] + [f"  {r['symbol']} {r['pct']:+.2f}% - {r['reason']}" for r in gainers]
    L += ["", "Top losers:"] + [f"  {r['symbol']} {r['pct']:+.2f}% - {r['reason']}" for r in losers]
    L += ["", f"Events today ({info['today_short']}):"] + [f"  [{e['tag']}] {e['text']}" for e in events]
    L += ["", "Disclaimer: This video is for information and education only. It is not investment advice "
              "or a recommendation to buy or sell any security. We are not SEBI-registered advisers. "
              "Please do your own research or consult a registered adviser.", "",
          "#stockmarket #nifty #sensex #sharemarket #fii #stocks #trading #dailybyte #shorts"]
    tags = ["stock market", "nifty", "nifty 50", "sensex", "share market", "top gainers", "top losers",
            "fii dii data", "gift nifty", "sector performance", "stock market today", "indian stock market",
            "daily byte", "market recap"]
    return {"title": title, "description": "\n".join(L), "tags": tags}


# ----------------------------------------------------------------------------- demo data
def demo_data():
    rng = np.random.default_rng(3)
    days = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=250)
    close = 23500 + np.cumsum(rng.normal(12, 110, len(days)))
    opn = close + rng.normal(0, 60, len(days))
    df = pd.DataFrame({"Open": opn, "Close": close,
                       "High": np.maximum(opn, close) + rng.uniform(20, 120, len(days)),
                       "Low": np.minimum(opn, close) - rng.uniform(20, 120, len(days))}, index=days)
    m = market.analyze(df, bank_pct=0.62, vix=13.4)
    mk = lambda rows: [{"symbol": s, "name": n, "close": c, "pct": p, "volx": v, "reason": r}
                       for s, n, c, p, v, r in rows]
    g = mk([("STOCK-A", "Demo Company A Ltd.", 168.4, 4.8, 2.6, "Sample reason text for demo only."),
            ("STOCK-B", "Demo Company B Ltd.", 412.1, 3.9, 1.9, "Sample reason text for demo only."),
            ("STOCK-C", "Demo Company C Ltd.", 702.5, 3.1, 1.4, "Sample reason text for demo only."),
            ("STOCK-D", "Demo Company D Ltd.", 1624.0, 2.6, 1.2, "Sample reason text for demo only."),
            ("STOCK-E", "Demo Company E Ltd.", 5480.0, 2.2, 1.1, "Sample reason text for demo only.")])
    l_ = mk([("STOCK-F", "Demo Company F Ltd.", 2310.0, -3.4, 2.1, "Sample reason text for demo only."),
             ("STOCK-G", "Demo Company G Ltd.", 688.2, -2.7, 1.7, "Sample reason text for demo only."),
             ("STOCK-H", "Demo Company H Ltd.", 1480.5, -2.1, 1.3, "Sample reason text for demo only."),
             ("STOCK-I", "Demo Company I Ltd.", 3350.0, -1.8, 1.0, "Sample reason text for demo only."),
             ("STOCK-J", "Demo Company J Ltd.", 2250.0, -1.5, 0.9, "Sample reason text for demo only.")])
    events = [{"tag": "RESULTS", "text": "Sample: large-cap quarterly results"},
              {"tag": "F&O", "text": "Sample: Nifty weekly F&O expiry"},
              {"tag": "DATA", "text": "Sample: India inflation data release"},
              {"tag": "GLOBAL", "text": "Sample: US central bank speech"}]
    tiles = [{"label": "GIFT NIFTY", "value": m["close"] * 1.003, "pct": 0.30, "dec": 0, "prefix": ""},
             {"label": "DOW JONES", "value": 44120.0, "pct": 0.42, "dec": 0, "prefix": ""},
             {"label": "NASDAQ", "value": 19850.0, "pct": 0.91, "dec": 0, "prefix": ""},
             {"label": "BRENT CRUDE", "value": 78.2, "pct": -1.10, "dec": 2, "prefix": "$"},
             {"label": "USD / INR", "value": 86.35, "pct": 0.12, "dec": 2, "prefix": ""},
             {"label": "GOLD", "value": 2450.0, "pct": 0.35, "dec": 0, "prefix": "$"}]
    fd = {"fii": -1240.5, "dii": 2105.3, "source": "demo"}
    names = [s[0] for s in market.SECTORS]
    sec = sorted([{"name": n, "pct": float(p)} for n, p in zip(names, rng.normal(0.2, 1.1, len(names)))],
                 key=lambda x: -x["pct"])
    return m, g, l_, events, "Sample Nifty reason for demo only.", tiles, fd, sec


# ----------------------------------------------------------------------------- content safety
def apply_content_safety(nifty_reason, gainers, losers, events):
    """Sanitize every free-text field before it enters the report.

    Runs deterministically (no Gemini) on whatever text ai_pass produced, including the raw
    Google News fallback path - the pipeline must not republish recommendation-style
    headlines just because no AI reason was available for a mover. In Phase 2 this happens
    before the report is built, so the report stores publishable text and the presentation
    layer inherits it rather than being cleaned separately.

    Returns the (possibly rewritten) fields plus every non-SAFE finding for the audit trail.
    """
    findings = []

    def _track(label, text, fallback):
        clean, result = sanitize_field(text or "", fallback=fallback)
        if result.status is not SafetyStatus.SAFE:
            findings.append((label, result))
        return clean

    # ai_pass returns the Nifty summary with its provenance attached; demo fixtures pass a
    # plain string. Either way only the text is sanitized, never the provenance.
    if isinstance(nifty_reason, dict):
        nifty_reason = {**nifty_reason, "text": _track("nifty_reason", nifty_reason.get("text"), "")}
    else:
        nifty_reason = _track("nifty_reason", nifty_reason, "")

    for row in gainers + losers:
        row["reason"] = _track(f"mover:{row.get('symbol')}", row.get("reason"), NO_CATALYST_TEXT)
        if row["reason"] == NO_CATALYST_TEXT:
            row["reason_source"] = news.ORIGIN_NONE      # blocked text is no longer that source's
            row["reason_publisher"] = None

    safe_events = []
    for e in events:
        text = _track(f"event:{e.get('tag', '')}", e.get("text"), "")
        if text:                     # an event with nothing safe to say is dropped, not shown blank
            safe_events.append({**e, "text": text})
    events = safe_events or [{"tag": "INFO", "text": "No major scheduled events; track global cues",
                              "source": news.ORIGIN_RULE_PLACEHOLDER, "publisher": None}]

    return nifty_reason, gainers, losers, events, findings


def collect_public_text(scenes, events, meta) -> dict:
    """Every string that will actually appear to a viewer: on-screen captions (via each
    scene's own `captions()`, since MoversScene builds its per-row text dynamically rather
    than storing it in `.texts`), the event cards EventsScene draws directly, and the
    YouTube title/description."""
    fields = {"youtube_title": meta["title"], "youtube_description": meta["description"]}
    for e in events:
        fields[f"event:{e.get('tag', '')}"] = e.get("text", "")
    for scene in scenes:
        for i, (_, _, text) in enumerate(scene.captions()):
            fields[f"{type(scene).__name__}.caption[{i}]"] = text
    return fields


def summarize_content_safety(pre_findings, final_scan=None) -> dict:
    """Assemble the audit trail written into MarketReport.content_safety.

    Called twice per run: once while building the report (final_scan is None, because
    nothing has been rendered yet) and again after the pre-publication scan, which replaces
    the placeholder with the real verdict.
    """
    findings = [{"field": label, **r.to_dict()} for label, r in pre_findings]
    sanitized = sum(1 for _, r in pre_findings if r.status is SafetyStatus.SANITIZED)
    blocked = sum(1 for _, r in pre_findings if r.status is SafetyStatus.BLOCKED)
    if final_scan is None:
        final = {"status": "PENDING", "blocked_fields": []}
        status = "SANITIZED" if sanitized else "SAFE"
    else:
        blocked += len(final_scan.blocked_fields)
        final = {"status": final_scan.status.value, "blocked_fields": final_scan.blocked_fields}
        status = ("BLOCKED" if final_scan.status is not SafetyStatus.SAFE
                  else "SANITIZED" if sanitized else "SAFE")
    return {
        "status": status, "sanitized_count": sanitized, "blocked_count": blocked,
        "findings": findings, "final_scan": final,
        "version": CONTENT_SAFETY_VERSION,
    }


# ----------------------------------------------------------------------------- main
def durations(present: set) -> dict:
    dur = {k: v for k, v in BASE_DUR.items() if k in present}
    missing = sum(v for k, v in BASE_DUR.items() if k not in present)
    base = sum(BASE_DUR[k] for k in STRETCH)
    for k in STRETCH:
        dur[k] += missing * BASE_DUR[k] / base
    return dur


def collect(args, today):
    """Acquisition. Returns the raw session pieces, or None when the run should be skipped.

    This is the only place provider output exists as loose dictionaries; everything after
    build_report() reads the canonical report instead.
    """
    if args.demo:
        m, gainers, losers, events, nifty_reason, tiles, fd, sec = demo_data()
        return {"m": m, "gainers": gainers, "losers": losers, "events": events,
                "nifty_reason": nifty_reason, "tiles": tiles, "fd": fd, "sec": sec,
                "ai_facts": {}, "nse_idx": {}}

    print("[1/6] Fetching Nifty data...")
    m = market.get_market()
    prev_wd = today - dt.timedelta(days={0: 3, 6: 2}.get(today.weekday(), 1))
    state_file = os.path.join(OUT_DIR, "last_session.txt")
    if args.upload and not args.force:
        if m["recap_date"] != prev_wd:
            print(f"Latest session is {m['recap_date']}, expected {prev_wd} (market holiday?). Skipping.")
            return None
        if os.path.exists(state_file) and open(state_file).read().strip() == str(m["recap_date"]):
            print(f"Session {m['recap_date']} already posted. Skipping.")
            return None
    print(f"      session {m['recap_date']}: Nifty {m['close']:.2f} ({m['pct']:+.2f}%)")

    print("[2/6] NSE data, sectors, global cues...")
    nse = market.NSE()
    idx = market.nse_all_indices(nse, m["recap_date"])
    fd = market.fii_dii_nse(nse, m["recap_date"])
    sec = market.get_sectors(m["recap_date"], m["prev_date"], idx)
    tiles = market.get_globals()

    print("[3/6] Top gainers & losers...")
    universe = market.get_universe(UNIVERSE)
    gainers, losers = market.get_movers(universe, m["recap_date"], m["prev_date"], TOP_N)

    print("[4/6] AI cross-check, reasons & events (single Gemini call)...")
    ai_facts, nifty_reason, events = news.ai_pass(m["recap_date"], today, m["close"],
                                                  gainers, losers, m)
    # Legacy cross-check, kept as defence in depth. The authoritative verdict on whether
    # these sources agree is now the INDEX_CLOSE fact's CrossSourceValidator result.
    ref = idx.get("NIFTY 50", {}).get("last") or ai_facts.get("nifty_close")
    if ref:
        diff = abs(ref / m["close"] - 1) * 100
        print(f"      Nifty check: yahoo {m['close']:.2f} vs {ref:.2f} ({diff:.2f}% diff)")
        if diff > 0.2:
            raise RuntimeError("Nifty close mismatch between sources - not posting wrong numbers")
    else:
        print("      WARNING: no second source for Nifty close available today")
    fd = fd or ai_facts.get("fii_dii")
    if ai_facts.get("gift"):
        tiles.insert(0, {"label": "GIFT NIFTY", "value": ai_facts["gift"]["value"],
                         "pct": ai_facts["gift"]["pct"], "dec": 0, "prefix": ""})
    if ai_facts.get("brent"):
        tiles.append({"label": "BRENT CRUDE", "value": ai_facts["brent"]["value"],
                      "pct": ai_facts["brent"]["pct"], "dec": 2, "prefix": "$"})
    return {"m": m, "gainers": gainers, "losers": losers, "events": events,
            "nifty_reason": nifty_reason, "tiles": tiles, "fd": fd, "sec": sec,
            "ai_facts": ai_facts, "nse_idx": idx}


def build_report(raw, today, demo=False, content_safety=None):
    """Providers -> observations -> facts -> validation -> MarketReport."""
    now = now_ist()
    session_date = raw["m"]["recap_date"]
    yahoo = YahooProvider(session_date, today, now, demo=demo)
    nse = NseProvider(session_date, today, now, demo=demo)
    gemini = GeminiProvider(session_date, today, now, demo=demo)
    narrator = NewsProvider(session_date, today, now, demo=demo)

    ai_labels = frozenset({label for label, key in (("GIFT NIFTY", "gift"), ("BRENT CRUDE", "brent"))
                           if (raw["ai_facts"] or {}).get(key)})

    session_result = yahoo.from_market_dict(raw["m"])
    # Order matters: the source the renderer will display is observed first, so it supplies
    # each fact's published value (see report_builder.facts_from).
    results = [session_result,
               nse.indices(raw["nse_idx"]),
               yahoo.globals(raw["tiles"], ai_labels=ai_labels),
               yahoo.sectors(raw["sec"], raw["nse_idx"]),
               nse.fii_dii(raw["fd"]),
               yahoo.movers(raw["gainers"], raw["losers"]),
               gemini.facts(raw["ai_facts"])]
    observations = [o for r in results for o in r.observations]

    narrative = narrator.from_ai_pass(raw["nifty_reason"], raw["events"],
                                      raw["gainers"], raw["losers"], raw["ai_facts"]
                                      ).payload["narrative"]

    return report_builder.build_report(
        session=session_result.payload["index_session"], narrative=narrative,
        observations=observations, tiles=raw["tiles"], flows=raw["fd"], sectors=raw["sec"],
        gainers=raw["gainers"], losers=raw["losers"], report_date=today,
        universe_label=UNIVERSE_LABEL.get(UNIVERSE, UNIVERSE), demo=demo, now=now,
        content_safety=content_safety)


def check_publication(report, demo=False) -> bool:
    """The authoritative publication decision: the report's own validation verdict.

    Phase 2 makes this binding. A report that says it is not fit to publish now stops the
    run before anything is rendered, rather than being written afterwards as a note nobody
    acted on. Demo runs are exempt: synthetic data is never published anywhere.
    """
    summary = report.validation_summary
    if summary.publication_ready or demo:
        return True
    print("Publication BLOCKED by report validation:")
    for issue in summary.blocking_issues:
        print(f"  - {issue}")
    return False


def build_scenes(pres, info, dur, charts, uni):
    """Scene construction, reading only from the presentation view of the report."""
    present = pres.present()
    scenes = [video.IntroScene(info, hook_line(pres.m, pres.fd, pres.sec), dur["intro"])]
    if "global" in present:
        scenes.append(video.GlobalScene(pres.tiles, global_captions(pres.tiles), dur["global"]))
    if "fii" in present:
        scenes.append(video.FiiDiiScene(pres.fd, info["recap_str"], fii_captions(pres.fd), dur["fii"]))
    scenes.append(video.NiftyScene(pres.m, charts, nifty_captions(pres.m, pres.nifty_reason),
                                   dur["nifty"]))
    if "sector" in present:
        scenes.append(video.SectorScene(pres.sec, info["recap_str"], sector_captions(pres.sec),
                                        dur["sector"]))
    scenes += [video.MoversScene("gainers", pres.gainers, uni, info["recap_str"], dur["gainers"]),
               video.MoversScene("losers", pres.losers, uni, info["recap_str"], dur["losers"]),
               video.EventsScene(pres.events, info, dur["events"]),
               video.OutroScene(dur["outro"])]
    return scenes


def final_qa(scenes, events, meta, out, upload_requested):
    """Re-scan every finalized public-facing string immediately before publication.

    Text is already burned into video frames and written into metadata by this point, so
    nothing is rewritten here - anything not SAFE blocks publication instead.
    """
    scan = scan_publication(collect_public_text(scenes, events, meta))
    if scan.status is not SafetyStatus.SAFE:
        print("Content-safety QA FAILED - the following fields still contain unsafe text:")
        for field_name in scan.blocked_fields:
            r = scan.results[field_name]
            print(f"  - {field_name}: {r.status.value} ({r.reason})")
        print(f"Local artifacts preserved for inspection: {out}")
        if upload_requested:
            print("Upload blocked by content-safety QA.")
    return scan


def publish(out, meta, session_date):
    import upload
    vid = upload.upload(out, meta)
    print(f"Uploaded: https://youtube.com/shorts/{vid}")
    with open(os.path.join(OUT_DIR, "last_session.txt"), "w") as f:
        f.write(str(session_date))
    return vid


def run(args):
    os.makedirs(OUT_DIR, exist_ok=True)
    today = now_ist().date()

    raw = collect(args, today)
    if raw is None:
        return None

    # Content safety runs before the report is built, so the report stores publishable text
    # and presentation inherits it rather than being cleaned separately downstream.
    (raw["nifty_reason"], raw["gainers"], raw["losers"], raw["events"],
     safety_findings) = apply_content_safety(raw["nifty_reason"], raw["gainers"],
                                             raw["losers"], raw["events"])

    print("[5/6] Building validated market report...")
    report = build_report(raw, today, demo=args.demo,
                          content_safety=summarize_content_safety(safety_findings, None))
    print(f"      report: {report_builder.describe(report)}")

    if not check_publication(report, demo=args.demo):
        path = report_builder.save_report(report, OUT_DIR, demo=args.demo)
        print(f"Report written for diagnosis (no video rendered): {path}")
        return None

    pres = ReportPresentation(report)
    info = {"today_str": today.strftime("%A, %d %B %Y"),
            "today_short": today.strftime("%a %d %b"),
            "recap_str": pres.session_date.strftime("%a, %d %b %Y")}
    dur = durations(pres.present())
    tag = today.strftime("%Y-%m-%d")
    charts = chart.make_chart(pres.m, os.path.join(OUT_DIR, f"nifty_chart_{tag}"))
    uni = UNIVERSE_LABEL.get(UNIVERSE, UNIVERSE)
    scenes = build_scenes(pres, info, dur, charts, uni)

    print(f"[6/6] Rendering {sum(s.dur for s in scenes):.1f}s video ({len(scenes)} scenes)...")
    swells = list(np.cumsum([s.dur for s in scenes])[:-1])
    mus = music.get_music(ASSETS_DIR, OUT_DIR, DURATION, swells, date=pres.session_date)
    out = os.path.join(OUT_DIR, f"daily_byte_{tag}{'_DEMO' if args.demo else ''}.mp4")
    video.render(scenes, info, ticker_items(pres.m, pres.tiles, pres.sec, pres.gainers, pres.losers),
                 mus, out, demo=args.demo)

    meta = build_metadata(pres.m, pres.gainers, pres.losers, pres.events, info,
                          pres.fd, pres.sec, pres.tiles)
    with open(out.replace(".mp4", ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    scan = final_qa(scenes, pres.events, meta, out, args.upload)
    report.content_safety = summarize_content_safety(safety_findings, scan)
    path = report_builder.save_report(report, OUT_DIR, demo=args.demo)
    cs = report.content_safety
    print(f"      report: {os.path.basename(path)} | content safety: status={cs['status']} "
          f"sanitized={cs['sanitized_count']} blocked={cs['blocked_count']}")
    print(f"Done: {out}")

    if args.upload and not args.demo and scan.status is SafetyStatus.SAFE:
        publish(out, meta, pres.session_date)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--force", action="store_true")
    try:
        run(ap.parse_args())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
