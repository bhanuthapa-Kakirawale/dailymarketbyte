"""PRE-MARKET editorial planner (V1): "What should I know before the bell?"

    canonical MarketReport of the PREVIOUS session ──┐
    pre-open acquisition (providers.premarket) ──────┤
    verified scheduled events (core.event_calendar) ─┼──> build_pre_brief ──> PreMarketBrief
    previous session's published Radar stories ──────┘
                                                        PreEditorialPlanner.plan(brief)
                                                                  │
                                                                  ▼
                                                           PreSectionPlan ──> pre storyboard

The planner is deterministic: every rule is a threshold on validated values, every section
decision carries a written reason, and no model is asked which sections appear (Gemini only
ever touches the hook, through the approved hook engine). It computes no market value - it
selects, orders and WORDS values that were validated upstream, with fixed templates.

The PRE Short tells the viewer, in order:
    HOOK -> OVERNIGHT (what changed overnight) -> SETUP (where Nifty finished, labelled with
    its day) -> [optional previous-session context] -> EVENT (a verified schedule for today)
    -> [stock watch] -> WATCH AT THE OPEN (attention cues, never trades) -> CLOSE

It never predicts the open, never links one market to another causally, never recommends.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

from config import fmt_in
from core.event_calendar import ScheduledEvent
from core.freshness import QuoteKind, clock_label

from .post_plan import _structure_model, direction_phrase, session_support, structural_event

try:
    from video import RS
except Exception:   # pragma: no cover
    RS = "Rs "

PRE_PLAN_VERSION = "pre-1.0"

# --------------------------------------------------------------------------- policy
QUIET_CUE_PCT = 0.50          # every fresh cue below this: "a quiet night"
CONTRAST_MIN_PCT = 0.30       # an opposite-sign cue this large is a real contrast
MAX_CUES = 3                  # lead + at most two supporting cues (GIFT is its own strip)
MIN_CUES = 1
GIFT_MIN_ABS_PCT = 0.0        # a fresh GIFT reading is always context when available
VIX_MIN_CHANGE_PCT = 8.0      # own scene: India VIX moved this much on the previous session
VIX_WATCH_MIN_PCT = 5.0       # watch card
SECTOR_WATCH_MIN_PCT = 1.0    # a sector watch card must carry a real move (value test: a
                              # +0.31% "strongest sector" on a quiet day adds nothing)
FLOW_BIG_CRORE = 3000.0       # own scene: one side this large ...
FLOW_CONTRAST_CRORE = 1000.0  # ... or opposite sides, each at least this
SECTOR_SPREAD_PP = 1.0        # own scene: leader - laggard spread
SECTOR_BIG_PCT = 1.5          # ... or any sector moved this much
STOCK_WATCH_MAX = 2
WATCH_MAX = 3
OPTIONAL_BUDGET = 2
OPTIONAL_PRIORITY = ("STOCK_WATCH", "FLOWS", "VIX", "SECTORS")
SECTION_ORDER = ("OVERNIGHT", "SETUP", "FLOWS", "VIX", "SECTORS", "EVENT", "EXCHANGE", "IPO",
                 "STOCK_WATCH", "WATCH")
# Public intelligence sections (PUBLIC V2): outside the previous-session optional budget, but the
# first to go under the runtime ceiling after it.
PUBLIC_OPTIONAL = ("IPO", "EXCHANGE")
MAX_RUNTIME = 65.0

# Seconds per scene - set by how much there is to read, never stretched to fill a budget.
DUR = {"OVERNIGHT": 5.0, "OVERNIGHT_PER_CUE": 0.6, "GIFT": 0.8, "SETUP_PULSE": 5.6,
       "SETUP_CHART": 6.2, "VIX": 4.8, "FLOWS": 5.4, "SECTORS": 5.6, "EVENT": 5.0,
       "STOCKS_BASE": 4.2, "STOCKS_PER": 1.9, "WATCH_BASE": 3.2, "WATCH_PER": 2.3,
       "EXCHANGE_BASE": 3.6, "EXCHANGE_PER": 1.7, "IPO_CARD": 6.4, "IPO_BOARD_BASE": 3.8,
       "IPO_BOARD_PER": 1.0,
       "CLOSING": 2.6}


class PreMarketBlocked(RuntimeError):
    """The PRE Short cannot be built honestly (no validated previous-session Nifty, a report
    for the wrong session, or not a trading day). Nothing is rendered."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- the brief
@dataclass
class PreMarketBrief:
    """Everything the PRE planner may use - already validated, already dated."""
    pre_date: dt.date                     # the session about to open
    as_of: dt.datetime                    # IST cutoff the briefing is built at
    previous_session: dt.date             # canonical previous session
    nifty: dict                           # close/pct/chg/open/high/low/chart_df (prev session)
    sectors: list = field(default_factory=list)          # [{name, pct}] prev session
    flows: dict | None = None                            # {fii, dii} prev session
    global_cues: list = field(default_factory=list)      # [PreMarketQuote]
    gift: object | None = None                           # PreMarketQuote | None
    vix: object | None = None                            # VixReading | None
    events: list = field(default_factory=list)           # [ScheduledEvent] verified
    news_events: list = field(default_factory=list)      # report headlines - never shown
    stock_facts: list = field(default_factory=list)      # prev-session published Radar
    synthetic: bool = False
    universe: str = "Nifty 100"
    sources: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    acquisition_log: list = field(default_factory=list)
    radar_audit: list = field(default_factory=list)
    # fact_id -> [{source, source_type, independence_group, retrieved_at, ...}] for every
    # previous-session report fact PRE may show: recorded where the report is still in hand
    fact_provenance: dict = field(default_factory=dict)
    # GIFT publication gate verdict (operations.gift_policy): fetched / publication_allowed /
    # withheld / reason. A withheld reading is NOT in `gift` - no consumer can show it.
    gift_policy: dict = field(default_factory=dict)
    # publication boundary (PUBLIC V2): the profile, the official-event inputs, and the admitted
    # EXCHANGE / IPO WATCH models (presentation.public_intelligence) - set by
    # `apply_publication_profile` before planning
    publication_profile: str = "PUBLIC_UNREGISTERED"
    public_intelligence: object | None = None
    exchange_watch: dict | None = None
    ipo_watch: dict | None = None
    public_audit: dict = field(default_factory=dict)
    public_omitted: list = field(default_factory=list)

    @property
    def prev_weekday(self) -> str:
        return self.previous_session.strftime("%A")

    @property
    def prev_is_yesterday(self) -> bool:
        return self.previous_session == self.pre_date - dt.timedelta(days=1)

    def to_dict(self) -> dict:
        return {
            "pre_date": self.pre_date.isoformat(), "as_of": self.as_of.isoformat(),
            "previous_session": self.previous_session.isoformat(),
            "previous_session_label": self.prev_weekday, "synthetic": self.synthetic,
            "nifty": {k: v for k, v in self.nifty.items() if k != "chart_df"},
            "sectors": self.sectors, "flows": self.flows,
            "global_cues": [q.to_dict() for q in self.global_cues],
            "gift": self.gift.to_dict() if self.gift else None,
            "vix": self.vix.to_dict() if self.vix else None,
            "events": [e.to_dict() for e in self.events],
            "news_events_not_shown": self.news_events,
            "stock_facts": [{k: v for k, v in s.items() if k != "closes"} for s in self.stock_facts],
            "universe": self.universe, "sources": self.sources, "notes": self.notes,
            "acquisition_log": self.acquisition_log, "radar_audit": self.radar_audit,
            "fact_provenance": self.fact_provenance, "gift_policy": self.gift_policy,
            "publication_profile": self.publication_profile,
            "exchange_watch": self.exchange_watch, "ipo_watch": self.ipo_watch,
        }


def stock_fact_from_story(sp: dict, story: dict, evidence, i: int, n: int) -> dict:
    """One published previous-session Radar story as a stock-watch fact. The Radar story
    model decides every value and string (fixed templates); this only keeps what PRE needs."""
    from .radar_story import build_radar_story_model
    m = build_radar_story_model(sp, story, evidence, i, n)
    closes = list((m.candles or {}).get("close") or [])
    return {"symbol": m.symbol, "change": m.display_change, "positive": m.change_positive,
            "takeaway": m.takeaway, "support": (m.support or {}).get("label", ""),
            "event_family": m.event_family, "closes": closes[-20:]}


def build_pre_brief(report, pre_date: dt.date, as_of: dt.datetime, calendar, acquisition,
                    events: list, radar_stories=(), evidence=None, radar_audit=(),
                    sources=None, synthetic: bool = False) -> PreMarketBrief:
    """Assemble the brief from a canonical report of the PREVIOUS session. Refuses (raises
    PreMarketBlocked) rather than describe the wrong session."""
    from .report_adapter import ReportPresentation
    if calendar.is_session(pre_date) is False:
        raise PreMarketBlocked("NOT_A_SESSION", f"{pre_date} is not an NSE trading session")
    expected = calendar.previous_session(pre_date)
    if expected is None:
        raise PreMarketBlocked("NO_PREVIOUS_SESSION",
                               f"the trading calendar cannot establish the session before {pre_date}")
    if report is None:
        raise PreMarketBlocked("PREVIOUS_SESSION_MISSING",
                               f"no canonical report for the previous session {expected}")
    pres = ReportPresentation(report)
    if pres.session_date != expected:
        raise PreMarketBlocked(
            "PREVIOUS_SESSION_MISMATCH",
            f"the canonical report describes {pres.session_date}, but the session before "
            f"{pre_date} is {expected} - an older session is never shown as the previous one")
    m = pres.m
    if m.get("close") is None or m.get("pct") is None:
        raise PreMarketBlocked("PREVIOUS_NIFTY_MISSING",
                               f"no validated Nifty close/change for {expected}")
    stock_facts = []
    for i, (sp, story) in enumerate(list(radar_stories)[:STOCK_WATCH_MAX], start=1):
        stock_facts.append(stock_fact_from_story(sp, story, (evidence or {}).get(sp["instrument"]),
                                                 i, min(len(radar_stories), STOCK_WATCH_MAX)))
    nifty = {k: m.get(k) for k in ("close", "pct", "chg", "open", "high", "low", "prev")}
    nifty["chart_df"] = m.get("chart_df")
    nifty["fact_ids"] = list((report.nifty or {}).get("fact_ids") or [])
    # Provenance gate: a previous-session fact backed ONLY by an AI observation is never
    # market data on a PRE screen (POST may carry it as PROVISIONAL; PRE drops it).
    notes = []
    flows = pres.fd
    flow_ids = list((report.institutional_flows or {}).get("fact_ids") or [])
    if flows and not all(non_ai_fact(report, f) for f in flow_ids):
        notes.append("FII/DII dropped: backed only by an AI source (" + ", ".join(flow_ids) + ")")
        flows = None
    sector_ids = {s.get("name"): s.get("fact_id") for s in (report.sectors or [])}
    sectors = []
    for s in pres.sec:
        if s.get("pct") is None:
            continue
        fid = sector_ids.get(s.get("name"))
        if fid and not non_ai_fact(report, fid):
            notes.append(f"sector {s.get('name')} dropped: backed only by an AI source ({fid})")
            continue
        sectors.append(s)
    return PreMarketBrief(
        pre_date=pre_date, as_of=as_of, previous_session=expected, nifty=nifty,
        sectors=sectors, flows=flows, notes=notes,
        global_cues=list(acquisition.global_cues), gift=acquisition.gift, vix=acquisition.vix,
        events=[e for e in events if e.showable],
        news_events=[{"tag": e.get("tag"), "text": e.get("text")} for e in pres.events],
        stock_facts=stock_facts, synthetic=synthetic,
        universe=(report.metadata or {}).get("universe") or "Nifty 100",
        sources=dict(sources or {}, market_report_id=report.report_id),
        fact_provenance=report_fact_provenance(
            report, nifty["fact_ids"] + flow_ids + [f for f in sector_ids.values() if f]),
        acquisition_log=list(acquisition.log), radar_audit=list(radar_audit))


def non_ai_fact(report, fact_id: str) -> bool:
    """True when the canonical fact has at least one observation that is not an LLM. A fact
    id the report does not carry is not evidence either way (True: nothing to exclude)."""
    from core.enums import SourceType
    fact = report.fact(fact_id) if fact_id else None
    if fact is None:
        return True
    obs = list(fact.observations or [])
    return bool(obs) and any(o.source_type is not SourceType.AI for o in obs)


def report_fact_provenance(report, fact_ids) -> dict:
    """{fact_id: {validation_status, observations: [...]}} - who said each previous-session
    number, straight from the canonical report (never reconstructed)."""
    out = {}
    for fid in fact_ids:
        fact = report.fact(fid) if fid else None
        if fact is None:
            continue
        out[fid] = {
            "metric": fact.metric.value, "instrument": fact.instrument,
            "market_date": fact.market_date.isoformat() if fact.market_date else None,
            "validation_status": getattr(fact.validation_status, "value", str(fact.validation_status)),
            "observations": [{
                "source": o.source_name, "source_type": o.source_type.value,
                "independence_group": o.independence_group,
                "retrieved_at": o.retrieved_at.isoformat() if o.retrieved_at else None,
                "observed_at": o.observed_at.isoformat() if o.observed_at else None,
                "source_reference": o.source_reference} for o in fact.observations or []]}
    return out


# --------------------------------------------------------------------------- contracts
@dataclass
class GlobalCueModel:
    name: str
    value: str                 # "+2.26%"
    change_pct: float
    positive: bool
    when: str                  # "US CLOSE · MON" / "AT 7:45 AM IST"
    role: str                  # LEAD / CONTRAST / SUPPORT
    region: str
    kind: str
    source: str
    market_date: str | None
    market_timestamp: str | None


@dataclass
class GiftNiftyModel:
    value: str                 # "-0.60%"
    positive: bool
    time_label: str            # "7:52 AM IST"
    level: str                 # "23,050"
    sentence: str              # "GIFT Nifty was 0.60% lower at 7:52 AM IST"
    source: str
    market_timestamp: str
    reference: str = ""        # "VS ITS FRI SETTLEMENT" - what the change is measured from


@dataclass
class OvernightModel:
    headline: str
    takeaway: str
    quiet: bool
    cues: list                 # [GlobalCueModel], lead first
    gift: GiftNiftyModel | None = None


@dataclass
class PreviousSessionSetupModel:
    kind: str                  # PULSE (compact card) or CHART (a NEW structural event)
    chip: str                  # "YESTERDAY'S SETUP" / "FRIDAY'S SETUP"
    headline: str
    session_date: str
    session_label: str         # "Thursday"
    pulse: dict | None = None      # MarketPulseScene contract
    structure: dict | None = None  # MarketStructureScene contract (Radar chart grammar)
    event: dict | None = None      # the structural event, when one exists
    support: dict | None = None    # where the close sat in the day's range


@dataclass
class VixModel:
    headline: str
    value: str
    change: str
    positive: bool
    previous_label: str        # "WED"
    previous_value: str
    session_label: str         # "THU"
    note: str


@dataclass
class FlowsModel:
    headline: str
    subline: str
    bars: list


@dataclass
class EventCardModel:
    tag: str
    title: str
    time_label: str
    day: str
    month: str
    weekday: str
    source_label: str
    importance: str
    validation_status: str
    event_type: str


@dataclass
class StockWatchModel:
    headline: str
    subline: str
    items: list                # [{symbol, change, positive, line, support, closes}]


@dataclass
class WatchItemModel:
    category: str              # NIFTY_LEVEL / EVENT / VIX / SECTOR / STOCK / FLOWS / GIFT / OVERNIGHT
    tag: str
    title: str
    note: str
    positive: bool | None = None


@dataclass
class PreSectionPlan:
    version: str
    pre_date: str
    previous_session: str
    show_global_context: bool
    show_gift_nifty: bool
    show_vix: bool
    show_previous_session_setup: bool
    show_flows: bool
    show_event: bool
    show_sector_context: bool
    show_stock_watch: bool
    show_watch: bool
    show_closing: bool
    order: list
    reasons: dict
    labels: dict               # section key -> chip label
    durations: dict
    overnight: OvernightModel | None = None
    setup: PreviousSessionSetupModel | None = None
    vix: VixModel | None = None
    flows: FlowsModel | None = None
    event: EventCardModel | None = None
    sectors: dict | None = None
    stock_watch: StockWatchModel | None = None
    watch: list = field(default_factory=list)       # [WatchItemModel]
    exchange: dict | None = None                    # EXCHANGE WATCH model (public V2)
    ipo: dict | None = None                         # IPO WATCH model (public V2)
    watch_headline: str = "What to watch at the open"
    watch_subline: str = "Reference points, not trade signals"
    closing_line: str = "That's your setup before the bell."
    omitted: list = field(default_factory=list)
    synthetic: bool = False

    @property
    def total_duration(self) -> float:
        return round(sum(self.durations.get(k, 0.0) for k in self.order)
                     + DUR["CLOSING"], 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("setup") and d["setup"].get("structure"):
            s = d["setup"]["structure"]
            d["setup"]["structure"] = {"model": {k: v for k, v in s["model"].items()
                                                 if k not in ("candles", "ma_series")},
                                       "texts": s["texts"]}
        if d.get("stock_watch"):
            for it in d["stock_watch"]["items"]:
                it.pop("closes", None)
        d["total_duration_ex_hook"] = self.total_duration
        return d


# --------------------------------------------------------------------------- formatting only
def _pct(v: float, dec: int = 2) -> str:
    return f"{v:+.{dec}f}%"


def _abs_pct(v: float, dec: int = 2) -> str:
    return f"{abs(v):.{dec}f}%"


def _crore(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}{RS}{fmt_in(abs(v), 0)} cr"


def _short(name: str) -> str:
    return {"DOW JONES": "The Dow", "NASDAQ": "Nasdaq", "S&P 500": "The S&P 500",
            "NIKKEI 225": "Japan's Nikkei", "HANG SENG": "Hong Kong's Hang Seng"}.get(
        name, name.title())


def _when(q) -> str:
    if q.kind is QuoteKind.SESSION_CLOSE and q.market_date:
        return f"US CLOSE · {q.market_date:%a}".upper()
    if q.market_timestamp:
        return f"AT {clock_label(q.market_timestamp)} IST"
    return ""


# --------------------------------------------------------------------------- sections
def _overnight(brief, reasons, omitted):
    fresh = [q for q in brief.global_cues if q.fresh]
    for q in brief.global_cues:
        if not q.fresh:
            omitted.append({"section": "OVERNIGHT", "item": q.name,
                            "reason": f"{q.freshness.value}: {q.freshness_reason}"})
    gift = brief.gift if (brief.gift is not None and brief.gift.fresh) else None
    if brief.gift is not None and gift is None:
        g = brief.gift
        from providers.premarket import gift_status_of
        st = gift_status_of(g)
        why = f"{st['status']}: {st['reason']}"
        omitted.append({"section": "GIFT NIFTY", "item": "GIFT NIFTY", "reason": why})
    elif brief.gift is None:
        gp = brief.gift_policy or {}
        if gp.get("withheld") or gp.get("fetched") is False:
            why = ("publication policy: " + str(gp.get("reason")) +
                   (" (reading validated for audit, not shown)" if gp.get("withheld") else
                    " (not fetched)"))
        else:
            why = ("no GIFT Nifty reading from the exchange (NSE IX) - not fabricated, never "
                   "taken from Gemini")
        omitted.append({"section": "GIFT NIFTY", "item": "GIFT NIFTY", "reason": why})
    if not fresh and gift is None:
        reasons["OVERNIGHT"] = "omitted: no fresh overnight reading (every cue stale or unavailable)"
        return None
    ranked = sorted(fresh, key=lambda q: (-abs(q.change_pct), q.key))
    quiet = all(abs(q.change_pct) < QUIET_CUE_PCT for q in ranked)
    chosen = []
    if ranked:
        lead = ranked[0]
        chosen.append((lead, "LEAD"))
        contrast = next((q for q in ranked[1:] if (q.change_pct > 0) != (lead.change_pct > 0)
                         and abs(q.change_pct) >= CONTRAST_MIN_PCT), None)
        if contrast is not None and not quiet:
            chosen.append((contrast, "CONTRAST"))
        # then the next largest from a region not yet shown, then anything left
        regions = {q.region for q, _ in chosen}
        rest = [q for q in ranked[1:] if all(q is not c for c, _ in chosen)]
        rest.sort(key=lambda q: (q.region in regions, -abs(q.change_pct), q.key))
        for q in rest:
            if len(chosen) >= MAX_CUES:
                break
            chosen.append((q, "SUPPORT"))
    for q in ranked:
        if all(q is not c for c, _ in chosen):
            omitted.append({"section": "OVERNIGHT", "item": q.name,
                            "reason": f"fresh ({_pct(q.change_pct)}) but the overnight scene shows "
                                      f"one dominant cue and at most {MAX_CUES - 1} supporting ones"})
    cues = [GlobalCueModel(q.name, _pct(q.change_pct), q.change_pct, q.change_pct >= 0, _when(q),
                           role, q.region, q.kind.value, q.source,
                           q.market_date.isoformat() if q.market_date else None,
                           q.market_timestamp.isoformat() if q.market_timestamp else None)
            for q, role in chosen]
    gm = None
    if gift is not None:
        # GIFT Nifty's own move since its previous settlement, in the past tense at its own
        # time - never compared with Nifty's spot close (that gap reads as an opening call).
        tl = f"{clock_label(gift.market_timestamp)} IST"
        if round(gift.change_pct, 2) == 0:
            sentence = f"GIFT Nifty was unchanged at {tl}"
        else:
            side = "higher" if gift.change_pct > 0 else "lower"
            sentence = f"GIFT Nifty was {_abs_pct(gift.change_pct)} {side} at {tl}"
        ref_day = f"{gift.reference_date:%a} ".upper() if gift.reference_date else "PREVIOUS "
        gm = GiftNiftyModel(value=_pct(gift.change_pct), positive=gift.change_pct >= 0,
                            time_label=tl, level=fmt_in(gift.value, 0) if gift.value else "",
                            sentence=sentence, source=gift.source,
                            market_timestamp=gift.market_timestamp.isoformat(),
                            reference=f"VS ITS {ref_day}SETTLEMENT")
    if cues:
        lead = chosen[0][0]
        if quiet:
            headline = "A quiet night for global markets"
            takeaway = f"Every tracked market moved less than {QUIET_CUE_PCT:.1f}%"
        else:
            verb = "rose" if lead.change_pct > 0 else "fell"
            if lead.kind is QuoteKind.SESSION_CLOSE:
                headline = f"{_short(lead.name)} {verb} {_abs_pct(lead.change_pct)} overnight"
            else:
                side = "higher" if lead.change_pct > 0 else "lower"
                headline = (f"{_short(lead.name)} was {_abs_pct(lead.change_pct)} {side} "
                            f"at {clock_label(lead.market_timestamp)}")
            contrast = next((c for c, r in chosen if r == "CONTRAST"), None)
            if contrast is not None:
                cverb = "rose" if contrast.change_pct > 0 else "fell"
                takeaway = (f"Not every market moved the same way: {_short(contrast.name)} "
                            f"{cverb} {_abs_pct(contrast.change_pct)}")
            else:
                same = sum(1 for q in fresh if (q.change_pct > 0) == (lead.change_pct > 0))
                moved = "rose" if lead.change_pct > 0 else "fell"
                takeaway = (f"All {same} tracked markets {moved}" if same == len(fresh) and same > 1
                            else f"{same} of {len(fresh)} tracked markets {moved}")
    else:
        headline, takeaway = "Before the bell: GIFT Nifty", ""
    if gm is not None and not cues:
        takeaway = gm.sentence
    model = OvernightModel(headline=headline, takeaway=takeaway, quiet=quiet and bool(cues),
                           cues=cues, gift=gm)
    reasons["OVERNIGHT"] = (
        "core: what changed overnight - " +
        (f"lead {cues[0].name} {cues[0].value} ({cues[0].when})" if cues else "GIFT Nifty only") +
        (f"; {len(cues) - 1} supporting cue(s)" if len(cues) > 1 else "") +
        ("; all fresh cues below %.1f%% - a quiet night, said plainly" % QUIET_CUE_PCT if model.quiet else ""))
    gift_omit = next((o for o in omitted if o["section"] == "GIFT NIFTY"), None)
    reasons["GIFT"] = (("included in the overnight scene: " + gm.sentence) if gm else
                       ("omitted: " + gift_omit["reason"]) if gift_omit else "omitted")
    return model


def _setup(brief, reasons):
    n, wd = brief.nifty, brief.prev_weekday
    chip = "YESTERDAY'S SETUP" if brief.prev_is_yesterday else f"{wd.upper()}'S SETUP"
    sup = session_support(n.get("open"), n.get("high"), n.get("low"), n.get("close"))
    ev = structural_event(n.get("chart_df"))
    base = PreviousSessionSetupModel(kind="PULSE", chip=chip,
                                     headline=f"{direction_phrase(n['pct'])} on {wd}",
                                     session_date=brief.previous_session.isoformat(),
                                     session_label=wd, support=sup)
    if ev:
        ev = dict(ev, sentence=f"{ev['sentence']} on {wd}")
        m = {"chart_df": n["chart_df"], "pct": n["pct"], "close": n["close"]}
        base.kind = "CHART"
        base.structure = _structure_model(m, ev)
        base.headline = ev["sentence"] + "."
        base.event = {k: ev[k] for k in ("family", "level", "label", "sentence")}
        reasons["SETUP"] = (f"core: previous session ({wd}) - a new structural event: "
                            f"{ev['sentence'].lower()} - shown as the labelled chart")
    else:
        base.pulse = {"headline": base.headline, "label": f"NIFTY 50 · {wd.upper()}'S CLOSE",
                      "value": fmt_in(n["close"], 2), "change": _pct(n["pct"]),
                      "points": (f"{n['chg']:+.1f} pts" if n.get("chg") is not None else ""),
                      "positive": n["pct"] >= 0, "support": sup}
        reasons["SETUP"] = (f"core: previous session ({wd}) - close and change + one support "
                            f"({sup.get('text') or 'none'}); no new structural event, so no chart")
    return base


def _vix(brief, reasons):
    v = brief.vix
    if v is None:
        reasons["VIX"] = "omitted: no India VIX reading for the previous session"
        return None, False
    if not v.fresh:
        reasons["VIX"] = f"omitted: {v.freshness.value} - {v.freshness_reason}"
        return None, False
    wd = brief.prev_weekday
    side = "higher" if v.change_pct > 0 else "lower"
    model = VixModel(headline=f"India VIX closed {_abs_pct(v.change_pct, 1)} {side} on {wd}",
                     value=f"{v.value:.2f}", change=_pct(v.change_pct, 1),
                     positive=v.change_pct >= 0,
                     previous_label=v.previous_session.strftime("%a").upper(),
                     previous_value=f"{v.previous_value:.2f}",
                     session_label=v.session.strftime("%a").upper(),
                     note="India's volatility index, from Nifty option prices")
    big = abs(v.change_pct) >= VIX_MIN_CHANGE_PCT
    reasons["VIX"] = (f"included: India VIX {model.change} on {wd} (>= {VIX_MIN_CHANGE_PCT:.0f}%)"
                      if big else f"omitted: India VIX {model.change} on {wd} is below the "
                                  f"{VIX_MIN_CHANGE_PCT:.0f}% threshold for its own scene")
    return model, big


def _flows(brief, reasons):
    fd = brief.flows
    if not fd or fd.get("fii") is None or fd.get("dii") is None:
        reasons["FLOWS"] = "omitted: no validated FII/DII flows for the previous session - not fabricated"
        return None, False
    fii, dii = float(fd["fii"]), float(fd["dii"])
    wd = brief.prev_weekday
    big = max(abs(fii), abs(dii)) >= FLOW_BIG_CRORE
    contrast = (fii > 0) != (dii > 0) and min(abs(fii), abs(dii)) >= FLOW_CONTRAST_CRORE
    fw = "Foreign investors were net buyers" if fii >= 0 else "Foreign investors were net sellers"
    dw = "domestic institutions were net buyers" if dii >= 0 else "domestic institutions were net sellers"
    same = (fii >= 0) == (dii >= 0)
    headline = f"{fw}; {dw.replace('were', 'were also') if same else dw}"
    bars = [{"name": "FII", "value": _crore(fii), "tag": "NET BUYERS" if fii >= 0 else "NET SELLERS",
             "numeric": fii, "positive": fii >= 0},
            {"name": "DII", "value": _crore(dii), "tag": "NET BUYERS" if dii >= 0 else "NET SELLERS",
             "numeric": dii, "positive": dii >= 0}]
    model = FlowsModel(headline=headline, subline=f"Net cash-market flows on {wd} (provisional)",
                       bars=bars)
    ok = big or contrast
    reasons["FLOWS"] = ((f"included: FII {_crore(fii)}, DII {_crore(dii)} on {wd} - "
                         + ("large" if big else "opposite sides, each >= Rs 1,000 cr"))
                        if ok else f"omitted: FII {_crore(fii)}, DII {_crore(dii)} - neither large "
                                   f"(>= Rs {fmt_in(FLOW_BIG_CRORE, 0)} cr) nor a clear contrast")
    return model, ok


def _event(brief, reasons, omitted):
    for e in brief.news_events:
        omitted.append({"section": "EVENT", "item": e.get("text"),
                        "reason": f"news headline tagged {e.get('tag')}, not a verified schedule "
                                  "- never shown as today's event"})
    if not brief.events:
        reasons["EVENT"] = "omitted: no verified scheduled event today (official calendar or exchange rule)"
        return None
    e: ScheduledEvent = brief.events[0]
    for other in brief.events[1:]:
        omitted.append({"section": "EVENT", "item": other.event_name,
                        "reason": "one event card per Short; a more important event leads"})
    src = ("Source: exchange expiry rule" if e.validation_status.value == "RULE_DERIVED"
           else "Source: " + ("Federal Reserve calendar" if "federalreserve" in e.source
                              else "RBI press release" if "rbi.org.in" in e.source
                              else "official schedule"))
    if brief.synthetic:
        src = "Source: synthetic fixture"
    model = EventCardModel(tag=e.tag, title=e.event_name, time_label=e.time_label,
                           day=str(e.event_date.day), month=e.event_date.strftime("%b").upper(),
                           weekday=e.event_date.strftime("%A").upper(), source_label=src,
                           importance=e.importance.value,
                           validation_status=e.validation_status.value, event_type=e.event_type)
    reasons["EVENT"] = (f"core: verified scheduled event today - {e.event_name} "
                        f"({e.validation_status.value}, {e.importance.value})")
    return model


def _sectors(brief, reasons):
    secs = sorted((s for s in brief.sectors if s.get("pct") is not None), key=lambda s: -s["pct"])
    wd = brief.prev_weekday
    if len(secs) < 2:
        reasons["SECTORS"] = "omitted: fewer than two validated sector indices"
        return None, False
    rows = [{"name": s["name"], "value": _pct(s["pct"]), "numeric": float(s["pct"]),
             "positive": s["pct"] >= 0} for s in secs]
    n, up = len(secs), sum(1 for s in secs if s["pct"] > 0)
    down = sum(1 for s in secs if s["pct"] < 0)
    lead_tag, lag_tag = "LEADER", "LAGGARD"
    if down == n:
        headline = f"All {n} tracked sector indices fell on {wd}"
        lead_tag, lag_tag = "FELL LEAST", "FELL MOST"
    elif up == n:
        headline = f"All {n} tracked sector indices rose on {wd}"
        lag_tag = "ROSE LEAST"
    else:
        headline = f"{rows[0]['name']} led, {rows[-1]['name']} lagged on {wd}"
    model = {"headline": headline, "tone": f"{up} of {n} tracked indices closed higher",
             "rows": rows, "leader_tag": lead_tag, "laggard_tag": lag_tag,
             "strip_title": "ALL SECTORS, STRONGEST FIRST"}
    spread = secs[0]["pct"] - secs[-1]["pct"]
    ok = spread >= SECTOR_SPREAD_PP or max(abs(s["pct"]) for s in secs) >= SECTOR_BIG_PCT
    reasons["SECTORS"] = (f"included: {wd}'s sector spread {spread:.2f} pts" if ok else
                          f"omitted: {wd}'s sector spread {spread:.2f} pts is below "
                          f"{SECTOR_SPREAD_PP:.1f} and no sector moved {SECTOR_BIG_PCT:.1f}%")
    return model, ok


def _stocks(brief, reasons):
    items = [dict(s, line=f"{brief.prev_weekday}: {s['takeaway']}") for s in brief.stock_facts
             if s.get("takeaway")][:STOCK_WATCH_MAX]
    if not items:
        reasons["STOCK_WATCH"] = ("omitted: no published previous-session Radar story "
                                  "(no speculative overnight stock selection)")
        return None
    reasons["STOCK_WATCH"] = (f"included: {len(items)} of the previous session's published Radar "
                              "stories, carried forward in the selector's own order")
    return StockWatchModel(headline=f"Stocks that stood out on {brief.prev_weekday}",
                           subline="From Market Radar - what the data showed, not a call",
                           items=items)


def _watch(brief, show, setup, overnight, vix_m, flows_m, event, sectors_m, stocks_m):
    """2-3 attention cues. Card 1 is always the Nifty reference level; the rest are facts
    that are not already a scene of their own (a timed event is the exception - its time is
    the cue). Every card is a fact with its day/time - never an action."""
    n, wd = brief.nifty, brief.prev_weekday
    items = []
    ev, sup = setup.event, setup.support or {}
    if ev:
        what = ev["label"].lower().replace("avg", "average").capitalize()
        rel = "below" if ev["family"] in ("RANGE_DOWN", "MA_DOWN") else "above"
        items.append(WatchItemModel("NIFTY_LEVEL", "NIFTY LEVEL",
                                    f"{what}: {fmt_in(ev['level'], 1)}",
                                    f"Nifty closed {rel} it on {wd}, at {fmt_in(n['close'], 1)}",
                                    rel == "above"))
    elif sup.get("location") in ("LOW", "HIGH"):
        lvl = n["low"] if sup["location"] == "LOW" else n["high"]
        word = "low" if sup["location"] == "LOW" else "high"
        items.append(WatchItemModel("NIFTY_LEVEL", "NIFTY LEVEL", f"{wd}'s {word}: {fmt_in(lvl, 1)}",
                                    f"Nifty closed near it, at {fmt_in(n['close'], 1)}",
                                    word == "high"))
    elif n.get("low") is not None and n.get("high") is not None:
        items.append(WatchItemModel("NIFTY_LEVEL", "NIFTY RANGE",
                                    f"{wd}'s range: {fmt_in(n['low'], 0)} - {fmt_in(n['high'], 0)}",
                                    f"Nifty closed at {fmt_in(n['close'], 1)}", None))
    cands = []
    if event is not None:
        cands.append(WatchItemModel("EVENT", "SCHEDULED", event.title, event.time_label, None))
    v = brief.vix
    if not show["VIX"] and v is not None and v.fresh and abs(v.change_pct) >= VIX_WATCH_MIN_PCT:
        cands.append(WatchItemModel("VIX", "VOLATILITY", f"India VIX {v.value:.2f}",
                                    f"{_abs_pct(v.change_pct, 1)} {'higher' if v.change_pct > 0 else 'lower'} on {wd}",
                                    None))    # volatility is not good or bad: neutral colour
    secs = sorted((s for s in brief.sectors if s.get("pct") is not None), key=lambda s: -s["pct"])
    lead_s, lag_s = (secs[0], secs[-1]) if len(secs) >= 2 else (None, None)
    pick_s = (lag_s if abs(lag_s["pct"]) >= abs(lead_s["pct"]) else lead_s) if lead_s else None
    if not show["SECTORS"] and pick_s is not None and abs(pick_s["pct"]) >= SECTOR_WATCH_MIN_PCT:
        lead, lag, pick = lead_s, lag_s, pick_s
        if pick is lag:
            note = ("Fell most of %d tracked sector indices" % len(secs)) if lead["pct"] < 0 else "Weakest sector index"
        else:
            note = ("Rose most of %d tracked sector indices" % len(secs)) if lag["pct"] > 0 else "Strongest sector index"
        cands.append(WatchItemModel("SECTOR", "SECTOR", f"{pick['name']} {_pct(pick['pct'])} on {wd}",
                                    note, pick["pct"] >= 0))
    if not show["STOCK_WATCH"] and brief.stock_facts:
        s = brief.stock_facts[0]
        cands.append(WatchItemModel("STOCK", "RADAR STOCK", f"{s['symbol']} {s['change']} on {wd}",
                                    s["takeaway"], s["positive"]))
    fd = brief.flows
    if not show["FLOWS"] and fd and fd.get("fii") is not None and fd.get("dii") is not None \
            and max(abs(fd["fii"]), abs(fd["dii"])) >= FLOW_CONTRAST_CRORE:
        fii, dii = float(fd["fii"]), float(fd["dii"])
        cands.append(WatchItemModel("FLOWS", "FII / DII", f"FIIs net {'bought' if fii >= 0 else 'sold'} "
                                    f"{RS}{fmt_in(abs(fii), 0)} cr",
                                    f"On {wd}; DIIs net {'bought' if dii >= 0 else 'sold'} "
                                    f"{RS}{fmt_in(abs(dii), 0)} cr", fii >= 0))
    if overnight is not None and overnight.gift is not None:
        g = overnight.gift
        ref = g.reference.removeprefix("VS ITS ").capitalize()
        cands.append(WatchItemModel("GIFT", "GIFT NIFTY", f"GIFT Nifty {g.value}",
                                    f"At {g.time_label}, vs its {ref}", g.positive))
    if overnight is not None and overnight.cues and not overnight.quiet:
        c = overnight.cues[0]
        if c.kind == QuoteKind.SESSION_CLOSE.value and c.market_date:
            note = f"US close on {dt.date.fromisoformat(c.market_date):%A}"
        else:
            note = f"At {clock_label(dt.datetime.fromisoformat(c.market_timestamp))} IST"
        cands.append(WatchItemModel("OVERNIGHT", "OVERNIGHT", f"{c.name} {c.value}", note,
                                    c.positive))
    for c in cands:
        if len(items) >= WATCH_MAX:
            break
        items.append(c)
    return items


# --------------------------------------------------------------------------- planner
class PreEditorialPlanner:
    """Deterministic: the same brief always yields the same plan, byte for byte."""

    version = PRE_PLAN_VERSION

    def plan(self, brief: PreMarketBrief) -> PreSectionPlan:
        reasons, omitted = {}, []
        overnight = _overnight(brief, reasons, omitted)
        setup = _setup(brief, reasons)
        vix_m, vix_ok = _vix(brief, reasons)
        flows_m, flows_ok = _flows(brief, reasons)
        event = _event(brief, reasons, omitted)
        sectors_m, sectors_ok = _sectors(brief, reasons)
        stocks_m = _stocks(brief, reasons)
        exchange_m, ipo_m = brief.exchange_watch, brief.ipo_watch
        reasons["EXCHANGE"] = (f"included: {len(exchange_m['cards'])} official exchange event(s)"
                               if exchange_m else "omitted: no validated official exchange event "
                                                  "for today")
        reasons["IPO"] = ("included: dated IPO event(s) today" if ipo_m else
                          "omitted: no official IPO event dated today")

        show = {"OVERNIGHT": overnight is not None, "SETUP": True, "VIX": vix_ok,
                "FLOWS": flows_ok, "SECTORS": sectors_ok, "EVENT": event is not None,
                "EXCHANGE": exchange_m is not None, "IPO": ipo_m is not None,
                "STOCK_WATCH": stocks_m is not None, "WATCH": True}
        qualified = [k for k in OPTIONAL_PRIORITY if show[k]]
        for k in qualified[OPTIONAL_BUDGET:]:
            show[k] = False
            reasons[k] = (f"omitted: qualified ({reasons[k].removeprefix('included: ')}), but the "
                          f"Short already carries {OPTIONAL_BUDGET} optional sections of higher "
                          f"priority ({', '.join(qualified[:OPTIONAL_BUDGET])})")
        watch = _watch(brief, show, setup, overnight, vix_m, flows_m, event, sectors_m, stocks_m)
        reasons["WATCH"] = ("core: attention cues for the open - " +
                            "; ".join(f"{w.tag}: {w.title}" for w in watch))
        reasons["CLOSING"] = "core: minimal sign-off"

        wd3 = brief.previous_session.strftime("%a").upper()
        labels = {"OVERNIGHT": "OVERNIGHT", "SETUP": setup.chip, "VIX": f"INDIA VIX · {wd3}",
                  "FLOWS": f"FII / DII · {wd3}", "SECTORS": f"SECTORS · {wd3}",
                  "EVENT": "TODAY'S CALENDAR", "STOCK_WATCH": f"STOCK WATCH · {wd3}",
                  "EXCHANGE": "EXCHANGE WATCH", "IPO": "IPO WATCH",
                  "WATCH": "WATCH AT THE OPEN"}
        durations = {
            "OVERNIGHT": round(DUR["OVERNIGHT"] + DUR["OVERNIGHT_PER_CUE"] * max(0, len(overnight.cues) - 1)
                               + (DUR["GIFT"] if overnight.gift else 0.0), 2) if overnight else 0.0,
            "SETUP": DUR["SETUP_CHART"] if setup.kind == "CHART" else DUR["SETUP_PULSE"],
            "VIX": DUR["VIX"], "FLOWS": DUR["FLOWS"],
            "SECTORS": round(DUR["SECTORS"] + 0.1 * max(0, len(brief.sectors) - 6), 2),
            "EVENT": DUR["EVENT"],
            "STOCK_WATCH": round(DUR["STOCKS_BASE"] + DUR["STOCKS_PER"] * len(stocks_m.items), 2)
            if stocks_m else 0.0,
            "WATCH": round(DUR["WATCH_BASE"] + DUR["WATCH_PER"] * len(watch), 2),
            "EXCHANGE": round(DUR["EXCHANGE_BASE"] + DUR["EXCHANGE_PER"] * len(exchange_m["cards"]), 2)
            if exchange_m else 0.0,
            "IPO": (DUR["IPO_CARD"] if ipo_m["layout"] == "CARD" else
                    round(DUR["IPO_BOARD_BASE"] + DUR["IPO_BOARD_PER"] * len(ipo_m["rows"]), 2))
            if ipo_m else 0.0,
        }
        order = [k for k in SECTION_ORDER if show[k]]
        # hard ceiling: drop optional sections, lowest priority first, never core ones
        for k in tuple(reversed(OPTIONAL_PRIORITY)) + PUBLIC_OPTIONAL:
            if sum(durations[o] for o in order) + DUR["CLOSING"] + 5.0 <= MAX_RUNTIME:
                break
            if k in order:
                order.remove(k)
                show[k] = False
                reasons[k] = f"omitted: runtime ceiling {MAX_RUNTIME:.0f}s"
        return PreSectionPlan(
            version=PRE_PLAN_VERSION, pre_date=brief.pre_date.isoformat(),
            previous_session=brief.previous_session.isoformat(),
            show_global_context=show["OVERNIGHT"],
            show_gift_nifty=bool(overnight and overnight.gift), show_vix=show["VIX"],
            show_previous_session_setup=True, show_flows=show["FLOWS"], show_event=show["EVENT"],
            show_sector_context=show["SECTORS"], show_stock_watch=show["STOCK_WATCH"],
            show_watch=True, show_closing=True, order=order, reasons=reasons, labels=labels,
            durations=durations, overnight=overnight, setup=setup,
            vix=vix_m if show["VIX"] else None, flows=flows_m if show["FLOWS"] else None,
            event=event, sectors=sectors_m if show["SECTORS"] else None,
            stock_watch=stocks_m if show["STOCK_WATCH"] else None, watch=watch,
            exchange=exchange_m if show["EXCHANGE"] else None,
            ipo=ipo_m if show["IPO"] else None,
            watch_subline=("Reference points from " + brief.prev_weekday +
                           (" and overnight" if overnight else "") + ", not trade signals"),
            omitted=omitted, synthetic=brief.synthetic)


def plan_pre_sections(brief: PreMarketBrief) -> PreSectionPlan:
    return PreEditorialPlanner().plan(brief)


# --------------------------------------------------------------------------- language guard
# On top of the hook engine's lists: the phrasings a morning briefing is most tempted by.
PRE_PREDICTION_PATTERNS = [
    r"\bopen(?:s|ing)? (?:higher|lower|flat|up|down|positive|negative|strong|weak)\b",
    r"\bsofter start\b", r"\bstronger start\b", r"\bcontinu(?:e|es|ed|ation)\b",
    r"\brally\b", r"\brebound\b", r"\breversal\b", r"\bindicat(?:es|ing) (?:a|an|the)\b",
    r"\bsuggest(?:s|ing)?\b", r"\bset up for\b",
]
PRE_CAUSAL_PATTERNS = [r",\s*so\b", r"\bwhich (?:means|meant)\b", r"\bleading to\b",
                       r"\bthat's why\b", r"\bin response to\b", r"\breact(?:s|ed|ing)? to\b"]
PRE_RECOMMENDATION_PATTERNS = [r"\bhold\b", r"\bwatch for\b", r"\bkeep an eye\b",
                               r"\bconsider\b", r"\bavoid\b", r"\blevels? to trade\b"]


def pre_language_issues(texts: dict) -> list:
    """Every viewer-visible PRE string, checked with the hook engine's own deterministic word
    lists (prediction, causal, recommendation, hype) plus content safety. A PRE string that
    trips one is a bug in a template - the Short is not rendered."""
    import re

    from core.content_safety import SafetyStatus, classify_text
    from hooks import policy
    groups = {"prediction": policy.PREDICTION_PATTERNS + PRE_PREDICTION_PATTERNS,
              "causal": policy.CAUSAL_PATTERNS + PRE_CAUSAL_PATTERNS,
              "recommendation": policy.RECOMMENDATION_PATTERNS + PRE_RECOMMENDATION_PATTERNS}
    issues = []
    for key, text in texts.items():
        if not isinstance(text, str) or not text:
            continue
        low = text.lower()
        for name, pats in groups.items():
            for p in pats:
                if re.search(p, text, re.I):
                    issues.append(f"{key}: {name} wording ({p}) in {text!r}")
        for w in re.findall(r"[a-z][a-z\-]*", low):
            if w in policy.HYPE_WORDS:
                issues.append(f"{key}: hype word {w!r} in {text!r}")
        if classify_text(text).status is not SafetyStatus.SAFE:
            issues.append(f"{key}: content safety {classify_text(text).status.value} for {text!r}")
    return issues


__all__ = ["PreMarketBrief", "PreMarketBlocked", "PreSectionPlan", "PreEditorialPlanner",
           "plan_pre_sections", "build_pre_brief", "pre_language_issues", "GlobalCueModel",
           "GiftNiftyModel", "OvernightModel", "PreviousSessionSetupModel", "VixModel",
           "FlowsModel", "EventCardModel", "StockWatchModel", "WatchItemModel",
           "stock_fact_from_story", "non_ai_fact", "PRE_PLAN_VERSION", "OPTIONAL_BUDGET", "OPTIONAL_PRIORITY",
           "SECTION_ORDER", "MAX_RUNTIME"]
