"""Data / editorial / publication checks shared by the PRE and POST evaluators.

Every check READS what an existing component already decided - report facts and their
validation status, the REPORT job's capture record, the persisted snapshots, the V3 arbiter's
trace, the publication audit - and grades it against the capability matrix. None of them
fetches, computes a market statistic or changes a selection.
"""
from __future__ import annotations

import datetime as dt

from . import evidence as ev
from .matrix import requirement
from .models import (FAIL, HEALTHY, HEALTHY_EMPTY, MISSING, PARTIAL, PASS, PUBLISH, SKIP,
                     SOURCE_FAILURE, STALE, UNSUPPORTED, WARN, CheckResult, health_status,
                     unmet)


def mk(edition, check_id, category, capability, status, message, **kw) -> CheckResult:
    return CheckResult(check_id=check_id, category=category, capability=capability,
                       requirement=requirement(capability, edition), status=status,
                       message=message, **kw)


def unmet_status(edition, capability) -> str:
    return unmet(requirement(capability, edition))


def guarded(edition, check_id, category, capability, fn, *a, **kw):
    """Run one check. A bug or an unexpected input inside it never crashes the gate: it is
    graded like an unmet capability (FAIL if required, WARN if optional), code CHECK_ERROR."""
    try:
        out = fn(*a, **kw)
        return out if isinstance(out, list) else [out]
    except Exception as exc:
        req = requirement(capability, edition)
        return [CheckResult(check_id=check_id, category=category, capability=capability,
                            requirement=req, status=unmet(req),
                            message=f"CHECK_ERROR: {type(exc).__name__}: {exc}",
                            remediation="readiness could not evaluate this input - inspect it "
                                        "by hand before generating")]


def skipped(edition, check_id, category, capability, why) -> CheckResult:
    return mk(edition, check_id, category, capability, SKIP, f"not evaluated: {why}")


# --------------------------------------------------------------------------- report facts
def _eligible(fact) -> bool:
    from intelligence.models import ELIGIBLE_HISTORICAL_STATUSES
    st = getattr(fact.validation_status, "value", fact.validation_status)
    return st in ELIGIBLE_HISTORICAL_STATUSES


def _facts(report, ids) -> list:
    return [f for f in (report.fact(i) for i in ids or []) if f is not None]


def check_benchmark(edition, report, session: dt.date) -> CheckResult:
    """The headline index close: present, eligible (VERIFIED / SINGLE_SOURCE), dated to the
    expected session. A close from any other session is a stale benchmark - BLOCKING."""
    req = requirement("BENCHMARK", edition)
    closes = [f for f in _facts(report, (report.nifty or {}).get("fact_ids"))
              if getattr(f.metric, "value", f.metric) == "INDEX_CLOSE"] or \
        [f for f in report.facts if getattr(f.metric, "value", f.metric) == "INDEX_CLOSE"
         and f.instrument == "NIFTY 50"]
    if not closes:
        return mk(edition, "BENCHMARK", "MARKET_DATA", "BENCHMARK", unmet(req),
                  "no Nifty 50 close fact in the canonical report", expected=str(session),
                  remediation="rebuild the session's report (REPORT job)")
    f = closes[0]
    st = getattr(f.validation_status, "value", f.validation_status)
    if f.market_date != session:
        return mk(edition, "BENCHMARK", "MARKET_DATA", "BENCHMARK", unmet(req),
                  f"stale benchmark: Nifty 50 close is dated {f.market_date}, expected {session}",
                  source_health=STALE, observed=str(f.market_date), expected=str(session),
                  remediation="expected session OHLCV missing - re-run the REPORT job once the "
                              "provider has the session")
    if not _eligible(f):
        return mk(edition, "BENCHMARK", "MARKET_DATA", "BENCHMARK", unmet(req),
                  f"Nifty 50 close is {st} (needs VERIFIED or SINGLE_SOURCE)", observed=st)
    return mk(edition, "BENCHMARK", "MARKET_DATA", "BENCHMARK", PASS,
              f"Nifty 50 close {f.value:,.2f} for {session} ({st})", source_health=HEALTHY,
              observed=st, as_of=str(f.market_date))


# The Indian cash-session measurements: none may be dated after the session the edition
# describes. INDEX_LEVEL (live / indicative - GIFT Nifty this morning), commodities and FX are
# legitimately dated later in a morning-built report and are judged by their own freshness rules.
INDIA_SESSION_METRICS = frozenset({"INDEX_CLOSE", "INDEX_CHANGE_PCT", "STOCK_CLOSE",
                                   "STOCK_CHANGE_PCT", "STOCK_RELATIVE_VOLUME",
                                   "SECTOR_CHANGE_PCT", "FII_NET_CASH", "DII_NET_CASH",
                                   "VOLATILITY_INDEX", "TECHNICAL_LEVEL"})


def check_temporal(edition, report, cutoff: dt.datetime, latest_allowed: dt.date) -> CheckResult:
    """Point-in-time: the report must have existed at the cutoff, and no Indian cash-session
    fact may be dated after `latest_allowed` (PRE: the previous session - India data of the
    session about to open is never an input; POST: the recap session)."""
    req = requirement("TEMPORAL_SAFETY", edition)
    gen = ev.parse_ts(getattr(report, "generated_at", None))
    if gen is not None and gen > cutoff:
        return mk(edition, "TEMPORAL_SAFETY", "TEMPORAL", "TEMPORAL_SAFETY", unmet(req),
                  f"the canonical report was generated {gen.isoformat()} - after the cutoff "
                  f"{cutoff.isoformat()} (it did not exist yet)", source_health=MISSING,
                  observed=gen.isoformat(), expected=f"<= {cutoff.isoformat()}",
                  remediation="evaluate at a later cutoff, or run the REPORT job first")
    late = sorted({f"{f.instrument} {f.market_date}" for f in report.facts
                   if getattr(f.metric, "value", f.metric) in INDIA_SESSION_METRICS
                   and f.market_date and f.market_date > latest_allowed})
    if late:
        return mk(edition, "TEMPORAL_SAFETY", "TEMPORAL", "TEMPORAL_SAFETY", unmet(req),
                  f"future data: {len(late)} Indian session fact(s) dated after {latest_allowed}: "
                  + ", ".join(late[:4]), observed=late[:10], expected=f"<= {latest_allowed}",
                  remediation="the report describes the wrong session - rebuild it")
    return mk(edition, "TEMPORAL_SAFETY", "TEMPORAL", "TEMPORAL_SAFETY", PASS,
              f"report existed at the cutoff; no Indian session fact after {latest_allowed}",
              observed=gen.isoformat() if gen else None)


def check_sectors(edition, report, session) -> CheckResult:
    req = requirement("SECTORS", edition)
    ids = [s.get("fact_id") for s in report.sectors or [] if isinstance(s, dict)]
    ok = [f for f in _facts(report, ids) if f.market_date == session and _eligible(f)]
    if not ok:
        return mk(edition, "SECTORS", "MARKET_DATA", "SECTORS", unmet(req),
                  f"no validated sector move for {session} - the sectors section is omitted",
                  source_health=MISSING, remediation="NSE sector indices unavailable at build "
                                                     "time; the edition proceeds without sectors")
    return mk(edition, "SECTORS", "MARKET_DATA", "SECTORS", PASS,
              f"{len(ok)} validated sector moves", source_health=HEALTHY, observed=len(ok))


def check_movers(edition, report) -> CheckResult:
    from editorial.movers_gate import movers_coverage_verdict
    req = requirement("MOVERS", edition)
    v = movers_coverage_verdict(report)
    if v["publishable"]:
        return mk(edition, "MOVERS", "MARKET_DATA", "MOVERS", PASS, v["reason"],
                  source_health=HEALTHY, observed=v.get("coverage_pct"),
                  expected=f">= {v['min_coverage_pct']}")
    return mk(edition, "MOVERS", "MARKET_DATA", "MOVERS", unmet(req),
              f"movers suppressed: {v['reason']}", source_health=PARTIAL,
              observed=v.get("coverage_pct"), expected=f">= {v['min_coverage_pct']}",
              remediation="movers are omitted (never ranked over a partial universe)")


def check_fii_dii(edition, report, session) -> CheckResult:
    req = requirement("FII_DII", edition)
    facts = [f for f in _facts(report, (report.institutional_flows or {}).get("fact_ids"))
             if f.market_date == session]
    ok = [f for f in facts if _eligible(f)]
    if not ok:
        held = sorted({getattr(f.validation_status, "value", str(f.validation_status))
                       for f in facts})
        why = (f"only {'/'.join(held)} FII/DII facts (e.g. AI-only) for {session}" if facts else
               f"no FII/DII figure for {session} (NSE not yet published / blocked)")
        return mk(edition, "FII_DII", "FLOWS", "FII_DII", unmet(req),
                  why + " - flows are omitted, never relabelled from another day",
                  source_health=PARTIAL if facts else MISSING, observed=held or None,
                  remediation="normal before NSE publishes (~19:00 IST); re-run the REPORT later")
    return mk(edition, "FII_DII", "FLOWS", "FII_DII", PASS,
              f"FII/DII facts for {session}", source_health=HEALTHY, observed=len(ok))


def check_vix_report(edition, report, session) -> CheckResult:
    req = requirement("INDIA_VIX", edition)
    ok = [f for f in report.facts if getattr(f.metric, "value", f.metric) == "VOLATILITY_INDEX"
          and f.market_date == session and _eligible(f)]
    if not ok:
        return mk(edition, "INDIA_VIX", "MARKET_DATA", "INDIA_VIX", unmet(req),
                  f"no validated India VIX for {session}", source_health=MISSING)
    return mk(edition, "INDIA_VIX", "MARKET_DATA", "INDIA_VIX", PASS,
              f"India VIX {ok[0].value:.2f} for {session}", source_health=HEALTHY)


# --------------------------------------------------------------------------- institutional
INSTITUTIONAL_STALE_DAYS = {"NSE": 1, "CDSL": 3, "NSDL": 20}   # = private desk's thresholds


def _key_date(snap):
    for v in (snap.report_key, snap.data_as_of, snap.report_date):
        try:
            return dt.date.fromisoformat(str(v)[:10])
        except (TypeError, ValueError):
            continue
    return None


def check_institutional(edition, out_dir, ref_date: dt.date, cutoff, record=None,
                        nse_session: dt.date | None = None) -> CheckResult:
    """CDSL / NSDL / NSE provisional snapshots as they stood at the cutoff (point-in-time
    `first_retrieved_before`), each in its OWN time identity, aged against the edition's
    reference date. NSE's provisional figure is a per-SESSION report: it is current only when it
    is for `nse_session` (POST: the recap session; PRE: the previous session). CDSL / NSDL are
    aged in days (the private desk's thresholds). Unavailable -> degrade; never a replacement
    story."""
    from institutional_flows.models import CDSL, NSDL, NSE
    req = requirement("INSTITUTIONAL_FLOW", edition)
    per, bad = {}, []
    for src in (NSE, CDSL, NSDL):
        snap = ev.institutional_latest(out_dir, src, ref_date, cutoff)
        if snap is None:
            per[src] = {"health": MISSING}
            bad.append(f"{src} no snapshot at the cutoff")
            continue
        d = _key_date(snap)
        age = (ref_date - d).days if d else None
        if src == NSE and nse_session is not None:
            health = HEALTHY if d is not None and d >= nse_session else STALE
        else:
            health = HEALTHY if age is not None and age <= INSTITUTIONAL_STALE_DAYS[src] else STALE
        per[src] = {"health": health, "report_key": snap.report_key, "age_days": age,
                    "first_retrieved_at": snap.first_retrieved_at}
        if health != HEALTHY:
            bad.append(f"{src} latest {snap.report_key}" + (
                f" (expected {nse_session})" if src == NSE and nse_session else
                f" ({age} day(s) old)"))
    capture = ((record or {}).get("details") or {}).get("institutional_flows") or {}
    if capture.get("institutional_flow_status") == "FAILED":
        bad.append(f"capture FAILED ({capture.get('error')})")
    if bad:
        worst = MISSING if all(v["health"] == MISSING for v in per.values()) else STALE
        return mk(edition, "INSTITUTIONAL_FLOW", "INSTITUTIONAL", "INSTITUTIONAL_FLOW",
                  unmet(req), "institutional flow degraded: " + "; ".join(bad),
                  source_health=worst, observed=per,
                  remediation="CDSL/NSDL/NSE source unavailable or not yet published - the "
                              "FLOWS story falls back or is omitted")
    return mk(edition, "INSTITUTIONAL_FLOW", "INSTITUTIONAL", "INSTITUTIONAL_FLOW", PASS,
              "NSE / CDSL / NSDL snapshots current at the cutoff", source_health=HEALTHY,
              observed=per)


# --------------------------------------------------------------------------- market events
_EVENT_HEALTH = {"SUCCESS": HEALTHY, "NOT_SUPPORTED_YET": UNSUPPORTED,
                 "SOURCE_UNAVAILABLE": SOURCE_FAILURE, "PARSE_ERROR": SOURCE_FAILURE,
                 "VALIDATION_FAILED": SOURCE_FAILURE}


def check_market_events(edition, out_dir, ref_session, cutoff, record) -> list:
    """One check per LIVE family, from the REPORT job's own capture record for the session
    (the authoritative per-family status - it alone sees a valid empty SUCCESS), with the
    point-in-time event count beside it. A family failure degrades; none blocks."""
    req = requirement("MARKET_EVENTS", edition)
    families = ev.live_event_families()
    capture = ((record or {}).get("details") or {}).get("market_events") or {}
    results = capture.get("results") or {}
    out = []
    for fam in families:
        known = len(ev.events_known_at(out_dir, fam, cutoff))
        cid = f"MARKET_EVENTS.{fam}"
        if record is None:
            health, why = MISSING, (f"no REPORT capture record for {ref_session} completed by "
                                    "the cutoff")
        elif capture.get("market_events_status") == "FAILED":
            health, why = SOURCE_FAILURE, f"capture FAILED ({capture.get('error')})"
        else:
            r = results.get(fam) or {}
            st = r.get("status")
            health = _EVENT_HEALTH.get(st, MISSING)
            if health == HEALTHY and not (r.get("events") or []):
                health = HEALTHY_EMPTY
            why = f"{st or 'not captured'}" + (f" ({len(r.get('events') or [])} filing(s) "
                                               "this capture)" if st == "SUCCESS" else "")
        status = health_status(req, health)
        msg = f"{fam}: {why}; {known} event(s) known at the cutoff"
        if status != PASS:
            msg = f"{fam} source unavailable ({why}) - Market Events will omit it"
        out.append(mk(edition, cid, "MARKET_EVENTS", "MARKET_EVENTS", status, msg,
                      source_health=health, observed={"capture": why, "known_events": known},
                      as_of=(record or {}).get("completed_at"),
                      remediation=None if status == PASS else
                      "re-run the REPORT job later; validate_market_events_sources.py probes "
                      "reachability"))
    return out


# --------------------------------------------------------------------------- Market Structure
FIFTY_TWO_WEEK = ("NEW_52W_HIGH", "NEW_52W_LOW")


def check_market_structure(edition, out_dir, ref_session, cutoff, intel=None) -> CheckResult:
    """The persisted snapshot for the expected session, available at the cutoff, graded by the
    aggregator's own per-metric status (PUBLISHABLE / PARTIAL >= 95% / SUPPRESSED). Partial
    52-week coverage is the expected P3 state (recent listings lack 252 sessions and count as
    uncovered, never as a zero) - noted, not degraded. PRE only ever shows FIFTY_TWO_WEEK."""
    req = requirement("MARKET_STRUCTURE", edition)
    art, path = ev.structure_artifact(out_dir, ref_session)
    if art is None:
        return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", unmet(req),
                  f"no Market Structure snapshot for {ref_session} - UNDER THE SURFACE is "
                  "omitted", source_health=MISSING, source=path,
                  remediation="the REPORT job's Radar step builds it; "
                              f"python -m market_structure.build --session {ref_session}")
    if art.get("_unreadable"):
        return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", unmet(req),
                  "Market Structure snapshot unreadable", source_health=SOURCE_FAILURE,
                  source=path)
    avail = ev.structure_available_at(art)
    if avail is None or avail > cutoff:
        return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", unmet(req),
                  f"Market Structure snapshot for {ref_session} did not exist at the cutoff "
                  f"(built {avail.isoformat() if avail else 'at an unknown time'})",
                  source_health=MISSING, source=path, as_of=avail.isoformat() if avail else None)
    snap = art.get("snapshot") or {}
    if str(snap.get("session_date")) != ref_session.isoformat():
        return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", unmet(req),
                  f"snapshot describes {snap.get('session_date')}, expected {ref_session}",
                  source_health=STALE, source=path)
    if intel is not None and getattr(intel, "structure_status", None) == "VALIDATION_FAILED":
        return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", unmet(req),
                  "snapshot failed reconciliation on load (VALIDATION_FAILED)",
                  source_health=SOURCE_FAILURE, source=path)
    metrics = snap.get("metrics") or {}
    status_of = {k: (m.get("status"), m.get("coverage_pct")) for k, m in metrics.items()}
    w52 = {k: v for k, v in status_of.items() if k in FIFTY_TWO_WEEK}
    core = {k: v for k, v in status_of.items() if k not in FIFTY_TWO_WEEK}
    relevant = w52 if edition == "PRE" else core
    observed = {"universe": snap.get("universe_label") or snap.get("universe"),
                "constituents": snap.get("constituent_count"),
                "metrics": {k: f"{s} {c}%" for k, (s, c) in status_of.items()}}
    usable = [k for k, (s, _c) in relevant.items() if s in ("PUBLISHABLE", "PARTIAL")]
    partial = [k for k, (s, _c) in relevant.items() if s == "PARTIAL"]
    if not usable:
        return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", unmet(req),
                  "every relevant Market Structure metric is SUPPRESSED (coverage < 95%)",
                  source_health=PARTIAL, observed=observed, source=path,
                  remediation="constituent OHLCV not yet backfilled - UNDER THE SURFACE omitted")
    w52_note = ""
    if edition != "PRE" and any(s != "PUBLISHABLE" for s, _c in w52.values()):
        w52_note = ("; 52-week coverage " + ", ".join(f"{s} {c}%" for s, c in w52.values()) +
                    " (insufficient-history listings uncovered - expected)")
    if partial and edition == "POST":
        return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", unmet(req),
                  "Market Structure partial (>= 95% coverage): " + ", ".join(partial) + w52_note,
                  source_health=PARTIAL, observed=observed, source=path,
                  remediation="some constituents lack session OHLCV - shown with the real "
                              "denominator")
    note = (" (52-week coverage PARTIAL - insufficient-history listings uncovered, expected)"
            if partial else "")
    return mk(edition, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE", PASS,
              f"{len(usable)} usable metric(s) for {ref_session}" + note + w52_note,
              source_health=PARTIAL if partial else HEALTHY, observed=observed, source=path,
              as_of=avail.isoformat())


# --------------------------------------------------------------------------- official snapshots
_SNAP_HEALTH = {"SUCCESS": HEALTHY, "NO_DATA": HEALTHY_EMPTY, "NOT_SUPPORTED": UNSUPPORTED}


def check_official_snapshots(edition, out_dir, session, cutoff) -> CheckResult:
    req = requirement("OFFICIAL_SNAPSHOTS", edition)
    man = ev.official_manifest(out_dir, session)
    if not man:
        return mk(edition, "OFFICIAL_SNAPSHOTS", "OFFICIAL_LISTS", "OFFICIAL_SNAPSHOTS",
                  unmet(req), f"no official snapshot manifest for {session} - EXCHANGE / IPO "
                  "WATCH omitted", source_health=MISSING,
                  remediation="the REPORT job captures them inside the session's window")
    per, bad = {}, []
    for kind, s in sorted((man.get("snapshots") or {}).items()):
        health = _SNAP_HEALTH.get(s.get("status"), SOURCE_FAILURE)
        if health in (HEALTHY, HEALTHY_EMPTY) and not ev.at_or_before(s.get("retrieved_at"), cutoff):
            health = MISSING
        per[kind] = f"{s.get('status')} -> {health}"
        if health not in (HEALTHY, HEALTHY_EMPTY, UNSUPPORTED):
            bad.append(f"{kind} {s.get('status')}" + (" (after the cutoff)" if health == MISSING
                                                      else ""))
    if bad:
        return mk(edition, "OFFICIAL_SNAPSHOTS", "OFFICIAL_LISTS", "OFFICIAL_SNAPSHOTS",
                  unmet(req), "official list(s) unavailable: " + ", ".join(bad),
                  source_health=SOURCE_FAILURE, observed=per,
                  remediation="NSE list unavailable/invalid - that watch section is omitted")
    return mk(edition, "OFFICIAL_SNAPSHOTS", "OFFICIAL_LISTS", "OFFICIAL_SNAPSHOTS", PASS,
              f"official lists validated for {session}", source_health=HEALTHY, observed=per)


# --------------------------------------------------------------------------- editorial plan
def _trace_problems(editorial: dict) -> list:
    from presentation import editorial_arbiter as ea
    trace = (editorial or {}).get("trace") or []
    if not trace:
        return ["editorial decision trace is empty"]
    problems = []
    shown = {r.get("candidate_id") for r in trace if r.get("decision") in ea.SHOWN}
    for r in trace:
        if not r.get("decision"):
            problems.append(f"{r.get('candidate_id')}: no decision")
        if r.get("decision") == "SUPPRESSED_DUPLICATE" and r.get("duplicate_of") not in shown:
            problems.append(f"{r.get('candidate_id')}: duplicate of {r.get('duplicate_of')}, "
                            "which is not shown (unresolved conflict)")
    ids = [r.get("candidate_id") for r in trace]
    if len(ids) != len(set(ids)):
        problems.append("a candidate appears twice in the trace")
    return problems


def check_editorial_post(edition, sb, max_runtime: float) -> CheckResult:
    """The V3 plan, as the storyboard realises it: structurally complete (hook ... close, the
    index story present unless the hook states it), every scene renderable, within the runtime
    budget, a complete decision trace with no unresolved duplicate. A short or quiet edition is
    valid - only the readability floor (MIN_SHORT_DURATION) applies, never a filler minimum."""
    from daily_video.composer import SCENE_CLASSES
    from editorial.config import MIN_SHORT_DURATION
    req = requirement("EDITORIAL_PLAN", edition)
    kinds = [s.kind for s in sb.scenes]
    problems = []
    if not kinds or kinds[0] not in ("DYNAMIC_HOOK", "HOOK"):
        problems.append("does not open with a hook")
    if not kinds or kinds[-1] != "CLOSING":
        problems.append("does not end with the close")
    hook_ids = set((sb.hook_plan or {}).get("fact_ids") or [])
    if not ({"PULSE", "NIFTY"} & set(kinds)) and "nifty.move" not in hook_ids:
        problems.append("no index story (PULSE / NIFTY) and the hook does not state Nifty's move")
    bad = [k for k in kinds if k not in SCENE_CLASSES]
    if bad:
        problems.append("unrenderable scene kind(s): " + ", ".join(bad))
    dur = sb.total_duration
    if dur < MIN_SHORT_DURATION:
        problems.append(f"{dur:.1f}s is below the {MIN_SHORT_DURATION:.0f}s readability floor")
    if dur > max_runtime:
        problems.append(f"{dur:.1f}s exceeds the {max_runtime:.0f}s budget")
    editorial = (sb.post_plan or {}).get("editorial") or {}
    problems += _trace_problems(editorial)
    observed = {"scenes": kinds, "duration": dur, "selected": editorial.get("selected"),
                "lead": editorial.get("lead_section")}
    if problems:
        return mk(edition, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN", unmet(req),
                  "planner output structurally invalid: " + "; ".join(problems),
                  observed=observed, remediation="a planner/storyboard defect - do not render; "
                                                 "inspect editorial_trace")
    return mk(edition, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN", PASS,
              f"{len(kinds)} scenes, {dur:.1f}s, trace complete "
              f"({len(editorial.get('trace') or [])} candidates)", observed=observed)


def check_editorial_pre(edition, plan, sb, max_runtime: float) -> CheckResult:
    from daily_video.composer import SCENE_CLASSES
    from editorial.config import MIN_SHORT_DURATION
    req = requirement("EDITORIAL_PLAN", edition)
    problems = []
    missing = [s for s in ("SETUP", "WATCH") if s not in plan.order]
    if missing:
        problems.append("required section(s) missing: " + ", ".join(missing))
    kinds = [s.kind for s in sb.scenes]
    bad = [k for k in kinds if k not in SCENE_CLASSES]
    if bad:
        problems.append("unrenderable scene kind(s): " + ", ".join(bad))
    dur = sb.total_duration
    if dur < MIN_SHORT_DURATION:
        problems.append(f"{dur:.1f}s is below the {MIN_SHORT_DURATION:.0f}s readability floor")
    if dur > max_runtime:
        problems.append(f"{dur:.1f}s exceeds the {max_runtime:.0f}s ceiling")
    editorial = getattr(plan, "editorial", None) or {}
    problems += _trace_problems(editorial)
    observed = {"order": list(plan.order), "scenes": kinds, "duration": dur,
                "selected": editorial.get("selected")}
    if problems:
        return mk(edition, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN", unmet(req),
                  "planner output structurally invalid: " + "; ".join(problems),
                  observed=observed, remediation="a planner/storyboard defect - do not render")
    note = "" if "OVERNIGHT" in plan.order else " (no OVERNIGHT section - see GLOBAL_CUES)"
    return mk(edition, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN", PASS,
              f"{len(plan.order)} sections, {dur:.1f}s, trace complete{note}", observed=observed)


# --------------------------------------------------------------------------- publication
def check_publication(edition, intent, audit: dict, content_safe: bool, extra_blocks=()) -> CheckResult:
    """The publication boundary, never downgraded: any content failure (safety, language,
    provenance, displayed claims, GMP, source / universe visibility, profile) BLOCKS. A
    rights-only BLOCK (REVIEW_REQUIRED sources under the default policy) is graded by intent:
    SHADOW - the render is compliant and nothing is uploaded -> PASS, recorded; PUBLISH -> FAIL."""
    from operations.daily_check import audit_verdict
    req = requirement("PUBLICATION", edition)
    failed = list(audit.get("failed_checks") or [])
    verdict = audit_verdict(audit.get("final"), failed)
    blocks = list(extra_blocks)
    if not content_safe:
        blocks.append("final content-safety scan is not SAFE")
    if verdict == "CONTENT_FAILURE":
        blocks.append("publication audit content check(s) failed: " +
                      ", ".join(c for c in failed if c != "publication_rights"))
    if verdict == "UNKNOWN":
        blocks.append("publication audit gave no verdict")
    observed = {"audit_final": audit.get("final"), "failed_checks": failed, "verdict": verdict,
                "rights_policy": (audit.get("rights_policy") or {}).get(
                    "PUBLIC_REVIEW_REQUIRED_POLICY")}
    if blocks:
        return mk(edition, "PUBLICATION", "PUBLICATION", "PUBLICATION", unmet(req),
                  "; ".join(blocks), observed=observed,
                  remediation="a template / content defect - nothing may be rendered for "
                              "publication until it is fixed")
    if verdict == "RIGHTS_BLOCK_ONLY":
        if intent == PUBLISH:
            return mk(edition, "PUBLICATION", "PUBLICATION", "PUBLICATION", unmet(req),
                      "rights review required: " + "; ".join(
                          (audit.get("scans") or {}).get("publication_rights", {}).get("issues")
                          or ["publication_rights"]),
                      observed=observed,
                      remediation="publication needs the rights review or the owner's "
                                  "PUBLIC_REVIEW_REQUIRED_POLICY=ATTRIBUTED_EOD")
        return mk(edition, "PUBLICATION", "PUBLICATION", "PUBLICATION", PASS,
                  "content checks pass; RIGHTS_BLOCK_ONLY (expected in shadow - no upload)",
                  observed=observed)
    return mk(edition, "PUBLICATION", "PUBLICATION", "PUBLICATION", PASS,
              "publication audit PASS", observed=observed)


__all__ = ["mk", "unmet_status", "guarded", "skipped", "check_benchmark", "check_temporal", "check_sectors",
           "check_movers", "check_fii_dii", "check_vix_report", "check_institutional",
           "check_market_events", "check_market_structure", "check_official_snapshots",
           "check_editorial_post", "check_editorial_pre", "check_publication", "FAIL", "WARN",
           "PASS", "SKIP", "INSTITUTIONAL_STALE_DAYS"]
