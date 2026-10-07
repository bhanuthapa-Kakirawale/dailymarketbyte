"""SYNTHETIC public-intelligence scenarios (A-Q) for tests and review renders.

Every name is a placeholder (STOCK-001, DEMO IPO A LTD) so nothing here can be mistaken for a
real security, and every render built from it carries the red SYNTHETIC badge. The shapes match
the real sources (NSE constituent file, fo_secban.csv, reportASM, NSE issue lists) so the SAME
parsers, validators, selectors and gates run on them.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import market_structure as ms
from exchange_watch import mark_changes, parse_asm, parse_fo_ban, validate
from exchange_watch.models import Change, EventFamily, ExchangeEvent
from ipo_watch import (BoardType, FinancialRow, IPOEvent, IPOStatus, ListingOutcome,
                       Subscription)
from presentation.public_intelligence import PublicIntelligence

SYNTHETIC_WATERMARK = "SYNTHETIC DATA - NOT REAL"
INDUSTRIES = ["Healthcare", "Financial Services", "Automobile and Auto Components",
              "Information Technology", "Capital Goods", "Fast Moving Consumer Goods",
              "Metals & Mining", "Oil Gas & Consumable Fuels", "Power", "Realty"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def universe(n=200, index="NIFTY200", prefix="STOCK") -> ms.UniverseDefinition:
    lines = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    for i in range(n):
        lines.append(f"Demo Company {prefix} {i:03d} Ltd.,{INDUSTRIES[i % len(INDUSTRIES)]},"
                     f"{prefix}-{i:03d},EQ,SYNTH{i:05d}")
    return ms.from_constituent_csv("\n".join(lines), index,
                                   f"synthetic://ind_{index.lower()}list.csv",
                                   "2026-09-25T19:30:00+05:30")


def _sector_members(uni, industry):
    return [s for s, c in sorted(uni.constituents.items()) if c.industry == industry]


def structure(session: dt.date, prev: dt.date, scenario: str, with_nifty100: bool = False):
    """(snapshot, universe, observations) for one Market Structure scenario:
        QUIET          few events, mixed tape                     -> nothing to show
        SELLOFF        ~85% of the universe lower                 -> broad move
        CONCENTRATED   18 unusual-volume stocks, 6 in Healthcare  -> concentration (one-third)
        MANY_UNUSUAL   42 unusual-volume stocks across sectors
        DIVERGENCE     Nifty up while 130 of 200 fell
        RANGE          14 range breaks up, 3 down"""
    uni = universe()
    syms = sorted(uni.constituents)
    unusual, up, down, moves = [], [], [], {}
    hc = _sector_members(uni, "Healthcare")
    fin = _sector_members(uni, "Financial Services")
    auto = _sector_members(uni, "Automobile and Auto Components")
    it = _sector_members(uni, "Information Technology")
    if scenario == "CONCENTRATED":
        unusual = hc[:6] + fin[:4] + auto[:3] + it[:2] + [syms[4], syms[6], syms[8]]
    elif scenario == "MANY_UNUSUAL":
        unusual = syms[::3][:42] + syms[1:40:9]
    elif scenario == "RANGE":
        up = syms[1:29:2]
        down = syms[100:103]
    for i, s in enumerate(syms):
        if scenario == "SELLOFF":
            moves[s] = -1.2 if i % 7 else 0.4
        elif scenario == "DIVERGENCE":
            moves[s] = -0.8 if i < 130 else 0.9
        else:
            moves[s] = 0.5 if i % 2 else -0.5
    vol = NS(scanned=list(syms), anomalies=[NS(instrument=s, relative_volume=2.8) for s in unusual])
    ev = lambda names: [NS(event_type=NS(value=n)) for n in names]
    tech = NS(scanned=list(syms),
              flagged=[NS(instrument=s, events=ev(["BREAK_ABOVE_20D_RANGE"])) for s in up] +
              [NS(instrument=s, events=ev(["BREAK_BELOW_20D_RANGE"])) for s in down])
    series = {s: [{"date": prev, "close": 100.0}, {"date": session, "close": 100.0 + moves[s]}]
              for s in syms}
    obs = ms.build_observations(uni, session, prev, vol, tech, series)
    subs = []
    if with_nifty100:
        subs = [ms.from_constituent_csv(
            "\n".join(["Company Name,Industry,Symbol,Series,ISIN Code"] +
                      [f"Demo Company STOCK {i:03d} Ltd.,{INDUSTRIES[i % len(INDUSTRIES)]},"
                       f"STOCK-{i:03d},EQ,SYNTH{i:05d}" for i in range(100)]),
            "NIFTY100", "synthetic://ind_nifty100list.csv", "2026-09-25T19:30:00+05:30")]
    return ms.aggregate(obs, uni, session, subs), uni, obs


# --------------------------------------------------------------------------- exchange events
def fo_ban_text(trade_date: dt.date, symbols) -> str:
    head = f"Securities in Ban For Trade Date {trade_date.strftime('%d-%b-%Y').upper()}:"
    return "\n".join([head] + [f"{i},{s}" for i, s in enumerate(symbols, 1)])


def exchange_events(list_date: dt.date, scenario: str, uni=None) -> list:
    """F:  FNO_BAN        two securities in the ban period (one new vs the previous list)
       G:  SURVEILLANCE   one NIFTY 200 constituent on the long-term ASM list
       H:  CORPORATE      one official corporate announcement (model-level; adapter planned)"""
    out = []
    if scenario in ("FNO_BAN", "ALL"):
        res = parse_fo_ban(fo_ban_text(list_date, ["STOCK-011", "STOCK-042"]),
                           "2026-09-25T20:05:00+05:30", "synthetic://fo_secban.csv")
        prev = parse_fo_ban(fo_ban_text(list_date - dt.timedelta(days=1), ["STOCK-042"]),
                            None, "synthetic://fo_secban.csv").events
        ok, _ = validate(res.events, list_date)
        out += mark_changes(ok, prev)
    if scenario in ("SURVEILLANCE", "ALL"):
        payload = {"longterm": {"data": [
            {"asmSurvIndicator": "Stage I", "asmTime": list_date.strftime("%d-%b-%Y"),
             "companyName": "Demo Company STOCK 077 Ltd.", "symbol": "STOCK-077",
             "survDesc": "Long Term Additional Surveillance Measure (LTASM) - Stage I"},
            {"asmSurvIndicator": "Stage II", "asmTime": list_date.strftime("%d-%b-%Y"),
             "companyName": "Demo Outside Co Ltd.", "symbol": "OUTSIDE-1",
             "survDesc": "Long Term Additional Surveillance Measure (LTASM) - Stage II"}]}}
        ok, _ = validate(parse_asm(payload, "2026-09-25T20:05:00+05:30",
                                   "synthetic://reportASM").events, list_date)
        out += ok
    if scenario in ("CORPORATE",):
        out.append(ExchangeEvent(
            event_id=f"CORP:{list_date}:STOCK-120", family=EventFamily.CORPORATE_EVENT,
            symbol="STOCK-120", company="Demo Company STOCK 120 Ltd.", status="BOARD MEETING",
            detail="Board meeting to consider quarterly results", data_as_of=list_date,
            source_name="nse_website", source_reference="synthetic://corporate-announcements",
            change=Change.UNKNOWN, validation_status="VALIDATED"))
    return out


# --------------------------------------------------------------------------- IPOs
def ipos(day: dt.date, scenario: str) -> list:
    """L OPENS_TODAY, M CLOSES_TODAY (+ bids with the exchange timestamp), N LISTING_DAY,
    O MISSING_SUBSCRIPTION (bids without a timestamp -> omitted), BOARD (several events)."""
    base = dict(source_name="nse_ipo_issues", source_reference="synthetic://nse-ipo-issues",
                data_as_of=day, retrieved_at="2026-09-25T07:20:00+05:30")
    a = IPOEvent(company_name="DEMO IPO A LTD", board_type=BoardType.MAINBOARD,
                 status=IPOStatus.OPEN, symbol="DEMOA", issue_open_date=day - dt.timedelta(days=2),
                 issue_close_date=day, price_band_low=258.0, price_band_high=272.0, lot_size=55,
                 fresh_issue_crore=700.0, ofs_crore=500.0, issue_size_crore=1200.0,
                 official_document_reference="RHP dated 18 Sep 2026 (synthetic://sebi-rhp)",
                 subscription=Subscription(total=4.8, retail=2.1, qib=7.9, nii=3.3,
                                           as_of=dt.datetime.combine(day - dt.timedelta(days=1),
                                                                     dt.time(17, 0), IST),
                                           source_name="nse_ipo_issues",
                                           source_reference="synthetic://nse-bid-details"),
                 financials=[FinancialRow("Revenue from operations", "FY26", 1234.5, "RHP",
                                          "RHP p. 312")], **base)
    b = IPOEvent(company_name="DEMO IPO B LTD", board_type=BoardType.MAINBOARD,
                 status=IPOStatus.UPCOMING, symbol="DEMOB", issue_open_date=day,
                 issue_close_date=day + dt.timedelta(days=2), price_band_low=132.0,
                 price_band_high=139.0, **base)
    c = IPOEvent(company_name="DEMO IPO C LTD", board_type=BoardType.SME, status=IPOStatus.LISTING,
                 symbol="DEMOC", issue_open_date=day - dt.timedelta(days=8),
                 issue_close_date=day - dt.timedelta(days=5), listing_date=day,
                 price_band_low=51.0, price_band_high=54.0,
                 listing=ListingOutcome(issue_price=54.0, listing_price=58.1, close_price=57.2,
                                        session=day, source_name="nse_website",
                                        source_reference="synthetic://nse-quote"), **base)
    d = IPOEvent(company_name="DEMO IPO D LTD", board_type=BoardType.MAINBOARD,
                 status=IPOStatus.OPEN, symbol="DEMOD", issue_open_date=day - dt.timedelta(days=1),
                 issue_close_date=day, price_band_low=290.0, price_band_high=302.0,
                 subscription=Subscription(total=1.3, as_of=None, source_name="nse_ipo_issues",
                                           source_reference="synthetic://nse-ipo-issues"), **base)
    return {"OPENS_TODAY": [b], "CLOSES_TODAY": [a], "LISTING_DAY": [c],
            "MISSING_SUBSCRIPTION": [d], "BOARD": [a, b, c], "NONE": []}[scenario]


def intelligence(session: dt.date, prev: dt.date, list_date: dt.date, structure_scenario=None,
                 exchange_scenario=None, ipo_scenario=None, with_nifty100=False, ipo_day=None):
    """`ipo_day`: the date the IPO events are dated on (PRE: the morning's date = list_date;
    POST: the recap session)."""
    snap = uni = None
    if structure_scenario:
        snap, uni, _ = structure(session, prev, structure_scenario, with_nifty100)
    uni = uni or universe()
    return PublicIntelligence(
        structure=snap,
        exchange_events=exchange_events(list_date, exchange_scenario, uni) if exchange_scenario else [],
        ipos=ipos(ipo_day or list_date, ipo_scenario) if ipo_scenario else [],
        known_securities=uni.companies(), universe_symbols=uni.symbols(), synthetic=True,
        exchange_status="SYNTHETIC" if exchange_scenario else "NOT_AVAILABLE",
        ipo_status="SYNTHETIC" if ipo_scenario else "NOT_AVAILABLE",
        structure_status="SYNTHETIC" if structure_scenario else "NOT_AVAILABLE")


__all__ = ["universe", "structure", "exchange_events", "ipos", "intelligence", "fo_ban_text",
           "SYNTHETIC_WATERMARK"]
