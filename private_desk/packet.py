"""PrivateCandidatePacket - the stable, versioned INTELLIGENCE CONTEXT of one Radar candidate.

Defined now so a future integration (docs/PRIVATE_DESK_KITE_INTEGRATION.md) has a contract to
build against. It carries observations and their provenance ONLY: no side, quantity, price,
order type, product, account, entry, stop or target - a test fails if any such field appears.
Phase 1 builds packets for the CSV export; nothing sends them anywhere.

1.1 (private market regime V1): adds `market_regime` - the session's regime label, its dimension
states, reason code and calculation version (docs/PRIVATE_MARKET_REGIME.md). CONTEXT ONLY: it
describes the market the candidate appeared in and never changes the candidate's evidence.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "private-candidate-packet-1.1"

# Field names that would turn context into an instruction. Never allowed on the packet.
FORBIDDEN_FIELD_TOKENS = ("order", "quantity", "qty", "price_limit", "limit_price",
                          "trigger", "stop", "target", "entry", "exit", "side",
                          "transaction", "buy", "sell", "account", "product", "lot",
                          "leverage", "position_size", "broker", "exchange_order", "tag")


@dataclass(frozen=True)
class PrivateCandidatePacket:
    schema_version: str
    session_date: str
    symbol: str
    company: str | None
    sector: str | None
    radar_state: dict                    # attention, novelty, appearance, families, direction
    detector_ids: tuple                  # recorded reason codes, e.g. VOLUME_UNUSUAL
    reasons: tuple                       # deterministic detector sentences (WHY THIS STOCK)
    metrics: dict                        # replayed detector values (None when not reconciled)
    market_context: dict                 # breadth / index diagnostics of the same session
    sector_context: dict                 # the candidate's sector row of the same session
    official_events: tuple               # exchange list memberships (F&O ban, ASM, GSM, IPO)
    source_artifact_ids: tuple           # where each part came from
    generated_at: str
    notes: tuple = field(default_factory=tuple)
    market_regime: dict = field(default_factory=dict)   # 1.1: label + dimension states (context)

    def to_dict(self) -> dict:
        return asdict(self)


def build_packet(view: dict, *, market_context: dict, sector_row: dict | None,
                 source_artifacts: list, generated_at: dt.datetime,
                 market_regime: dict | None = None) -> PrivateCandidatePacket:
    reasons = tuple(i["text"] for i in view["why"] if i.get("text"))
    return PrivateCandidatePacket(
        schema_version=SCHEMA_VERSION, session_date=view["session"].isoformat(),
        symbol=view["symbol"], company=view.get("company"), sector=view.get("sector"),
        radar_state={"attention_level": view["attention_level"],
                     "novelty_type": view["novelty_type"], "appearance": view["appearance"],
                     "families": list(view["families"]), "direction": view["direction"],
                     "persistence": view["persistence"],
                     "selected_for_story": view["selected"]},
        detector_ids=tuple(view["reason_codes"]), reasons=reasons,
        metrics={"price_change_pct": view["price_change_pct"],
                 "relative_volume": view["relative_volume"],
                 "vs_nifty_5d_pp": view["rel_5d_pp"], "vs_nifty_20d_pp": view["rel_20d_pp"],
                 "close": view["close"], "replay_status": view["replay_status"]},
        market_context=dict(market_context), sector_context=dict(sector_row or {}),
        official_events=tuple({"kind": e["kind"], "status": e["status"],
                               "source_date": e.get("source_date")} for e in view["official"]),
        source_artifact_ids=tuple(source_artifacts), generated_at=generated_at.isoformat(),
        notes=("Radar candidate = attention item, not a trade recommendation.",
               "Market regime = context of the session, not a forecast or a recommendation."),
        market_regime=dict(market_regime or {}))


__all__ = ["PrivateCandidatePacket", "build_packet", "SCHEMA_VERSION", "FORBIDDEN_FIELD_TOKENS"]
