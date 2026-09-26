"""Daily Byte - ~75s YouTube Short recapping the previous Indian market session.

Usage:
    python main.py              # build today's video (real data)
    python main.py --upload     # build + upload to YouTube (skips if yesterday was a holiday)
    python main.py --demo       # offline test with synthetic data (marked DEMO on screen)
    python main.py --force      # ignore holiday / already-posted checks
    python main.py --mode report [--session-date D]    # canonical report only, no video
    python main.py --mode premarket --shadow            # PRE shadow run (never uploads)

POST renders from the canonical report the REPORT job already built for the session; it builds
one itself (same code path, `produce_report`) only when none exists. See
docs/PRODUCTION_SCHEDULE.md.
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
import intelligence
from core import MarketReport
from core.content_safety import (CONTENT_SAFETY_VERSION, SafetyStatus,
                                 sanitize_field, scan_publication)
from presentation import ReportPresentation
from providers import GeminiProvider, NewsProvider, NseProvider, YahooProvider
from qa import check_video, write_qa_artifact
from qa.readability_qa import check_plan as check_readability
from operations.report_lookup import (ARTIFACT_MISSING, SESSION_MISMATCH, UNREADABLE,
                                      find_canonical_report)
from operations.sessions import latest_final_session, next_session
from storage import JOB_POST_MARKET, MarketHistory, default_db_path

RS = video.RS


# ----------------------------------------------------------------------------- LEGACY
# LEGACY / NON-PRODUCTION: ticker_items, build_metadata, build_public_metadata and
# readability_qa served the legacy video.py Short. Since the production cut-over main.run
# renders POST_UNIFIED (products.post_unified) and calls none of them; they remain only for
# old artifacts / tests and must not be wired back into the scheduled path.
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


def build_public_metadata(m, plan, info):
    """PUBLIC_UNREGISTERED title/description/tags: built only from what the public plan shows
    (presentation.legacy_public.public_metadata) - no stock names, no rankings."""
    from presentation.legacy_public import public_metadata
    return public_metadata(m, plan, info)


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


def collect_public_text(plan, meta) -> dict:
    # `plan`: anything with public_text() - the POST_UNIFIED storyboard in production
    """Every string that will appear to a viewer, taken from the editorial plan itself.

    The plan is the script, so it is the complete and authoritative list of on-screen text -
    including the hook and every context statement. Reading it here rather than introspecting
    rendered scenes means nothing can reach the screen without passing the safety scan.
    """
    # The registered disclaimer names the words it disclaims ("buy/sell/hold, target,
    # stop-loss"); it - and only it, verbatim - is exempt. Everything else is scanned.
    from publication.disclaimer import strip_registered
    fields = {"youtube_title": meta["title"],
              "youtube_description": strip_registered(meta["description"])}
    fields.update(plan.public_text())
    return fields


def summarize_content_safety(pre_findings) -> dict:
    """Stage A: what sanitisation did to the narrative BEFORE the report was finalized.

    This is canonical - it describes the content of the report itself, so it is written into
    MarketReport.content_safety once and never revised. The final publication scan is
    deliberately absent: that happens after the report exists, describes an execution rather
    than the market, and belongs in publication_runs and the QA artifact instead.
    """
    findings = [{"field": label, **r.to_dict()} for label, r in pre_findings]
    sanitized = sum(1 for _, r in pre_findings if r.status is SafetyStatus.SANITIZED)
    blocked = sum(1 for _, r in pre_findings if r.status is SafetyStatus.BLOCKED)
    return {
        "stage": "PRE_REPORT_SANITISATION",
        "status": "SANITIZED" if sanitized else "SAFE",
        "sanitized_count": sanitized, "blocked_count": blocked,
        "findings": findings, "version": CONTENT_SAFETY_VERSION,
    }


def operational_content_qa(scan) -> dict:
    """Stage B: the final publication scan, as an operational result.

    Never written into the canonical report - by the time this runs the report is persisted
    and immutable, and whether an upload was allowed is not a fact about the market.
    """
    return {"stage": "FINAL_PUBLICATION_SCAN", "status": scan.status.value,
            "passed": scan.status is SafetyStatus.SAFE,
            "blocked_fields": list(scan.blocked_fields),
            "version": CONTENT_SAFETY_VERSION}


# ----------------------------------------------------------------------------- main
def plan_short(report, snapshot, now=None, profile=None):
    """The editorial script for this Short: what is said, in what order, for how long.

    Durations come from how much there is to read, not from a fixed 75-second budget, and
    every candidate hook and statement is content-checked here rather than after rendering.
    Reads the report and snapshot; writes to neither.
    """
    from products import post_unified
    return post_unified.plan_for_post(report, snapshot, now=now, profile=profile)


def collect(args, today, morning_facts=True):
    """Acquisition. Returns the raw session pieces, or None when the run should be skipped.

    This is the only place provider output exists as loose dictionaries; everything after
    build_report() reads the canonical report instead. `morning_facts=False` (a report built
    the evening before the morning it is for) drops Gemini's "GIFT Nifty this morning" - that
    reading cannot exist yet, so it is never accepted rather than trusted to be null.
    """
    if args.demo:
        m, gainers, losers, events, nifty_reason, tiles, fd, sec = demo_data()
        return {"m": m, "gainers": gainers, "losers": losers, "events": events,
                "nifty_reason": nifty_reason, "tiles": tiles, "fd": fd, "sec": sec,
                "ai_facts": {}, "nse_idx": {},
                # Synthetic universe: complete by construction, and labelled as such.
                "movers_coverage": {"universe_expected": len(gainers) + len(losers),
                                    "universe_observed": len(gainers) + len(losers),
                                    "universe_validated": len(gainers) + len(losers),
                                    "coverage_pct": 100.0, "excluded": [], "demo": True,
                                    "coverage_basis": "validated / expected"}}

    print("[1/6] Fetching Nifty data...")
    m = market.get_market()
    prev_wd = today - dt.timedelta(days={0: 3, 6: 2}.get(today.weekday(), 1))
    state_file = os.path.join(OUT_DIR, "last_session.txt")
    if args.upload and not args.force:
        if m["recap_date"] != prev_wd:
            if (m["recap_date"] < prev_wd
                    and market.session_calendar(m.get("session_dates")).is_session(prev_wd)):
                # Not a holiday: NSE traded on prev_wd, and neither the primary benchmark nor
                # NSE's official end-of-day file (`market.recover_recap_session`) could establish
                # it. That is a failure to describe a real session, not a quiet day - block
                # publication loudly (never a silent "holiday" skip, never an older session).
                recs = [r for r in ((m.get("session_alignment") or {}).get("benchmark_recovery")
                                    or []) if r.get("session_date") == prev_wd.isoformat()]
                tried = (f"{recs[0].get('source')}: {recs[0].get('validation_status')}" if recs
                         else "no official fallback record")
                raise market.SessionAlignmentError(
                    f"BENCHMARK_MISSING_RECAP_SESSION: {prev_wd} was a real NSE session but the "
                    f"benchmark ends at {m['recap_date']} (primary missing; {tried}) - "
                    "publication blocked")
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
    sector_recovery = []
    sec = market.get_sectors(m["recap_date"], m["prev_date"], idx,
                             session_dates=m.get("session_dates"), recovery_log=sector_recovery)
    if m.get("session_alignment") is not None:
        m["session_alignment"].setdefault("index_recovery", []).extend(sector_recovery)
    _report_benchmark_fallback(m)
    tiles = market.get_globals()

    print("[3/6] Top gainers & losers...")
    universe = market.get_universe(UNIVERSE)
    gainers, losers, movers_coverage = market.get_movers_audited(
        universe, m["recap_date"], m["prev_date"], TOP_N, session_dates=m.get("session_dates"))
    print(f"      mover coverage {movers_coverage['universe_validated']}/"
          f"{movers_coverage['universe_expected']} ({movers_coverage['coverage_pct']}%)")

    print("[4/6] AI cross-check, reasons & events (single Gemini call)...")
    ai_facts, nifty_reason, events = news.ai_pass(m["recap_date"], today, m["close"],
                                                  gainers, losers, m)
    # Legacy cross-check, kept as defence in depth. The authoritative verdict on whether
    # these sources agree is now the INDEX_CLOSE fact's CrossSourceValidator result.
    ref = idx.get("NIFTY 50", {}).get("last") or ai_facts.get("nifty_close")
    if ref:
        diff = abs(ref / m["close"] - 1) * 100
        print(f"      Nifty check: {m.get('close_source') or 'yahoo'} {m['close']:.2f} vs "
              f"{ref:.2f} ({diff:.2f}% diff)")
        if diff > 0.2:
            raise RuntimeError("Nifty close mismatch between sources - not posting wrong numbers")
    else:
        print("      WARNING: no second source for Nifty close available today")
    fd = fd or ai_facts.get("fii_dii")
    if not morning_facts and ai_facts.pop("gift", None) is not None:
        print("      GIFT Nifty (Gemini) dropped: an evening build cannot observe the next morning")
    if ai_facts.get("gift"):
        tiles.insert(0, {"label": "GIFT NIFTY", "value": ai_facts["gift"]["value"],
                         "pct": ai_facts["gift"]["pct"], "dec": 0, "prefix": ""})
    if ai_facts.get("brent"):
        tiles.append({"label": "BRENT CRUDE", "value": ai_facts["brent"]["value"],
                      "pct": ai_facts["brent"]["pct"], "dec": 2, "prefix": "$"})
    return {"m": m, "gainers": gainers, "losers": losers, "events": events,
            "nifty_reason": nifty_reason, "tiles": tiles, "fd": fd, "sec": sec,
            "ai_facts": ai_facts, "nse_idx": idx, "movers_coverage": movers_coverage}


def _report_benchmark_fallback(m):
    """Make a second-source benchmark/index value impossible to miss in the run log."""
    align = m.get("session_alignment") or {}
    recs = list(align.get("benchmark_recovery") or []) + list(align.get("index_recovery") or [])
    for r in recs:
        print(f"      FALLBACK {r['index']} {r['session_date']}: {r['source']} "
              f"-> {r['validation_status']}")
    if align.get("previous_close_source") not in (None, "yahoo"):
        print(f"      Nifty previous close ({m['prev_date']}) from "
              f"{align.get('previous_close_source')}: {m['prev']:.2f}")
    if align.get("recap_close_source") not in (None, "yahoo"):
        print(f"      Nifty recap close ({m['recap_date']}) from "
              f"{align.get('recap_close_source')}: {m['close']:.2f}")


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
        content_safety=content_safety, movers_coverage=raw.get("movers_coverage"),
        session_alignment=raw["m"].get("session_alignment"))


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


def readability_qa(plan, upload_requested):
    """Could a viewer read this at normal speed? Deterministic, and a publication gate."""
    result = check_readability(plan)
    if result.passed:
        note = f" ({len(result.warnings)} warning(s))" if result.warnings else ""
        print(f"      readability QA: {result.status}{note}")
    else:
        print(f"      readability QA FAILED ({len(result.blocking_issues)} blocking):")
        for issue in result.blocking_issues:
            print(f"  - {issue}")
        if upload_requested:
            print("Upload blocked by readability QA.")
    for warning in result.warnings:
        print(f"      readability warning: {warning}")
    return result


def final_qa(plan, meta, out, upload_requested):
    """Re-scan every finalized public-facing string immediately before publication.

    Text is already burned into video frames and written into metadata by this point, so
    nothing is rewritten here - anything not SAFE blocks publication instead.
    """
    scan = scan_publication(collect_public_text(plan, meta))
    if scan.status is not SafetyStatus.SAFE:
        print("Content-safety QA FAILED - the following fields still contain unsafe text:")
        for field_name in scan.blocked_fields:
            r = scan.results[field_name]
            print(f"  - {field_name}: {r.status.value} ({r.reason})")
        print(f"Local artifacts preserved for inspection: {out}")
        if upload_requested:
            print("Upload blocked by content-safety QA.")
    return scan


def may_become_canonical(report, demo=False) -> bool:
    """THE canonical-storage rule (PRE shadow readiness, owner-approved): a MarketReport that
    fails data validation never becomes canonical - whichever job built it (the evening REPORT
    job or POST's inline fallback). It is written to reports/unfit/ for diagnosis, the run is
    BLOCKED, and a later retry may build a fit report for the same session. A transient data gap
    must not permanently lock an invalid canonical record (canonical history is immutable).
    Demo reports are exempt: they are stored with is_demo=1 and excluded from every query."""
    return bool(demo) or bool(report.publication_ready)


def persist_report(report, artifact_path, demo=False, history=None):
    """Record the report in the historical index. Returns (ok, error).

    Only a report that `may_become_canonical` is indexed; an unfit report is refused here as
    well as upstream (defence in depth - this is the only route a recap report takes into
    history). "Why wasn't the 22 Sep report published?" stays answerable: the unfit JSON is
    kept under reports/unfit/ and the run row records its blocking issues and artifact path.

    Demo runs are written with is_demo=1 and excluded from every history query by default,
    so synthetic fixtures cannot contaminate real market history while still being available
    for debugging the pipeline itself.
    """
    if not may_become_canonical(report, demo):
        reason = ("refused: the report fails data validation and may not become canonical ("
                  + "; ".join(report.validation_summary.blocking_issues) + ")")
        print(f"      history: {reason}")
        return False, reason
    try:
        owned = history is None
        history = history or MarketHistory(default_db_path(OUT_DIR))
        try:
            written = history.save_report(report, artifact_path=artifact_path, is_demo=demo)
            print(f"      history: {'stored' if written else 'already stored'} {report.report_id}"
                  f"{' (demo)' if demo else ''}")
            return True, None
        finally:
            if owned:
                history.close()
    except Exception as exc:
        # Auditability is part of publication integrity: the caller refuses to upload.
        print(f"      history: FAILED to persist {report.report_id}: {type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"


def adopt_existing_report(report, history):
    """If this report_id is already canonical, reuse the stored artifact instead of rewriting.

    Immutability has to hold across runs, not only within one. A rerun regenerates a report
    in memory that can differ from the persisted one - a later `generated_at`, a source that
    has since come back, a headline that has moved on - and writing that over the artifact
    SQLite points at would leave the index describing a file that no longer matches it.

    So on a rerun the stored artifact wins: it is loaded and used for rendering, so the video
    is built from the same canonical data the history already records.

    Returns `(report, path, error)`. `(None, None, None)` means this report has not been
    stored before and the caller should write it normally. A non-None `error` means the
    canonical record is unusable and the run must stop rather than paper over it.
    """
    if history is None:
        return None, None, None
    try:
        stored = history.get_report(report.report_id)
    except Exception as exc:
        return None, None, f"could not read canonical history: {type(exc).__name__}: {exc}"
    if stored is None:
        return None, None, None

    path = stored.json_artifact_path
    if not path or not os.path.exists(path):
        return None, None, (f"history records canonical report {report.report_id} but its JSON "
                            f"artifact is missing: {path!r}")
    try:
        adopted = MarketReport.from_json(open(path, encoding="utf-8").read())
    except Exception as exc:
        return None, None, (f"canonical artifact {path} for {report.report_id} is unreadable: "
                            f"{type(exc).__name__}: {exc}")
    if adopted.report_id != report.report_id:
        return None, None, (f"canonical artifact {path} contains report_id "
                            f"{adopted.report_id!r}, expected {report.report_id!r}")
    return adopted, path, None


def build_intelligence(report, history, demo=False):
    """Derive historical context and write it as its own artifact. Never fatal.

    Historical context is an enhancement, not part of market-data validation: if the history
    database cannot be read the day's recap still publishes, simply without context. What
    must never happen is fabricating context to fill the gap, so a failure produces an empty
    snapshot and a recorded warning rather than a plausible-looking statement.

    Reads the canonical report and canonical history; writes neither.
    """
    try:
        snapshot = intelligence.build_snapshot(report, history, include_demo=demo)
        path = intelligence.save_snapshot(snapshot, OUT_DIR, demo=demo)
        print(f"      intelligence: {intelligence.describe(snapshot)} "
              f"-> {os.path.basename(path)}")
        for warning in snapshot.warnings[:3]:
            print(f"      intelligence note: {warning}")
        return snapshot
    except Exception as exc:
        print(f"      intelligence: skipped after {type(exc).__name__}: {exc}")
        return None


def video_qa(out, meta_path, report_path, expected_duration, upload_requested):
    """Deterministic artifact QA. Failure blocks publication but preserves every artifact."""
    result = check_video(out, expected_duration=expected_duration, metadata_path=meta_path,
                         report_path=report_path, expect_audio=False)
    if result.passed:
        note = f" ({len(result.warnings)} warning(s))" if result.warnings else ""
        print(f"      video QA: {result.status.value}{note}")
    else:
        print(f"      video QA FAILED ({len(result.blocking_issues)} blocking):")
        for issue in result.blocking_issues:
            print(f"  - {issue}")
        print(f"Local artifacts preserved for inspection: {out}")
        if upload_requested:
            print("Upload blocked by video QA.")
    for warning in result.warnings:
        print(f"      video QA warning: {warning}")
    return result


def publish(out, meta, session_date, audit):
    """`audit`: the PASS publication_audit.json for exactly this file - upload.upload refuses
    anything else (publication.audit.require_publication_pass)."""
    import upload
    vid = upload.upload(out, meta, audit)
    print(f"Uploaded: https://youtube.com/shorts/{vid}")
    with open(os.path.join(OUT_DIR, "last_session.txt"), "w") as f:
        f.write(str(session_date))
    return vid


# ----------------------------------------------------------------------------- report production
class ReportOutcome:
    """What `produce_report` / `obtain_post_report` ended with. `report`/`report_path` are set
    only when a canonical report is in hand (freshly persisted, adopted or reused); otherwise the
    run has already been closed in history with the reason, and `status` says why."""

    def __init__(self, status, report=None, report_path=None, reason="", source=None):
        self.status, self.report, self.report_path = status, report, report_path
        self.reason, self.source = reason, source

    @property
    def ok(self) -> bool:
        return self.report is not None


def produce_report(args, today, history, run_id, *, expected_session=None, morning_facts=True):
    """Acquire -> content safety -> build -> adopt-or-persist: the ONE report-building path.

    Used by POST (`obtain_post_report`, when no canonical report exists yet) and by the REPORT
    job (`products.report_job`). Both obey the same canonical-storage rule
    (`may_become_canonical`): a report that fails data validation is written to reports/unfit/
    for diagnosis, the run is BLOCKED, and nothing canonical is written - a retry can still
    build a fit report for the session once the data settles. `expected_session` makes a
    benchmark that ends on another session a hard stop BEFORE anything is persisted.
    `morning_facts=False` (a report built the evening before its edition) drops readings that
    only exist on the edition morning (Gemini's "GIFT Nifty this morning").

    Raises market.SessionAlignmentError after recording it, exactly as before.
    """
    try:
        raw = collect(args, today, morning_facts=morning_facts)
    except market.SessionAlignmentError as exc:
        print(f"Publication blocked: {exc}")
        _finish(history, run_id, "FAILED", "BLOCKED", failure_stage="SESSION_ALIGNMENT",
                failure_reason=str(exc)[:1000], run_status="BLOCKED")
        raise
    if raw is None:
        _finish(history, run_id, "NO_UPLOAD", "SKIPPED", failure_stage="COLLECT",
                failure_reason="holiday or already posted", run_status="SKIPPED")
        return ReportOutcome("SKIPPED", reason="holiday or already posted")
    recap = raw["m"]["recap_date"]
    if expected_session is not None and recap != expected_session and not args.demo:
        if recap < expected_session:
            exc = market.SessionAlignmentError(
                f"BENCHMARK_MISSING_RECAP_SESSION: {expected_session} is the latest final NSE "
                f"session but the benchmark ends at {recap} - the report is not built from an "
                "older session")
            print(f"Report blocked: {exc}")
            _finish(history, run_id, "FAILED", "BLOCKED", failure_stage="SESSION_ALIGNMENT",
                    failure_reason=str(exc)[:1000], run_status="BLOCKED")
            raise exc
        reason = (f"HISTORICAL_REBUILD_UNSUPPORTED: requested {expected_session}, but live "
                  f"acquisition describes {recap} - a past session's report is never rebuilt")
        print(f"Report blocked: {reason}")
        _finish(history, run_id, "FAILED", "BLOCKED", failure_stage="SESSION_RESOLUTION",
                failure_reason=reason, run_status="BLOCKED")
        return ReportOutcome("BLOCKED", reason=reason)
    _stage(history, run_id, "COLLECTED", target_date=recap, source_session_date=recap)

    # Content safety runs before the report is built, so the report stores publishable
    # text and presentation inherits it rather than being cleaned separately downstream.
    (raw["nifty_reason"], raw["gainers"], raw["losers"], raw["events"],
     safety_findings) = apply_content_safety(raw["nifty_reason"], raw["gainers"],
                                             raw["losers"], raw["events"])

    print("[5/7] Building validated market report...")
    # The report is FINALIZED here: content safety has already run, and nothing after
    # this point may alter its facts, observations, validation, catalysts or provenance.
    # Its report_date is the edition it recaps the session for (the next canonical session,
    # which is the run date of an ordinary 07:40 run), so an evening build and a next-morning
    # build of the same session share one report_id.
    edition = today if args.demo else (next_session(recap) or today)
    report = build_report(raw, edition, demo=args.demo,
                          content_safety=summarize_content_safety(safety_findings))
    print(f"      report: {report_builder.describe(report)}")
    _stage(history, run_id, "REPORT_BUILT", report_id=report.report_id)

    # A rerun of an already-canonical report adopts the stored artifact rather than
    # regenerating over it; a first run writes the JSON and then indexes it. Either way
    # the canonical artifact is written exactly once, ever.
    adopted, adopted_path, artifact_error = adopt_existing_report(report, history)
    if artifact_error:
        print(f"Publication blocked: {artifact_error}")
        _finish(history, run_id, "FAILED", "BLOCKED", failure_stage="CANONICAL_ARTIFACT",
                failure_reason=artifact_error, run_status="BLOCKED")
        return ReportOutcome("BLOCKED", reason=artifact_error)

    if adopted is not None:
        report, report_path = adopted, adopted_path
        print(f"      canonical report {report.report_id} already exists - rendering from "
              f"the stored artifact, not regenerating it")
        _stage(history, run_id, "PERSISTED", report_id=report.report_id,
               artifact_path=report_path)
        return ReportOutcome("ADOPTED", report, report_path, source="ADOPTED_EXISTING")

    if not may_become_canonical(report, args.demo):
        # Not canonical: a retry may still produce a fit report once the data settles.
        path = report_builder.save_unfit_report(report, OUT_DIR)
        reason = "; ".join(report.validation_summary.blocking_issues)
        print(f"Report NOT committed (fails data validation, retryable): {reason}")
        print(f"      diagnostic artifact: {path}")
        _finish(history, run_id, "DATA_QA_FAILED", "BLOCKED", failure_stage="DATA_QA",
                failure_reason=reason, data_qa_status="FAILED", artifact_path=path,
                run_status="BLOCKED")
        return ReportOutcome("UNFIT", report_path=path, reason=reason)

    # JSON artifact first, then the historical index: the file is the immutable record
    # of what this run produced, and the database is an index over those files.
    report_path = report_builder.save_report(report, OUT_DIR, demo=args.demo)
    ok, error = persist_report(report, report_path, demo=args.demo, history=history)
    if not ok:
        _finish(history, run_id, "FAILED", "BLOCKED", failure_stage="PERSIST",
                failure_reason=error, artifact_path=report_path, run_status="FAILED")
        print("Publication blocked: the run could not be recorded in history.")
        return ReportOutcome("PERSIST_FAILED", reason=error)
    _stage(history, run_id, "PERSISTED", artifact_path=report_path)
    return ReportOutcome("BUILT", report, report_path, source="BUILT")


def _post_skip_reason(args, today, session):
    """The POST publication preconditions, applied to a calendar-resolved session exactly as
    `collect` applies them to the benchmark's: skip after a holiday, skip if already posted."""
    if not args.upload or args.force:
        return None
    prev_wd = today - dt.timedelta(days={0: 3, 6: 2}.get(today.weekday(), 1))
    if session != prev_wd:
        return f"Latest session is {session}, expected {prev_wd} (market holiday?). Skipping."
    state_file = os.path.join(OUT_DIR, "last_session.txt")
    if os.path.exists(state_file) and open(state_file).read().strip() == str(session):
        return f"Session {session} already posted. Skipping."
    return None


def obtain_post_report(args, today, history, run_id):
    """POST consumes the canonical report the REPORT job built; it builds one only when none
    exists (production policy: never cost a day's publication, recorded as
    report_source=BUILT_INLINE). Returns a ReportOutcome; a non-ok one is already recorded."""
    session = None if args.demo else latest_final_session(now_ist())
    replay = getattr(args, "session_date", None)
    if replay and not args.demo:
        # a replay renders an ALREADY-BUILT canonical report for that session - it never
        # rebuilds one and never uploads (an old session is never published as today's)
        if args.upload:
            print("Refused: --session-date is a replay of a past session; it never uploads")
            _finish(history, run_id, "NO_UPLOAD", "BLOCKED", failure_stage="COLLECT",
                    failure_reason="replay with --upload", run_status="BLOCKED")
            return ReportOutcome("BLOCKED", reason="replay with --upload")
        session = dt.date.fromisoformat(str(replay))
        lookup = find_canonical_report(session, history=history)
        if not lookup.found:
            _finish(history, run_id, "FAILED", "BLOCKED", failure_stage="CANONICAL_ARTIFACT",
                    failure_reason=f"no canonical report for {session}: {lookup.reason}",
                    run_status="BLOCKED", target_date=session, source_session_date=session)
            return ReportOutcome("BLOCKED", reason=lookup.reason)
        _stage(history, run_id, "PERSISTED", report_id=lookup.report_id,
               artifact_path=lookup.path,
               details={"report_source": "REUSED_CANONICAL", "replay": True,
                        "lookup": lookup.to_dict()})
        print(f"[1/7] REPLAY of canonical report for {session} ({lookup.report_id})")
        return ReportOutcome("REUSED", lookup.report, lookup.path, source="REUSED_CANONICAL")
    if session is not None and history is not None:
        lookup = find_canonical_report(session, history=history)
        if lookup.found:
            skip = _post_skip_reason(args, today, session)
            if skip:
                print(skip)
                _finish(history, run_id, "NO_UPLOAD", "SKIPPED", failure_stage="COLLECT",
                        failure_reason="holiday or already posted", run_status="SKIPPED",
                        target_date=session, source_session_date=session)
                return ReportOutcome("SKIPPED", reason=skip)
            print(f"[1/7] Canonical report for {session} already built ({lookup.report_id}) - "
                  "POST renders from it; nothing is re-acquired")
            _stage(history, run_id, "PERSISTED", report_id=lookup.report_id,
                   artifact_path=lookup.path,
                   details={"report_source": "REUSED_CANONICAL", "lookup": lookup.to_dict()})
            return ReportOutcome("REUSED", lookup.report, lookup.path, source="REUSED_CANONICAL")
        if lookup.status in (ARTIFACT_MISSING, UNREADABLE, SESSION_MISMATCH):
            print(f"Publication blocked: canonical report for {session} is unusable: "
                  f"{lookup.reason}")
            _finish(history, run_id, "FAILED", "BLOCKED", failure_stage="CANONICAL_ARTIFACT",
                    failure_reason=lookup.reason, run_status="BLOCKED",
                    target_date=session, source_session_date=session)
            return ReportOutcome("BLOCKED", reason=lookup.reason)
        print(f"      no canonical report for {session} yet (REPORT job missing or failed) - "
              "POST builds it with the same code path")
    outcome = produce_report(args, today, history, run_id)
    if outcome.ok:
        if outcome.source == "BUILT" and session is not None:
            outcome.source = "BUILT_INLINE"
        _stage(history, run_id, "PERSISTED", details={"report_source": outcome.source})
    return outcome


def publication_audit(sb, meta, out, report, args, tag):
    """Gate 5: the per-video publication audit of the POST_UNIFIED storyboard (claims,
    provenance, universe, language, rights). PASS or BLOCK; a BLOCK makes the upload
    impossible (upload.upload re-checks it against the file)."""
    from daily_video.composer import AUDIO_ENABLED
    from daily_video.public_storyboard import audit_storyboard
    from publication import write_publication_audit
    from qa.video_qa import probe_media
    probe = probe_media(out) if os.path.exists(out) else {}
    audit = audit_storyboard(sb, "POST_UNIFIED", metadata=meta, video_path=out,
                             synthetic=bool(args.demo),
                             audio={"audio_stream": bool(probe.get("audio")),
                                    "audio_phase_enabled": AUDIO_ENABLED})
    path = write_publication_audit(audit, os.path.join(OUT_DIR, "publication", tag))
    print(f"      publication audit: {audit['final']}"
          + (f" (failed: {', '.join(audit['failed_checks'])})" if audit["failed_checks"] else "")
          + f" -> {os.path.relpath(path, OUT_DIR)}")
    return audit, path


class _FrameQA:
    """The freeze-frame layout QA of the unified storyboard, in the shape the run's
    readability slot records (the legacy per-scene reading budget does not apply to it)."""

    def __init__(self, frames_qa: dict):
        self.passed = bool(frames_qa.get("passed"))
        self.blocking_issues = [f"{k}: {v}" for k, v in (frames_qa.get("issues") or {}).items()]
        self.warnings = []
        self.status = "PASS" if self.passed else "FAIL"

    def to_dict(self) -> dict:
        return {"kind": "FREEZE_FRAME_QA", "status": self.status, "passed": self.passed,
                "blocking_issues": self.blocking_issues}


def run(args):
    os.makedirs(OUT_DIR, exist_ok=True)
    today = now_ist().date()
    mode = "DEMO" if args.demo else ("PRODUCTION" if args.upload else "LOCAL")
    history, run_id = _open_history(mode)

    try:
        outcome = obtain_post_report(args, today, history, run_id)
        if not outcome.ok:
            return None
        report, report_path = outcome.report, outcome.report_path
        _stage(history, run_id, "PERSISTED", target_date=report.session_date,
               source_session_date=report.session_date)

        # Gate 1 of 3: data validation.
        if not check_publication(report, demo=args.demo):
            _finish(history, run_id, "DATA_QA_FAILED", "BLOCKED", failure_stage="DATA_QA",
                    failure_reason="; ".join(report.validation_summary.blocking_issues),
                    data_qa_status="FAILED", artifact_path=report_path, run_status="BLOCKED")
            print(f"Report written for diagnosis (no video rendered): {report_path}")
            return None
        _stage(history, run_id, "DATA_QA_PASSED", data_qa_status="PASSED")

        # Derived historical context, read from canonical history after it was persisted.
        # It cannot alter the report or its rows, and its absence never blocks the run.
        snapshot = build_intelligence(report, history, demo=args.demo)

        # The editorial plan is the video's script: what earns screen time, what opens it,
        # and how long each scene needs to be readable. Derived from the report and the
        # snapshot, it changes neither.
        from publication import resolve_profile
        profile = resolve_profile(getattr(args, "profile", None))
        public = profile.is_public
        plan = plan_short(report, snapshot, now=now_ist(), profile=profile)
        print(f"      editorial: {len(plan.scenes)} scenes, {plan.total_duration:.1f}s "
              f"| hook: {plan.hook.primary_text} {plan.hook.primary_value}".rstrip())

        # ---- POST_UNIFIED (production cut-over): the SAME product the review renderer
        # builds - products.post_unified. The legacy video.py Short is non-production.
        from products import post_unified as PU
        from presentation.public_intelligence import load_public_intelligence
        edition = report.report_date or today
        session = report.session_date or edition
        replay = bool(getattr(args, "session_date", None))
        tag = (f"replay_{session}" if replay else today.strftime("%Y-%m-%d")) + \
            ("_DEMO" if args.demo else "")
        intel = load_public_intelligence(
            session, next_session(session) or edition, OUT_DIR,
            fetch=not (args.demo or replay or getattr(args, "no_fetch_public", False)),
            now_iso=dt.datetime.now(dt.timezone.utc).isoformat())
        sb, pres = PU.build_post_storyboard(
            report, plan, profile=profile, intelligence=intel,
            hook_ai=bool(getattr(args, "hook_ai", False)),
            radar_dir=os.path.join(OUT_DIR, "radar"), sources={"market_report": report_path})
        info = {"today_str": edition.strftime("%A, %d %B %Y"),
                "today_short": edition.strftime("%a %d %b"),
                "recap_str": pres.session_date.strftime("%a, %d %b %Y")}
        total = sb.total_duration
        print(f"[6/7] Rendering {PU.PRODUCT}: {total:.1f}s, {len(sb.scenes)} scenes "
              f"({', '.join(s.kind for s in sb.scenes)})")
        run_dir = os.path.join(OUT_DIR, "post", tag)
        os.makedirs(run_dir, exist_ok=True)
        out = os.path.join(OUT_DIR, f"daily_byte_{tag}.mp4")
        rendered = PU.render_post(sb, out, os.path.join(run_dir, "freeze_frames"),
                                  watermark=PU.DEMO_WATERMARK if args.demo else None)
        meta = PU.post_metadata(sb, pres, info)
        meta_path = out.replace(".mp4", ".json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        with open(os.path.join(run_dir, "storyboard.json"), "w", encoding="utf-8") as f:
            json.dump(sb.to_dict(), f, indent=2, ensure_ascii=False, default=str)
        _stage(history, run_id, "RENDERED", artifact_path=out)

        print("[7/7] Publication QA...")
        # Gates 2-5 are operational: they decide whether this artifact may be published and
        # record that decision in the QA artifact and publication_runs. None reopens the
        # canonical report, its JSON, or its rows.
        qa_result = video_qa(out, meta_path, report_path, total, args.upload)
        read_result = _FrameQA(rendered["frames_qa"])
        scan = final_qa(sb, meta, out, args.upload)
        content_ok = scan.status is SafetyStatus.SAFE
        audit, audit_path = publication_audit(sb, meta, out, report, args, tag)
        from publication.audit import content_checks_passed
        publication_ok = content_checks_passed(audit)          # the render itself is compliant
        if args.upload and not args.demo and audit["final"] != "PASS":
            publication_ok = False                             # rights etc. block the upload
        _merge_details(history, run_id, publication={
            "product": PU.PRODUCT, "profile": profile.value, "final": audit["final"],
            "failed_checks": audit["failed_checks"], "audit_path": audit_path,
            "facts_blocked": audit["facts_blocked"], "block_reasons": audit["block_reasons"],
            "optional_sections": audit.get("optional_sections")})

        # a replay never overwrites the QA record of the production run for that report
        qa_path = write_qa_artifact(qa_result, run_dir if replay else OUT_DIR, report,
                                    report_path=report_path,
                                    video_path=out, demo=args.demo,
                                    content_qa=operational_content_qa(scan),
                                    readability=read_result.to_dict(),
                                    editorial=plan.to_dict())
        print(f"      qa artifact: {os.path.basename(qa_path)}")

        cs = report.content_safety
        print(f"      report: {os.path.basename(report_path)} (unchanged) | sanitisation: "
              f"{cs['status']} sanitized={cs['sanitized_count']} blocked={cs['blocked_count']} "
              f"| final scan: {scan.status.value}")
        print(f"Done: {out}")

        _stage(history, run_id, "VIDEO_QA_PASSED" if qa_result.passed else "VIDEO_QA_FAILED",
               video_qa_status=qa_result.status.value,
               content_qa_status="PASSED" if content_ok else "FAILED")

        # RADAR_PUBLISHED is decided here, after the artifact and every QA gate - never by the
        # REPORT job's selection: only the Radar stories this MP4 actually shows (none publicly).
        _record_radar(history, run_id, report, out, demo=args.demo, storyboard=sb,
                      qa_passed=qa_result.passed and content_ok and read_result.passed,
                      qa={"video_qa": qa_result.status.value, "frames_qa": read_result.passed,
                          "content_qa": scan.status.value, "qa_artifact": qa_path})

        def _manifest(upload_status):
            doc = PU.manifest(entry_point="main.py -> products.route -> main.run",
                              report_source=outcome.source, report_id=report.report_id,
                              session=session, sb=sb, audit=audit, upload_status=upload_status,
                              video_path=out,
                              qa={"video_qa": qa_result.status.value,
                                  "video_qa_blocking": list(qa_result.blocking_issues),
                                  "frames_qa": rendered["frames_qa"],
                                  "content_qa": scan.status.value})
            with open(os.path.join(run_dir, "production_manifest.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False, default=str)
            return doc

        if not (qa_result.passed and content_ok and read_result.passed and publication_ok):
            stage = ("VIDEO_QA" if not qa_result.passed
                     else "FRAMES_QA" if not read_result.passed
                     else "CONTENT_QA" if not content_ok else "PUBLICATION_AUDIT")
            reason = (qa_result.blocking_issues or read_result.blocking_issues
                      or scan.blocked_fields or audit["failed_checks"])
            _manifest("REFUSED: " + stage + " - " + "; ".join(reason))
            _finish(history, run_id, "FAILED", "BLOCKED", failure_stage=stage,
                    failure_reason="; ".join(reason), artifact_path=out, run_status="BLOCKED")
            return out

        if args.upload and not args.demo:
            vid = publish(out, meta, pres.session_date, audit_path)
            _manifest(f"PUBLISHED: {vid}")
            _finish(history, run_id, "PUBLISHED", "PUBLISHED", artifact_path=out,
                    youtube_video_id=vid, run_status="SUCCESS")
        else:
            _manifest("NOT_ATTEMPTED (no --upload)"
                      + ("" if audit["final"] == "PASS" else
                         f"; an upload would be refused: {', '.join(audit['failed_checks'])}"))
            _finish(history, run_id, "NO_UPLOAD", "NOT_ATTEMPTED", artifact_path=out,
                    run_status="SUCCESS")
        return out
    except Exception as exc:
        # An unexpected crash still closes the run (a run left at COLLECTED says nothing).
        # A failed POST render never touches the canonical report it was rendering from.
        _fail_open_run(history, run_id, exc)
        raise
    finally:
        if history:
            history.close()


def _record_radar(history, run_id, report, artifact_path, *, qa_passed, qa, demo=False,
                  storyboard=None):
    """Record RADAR_SELECTED vs RADAR_PUBLISHED for this POST run (run details `radar`): only
    the Radar stories the rendered POST_UNIFIED storyboard SHOWS count - none under
    PUBLIC_UNREGISTERED (the gate keeps every Radar story private). Never fatal."""
    try:
        from products import radar_publication as rp
        session = report.session_date
        rendered = rp.rendered_radar_stories(storyboard) if storyboard is not None else []
        confirm = rp.confirm_radar_publication(
            session, rendered, qa_passed=qa_passed, out_dir=OUT_DIR, run_id=run_id,
            artifact_path=artifact_path, qa=qa, demo=demo, confirmed_by="main.run")
        sel = rp.radar_selection(session, OUT_DIR) if session else {}
        pub = rp.radar_publication(session, OUT_DIR) if session else {}
        record = {"selected_count": sel.get("selected_count", 0),
                  "selected_symbols": sel.get("selected_symbols", []),
                  "rendered_symbols": rendered,
                  "published_count": pub.get("published_count", 0),
                  "published_symbols": pub.get("published_symbols", []),
                  "confirmation_status": confirm["status"],
                  "confirmed_at": pub.get("confirmed_at"),
                  "artifact_path": artifact_path, "qa": qa, "qa_passed": bool(qa_passed),
                  "note": ("POST_UNIFIED: Radar stories are shown only under "
                           "PRIVATE_ANALYTICS; the public Short publishes none")}
        _merge_details(history, run_id, radar=record)
        print(f"      radar: selected {record['selected_count']} / published "
              f"{record['published_count']} ({confirm['status']})")
        return record
    except Exception as exc:
        print(f"      radar: publication record skipped ({type(exc).__name__}: {exc})")
        return None


def _merge_details(history, run_id, **extra):
    """Add keys to a run's details without dropping what earlier stages recorded."""
    if not (history and run_id):
        return
    try:
        runs = [r for r in history.get_publication_runs(limit=200) if r.run_id == run_id]
        details = dict(runs[0].details or {}) if runs else {}
        details.update(extra)
        history.update_run(run_id, details=details)
    except Exception as exc:
        print(f"[history] could not record details: {exc}")


def _fail_open_run(history, run_id, exc, stage="EXCEPTION"):
    """Close a run that is still open with FAILED; a run already closed is left as recorded."""
    if not (history and run_id):
        return
    try:
        runs = [r for r in history.get_publication_runs(limit=200) if r.run_id == run_id]
        if runs and runs[0].completed_at:
            return
        history.finish_run(run_id, "FAILED", "BLOCKED", failure_stage=stage,
                           failure_reason=f"{type(exc).__name__}: {exc}"[:1000],
                           run_status="FAILED")
    except Exception as inner:
        print(f"[history] could not record the failure: {inner}")


def _open_history(mode, job_type=JOB_POST_MARKET):
    """Open the history database and start a run record. Never fatal on its own - a failure
    here surfaces at the persistence gate, which is where refusing to publish belongs."""
    try:
        history = MarketHistory(default_db_path(OUT_DIR))
        return history, history.start_run(mode, job_type=job_type)
    except Exception as exc:
        print(f"[history] unavailable: {type(exc).__name__}: {exc}")
        return None, None


def _stage(history, run_id, stage, **fields):
    if history and run_id:
        try:
            history.update_run(run_id, stage=stage, **fields)
        except Exception as exc:
            print(f"[history] could not record stage {stage}: {exc}")


def _finish(history, run_id, stage, publication_status, **fields):
    if history and run_id:
        try:
            history.finish_run(run_id, stage, publication_status, **fields)
        except Exception as exc:
            print(f"[history] could not close run: {exc}")


# Note: there is deliberately no "refresh the stored report" helper here. Canonical history
# is written once per run and is immutable thereafter; MarketHistory.save_report(replace=True)
# exists only as an administrative recovery tool and is never called by this orchestration.


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--force", action="store_true")
    # Product router. postmarket is the default and runs `run(args)`; the other modes' logic
    # lives in products/, not here.
    ap.add_argument("--mode", choices=("postmarket", "premarket", "report"), default="postmarket")
    ap.add_argument("--session-date", help="premarket: the session about to open; report: the "
                                            "session to build (default: latest final session); "
                                            "postmarket: REPLAY an already-built canonical "
                                            "report (never rebuilds, never uploads)")
    ap.add_argument("--shadow", action="store_true",
                    help="premarket: shadow run -> output/pre_shadow/<date>/, never uploads")
    ap.add_argument("--skip-radar", action="store_true", help="report: build the report only")
    ap.add_argument("--as-of", help="premarket: IST cutoff (ISO datetime); default now")
    ap.add_argument("--frames-only", action="store_true", help="premarket: frames + QA, no MP4")
    ap.add_argument("--no-fetch-public", action="store_true",
                    help="postmarket: do not read today's official lists (F&O ban, ASM/GSM, "
                         "NSE IPO lists); the Exchange / IPO Watch sections are then omitted")
    ap.add_argument("--profile", choices=("PUBLIC_UNREGISTERED", "PRIVATE_ANALYTICS"),
                    default=None, help="publication profile (default PUBLIC_UNREGISTERED); "
                                       "PRIVATE_ANALYTICS output is never uploaded")
    ap.add_argument("--hook-ai", action="store_true",
                    help="premarket: let Gemini choose among the approved hook candidates")
    try:
        from products import VideoRequest, route
        _args = ap.parse_args()
        route(VideoRequest.from_args(_args), _args, postmarket_runner=run)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
