"""V1 regime rules - every threshold, every rule, every sentence. Deterministic, no model.

Thresholds are conventional, symmetric round numbers, sanity-checked (not fitted) against the
stored history (output/private_desk/regime_research/regime_threshold_review.md). They are
PROVISIONAL while history accumulates (CALCULATION_VERSION says so). Changing any value here
means bumping `model.CALCULATION_VERSION`.

Dimensions and roles
--------------------
CORE (decide the regime): TREND (NIFTY 50 vs its SMA20 / SMA50), BREADTH (NIFTY 200 share
above own SMA50 + 10-session advance share + today's breadth), SECTORS (share of NSE Industry
sectors with a positive 20-session median return), VOLUME (unusual-volume events on advancing vs
declining stocks over 5 sessions).
STRESS (can only block BULLISH): VOLATILITY (NIFTY 50 20-session realised volatility).
CONTEXT (shown, never decide): RELATIVE (share of NIFTY 200 beating NIFTY 50 over 20 sessions),
FLOWS (FII / DII net cash, canonical report), India VIX (canonical report).
"""
from __future__ import annotations

from . import metrics as mt
from .model import (BALANCED, BEARISH, BROAD, BULLISH, CONTEXT, CORE, ELEVATED, FALLING,
                    INSUFFICIENT_DATA, MIXED, NARROW, NEGATIVE, NET_BUYERS, NET_SELLERS,
                    NEUTRAL, NEUTRAL_STATE, POSITIVE, RISING, STABLE, STRESS, TRANSITIONAL,
                    UNAVAILABLE, RegimeDimension)

# ------------------------------------------------------------------ thresholds (V1, provisional)
TREND_FLAT_SMA50_PCT = 1.0          # |close vs SMA50| below this ...
TREND_FLAT_SPREAD_PCT = 0.5         # ... and |SMA20 vs SMA50| below this = range-like (NEUTRAL)
BREADTH_HIGH_PCT = 60.0             # share of NIFTY 200 above own SMA50: >= POSITIVE side
BREADTH_LOW_PCT = 40.0              # <= NEGATIVE side
PERSIST_MID_PCT = 50.0              # 10-session average advance share: direction split
PERSIST_BAND_PCT = 5.0              # 45..55 = balanced persistence (needed for NEUTRAL)
WASHOUT_PCT = 80.0                  # today: >= 80% of covered stocks on one side
SECTOR_BROAD_SHARE = 200.0 / 3      # >= two-thirds of counted sectors positive = POSITIVE
SECTOR_NARROW_SHARE = 100.0 / 3     # <= one-third positive = NEGATIVE
VOLUME_MIN_EVENTS = 20              # fewer unusual-volume events in 5 sessions = NEUTRAL (quiet)
VOLUME_UP_SHARE = 65.0              # >= share of up-day events = POSITIVE
VOLUME_DOWN_SHARE = 45.0            # <= = NEGATIVE (asymmetric: see threshold review - the
                                    #    sample's median up-share is ~60%, not 50%)
VOL_ELEVATED_PCT = 20.0             # realised 20-session annualised volatility >= ELEVATED
VOL_CHANGE_PCT = 25.0               # +/- change vs 5 sessions earlier = RISING / FALLING
RELATIVE_BROAD_PCT = 55.0           # context: share beating NIFTY 50 >= BROAD
RELATIVE_NARROW_PCT = 45.0          # context: <= NARROW

DIMENSION_ORDER = ("TREND", "BREADTH", "SECTORS", "VOLUME", "VOLATILITY", "RELATIVE", "FLOWS")
CORE_KEYS = ("TREND", "BREADTH", "SECTORS", "VOLUME")
NAMES = {"TREND": "Index trend", "BREADTH": "Breadth", "SECTORS": "Sector participation",
         "VOLUME": "Volume participation", "VOLATILITY": "Volatility",
         "RELATIVE": "Relative strength spread", "FLOWS": "Institutional flow"}

RULES = {
    "TREND": (f"POSITIVE: NIFTY 50 close above its SMA20 and SMA50, and SMA20 above SMA50. "
              f"NEGATIVE: close below both, and SMA20 below SMA50. NEUTRAL (range-like): "
              f"|close vs SMA50| < {TREND_FLAT_SMA50_PCT:g}% and |SMA20 vs SMA50| < "
              f"{TREND_FLAT_SPREAD_PCT:g}% (checked first). MIXED: anything else (price and "
              "averages disagree)."),
    "BREADTH": (f"POSITIVE: >= {BREADTH_HIGH_PCT:g}% of NIFTY 200 above their own SMA50 and the "
                f"10-session average advance share >= {PERSIST_MID_PCT:g}%. NEGATIVE: <= "
                f"{BREADTH_LOW_PCT:g}% above SMA50 and 10-session advance share <= "
                f"{PERSIST_MID_PCT:g}%. NEUTRAL: between {BREADTH_LOW_PCT:g}% and "
                f"{BREADTH_HIGH_PCT:g}% above SMA50 and advance share within "
                f"{PERSIST_MID_PCT - PERSIST_BAND_PCT:g}-{PERSIST_MID_PCT + PERSIST_BAND_PCT:g}%. "
                f"MIXED: anything else. A one-day washout (>= {WASHOUT_PCT:g}% of stocks on one "
                "side) against a POSITIVE / NEGATIVE reading turns it MIXED; one day never "
                "creates a POSITIVE or NEGATIVE reading by itself."),
    "SECTORS": (f"Per NSE Industry sector (>= {mt.SECTOR_MIN_COVERED} covered constituents), the "
                f"median {mt.RETURN_WINDOW}-session return. POSITIVE: at least two-thirds of "
                "counted sectors positive. NEGATIVE: at most one-third positive. MIXED: between."),
    "VOLUME": (f"Unusual-volume events (RVOL >= {mt.UNUSUAL_VOLUME_RVOL:g}x prior-20 average) over "
               f"the last {mt.VOLUME_WINDOW} sessions, split by the stock's day direction. NEUTRAL "
               f"(quiet): fewer than {VOLUME_MIN_EVENTS} events. POSITIVE: up-day share >= "
               f"{VOLUME_UP_SHARE:g}%. NEGATIVE: up-day share <= {VOLUME_DOWN_SHARE:g}%. "
               "MIXED: between."),
    "VOLATILITY": (f"NIFTY 50 {mt.RV_WINDOW}-session realised volatility (annualised). ELEVATED: "
                   f">= {VOL_ELEVATED_PCT:g}% - blocks BULLISH. RISING / FALLING: changed by "
                   f">= {VOL_CHANGE_PCT:g}% vs {mt.RV_LAG} sessions earlier. STABLE otherwise. "
                   "India VIX (canonical report) is shown beside it; V1 does not use it as a rule "
                   "input (too few stored readings to set a threshold)."),
    "RELATIVE": (f"CONTEXT ONLY. Share of NIFTY 200 with a {mt.RETURN_WINDOW}-session return above "
                 f"NIFTY 50's. BROAD >= {RELATIVE_BROAD_PCT:g}%, NARROW <= "
                 f"{RELATIVE_NARROW_PCT:g}%, BALANCED between."),
    "FLOWS": ("CONTEXT ONLY - never decides the regime. FII net cash from the canonical report "
              "of this session: NET_BUYERS / NET_SELLERS; its validation status is shown."),
}

REGIME_RULES = {
    BULLISH: ("TREND POSITIVE and BREADTH POSITIVE; SECTORS not NEGATIVE; VOLUME not NEGATIVE "
              "unless SECTORS is POSITIVE; volatility not ELEVATED."),
    BEARISH: ("TREND NEGATIVE and BREADTH NEGATIVE; SECTORS not POSITIVE; VOLUME not POSITIVE "
              "unless SECTORS is NEGATIVE."),
    NEUTRAL: ("TREND NEUTRAL or MIXED (no established index direction) and BREADTH NEUTRAL "
              "(balanced participation); no core dimensions in opposite directions; SECTORS and "
              "VOLUME not both leaning the same way; volatility not ELEVATED."),
    TRANSITIONAL: ("Any other combination with the minimum data present: core dimensions "
                   "conflict, trend and breadth disagree, participation is building one way "
                   "under a directionless index, or a pillar reading is internally mixed. Also "
                   "any BULLISH / BEARISH / NEUTRAL reading whose previous session's evidence "
                   "read differently (confirmation rule)."),
    INSUFFICIENT_DATA: ("TREND or BREADTH unavailable, or none of SECTORS / VOLUME / VOLATILITY "
                        "available."),
}
CONFIRMATION_RULE = ("A BULLISH, BEARISH or NEUTRAL reading becomes the regime only when the "
                     "previous session's own evidence gave the same reading (two consecutive "
                     "sessions). Until then the regime is TRANSITIONAL (UNCONFIRMED_CHANGE). "
                     "TRANSITIONAL and INSUFFICIENT_DATA apply immediately. The previous session's "
                     "reading is recomputed from data up to that session only - no smoothing, no "
                     "chain back further than one session.")

SOURCES = {
    "TREND": "OHLCV store ^NSEI (NIFTY 50), canonical session spine",
    "BREADTH": "OHLCV store, NIFTY 200 constituents (Market Structure membership)",
    "SECTORS": "OHLCV store + NSE Industry column of the NIFTY 200 constituent file",
    "VOLUME": "OHLCV store volumes, market.relative_volume (RVOL v2.0)",
    "VOLATILITY": "OHLCV store ^NSEI; India VIX from the canonical report",
    "RELATIVE": "OHLCV store, NIFTY 200 vs ^NSEI",
    "FLOWS": "canonical report (institutional_flows)",
}


def _f(v, nd=1, signed=False):
    if v is None:
        return "n/a"
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


# ------------------------------------------------------------------ dimension classifiers
def trend_state(m: dict) -> tuple:
    c, s20, s50 = m.get("close"), m.get("sma20"), m.get("sma50")
    if None in (c, s20, s50):
        return UNAVAILABLE, f"Index trend unavailable: {m.get('reason') or 'insufficient history'}."
    d50, spread, d20 = m["dist_sma50_pct"], m["sma20_vs_sma50_pct"], m["dist_sma20_pct"]
    where = (f"NIFTY 50 {_f(c, 2)} is {_f(d20, 2, True)}% vs SMA20 and {_f(d50, 2, True)}% vs "
             f"SMA50; SMA20 is {_f(spread, 2, True)}% vs SMA50")
    if abs(d50) < TREND_FLAT_SMA50_PCT and abs(spread) < TREND_FLAT_SPREAD_PCT:
        return NEUTRAL_STATE, f"{where} - flat, range-like."
    if c > s20 and c > s50 and s20 > s50:
        return POSITIVE, f"{where} - price above both averages, averages rising in order."
    if c < s20 and c < s50 and s20 < s50:
        return NEGATIVE, f"{where} - price below both averages, averages falling in order."
    return MIXED, f"{where} - price and averages disagree."


def breadth_state(m: dict) -> tuple:
    p50, a10, a1, d1 = (m.get("pct_above_sma50"), m.get("advance_share_10d_pct"),
                        m.get("advance_share_pct"), m.get("decline_share_pct"))
    if p50 is None or a10 is None:
        why = ("fewer than 50 sessions of constituent history" if p50 is None
               else "10-session advance share not available")
        return UNAVAILABLE, f"Breadth unavailable: {m.get('reason') or why}."
    today = (f"today {m['advances']} advanced / {m['declines']} declined of {m['covered']}"
             if a1 is not None else "today's breadth unavailable")
    where = (f"{_f(p50)}% of {m.get('universe_label')} above their own SMA50 "
             f"({m['above_sma50']}/{m['sma50_covered']}); 10-session average advance share "
             f"{_f(a10)}%; {today}")
    if p50 >= BREADTH_HIGH_PCT and a10 >= PERSIST_MID_PCT:
        state = POSITIVE
    elif p50 <= BREADTH_LOW_PCT and a10 <= PERSIST_MID_PCT:
        state = NEGATIVE
    elif (BREADTH_LOW_PCT < p50 < BREADTH_HIGH_PCT
          and abs(a10 - PERSIST_MID_PCT) <= PERSIST_BAND_PCT):
        state = NEUTRAL_STATE
    else:
        state = MIXED
    if state == POSITIVE and d1 is not None and d1 >= WASHOUT_PCT:
        return MIXED, f"{where} - participation was broad but today's broad decline contradicts it."
    if state == NEGATIVE and a1 is not None and a1 >= WASHOUT_PCT:
        return MIXED, f"{where} - participation was weak but today's broad advance contradicts it."
    tail = {POSITIVE: "broad participation.", NEGATIVE: "weak participation.",
            NEUTRAL_STATE: "balanced participation.", MIXED: "participation readings disagree."}
    return state, f"{where} - {tail[state]}"


def sector_state(m: dict) -> tuple:
    n, pos = m.get("sectors_counted"), m.get("sectors_positive")
    if not n:
        return UNAVAILABLE, f"Sector participation unavailable: {m.get('reason')}."
    share = m["positive_share_pct"]
    where = (f"{pos} of {n} sectors have a positive {m['window_sessions']}-session median return "
             f"({_f(share)}%)")
    if share >= SECTOR_BROAD_SHARE - 1e-9:
        return POSITIVE, f"{where} - broad sector participation."
    if share <= SECTOR_NARROW_SHARE + 1e-9:
        return NEGATIVE, f"{where} - weakness across most sectors."
    return MIXED, f"{where} - sectors split."


def volume_state(m: dict) -> tuple:
    ev = m.get("events")
    if ev is None:
        return UNAVAILABLE, f"Volume participation unavailable: {m.get('reason')}."
    up, down = m["up_events"], m["down_events"]
    where = (f"{ev} unusual-volume events in {m['window_sessions']} sessions: {up} on advancing "
             f"stocks, {down} on declining stocks")
    if ev < VOLUME_MIN_EVENTS or m.get("up_share_pct") is None:
        return NEUTRAL_STATE, f"{where} - too quiet to lean either way."
    share = m["up_share_pct"]
    if share >= VOLUME_UP_SHARE:
        return POSITIVE, f"{where} ({_f(share)}% up) - heavy volume is concentrated in advances."
    if share <= VOLUME_DOWN_SHARE:
        return NEGATIVE, f"{where} ({_f(share)}% up) - heavy volume is concentrated in declines."
    return MIXED, f"{where} ({_f(share)}% up) - heavy volume on both sides."


def volatility_state(m: dict) -> tuple:
    rv = m.get("realised_20d_pct")
    vix = (f"; India VIX {_f(m['india_vix'], 2)} ({m.get('vix_status') or 'status n/a'})"
           if m.get("india_vix") is not None else "; India VIX not recorded for this session")
    if rv is None:
        return UNAVAILABLE, f"Volatility unavailable: {m.get('reason')}{vix}."
    chg = m.get("realised_change_pct")
    where = (f"NIFTY 50 realised volatility {_f(rv)}% (annualised, {mt.RV_WINDOW} sessions), "
             f"{_f(chg, 0, True) if chg is not None else 'n/a'}% vs {m['lag_sessions']} sessions "
             f"earlier{vix}")
    if rv >= VOL_ELEVATED_PCT:
        return ELEVATED, f"{where} - elevated."
    if chg is not None and chg >= VOL_CHANGE_PCT:
        return RISING, f"{where} - rising from a lower level."
    if chg is not None and chg <= -VOL_CHANGE_PCT:
        return FALLING, f"{where} - falling."
    return STABLE, f"{where} - stable."


def relative_state(m: dict) -> tuple:
    share = m.get("outperform_share_pct")
    if share is None:
        return UNAVAILABLE, f"Relative strength spread unavailable: {m.get('reason')}."
    where = (f"{m['outperforming']} of {m['covered']} NIFTY 200 stocks beat NIFTY 50's "
             f"{_f(m['nifty_return_20d_pct'], 2, True)}% over {m['window_sessions']} sessions "
             f"({_f(share)}%)")
    if share >= RELATIVE_BROAD_PCT:
        return BROAD, f"{where} - broad."
    if share <= RELATIVE_NARROW_PCT:
        return NARROW, f"{where} - narrow (fewer stocks than the index)."
    return BALANCED, f"{where} - balanced."


def flow_state(m: dict) -> tuple:
    fii = m.get("fii_net_cr")
    if fii is None:
        return UNAVAILABLE, "Institutional flow unavailable: no canonical report figure for this session."
    status = m.get("fii_status") or "status n/a"
    dii = (f", DII net {_f(m['dii_net_cr'], 0, True)} Cr" if m.get("dii_net_cr") is not None else "")
    state = NET_BUYERS if fii > 0 else NET_SELLERS if fii < 0 else NEUTRAL_STATE
    return state, (f"FII net {_f(fii, 0, True)} Cr ({status}){dii} - context only, does not "
                   "decide the regime.")


CLASSIFIERS = {"TREND": (trend_state, CORE, "trend"), "BREADTH": (breadth_state, CORE, "breadth"),
               "SECTORS": (sector_state, CORE, "sectors"), "VOLUME": (volume_state, CORE, "volume"),
               "VOLATILITY": (volatility_state, STRESS, "volatility"),
               "RELATIVE": (relative_state, CONTEXT, "relative"),
               "FLOWS": (flow_state, CONTEXT, "flows")}

_FACT_KEYS = {
    "TREND": ("close", "sma20", "sma50", "dist_sma20_pct", "dist_sma50_pct", "sma20_vs_sma50_pct",
              "return_20d_pct", "high_20", "low_20"),
    "BREADTH": ("advances", "declines", "unchanged", "covered", "members", "advance_share_pct",
                "decline_share_pct", "advance_share_10d_pct", "pct_above_sma50", "above_sma50",
                "sma50_covered", "pct_above_sma20"),
    "SECTORS": ("window_sessions", "sectors_counted", "sectors_positive", "sectors_negative",
                "positive_share_pct", "rows"),
    "VOLUME": ("window_sessions", "rvol_threshold", "events", "up_events", "down_events",
               "flat_events", "up_share_pct", "days"),
    "VOLATILITY": ("realised_20d_pct", "realised_20d_pct_lag", "realised_change_pct",
                   "lag_sessions", "india_vix", "vix_status", "vix_report_id"),
    "RELATIVE": ("window_sessions", "nifty_return_20d_pct", "outperforming", "covered",
                 "outperform_share_pct"),
    "FLOWS": ("fii_net_cr", "dii_net_cr", "fii_status", "report_id"),
}


def build_dimensions(metrics: dict) -> tuple:
    out = []
    for key in DIMENSION_ORDER:
        fn, role, mkey = CLASSIFIERS[key]
        m = metrics[mkey]
        state, text = fn(m)
        out.append(RegimeDimension(
            key=key, name=NAMES[key], role=role, state=state,
            facts={k: m.get(k) for k in _FACT_KEYS[key]}, rule=RULES[key], explanation=text,
            source=SOURCES[key], data_as_of=metrics["session"], available=state != UNAVAILABLE))
    return tuple(out)


# ------------------------------------------------------------------ regime decision
def major_contradiction(sectors: str, volume: str, direction: str) -> bool:
    """Evidence hierarchy: trend + breadth (pillars) > sectors > volume. A MAJOR contradiction
    of a pillar direction is sector participation pointing the other way, or volume pointing
    the other way while sectors do not confirm the pillars. Volume alone cannot overturn
    trend, breadth AND sectors agreeing (it is the noisiest dimension: 5 sessions of events)."""
    other = NEGATIVE if direction == POSITIVE else POSITIVE
    return sectors == other or (volume == other and sectors != direction)


def decide(dims: dict) -> tuple:
    """`(candidate_regime, reason_code)` from this session's dimension states alone."""
    s = {k: d.state for k, d in dims.items()}
    trend, breadth = s["TREND"], s["BREADTH"]
    support = [s[k] for k in ("SECTORS", "VOLUME", "VOLATILITY") if s[k] != UNAVAILABLE]
    if trend == UNAVAILABLE or breadth == UNAVAILABLE or not support:
        return INSUFFICIENT_DATA, "MINIMUM_DATA_NOT_MET"
    core = [s[k] for k in CORE_KEYS if s[k] != UNAVAILABLE]
    stress = s["VOLATILITY"] == ELEVATED
    has_pos, has_neg = POSITIVE in core, NEGATIVE in core
    sectors, volume = s["SECTORS"], s["VOLUME"]
    if trend == POSITIVE and breadth == POSITIVE:
        if major_contradiction(sectors, volume, POSITIVE):
            return TRANSITIONAL, "SUPPORT_CONTRADICTS"
        if stress:
            return TRANSITIONAL, "VOLATILITY_STRESS"
        return BULLISH, "BOTH_PILLARS_POSITIVE"
    if trend == NEGATIVE and breadth == NEGATIVE:
        if major_contradiction(sectors, volume, NEGATIVE):
            return TRANSITIONAL, "SUPPORT_CONTRADICTS"
        return BEARISH, "BOTH_PILLARS_NEGATIVE"
    if has_pos and has_neg:
        return TRANSITIONAL, "CONFLICT"
    if trend in (NEUTRAL_STATE, MIXED) and breadth == NEUTRAL_STATE:
        if sectors in (POSITIVE, NEGATIVE) and volume == sectors:
            return TRANSITIONAL, "PARTICIPATION_BUILDING"
        if stress:
            return TRANSITIONAL, "VOLATILITY_STRESS"
        return NEUTRAL, "BALANCED"
    if trend in (POSITIVE, NEGATIVE):
        return TRANSITIONAL, "TREND_WITHOUT_BREADTH"
    if breadth in (POSITIVE, NEGATIVE):
        return TRANSITIONAL, "BREADTH_WITHOUT_TREND"
    if trend == MIXED:
        return TRANSITIONAL, "TREND_MIXED"
    return TRANSITIONAL, "BREADTH_MIXED"


def confirm(candidate: str, reason: str, previous_candidate: str | None) -> tuple:
    """The confirmation rule (CONFIRMATION_RULE). Returns `(regime, reason_code)`."""
    if candidate in (BULLISH, BEARISH, NEUTRAL) and previous_candidate != candidate:
        return TRANSITIONAL, "UNCONFIRMED_CHANGE"
    return candidate, reason


REASON_TEXT = {
    "MINIMUM_DATA_NOT_MET": "The minimum reliable evidence is not available.",
    "BOTH_PILLARS_POSITIVE": "Index trend and breadth are both positive and nothing core contradicts them.",
    "BOTH_PILLARS_NEGATIVE": "Index trend and breadth are both negative and nothing core contradicts them.",
    "BALANCED": "The index has no established direction and participation is balanced - a range-like market.",
    "SUPPORT_CONTRADICTS": "Index trend and breadth agree, but sector or volume participation points the other way.",
    "PARTICIPATION_BUILDING": "The index has no established direction, but sector and volume participation both lean the same way.",
    "VOLATILITY_STRESS": "The structure would qualify, but volatility is elevated.",
    "CONFLICT": "Core dimensions point in opposite directions.",
    "TREND_WITHOUT_BREADTH": "The index trend has a direction that breadth does not confirm.",
    "BREADTH_WITHOUT_TREND": "Breadth has a direction that the index trend does not confirm.",
    "TREND_MIXED": "The index is crossing its averages - price and trend disagree.",
    "BREADTH_MIXED": "Participation readings disagree with each other.",
    "UNCONFIRMED_CHANGE": "Today's evidence reads {candidate}, but the previous session read "
                          "{previous} - a change not yet confirmed by a second session.",
}

def _lean(d: RegimeDimension) -> str:
    """UP / DOWN / BALANCED for one decision-relevant dimension (volatility: ELEVATED = DOWN)."""
    if d.role == STRESS:
        return "DOWN" if d.state == ELEVATED else "BALANCED"
    return {POSITIVE: "UP", NEGATIVE: "DOWN"}.get(d.state, "BALANCED")


def _settled(label: str, dims: dict) -> tuple:
    """Supporting / conflicting lines for a settled label (BULLISH / BEARISH / NEUTRAL)."""
    want = {BULLISH: "UP", BEARISH: "DOWN", NEUTRAL: "BALANCED"}[label]
    sup, con = [], []
    for key in DIMENSION_ORDER:
        d = dims[key]
        if d.role == CONTEXT or d.state == UNAVAILABLE:
            continue
        line = f"{d.name} {d.state}: {d.explanation}"
        lean = _lean(d)
        if lean == want:
            sup.append(line)
        elif d.role == STRESS and lean == "BALANCED":
            if label == BULLISH and d.state != RISING:
                sup.append(line)                       # calm volatility is consistent with BULLISH
            elif d.state == RISING:
                con.append(line)
        else:
            con.append(line)
    return sup, con


def evidence(regime: str, reason: str, candidate: str, previous: str | None, dims: dict) -> tuple:
    """`(supporting, conflicting)` - every line is one dimension's own explanation, so the WHY
    maps one-to-one onto dimensions.

    BULLISH / BEARISH / NEUTRAL: lines agreeing / disagreeing with that label.
    TRANSITIONAL (UNCONFIRMED_CHANGE): supporting = the previous session read differently;
      conflicting = today's lines that already agree with the unconfirmed reading.
    TRANSITIONAL (other): supporting = the directional or mixed lines that create the tension;
      conflicting = lines that read as settled/balanced.
    """
    if regime in (BULLISH, BEARISH, NEUTRAL):
        sup, con = _settled(regime, dims)
        return tuple(sup), tuple(con)
    if regime == INSUFFICIENT_DATA:
        return (), ()
    if reason == "UNCONFIRMED_CHANGE":
        agree, _ = _settled(candidate, dims)
        return ((f"Previous session's evidence read {previous or 'n/a'}; today's reads "
                 f"{candidate} - not yet confirmed.",), tuple(agree))
    sup, con = [], []
    for key in DIMENSION_ORDER:
        d = dims[key]
        if d.role == CONTEXT or d.state == UNAVAILABLE:
            continue
        line = f"{d.name} {d.state}: {d.explanation}"
        (sup if d.state in (POSITIVE, NEGATIVE, MIXED, ELEVATED, RISING) else con).append(line)
    return tuple(sup), tuple(con)


def brief(d: RegimeDimension) -> str:
    """The two or three numbers a dimension's state rests on, for the one-paragraph WHY."""
    f = d.facts
    if d.state == UNAVAILABLE:
        return ""
    if d.key == "TREND":
        return (f"NIFTY 50 {_f(f['dist_sma50_pct'], 1, True)}% vs SMA50, "
                f"{_f(f['dist_sma20_pct'], 1, True)}% vs SMA20")
    if d.key == "BREADTH":
        return (f"{f['above_sma50']}/{f['sma50_covered']} above SMA50, 10-session advance share "
                f"{_f(f['advance_share_10d_pct'])}%")
    if d.key == "SECTORS":
        return f"{f['sectors_positive']}/{f['sectors_counted']} sectors positive over 20 sessions"
    if d.key == "VOLUME":
        return f"unusual volume {f['up_events']} on advancers / {f['down_events']} on decliners"
    if d.key == "VOLATILITY":
        return f"realised {_f(f['realised_20d_pct'])}%"
    return ""


def explain(regime: str, reason: str, candidate: str, previous: str | None, dims: dict) -> str:
    """WHY THIS REGIME: the rule that fired, then each core dimension's state and the numbers
    it rests on - built only from the dimensions, so it maps one-to-one onto them."""
    head = REASON_TEXT[reason].format(candidate=candidate, previous=previous or "n/a")

    def part(k):
        b = brief(dims[k])
        return f"{dims[k].name} {dims[k].state}" + (f" ({b})" if b else "")
    states = "; ".join(part(k) for k in CORE_KEYS)
    vol = dims["VOLATILITY"]
    tail = f" {part('VOLATILITY')}." if vol.state != UNAVAILABLE else ""
    missing = [dims[k].name for k in DIMENSION_ORDER if dims[k].state == UNAVAILABLE
               and dims[k].role != CONTEXT]
    miss = f" Unavailable: {', '.join(missing)}." if missing else ""
    return f"{regime}: {head} {states}.{tail}{miss}"


__all__ = ["build_dimensions", "decide", "confirm", "evidence", "explain", "RULES",
           "REGIME_RULES", "CONFIRMATION_RULE", "DIMENSION_ORDER", "CORE_KEYS", "NAMES",
           "REASON_TEXT"]
