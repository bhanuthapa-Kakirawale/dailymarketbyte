"""POST freeze - the publication gate in front of every top-gainer / top-loser / Movers claim.

Two deterministic checks, both read from the canonical report (nothing is fetched or
recomputed here):

  1. Universe coverage. `report.metadata["movers_coverage"]` is the acquisition audit written
     by `market.get_movers_audited`: universe_expected, universe_observed, universe_validated
     and coverage_pct (= validated / expected). The ranking is publishable only when
     coverage_pct >= MOVERS_MIN_COVERAGE_PCT. A report with no coverage record (every report
     built before this gate) is UNKNOWN and treated like insufficient coverage: a "top gainer"
     over an unknown universe is exactly the silent partial ranking the gate exists to stop.
  2. Move validation. A mover row that carries a non-publishable `validation` verdict from
     `core.move_guard` is held back (acquisition already ranks only validated moves; this is
     the defensive second look for any row that reaches the report some other way).

Suppression is recorded - reason, counts and held rows - and nothing is removed from the
report or from history.
"""
from __future__ import annotations

from core.move_guard import PUBLISHABLE_STATUSES

from .config import MOVERS_MIN_COVERAGE_PCT


def movers_coverage_verdict(report) -> dict:
    cov = dict((getattr(report, "metadata", None) or {}).get("movers_coverage") or {})
    out = {"universe_expected": cov.get("universe_expected"),
           "universe_observed": cov.get("universe_observed"),
           "universe_validated": cov.get("universe_validated"),
           "coverage_pct": cov.get("coverage_pct"),
           "min_coverage_pct": MOVERS_MIN_COVERAGE_PCT,
           "guard_excluded": [{"symbol": r.get("symbol"), "change_pct": r.get("pct"),
                               "status": (r.get("validation") or {}).get("status"),
                               "reason": (r.get("validation") or {}).get("reason")}
                              for r in cov.get("excluded") or []]}
    pct = cov.get("coverage_pct")
    if not cov or pct is None:
        out.update(status="UNKNOWN", publishable=False,
                   reason="no universe coverage recorded for this report's mover ranking "
                          "(built before the coverage gate) - ranking unproven")
    elif float(pct) < MOVERS_MIN_COVERAGE_PCT:
        out.update(status="INSUFFICIENT", publishable=False,
                   reason=f"universe coverage {float(pct):.1f}% "
                          f"({cov.get('universe_validated')}/{cov.get('universe_expected')}) "
                          f"is below {MOVERS_MIN_COVERAGE_PCT:.0f}%")
    else:
        out.update(status="OK", publishable=True,
                   reason=f"universe coverage {float(pct):.1f}% "
                          f"({cov.get('universe_validated')}/{cov.get('universe_expected')})")
    return out


def row_publishable(row: dict) -> bool:
    """False only for a row carrying an explicit non-publishable guard verdict."""
    v = row.get("validation")
    return not v or v.get("status") in PUBLISHABLE_STATUSES


__all__ = ["movers_coverage_verdict", "row_publishable"]
