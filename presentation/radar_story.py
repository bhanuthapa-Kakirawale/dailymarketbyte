"""Radar stock story - presentation model (Phase 2).

    RadarStory (validated detector output) + RadarVisualEvidence (local OHLCV, no lookahead)
        -> build_radar_story_model() -> RadarStoryModel -> daily_video renderer

Everything the renderer draws is decided here, from fields the Radar pipeline already
validated; the renderer computes nothing. The model is deliberately small - one event, one
reference level, one supporting fact, one sentence - because a Radar scene has ~2 seconds to be
understood on a phone:

    which stock  ->  what happened (one event, on the chart)  ->  one supporting fact  ->  one line

Rules (all deterministic, none model-written):
- ONE event: the story's highest-priority detector event (longer-window range breaks first).
  Its reference is the detector's own level (prior-N-day high/low, or the N-day average).
- AT MOST TWO prices on screen: the reference level and the latest close.
- EXACTLY ONE supporting fact, in LOCKED priority:
    1. volume - when the Radar volume detector flagged the session (ELEVATED / UNUSUAL /
       EXTREME), worded as that level and never upgraded;
    2. otherwise price location within the session's own range (near the high / near the low
       / mid-range), from the session's stored OHLC.
- Relative strength, sector/peer comparison and novelty stay in the backend; they are not part
  of the published scene.
- The takeaway is a fixed template of (event, supporting fact): factual, no prediction, no
  cause, no recommendation.

Stories WITHOUT a validated technical event (POST final edge-case patch - verified 24 Sep 2026:
COROMANDEL was selected on unusual volume + relative performance with no STRUCTURE event, and
rendered an empty panel reading "Something changed on the chart"):
- VOLUME family - the Radar volume detector flagged the session: volume IS the story. The
  candles stay as neutral price context (no ring, no callout, no reference level), the volume
  histogram is the hero with the detector's own multiple, and a dashed line at the prior
  20-session average - drawn only when the local window reproduces the detector's multiple.
  Takeaway: "Trading volume was <level words> while price finished <x>% lower/higher."
- SESSION family - neither an event nor flagged volume: neutral candles + where the close sat
  in the day's range. Takeaway states only the session move and that location.
- No family ever claims a chart event it does not have, and the no-chart TEXT card never reuses
  the Radar planner's `context_line` (it carries internal labels such as "Positive Alignment" -
  the composite's multi-session direction, not the session's move).
- Every direction word (higher/lower, colour of the close tag and change badge) comes from the
  story's own validated session move (`price_change_pct`), never from `story["direction"]`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from config import fmt_in

try:
    from video import RS
except Exception:   # pragma: no cover - video import is always available in the app
    RS = "Rs "

CHART_SESSIONS = 24            # 20-day range + the event session + a little lead-in
NEAR_EXTREME = 0.25            # close within the top/bottom quarter of the day's range

EVENT_PRIORITY = ("BREAK_ABOVE_50D_RANGE", "BREAK_BELOW_50D_RANGE", "BREAK_ABOVE_20D_RANGE",
                  "BREAK_BELOW_20D_RANGE", "CROSS_ABOVE_SMA50", "CROSS_BELOW_SMA50",
                  "CROSS_ABOVE_SMA20", "CROSS_BELOW_SMA20", "RANGE_COMPRESSION")
VOLUME_WORDS = {"ELEVATED": "above-normal volume", "UNUSUAL": "unusually high volume",
                "EXTREME": "exceptionally high volume"}
EVENT_SENTENCE = {
    "RANGE_UP": "Price broke above its recent range",
    "RANGE_DOWN": "Price fell below its recent range",
    "MA_UP": "Price moved above its {w}-day average",
    "MA_DOWN": "Price moved below its {w}-day average",
    "COMPRESSION": "Its trading range tightened",
}
EVENT_CALLOUT = {"RANGE_UP": "Broke out here", "RANGE_DOWN": "Fell out here",
                 "MA_UP": "Crossed above here", "MA_DOWN": "Crossed below here",
                 "COMPRESSION": "Range tightened"}
LOCATION_LABEL = {"HIGH": "CLOSED NEAR THE DAY'S HIGH", "LOW": "CLOSED NEAR THE DAY'S LOW",
                  "MID": "CLOSED MID-RANGE ON THE DAY"}
LOCATION_CLAUSE = {"HIGH": "closed near the day's high", "LOW": "closed near the day's low"}
# No-event families (never a chart event): what the story is built on.
NO_EVENT_FAMILIES = ("VOLUME", "SESSION")
VOLUME_LEVEL_ADJ = {"ELEVATED": "above normal", "UNUSUAL": "unusually high",
                    "EXTREME": "exceptionally high"}
VOLUME_AVG_SESSIONS = 20        # RVOL v2.0: mean of the PRIOR 20 sessions (market.relative_volume)
VOLUME_AVG_LABEL = "20-DAY AVG"
VOLUME_AVG_TOLERANCE = 0.02     # local window must reproduce the detector's multiple within 2%


def rupees(v: float) -> str:
    return f"{RS}{fmt_in(v, 1)}"


@dataclass
class RadarStoryModel:
    symbol: str
    display_name: str
    display_change: str
    change_positive: bool
    story_index: int
    story_count: int
    event_type: str | None
    event_family: str            # RANGE_UP/RANGE_DOWN/MA_UP/MA_DOWN/COMPRESSION; no event:
    #                              VOLUME / SESSION (chart) or TEXT (no local chart evidence)
    event_callout: str
    reference_label: str
    reference_level: float | None
    reference_display: str
    latest_close: float | None
    latest_close_display: str
    close_label: str
    candles: dict | None          # {"open","high","low","close"} for the visible window
    band: dict | None             # {"i0","i1","low","high"} - the prior range, window indices
    ma_series: list | None        # ONLY the event's own average, window-aligned
    support: dict                 # {"kind": "VOLUME"|"DAY_RANGE", ...}
    takeaway: str
    duration: float
    warnings: list = field(default_factory=list)

    @property
    def price_values(self) -> list:
        """Every exact price the scene displays - at most two, by construction."""
        return [v for v in (self.reference_display, self.latest_close_display) if v]

    def strings(self) -> dict:
        """Every viewer-visible string, for the storyboard's declared texts."""
        s = self.support
        return {"symbol": self.symbol, "company": self.display_name, "change": self.display_change,
                "callout": self.event_callout, "reference_label": self.reference_label,
                "reference_value": self.reference_display, "close_label": self.close_label,
                "close_value": self.latest_close_display, "support_label": s.get("label", ""),
                "support_caption": s.get("caption", ""), "support_low": s.get("low_label", ""),
                "support_high": s.get("high_label", ""), "takeaway": self.takeaway,
                **({"support_average": s["average_label"]} if s.get("average_label") else {})}

    def to_dict(self) -> dict:
        return asdict(self)


def _family(event: str | None) -> str:
    if not event:
        return "TEXT"
    if event == "RANGE_COMPRESSION":
        return "COMPRESSION"
    up = "ABOVE" in event
    return ("RANGE_" if "RANGE" in event else "MA_") + ("UP" if up else "DOWN")


def _window(event: str | None) -> int | None:
    if not event:
        return None
    return 50 if "50" in event else (20 if "20" in event else None)


def move_clause(chg: float | None) -> str:
    """The session move in words, from the same one-decimal rounding as the change badge."""
    if chg is None:
        return ""
    shown = f"{abs(float(chg)):.1f}"          # formatted exactly like the change badge
    if float(shown) == 0:
        return "finished virtually unchanged"
    return f"finished {shown}% {'higher' if chg > 0 else 'lower'}"


def volume_takeaway(level: str, chg: float | None) -> str:
    move = move_clause(chg)
    lead = f"Trading volume was {VOLUME_LEVEL_ADJ[level]}"
    return f"{lead} while price {move}." if move else f"{lead}."


def session_takeaway(chg: float | None, loc: str | None) -> str:
    move = move_clause(chg)
    if not move:
        return ""
    clause = f" and {LOCATION_CLAUSE[loc]}" if loc in LOCATION_CLAUSE else ""
    return f"Price {move}{clause}."


def _volume_average(vols: list, rvol: float, warnings: list) -> float | None:
    """The prior-20-session average volume the detector's multiple implies (latest / RVOL),
    drawn only when the visible window itself reproduces it - otherwise no line at all."""
    if not rvol or rvol <= 0 or len(vols) < VOLUME_AVG_SESSIONS + 1 or not vols[-1]:
        warnings.append("volume average line omitted: not enough local sessions to show it")
        return None
    implied = vols[-1] / rvol
    local = sum(vols[-VOLUME_AVG_SESSIONS - 1:-1]) / VOLUME_AVG_SESSIONS
    if implied <= 0 or abs(local / implied - 1) > VOLUME_AVG_TOLERANCE:
        warnings.append(f"volume average line omitted: local window average {local:.0f} does not "
                        f"reproduce the detector's multiple (implies {implied:.0f})")
        return None
    return float(implied)


def primary_event(events) -> str | None:
    return next((e for e in EVENT_PRIORITY if e in (events or [])), None)


def day_location(o, h, l, c) -> str | None:
    """Where the session closed inside its own high-low range. None if the range is flat."""
    if None in (h, l, c) or h <= l:
        return None
    pos = (c - l) / (h - l)
    if pos >= 1 - NEAR_EXTREME:
        return "HIGH"
    if pos <= NEAR_EXTREME:
        return "LOW"
    return "MID"


def build_radar_story_model(story_pres: dict, story: dict, evidence, index: int,
                            count: int) -> RadarStoryModel:
    sym = story_pres["instrument"]
    chg = story.get("price_change_pct")
    change = story_pres.get("price_change_display") or (f"{chg:+.1f}%" if chg is not None else "")
    events = (story.get("technical_context") or {}).get("events") or []
    event = primary_event(events)
    fam = _family(event)
    w = _window(event)
    vol = story.get("volume_context") or {}
    warnings = []

    base = dict(symbol=sym, display_name=story_pres.get("company_name") or "",
                display_change=change, change_positive=(chg or 0) >= 0, story_index=index,
                story_count=count, event_type=event)

    level = (vol.get("level") or "").upper()
    rvol = vol.get("relative_volume")
    volume_flagged = level in VOLUME_WORDS and rvol is not None

    has_candles = (evidence is not None and evidence.open_series and evidence.high_series
                   and evidence.low_series)
    if not has_candles:
        warnings.append("no local OHLC evidence - text scene, no chart is drawn")
        # Never the planner's context_line: it carries internal labels ("Positive Alignment").
        if fam in EVENT_SENTENCE:
            sentence = EVENT_SENTENCE[fam].format(w=w)
        elif volume_flagged:
            sentence = volume_takeaway(level, chg)
        else:
            sentence = session_takeaway(chg, None)
        return RadarStoryModel(**base, event_family="TEXT", event_callout="", reference_label="",
                               reference_level=None, reference_display="", latest_close=None,
                               latest_close_display="", close_label="", candles=None, band=None,
                               ma_series=None, support={"kind": "NONE"},
                               takeaway=(sentence + ".") if sentence and not sentence.endswith(".")
                               else sentence, duration=4.6, warnings=warnings)

    n_all = len(evidence.close_series)
    k = min(CHART_SESSIONS, n_all)
    sl = slice(n_all - k, n_all)
    candles = {"open": list(evidence.open_series[sl]), "high": list(evidence.high_series[sl]),
               "low": list(evidence.low_series[sl]), "close": list(evidence.close_series[sl])}
    last_close = evidence.close_series[-1]
    loc = day_location(evidence.open_series[-1], evidence.high_series[-1],
                       evidence.low_series[-1], last_close)

    if fam == "TEXT":
        # No validated technical event: nothing is claimed about the chart. Volume leads when
        # the detector flagged it; otherwise only the session's own move and range location.
        if volume_flagged:
            vols = [float(v or 0) for v in evidence.volume_series[sl]]
            avg = _volume_average(vols, float(rvol), warnings)
            support = {"kind": "VOLUME", "level": level, "multiple": float(rvol),
                       "label": f"{rvol:.1f}× normal volume", "caption": "", "volume": vols,
                       "average": avg, "average_sessions": VOLUME_AVG_SESSIONS,
                       "average_label": VOLUME_AVG_LABEL if avg else ""}
            nfam, takeaway = "VOLUME", volume_takeaway(level, chg)
        else:
            support = {"kind": "DAY_RANGE", "location": loc or "MID",
                       "label": LOCATION_LABEL[loc or "MID"], "caption": "TODAY'S RANGE",
                       "low_label": "DAY LOW", "high_label": "DAY HIGH",
                       "day_low": float(evidence.low_series[-1]),
                       "day_high": float(evidence.high_series[-1]), "day_close": float(last_close)}
            nfam, takeaway = "SESSION", session_takeaway(chg, loc)
        return RadarStoryModel(
            **base, event_family=nfam, event_callout="", reference_label="",
            reference_level=None, reference_display="", latest_close=float(last_close),
            latest_close_display=rupees(last_close), close_label="CLOSE", candles=candles,
            band=None, ma_series=None, support=support, takeaway=takeaway, duration=6.8,
            warnings=warnings)

    ref_level, ref_label, band, ma = None, "", None, None
    if fam in ("RANGE_UP", "RANGE_DOWN"):
        hi = evidence.range_50_high if w == 50 else evidence.range_20_high
        lo = evidence.range_50_low if w == 50 else evidence.range_20_low
        if hi is not None and lo is not None:
            ref_level = hi if fam == "RANGE_UP" else lo
            ref_label = f"{w}-DAY {'HIGH' if fam == 'RANGE_UP' else 'LOW'}"
            # the prior window, clipped to what is visible, never including the event session
            band = {"i0": max(0, k - 1 - w), "i1": k - 2, "low": float(lo), "high": float(hi)}
    elif fam in ("MA_UP", "MA_DOWN"):
        src = evidence.sma50_series if w == 50 else evidence.sma20_series
        ma = [None if v is None else float(v) for v in src[sl]]
        if ma and ma[-1] is not None:
            ref_level = ma[-1]
            ref_label = f"{w}-DAY AVG"          # fits the tag column at the minimum font

    # --- the ONE supporting fact: volume first, else price location --------------------------
    if volume_flagged:
        support = {"kind": "VOLUME", "level": level, "multiple": float(rvol),
                   "label": f"{rvol:.1f}× normal volume", "caption": "",
                   "volume": [float(v or 0) for v in evidence.volume_series[sl]]}
        clause = f"with {VOLUME_WORDS[level]}"
    else:
        support = {"kind": "DAY_RANGE", "location": loc or "MID",
                   "label": LOCATION_LABEL[loc or "MID"], "caption": "TODAY'S RANGE",
                   "low_label": "DAY LOW", "high_label": "DAY HIGH",
                   "day_low": float(evidence.low_series[-1]),
                   "day_high": float(evidence.high_series[-1]), "day_close": float(last_close)}
        clause = f"and {LOCATION_CLAUSE[loc]}" if loc in LOCATION_CLAUSE else ""

    sentence = EVENT_SENTENCE[fam].format(w=w)
    takeaway = f"{sentence} {clause}." if clause else f"{sentence}."
    return RadarStoryModel(
        **base, event_family=fam, event_callout=EVENT_CALLOUT.get(fam, ""),
        reference_label=ref_label if ref_level is not None else "",
        reference_level=float(ref_level) if ref_level is not None else None,
        reference_display=rupees(ref_level) if ref_level is not None else "",
        latest_close=float(last_close), latest_close_display=rupees(last_close),
        close_label="CLOSE", candles=candles, band=band, ma_series=ma, support=support,
        takeaway=takeaway, duration=6.8, warnings=warnings)


__all__ = ["RadarStoryModel", "build_radar_story_model", "primary_event", "day_location",
           "move_clause", "volume_takeaway", "session_takeaway", "NO_EVENT_FAMILIES",
           "CHART_SESSIONS", "VOLUME_WORDS", "EVENT_SENTENCE", "LOCATION_LABEL"]
