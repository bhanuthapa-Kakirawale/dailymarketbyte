"""Extreme-move / corporate-action / bad-data guard for single-stock moves (POST freeze).

A day-over-day move is published - as a Movers row, a "top gainer/loser" claim or a Market
Radar story - only after it passes the deterministic checks below. The guard never deletes or
rewrites a reading: it returns a verdict and its reasons, the caller excludes an unpublishable
move from what the Short shows, and the raw values stay in the audit output.

Checks, in order (the first failing one decides the status):

  1. MISSING_PREVIOUS_CLOSE   no usable previous close / close (None or <= 0).
  2. STALE_PREVIOUS_CLOSE     the previous close is not from the expected previous session, or
                              the latest close is not from the session being described - a
                              silently multi-day change must never be called a one-day move.
  3. INCONSISTENT_OHLC        the session's own bar contradicts itself (low above the open or
                              close, high below them, non-positive prices).
  4. INCONSISTENT_CHANGE      the stated % change does not match the two closes it came from.
  Only a move of at least EXTREME_MOVE_PCT continues to the discontinuity checks:
  5. CORPORATE_ACTION_SUSPECTED  close / previous close sits within RATIO_TOLERANCE of a
                              standard split / bonus / consolidation ratio AND the move happened
                              at the open (a price-basis change, not trading).
  6. UNRESOLVED_EXTREME_MOVE  |move| >= UNRESOLVABLE_MOVE_PCT. With one price source and no
                              corporate-action calendar, a move this large cannot be told apart
                              from an unadjusted corporate action or a bad print - it is held.
  7. PRICE_DISCONTINUITY      an extreme move without volume confirmation (relative volume
                              unknown or below VOLUME_CONFIRM_RVOL): genuine large moves trade
                              heavily; a price that jumped on ordinary volume is suspect.
  Otherwise VALIDATED_EXTREME (publishable, flagged in the audit) or VALIDATED.

Nothing here names a stock. Thresholds are the only knobs and they live in this module.
There is no dividend / corporate-action feed in the pipeline (no paid APIs), so detection is
signature-based; a genuine >= 20% one-day move is withheld by design - accuracy over
completeness - and recorded in the audit with its raw values.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

GUARD_VERSION = "1.0"

EXTREME_MOVE_PCT = 10.0          # |move| from here on needs the discontinuity checks
UNRESOLVABLE_MOVE_PCT = 20.0     # |move| from here on is never published single-source
VOLUME_CONFIRM_RVOL = 1.5        # an extreme move needs at least this relative volume
OPEN_GAP_SHARE = 0.8             # >= this share of the move already present at the open
RATIO_TOLERANCE = 0.02           # relative distance to a standard corporate-action ratio
CHANGE_TOLERANCE_PP = 0.05       # stated % vs % recomputed from the two closes
OHLC_TOLERANCE = 1e-4            # relative slack for float noise in the bar checks

# close / previous close after a split (1/k), a bonus a:b (b/(a+b)) or a consolidation (k).
CORPORATE_ACTION_RATIOS = {
    "1:2 split or 1:1 bonus": 1 / 2, "1:3 split or 2:1 bonus": 1 / 3,
    "1:4 split or 3:1 bonus": 1 / 4, "1:5 split or 4:1 bonus": 1 / 5,
    "1:10 split": 1 / 10, "1:2 bonus": 2 / 3, "1:3 bonus": 3 / 4, "1:4 bonus": 4 / 5,
    "1:5 bonus": 5 / 6, "2:1 consolidation": 2.0, "3:1 consolidation": 3.0,
    "4:1 consolidation": 4.0, "5:1 consolidation": 5.0, "10:1 consolidation": 10.0,
    "3:2 consolidation": 1.5,
}

VALIDATED = "VALIDATED"
VALIDATED_EXTREME = "VALIDATED_EXTREME"
PUBLISHABLE_STATUSES = frozenset({VALIDATED, VALIDATED_EXTREME})


@dataclass(frozen=True)
class MoveValidation:
    status: str
    reason: str
    change_pct: float | None
    checks: tuple = ()
    details: dict = field(default_factory=dict)

    @property
    def publishable(self) -> bool:
        return self.status in PUBLISHABLE_STATUSES

    def to_dict(self) -> dict:
        return {"status": self.status, "publishable": self.publishable, "reason": self.reason,
                "change_pct": None if self.change_pct is None else round(self.change_pct, 4),
                "checks": list(self.checks), "details": dict(self.details),
                "guard_version": GUARD_VERSION}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN -> None


def _date(v):
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def validate_move(*, close, prev_close, open_=None, high=None, low=None, change_pct=None,
                  relative_volume=None, session_date=None, expected_session_date=None,
                  prev_date=None, expected_prev_date=None) -> MoveValidation:
    """The verdict on one stock's one-session move. Pure: same inputs, same verdict."""
    close, prev = _num(close), _num(prev_close)
    o, h, l = _num(open_), _num(high), _num(low)
    rvol, stated = _num(relative_volume), _num(change_pct)
    checks = []

    def verdict(status, reason, pct=None, **details):
        return MoveValidation(status, reason, pct, tuple(checks), details)

    checks.append("previous_close_present")
    if close is None or prev is None or close <= 0 or prev <= 0:
        return verdict("MISSING_PREVIOUS_CLOSE", "no usable close / previous close",
                       close=close, prev_close=prev)
    pct = (close / prev - 1) * 100

    checks.append("session_alignment")
    sd, esd = _date(session_date), _date(expected_session_date)
    if sd is not None and esd is not None and sd != esd:
        return verdict("STALE_PREVIOUS_CLOSE", f"latest close is from {sd}, not {esd}", pct,
                       session_date=str(sd), expected_session_date=str(esd))
    pd_, epd = _date(prev_date), _date(expected_prev_date)
    if pd_ is not None and epd is not None and pd_ != epd:
        return verdict("STALE_PREVIOUS_CLOSE",
                       f"previous close is from {pd_}, not the previous session {epd}", pct,
                       prev_date=str(pd_), expected_prev_date=str(epd))

    if any(v is not None for v in (o, h, l)):
        checks.append("ohlc_consistency")
        bar = [v for v in (o, h, l) if v is not None]
        if any(v <= 0 for v in bar):
            return verdict("INCONSISTENT_OHLC", "non-positive price in the session bar", pct,
                           open=o, high=h, low=l, close=close)
        hi_ok = h is None or h >= max(v for v in (o, close) if v is not None) * (1 - OHLC_TOLERANCE)
        lo_ok = l is None or l <= min(v for v in (o, close) if v is not None) * (1 + OHLC_TOLERANCE)
        if not (hi_ok and lo_ok) or (h is not None and l is not None and h < l):
            return verdict("INCONSISTENT_OHLC", "open/close outside the session's high-low", pct,
                           open=o, high=h, low=l, close=close)

    if stated is not None:
        checks.append("change_consistency")
        if abs(stated - pct) > CHANGE_TOLERANCE_PP:
            return verdict("INCONSISTENT_CHANGE",
                           f"stated {stated:+.2f}% but the closes give {pct:+.2f}%", pct,
                           stated_change_pct=round(stated, 4))

    if abs(pct) < EXTREME_MOVE_PCT:
        return verdict(VALIDATED, "passed consistency checks", pct)

    ratio = close / prev
    gap_share = None
    if o is not None and close != prev:
        gap_share = (o - prev) / (close - prev)
    at_open = gap_share is None or gap_share >= OPEN_GAP_SHARE
    details = {"ratio": round(ratio, 5), "open_gap_share": None if gap_share is None
               else round(gap_share, 3), "relative_volume": rvol}

    checks.append("corporate_action_ratio")
    for label, r in CORPORATE_ACTION_RATIOS.items():
        if abs(ratio / r - 1) <= RATIO_TOLERANCE and at_open:
            return verdict("CORPORATE_ACTION_SUSPECTED",
                           f"close/previous close {ratio:.3f} matches a {label} ({r:.3f}) and the "
                           f"move was already there at the open", pct, **details,
                           matched_ratio=label)

    checks.append("unresolvable_magnitude")
    if abs(pct) >= UNRESOLVABLE_MOVE_PCT:
        return verdict("UNRESOLVED_EXTREME_MOVE",
                       f"{pct:+.2f}% is at/above the {UNRESOLVABLE_MOVE_PCT:.0f}% single-source "
                       f"limit; cannot rule out an unadjusted corporate action or bad print",
                       pct, **details)

    checks.append("volume_confirmation")
    if rvol is None or rvol < VOLUME_CONFIRM_RVOL:
        return verdict("PRICE_DISCONTINUITY",
                       f"{pct:+.2f}% without volume confirmation (relative volume "
                       f"{'unknown' if rvol is None else f'{rvol:.2f}x'} < {VOLUME_CONFIRM_RVOL}x)",
                       pct, **details)
    return verdict(VALIDATED_EXTREME, f"extreme {pct:+.2f}% move, internally consistent and "
                   f"volume-confirmed ({rvol:.2f}x)", pct, **details)


__all__ = ["validate_move", "MoveValidation", "PUBLISHABLE_STATUSES", "VALIDATED",
           "VALIDATED_EXTREME", "EXTREME_MOVE_PCT", "UNRESOLVABLE_MOVE_PCT",
           "VOLUME_CONFIRM_RVOL", "CORPORATE_ACTION_RATIOS", "GUARD_VERSION"]
