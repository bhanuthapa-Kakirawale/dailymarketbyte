# Publication policy: PRIVATE_ANALYTICS vs PUBLIC_UNREGISTERED

Daily Market Byte publishes **source-backed intelligence about what changed inside India's
market** - not stock tips. The Radar's stock-level analytics are kept, whole, for private use.
What the public sees is decided by a structural gate, not by a disclaimer.

```
MARKET DATA
    |
    +---- PRIVATE_ANALYTICS    named stocks, Radar events, unusual volume per stock, rankings,
    |                          charts, watchlists (never uploaded)
    |
    +---- PUBLIC_UNREGISTERED  market / index / sector facts, market-wide aggregates over a
                               NAMED universe, official exchange / company / IPO facts -
                               each with a visible SOURCE and DATA AS OF
```

This is an engineering control written from a conservative reading of what an
**unregistered** publisher (no SEBI Research Analyst registration) should avoid. It is not a
legal opinion; see "Open questions" below.

## Profiles (`publication/profile.py`)

| Profile | Use | Uploadable |
|---|---|---|
| `PUBLIC_UNREGISTERED` | **default** for every renderer that can reach the public (legacy POST, unified POST, PRE) | yes, with a PASS audit |
| `PRIVATE_ANALYTICS` | research / review renders; must be asked for by name (`--profile PRIVATE_ANALYTICS`, `profile=...`, env `PUBLICATION_PROFILE`) | never |
| `PUBLIC_RA_REGISTERED` | reserved name only - selecting it raises `ProfileNotAvailable` | - |

An omitted argument can never widen what a viewer sees.

## Classification (`publication/classification.py`, `publication/classify.py`)

Every candidate statement is a `PublishableFact` carrying: `scope` (MARKET / INDEX / SECTOR /
SECURITY / IPO), `origin` (OFFICIAL_EXCHANGE / OFFICIAL_REGULATOR / OFFICIAL_COMPANY /
MARKET_DATA / INTERNAL_ANALYTICS / NEWS / AI), `content_class` (MARKET_AGGREGATE /
EXCHANGE_EVENT / CORPORATE_EVENT / IPO_EVENT / FINANCIAL_STATISTIC / TECHNICAL_ANALYSIS /
OPINION / RECOMMENDATION), `orientation` (HISTORICAL / CURRENT_FACT / SCHEDULED_EVENT /
FORWARD_LOOKING), `source_name`, `source_label` (what the viewer sees), `source_reference`,
`data_as_of`, `retrieved_at`, `universe`, `publication_rights_status`,
`official_document_reference`, `verification_status`, `security`, `ranking`, `tags`.

Classification happens where the content is built (the planner / storyboard) - never by parsing
rendered text afterwards (the same rule as provenance).

## The PUBLIC_UNREGISTERED rules (`publication/policy.py`)

Deterministic, ordered, each block carries a reason code:

| Code | Rule |
|---|---|
| `AI_NOT_A_SOURCE` | Gemini may choose/shorten approved candidates - it is never the source of a published fact |
| `NEWS_NOT_A_SOURCE` | a third-party headline is discovery, not a published fact |
| `RECOMMENDATION`, `OPINION` | never published |
| `FORWARD_LOOKING` | forecasts of any scope (a scheduled event is `SCHEDULED_EVENT`, not a forecast) |
| `SECURITY_TECHNICAL_ANALYSIS` | technical analysis of a named security - **removing the price does not make it safe** |
| `SECURITY_RANKING` | named securities in an order (top gainer/loser, stock watch) |
| `SECURITY_WITHOUT_OFFICIAL_EVENT` | a named security needs an OFFICIAL exchange/regulator/company event |
| `OFFICIAL_REFERENCE_MISSING` | an official fact must carry its reference |
| `IPO_UNOFFICIAL_SOURCE`, `IPO_CONTENT_CLASS` | IPO facts come from SEBI offer documents / the exchange |
| `GMP` | grey-market premium is blocked outright |
| `UNIVERSE_MISSING` | a count over stocks must name its exact universe |
| `DATA_AS_OF_MISSING` | the viewer must be able to see what the fact represents |
| `RIGHTS_*` | see the rights registry |
| `LANGUAGE_*` | the fact's own text fails the public language scan |

There is **no disclaimer parameter**: a disclaimer cannot turn a BLOCK into an ALLOW (tested).

### What that removes from the public Short

- Market Radar stock stories (POST) and the PRE stock watch - replaced by **UNDER THE SURFACE**
  (docs/MARKET_STRUCTURE.md). A Radar-selected stock can still count anonymously there.
- Top gainers / losers (POST movers scene, legacy GAINERS/LOSERS, the ticker, the title and
  the description).
- Single-stock hook openings (legacy `hook-mover`, the Dynamic Hook's Radar/stock facts - removed
  from the hook fact sheet BEFORE candidates are built, so no blocked fact reaches Gemini).
- Stock-level history insights (MOVER_RECURRENCE, RELATIVE_VOLUME) in the legacy CONTEXT scene.
- News / Gemini "events" (legacy WATCH NEXT); the rule-derived F&O expiry stays.
- AI-only figures (e.g. Gemini-only FII/DII or GIFT): the section is dropped.
- GIFT Nifty unless `operations.gift_policy` explicitly allows it (defence in depth in PRE).

### How a named security may appear publicly

Only through an **official event**: `exchange_watch.OfficialEvidenceResolver` looks a symbol up
in validated official lists; with an event, the public card describes THAT event in the
exchange's terms (never "Radar", "breakout", "unusual volume"); without one, it stays private.
The audit records why each named security was allowed (`why_each_named_security_is_allowed`).

## Rights registry (`publication/rights.py`)

Technical accessibility is not publication permission.

| Status | Meaning | Sources |
|---|---|---|
| `APPROVED` | our own content / derived aggregates / hand-entered official schedules | `daily_byte_derived`, `daily_byte_market_structure`, `expiry_calendar_rule`, `rbi_press_release`, `federal_reserve_calendar` |
| `REVIEW_REQUIRED` | redistribution terms not reviewed | NSE (website, archive files, F&O ban, surveillance, IPO), Yahoo, SEBI offer documents |
| `RESTRICTED` | never published | Gemini, Google News headlines, NSE IX (GIFT) while the rights review is OPEN, synthetic fixtures |
| `UNKNOWN` | not in the registry - blocked | - |

`REVIEW_REQUIRED` follows the configured policy `PUBLIC_REVIEW_REQUIRED_POLICY`:
`ATTRIBUTED_EOD` (default) publishes end-of-day / official-notice facts with the source visibly
attributed and lists every such source in the audit (`rights_review_required`); readings
tagged `LIVE` (real-time exchange feeds) are not covered. `BLOCK` publishes nothing from such a
source. An unknown value fails closed (BLOCK). Nothing is ever silently upgraded to APPROVED.

## Language scan (`publication/language.py`)

Deterministic word lists (no model), each reported separately in the audit:
recommendation language (buy/sell/hold, target, stop-loss, entry/exit, accumulate, stocks to
buy/watch, conviction, model portfolio, bullish/bearish ...), IPO recommendation language
(apply/avoid, subscribe to the IPO, good/best IPO, listing gain/pop, fair value, cheap,
expensive, GMP/grey market ...), ranking language (top stocks/gainers/losers, best stocks),
forecast language (will rise, likely to, expected to, outlook, forecast, upside ...),
security-specific technical analysis (a named security in the same sentence as a technical term -
even an approved one), unapproved named securities, and English only (no non-English
characters, no Hindi/Hinglish words). Index/sector technical context ("Nifty closed below its
20-day low") is allowed.

The registered disclaimer (`publication/disclaimer.py`) names the words it disclaims; that exact
string - and only it, verbatim - is exempt from the scan. The legacy FLOWS tags therefore read
`NET BUYERS` / `NET SELLERS` (as in PRE), never `NET BUY` / `NET SELL`.

## Visible provenance

Every factual scene shows a SOURCE / DATA AS OF plate (`daily_video/provenance_bar.py`,
`video.provenance_layer` for the legacy renderer): bold 26 px, above the disclaimer, left of the
Shorts action rail, on the scene's own layer (no flashing, visible for the whole scene).
DATA AS OF is the market/event time the fact represents; FETCHED appears for live readings.
Plain text only - no exchange logo, nothing implying endorsement. Model:
`presentation/provenance_label.py`.

## Publication audit (`publication/audit.py`) and the upload hard-block

Every public render writes `publication_audit.json`: profile, session, facts considered /
allowed / blocked + block reasons, named securities and why each was allowed, Market Structure
universe/coverage/sector mapping, sources, references, data_as_of, retrieved_at, rights status,
Exchange Watch, IPO (content present, companies, sources, subscription as-of, official facts,
`gmp_present`), Gemini (candidate set, final selected claims), the scans and the final verdict.

`final = PASS` only when the profile is PUBLIC_UNREGISTERED, every language scan passes, every
factual scene shows SOURCE and DATA AS OF, every Market Structure scene shows its universe and
denominator, and `gmp_present` is false.

`upload.upload(video, meta, audit)` requires the audit and calls
`require_publication_pass(audit, video)` **before any YouTube client or credential is touched**:
it refuses a missing audit, a BLOCK, a non-public profile, a synthetic render, or an audit whose
recorded sha256 is not this exact file. The manual CLI needs the audit path too. In `main.run`
the audit is a fifth gate (after data, content, video and readability QA) and its verdict is
recorded in the run's `details.publication`.

## Where it runs

| Path | Gate | Provenance | Audit |
|---|---|---|---|
| scheduled POST (`main.run`, legacy `video.py`) | `editorial.plan_short(profile=...)` + `presentation/legacy_public.py` (ticker, title, description) | `video.render(provenance=...)` | `output/publication/<date>/publication_audit.json`, gate 5, upload hard-block |
| unified POST (`render_daily_market_byte.py`) | `daily_video.build_storyboard(profile=..., intelligence=...)` | provenance bar | `--out-dir/publication_audit.json` |
| PRE (`products.premarket.render_pre`) | `presentation/pre_public.apply_publication_profile` before planning | provenance bar | `<run dir>/publication_audit.json`; a public BLOCK blocks the render |

The legacy scheduled POST gets the gate, provenance and audit, but not the new UNDER THE
SURFACE / EXCHANGE / IPO scenes - those live in the unified renderer and arrive with the POST
cut-over (docs/PRODUCTION_SCHEDULE.md).

## Open questions (not resolved by this code)

- Whether any particular public statement is "research" under SEBI (Research Analysts)
  Regulations is a legal question; this policy is deliberately conservative, not a ruling.
- Redistribution terms for NSE / NSE Indices / Yahoo data are **not reviewed**
  (`REVIEW_REQUIRED`); NSE IX stays RESTRICTED (docs/NSEIX_RIGHTS_REVIEW.md).
- The NSE website APIs used for surveillance and IPO lists are undocumented and can change.
