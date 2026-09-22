# Migration plan

## Why strangler, not rewrite

Daily Market Byte is a working application that publishes on a schedule. The canonical
architecture is being grown *around* it rather than swapped in, so that at no point does a
day's publication depend on code written that week.

The legacy modules - `market.py`, `news.py`, `chart.py`, `video.py`, `music.py`, `upload.py` -
remain the production path. They are deliberately left intact, including their quirks. New
structure is added at the edges and takes over responsibilities one at a time.

## Phase 1 (complete): canonical models and the report artifact

Added `core/` (models, enums, validation, report) and `adapters/` (legacy dicts to canonical
Observations). `main.py` gained exactly one wrapped call.

The canonical layer runs as a **tee**: it observes data the pipeline already collected and
writes `output/reports/premarket_YYYY-MM-DD.json`. The renderer still consumes the original
dicts, so video output is equivalent by construction. `build_and_save_report` swallows all
exceptions - new code must not be able to cost a publication.

What this bought:

- Provenance that the legacy dicts destroyed is reconstructed and recorded. Global tiles now
  distinguish Yahoo from Gemini; sector rows distinguish NSE from Yahoo.
- `main.py`'s Nifty cross-check, previously printed and discarded, is now a stored
  `ValidationResult` with both compared values.
- AI-sourced numbers are explicitly typed and cannot be marked verified on their own.
- A dated, serialisable historical record starts accumulating immediately.

### Known compromises

- **The report does not gate publication.** It records `publication_ready`; the legacy
  safeguards still enforce. Deliberate - enforcing would change production behaviour.
- **Provenance for events is unresolved.** `news.ai_pass` returns events without recording
  whether Gemini or the Google News fallback produced them, so events are marked
  `provenance_resolved: false` unless they came from the F&O expiry rule.
- **Catalyst attribution is reconstructed, not recorded.** `classify_catalyst` replays
  `ai_pass`'s own logic to work out whether a reason came from a headline, Gemini, or the
  no-catalyst sentinel, and flags every result `inferred: true`. It is coupled to a literal
  string in `news.py`, guarded by a test.
- **`observed_at` is almost always `None`.** Providers either do not supply it or the legacy
  functions discard it. `nse_all_indices` parses NSE's timestamp and throws it away.
- **Relative volume uses a 10-session lookback**, not the 20 the product spec describes. The
  definition is now recorded in observation metadata rather than silently implied.

## Phase 1.1 (complete): content safety

`core/content_safety.py` - deterministic recommendation-language filter, applied before the
report is built and re-scanned before upload. See `docs/CONTENT_SAFETY.md`.

## Phase 2 (complete): the report becomes authoritative

The Phase 1 tee is gone. The report is now built *before* rendering, decides whether
publication may proceed, and feeds the renderer through a presentation boundary.

What changed:

- **`providers/`** wraps the existing `market.py` / `news.py` fetching. Above that line
  everything is an Observation or a typed payload, never a raw provider dict.
- **`core/sources.py`** models `SourceMetadata` and, separately, `independence_group`.
- **`CrossSourceValidator`** counts independence groups rather than observations, so one
  source can no longer appear to confirm itself.
- **Provenance is recorded at acquisition.** `news.ai_pass` stamps every reason and event
  with the branch that produced it; `nse_all_indices` keeps NSE's market timestamp, which now
  reaches `Observation.observed_at`. The Phase 1 string-matching reconstruction is deleted.
- **`presentation/report_adapter.py`** converts the report into the structures `video.py` and
  `chart.py` already accept. Neither renderer module was changed.
- **`main.check_publication`** makes `publication_ready` binding: an unfit report writes its
  JSON for diagnosis and renders nothing.
- **Schema 2.0** adds `sources`, `independence_group`, `source_metadata` and
  `technicals.candles`.

### Phase 2 compromises

- **The legacy Nifty `RuntimeError` remains** in `collect()` as defence in depth, so that
  check now exists in two places. The report's `CONFLICT` verdict is the authoritative one;
  the older guard fires first and is redundant but harmless.
- **News independence is approximated by publisher.** One story syndicated across genuinely
  different publishers still counts as two groups.
- **`display_rights_status` is a placeholder** (`UNREVIEWED` for every external source). Phase
  2 models provenance, not licensing - a full rights system is explicitly out of scope.
- **`main.collect()` still holds loose dictionaries** briefly between the legacy fetchers and
  the providers. The providers consume them immediately, but the seam is visible.
- **Relative volume still uses a 10-session lookback**, not the 20 the product spec describes.

## Phase 3+ (explicitly deferred)

Fact database, source-licence registry, instrument master, catalyst time-matching, relevance
scoring for stock selection, post-market edition, adaptive video duration.

These are deferred on the product spec's own reasoning: the YouTube channel is an unvalidated
experiment, and this infrastructure would multiply the codebase without changing a single
frame. Phases 1 and 2 exist so that building them later does not mean rebuilding the
foundation.

## Rules for contributors

- Do not rewrite a legacy module to make new code prettier. Adapt at the boundary.
- Any architecture-affecting change updates these docs in the same commit.
- New provenance must be honest. An unavailable source is `None`, never a plausible guess.
- Tests must run offline. No test may touch NSE, Yahoo, Google, Gemini or YouTube.
