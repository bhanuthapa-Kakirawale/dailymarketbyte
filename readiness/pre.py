"""PRE readiness: is the "before the bell" edition for `pre_date` safe to generate right now?

Mirrors `products.premarket.run_premarket` up to - and not including - the Composer:
`require_previous_report` (the previous canonical session, never an older one), the brief as
`build_real_brief` builds it (read-only seams: the history is opened read-only, public
intelligence and institutional flows are read from the persisted snapshots, the published-Radar
ledger is not opened - the public profile shows no stock watch), then the cheap half of
`render_pre`: publication profile -> V3 plan -> storyboard (deterministic hook) -> content /
language / provenance gates -> publication audit -> runtime ceiling.

LIVE probes the overnight cues through the real acquisition path (`fetch_premarket_quotes`,
~11 small Yahoo requests, counted); REPLAY never fetches (cues = SKIP).
"""
from __future__ import annotations

import datetime as dt
import time

from . import checks as C
from . import environment as E
from . import evidence as ev
from .models import LIVE, PASS, PREFLIGHT, REPLAY, SHADOW, SKIP, WARN, ReadinessExecutionError, ReadinessResult
from .post import _environment

EDITION = "PRE"
INDIA_OPEN = dt.time(9, 15)


def default_cutoff(pre_date: dt.date) -> dt.datetime:
    from products.premarket import DEFAULT_CUTOFF
    return dt.datetime.combine(pre_date, DEFAULT_CUTOFF, tzinfo=ev.IST)


class _Counter:
    """Counts provider calls - the gate reports exactly how much network it used."""

    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def __call__(self, *a, **kw):
        self.calls += 1
        return self.fn(*a, **kw)


def _no_fetch(*_a, **_kw):
    import pandas as pd
    return pd.DataFrame()


def evaluate_pre(pre_date: dt.date | None = None, as_of: dt.datetime | None = None, *,
                 mode: str | None = None, intent: str = SHADOW, out_dir: str | None = None,
                 calendar=None, history_fn=None, env: dict | None = None,
                 gift_policy=None) -> ReadinessResult:
    """`pre_date` = the session about to open (default: the cutoff's date); `as_of` = the cutoff
    (default with a `pre_date`: that morning 07:45 IST, run_premarket's convention). Nothing
    reads the wall clock. `history_fn` overrides the LIVE cue provider (tests)."""
    import config
    from core.trading_calendar import SessionCalendar
    t0 = time.perf_counter()
    try:
        out_dir = out_dir or config.OUT_DIR
        cal = calendar or SessionCalendar()
        if as_of is None:
            if pre_date is None:
                raise ReadinessExecutionError("evaluate_pre needs as_of or pre_date")
            as_of = default_cutoff(pre_date)
        as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=ev.IST)
        pre_date = pre_date or as_of.astimezone(ev.IST).date()
        mode = mode or LIVE
        res = ReadinessResult(edition=EDITION, stage=PREFLIGHT, mode=mode, intent=intent,
                              as_of=as_of, cutoff=as_of, session_date=pre_date,
                              edition_date=pre_date)
        if mode == REPLAY:
            provider = _Counter(_no_fetch)
        else:
            from providers.premarket import yahoo_history
            provider = _Counter(history_fn or yahoo_history)
        res.checks = _checks(res, pre_date, as_of, out_dir, cal, env or {}, provider, gift_policy)
    except ReadinessExecutionError:
        raise
    except Exception as exc:
        raise ReadinessExecutionError(f"{type(exc).__name__}: {exc}") from exc
    res.evaluated_at = config.now_ist().isoformat()
    res.runtime = {"seconds": round(time.perf_counter() - t0, 3),
                   "network_calls": provider.calls if mode == LIVE else 0}
    return res


def _checks(res, pre_date, as_of, out_dir, cal, env, provider, gift_policy) -> list:
    from presentation.pre_plan import PreMarketBlocked
    from products.premarket import require_previous_report
    out = []
    E_ = EDITION

    # ---- session + window
    st = cal.status(pre_date)
    if st != "SESSION":
        code = "NOT_A_SESSION" if st == "NON_SESSION" else "SESSION_UNKNOWN"
        out.append(C.mk(E_, "SESSION", "SESSION", "SESSION", C.unmet_status(E_, "SESSION"),
                        f"{code}: {pre_date} is " + ("not an NSE trading session - no PRE edition"
                                                    if st == "NON_SESSION" else
                                                    "not covered by the trading calendar"),
                        observed=code, remediation="nothing to generate today" if
                        st == "NON_SESSION" else "extend core/trading_calendar.py"))
        out += _environment(E_, out_dir, None, env)
        return out
    prev = cal.previous_session(pre_date)
    res.source_session = prev
    out.append(C.mk(E_, "SESSION", "SESSION", "SESSION", PASS,
                    f"{pre_date} is a session; previous completed session {prev}",
                    observed=str(prev)))
    open_at = dt.datetime.combine(pre_date, INDIA_OPEN, tzinfo=ev.IST)
    prev_final = dt.datetime.combine(prev, dt.time(15, 40), tzinfo=ev.IST) if prev else None
    if as_of >= open_at:
        out.append(C.mk(E_, "EDITION_WINDOW", "TEMPORAL", "EDITION_WINDOW",
                        C.unmet_status(E_, "EDITION_WINDOW"),
                        f"cutoff {as_of:%Y-%m-%d %H:%M} is at/after the {pre_date} 09:15 open - "
                        "the Indian session is live; PRE is a before-the-bell edition",
                        observed=as_of.isoformat(), expected=f"< {open_at.isoformat()}",
                        remediation="PRE runs before 09:15 IST"))
    elif prev_final is not None and as_of < prev_final:
        out.append(C.mk(E_, "EDITION_WINDOW", "TEMPORAL", "EDITION_WINDOW",
                        C.unmet_status(E_, "EDITION_WINDOW"),
                        f"the previous session {prev} is not final at the cutoff",
                        observed=as_of.isoformat(), expected=f">= {prev_final.isoformat()}"))
    else:
        out.append(C.mk(E_, "EDITION_WINDOW", "TEMPORAL", "EDITION_WINDOW", PASS,
                        f"cutoff {as_of:%Y-%m-%d %H:%M} IST is before the open"))

    history, hist_detail = None, ""
    try:
        history = ev.open_history(out_dir)
    except Exception as exc:
        hist_detail = f"{type(exc).__name__}: {exc}"
    built = {}
    try:
        report = None
        try:
            if history is None:
                raise PreMarketBlocked("PREVIOUS_REPORT_UNAVAILABLE",
                                       f"history database unavailable ({hist_detail})")
            prev, report, path = require_previous_report(pre_date, cal, history=history)
            out.append(C.mk(E_, "CANONICAL_REPORT", "REPORT", "CANONICAL_REPORT", PASS,
                            f"previous-session report {report.report_id} for {prev} "
                            "(publication-ready)", observed=report.report_id, source=path))
        except PreMarketBlocked as exc:
            out.append(C.mk(E_, "CANONICAL_REPORT", "REPORT", "CANONICAL_REPORT",
                            C.unmet_status(E_, "CANONICAL_REPORT"), f"{exc.code}: {exc.message}",
                            observed=exc.code,
                            remediation="run the evening REPORT for the previous session "
                                        "(scripts\\run_evening.bat); PRE never shows an older "
                                        "session"))
        record = ev.report_record_at(out_dir, prev, as_of)
        if report is not None:
            out += C.guarded(E_, "BENCHMARK", "MARKET_DATA", "BENCHMARK",
                             C.check_benchmark, E_, report, prev)
            out += C.guarded(E_, "TEMPORAL_SAFETY", "TEMPORAL", "TEMPORAL_SAFETY",
                             C.check_temporal, E_, report, as_of, prev)
            out += C.guarded(E_, "SECTORS", "MARKET_DATA", "SECTORS",
                             C.check_sectors, E_, report, prev)
            out += C.guarded(E_, "FII_DII", "FLOWS", "FII_DII",
                             C.check_fii_dii, E_, report, prev)
        else:
            for cid, cat in (("BENCHMARK", "MARKET_DATA"), ("TEMPORAL_SAFETY", "TEMPORAL"),
                             ("SECTORS", "MARKET_DATA"), ("FII_DII", "FLOWS")):
                out.append(C.skipped(E_, cid, cat, cid, "no usable previous-session report"))
        out += C.guarded(E_, "INSTITUTIONAL_FLOW", "INSTITUTIONAL", "INSTITUTIONAL_FLOW",
                         C.check_institutional, E_, out_dir, pre_date, as_of, record,
                         nse_session=prev)
        out += C.guarded(E_, "MARKET_EVENTS", "MARKET_EVENTS", "MARKET_EVENTS",
                         C.check_market_events, E_, out_dir, prev, as_of, record)
        out += C.guarded(E_, "OFFICIAL_SNAPSHOTS", "OFFICIAL_LISTS", "OFFICIAL_SNAPSHOTS",
                         C.check_official_snapshots, E_, out_dir, prev, as_of)
        out += C.guarded(E_, "EVENT_SCHEDULE", "CALENDAR", "EVENT_SCHEDULE",
                         _event_schedule, pre_date)
        if report is not None:
            out += C.guarded(E_, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN", _build, built,
                             res, pre_date, as_of, out_dir, cal, history, provider, gift_policy)
        else:
            out.append(C.skipped(E_, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN",
                                 "no usable previous-session report"))
        out += _acquisition_checks(res, built)
        out += C.guarded(E_, "INTELLIGENCE", "INTELLIGENCE", "INTELLIGENCE",
                         _intelligence, out_dir, prev)
        out += C.guarded(E_, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE",
                         C.check_market_structure, E_, out_dir, prev, as_of,
                         getattr(built.get("brief"), "public_intelligence", None))
        if "sb" in built:
            out += C.guarded(E_, "PUBLICATION", "PUBLICATION", "PUBLICATION",
                             _publication, built, res.intent)
        else:
            out.append(C.skipped(E_, "PUBLICATION", "PUBLICATION", "PUBLICATION",
                                 "no storyboard to audit (see EDITORIAL_PLAN)"))
    finally:
        if history is not None:
            history.close()
    out.append(C.mk(E_, "RADAR", "RADAR", "RADAR", SKIP,
                    "public profile shows no stock watch (published Radar ledger not opened)"))
    out += _environment(E_, out_dir, not hist_detail, env, hist_detail)
    return out


# --------------------------------------------------------------------------- stages
def _event_schedule(pre_date):
    from operations.official_events import check_official_events
    r = check_official_events(pre_date)
    st = r.get("status")
    detail = "; ".join(f["message"] for f in r.get("findings") or [])
    if st in ("EXPIRED", "WARN_FILE"):
        # an expired verification / rejected file removes events from the edition
        return C.mk(EDITION, "EVENT_SCHEDULE", "CALENDAR", "EVENT_SCHEDULE",
                    C.unmet_status(EDITION, "EVENT_SCHEDULE"),
                    f"official event schedule {st}: {detail}", source_health="STALE",
                    observed=st, remediation="python verify_official_events.py --stamp")
    # OK, or a maintenance warning (expiring soon / coverage / next year's holidays): every
    # verified event is still shown today
    return C.mk(EDITION, "EVENT_SCHEDULE", "CALENDAR", "EVENT_SCHEDULE", PASS,
                "official event schedule verified" + (f" (maintenance: {detail})" if detail else ""),
                source_health="HEALTHY", observed=st)


def _intelligence(out_dir, prev):
    from intelligence import load_snapshot
    snap = load_snapshot(out_dir, prev)
    if snap is None:
        return C.mk(EDITION, "INTELLIGENCE", "INTELLIGENCE", "INTELLIGENCE",
                    C.unmet_status(EDITION, "INTELLIGENCE"),
                    f"no intelligence snapshot for {prev} - flow materiality context omitted",
                    source_health="MISSING")
    return C.mk(EDITION, "INTELLIGENCE", "INTELLIGENCE", "INTELLIGENCE", PASS,
                f"intelligence snapshot for {prev}", source_health="HEALTHY")


def _build(built, res, pre_date, as_of, out_dir, cal, history, provider, gift_policy):
    from daily_video.pre_storyboard import build_pre_storyboard
    from institutional_flows.context import load_institutional
    from presentation.pre_plan import MAX_RUNTIME, plan_pre_sections
    from presentation.pre_public import apply_publication_profile
    from presentation.public_intelligence import load_public_intelligence
    from products.premarket import build_real_brief
    replay = res.mode == REPLAY
    prev = cal.previous_session(pre_date)

    def intel_fn(d, _live):
        return load_public_intelligence(prev, d, out_dir, fetch=False, now_iso=as_of.isoformat(),
                                        snapshot_session=prev, replay=replay, now=as_of,
                                        capture_mode="PRE_FALLBACK", calendar=cal)

    def institutional_fn(_live):
        return load_institutional(out_dir, cutoff=as_of, live=False, on_or_before=pre_date)

    def stories_fn(_prev, _prev_prev):
        return [], {}, [], {"radar_publication": "NOT_READ: public profile shows no stock watch"}

    gift_fn = (lambda _d, _a: None) if replay else None
    brief, acq = build_real_brief(pre_date, as_of, history_fn=provider, gift_fn=gift_fn,
                                  calendar=cal, shadow=False, gift_policy=gift_policy,
                                  intel_fn=intel_fn, institutional_fn=institutional_fn,
                                  history=history, stories_fn=stories_fn)
    built.update(brief=brief, acq=acq)
    gate = apply_publication_profile(brief)
    res.profile = gate.profile.value
    plan = plan_pre_sections(brief)
    sb = build_pre_storyboard(brief, plan, hook_ai=False, gate=gate)
    built.update(plan=plan, sb=sb)
    return C.check_editorial_pre(EDITION, plan, sb, MAX_RUNTIME)


def _acquisition_checks(res, built) -> list:
    """Overnight cues + India VIX + GIFT, graded by the PRE run's OWN degradation rules
    (`products.pre_run_history.degradations`, i.e. core.freshness)."""
    E_ = EDITION
    acq = built.get("acq")
    if res.mode == REPLAY:
        why = "REPLAY never acquires - overnight cues are judged at acquisition time"
        return [C.mk(E_, "GLOBAL_CUES", "GLOBAL", "GLOBAL_CUES", SKIP, f"not evaluated: {why}"),
                C.mk(E_, "INDIA_VIX", "MARKET_DATA", "INDIA_VIX", SKIP, f"not evaluated: {why}"),
                _gift(built)]
    if acq is None:
        return [C.skipped(E_, "GLOBAL_CUES", "GLOBAL", "GLOBAL_CUES", "brief not built"),
                C.skipped(E_, "INDIA_VIX", "MARKET_DATA", "INDIA_VIX", "brief not built"),
                _gift(built)]
    from products.pre_run_history import degradations
    degr = degradations(acq)
    cues = [d for d in degr if d["source"] not in ("GIFT NIFTY", "INDIA VIX")]
    vix = [d for d in degr if d["source"] == "INDIA VIX"]
    fresh = [q.name for q in acq.global_cues if q.fresh]
    out = []
    if cues:
        out.append(C.mk(E_, "GLOBAL_CUES", "GLOBAL", "GLOBAL_CUES", WARN,
                        f"{len(cues)} overnight cue(s) unavailable/stale: " +
                        "; ".join(f"{d['source']} {d['status']}" for d in cues) +
                        (" - OVERNIGHT omitted" if not fresh else ""),
                        source_health="STALE" if fresh else "MISSING",
                        observed={"fresh": fresh, "degraded": cues},
                        remediation="the cue is dropped (never shown stale); re-run later if a "
                                    "market's close is not final yet"))
    else:
        out.append(C.mk(E_, "GLOBAL_CUES", "GLOBAL", "GLOBAL_CUES", PASS,
                        "overnight cues fresh: " + ", ".join(fresh), source_health="HEALTHY",
                        observed={"fresh": fresh}))
    if vix:
        out.append(C.mk(E_, "INDIA_VIX", "MARKET_DATA", "INDIA_VIX", WARN,
                        f"India VIX {vix[0]['status']}: {vix[0]['reason']}",
                        source_health="STALE"))
    else:
        out.append(C.mk(E_, "INDIA_VIX", "MARKET_DATA", "INDIA_VIX", PASS,
                        f"India VIX fresh for {acq.vix.session}", source_health="HEALTHY"))
    out.append(_gift(built))
    return out


def _gift(built) -> object:
    brief = built.get("brief")
    pol = getattr(brief, "gift_policy", None) or {}
    if pol and not pol.get("publication_allowed"):
        return C.mk(EDITION, "GIFT_NIFTY", "GLOBAL", "GIFT_NIFTY", SKIP,
                    f"POLICY_DISABLED - not fetched, never shown ({pol.get('reason')}); a policy "
                    "withhold is never a degradation")
    shown = bool(brief is not None and brief.gift is not None)
    return C.mk(EDITION, "GIFT_NIFTY", "GLOBAL", "GIFT_NIFTY", SKIP if not pol else PASS,
                "GIFT Nifty " + ("validated" if shown else "not shown (no fresh reading)"))


def _publication(built, intent):
    from core.content_safety import SafetyStatus, scan_publication
    from daily_video.public_storyboard import audit_storyboard
    from presentation.pre_plan import pre_language_issues
    from presentation.pre_provenance import pre_provenance_audit
    brief, plan, sb = built["brief"], built["plan"], built["sb"]
    public = sb.public_text()
    scan = scan_publication(public)
    blocks = [f"language: {x}" for x in pre_language_issues(public)]
    prov = pre_provenance_audit(brief, plan)
    if not prov["ok"]:
        blocks.append("provenance: displayed fact(s) with AI provenance: " + ", ".join(
            str(r.get("fact")) for r in prov["ai_violations"]))
    audit = audit_storyboard(sb, "PRE", synthetic=brief.synthetic)
    built["audit"] = audit
    return C.check_publication(EDITION, intent, audit, scan.status is SafetyStatus.SAFE, blocks)


__all__ = ["evaluate_pre", "default_cutoff", "EDITION"]
