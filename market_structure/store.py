"""The Market Structure artifact (`output/market_structure/market_structure_<session>.json`) and
the publishable facts a chosen insight contributes.

The artifact is INTERNAL: it keeps the per-constituent observations (named) so every public
count can be traced back to the observation ids behind it. Loading re-aggregates from those
observations and refuses a file whose stored counts do not match - a count that cannot be
reproduced from its observations is not published.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from core import sources as S
from presentation.provenance_label import session_label
from publication.classification import (ContentClass, Orientation, Origin, PublishableFact,
                                        RightsStatus, Scope)
from publication.classify import strictest_rights

from .aggregator import ReconciliationError, aggregate
from .observations import StructureObservation
from .universe import UniverseDefinition

SOURCE_NAMES = (S.SRC_YAHOO, S.SRC_NSE_CONSTITUENTS, S.SRC_MARKET_STRUCTURE)
SOURCE_LABEL = "Yahoo Finance EOD data · NSE Indices sectors"


def artifact_path(out_dir: str, session: dt.date) -> str:
    return os.path.join(out_dir, "market_structure", f"market_structure_{session.isoformat()}.json")


def save_snapshot(snapshot, observations, universe_def, out_dir: str, subset_defs=()) -> str:
    path = artifact_path(out_dir, dt.date.fromisoformat(snapshot.session_date))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"snapshot": snapshot.to_dict(), "universe": universe_def.to_dict(),
               "subset_universes": [s.to_dict() for s in subset_defs or ()],
               "observations": [o.to_dict() for o in observations],
               "private": True, "note": "internal artifact - names securities; public output "
                                        "shows counts only"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    return path


def load_snapshot(path: str):
    """(snapshot, universe_def, observations), re-aggregated and checked against the file."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    uni = UniverseDefinition.from_dict(d["universe"])
    subs = [UniverseDefinition.from_dict(s) for s in d.get("subset_universes") or []]
    obs = [StructureObservation.from_dict(o) for o in d["observations"]]
    session = dt.date.fromisoformat(d["snapshot"]["session_date"])
    snap = aggregate(obs, uni, session, subs)
    for k, m in snap.metrics.items():
        stored = d["snapshot"]["metrics"].get(k) or {}
        if (stored.get("numerator"), stored.get("denominator")) != (m.numerator, m.denominator):
            raise ReconciliationError(f"{path}: stored {k} does not reproduce from observations")
    return snap, uni, obs


def structure_facts(insight, snapshot, session: dt.date) -> list:
    """The PublishableFacts one chosen insight puts on screen - MARKET scope, our own aggregate,
    tagged MARKET_STRUCTURE and carrying its universe (the gate refuses one without)."""
    rights = strictest_rights(SOURCE_NAMES)
    prov = session_label(SOURCE_LABEL, session)
    out = []
    for i, text in enumerate(insight.public_strings()):
        out.append(PublishableFact(
            fact_id=f"structure.{insight.kind.lower()}.{i}", text=text, scope=Scope.MARKET,
            origin=Origin.INTERNAL_ANALYTICS, content_class=ContentClass.MARKET_AGGREGATE,
            orientation=Orientation.HISTORICAL, source_name=S.SRC_MARKET_STRUCTURE,
            source_label=prov.source, source_reference=snapshot.universe_source.get(
                "source_reference", ""), data_as_of=session, universe=snapshot.universe_label,
            publication_rights_status=rights if rights is not RightsStatus.UNKNOWN
            else RightsStatus.UNKNOWN, section="STRUCTURE",
            tags=frozenset({"MARKET_STRUCTURE"})))
    return out


__all__ = ["save_snapshot", "load_snapshot", "structure_facts", "artifact_path", "SOURCE_LABEL",
           "SOURCE_NAMES"]
