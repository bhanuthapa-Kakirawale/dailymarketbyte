# Shorts editorial layer

## The canonical report is not the video script

```
MarketReport + IntelligenceSnapshot
            |
            v
   Editorial selection  (editorial/)
            |
            v
      ShortsPlan        - what is said, in what order, for how long
            |
            v
        Scenes          - video.scenes_from_plan()
            |
            v
       Renderer
```

The report holds everything validated. The plan decides which small part of it earns screen
time. A fact being valid does not entitle it to a place in a sixty-second video, and the
Short was previously failing precisely because the pipeline tried to expose the whole report.

The plan is **derived presentation state**: reproducible from the report and the snapshot,
recorded in the QA artifact, never persisted as canonical fact and never written back into
either input.

## One scene, one primary message

Each `ScenePlan` has exactly one `primary_value` and one `primary_text`. Everything else is
secondary by construction, so density has to be a deliberate choice rather than a
side-effect of having data available.

```
NIFTY 50            -1.25%
Closed below its 20-day average
```

not

```
Nifty -0.36% | 20 DMA | 50 DMA | Pivot | R1 | S1 | RSI | Volume
```

## The hook

> The unified `daily_video` renderer now opens with the **Dynamic Hook Engine**
> (`hooks/`, see `docs/HOOK_ENGINE.md`): 2-3 teaser beats and a curiosity + summary hook,
> where Gemini may choose among deterministic, pre-approved candidates. The rule-based
> selection below is still what the legacy `video.py` path (and the `ShortsPlan` HOOK scene)
> uses.

The first three seconds answer *why keep watching?*, not *what is this channel called?*.
Branding is still present but small and below the fact.

Selection is deterministic and rule-based — **no LLM**. Candidates score themselves from
canonical facts or intelligence insights, sort by `(-score, family order, id)`, and the first
one that passes content safety wins:

| # | Candidate | Fires when |
| --- | --- | --- |
| 1 | unusual index move | intelligence says the move beat ≥15 of the last 20 sessions |
| 2 | VIX extreme | ranked ≥17 or ≤3 of the previous 20 readings |
| 3 | FII/DII streak | a run of ≥3 recorded sessions |
| 4 | large flow | \|FII\| ≥ ₹2,500 cr |
| 5 | large index move | \|move\| ≥ 1.0% |
| 6 | sector move | \|move\| ≥ 2.0% |
| 7 | big mover | \|move\| ≥ 5.0% |
| 8 | other strong insight | `selection_score` ≥ 0.75 |
| — | **fallback** | the Nifty close, stated plainly |

Thresholds are deliberately high so an ordinary day falls through to the fallback rather than
being dressed up. The score comes from how unusual the reading already was, so the hook is
the most *informative* fact available, never the most dramatic phrasing of a dull one.

**Safety is never bypassed.** Every candidate's text goes through the Phase 1.1 scan; a
failing candidate is skipped in favour of the next, and the fallback is itself checked. No
hook is ever rewritten to make it pass.

## Readability budgets

All in `editorial/config.py`, not scattered as magic numbers.

| | |
| --- | --- |
| `MAX_PRIMARY_WORDS` / hard | 8 / 11 |
| `MAX_SECONDARY_WORDS` / hard | 12 / 16 |
| `MAX_MAJOR_CARDS` | 3 |
| `MAX_GLOBAL_CUES` | 3 |
| `MAX_SECTORS_HIGHLIGHTED` | 3 |
| `MAX_MOVERS_DISPLAYED` | 3 |
| `MAX_CONTEXT_INSIGHTS` | 2 |
| `MAX_EVENTS` | 2 |

## Reading time and duration

```
estimated_read_seconds =
    1.2s base visual processing
  + reading_words / 2.6 words-per-second
  + 0.6s per card
  + 1.2s if a chart is present
  + 0.35s per large number

planned_duration = clamp(estimated_read_seconds, scene minimum, scene maximum)
```

**Numbers are glanced, not read.** A formatted value and a short chip are one fixation each,
already priced by the per-value term, so `reading_words` excludes them. Counting them as
prose as well priced a two-row FII/DII card at nine seconds and made the planner delete its
own content.

The estimate is approximate on purpose. Its job is only to stop "35 words + 5 cards + a
chart" ever being paired with a four-second scene.

When a scene cannot be read even at its maximum duration the planner **drops rows** rather
than speeding the viewer up — editorial omission, not shrinking text. `MIN_ITEMS` keeps a
floor so a scene is never emptied.

**Trimming must not cost a scene its point.** `trim_to_fit` drops from the end, so every
scene orders its rows to put the contrast first and the optional extra last:

| Scene | Order |
| --- | --- |
| Sectors | strongest, weakest, runner-up |
| Movers | best gainer, worst loser, runner-up |

Both were bugs before they were rules. Ranking sectors purely by move dropped the weakest —
the very thing the scene exists to contrast. Ranking movers purely by size of move let a
strong session fill "Biggest moves" with three gainers and no loser at all, turning a recap
into a leaderboard.

## Total duration

Content-driven, not fixed. The old pipeline always produced exactly 75.0s because
`sum(BASE_DUR)` was a constant; that constraint is gone.

- **Target**: 45–60s
- **Guardrails** (QA): 15–70s

A sparse session produces a shorter video. Nothing is padded to reach a number, and nothing
is rushed to stay under one.

## Omission policy

The Short may leave out extra sectors, extra movers, secondary global cues, technical levels,
less important events and redundant intelligence. Every omission is recorded in
`plan.omitted` with its reason, so a choice is visible in the artifact rather than silently
lost. **Nothing is removed from the MarketReport or from history.**

## Captions and motion

- **No caption cycling.** `caption_changes()` is 0 by design; scenes show one stable
  takeaway. The old Nifty scene rotated up to six analytical sentences and the movers scene
  six more, while the viewer was also reading cards.
- **Ticker hides on reading-heavy scenes.** `show_ticker` is per-scene; only the hook, global
  cues and outro keep it. A scrolling strip competing with a number someone is trying to take
  in is the cheapest distraction to remove.

## Scene-by-scene

| Scene | Before | Now |
| --- | --- | --- |
| Opening | branding title card, 2.0s | **hook**: one dominant fact, branding left to the header |
| Global | 6 tiles in a 2×3 grid | up to 3 cues, by relevance order |
| Nifty | value + change + 4 chips + chart + 6 cycling captions | one number, one observation, chart |
| FII/DII | two large cards + bars + combined-net line | two rows, optional streak line |
| Sectors | 12-cell heatmap | strongest + weakest (narrative) below `MIN_SECTORS_FOR_HEATMAP`, a scan-oriented heatmap grid (up to `MAX_HEATMAP_CARDS`) at or above it |
| Movers | two scenes × 5 rows, 6 cycling captions | two ranked scan scenes - **Top 5 Gainers** then **Top 5 Losers** - each up to `MAX_RANKED_MOVERS` rows, rank + symbol + signed % only |
| Context | up to 3 full statements | up to 2, rendered short-form |
| Events | up to 5 cards | up to 2 |
| Outro | 3.0s, 4 text elements | ≤3.0s, CTA drawn from the plan |

### Context short form

Intelligence statements are written for the artifact — *"Over the last 20 available sessions
DIIs were net buyers in 20 of them, with a cumulative net flow of Rs 5,195 crore"* is precise
and unreadable on a phone. `editorial.context_line()` composes the same numbers from the
insight's **structured metadata** into a glanceable line (*"Net buyers, 20 recorded
sessions"*). Nothing is invented and no intelligence calculation changes; only the wording is
chosen for the screen.

### A rank is only worth saying at the ends

"Bigger move than 0 of the last 20 sessions" is accurate, carries no information, and on
screen reads like a failed lookup. A mid-pack rank is no better. `_rank_line` therefore
states the rank only when it sits within `RANK_NOTABLE_FRACTION` (0.75) of either end, and
says each end in its own direction — *"Bigger move than 18 of 20 sessions"* or *"Smaller move
than 20 of 20 sessions"*. Everything between returns `""`, and the caller falls through to a
line that does carry information (the 20-day average, or the VIX). An insight whose short
form comes back empty leaves the context scene rather than rendering a blank row; it stays in
the snapshot untouched.

### Catalysts are not causes

A mover's catalyst is shown as something reported alongside the move, clipped to five words
on a word boundary. The Short never says a stock rose *because* of something.

## Traceability

Every scene carries `source_fact_ids` and `source_insight_ids`, and every displayed number is
in `ScenePlan.numeric_values()`. A test asserts every one of them exists in the canonical
report — no hard-coded market numbers can reach the screen.

Context-scene figures are traceable through `source_insight_ids` instead: they are
intelligence-derived counts, not raw report facts.

## Content safety

`plan.public_text()` is the complete, authoritative list of on-screen strings — including the
hook and every context line — and it is what the final scan reads. Taking it from the plan
rather than introspecting rendered scenes means nothing can reach the screen without being
scanned.

That only holds if no scene draws a string of its own, and twice it did not: the outro drew
a hardcoded "DAILY MARKET BYTE" / "New recap every trading day", and the hook drew a second
wordmark under the fact. Both were invisible to the content scan *and* to the reading budget.
The outro now takes its CTA from the plan; the hook leaves branding to the header, which
`render()` already draws on every frame. A test walks each scene's `draw()` with a recording
surface and asserts every string it paints was declared by the plan.

## Two reading modes: narrative and scan

Most scenes are **narrative**: read card by card, one fixation at a time, so a hard cap on
how many items compete at once (`MAX_MAJOR_CARDS`) is the right rule. The sector heatmap and
the two ranked-mover scenes (Top 5 Gainers / Top 5 Losers) are **scan** presentations instead
- a name/symbol and a signed percentage register in a glance down a grid or column, not by
being read in sequence - so both the reading-time estimate (`estimate_heatmap_scene`,
`estimate_ranked_movers_scene`) and readability QA's card-count gate price them on their own,
much higher, scale (`MAX_HEATMAP_CARDS`, `MAX_RANKED_MOVERS`) rather than stretching the
narrative one. A scene declares which mode it is in via `ScenePlan.metadata["presentation"]`
(`"HEATMAP"` or `"SCAN"`); every other scene is narrative by default.

## Readability QA

`qa/readability_qa.py`. Deterministic arithmetic — no computer vision, no model.

**BLOCK** (publication gate, alongside data QA, content QA and video QA):
- estimated reading time exceeds the scene duration by more than 25% (`BLOCK_READ_RATIO`)
- primary or secondary text over the hard word limit
- more than `MAX_MAJOR_CARDS` items competing at once for a narrative scene, or more than
  `MAX_HEATMAP_CARDS` / `MAX_RANKED_MOVERS` for a HEATMAP / SCAN scene
- a scene with no duration, or a duration past its type's maximum
- any caption cycling
- the Short outside the 15–70s guardrails, or with no scenes

**WARN** (never blocks):
- reading time marginally over the duration
- a soft word budget exceeded

Warnings never block, because a gate that fires on trivia is a gate someone switches off.
The result is written into the QA artifact as `readability_qa`, with the full plan under
`editorial_plan`.

## Safe zones and type

Layout still respects the existing Shorts safe zone: content between y≈340 and y≈1195,
nothing important on the far right below y≈1100, and the strip below the disclaimer left for
YouTube's own UI. Minimum on-screen sizes are declared in `config.py`
(`MIN_PRIMARY_VALUE_SIZE` and friends) and judged for a phone rather than a desktop preview.
Hierarchy does the work: the number is large, its label smaller, context smaller again.

## Cold start and missing sections

- **No history** → no context scene, hook falls back to a canonical fact, the Short is
  simply shorter.
- **No FII/DII, sectors, movers, events or global cues** → those scenes are omitted rather
  than rendered empty. A sparse session yields a shorter video, never filler.

## Determinism

The same report, snapshot and editorial configuration always produce the same plan: the same
hook, the same movers in the same order, the same durations. Tie-breaks are explicit
everywhere. No model makes an editorial judgement.

## The boundary that does not move

No predictions, no BUY/SELL/HOLD, no targets or stop losses, no recommendations, no
clickbait. The hook creates curiosity from real market information or it falls back to
stating the close plainly.

## POST-MARKET section planning (Phase 3, unified `daily_video` Short)

`presentation/post_plan.py` (`plan_post_sections` -> `PostSectionPlan`) decides which sections
the POST Short contains, deterministically and with a written reason for every section in or
out (`storyboard.post_plan`, `render_post_phase3.py` writes `post_section_plan_<date>.json`). No
model is involved - Gemini stays inside the Dynamic Hook boundary. The storyboard only lays out
the plan; the renderer only draws.

Story: HOOK -> MARKET -> SECTORS -> [optional context] -> RADAR -> CLOSE. Radar is the last
analytical section and the 2.6 s close follows it directly.

| Section | Kind | Rule |
|---|---|---|
| Market pulse | core | Nifty close + change (one primary fact) and ONE support: close near the day's high/low (top/bottom quarter of the range), else which side of the open it finished. Headline graded: almost unchanged (<0.10%), slightly (<0.60%), plain (<1.25%), sharply. No Bank Nifty/VIX tiles. |
| Nifty chart | optional | Only for a NEW structural event today: a 20-day range break, else a 20-day-average cross. A continuing state is not an event (replayed over 186 real sessions: fires on ~26%). Built from the Radar chart components with a brand edge and a card sized for a chart with no evidence strip (the pulse already carries the day's range). |
| Sectors | core | Leader dominant, laggard visible, tone line "N of M sector indices closed higher", and every sector in a ranked heat strip. On a uniform day the words follow the direction: all down -> "All N sector indices fell; X fell least" (FELL LEAST / FELL MOST), all up -> "...rose; X led" (LEADER / ROSE LEAST) - "led" is never said of a sector that fell (found on the real 24 Sep 2026 session). |
| Movers | optional | Only if a top gain/fall that is NOT a published Radar story moved >= 7%, or the top gain vs top fall spread is >= 12 pts (POST freeze; the old 4% / 6 pts fired on 90% of replayed sessions, the new rule on 24%). Only when the universe coverage gate passes (below). At most two cards. |
| FII/DII | optional | Only with validated flows of at least Rs 1,000 cr. |
| Global context | optional, rare | Renamed from "Overnight global cues" (a PRE topic). Only if a global move >= 1.5% on a day Nifty itself moved >= 1% the same way. |
| Special event | optional, rare | Only a high-impact scheduled event (RBI/FED/BUDGET/POLICY); forward-looking "watch next" items are not POST facts. |
| Radar | core | The 3 published stories (Phase 2, unchanged; no-event stories use the VOLUME / SESSION presentation below). |

At most `OPTIONAL_BUDGET` (2) optional sections per Short, in priority Nifty chart > flows >
movers > event > global; a qualifying section beyond the budget is recorded as omitted with the
reason.

Transitions (`daily_video/composer.py`): the outgoing scene stays whole until the cut, then lifts
away while the incoming scene rises in. Its headline zone clears almost at once, so two
headlines never overprint; the rest fades on an ease-in curve so the screen is never empty (the
old fade-out-then-fade-in left a blank beat at every boundary; a test now measures every cut).
The section chip slides in. A full-screen vertical push was rejected: inside a Short it looks
like the feed's own swipe to the next video.


## POST freeze: coverage gate and move guard

**Universe coverage gate** (`editorial/movers_gate.py`). A top-gainer / top-loser ranking is
only a fact about the universe it was computed over. `market.get_movers_audited` records, at
acquisition, `universe_expected` (the universe size), `universe_observed` (stocks with a clean,
date-aligned day-over-day change), `universe_validated` (those that also passed the move guard)
and `coverage_pct = validated / expected`, into `report.metadata["movers_coverage"]`, together
with the missing, date-gap and guard-held symbols. `plan_short` publishes the GAINERS / LOSERS
scenes - and therefore every Movers card and every `top_gainer` / `top_loser` hook claim built
from them - only when `coverage_pct >= MOVERS_MIN_COVERAGE_PCT` (90). Below it, or when the
report has no coverage record at all (every report built before the gate: status UNKNOWN), the
scenes are not built and `plan.omitted` records `universe_coverage` with the status, counts and
reason. Nothing is ranked over what happened to arrive; nothing is removed from the report.

**Move guard** (`core/move_guard.py`, see the module docstring for the exact rules). Acquisition
ranks only publishable moves; a held move stays in `movers_coverage.excluded` with its raw
open/high/low/close/previous close and verdict, and counts against coverage (a move that cannot
be trusted is not an observation of the universe). The same guard runs on Market Radar stories
before publication (`presentation/radar_guard.py`).

Replay evidence (124 Nifty 100 sessions with local stock data, 24 Mar - 24 Sep 2026): Movers
inclusion 90.3% -> 24.2%; the guard flagged 9 records (5 volume-confirmed extremes published,
4 held - VEDL -64.9% on 30 Apr, and three >= 10% moves whose volume history was too short to
confirm); the coverage gate suppressed 6 sessions (24 Sep at 81%, and the five sessions right
after a date on which Yahoo carries stock rows but no Nifty row, where every stock's previous
close is misaligned).

## Radar stories without a technical event (POST final edge-case patch)

Verified 24 Sep 2026: the selector published COROMANDEL on UNUSUAL volume (3.0x) plus relative
performance, with no STRUCTURE event, on a -0.69% day. The Phase 2 renderer had no path for it.
It drew an empty chart card reading "Something changed on the chart with unusually high volume."
`presentation.radar_story` now gives such a story one of two families. The shell, the identity
row, the price tag column and the takeaway line are the same as every other Radar story:

- **VOLUME**: the Radar volume detector flagged the session. The upper panel holds the candles as
  neutral price context: no ring, no callout, no reference level, and they step back when the
  evidence arrives. The lower, larger panel is the volume histogram. It carries the detector's
  own multiple ("3.0× normal volume"), the session's bar in amber, and a dashed line at the prior
  20-session average ("20-DAY AVG"). The line spans only the 20 sessions it averages. It is drawn
  only when the visible window reproduces the detector's multiple within 2%; otherwise there is
  no line and a warning is recorded. Takeaway (fixed template): "Trading volume was
  <above normal | unusually high | exceptionally high> while price finished <x>% <higher | lower>."
- **SESSION**: neither an event nor flagged volume. Neutral candles plus the day-range strip.
  Takeaway: "Price finished <x>% lower/higher [and closed near the day's high/low]."

Rules: one main idea; one supporting fact; one price (the close); no claim that anything
happened "on the chart"; no invented breakout, range or average event. The no-chart TEXT card
never reuses the Radar planner's `context_line`, because it carries internal labels.

**Direction.** A story's internal `direction` (`ALIGNED_POSITIVE` / `ALIGNED_NEGATIVE` /
`MIXED`, from Radar's composite) and its derived `direction_label` ("Positive Alignment") and
`editorial_selection_reason` ("... positive-direction development") describe multi-session
STRUCTURE / RELATIVE_PERFORMANCE agreement. They are internal only, used by the selector's
diversity slots, and never appear on screen. Every viewer-visible direction (change badge
colour, close-tag colour, "higher"/"lower") comes from the story's validated session move
(`price_change_pct`). COROMANDEL was `ALIGNED_POSITIVE`, and it is shown red with "0.7% lower".
Normal event stories are pixel-identical to Phase 2 (frame hashes compared before and after).


## Public profile V2 (PUBLIC_UNREGISTERED, public intelligence V1)

The publication profile (docs/PUBLICATION_POLICY.md) is applied BEFORE the storyboard, so the
editorial rules above run only over admitted facts.

**Unified POST (`daily_video.build_storyboard`, default profile PUBLIC_UNREGISTERED):**

    DYNAMIC HOOK -> MARKET PULSE -> SECTOR STORY -> [optional context] -> EXCHANGE WATCH (opt.)
                 -> IPO WATCH (opt.) -> UNDER THE SURFACE (0-2 scenes) -> CLOSING

- Market Radar stock stories are private: each is classified `SECURITY / INTERNAL_ANALYTICS /
  TECHNICAL_ANALYSIS` and refused; `omitted` records "publication profile ... stays PRIVATE".
  The Radar closing line goes with them.
- MOVERS is a security ranking and is refused publicly (`post_plan.reasons["MOVERS"]`).
- Every remaining section is admitted fact-by-fact (an AI-only section is dropped) and carries
  its SOURCE / DATA AS OF plate.
- UNDER THE SURFACE, EXCHANGE WATCH and IPO WATCH come from `PublicIntelligence`
  (docs/MARKET_STRUCTURE.md, EXCHANGE_WATCH.md, IPO_WATCH.md). Runtime ceiling 62 s: optional
  sections are trimmed first (global, event, movers, flows, Nifty chart, IPO, exchange), then a
  second structure scene - never a core section, never padded.
- The Dynamic Hook's fact sheet is restricted by the same gate before candidates are built
  (`publication.public_hooks.restrict_sheet`), and the Market Structure count is offered instead
  ("Nifty moved just +0.12%. 18 NIFTY 200 stocks saw unusual volume.").

**Legacy scheduled POST (`editorial.plan_short(profile=...)`):** no GAINERS/LOSERS scenes, no
single-stock hook (`hook-mover`), no stock-level CONTEXT insight, no news/Gemini WATCH NEXT
event (the F&O expiry rule stays); the ticker carries no stock; title/description/tags from
`presentation/legacy_public.public_metadata` (no stock names, no "Top gainers"); FLOWS tags
`NET BUYERS` / `NET SELLERS`.

`PRIVATE_ANALYTICS` renders exactly what this document describes above (Radar stories, movers,
Radar hook archetypes) for research, and is never uploaded.
