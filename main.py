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
from config import OUT_DIR, ASSETS_DIR, UNIVERSE, UNIVERSE_LABEL, TOP_N, DURATION, now_ist, fmt_in

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


# ----------------------------------------------------------------------------- main
def durations(present: set) -> dict:
    dur = {k: v for k, v in BASE_DUR.items() if k in present}
    missing = sum(v for k, v in BASE_DUR.items() if k not in present)
    base = sum(BASE_DUR[k] for k in STRETCH)
    for k in STRETCH:
        dur[k] += missing * BASE_DUR[k] / base
    return dur


def run(args):
    os.makedirs(OUT_DIR, exist_ok=True)
    today = now_ist().date()

    if args.demo:
        m, gainers, losers, events, nifty_reason, tiles, fd, sec = demo_data()
    else:
        print("[1/5] Fetching Nifty data...")
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

        print("[2/5] NSE data, sectors, global cues...")
        nse = market.NSE()
        idx = market.nse_all_indices(nse, m["recap_date"])
        fd = market.fii_dii_nse(nse, m["recap_date"])
        sec = market.get_sectors(m["recap_date"], m["prev_date"], idx)
        tiles = market.get_globals()

        print("[3/5] Top gainers & losers...")
        universe = market.get_universe(UNIVERSE)
        gainers, losers = market.get_movers(universe, m["recap_date"], m["prev_date"], TOP_N)

        print("[4/5] AI cross-check, reasons & events (single Gemini call)...")
        facts, nifty_reason, events = news.ai_pass(m["recap_date"], today, m["close"], gainers, losers, m)
        ref = idx.get("NIFTY 50", {}).get("last") or facts.get("nifty_close")
        if ref:
            diff = abs(ref / m["close"] - 1) * 100
            print(f"      Nifty check: yahoo {m['close']:.2f} vs {ref:.2f} ({diff:.2f}% diff)")
            if diff > 0.2:
                raise RuntimeError("Nifty close mismatch between sources - not posting wrong numbers")
        else:
            print("      WARNING: no second source for Nifty close available today")
        fd = fd or facts.get("fii_dii")
        if facts.get("gift"):
            tiles.insert(0, {"label": "GIFT NIFTY", "value": facts["gift"]["value"],
                             "pct": facts["gift"]["pct"], "dec": 0, "prefix": ""})
        if facts.get("brent"):
            tiles.append({"label": "BRENT CRUDE", "value": facts["brent"]["value"],
                          "pct": facts["brent"]["pct"], "dec": 2, "prefix": "$"})

    info = {"today_str": today.strftime("%A, %d %B %Y"),
            "today_short": today.strftime("%a %d %b"),
            "recap_str": m["recap_date"].strftime("%a, %d %b %Y")}
    present = {"intro", "nifty", "gainers", "losers", "events", "outro"}
    if len(tiles) >= 3:
        present.add("global")
    if fd:
        present.add("fii")
    if len(sec) >= 6:
        present.add("sector")
    dur = durations(present)
    tag = today.strftime("%Y-%m-%d")
    charts = chart.make_chart(m, os.path.join(OUT_DIR, f"nifty_chart_{tag}"))
    uni = UNIVERSE_LABEL.get(UNIVERSE, UNIVERSE)

    scenes = [video.IntroScene(info, hook_line(m, fd, sec), dur["intro"])]
    if "global" in present:
        scenes.append(video.GlobalScene(tiles, global_captions(tiles), dur["global"]))
    if "fii" in present:
        scenes.append(video.FiiDiiScene(fd, info["recap_str"], fii_captions(fd), dur["fii"]))
    scenes.append(video.NiftyScene(m, charts, nifty_captions(m, nifty_reason), dur["nifty"]))
    if "sector" in present:
        scenes.append(video.SectorScene(sec, info["recap_str"], sector_captions(sec), dur["sector"]))
    scenes += [video.MoversScene("gainers", gainers, uni, info["recap_str"], dur["gainers"]),
               video.MoversScene("losers", losers, uni, info["recap_str"], dur["losers"]),
               video.EventsScene(events, info, dur["events"]),
               video.OutroScene(dur["outro"])]

    print(f"[5/5] Rendering {sum(s.dur for s in scenes):.1f}s video ({len(scenes)} scenes)...")
    swells = list(np.cumsum([s.dur for s in scenes])[:-1])
    mus = music.get_music(ASSETS_DIR, OUT_DIR, DURATION, swells, date=m["recap_date"])
    out = os.path.join(OUT_DIR, f"daily_byte_{tag}{'_DEMO' if args.demo else ''}.mp4")
    video.render(scenes, info, ticker_items(m, tiles, sec, gainers, losers), mus, out, demo=args.demo)

    meta = build_metadata(m, gainers, losers, events, info, fd, sec, tiles)
    with open(out.replace(".mp4", ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"Done: {out}")

    if args.upload and not args.demo:
        import upload
        vid = upload.upload(out, meta)
        print(f"Uploaded: https://youtube.com/shorts/{vid}")
        with open(os.path.join(OUT_DIR, "last_session.txt"), "w") as f:
            f.write(str(m["recap_date"]))
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
