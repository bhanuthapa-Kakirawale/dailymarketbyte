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
