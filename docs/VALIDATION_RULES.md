# Validation rules

Validation runs at two levels. Per-observation checks decide whether a single reading
qualifies at all; the cross-source check decides how much the surviving readings actually
establish. Every validator is deterministic and unit-tested offline (`tests/test_validation.py`).

## 1. Source validation

Each observation carries a `SourceType` that fixes how much independent weight it can
contribute. Authority order, used when choosing which source a fact quotes:

```
PRIMARY > SECONDARY > BROKER > NEWS > DERIVED > AI
```

`DERIVED` means this pipeline computed it (EMA, RSI, pivots, relative volume). Nothing
external vouches for a derived value and nothing can corroborate it, so derived facts sit at
`SINGLE_SOURCE` by design.

## 2. Date validation

An observation's `market_date` must equal the session being reported.

**A mismatch is `REJECTED`, not `STALE`.** This is a deliberate distinction: a wrong-dated
reading is not an old copy of the right number, it is a different day's number. Publishing it
would misattribute one session's move to another - precisely the yfinance day-gap failure
(ADANIGREEN, 17 Sep 2026) that the `prev_date` guards in `market.py` already defend against.

A rejected observation is dropped from consideration. If some observations survive, the fact
continues with those. If none do, the fact is `MISSING` even when the pipeline still holds a
display value - the artifact must never imply support that is not there.

## 3. Freshness validation

Age of `observed_at` (or `retrieved_at` when the source gives no timestamp) against a
configured `max_age` produces `STALE`. Unset `max_age` skips the check; `now` is injectable
so freshness is testable without a clock.

## 4. Range validation

Magnitude sanity only - it catches unit errors and obvious nonsense. Defaults mirror the
sanity ranges `news.py` already enforced (FII/DII within +/-60,000 crore, and so on).

**Passing a range check is explicitly not verification.** A plausible wrong number passes.
This is the reason the LLM rule below cannot be satisfied by range-checking alone.

## 5. Cross-source verification — counted in independence groups

**Corroboration is counted in independence groups, not in observations.** Two readings from
one source are one witness however they were fetched, so counting observations would let a
single source appear to confirm itself. See `docs/SOURCE_PROVENANCE.md`.

Given the observations that survived the checks above:

| Situation                                                          | Status          |
| ------------------------------------------------------------------ | --------------- |
| No usable observation                                               | `MISSING`       |
| Any two usable observations disagree beyond tolerance               | `CONFLICT`      |
| One independent group, non-AI (any number of observations)          | `SINGLE_SOURCE` |
| One independent group, AI, **critical** metric                      | `PROVISIONAL`   |
| One independent group, AI, non-critical metric                      | `SINGLE_SOURCE` |
| Two or more groups agreeing, at least one non-AI                    | `VERIFIED`      |
| Two or more groups agreeing, **all AI**, critical metric            | `PROVISIONAL`   |

Worked examples:

- NSE + Yahoo agreeing → **VERIFIED** (two groups, one non-AI)
- Yahoo index endpoint + Yahoo bulk endpoint agreeing → **SINGLE_SOURCE** (one group)
- Gemini response A + Gemini response B agreeing → **PROVISIONAL** for a critical metric
- Two Google News items from the same publisher → one group, so **not** corroboration
- Yahoo + Gemini agreeing → **VERIFIED** (AI corroborated by an acceptable non-AI source)

Disagreement is checked across *all* usable observations, including within one group: two
readings of one number that do not match is a data problem regardless of whether they were
ever going to count as corroboration.

`details` records `independence_groups`, `groups_compared`, `group_values`,
`source_identities`, `values`, `min`/`max`, `difference`, `difference_pct` and the tolerances,
so an archived verdict can be re-argued without re-fetching anything.

### Tolerances

Set per metric in `core/validation.py::policy_for`:

- Price/level metrics: **0.2 %** relative - the same threshold `main.py`'s Nifty cross-check
  gate has always used.
- Percentage-point metrics (`INDEX_CHANGE_PCT`, `STOCK_CHANGE_PCT`, `SECTOR_CHANGE_PCT`):
  **0.05 absolute**. Comparing these relatively misleads - `+0.29 %` vs `+0.31 %` is a 6.5 %
  relative gap but a 0.02-point one.
- FII/DII: **1 %**, because provisional flow figures are revised between sources.

When both tolerances are configured, agreement on either is enough.

## 6. The LLM rule

> An LLM is not a source of truth for critical numerical market data.

Concretely, for `CRITICAL_METRICS` - index closes and levels, index and stock percentage
changes, sector changes, FII/DII, commodity prices, FX rates:

- An AI-only fact **can never** reach `VERIFIED`. Its ceiling is `PROVISIONAL`.
- Two agreeing AI observations are still `PROVISIONAL`. Agreement between LLM answers is not
  independent corroboration - and because every Gemini answer shares the `GEMINI`
  independence group, the group rule enforces this structurally rather than by special case.
- An AI observation **may** form part of a `VERIFIED` fact once an acceptable non-AI source
  independently agrees inside tolerance.
- Grounded Google Search does **not** make Gemini a primary source. It remains
  `AI_DISCOVERY`.

In today's pipeline this means GIFT Nifty and Brent - which only Gemini can source - are
correctly recorded as `PROVISIONAL` rather than verified, while Gemini's Nifty close is
valuable precisely because it corroborates Yahoo's.

The rule is enforced in `CrossSourceValidator`, not at the call sites, so no new caller can
forget it.

## 7. Publication gate

`MarketReport.validation_summary.publication_ready` is `False` when either:

- a **critical** fact is in `CONFLICT` - reproducing the existing safeguard in `main.py`
  that aborts the run on a >0.2 % Nifty mismatch, or
- a **required** fact (`INDEX_CLOSE`, `INDEX_CHANGE_PCT` for NIFTY 50) is absent or unusable.

An optional fact being `MISSING` never blocks. NSE blocking cloud IPs and Gemini returning
nothing is an ordinary day on GitHub Actions, not a failure.

**Phase 2 makes this verdict authoritative.** `main.check_publication` runs immediately after
the report is built and before anything is rendered: a report that is not fit to publish stops
the run, writes its JSON for diagnosis, and produces no video and no upload. Demo runs are
exempt, because synthetic data is never published anywhere.

The legacy >0.2% Nifty `RuntimeError` in `main.collect` remains as defence in depth, but the
authoritative verdict on whether sources agree is now the `INDEX_CLOSE` fact's
`CrossSourceValidator` result.

Content safety (`docs/CONTENT_SAFETY.md`) is a **separate and equally binding** gate. Data
validation and content safety both have to pass; neither can override the other.
