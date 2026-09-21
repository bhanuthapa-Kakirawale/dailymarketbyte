# Data model

Four types carry everything: `Observation`, `Fact`, `ValidationResult`, `MarketReport`.
All are plain dataclasses in `core/`, serialise to JSON-compatible dicts, and deserialise back.

## Observation

What exactly one source said, exactly once. **Frozen** - validation reads Observations and
never rewrites them. If a number looks wrong, that judgement belongs in a `ValidationResult`,
not in an edit to the record of what the provider actually returned.

| Field              | Notes                                                        |
| ------------------ | ------------------------------------------------------------ |
| `observation_id`   | Deterministic: `obs_{metric}-{instrument}-{source}-{date}`   |
| `metric`           | `Metric` enum - what was measured                            |
| `instrument`       | What it was measured on (`NIFTY 50`, `RELIANCE`, `IT`)       |
| `value` / `unit`   | `None` value means the source had nothing to say             |
| `market_date`      | The session this reading belongs to                          |
| `observed_at`      | When the source says it was true (often unknown -> `None`)   |
| `retrieved_at`     | When we fetched it. Timezone-aware, IST                      |
| `source_name`      | Stable id: `nse_website`, `yahoo_finance`, `gemini`          |
| `source_type`      | `SourceType` enum - drives the trust rules                   |
| `source_reference` | Endpoint, URL or ticker where available                      |
| `metadata`         | Anything source-specific worth keeping                       |

Ids are deterministic so the same reading arriving by two routes collapses to one
observation instead of masquerading as two agreeing sources.

## Fact

One normalized market fact plus every observation and check behind it.

`value` is the **published** number - the one the video showed - chosen by collection order,
never by averaging. Averaging two disagreeing sources would manufacture a number no source
ever reported. `metadata.published_value_source` names where it came from, and the other
observations sit beside it so a reader can see what the alternatives said.

`validation_status` is the verdict; `validation_results` is the audit trail that justifies it.

## ValidationResult

One check, its verdict, and enough detail to re-argue the verdict later:

```json
{
  "validator": "cross_source",
  "status": "CONFLICT",
  "message": "INDEX_CLOSE sources disagree: {...}",
  "checked_at": "2026-09-21T07:40:00+05:30",
  "details": {
    "values": {"yahoo_finance": 25140.35, "nse_website": 25900.0},
    "source_types": {"yahoo_finance": "SECONDARY", "nse_website": "PRIMARY"},
    "min": 25140.35, "max": 25900.0,
    "difference": 759.65, "difference_pct": 2.933,
    "tolerance_pct": 0.2, "critical_metric": true
  }
}
```

`details` always carries the compared values. Without them a `CONFLICT` in a six-month-old
report is unactionable.

## MarketReport

The contract between market intelligence and any presentation layer.

- **Identity**: `report_id`, `report_type`, `report_date`, `generated_at`
- **`session_date`**: the trading session described, which is *not* the report date - a
  Monday pre-market report describes Friday's session
- **`facts`**: every canonical Fact with full provenance
- **Presentation sections**: `nifty`, `technicals`, `global_cues`, `institutional_flows`,
  `sectors`, `gainers`, `losers`, `events` - shaped close to what the renderer uses, each
  carrying `fact_id`/`fact_ids` back-references into `facts`
- **`validation_summary`**: status counts, `publication_ready`, `blocking_issues`

Serialise with `to_dict()` / `to_json()`, restore with `from_dict()` / `from_json()`.
Round-tripping preserves provenance down to individual observations and validation details.

## Enums

`SourceType` - `PRIMARY` (exchange/regulator), `SECONDARY` (established redistributor),
`BROKER` (accurate but usually not publishable), `NEWS`, `AI`, `DERIVED` (computed here).

`ValidationStatus` - `UNVALIDATED`, `VERIFIED`, `SINGLE_SOURCE`, `PROVISIONAL`, `CONFLICT`,
`STALE`, `MISSING`, `REJECTED`.

`ReportType` - `PRE_MARKET`, `POST_MARKET` (enum only; no post-market code exists).

`Metric` - index/stock/sector/flow/commodity/FX/technical measures. `CRITICAL_METRICS` marks
those an LLM cannot verify alone; `REQUIRED_METRICS` marks those a report cannot omit.
