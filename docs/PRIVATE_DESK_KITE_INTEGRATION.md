# Private Desk → Kite Terminal: future integration contract (NOT implemented)

Phase 1 has **no** Kite connection, no "send" button, no API endpoint, no broker library and no
order logic. This document fixes the contract now, so a future phase can build on it without
changing what the desk is.

## 1. Principle

The Private Desk produces **intelligence context**. The Kite Terminal, a separate owner-side
system, owns everything about trading: risk, sizing, order construction, account selection and
execution. The owner confirms every action there. The desk must never become a source of orders.

## 2. The candidate packet (`private_desk/packet.py`)

`PrivateCandidatePacket`, schema `private-candidate-packet-1.0`, is a frozen dataclass that
serialises to JSON with `to_dict()`.

| Field | Content |
|---|---|
| `schema_version` | `"private-candidate-packet-1.0"`. Changes only with a documented migration. |
| `session_date` | The Radar session (ISO date). |
| `symbol`, `company`, `sector` | NSE symbol; company name and NIFTY 200 industry sector from the Market Structure artifact. |
| `radar_state` | attention level, Radar novelty, desk appearance label, families, evidence direction, persistence, whether it was selected for a story |
| `detector_ids` | The recorded reason codes, e.g. `VOLUME_UNUSUAL`, `STRUCTURE_BREAK_ABOVE_20D_RANGE` |
| `reasons` | The deterministic detector sentences (WHY THIS STOCK) |
| `metrics` | price change, RVOL, 5D/20D vs NIFTY (pp), close, replay status. Values are `None` unless reconciled. |
| `market_context` | That session's breadth and index diagnostics |
| `sector_context` | The candidate's sector row for that session |
| `official_events` | Exchange list memberships (F&O ban, ASM, GSM, IPO) with list date |
| `source_artifact_ids` | Which stores and artifacts each part came from |
| `generated_at` | When the packet was built |
| `notes` | Always includes "Radar candidate = attention item, not a trade recommendation." |

**Forbidden, and enforced by `tests/test_private_desk.py::test_candidate_packet_contains_no_order_fields`:**
no field, at any nesting depth, may carry an order concept. That means no side, buy, sell,
quantity, lot, price limit, trigger, entry, exit, stop, target, product, account, leverage,
position size, broker, exchange order or tag. `FORBIDDEN_FIELD_TOKENS` lists them.

In Phase 1, packets are built only to feed the Radar CSV export. Nothing sends them.

## 3. Future flow (later phase, owner approval required)

```
Private Desk (read-only)
    │  owner clicks "Send for Trade Review"            <- not built
    ▼
PrivateCandidatePacket (JSON, context only)
    │  local hand-off (file drop or localhost call; to be decided)
    ▼
Kite Terminal
    │  independently runs its own risk / sizing / order logic
    ▼
owner confirms in Kite Terminal                    <- the only place an order can originate
```

Rules for that phase:
- The hand-off is one-way, desk → terminal. The desk never receives credentials, tokens or
  order state from the terminal.
- The desk stays read-only over DMB stores. Any review state it gains lives in its own store
  under `output/private_desk/`, never in the Radar history.
- Nothing leaves the machine.

## 4. Future feedback loop (Phase 3/4, document only)

```
Radar candidate
    ▼
owner reviewed (desk-side note: reviewed / dismissed)
    ▼
candidate sent to Kite Terminal
    ▼
trade accepted / rejected (terminal-side decision)
    ▼
actual trade result (terminal-side record)
    ▼
Private Desk research history (own store; joins on session_date + symbol + schema_version)
```

The research history would let Phase 2's outcome analytics separate "what the Radar flagged"
from "what the owner acted on". Until then the desk computes no forward returns and makes no
win-rate claims.
