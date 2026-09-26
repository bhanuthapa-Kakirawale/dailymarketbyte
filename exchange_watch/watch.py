"""Validation, change detection, official-evidence resolution, editorial selection and the
public model for EXCHANGE WATCH.

    OfficialEvidenceResolver(events).resolve(symbol)
        official factual event exists -> PUBLIC: the story may describe THAT event, in the
                                         exchange's terms, citing the exchange
        none                          -> PRIVATE: the security stays out of public output (it
                                         may still count anonymously in Market Structure)

A Radar candidate never becomes public because the Radar liked it. The resolver only looks the
symbol up in official lists; the resulting card never says "Radar", "breakout" or "unusual
volume" - the language scan would block it if it did.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field, replace

from presentation.provenance_label import ProvenanceLabel, fmt_date
from publication.classification import (ContentClass, Orientation, Origin, PublishableFact,
                                        Scope)
from publication.classify import SOURCE_LABELS
from publication.rights import rights_for

from .models import Change, EventFamily, ExchangeEvent

MAX_CARDS = 3
# Fixed, factual one-liners per family - the exchange's status, never its implication.
FAMILY_TAG = {EventFamily.FNO_BAN: "F&O BAN", EventFamily.SURVEILLANCE_ASM: "ASM",
              EventFamily.SURVEILLANCE_GSM: "GSM", EventFamily.CORPORATE_EVENT: "CORPORATE EVENT"}
FAMILY_LINE = {
    EventFamily.FNO_BAN: "New F&O positions are restricted while the exchange ban is in effect.",
    EventFamily.SURVEILLANCE_ASM: "On NSE's Additional Surveillance Measure list.",
    EventFamily.SURVEILLANCE_GSM: "On NSE's Graded Surveillance Measure list.",
    EventFamily.CORPORATE_EVENT: "Official company announcement filed with the exchange.",
}
FAMILY_PRIORITY = {EventFamily.FNO_BAN: 0, EventFamily.SURVEILLANCE_GSM: 1,
                   EventFamily.SURVEILLANCE_ASM: 2, EventFamily.CORPORATE_EVENT: 3}


# --------------------------------------------------------------------------- validation
SURVEILLANCE_WINDOW_DAYS = 4     # an ASM/GSM list is dated by its own publication day


def validate(events, expected_list_date: dt.date) -> tuple:
    """(valid, rejected) - an event is valid only if it carries a symbol and a source reference
    and is dated for the expected date: the F&O ban file EXACTLY for the trade date; a
    surveillance list on or up to SURVEILLANCE_WINDOW_DAYS before it (never after). A stale list
    (yesterday's ban file) is rejected, never relabelled."""
    ok, bad = [], []
    for e in events:
        why = None
        surveillance = e.family in (EventFamily.SURVEILLANCE_ASM, EventFamily.SURVEILLANCE_GSM)
        if not e.symbol:
            why = "no symbol"
        elif not e.source_reference:
            why = "no source reference"
        elif surveillance and not (0 <= (expected_list_date - e.data_as_of).days
                                   <= SURVEILLANCE_WINDOW_DAYS):
            why = (f"surveillance list dated {e.data_as_of}, outside {SURVEILLANCE_WINDOW_DAYS} "
                   f"days before {expected_list_date}")
        elif not surveillance and e.data_as_of != expected_list_date:
            why = f"list dated {e.data_as_of}, expected {expected_list_date}"
        if why:
            bad.append({"event_id": e.event_id, "reason": why})
        else:
            ok.append(replace(e, validation_status="VALIDATED"))
    return ok, bad


def mark_changes(events, previous_events) -> list:
    """NEW / CONTINUING against the previous stored list of the same family. With no previous
    list the change is UNKNOWN - "new" is never claimed without evidence."""
    prev = {}
    for e in previous_events or []:
        prev.setdefault(e.family, set()).add(e.symbol)
    out = []
    for e in events:
        if e.family not in prev:
            out.append(replace(e, change=Change.UNKNOWN))
        else:
            out.append(replace(e, change=Change.CONTINUING if e.symbol in prev[e.family]
                               else Change.NEW))
    return out


# --------------------------------------------------------------------------- resolver
@dataclass
class ResolvedEvidence:
    symbol: str
    public: bool
    events: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "public": self.public, "reason": self.reason,
                "events": [e.event_id for e in self.events]}


class OfficialEvidenceResolver:
    def __init__(self, validated_events):
        self._by_symbol = {}
        for e in validated_events:
            if e.validation_status == "VALIDATED":
                self._by_symbol.setdefault(e.symbol, []).append(e)

    def resolve(self, symbol: str) -> ResolvedEvidence:
        evs = self._by_symbol.get(symbol, [])
        if not evs:
            return ResolvedEvidence(symbol, False, [], "no official exchange/company event - "
                                                        "stays PRIVATE (may count anonymously in "
                                                        "Market Structure)")
        return ResolvedEvidence(symbol, True, evs, "official event: " + ", ".join(
            f"{e.family.value} ({SOURCE_LABELS.get(e.source_name, e.source_name)}, "
            f"{e.data_as_of})" for e in evs))

    def resolve_all(self, symbols) -> list:
        return [self.resolve(s) for s in symbols]


# --------------------------------------------------------------------------- selection
def select_events(events, universe_symbols=None, limit: int = MAX_CARDS) -> tuple:
    """Transparent factual order: implemented family priority (F&O ban first), NEW before
    CONTINUING before UNKNOWN, index-universe members first (broad audience), then symbol.
    Surveillance lists run to hundreds of names, so only index-universe members qualify for
    them. Returns (chosen, omitted [{event_id, reason}])."""
    uni = set(universe_symbols or ())
    change_rank = {Change.NEW: 0, Change.CONTINUING: 1, Change.UNKNOWN: 2}
    eligible, omitted = [], []
    for e in events:
        if e.family in (EventFamily.SURVEILLANCE_ASM, EventFamily.SURVEILLANCE_GSM) and e.symbol not in uni:
            omitted.append({"event_id": e.event_id, "reason": "surveillance entry outside the "
                                                              "index universe"})
            continue
        if e.family not in FAMILY_PRIORITY:
            omitted.append({"event_id": e.event_id, "reason": "family not published in V1"})
            continue
        eligible.append(e)
    eligible.sort(key=lambda e: (FAMILY_PRIORITY[e.family], change_rank[e.change],
                                 e.symbol not in uni, e.symbol))
    # one card per security
    chosen, seen = [], set()
    for e in eligible:
        if e.symbol in seen:
            omitted.append({"event_id": e.event_id, "reason": "one card per security"})
            continue
        if len(chosen) >= limit:
            omitted.append({"event_id": e.event_id, "reason": f"card limit {limit}"})
            continue
        chosen.append(e)
        seen.add(e.symbol)
    return chosen, omitted


# --------------------------------------------------------------------------- public model
def _card(e: ExchangeEvent) -> dict:
    tag = FAMILY_TAG.get(e.family, e.family.value)
    status = e.status if e.family is not EventFamily.FNO_BAN else f"TRADE DATE {fmt_date(e.data_as_of)}"
    change = {Change.NEW: "Entered the list for this date", Change.CONTINUING:
              "Also on the previous list"}.get(e.change, "")
    return {"tag": tag, "name": e.symbol, "company": e.company, "status": status.upper(),
            "line": FAMILY_LINE.get(e.family, ""), "change": change,
            "event_id": e.event_id, "family": e.family.value}


def build_model(chosen, mode: str = "POST") -> dict | None:
    if not chosen:
        return None
    n = len(chosen)
    families = {e.family for e in chosen}
    if families == {EventFamily.FNO_BAN}:
        headline = ("One security in the F&O ban period" if n == 1
                    else f"{n} securities in the F&O ban period")
    else:
        word = {1: "One", 2: "Two", 3: "Three"}.get(n, str(n))
        headline = f"{word} exchange update{'s' if n > 1 else ''}" + (
            " before the bell" if mode == "PRE" else "")
    srcs = sorted({SOURCE_LABELS.get(e.source_name, e.source_name) for e in chosen})
    dates = sorted({e.data_as_of for e in chosen})
    label = ProvenanceLabel(source=" · ".join(srcs), data_as_of=" / ".join(fmt_date(d) for d in dates),
                            as_of_label="LIST DATE")
    return {"headline": headline, "cards": [_card(e) for e in chosen],
            "sources": sorted({e.source_name for e in chosen}),
            "provenance": label.to_dict(), "provenance_lines": label.lines(),
            "event_ids": [e.event_id for e in chosen]}


def exchange_facts(chosen) -> list:
    """PublishableFacts for the chosen events: SECURITY scope, OFFICIAL_EXCHANGE origin,
    EXCHANGE_EVENT class, with the official reference - what lets the gate name the security."""
    out = []
    for e in chosen:
        c = _card(e)
        for i, text in enumerate(s for s in (c["name"], c["company"], c["tag"], c["status"],
                                              c["line"], c["change"]) if s):
            out.append(PublishableFact(
                fact_id=f"exchange.{e.event_id}.{i}", text=text, scope=Scope.SECURITY,
                origin=Origin.OFFICIAL_EXCHANGE, content_class=ContentClass.EXCHANGE_EVENT,
                orientation=Orientation.CURRENT_FACT, source_name=e.source_name,
                source_label=SOURCE_LABELS.get(e.source_name, e.source_name),
                source_reference=e.source_reference, data_as_of=e.data_as_of,
                retrieved_at=e.retrieved_at, security=e.symbol,
                publication_rights_status=rights_for(e.source_name).status,
                verification_status=e.validation_status, section="EXCHANGE_WATCH"))
    return out


# --------------------------------------------------------------------------- store
def store_path(out_dir: str, list_date: dt.date) -> str:
    return os.path.join(out_dir, "exchange_watch", f"exchange_events_{list_date.isoformat()}.json")


def save_events(events, out_dir: str, list_date: dt.date, sources=()) -> str:
    path = store_path(out_dir, list_date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"list_date": list_date.isoformat(), "events": [e.to_dict() for e in events],
                   "sources": [s.to_dict() if hasattr(s, "to_dict") else s for s in sources]},
                  fh, indent=2, ensure_ascii=False)
    return path


def load_previous(out_dir: str, list_date: dt.date, max_back: int = 7) -> list:
    """The most recent stored list strictly before `list_date` (never the same day)."""
    for k in range(1, max_back + 1):
        p = store_path(out_dir, list_date - dt.timedelta(days=k))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return [ExchangeEvent.from_dict(e) for e in json.load(fh)["events"]]
    return []


__all__ = ["validate", "mark_changes", "OfficialEvidenceResolver", "ResolvedEvidence",
           "select_events", "build_model", "exchange_facts", "save_events", "load_previous",
           "FAMILY_LINE", "FAMILY_TAG", "MAX_CARDS"]
