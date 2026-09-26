# Dynamic Hook Engine (Phase 1)

The first 4-5 seconds of every Daily Market Byte Short. They have to answer three questions
before the viewer scrolls: *why stop?*, *what interesting thing happened?*, and *what will I
get if I keep watching?*

```
validated inputs (PlanScene/ReportPresentation/Radar | PreMarketInputs | CustomStockInputs)
        │  hooks/sheet_post.py · sheet_pre.py · sheet_custom.py
        ▼
HookFactSheet  = HookFacts (formatted, with licensed words) + TeaserBeatOptions (validated visuals)
        │  hooks/candidates.py   (deterministic HookCandidateBuilder)
        ▼
1-5 approved HookCandidates, each with a self-validated template hook
        │  hooks/gemini.py       (optional: one structured, un-grounded Gemini call)
        ▼
Gemini JSON ── hooks/validation.py ── pass ─────────────► HookPlan(source=GEMINI)
        │                          └ choice ok, lines bad ► HookPlan(GEMINI_CHOICE_TEMPLATE_TEXT)
        └ no key / error / 429 / not JSON / bad choice ───► HookPlan(DETERMINISTIC)  (top candidate)
        │  daily_video/storyboard.dynamic_hook_spec
        ▼
DYNAMIC_HOOK scene (daily_video/hook_scene.py): 2-3 teaser beats → settled hook
```

The video never fails because Gemini failed. Every path ends in a `HookPlan`, and the plan
records which path it took and why (`source`, `fallback_reason`, `validation_issues`, the raw
Gemini reply). The render script writes it to `hook_plan_<session>.json`.

## Hook structure (hybrid)

| Line | Job | Limit |
|---|---|---|
| eyebrow | archetype family, fixed per archetype (never model-written) | - |
| **curiosity line** | the strongest validated fact or contrast | 64 chars / 11 words |
| **summary line** | what the Short will show, drawn only from sections it really has | 84 chars / 14 words |

Frame layout, shared by every archetype: eyebrow (y≈290), curiosity line (y≈332, 2 lines,
numbers coloured by sign, second sentence in the brand colour), stage (y 514-1168: beats,
then the hero), an `INSIDE` / `BEFORE THE BELL` chip over the summary list, and agenda chips.
Everything else - header, date, disclaimer, background - is the video's normal chrome, so every
hook is unmistakably Daily Market Byte.

## Archetypes

| Archetype | Story shape | Modes | Trigger (deterministic) | Heroes |
|---|---|---|---|---|
| `QUIET_MARKET_HIDDEN_ACTION` | small headline number, big moves underneath | POST, CUSTOM | \|Nifty\| < 0.40% and ≥2 flagged stocks moved ≥1% and ≥3× Nifty (CUSTOM: \|move\| < 1% with an average cross) | `DEPTH_LOLLIPOP`, `SIGNAL_STACK_CHART` |
| `BIG_MOVE` | one number is the story | POST, PRE, CUSTOM | \|Nifty\| ≥ 1% (stock ≥ 5%); boosted by a 20-session rank | `HEADLINE_NUMBER` |
| `CONTRAST` | two things went opposite ways | POST, CUSTOM | sector leader > 0 > laggard, spread ≥ 1.5 pp; or FIIs vs DIIs opposite, each ≥ Rs 1,000 cr; or weak-chart event + a strong fundamental | `VERSUS_SPLIT` |
| `UNUSUAL_ACTIVITY` | several signals stacked on one name | POST, CUSTOM | Radar story with ≥3 independent signals, or ≥2× volume and ≥2.5% move; CUSTOM: range break on ≥2× volume | `SIGNAL_STACK_CHART`, `DEPTH_LOLLIPOP` |
| `OVERNIGHT_CUE` | the world moved while India slept | PRE | largest global cue ≥ 1%; GIFT Nifty added when validated | `OVERNIGHT_BOARD`, `HEADLINE_NUMBER` |
| `EVENT_LED` | a dated, named event | PRE (POST if high-impact) | an event tagged RBI/FED/BUDGET/POLICY/GDP/CPI/RESULTS/F&O or `impact=HIGH` | `EVENT_CALENDAR` |
| `THINGS_TO_KNOW` | numbered briefing | all | always available at the lowest score, so there is always a candidate | `NUMBERED_LIST` |

Scores rise only with how unusual the underlying reading is. Ordering is total (score →
archetype order → id). Up to 5 candidates are offered.

**Why these seven.** The brief's `SECTOR_BATTLE` became `CONTRAST` because the same story
shape covers sector vs sector, FIIs vs DIIs and chart vs fundamentals (the CUSTOM example). A
separate `PREMARKET_THINGS_TO_KNOW` became the mode-agnostic `THINGS_TO_KNOW`, which is also
the universal fallback. `OVERNIGHT_CUE` was added because "overnight cue" and "event" are
different shapes with different heroes.

## Visual language (Phase 1A: editorial collage)

Phase 1A redesigned only the rendering (`daily_video/hook_scene.py`, `daily_video/hook_kit.py`);
candidates, Gemini, validation, fallback and timing are unchanged.

- **Beats accumulate instead of cutting.** Beat 1 is a full-stage base card (dark panel or
  night card, faint grid) whose key figure rises into place and gets a hand-drawn **neon ring**.
  Beats 2-3 are **paper slips** (warm paper, grain, torn tape) that snap in with a small
  overshoot from their side and pin onto the collage: a rising fact upper-right, a falling one
  lower-left, so the geometry itself says "up here, down there". Earlier cards dim behind.
- **Stamps** (`storyboard.HOOK_STAMPS`): fixed per (mode, archetype), never model-written,
  declared and scanned, e.g. QUIET: `QUIET DAY?` on the first figure, `BUT LOOK UNDERNEATH` on
  the last beat. A stamp is only ever used where its archetype's own trigger makes it true.
- **Curiosity line**: legible on frame 0; its first figure gets an animated neon marker
  underline. Second sentence in the brand colour.
- **Settled frame**: one curiosity line, one hero, one summary line under an `INSIDE` /
  `BEFORE THE BELL` chip. Agenda chips were removed - they competed with the hero.
- **Heroes**: `DEPTH_LOLLIPOP` became the *waterline* (paper tags pinned at each stock's move,
  Nifty neon-ringed on the line); `HEADLINE_NUMBER` a giant figure with a hand-drawn underline,
  candles, neon ring on the last candle and a taped context slip; `VERSUS_SPLIT` two paper cards
  from opposite sides with a neon VS; `SIGNAL_STACK_CHART` a chart with a neon ring on the event,
  and a paper volume slip with a hand-drawn arrow to the amber spike; `OVERNIGHT_BOARD` a night
  card with moon, underlined lead figure, a world strip, and a GIFT Nifty slip; `EVENT_CALENDAR` a
  taped paper calendar and a neon ring on the time; `NUMBERED_LIST` three fanned paper cards
  01/02/03.
- **Motion rules**: ease-out and back-ease snaps (overshoot only on cards landing), masked
  number rises, strokes drawn on progressively. No count-up of numbers: an intermediate figure
  was never validated and must never be on screen. Everything settles by settle + 0.9 s; only
  neon pulses breathe after that (a test checks the layout is frozen).
- **Neon** is limited to: the first figure's ring, the curiosity underline, the hero's key
  figure/event marker, VS, the event time. Rotations stay within ±3° (±6° for a stamp).
- Rejected: count-up numbers, world-map decoration, flashing red on falls, emoji/stickers,
  confetti/particle bursts, per-stock special cases, agenda chips on the settled frame.

## Hero visuals (Phase 1 geometry, restyled in 1A)

- **DEPTH_LOLLIPOP** – Nifty and each flagged stock on one % scale from a shared 0% waterline.
  Nifty barely leaves the line (a calm band marks its range); the stocks are far from it. The
  visual *is* the claim "quiet on top, busy underneath", and it is to scale.
- **HEADLINE_NUMBER** – a giant % over the last 20 candles, last candle ringed, context chip.
- **VERSUS_SPLIT** – two panels, bars from a shared midline, VS badge. When the two sides have
  no common scale (chart verdict vs a fundamental figure), arrows replace the bars so the
  graphic implies no false magnitude.
- **SIGNAL_STACK_CHART** – price line with range band or average, event marker + callout,
  amber volume spike, check-chips per signal family.
- **OVERNIGHT_BOARD** – lead cue tile (moon icon), other cues, GIFT Nifty strip.
- **EVENT_CALENDAR** – calendar page with the date, event tag, title and time.
- **NUMBERED_LIST** – three numbered rows; text items wrap, they are never cut mid-word.

## Teaser beats

2-3 beats of 0.85 s (0.95 s for two), each a quick cut of a real scene's visual language:
`LINE` (Nifty/stock line), `SECTOR_PAIR`, `SECTOR_TILE`, `MOVER_BAR`, `BREAKOUT` (Radar chart
event), `VOLUME` (amber spike), `RADAR` (sweep), `FLOWS`, `CUE`, `EVENT`, `METRIC`. Each beat
option is built by a sheet builder from validated data; the payload is copied, never computed.

Timing (3 beats): `0.00-0.85 · 0.85-1.70 · 1.70-2.55` beats, then 2.05 s settled. Total 4.6 s
(4.2 s with two beats; hard cap 5.0 s; the settled hold is always ≥ 1.8 s). The eyebrow and
curiosity line are on screen from **frame 0** - the feed autoplays from it - and the first beat
is already visible there. A light sweep marks each cut; progress pips show the beat count. The
hook scene is `self_animated`: the composer does not fade it in.

**POST beat diversity (POST freeze, `hooks/diversity.py`; PRE and CUSTOM unchanged).** A beat's
entities are the entities of the facts it cites plus every name it draws (a Radar sweep draws
all its stocks). (1) At most one beat per entity - `RADAR_EVENT:X`, `VOLUME_SPIKE:X`,
`TOP_LOSER` when X is the top loser, and a sweep that lights X all collide. (2) Then different
families are preferred - INDEX (`LINE`), BREADTH (`SECTOR_*`, `FLOWS`), STOCK (`MOVER_BAR`,
`BREAKOUT`, `VOLUME`, `RADAR`), then GLOBAL / EVENT / METRIC - and beats play in that order:
index -> sector/flow -> stock. (3) Fewer beats rather than a repeat: `pick_beats` tops up from
unused families, then from any entity-distinct beat, and stops there. Gemini sees each beat's
`entities` and `family`; a Gemini pick that repeats an entity fails structural validation and
the deterministic hook opens instead (the real 24 Sep 2026 Gemini pick was
NIFTY_CLOSE + VOLUME_SPIKE:LTF + RADAR_EVENT:LTF).

## Gemini contract

One call per run, **no Google Search tool** (the facts are supplied, so no grounding quota is
spent), `responseMimeType: application/json` with a `responseSchema` whose enums pin every
choice to what was offered, 45 s timeout, 2 tries, 429 fails fast - through the existing
`news.ask_gemini` (which gained optional `timeout` / `generation_config` arguments; defaults
unchanged). Prompt version `hook-1.0`.

```json
{
  "candidate_id": "post-quiet-hidden",
  "archetype": "QUIET_MARKET_HIDDEN_ACTION",
  "hero_visual": "DEPTH_LOLLIPOP",
  "teaser_beats": ["NIFTY_CLOSE", "RADAR_EVENT:MANKIND", "RADAR_EVENT:OFSS"],
  "curiosity_line": "Nifty moved just +0.29%. Five stocks underneath didn't.",
  "summary_line": "Inside: every sector, top movers and 5 Radar stocks.",
  "fact_ids_used": ["nifty.move", "radar.hidden"]
}
```

Gemini sees facts as `{fact_id, entity, statement, display, supports_words_for}`, candidates
with their allowed heroes and example lines, and beats with a plain description. It never
sees a series, so there is nothing to compute from.

## Validation (hooks/validation.py)

Run on Gemini's lines **and** on every template (a candidate whose own template fails is never
offered):

- length, allowed characters (no emoji), content safety `SAFE` (`core.content_safety`);
- no causal language (because, due to, after, amid, on hopes, driven by, led by, reason, ...);
- no prediction (will, could, may, likely, expect, set to, next, tomorrow, gap-up, target, ...);
- no recommendation (buy, sell, accumulate, invest, worth watching/seeing, opportunity, picks, ...);
- no hype (surge, soar, crash, plunge, massive, huge, record, historic, ever, ...);
- **numbers**: every number, digits or spelled out, must be one of the normalised number strings
  of a fact **about the entity it is written next to** (nearest preceding mention in the
  sentence), or of an entity-free count followed by its own noun ("5 stocks" can't license
  "5 sectors"); an explicit sign must match the fact. Rounding is rejected ("0.3" ≠ "0.29");
- **entities**: every capitalised word is a fact entity/alias, a brand word, or a plain sentence
  opener; naming a sector the sheet does not contain is rejected;
- **claims**: characterisations and superlatives ("just", "quiet", "led", "top", "biggest",
  "broke", "opposite", "strong", "weak") need a fact carrying the matching deterministic claim;
- **direction**: "rose/fell/bought/sold" right after an entity must match its fact;
- the curiosity line names a fact of the chosen candidate; the summary line promises only
  sections the Short contains, and must promise at least one;
- structure: known candidate, matching archetype, an approved hero with data, 2-3 distinct known
  beats, known fact ids.

A valid choice with invalid lines keeps Gemini's choice and uses that candidate's validated
template lines (`GEMINI_CHOICE_TEMPLATE_TEXT`). Anything structurally wrong → full fallback.

## Rules

- Major index day priority (POST freeze, `candidates.major_index_priority`, applied before
  Gemini sees the candidates - the model never makes this call). When |Nifty| >=
  `MAJOR_INDEX_PCT` (1.5%) and a BIG_MOVE candidate exists, a single-stock UNUSUAL_ACTIVITY
  candidate is NOT OFFERED unless its stock event is objectively exceptional - all three on the
  sheet's validated (move-guard-passed) Radar facts: |stock move| >= `EXCEPTIONAL_STOCK_PCT`
  (10%), relative volume >= `EXCEPTIONAL_RVOL` (5x), and |stock move| >=
  `EXCEPTIONAL_INDEX_MULTIPLE` (5) x |Nifty move|. An exceptional candidate then competes on its
  ordinary score. The decision (rule, suppressed and excepted candidates with each test's
  result) is recorded as `HookPlan.priority_rule`. Found on the real 24 Sep 2026 session:
  Nifty -1.64%, yet LTF -9.03% (UNUSUAL, score 0.76) opened the Short over BIG_MOVE (0.73).
- Sector claims follow direction: the strongest sector carries the "leader" claim (which
  licenses "led"/"top") only if it actually rose, the weakest carries "laggard" only if it
  actually fell, and the teaser slip's tags match the POST sector scene (FELL LEAST / FELL MOST
  on an all-down day). Fixed after the real 24 Sep 2026 session, where every sector fell and the
  teaser said LEADER over IT -0.44% - and a Gemini line "IT led" would have passed validation.

- Gemini chooses among deterministic, pre-approved candidates; it does not decide what is
  eligible. This deliberately relaxes the older rule "never ask Gemini what matters" for the
  hook only, at the owner's request (Phase 1 brief). Eligibility, scores, heroes and beats stay
  deterministic; with no key the hook is fully deterministic and reproducible.
- The hook never introduces a fact: every number on screen is a formatted value from the sheet,
  and the sheet is built from the same inputs as the rest of the Short.
- Every string any beat or hero draws is declared in the scene's `texts` (a test renders every
  beat and hero and checks each drawn string), so `Storyboard.public_text()` - and the content
  scan - see exactly what the viewer sees.
- Synthetic fixtures (`hooks/fixtures.py`) use placeholder stocks, and preview renders carry a
  "SYNTHETIC DATA - NOT REAL" badge.

## Status

- POST_MARKET: integrated into `daily_video` (`build_storyboard(dynamic_hook=True)` is the
  default; `dynamic_hook=False` keeps the legacy number card). `render_daily_market_byte.py`
  asks Gemini when `GEMINI_API_KEY` is set (`--no-hook-ai` to disable).
- PRE_MARKET (PRE V1, `docs/PRE_MARKET.md`): integrated into the PRE Short.
  `pre_market_inputs_from_brief(brief, plan)` fills `PreMarketInputs` with only what the Short
  shows (the plan's selected cues, its GIFT strip, its one event). Data-only additions, no
  visual change: a cue may carry `when` ("overnight" / "this morning"), `note` (the tile's
  timing label) and `time_label`; a LIVE lead is written "Hang Seng was 1.73% lower at 7:45 AM."
  (the time is licensed by its own fact); GIFT is "was ... at 7:52 AM", never "is" or "points
  to", and its tile note is "AT 7:52 AM IST"; "am"/"pm" joined the generic words; the summary
  may promise "what to watch" (`SECTION_PROMISES["WATCH"]`). A weekly F&O expiry is passed as a
  non-high-impact item (never an `EVENT_LED` hook); monthly expiry / FED / RBI are high-impact.
  Gemini still only chooses among PRE candidates; the PRE sections are decided by the
  deterministic `PreEditorialPlanner`, never by the model. PRE final polish: the first teaser
  beat is the curiosity line's own entity (`engine.lead_beat_first`, PRE mode only, one beat
  per entity, lines re-validated against the reordered beats), and the PRE summary lists its
  sections in play order (`candidates.summary_line`); POST is unchanged on both counts.
- CUSTOM_SINGLE_STOCK: hook only. `CustomStockInputs` is the contract the future video's data
  layer fills. No fundamentals source exists yet - CUSTOM fundamentals are synthetic.
- Previews: `python render_hook_previews.py` → `output/hook_previews/`.
