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

## Phase 2 candidates (not started - awaiting approval)

Ordered by value per unit of risk:

1. **Compliance linter.** Block render/upload if any caption, title or description contains
   recommendation language (BUY/SELL/TARGET/MULTIBAGGER). Currently only a disclaimer exists,
   with no enforcement. Cheapest meaningful risk reduction available.
2. **Post-render QA gate.** Verify duration, resolution, audio track and non-blank frames
   before upload.
3. **Record provenance at the source.** Have `news.ai_pass` return which path produced each
   reason and event, and `nse_all_indices` return NSE's timestamp. Removes the two inference
   hacks above and populates `observed_at`.
4. **Make `publication_ready` authoritative**, replacing the ad-hoc `RuntimeError` in
   `main.py` with the validation gate.
5. **Provider abstraction.** Only worthwhile once a second provider actually exists.

## Phase 3+ (explicitly deferred)

Fact database, source-licence registry, instrument master, catalyst time-matching, relevance
scoring for stock selection, post-market edition, adaptive video duration.

These are deferred on the product spec's own reasoning: the YouTube channel is an unvalidated
experiment, and this infrastructure would multiply the codebase without changing a single
frame. Phase 1 exists so that building them later does not mean rebuilding the foundation.

## Rules for contributors

- Do not rewrite a legacy module to make new code prettier. Adapt at the boundary.
- Any architecture-affecting change updates these docs in the same commit.
- New provenance must be honest. An unavailable source is `None`, never a plausible guess.
- Tests must run offline. No test may touch NSE, Yahoo, Google, Gemini or YouTube.
