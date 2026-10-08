"""The PRE / POST capability matrix (PK-C): what each edition REQUIRES, what is OPTIONAL (its
absence degrades the edition - a section is omitted - but never blocks it) and what does not
apply. Traced from the production paths, not from a wish list:

  PRE  - `products.premarket.build_real_brief` + `render_pre`: blocks only on the previous
         session's canonical report (`require_previous_report`), the content / language /
         provenance / publication gates and the runtime ceiling. SETUP and WATCH are forced;
         OVERNIGHT appears only when a cue is fresh, so missing cues degrade.
  POST - `main.run`: blocks on the canonical report (gate 1 data QA), the content scan and the
         publication audit's content checks; every other section passes its own value test or
         is omitted (`presentation.post_plan`, the arbiter).

A check's severity is derived from this table (`models.SEVERITY`), so the table IS the policy.
docs/PRODUCTION_READINESS.md reproduces it.
"""
from __future__ import annotations

from .models import NOT_APPLICABLE as NA
from .models import OPTIONAL as OPT
from .models import REQUIRED as REQ

# capability -> (PRE, POST, what decides it)
MATRIX = {
    "SESSION":            (REQ, REQ, "core.trading_calendar / products.report_job.resolve_session"),
    "EDITION_WINDOW":     (REQ, REQ, "PRE before the 09:15 open; POST session final (15:40 IST)"),
    "CANONICAL_REPORT":   (REQ, REQ, "operations.report_lookup.find_canonical_report + publication_ready"),
    "BENCHMARK":          (REQ, REQ, "Nifty 50 close fact, eligible, dated to the expected session"),
    "TEMPORAL_SAFETY":    (REQ, REQ, "report existed at the cutoff; PRE inputs carry no India data on/after the PRE date"),
    "GLOBAL_CUES":        (OPT, NA, "providers.premarket.fetch_premarket_quotes + core.freshness"),
    "INDIA_VIX":          (OPT, OPT, "PRE: acquisition freshness; POST: report fact"),
    "GIFT_NIFTY":         (NA, NA, "operations.gift_policy (closed by default - never a degradation)"),
    "EVENT_SCHEDULE":     (OPT, NA, "operations.official_events.check_official_events"),
    "SECTORS":            (OPT, OPT, "report sector facts"),
    "MOVERS":             (NA, OPT, "editorial.movers_gate.movers_coverage_verdict"),
    "FII_DII":            (OPT, OPT, "report FII/DII facts (NSE provisional)"),
    "INSTITUTIONAL_FLOW": (OPT, OPT, "institutional_flows.store.load_latest (point-in-time)"),
    "MARKET_EVENTS":      (OPT, OPT, "REPORT-job capture record + market_events.store.list_events(as_of)"),
    "MARKET_STRUCTURE":   (OPT, OPT, "market_structure_<D>.json metric status / coverage"),
    "OFFICIAL_SNAPSHOTS": (OPT, OPT, "official_snapshots manifest (F&O ban / ASM / GSM / IPO lists)"),
    "INTELLIGENCE":       (OPT, OPT, "intelligence.load_snapshot (derived historical context)"),
    "RADAR":              (NA, NA, "public profile shows no Radar story / stock watch"),
    "EDITORIAL_PLAN":     (REQ, REQ, "Editorial Planner V3 plan + storyboard structure"),
    "PUBLICATION":        (REQ, REQ, "content scan + publication audit (rights by intent)"),
    "RENDERER":           (REQ, REQ, "daily_video.composer imports"),
    "FFMPEG":             (REQ, REQ, "video._ffmpeg / imageio_ffmpeg"),
    "FONTS":              (REQ, REQ, "daily_video.typography.font_report (a TrueType font resolves)"),
    "AUDIO_ASSETS":       (NA, NA, "daily_video.composer.AUDIO_ENABLED is False"),
    "STORAGE":            (REQ, REQ, "output root writable, history DB readable, disk space"),
}

EDITIONS = ("PRE", "POST")


def requirement(capability: str, edition: str) -> str:
    pre, post, _ = MATRIX[capability]
    return pre if edition == "PRE" else post


def as_table() -> list:
    return [{"capability": k, "PRE": v[0], "POST": v[1], "authority": v[2]}
            for k, v in MATRIX.items()]


__all__ = ["MATRIX", "EDITIONS", "requirement", "as_table"]
