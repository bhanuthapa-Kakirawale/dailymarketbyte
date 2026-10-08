"""POST readiness: is the recap of a COMPLETED Indian session safe to generate right now?

Mirrors `main.run` up to - and not including - `PU.render_post`: the canonical report the REPORT
job built (read-only; never rebuilt), the derived intelligence snapshot (computed in memory,
never saved), `PU.plan_for_post`, the persisted public intelligence (`fetch=False`, as
`evening_full` runs the POST with `--no-fetch-public`), `PU.build_post_storyboard` (deterministic
hook, as production), `PU.post_metadata`, the final content scan and the publication audit.
Nothing is rendered, fetched or written.
"""
from __future__ import annotations

import datetime as dt
import time

from . import checks as C
from . import environment as E
from . import evidence as ev
from .models import (LIVE, PASS, POST_RENDER, PREFLIGHT, REPLAY, SHADOW, SKIP,
                     ReadinessExecutionError, ReadinessResult)

EDITION = "POST"
REPLAY_CUTOFF = dt.time(23, 59, 59)     # public_intelligence's replay convention


def default_cutoff(session: dt.date) -> dt.datetime:
    return dt.datetime.combine(session, REPLAY_CUTOFF, tzinfo=ev.IST)


def evaluate_post(session: dt.date | None = None, as_of: dt.datetime | None = None, *,
                  mode: str | None = None, intent: str = SHADOW, out_dir: str | None = None,
                  calendar=None, env: dict | None = None) -> ReadinessResult:
    """`as_of` is the cutoff (required unless `session` is given - then the session's end of
    day). Nothing reads the wall clock. `env` passes the environment checks' seams (tests)."""
    import config
    from core.trading_calendar import SessionCalendar
    t0 = time.perf_counter()
    try:
        out_dir = out_dir or config.OUT_DIR
        cal = calendar or SessionCalendar()
        if as_of is None:
            if session is None:
                raise ReadinessExecutionError("evaluate_post needs as_of or session")
            as_of = default_cutoff(session)
        as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=ev.IST)
        mode = mode or LIVE
        res = ReadinessResult(edition=EDITION, stage=PREFLIGHT, mode=mode, intent=intent,
                              as_of=as_of, cutoff=as_of)
        res.checks = _checks(res, session, as_of, out_dir, cal, env or {})
    except ReadinessExecutionError:
        raise
    except Exception as exc:
        raise ReadinessExecutionError(f"{type(exc).__name__}: {exc}") from exc
    res.evaluated_at = config.now_ist().isoformat()
    res.runtime = {"seconds": round(time.perf_counter() - t0, 3), "network_calls": 0}
    return res


def _checks(res, session, as_of, out_dir, cal, env) -> list:
    from operations.report_lookup import find_canonical_report
    from operations.sessions import edition_date_for
    from products.report_job import resolve_session
    out = []
    E_ = EDITION

    # ---- session + edition window
    resolved, err = resolve_session(session, as_of, cal)
    if err:
        code, msg = err
        cap = "EDITION_WINDOW" if code == "SESSION_NOT_FINAL" else "SESSION"
        out.append(C.mk(E_, cap, "SESSION", cap, C.unmet_status(E_, cap), f"{code}: {msg}",
                        observed=code, remediation="POST runs only for a completed session "
                        "(after 15:40 IST)" if cap == "EDITION_WINDOW" else
                        "no POST edition for a non-session day"))
        res.session_date = session
        out += _not_evaluated(E_, f"session unresolved ({code})")
        out += _environment(E_, out_dir, None, env)
        return out
    res.session_date = resolved
    res.edition_date = edition_date_for(resolved, cal)
    out.append(C.mk(E_, "SESSION", "SESSION", "SESSION", PASS,
                    f"completed session {resolved} (edition {res.edition_date})",
                    observed=str(resolved)))
    out.append(C.mk(E_, "EDITION_WINDOW", "SESSION", "EDITION_WINDOW", PASS,
                    f"{resolved} is final at the cutoff {as_of:%Y-%m-%d %H:%M} IST"))

    # ---- canonical report (read-only history)
    history, hist_detail = None, ""
    try:
        history = ev.open_history(out_dir)
    except Exception as exc:
        hist_detail = f"{type(exc).__name__}: {exc}"
    try:
        report = None
        if history is None:
            out.append(C.mk(E_, "CANONICAL_REPORT", "REPORT", "CANONICAL_REPORT",
                            C.unmet_status(E_, "CANONICAL_REPORT"),
                            f"history database unavailable ({hist_detail})",
                            remediation="run the REPORT job first (scripts\\run_evening.bat)"))
        else:
            lookup = find_canonical_report(resolved, history=history)
            if not lookup.found:
                out.append(C.mk(E_, "CANONICAL_REPORT", "REPORT", "CANONICAL_REPORT",
                                C.unmet_status(E_, "CANONICAL_REPORT"),
                                f"{lookup.status}: {lookup.reason}", observed=lookup.status,
                                remediation="run the REPORT job first (scripts\\run_evening.bat); "
                                            "POST never renders an unvalidated inline build as "
                                            "'ready'"))
            elif not lookup.report.publication_ready:
                out.append(C.mk(E_, "CANONICAL_REPORT", "REPORT", "CANONICAL_REPORT",
                                C.unmet_status(E_, "CANONICAL_REPORT"),
                                "report failed data validation: " + "; ".join(
                                    lookup.report.validation_summary.blocking_issues),
                                observed=lookup.report_id))
            else:
                report = lookup.report
                out.append(C.mk(E_, "CANONICAL_REPORT", "REPORT", "CANONICAL_REPORT", PASS,
                                f"canonical report {lookup.report_id} (publication-ready)",
                                observed=lookup.report_id, source=lookup.path,
                                as_of=report.generated_at.isoformat()
                                if report.generated_at else None))
        record = ev.report_record_at(out_dir, resolved, as_of)
        if report is not None:
            out += C.guarded(E_, "BENCHMARK", "MARKET_DATA", "BENCHMARK",
                             C.check_benchmark, E_, report, resolved)
            out += C.guarded(E_, "TEMPORAL_SAFETY", "TEMPORAL", "TEMPORAL_SAFETY",
                             C.check_temporal, E_, report, as_of, resolved)
            out += C.guarded(E_, "SECTORS", "MARKET_DATA", "SECTORS",
                             C.check_sectors, E_, report, resolved)
            out += C.guarded(E_, "MOVERS", "MARKET_DATA", "MOVERS", C.check_movers, E_, report)
            out += C.guarded(E_, "FII_DII", "FLOWS", "FII_DII",
                             C.check_fii_dii, E_, report, resolved)
            out += C.guarded(E_, "INDIA_VIX", "MARKET_DATA", "INDIA_VIX",
                             C.check_vix_report, E_, report, resolved)
        else:
            for cid, cat in (("BENCHMARK", "MARKET_DATA"), ("TEMPORAL_SAFETY", "TEMPORAL"),
                             ("SECTORS", "MARKET_DATA"), ("MOVERS", "MARKET_DATA"),
                             ("FII_DII", "FLOWS"), ("INDIA_VIX", "MARKET_DATA")):
                out.append(C.skipped(E_, cid, cat, cid, "no usable canonical report"))
        out += C.guarded(E_, "INSTITUTIONAL_FLOW", "INSTITUTIONAL", "INSTITUTIONAL_FLOW",
                         C.check_institutional, E_, out_dir, resolved, as_of, record,
                         nse_session=resolved)
        out += C.guarded(E_, "MARKET_EVENTS", "MARKET_EVENTS", "MARKET_EVENTS",
                         C.check_market_events, E_, out_dir, resolved, as_of, record)
        out += C.guarded(E_, "OFFICIAL_SNAPSHOTS", "OFFICIAL_LISTS", "OFFICIAL_SNAPSHOTS",
                         C.check_official_snapshots, E_, out_dir, resolved, as_of)

        # ---- the plan + storyboard + publication audit, exactly as main.run builds them
        built = {}
        if report is not None:
            out += C.guarded(E_, "INTELLIGENCE", "INTELLIGENCE", "INTELLIGENCE",
                             _intelligence, built, report, history)
            out += C.guarded(E_, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN",
                             _plan, built, report, resolved, res, out_dir, as_of, cal)
        else:
            out.append(C.skipped(E_, "INTELLIGENCE", "INTELLIGENCE", "INTELLIGENCE",
                                 "no usable canonical report"))
            out.append(C.skipped(E_, "EDITORIAL_PLAN", "EDITORIAL", "EDITORIAL_PLAN",
                                 "no usable canonical report"))
        out += C.guarded(E_, "MARKET_STRUCTURE", "MARKET_STRUCTURE", "MARKET_STRUCTURE",
                         C.check_market_structure, E_, out_dir, resolved, as_of,
                         built.get("intel"))
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
                    "public profile shows no Radar story (selection is the REPORT job's)"))
    out.append(C.mk(E_, "GIFT_NIFTY", "GLOBAL", "GIFT_NIFTY", SKIP,
                    "POST never shows GIFT Nifty from the exchange (PRE-only topic)"))
    out += _output_collision(E_, out_dir, resolved)
    out += _environment(E_, out_dir, history is not None or not hist_detail, env, hist_detail)
    return out


# --------------------------------------------------------------------------- stages
def _intelligence(built, report, history):
    import intelligence
    snap = intelligence.build_snapshot(report, history)        # in memory - never saved here
    built["snapshot"] = snap
    n = len(getattr(snap, "insights", []) or [])
    return C.mk(EDITION, "INTELLIGENCE", "INTELLIGENCE", "INTELLIGENCE", PASS,
                f"historical context derivable ({n} insight(s))", observed=n)


def _plan(built, report, session, res, out_dir, as_of, cal):
    from daily_video.storyboard import POST_MAX_RUNTIME
    from operations.sessions import next_session
    from presentation.public_intelligence import load_public_intelligence
    from products import post_unified as PU
    from publication import resolve_profile
    profile = resolve_profile(None)
    res.profile = profile.value
    plan = PU.plan_for_post(report, built.get("snapshot"), now=as_of, profile=profile)
    edition = report.report_date or res.edition_date
    intel = load_public_intelligence(
        session, next_session(session, cal) or edition, out_dir, fetch=False,
        now_iso=as_of.isoformat(), snapshot_session=session, replay=res.mode == REPLAY,
        now=as_of, capture_mode="POST_FALLBACK", calendar=cal)
    built["intel"] = intel
    import os
    sb, pres = PU.build_post_storyboard(report, plan, profile=profile, intelligence=intel,
                                        hook_ai=False, radar_dir=os.path.join(out_dir, "radar"),
                                        sources={"market_report": "canonical"})
    built.update(sb=sb, pres=pres, edition=edition)
    return C.check_editorial_post(EDITION, sb, POST_MAX_RUNTIME)


def _publication(built, intent):
    from core.content_safety import SafetyStatus, scan_publication
    from daily_video.composer import AUDIO_ENABLED
    from daily_video.public_storyboard import audit_storyboard
    from main import collect_public_text
    from products import post_unified as PU
    sb, pres, edition = built["sb"], built["pres"], built["edition"]
    info = {"today_str": edition.strftime("%A, %d %B %Y"),
            "today_short": edition.strftime("%a %d %b"),
            "recap_str": pres.session_date.strftime("%a, %d %b %Y")}
    meta = PU.post_metadata(sb, pres, info)
    scan = scan_publication(collect_public_text(sb, meta))
    audit = audit_storyboard(sb, "POST_UNIFIED", metadata=meta, video_path=None, synthetic=False,
                             audio={"audio_stream": False, "audio_phase_enabled": AUDIO_ENABLED})
    built["audit"] = audit
    return C.check_publication(EDITION, intent, audit, scan.status is SafetyStatus.SAFE)


def _output_collision(E_, out_dir, session) -> list:
    path, m = ev.post_manifest(out_dir, session)
    if m is None:
        return [C.mk(E_, "OUTPUT", "STORAGE", "STORAGE", PASS,
                     f"no POST artifact for {session} yet - a fresh render is written")]
    return [C.mk(E_, "OUTPUT", "STORAGE", "STORAGE", PASS,
                 f"a POST artifact for {session} exists ({path}); evening_full does not "
                 "re-render a passing one (--rerender-post); the canonical report is never "
                 "rewritten", source=path)]


def _not_evaluated(E_, why) -> list:
    caps = (("CANONICAL_REPORT", "REPORT"), ("BENCHMARK", "MARKET_DATA"),
            ("TEMPORAL_SAFETY", "TEMPORAL"), ("EDITORIAL_PLAN", "EDITORIAL"),
            ("PUBLICATION", "PUBLICATION"))
    return [C.skipped(E_, c, cat, c, why) for c, cat in caps]


def _environment(E_, out_dir, history_ok, env, hist_detail="") -> list:
    if history_ok is None:
        try:
            ev.open_history(out_dir).close()
            history_ok = True
        except Exception as exc:
            history_ok, hist_detail = False, f"{type(exc).__name__}: {exc}"
    return [*C.guarded(E_, "RENDERER", "RENDERING", "RENDERER", E.check_renderer, E_,
                       env.get("importer")),
            *C.guarded(E_, "FFMPEG", "RENDERING", "FFMPEG", E.check_ffmpeg, E_,
                       env.get("ffmpeg_resolver"), env.get("ffmpeg_runner")),
            *C.guarded(E_, "FONTS", "RENDERING", "FONTS", E.check_fonts, E_,
                       env.get("font_reporter")),
            E.check_audio(E_),
            *C.guarded(E_, "STORAGE", "STORAGE", "STORAGE", E.check_storage, E_, out_dir,
                       history_ok, hist_detail, env.get("write_probe"), env.get("disk_usage"))]


__all__ = ["evaluate_post", "default_cutoff", "EDITION", "POST_RENDER"]
