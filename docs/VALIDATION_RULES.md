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

## 5. Cross-source verification

Given the observations that survived the checks above:

| Situation                                                     | Status          |
| ------------------------------------------------------------- | --------------- |
| No usable observation                                          | `MISSING`       |
| One non-AI source                                              | `SINGLE_SOURCE` |
| One AI source, **critical** metric                             | `PROVISIONAL`   |
| One AI source, non-critical metric                             | `SINGLE_SOURCE` |
| Two or more sources, disagreeing beyond tolerance              | `CONFLICT`      |
| Two or more sources agreeing, at least one non-AI              | `VERIFIED`      |
| Two or more sources agreeing, **all AI**, critical metric      | `PROVISIONAL`   |

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
  independent corroboration.
- An AI observation **may** form part of a `VERIFIED` fact once an acceptable non-AI source
  independently agrees inside tolerance.

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

**Phase 1 records this verdict; it does not enforce it.** The legacy safeguards in `main.py`
still do the actual blocking and run earlier. Making `publication_ready` authoritative is a
Phase 2 decision, because it changes production behaviour.
