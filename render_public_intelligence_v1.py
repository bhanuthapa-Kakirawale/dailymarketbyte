"""Public market intelligence V1 - review renders -> output/public_intelligence_v1/

    python render_public_intelligence_v1.py                  # all scenarios, frames + audits
    python render_public_intelligence_v1.py --mp4 POST_REAL_2026-09-21 SYNTHETIC_PRE_PUBLIC
    python render_public_intelligence_v1.py --only SYNTHETIC_POST_CONCENTRATED

REAL scenarios read only artifacts already on disk (canonical report, Radar artifacts, a stored
Market Structure snapshot / exchange-event list when present) - nothing is fetched here.
SYNTHETIC scenarios (products.public_fixtures) use placeholder names, carry the red
"SYNTHETIC DATA - NOT REAL" badge on every frame, and their audits say synthetic: true (an upload
of one is refused by publication.audit.require_publication_pass). Nothing is uploaded, ever.

Per scenario: storyboard.json, publication_audit.json, freeze_frames/ (+ freeze-frame QA),
contact_sheet.png and, with --mp4, the MP4. Blocked-content attempts (I/J/P/Q) are written as
blocked_attempts.json - they have no video because the gate refuses them before a storyboard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from types import SimpleNamespace as NS

from config import OUT_DIR

OUT = os.path.join(OUT_DIR, "public_intelligence_v1")
SESSION, PREV, NEXT = dt.date(2026, 9, 25), dt.date(2026, 9, 24), dt.date(2026, 9, 28)
REAL_REPORT = os.path.join(OUT_DIR, "reports", "premarket_2026-09-23.json")
REAL_RADAR_DIR = os.path.join(OUT_DIR, "radar")


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- synthetic POST inputs
def _synthetic_post_inputs(pct):
    import numpy as np
    import pandas as pd

    from core.enums import SourceType
    from editorial.models import EditorialItem, ScenePlan, SceneType, ShortsPlan
    obs = lambda src, st: NS(source_name=src, source_type=SourceType(st),
                             retrieved_at=dt.datetime(2026, 9, 25, 16, 5, tzinfo=dt.timezone.utc))
    sectors = [("Metal", 1.4), ("Auto", 0.9), ("Pharma", 0.6), ("FMCG", 0.2), ("Bank", -0.3),
               ("IT", -0.8), ("Realty", -1.1), ("Energy", -1.3)]
    if pct < -1:
        sectors = [(n, v - 1.6) for n, v in sectors]

    sector_rows = [{"name": n, "fact_id": f"f.s{i}"} for i, (n, _) in enumerate(sectors)]

    class Report:
        session_date, report_date = SESSION, NEXT
        nifty = {"fact_ids": ["f.nifty"]}
        institutional_flows = {"fact_ids": ["f.fii"]}
        global_cues, events = [], []

        @property
        def sectors(self):
            return sector_rows

        def fact(self, fid):
            if fid == "f.nifty":
                return NS(observations=[obs("nse_website", "PRIMARY"), obs("yahoo_finance", "SECONDARY")])
            return NS(observations=[obs("yahoo_finance", "SECONDARY")])

    n = 40
    rng = np.random.default_rng(7)
    closes = list(24000 + np.cumsum(rng.normal(0, 60, n)))
    closes[-1] = closes[-2] * (1 + pct / 100)
    opens = [closes[0]] + closes[:-1]
    df = pd.DataFrame({"Open": opens, "High": [max(o, c) + 40 for o, c in zip(opens, closes)],
                       "Low": [min(o, c) - 40 for o, c in zip(opens, closes)], "Close": closes,
                       "ema20": pd.Series(closes).ewm(span=20).mean().tolist()},
                      index=pd.bdate_range(end=SESSION, periods=n))
    c = float(df["Close"].iloc[-1])
    m = {"pct": pct, "close": c, "chg": c - closes[-2], "open": float(df["Open"].iloc[-1]),
         "high": float(df["High"].iloc[-1]), "low": float(df["Low"].iloc[-1]), "chart_df": df,
         "prev_date": PREV}
    pres = NS(m=m, sec=[{"name": s, "pct": v} for s, v in sectors], session_date=SESSION, fd=None,
              report=Report())
    item = lambda t, v: EditorialItem(title=t, value=f"{v:+.2f}%", numeric=v, positive=v >= 0)
    plan = ShortsPlan(report_id="SYNTHETIC", session_date=SESSION, scenes=[
        ScenePlan(scene_id="g", scene_type=SceneType.GAINERS, items=[item("STOCK-005", 8.4)]),
        ScenePlan(scene_id="l", scene_type=SceneType.LOSERS, items=[item("STOCK-006", -4.1)])])
    radar_pres = {"status": "OK", "scenes": [{"role": "STORY", "story": {
        "instrument": "STOCK-001", "headline": "Closed above its 20-day high"}}]}
    radar_result = {"stories": [{"instrument": "STOCK-001", "price_change_pct": 6.1}]}
    return plan, pres, radar_pres, radar_result


SYNTHETIC_POST = {
    # id: (nifty %, structure, exchange, ipo, nifty100 split, what it shows)
    "SYNTHETIC_POST_CONCENTRATED": (0.12, "CONCENTRATED", "FNO_BAN", "CLOSES_TODAY", False,
                                    "A quiet Nifty + C strong sector concentration + F F&O ban + "
                                    "M IPO bidding closed with timestamped bids"),
    "SYNTHETIC_POST_SELLOFF": (-1.9, "SELLOFF", None, None, False, "B broad selloff (breadth)"),
    "SYNTHETIC_POST_MANY_UNUSUAL": (0.35, "MANY_UNUSUAL", "SURVEILLANCE", "LISTING_DAY", True,
                                    "D many unusual-volume observations + K NIFTY 100 vs rest of "
                                    "NIFTY 200 + G surveillance + N listing-day facts"),
    "SYNTHETIC_POST_NO_STRUCTURE": (0.08, "QUIET", "CORPORATE", None, False,
                                    "E no meaningful Market Structure event (section omitted) + "
                                    "H official corporate event"),
    "SYNTHETIC_POST_DIVERGENCE": (0.42, "DIVERGENCE", None, "BOARD", False,
                                  "Nifty up while most NIFTY 200 stocks fell + primary-market board"),
}


def render_synthetic_post(sid, spec, mp4):
    from daily_video import build_storyboard
    from products import public_fixtures as PF
    pct, structure, exchange, ipo, n100, what = spec
    plan, pres, rp, rr = _synthetic_post_inputs(pct)
    intel = PF.intelligence(SESSION, PREV, NEXT, structure_scenario=structure,
                            exchange_scenario=exchange, ipo_scenario=ipo, with_nifty100=n100,
                            ipo_day=SESSION)
    sb = build_storyboard(plan, pres, rp, rr, {}, "Nifty 100", {"synthetic": what},
                          intelligence=intel)
    return _finish(sid, sb, "POST_UNIFIED", mp4, synthetic=True, what=what)


# --------------------------------------------------------------------------- real POST
def render_real_post(sid, mp4, profile=None):
    import render_daily_market_byte as r
    from operations.sessions import next_session
    from presentation.public_intelligence import load_public_intelligence
    from products import post_unified as PU
    from publication import resolve_profile
    session = "2026-09-21"
    prof = resolve_profile(profile)
    report, plan = r.load_report_and_plan(REAL_REPORT, profile=prof)
    d = dt.date.fromisoformat(session)
    intel = load_public_intelligence(d, next_session(d) or d, OUT_DIR, fetch=False)
    sb, _ = PU.build_post_storyboard(report, plan, profile=prof, intelligence=intel,
                                     radar_dir=REAL_RADAR_DIR,
                                     sources={"market_report": REAL_REPORT})
    what = ("REAL 21 Sep 2026 session (stored canonical report + Radar artifacts), "
            f"profile {sb.publication_profile}; notes: {intel.notes}")
    return _finish(sid, sb, "POST_UNIFIED", mp4, synthetic=False, what=what)


def render_real_post_24(sid, mp4):
    """REAL session 24 Sep 2026 (Nifty -1.64%): the stored canonical report + the Market
    Structure snapshot built from the same Radar detectors (market_structure.build)."""
    import render_daily_market_byte as r
    from presentation.public_intelligence import load_public_intelligence
    from products import post_unified as PU
    from publication import resolve_profile
    path = os.path.join(OUT_DIR, "reports", "premarket_2026-09-25.json")
    prof = resolve_profile(None)
    report, plan = r.load_report_and_plan(path, profile=prof)
    d = dt.date(2026, 9, 24)
    intel = load_public_intelligence(d, dt.date(2026, 9, 25), OUT_DIR, fetch=False)
    sb, _ = PU.build_post_storyboard(report, plan, profile=prof, intelligence=intel,
                                     radar_dir=REAL_RADAR_DIR, sources={"market_report": path})
    return _finish(sid, sb, "POST_UNIFIED", mp4, synthetic=False,
                   what=f"REAL 24 Sep 2026 session; notes: {intel.notes}")


def _real_universe():
    import market_structure as ms
    path = os.path.join(OUT_DIR, "market_structure", "market_structure_2026-09-24.json")
    if not os.path.exists(path):
        return {}, set()
    _, uni, _ = ms.load_snapshot(path)
    return uni.companies(), uni.symbols()


def _ipos_from_dicts(rows):
    from ipo_watch import BoardType, IPOEvent, IPOStatus
    out = []
    for r in rows:
        d = {k: v for k, v in r.items() if k in IPOEvent.__dataclass_fields__}
        for k in ("data_as_of", "issue_open_date", "issue_close_date", "allotment_date",
                  "listing_date"):
            if d.get(k):
                d[k] = dt.date.fromisoformat(d[k])
        d["board_type"], d["status"] = BoardType(d["board_type"]), IPOStatus(d["status"])
        d["subscription"], d["listing"], d["financials"], d["risk_facts"] = None, None, [], []
        out.append(IPOEvent(**d))
    return out


def render_real_official(sid, fetch, mp4):
    """REAL official lists read today: NSE F&O ban file (its own trade date), ASM/GSM (NIFTY 200
    constituents only) and NSE IPO lists (the next dated IPO events). One EXCHANGE WATCH and one
    IPO WATCH review scene, watermarked as a review preview - never published."""
    from config import now_ist
    from daily_video.public_storyboard import exchange_spec, ipo_spec
    from daily_video.storyboard import SceneSpec, Storyboard
    from exchange_watch import (fetch_fo_ban, fetch_surveillance, load_previous, mark_changes,
                                save_events, validate)
    from exchange_watch.models import ExchangeEvent, SourceResult
    from ipo_watch import fetch_nse_issues
    from presentation.public_intelligence import PublicIntelligence, plan_public_sections
    from publication import PublicationGate
    known, uni_syms = _real_universe()
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    cache = os.path.join(OUT, sid, "official_lists.json")
    if fetch:
        results = [fetch_fo_ban(now_iso)] + list(fetch_surveillance(now_iso))
        ipos, ipo_notes = fetch_nse_issues(now_ist().date(), now_iso)
        _dump(cache, {"fetched_at": now_iso, "sources": [r.to_dict() for r in results],
                      "ipo_notes": ipo_notes, "ipos": [i.to_dict() for i in ipos]})
    elif not os.path.exists(cache):
        return {"scenario": sid, "skipped": "no official lists fetched (--fetch-live)"}
    data = json.load(open(cache, encoding="utf-8"))
    results = [SourceResult(r["source_name"], r["status"],
                            [ExchangeEvent.from_dict(e) for e in r["events"]], r["reason"],
                            dt.date.fromisoformat(r["list_date"]) if r["list_date"] else None,
                            r["retrieved_at"]) for r in data["sources"]]
    ban = next((r for r in results if r.source_name == "nse_fo_secban" and r.status == "OK"), None)
    list_date = ban.list_date if ban else now_ist().date()
    raw = [e for r in results if r.status == "OK" for e in r.events]
    valid, rejected = validate(raw, list_date)
    valid = mark_changes(valid, load_previous(OUT_DIR, list_date))
    save_events(valid, OUT_DIR, list_date, results)
    ipos = _ipos_from_dicts(data["ipos"])
    ipo_day = min((d for i in ipos for d in (i.issue_close_date, i.issue_open_date,
                                              i.listing_date) if d and d >= list_date),
                  default=list_date)
    intel = PublicIntelligence(exchange_events=valid, exchange_sources=results,
                               known_securities=known, universe_symbols=uni_syms)
    gate = PublicationGate(None, known)
    ex = plan_public_sections(gate, intel, list_date, "PRE", max_structure=0)
    ip = plan_public_sections(gate, PublicIntelligence(ipos=ipos), ipo_day, "PRE",
                              max_structure=0)
    scenes = ([exchange_spec(ex.exchange, "PRE")] if ex.exchange else [])
    scenes += ([ipo_spec(ip.ipo)] if ip.ipo else [])
    if not scenes:
        return {"scenario": sid, "skipped": "no qualifying official event", "rejected": rejected}
    scenes.append(SceneSpec(kind="CLOSING", section="CLOSING", duration=2.6,
                            texts={"cta": "SUBSCRIBE", "note": ""}, dim_background=False,
                            freeze={"t": 2.1}))
    sb = Storyboard(session_date=list_date, date_label=list_date.strftime("%a %d %b %Y").upper(),
                    kicker="BEFORE THE BELL", scenes=scenes, gate=gate,
                    public_audit={"exchange_watch": ex.audit["exchange_watch"],
                                  "ipo": ip.audit["ipo"]},
                    sources={"fetched_at": data["fetched_at"]})
    return _finish(sid, sb, "REVIEW_SCENES", mp4, synthetic=False,
                   watermark="REAL DATA - REVIEW PREVIEW",
                   what=f"REAL official lists fetched {data['fetched_at']}: F&O ban trade date "
                        f"{list_date}, IPO events dated {ipo_day}; rejected {len(rejected)}")


def render_real_pre(sid, mp4):
    from products.premarket import build_real_brief, render_pre as run
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    d = dt.date(2026, 9, 25)
    brief, acq = build_real_brief(d, dt.datetime.combine(d, dt.time(7, 45), ist))
    out = os.path.join(OUT, sid)
    res = run(brief, out, frames_only=not mp4, acquisition=acq)
    return {"scenario": sid, "synthetic": False, "ok": res.get("ok"), "blocked": res.get("blocked"),
            "duration": res.get("total_duration"), "order": res.get("order"),
            "publication_audit": res.get("publication_audit"),
            "what": "REAL reconstruction of the 25 Sep 2026 07:45 IST morning (public profile)"}


# --------------------------------------------------------------------------- PRE
def render_pre(sid, kind, mp4, exchange="FNO_BAN", ipo="BOARD"):
    from products import public_fixtures as PF
    from products.pre_fixtures import PRE_DATE, PREV as PPREV, synthetic_brief
    from products.premarket import render_pre as run
    brief = synthetic_brief(kind)
    brief.public_intelligence = PF.intelligence(PPREV, PPREV - dt.timedelta(days=3), PRE_DATE,
                                                exchange_scenario=exchange, ipo_scenario=ipo)
    out = os.path.join(OUT, sid)
    res = run(brief, out, watermark=PF.SYNTHETIC_WATERMARK, frames_only=not mp4)
    return {"scenario": sid, "synthetic": True, "ok": res.get("ok"), "blocked": res.get("blocked"),
            "duration": res.get("total_duration"), "order": res.get("order"),
            "publication_audit": res.get("publication_audit"),
            "what": f"PRE {kind} + exchange {exchange} + IPO {ipo} (public profile)"}


def _finish(sid, sb, product, mp4, synthetic, what, watermark=None):
    from daily_video import Composer
    from daily_video.public_storyboard import audit_storyboard
    from products import public_fixtures as PF
    from products.premarket import _contact_sheet
    from publication import write_publication_audit
    out = os.path.join(OUT, sid)
    os.makedirs(out, exist_ok=True)
    _dump(os.path.join(out, "storyboard.json"), sb.to_dict())
    comp = Composer(sb, watermark=PF.SYNTHETIC_WATERMARK if synthetic else watermark)
    names = [f"{i:02d}_{s.kind.lower()}" for i, s in enumerate(sb.scenes)]
    qa = comp.export_freeze_frames(os.path.join(out, "freeze_frames"), names)
    _contact_sheet([e["image"] for e in qa["scenes"]], os.path.join(out, "contact_sheet.png"),
                   label=f"{'SYNTHETIC' if synthetic else 'REAL'} - {sid} - "
                         f"{sb.publication_profile} - {sb.total_duration:.1f}s")
    video = None
    if mp4:
        video = os.path.join(out, f"{sid}.mp4")
        if not comp.render(video)["ok"]:
            video = None
    probe = {}
    if video:
        from qa.video_qa import probe_media
        probe = probe_media(video)
    audio = {"audio_stream": bool(probe.get("audio")), "audio_phase_enabled": False}
    audit = audit_storyboard(sb, product, video_path=video, synthetic=synthetic, audio=audio)
    write_publication_audit(audit, out)
    from publication.audit import content_checks_passed
    _dump(os.path.join(out, "qa_artifact.json"), {
        "scenario": sid, "freeze_frame_qa": {"passed": qa["passed"], "issues": {
            e["scene"]: e["qa"]["issues"] for e in qa["scenes"] if not e["qa"]["passed"]}},
        "video": video, "storyboard_duration": sb.total_duration,
        "probed_duration": probe.get("duration"), "probed_video": probe.get("video"),
        "audio_stream": audio["audio_stream"],
        "duration_matches": (probe.get("duration") is not None
                             and abs(probe["duration"] - sb.total_duration) < 0.5),
        "publication_audit": {"final": audit["final"], "failed_checks": audit["failed_checks"],
                              "content_checks_passed": content_checks_passed(audit)},
        "scenes": [s.kind for s in sb.scenes]})
    return {"scenario": sid, "synthetic": synthetic, "what": what,
            "profile": sb.publication_profile, "duration": sb.total_duration,
            "scenes": [s.kind for s in sb.scenes], "freeze_frame_qa": qa["passed"],
            "qa_issues": {e["scene"]: e["qa"]["issues"] for e in qa["scenes"] if not e["qa"]["passed"]},
            "publication_audit": {"final": audit["final"], "failed": audit["failed_checks"]},
            "video": video}


# --------------------------------------------------------------------------- blocked attempts
def blocked_attempts() -> dict:
    """I / J / P / Q: content the gate must refuse - recorded, never rendered."""
    from publication import PublicationGate, evaluate
    from publication import classify as C
    from publication.classification import (ContentClass, Orientation, Origin, PublishableFact,
                                            RightsStatus, Scope)
    ipo = dict(scope=Scope.IPO, origin=Origin.OFFICIAL_EXCHANGE, content_class=ContentClass.IPO_EVENT,
               orientation=Orientation.CURRENT_FACT, source_name="nse_ipo_issues",
               source_reference="synthetic://nse", data_as_of=SESSION, security="DEMO IPO A LTD",
               publication_rights_status=RightsStatus.REVIEW_REQUIRED)
    cases = {
        "I_radar_selected_stock_no_official_event": C.radar_story_fact(
            "STOCK-001", "STOCK-001 closed above its 20-day high on 4.1x normal volume", SESSION),
        "J_stock_technical_analysis_without_price": C.radar_story_fact(
            "STOCK-001", "STOCK-001: trading range has tightened", SESSION),
        "J_stock_to_watch_title": PublishableFact(
            fact_id="title", text="Stock to Watch: STOCK-001 breakout", scope=Scope.SECURITY,
            origin=Origin.INTERNAL_ANALYTICS, content_class=ContentClass.TECHNICAL_ANALYSIS,
            orientation=Orientation.FORWARD_LOOKING, source_name="daily_byte_derived",
            data_as_of=SESSION, security="STOCK-001",
            publication_rights_status=RightsStatus.APPROVED),
        "P_gmp": PublishableFact(fact_id="gmp", text="DEMO IPO A LTD GMP Rs 45",
                                 tags=frozenset({"GMP"}), **ipo),
        "Q_ipo_apply": PublishableFact(fact_id="apply", text="Apply for DEMO IPO A LTD - strong "
                                                            "listing expected", **ipo),
        "Q_ipo_fair_value": PublishableFact(fact_id="fv", text="DEMO IPO A LTD fair value Rs 400",
                                            **ipo),
    }
    known = {"STOCK-001": "Demo Company STOCK 001 Ltd."}
    out = {k: evaluate(f, "PUBLIC_UNREGISTERED", known).to_dict() for k, f in cases.items()}
    out["_private_profile_keeps_I"] = evaluate(cases["I_radar_selected_stock_no_official_event"],
                                               "PRIVATE_ANALYTICS", known).to_dict()
    assert all(not v["allowed"] for k, v in out.items() if not k.startswith("_"))
    return out


SCENARIOS = ["POST_REAL_2026-09-24", "PRE_REAL_2026-09-25", "REAL_OFFICIAL_LISTS",
             "POST_REAL_2026-09-21", "POST_REAL_2026-09-21_PRIVATE", *SYNTHETIC_POST,
             "SYNTHETIC_PRE_PUBLIC", "SYNTHETIC_PRE_MISSING_SUBSCRIPTION"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--mp4", nargs="*", default=[], help="scenario ids to also render as MP4")
    ap.add_argument("--fetch-live", action="store_true",
                    help="REAL_OFFICIAL_LISTS / PRE_REAL: read today's official lists and "
                         "Yahoo bars (read-only); otherwise cached lists are reused")
    args = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    summary = []
    for sid in args.only or SCENARIOS:
        mp4 = sid in args.mp4
        try:
            if sid == "POST_REAL_2026-09-24":
                res = render_real_post_24(sid, mp4)
            elif sid == "PRE_REAL_2026-09-25":
                if not args.fetch_live:
                    summary.append({"scenario": sid, "skipped": "needs --fetch-live"})
                    continue
                res = render_real_pre(sid, mp4)
            elif sid == "REAL_OFFICIAL_LISTS":
                res = render_real_official(sid, args.fetch_live, mp4)
            elif sid == "POST_REAL_2026-09-21":
                if not os.path.exists(REAL_REPORT):
                    summary.append({"scenario": sid, "skipped": "real artifacts not present"})
                    continue
                res = render_real_post(sid, mp4)
            elif sid == "POST_REAL_2026-09-21_PRIVATE":
                if not os.path.exists(REAL_REPORT):
                    continue
                res = render_real_post(sid, False, profile="PRIVATE_ANALYTICS")
            elif sid in SYNTHETIC_POST:
                res = render_synthetic_post(sid, SYNTHETIC_POST[sid], mp4)
            elif sid == "SYNTHETIC_PRE_PUBLIC":
                res = render_pre(sid, "RISK_OFF", mp4, "FNO_BAN", "BOARD")
            elif sid == "SYNTHETIC_PRE_MISSING_SUBSCRIPTION":
                res = render_pre(sid, "QUIET", mp4, None, "MISSING_SUBSCRIPTION")
            else:
                continue
        except Exception as exc:
            res = {"scenario": sid, "error": f"{type(exc).__name__}: {exc}"}
        summary.append(res)
        print(json.dumps({k: res.get(k) for k in ("scenario", "profile", "duration",
                                                   "freeze_frame_qa", "publication_audit",
                                                   "error", "blocked")}, default=str))
    _dump(os.path.join(OUT, "blocked_attempts.json"), blocked_attempts())
    _dump(os.path.join(OUT, "summary.json"), summary)
    return 0 if all(not s.get("error") for s in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
