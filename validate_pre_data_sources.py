"""PRE data sources + run history validation -> output/pre_data_sources/

    python validate_pre_data_sources.py            # audits + frames-only PRE runs
    python validate_pre_data_sources.py --no-runs  # audits only (no rendering)

Writes:
    gift_source_audit.json         live NSE IX reading, judged three ways; parse/freshness/
                                   provenance; unavailable + stale behaviour
    rbi_event_calendar_audit.json  the controlled file, a live re-check against RBI's own page,
                                   and what the calendar shows on the validation dates
    pre_run_history_audit.json     PRE runs recorded in a VALIDATION history DB
                                   (output/pre_data_sources/pre_run_history.db - production
                                   history is not touched) and read back
    real_pre_plan.json             the real 25 Sep PRE plan (live GIFT wiring, reconstruction)

Every PRE run here is frames-only (freeze frames + frame QA, no MP4): what changed in this phase
is data and two strings (the GIFT reference line and the RBI event card), so frames are enough
to see them. Cases marked SYNTHETIC use invented NSE IX payloads / placeholder data and say so.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import functools
import json
import os

from config import EXPIRY_WEEKDAY, OUT_DIR
from core.event_calendar import event_calendar_audit, load_official_events, scheduled_events_for
from core.freshness import IST, live_freshness, us_close_freshness
from core.trading_calendar import SessionCalendar

OUT = os.path.join(OUT_DIR, "pre_data_sources")
DB = os.path.join(OUT, "pre_run_history.db")
CAL = SessionCalendar()


def dump(name, obj):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    return path


# --------------------------------------------------------------------------- GIFT
def _synthetic_get(ts: str, ltp: float, day_change: float, dsp_day: dt.date, settle: float,
                   expiry="29-Sep-2026"):
    """A SYNTHETIC NSE IX (market-rate + DSP) pair in the real response shapes."""
    from providers import gift_nifty as gn
    rate = json.dumps({"data": [{"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY",
                                 "EXPIRYDATE": expiry, "LASTPRICE": f"{ltp:.2f}",
                                 "DAYCHANGE": f"{day_change:.2f}", "PERCHANGE": "",
                                 "CONTRACTSTRADED": 40000, "TIMESTMP": ts}]})
    d = dsp_day.strftime("%d-%b-%Y").upper()
    dsp = ("DATE,INSTRUMENT TYPE,SYMBOL,EXPIRY DATE,STRIKE,OPTION TYPE,SETTLEMENT PRICE\n"
           f"{d},FUTIDX,NIFTY,{expiry.upper()},0,FF,{settle:.7f}\n")

    def get(url):
        if url == gn.MARKET_RATE_URL:
            return 200, rate
        return (200, dsp) if url == gn.DSP_URL.format(d=dsp_day) else (404, "")
    return get


def gift_audit() -> dict:
    from providers import gift_nifty as gn
    from providers.premarket import gift_status_of
    now = dt.datetime.now(IST)
    nxt = CAL.next_session(now.date()) if hasattr(CAL, "next_session") else None
    if nxt is None:
        nxt = now.date() + dt.timedelta(days=1)
        while CAL.is_session(nxt) is not True:
            nxt += dt.timedelta(days=1)
    live = {}
    for label, pre, as_of in (
            ("today_live_judged_now", now.date(), now),
            ("next_session_judged_now", nxt, now),
            ("reconstruction_2026-09-25_0745", dt.date(2026, 9, 25),
             dt.datetime(2026, 9, 25, 7, 45, tzinfo=IST))):
        try:
            q = gn.fetch_gift_nifty(pre, as_of, nifty_close=23140.5)
            live[label] = {"pre_date": pre.isoformat(), "as_of": as_of.isoformat(),
                           "verdict": gift_status_of(q), "shown": q.fresh, "quote": q.to_dict()}
        except Exception as exc:
            live[label] = {"pre_date": pre.isoformat(), "verdict": {
                "status": "UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}, "shown": False}
    cases = {}
    d25, d24 = dt.date(2026, 9, 25), dt.date(2026, 9, 24)
    cut = dt.datetime(2026, 9, 25, 7, 45, tzinfo=IST)
    live_run = cut + dt.timedelta(seconds=40)
    for label, ts, ltp, chg, settle in (
            ("SYNTHETIC_fresh_0738", "25-Sep-2026 07:38:05", 23150.0, 52.5, 23097.5),
            ("SYNTHETIC_stale_0655", "25-Sep-2026 06:55:00", 23150.0, 52.5, 23097.5),
            ("SYNTHETIC_conflict", "25-Sep-2026 07:38:05", 23150.0, 10.0, 23097.5)):
        q = gn.fetch_gift_nifty(d25, cut, get=_synthetic_get(ts, ltp, chg, d24, settle),
                                retrieved_at=live_run, nifty_close=23063.1)
        from presentation.pre_plan import GiftNiftyModel  # noqa: F401 (display check below)
        cases[label] = {"verdict": gift_status_of(q), "shown": q.fresh,
                        "change_pct": q.change_pct, "timestamp": q.market_timestamp.isoformat(),
                        "provenance": {k: q.provenance.get(k) for k in (
                            "contract_expiry", "settlement_date", "settlement_price",
                            "consistency", "implied_reference", "judged_at")}}
    try:
        gn.fetch_gift_nifty(d25, cut, get=lambda url: (403, "Access Denied"))
    except gn.GiftSourceError as exc:
        cases["SIMULATED_unavailable_http403"] = {"verdict": {"status": "UNAVAILABLE",
                                                              "reason": str(exc)}, "shown": False}
    return {
        "checked_at": now.isoformat(),
        "source": {
            "chosen": "NSE IX (NSE International Exchange, GIFT City) - the exchange that lists "
                      "GIFT Nifty (NIFTY index futures)",
            "live_quote": gn.MARKET_RATE_URL, "reference": gn.DSP_URL.replace("{d:%d%m%Y}", "<DDMMYYYY>"),
            "source_names": ["nseix_market_rate", "nseix_settlement_file"],
            "source_type": "PRIMARY", "independence_group": "NSE_IX",
            "contract_rule": "near month = earliest expiry on/after the session date",
            "change_definition": "last price / previous NSE IX daily settlement price - 1 "
                                 "(GIFT Nifty's own move; never vs Nifty's spot close)",
            "market_timestamp": "TIMESTMP of the contract row = its last-trade time (IST)",
            "llm_involved": False,
        },
        "freshness_rule": {
            "limit_minutes": 20, "rule": "core.freshness.live_freshness(region='GIFT'): the "
            "reading must be from the morning of the session, at or before the cutoff and at most "
            "20 min old; judged at retrieval time on a live run (fetched <= 10 min after the "
            "cutoff), at the cutoff on a reconstruction (a later reading is FUTURE); <= 60 s "
            "exchange clock skew tolerated on a live run",
            "evidence": "NSE IX near-month trades continuously from 06:30 IST; a reading older "
                        "than 20 min at 07:40 means the feed/contract is not live - no evidence "
                        "found for a different threshold, so the V1 limit is kept"},
        "validation": {"consistency_points": gn.CHANGE_CONSISTENCY_POINTS,
                       "max_abs_change_pct": gn.MAX_ABS_CHANGE_PCT,
                       "max_vs_canonical_nifty_pct": gn.MAX_BASIS_VS_NIFTY_PCT},
        "verified_reference_semantics": (
            "2026-09-25 21:29 IST: Sep contract LTP 23183.0, DAYCHANGE -5.50 -> implied reference "
            "23188.5 == DSP(25-Sep) 23188.5; Oct contract LTP 23223.5, DAYCHANGE +37.00 -> "
            "23186.5 == DSP(25-Sep). The exchange's change is measured from the latest daily "
            "settlement. Morning (session 1) semantics are enforced at runtime: a DAYCHANGE that "
            "disagrees with the previous DSP is a CONFLICT and GIFT is omitted."),
        "live": live, "cases": cases,
        "limitations": [
            "Undocumented website API (the NSE IX site's own JSON), not a licensed data feed: "
            "shape can change without notice (a parse failure -> UNAVAILABLE, omitted).",
            "Display/redistribution rights for NSE IX data are UNREVIEWED - the owner should "
            "review NSE IX's website terms before public upload of PRE.",
            "Reachability from GitHub Actions runners is unverified (nseindia.com blocks cloud "
            "IPs; NSE IX is on a different host but may use the same CDN protection).",
            "No historical intraday GIFT data: a reconstruction of a past morning can never show "
            "GIFT (the only reading is live, i.e. after the cutoff).",
            "Session-1 (morning) DAYCHANGE semantics were verified only for session 2 (evening) "
            "on 2026-09-25; the first live morning run must confirm CONSISTENT in its audit.",
        ],
    }


# --------------------------------------------------------------------------- RBI
def rbi_audit() -> dict:
    import verify_official_events as vo
    cal = load_official_events()
    try:
        live = vo.verify()
    except Exception as exc:
        live = {"error": f"{type(exc).__name__}: {exc}", "all_match": False}
    days = {d: event_calendar_audit(dt.date.fromisoformat(d), CAL, EXPIRY_WEEKDAY)
            for d in ("2026-09-25", "2026-09-22", "2026-09-29", "2026-10-07", "2026-10-28",
                      "2026-12-04", "2027-02-05")}
    return {"file": cal.to_dict(), "live_verification_against_rbi_page": live,
            "calendar_on_dates": days,
            "handling": {
                "source": "RBI Press Release 2025-2026/2306 (23 Mar 2026), 'Meeting Schedule of "
                          "the Monetary Policy Committee for 2026-2027', www.rbi.org.in prid=62422",
                "storage": "data/official_events.json - controlled, manually maintained; every "
                           "entry carries source_url, source_reference, the quoted source line, "
                           "retrieved_at, verified_on",
                "runtime": "the daily run reads the file only (never RBI's site); the loader "
                           "rejects any entry off RBI's hosts, with dates that disagree with its "
                           "quoted line, a time without a quoted time source, or a non-official "
                           "status; an entry verified > 180 days before the day is not shown",
                "reverification": "python verify_official_events.py [--stamp]",
                "time": "RBI's schedule states no time - none claimed ('Final day of the RBI MPC "
                        "meeting')"}}


# --------------------------------------------------------------------------- PRE runs
def _shift_brief(brief, days: int, events):
    """Move a SYNTHETIC brief's dates by `days` and re-judge freshness honestly."""
    from providers.premarket import PreMarketQuote  # noqa: F401
    delta = dt.timedelta(days=days)
    pre, as_of = brief.pre_date + delta, brief.as_of + delta
    cues = []
    for q in brief.global_cues:
        md = q.market_date + delta if q.market_date else None
        ts = q.market_timestamp + delta if q.market_timestamp else None
        st, why = (us_close_freshness(md, pre, as_of) if q.region == "US"
                   else live_freshness(ts, pre, as_of, q.region))
        cues.append(dataclasses.replace(q, market_date=md, market_timestamp=ts, freshness=st,
                                        freshness_reason=why, retrieved_at=as_of,
                                        reference_date=q.reference_date + delta if q.reference_date else None))
    vix = dataclasses.replace(brief.vix, session=brief.vix.session + delta,
                              previous_session=brief.vix.previous_session + delta) if brief.vix else None
    return dataclasses.replace(brief, pre_date=pre, as_of=as_of,
                               previous_session=brief.previous_session + delta,
                               global_cues=cues, vix=vix, events=events)


@functools.lru_cache(maxsize=None)
def _yahoo(ticker, start, end, interval):
    from providers.premarket import yahoo_history
    return yahoo_history(ticker, start, end, interval)


def run_cases(frames: bool = True) -> dict:
    from products import VideoRequest
    from products.pre_run_history import PRE_DEMO_MODE, PreRunRecorder
    from products.premarket import render_pre, run_premarket
    from providers import gift_nifty as gn
    runs_dir = os.path.join(OUT, "runs")
    out = {}

    def real(label, day, gift_fn, note):
        req = VideoRequest(mode="premarket", session_date=day, frames_only=True,
                           out_dir=os.path.join(runs_dir, label))
        # canonical reports from the production history (read only); runs into the validation DB
        res = run_premarket(req, history_fn=_yahoo, gift_fn=gift_fn, run_db_path=DB)
        out[label] = {"note": note, "result": None if res is None else {
            k: res.get(k) for k in ("ok", "order", "total_duration", "blocked", "run",
                                    "degradations", "contact_sheet")}}
        return res

    d25 = dt.date(2026, 9, 25)
    cut25 = dt.datetime(2026, 9, 25, 7, 45, tzinfo=IST)
    real("REAL_2026-09-25_live_gift", d25, None,
         "real reconstruction, production GIFT wiring (live NSE IX -> reading after the cutoff "
         "-> FUTURE -> omitted)")

    def gift_unavailable(pre, as_of):
        raise gn.GiftSourceError("NSE IX market-rate returned HTTP 403 (simulated)")
    real("REAL_2026-09-25_gift_unavailable", d25, gift_unavailable,
         "real 25 Sep brief; NSE IX unreachable (simulated 403) -> GIFT omitted, run DEGRADED")

    def gift_stale(pre, as_of):
        return gn.fetch_gift_nifty(pre, as_of, get=_synthetic_get(
            "25-Sep-2026 06:55:00", 23150.0, 52.5, dt.date(2026, 9, 24), 23097.5),
            retrieved_at=cut25 + dt.timedelta(seconds=40), nifty_close=23063.1)
    real("REAL_2026-09-25_SYNTHETIC_stale_gift", d25, gift_stale,
         "real 25 Sep brief + SYNTHETIC NSE IX reading from 06:55 (50 min old) -> STALE, omitted")

    def gift_fresh(pre, as_of):
        return gn.fetch_gift_nifty(pre, as_of, get=_synthetic_get(
            "25-Sep-2026 07:38:05", 23150.0, 52.5, dt.date(2026, 9, 24), 23097.5),
            retrieved_at=cut25 + dt.timedelta(seconds=40), nifty_close=23063.1)
    real("REAL_2026-09-25_SYNTHETIC_fresh_gift", d25, gift_fresh,
         "real 25 Sep brief + SYNTHETIC fresh NSE IX reading (07:38, vs the REAL 24-Sep DSP "
         "23097.5) -> shown with its time; frames check the new reference line")

    real("REAL_2026-09-22_event_expiry", dt.date(2026, 9, 22), None,
         "real event-led date: weekly F&O expiry (RULE_DERIVED) on the canonical calendar")
    real("REAL_2026-09-28_blocked", dt.date(2026, 9, 28), None,
         "next session: the 25 Sep canonical report does not exist yet -> BLOCKED")

    # event-led on the real RBI schedule, synthetic market data (placeholder), dates shifted
    from products.pre_fixtures import synthetic_brief
    rbi_day = dt.date(2026, 10, 7)
    evs = scheduled_events_for(rbi_day, CAL, EXPIRY_WEEKDAY)
    brief = _shift_brief(synthetic_brief("QUIET"), 1, evs)
    rec = PreRunRecorder(DB, mode=PRE_DEMO_MODE)
    rec.start(brief.pre_date, brief.as_of, {"frames_only": True, "synthetic": True})
    run_dir = os.path.join(runs_dir, "SYNTHETIC_2026-10-07_rbi_event", rbi_day.isoformat())
    res = render_pre(brief, run_dir, watermark="SYNTHETIC DATA - NOT REAL", frames_only=frames)
    events_audit = event_calendar_audit(rbi_day, CAL, EXPIRY_WEEKDAY)
    record = rec.finished(res, brief, None, events_audit, res.get("provenance"), out_dir=run_dir)
    out["SYNTHETIC_2026-10-07_rbi_event"] = {
        "note": "REAL RBI schedule entry (7 Oct 2026 MPC decision day) on a SYNTHETIC market "
                "brief (placeholder data, watermark) - frames check the RBI event card",
        "events_shown": [e.to_dict() for e in evs],
        "result": {k: res.get(k) for k in ("ok", "order", "total_duration", "blocked",
                                           "contact_sheet")},
        "run": {k: record.get(k) for k in ("run_id", "run_status", "stage")}}
    return out


def read_back() -> list:
    from storage import MarketHistory
    with MarketHistory(DB) as h:
        rows = h.get_publication_runs(limit=200)
    return [{"run_id": r.run_id, "mode": r.mode, "target_date": r.target_date,
             "run_status": r.run_status, "stage": r.stage, "started_at": r.started_at,
             "completed_at": r.completed_at, "data_qa": r.data_qa_status,
             "content_qa": r.content_qa_status, "video_qa": r.video_qa_status,
             "publication_status": r.publication_status, "artifact_path": r.artifact_path,
             "failure_stage": r.failure_stage, "failure_reason": r.failure_reason,
             "degradations": (r.details or {}).get("degradations"),
             "section_order": ((r.details or {}).get("section_plan") or {}).get("order"),
             "hook": (r.details or {}).get("hook"),
             "gift": ((r.details or {}).get("gift") or {}).get("status"),
             "provenance_ok": ((r.details or {}).get("provenance") or {}).get("ok")}
            for r in rows]


def real_plan() -> dict:
    from presentation.pre_plan import plan_pre_sections
    from presentation.pre_provenance import pre_provenance_audit
    from products.premarket import build_real_brief
    d25 = dt.date(2026, 9, 25)
    brief, acq = build_real_brief(d25, dt.datetime(2026, 9, 25, 7, 45, tzinfo=IST),
                                  history_fn=_yahoo)
    plan = plan_pre_sections(brief)
    return {"pre_date": d25.isoformat(), "as_of": brief.as_of.isoformat(),
            "previous_session": brief.previous_session.isoformat(),
            "plan": plan.to_dict(), "acquisition": acq.to_dict(),
            "events": event_calendar_audit(d25, CAL, EXPIRY_WEEKDAY),
            "provenance": pre_provenance_audit(brief, plan), "brief_notes": brief.notes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-runs", action="store_true")
    args = ap.parse_args(argv)
    print("gift ->", dump("gift_source_audit.json", gift_audit()))
    print("rbi  ->", dump("rbi_event_calendar_audit.json", rbi_audit()))
    print("plan ->", dump("real_pre_plan.json", real_plan()))
    if not args.no_runs:
        if os.path.exists(DB):
            os.replace(DB, DB + ".previous")      # keep the last validation history, start clean
        cases = run_cases()
        print("runs ->", dump("pre_run_history_audit.json", {
            "history_db": DB, "note": "validation history DB - production history untouched",
            "cases": cases, "read_back": read_back()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
