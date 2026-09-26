# EXCHANGE WATCH - official exchange developments

The only route by which a named security reaches the public Short: an **official** list says
something about it, dated, with its reference. The card states the exchange's status in the
exchange's terms - never what it "means" for the price.

```
OfficialEventSource (parser + fail-closed fetch)  ->  ExchangeEvent (dated, referenced)
        -> validate(list date)  -> mark_changes(previous STORED list)
        -> OfficialEvidenceResolver (Radar candidate -> PUBLIC via official event / PRIVATE)
        -> select_events (factual order, <= 3 cards)  -> publication gate  -> EXCHANGE WATCH scene
```

## Implemented event families (V1)

| Family | Official source | Dated by | Notes |
|---|---|---|---|
| `FNO_BAN` | `nsearchives.nseindia.com/content/fo/fo_secban.csv` (archive file) | its header "Securities in Ban For Trade Date DD-MON-YYYY" - must equal the trade date exactly | fixed line: "New F&O positions are restricted while the exchange ban is in effect." |
| `SURVEILLANCE_ASM` | `www.nseindia.com/api/reportASM` (website API, undocumented) | each row's `asmTime`; accepted within 4 days before the list date | only NIFTY 200 constituents qualify (the list runs to hundreds of small names) |
| `SURVEILLANCE_GSM` | `www.nseindia.com/api/reportGSM` (website API, undocumented) | each row's `gsmTime` | same |

`CORPORATE_EVENT` exists in the model and the policy (an official corporate announcement may
name a company) but has no production adapter yet. Planned: board meetings/results, price-band
changes, trade-to-trade moves, market-wide position limits, bulk/block disclosures, exchange
clarifications - each needs its own official source, validator and test first.

## Rules

- A payload whose shape or date does not validate yields `INVALID` and **no** events; a stale
  list (yesterday's ban file) is rejected, never relabelled.
- `NEW` / `CONTINUING` only against a previously STORED list (`output/exchange_watch/`); with
  none, the change is `UNKNOWN` and "new" is never claimed.
- Selection order: F&O ban, GSM, ASM, corporate; NEW before CONTINUING before UNKNOWN; index
  members first; then symbol. One card per security, at most three.
- Every card's facts are SECURITY / OFFICIAL_EXCHANGE / EXCHANGE_EVENT with the official
  reference - the only combination the gate lets name a security. Provenance plate:
  "SOURCE: NSE / LIST DATE: 28 SEP 2026".
- Real exchange payloads are never committed; tests use synthetic, structurally identical rows.

Verified live 2026-09-26: the F&O ban file (trade date 28 Sep 2026: LICHSGFIN, SAIL, KAYNES),
reportASM and reportGSM all parsed and validated.
