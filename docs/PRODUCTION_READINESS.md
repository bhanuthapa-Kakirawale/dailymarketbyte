# Production Readiness Gate V1 (PK-C)

One deterministic answer, before an expensive render:

> **Is this PRE or POST edition safe and ready to generate right now?**

```
python -m readiness pre          # "before the bell" edition (LIVE: now)
python -m readiness post         # recap of the latest completed session (LIVE: now)
```

The gate is **read-only**. It consumes the existing authoritative checks and never renders,
uploads, rebuilds a report, re-runs a capture, or changes an editorial selection:

- the session calendar;
- the canonical report lookup;
- `core.freshness`;
- the REPORT job's capture record and the persisted snapshots;
- Editorial Planner V3;
- the publication audit;
- render prerequisites.

Package: `readiness/`.

| Module | Role |
|---|---|
| `models.py` | `CheckResult`, `ReadinessResult`, aggregation, exit codes |
| `matrix.py` | the capability matrix below |
| `evidence.py` | point-in-time, read-only loaders |
| `checks.py` | data, editorial and publication checks |
| `environment.py` | renderer, ffmpeg, fonts and storage checks |
| `pre.py` / `post.py` | the PREFLIGHT evaluators |
| `post_render.py` | the POST_RENDER stage |
| `report.py` | the readiness JSON |
| `__main__.py` | the CLI |

## Statuses

| Overall | Meaning | CLI exit |
|---|---|---|
| **READY** | Every check passes or does not apply. Generate normally. | `0` |
| **DEGRADED** | No required dependency is broken. One or more OPTIONAL capabilities are unavailable or stale, and that section is omitted or reduced safely. Generation may proceed; the warnings say what is missing. | `10` |
| **BLOCKED** | A REQUIRED dependency, a temporal guarantee or a safety control is broken. Do not generate or publish. | `20` |
| *(execution error)* | The gate itself could not evaluate. This is not a verdict, and it must never be read as READY. | `30` (any other non-zero, e.g. `1` from an interpreter crash, is also an error) |

**Check level.** Each check is PASS, WARN, FAIL or SKIP.
- The aggregation is pure: any FAIL makes the edition BLOCKED, otherwise any WARN makes it DEGRADED, otherwise it is READY.
- A check's status follows its capability's requirement. An unmet REQUIRED capability FAILs (severity BLOCKING). An unmet OPTIONAL capability WARNs (DEGRADING). NOT_APPLICABLE gives SKIP (INFO).
- An exception inside one check is graded the same way, with the code `CHECK_ERROR`, so a bug in an optional check can never cost the edition.

**Source health vocabulary.** Every source-facing check reports one of:

| Health | Meaning |
|---|---|
| `HEALTHY` | the source answered and has data |
| `HEALTHY_EMPTY` | answered, and validly had nothing |
| `STALE` | data exists but is older than expected |
| `PARTIAL` | some, but not all, of the data is there |
| `SOURCE_FAILURE` | the source failed |
| `UNSUPPORTED` | no adapter, by design (ESM, `NOT_SUPPORTED_YET`) |
| `MISSING` | never captured, or not yet captured at the cutoff |

`HEALTHY_EMPTY` and `UNSUPPORTED` never fail.

**A short or quiet edition that is valid is READY.** The only duration floor is the readability floor, `MIN_SHORT_DURATION` = 15 s. There is no historical-average minimum and no filler.

## Capability matrix (required vs optional)

Traced from the production paths:
- **PRE:** `products.premarket.build_real_brief` + `render_pre`.
- **POST:** `main.run` up to `render_post`.

The table is `readiness.matrix.MATRIX`, and each check's severity is derived from it.

| Capability | PRE | POST | Authority reused |
|---|---|---|---|
| Trading session (calendar) | REQUIRED | REQUIRED | `core.trading_calendar`, `products.report_job.resolve_session` |
| Edition window | REQUIRED (cutoff before the 09:15 open; previous session final) | REQUIRED (session final, ≥ 15:40 IST) | `SESSION_FINAL_TIME` |
| Canonical report (previous session / recap session), publication-ready | REQUIRED | REQUIRED | `find_canonical_report`, `require_previous_report` codes |
| Benchmark (Nifty 50 close: eligible, dated to the expected session) | REQUIRED | REQUIRED | report fact + `ELIGIBLE_HISTORICAL_STATUSES` |
| Temporal safety (report existed at the cutoff; no Indian cash-session fact dated after the described session) | REQUIRED | REQUIRED | report `generated_at`, fact `market_date` |
| Overnight global cues | OPTIONAL | — | `fetch_premarket_quotes` + `pre_run_history.degradations` (`core.freshness`) |
| India VIX | OPTIONAL | OPTIONAL | PRE: acquisition freshness; POST: report fact |
| GIFT Nifty | — (policy closed: never a degradation) | — | `operations.gift_policy` |
| Official event schedule file | OPTIONAL | — | `check_official_events` (EXPIRED / WARN_FILE degrade; maintenance warnings do not) |
| Sectors | OPTIONAL | OPTIONAL | report sector facts |
| Movers | — (public) | OPTIONAL | `editorial.movers_gate` (coverage ≥ 90%) |
| FII / DII (NSE provisional, in the report) | OPTIONAL | OPTIONAL | report facts (AI-only = PROVISIONAL, never shown) |
| Institutional flow (NSE / CDSL / NSDL snapshots) | OPTIONAL | OPTIONAL | `institutional_flows.store.load_latest(first_retrieved_before=cutoff)` |
| Market Events (six LIVE families; IPO projection exempt) | OPTIONAL | OPTIONAL | REPORT-job capture record + `market_events.store.list_events(as_of=)` |
| Market Structure | OPTIONAL | OPTIONAL | `market_structure_<D>.json` per-metric status |
| Official snapshots (F&O ban / ASM / GSM / IPO lists) | OPTIONAL | OPTIONAL | `official_snapshots` manifest |
| Intelligence snapshot | OPTIONAL | OPTIONAL | `intelligence.load_snapshot` (PRE) / `build_snapshot` in memory (POST) |
| Radar | — (public profile: no stock watch) | — (public shows no Radar) | — |
| Editorial Planner V3 plan | REQUIRED | REQUIRED | the planner + storyboard (below) |
| Publication (content scan + publication audit) | REQUIRED | REQUIRED | `scan_publication`, `audit_storyboard`, `audit_verdict` |
| Renderer (imports, Composer) | REQUIRED | REQUIRED | `daily_video.composer` |
| ffmpeg | REQUIRED | REQUIRED | `video._ffmpeg` + one `-version` call |
| Fonts | REQUIRED (a TrueType font resolves) | REQUIRED | `daily_video.typography.font_report` |
| Audio assets | — (`AUDIO_ENABLED` is False) | — | — |
| Storage (output and temp writable, history readable, disk ≥ 200 MB) | REQUIRED | REQUIRED | temp-file probe, `MarketHistory.open_readonly`, `shutil.disk_usage` |

How individual sources are graded:
- **Market Events.** Each LIVE family is its own check, `MARKET_EVENTS.<FAMILY>`.
  - SUCCESS with events is HEALTHY. SUCCESS with none is HEALTHY_EMPTY.
  - `NOT_SUPPORTED_YET` is UNSUPPORTED.
  - SOURCE_UNAVAILABLE, PARSE_ERROR or VALIDATION_FAILED is SOURCE_FAILURE, which WARNs.
  - No REPORT record completed by the cutoff is MISSING, which WARNs.
- **Market Structure.** It is graded by the aggregator's own per-metric status (PUBLISHABLE, PARTIAL ≥ 95%, SUPPRESSED).
  - POST reads the core breadth metrics: PARTIAL degrades, all SUPPRESSED degrades.
  - PRE reads only the 52-week metrics, the only kind PRE shows.
  - A PARTIAL 52-week coverage is the expected P3 state, never a zero reading: recent listings lack 252 sessions and count as uncovered. It is noted, not degraded.
- **Institutional.** NSE's provisional figure is a per-session report, so it must be for the expected session (POST: the recap session; PRE: the previous session). CDSL and NSDL are aged in days, using the private desk's thresholds: 3 and 20.

## Editorial Planner V3 check

The plan is built exactly as production builds it, and nothing is rendered:
- **POST:** `plan_for_post` → persisted public intelligence (`fetch=False`, as `evening_full` runs the POST) → `build_post_storyboard(hook_ai=False)`.
- **PRE:** `build_real_brief` with read-only seams → `plan_pre_sections` → `build_pre_storyboard(hook_ai=False)`.

**Structurally valid POST:**
- it opens with a hook and ends with the close;
- an index story (PULSE / NIFTY) is present, unless the hook itself states Nifty's move;
- every scene kind is renderable;
- 15 s ≤ duration ≤ `POST_MAX_RUNTIME`;
- the decision trace is non-empty, and every candidate has a decision;
- no `SUPPRESSED_DUPLICATE` points at a candidate that is not shown (an unresolved duplicate).

**Structurally valid PRE:** SETUP and WATCH are present, plus the same trace, scene and budget checks (`MAX_RUNTIME`). A missing OVERNIGHT section is reported under GLOBAL_CUES (a WARN), not as a plan failure.

## Publication

The publication boundary is never downgraded. The final content scan, PRE's language and provenance gates, and every content check of the publication audit block:
- language;
- displayed claims;
- GMP;
- source / universe visibility;
- profile.

**Rights** (`publication_rights` alone = `RIGHTS_BLOCK_ONLY` under the default `PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK`) are graded by **intent**:

| Intent | `RIGHTS_BLOCK_ONLY` |
|---|---|
| `--intent shadow` (default; nothing uploads) | PASS, recorded in `observed.verdict` |
| `--intent publish` | FAIL → BLOCKED |

## Freshness policy (semantic, not "the file exists")

Every reader is bounded by the **cutoff**. Something that came into existence after the cutoff counts as not existing yet.

| Input | Timestamp used | Rule |
|---|---|---|
| Canonical report | `generated_at` | must be ≤ cutoff |
| REPORT capture record (events, institutional capture status) | `completed_at` | newest record completed ≤ cutoff |
| Market event | revision `retrieved_at` | `list_events(as_of=cutoff)`: the revision current then (a later correction never leaks backward) |
| Institutional snapshot | revision `retrieved_at` | `institutional_flows.store.list_snapshots(as_of=cutoff)`: the revision genuinely on disk at the cutoff, by its own capture time - never `first_retrieved_at`, which is pinned to the first revision and so cannot tell a later restatement apart; NSE must be the expected session |
| Market Structure | revision `universe_source.retrieved_at` | `market_structure.store.load_revision_as_of(cutoff)`: the revision genuinely on disk at the cutoff (an immutable `.revN.json` chain alongside the always-current file); a session with no chain yet falls back to its single legacy file, `session_date` = expected session |
| Official snapshots | per-kind `retrieved_at` | ≤ cutoff |
| Overnight cues (PRE, LIVE only) | bar dates / timestamps | `core.freshness` (US close final; Asia live bar ≤ 30 min old; nothing on/after the PRE date read as a close) |
| PRE edition | cutoff | before 09:15 IST on the PRE date, and after the previous session's 15:40 final |
| POST edition | cutoff | the session must be final |

**PRE at 07:45.** The US close from the overnight session is valid, and so is the previous Indian close. Indian data of the session about to open is never an input: a fact dated on or after the PRE date FAILs TEMPORAL_SAFETY. INDEX_LEVEL readings (GIFT Nifty this morning), commodities and FX are not Indian cash-session facts and are judged by their own freshness rules.

## LIVE vs REPLAY (point-in-time)

- **LIVE** means neither `--session-date` nor `--as-of` is given. The cutoff is now; this is the ONLY wall-clock read.
  - PRE probes the overnight cues through the real acquisition path: about 8 small Yahoo requests (3 US daily, 2 Asia × daily + 5-minute, India VIX), counted in `runtime.network_calls`.
  - Nothing else touches the network. Snapshots are read as persisted (`fetch=False`).
- **REPLAY** means `--as-of`, or `--session-date` with no `--as-of`.
  - Default cutoffs: PRE is the PRE date at 07:45 IST, run_premarket's convention. POST is the session at 23:59:59 IST, public_intelligence's replay convention.
  - REPLAY never fetches: overnight cues and India VIX are SKIP.
  - The verdict is identical whatever the wall clock says (tested).

**Point-in-time provenance hardening (`feature/point-in-time-provenance-hardening-v1`).** Two upstream gaps PK-C surfaced but did not fix are now closed at the store layer, not in the gate itself:
- `institutional_flows.store.list_snapshots`/`load_latest` gained an `as_of` parameter (mirroring `market_events.store.list_events`'s own prior fix): a report's revision chain is now scanned by each revision's own `retrieved_at`, not the chain-wide `first_retrieved_at` a restatement used to inherit, so a later restatement can no longer leak into an earlier replay.
- Market Structure gained an immutable `.revN.json` revision chain alongside its existing mutable "current" file (`market_structure.store.save_snapshot`/`load_revision_as_of`); a rebuild that changes nothing is a true no-op, a rebuild that changes something gets its own revision, and replay reads the revision genuinely on disk as of the cutoff instead of blindly re-reading whatever the file currently holds. A session with no revision chain (built before this change) falls back to its single legacy file.

## Stages

| Stage | Question | Reads |
|---|---|---|
| `PREFLIGHT` (default) | may generation START? | everything above, before any render |
| `POST_RENDER` (`--stage post-render`) | did the rendered edition pass QA? | the POST `production_manifest.json`, or the PRE `shadow_manifest.json` + `pre_result_<D>.json` |

POST_RENDER reuses `operations.daily_check.post_content_verdict` and `audit_verdict`. Its checks are video QA, frames QA, content QA, the publication audit (rights by intent) and duration within 15–70 s. "Safe to render" is never "the MP4 passed QA".

## CLI

```
python -m readiness {pre|post} [--session-date YYYY-MM-DD] [--as-of ISO] \
                               [--intent shadow|publish] [--stage preflight|post-render] \
                               [--json] [--no-write]
```

Human output (`VERDICT:` + `DECISION:` lines, the repo's operator style):

```
================================================================
 DMB PRE READINESS  (PREFLIGHT, LIVE, intent SHADOW)
================================================================
  session              2026-10-08 (previous session 2026-10-07)
  cutoff               2026-10-08T07:12:03+05:30
  status               DEGRADED
  ------------------------------------------------------------
  PASS  SESSION                        2026-10-08 is a session; previous completed session 2026-10-07
  PASS  CANONICAL_REPORT               previous-session report 20261008_PRE_MARKET ...
  WARN  INSTITUTIONAL_FLOW             institutional flow degraded: NSE latest 2026-10-06 ...
  PASS  EDITORIAL_PLAN                 4 sections, 33.0s, trace complete
  PASS  PUBLICATION                    content checks pass; RIGHTS_BLOCK_ONLY (expected in shadow)
  ...
  fix INSTITUTIONAL_FLOW           CDSL/NSDL/NSE source unavailable or not yet published ...
  runtime              3.8s, 8 network call(s)
================================================================
VERDICT: DEGRADED
DECISION: SAFE TO GENERATE WITH DEGRADED INSTITUTIONAL
```

## JSON (`--json`, and the report file) - schema `dmb.readiness/1`

```json
{
  "schema": "dmb.readiness/1", "edition": "POST", "stage": "PREFLIGHT", "mode": "LIVE",
  "intent": "SHADOW", "profile": "PUBLIC_UNREGISTERED",
  "evaluated_at": "2026-10-07T19:31:02+05:30", "as_of": "2026-10-07T19:31:00+05:30",
  "cutoff": "2026-10-07T19:31:00+05:30", "session_date": "2026-10-07",
  "edition_date": "2026-10-08", "source_session": null,
  "overall_status": "DEGRADED", "decision": "SAFE TO GENERATE WITH DEGRADED FLOWS",
  "exit_code": 10,
  "blocking_reasons": [],
  "warnings": ["FII_DII: only PROVISIONAL FII/DII facts (e.g. AI-only) for 2026-10-07 - ..."],
  "checks": [{"check_id": "FII_DII", "category": "FLOWS", "capability": "FII_DII",
              "requirement": "OPTIONAL", "severity": "DEGRADING", "status": "WARN",
              "message": "...", "source_health": "PARTIAL", "observed": ["PROVISIONAL"],
              "expected": null, "source": null, "as_of": null,
              "remediation": "normal before NSE publishes (~19:00 IST); re-run the REPORT later"}],
  "notes": [], "runtime": {"seconds": 1.4, "network_calls": 0}
}
```

Stability rules:
- Checks appear in a fixed order.
- Only `evaluated_at` and `runtime` vary between evaluations of the same evidence.
- On an execution error the CLI prints `{"schema": ..., "overall_status": null, "error": ..., "exit_code": 30}`.

PK-D (`production_orchestrator/`, docs/PRODUCTION_ORCHESTRATOR.md) imports `readiness.pre.evaluate_pre`, `readiness.post.evaluate_post` and `readiness.post_render.evaluate_post_render` directly, the same in-process functions `operations.evening_full.post_readiness` already calls - a third caller, never a second readiness implementation.

## Report file (audit trail)

- **Where:** a LIVE evaluation writes `output/readiness/readiness_<PRE|POST>_<session>.json` (POST_RENDER: `..._post_render.json`). There is one file per edition and session, overwritten by the next LIVE evaluation, so files do not sprawl.
- **When not written:** REPLAY / `--as-of` and `--no-write` write nothing.
- **Other writes:** the only other write is the storage probe's temp file, created and deleted at once.

The Private Desk's Data Quality page shows the latest PRE and POST report (status, decision, blocking reasons, warnings). It reads that JSON and never runs the gate.

## Batch integration

- **`scripts\run_morning_pre.bat`** runs `python -m readiness pre` before `main.py --mode premarket --shadow`:
  - 0: continue.
  - 10: print "DEGRADED - continuing" and continue.
  - 20: print the reasons and stop with exit 2 (STOP). Nothing is rendered.
  - Anything else: stop with exit 1 (the gate could not evaluate).
- **`scripts\run_evening_full.bat`** (`operations.evening_full`) evaluates `readiness.evaluate_post` in-process, after the REPORT gate and right before the POST render. It is skipped when the session already has a passing POST.
  - A `READINESS` row joins the summary.
  - BLOCKED stops (POST NOT RUN, ATTENTION REQUIRED).
  - DEGRADED continues, and adds each warning to the notes.
  - A gate error stops (fail closed).
- **`scripts\run_production_pre.bat` / `run_production_post.bat`** (PK-D, docs/PRODUCTION_ORCHESTRATOR.md) call the same evaluators in-process, plus a run lock, idempotency, `--resume` and an immutable run manifest - the preferred production entry points, additive to the two bullets above.
- **Unchanged:** `run_post.bat`, `run_evening.bat`, `main.py` and the isolated test-run commands do not run the gate. They stay usable manual / advanced paths.

## Operator remediation flow

1. Read the `fix <CHECK>` lines: every FAIL / WARN with an obvious remedy carries one.
2. Act on the BLOCKED reason:

| BLOCKED reason | What to do |
|---|---|
| `CANONICAL_REPORT` (PRE: PREVIOUS_SESSION_MISSING) | Run the evening REPORT for the previous session (`scripts\run_evening.bat`). PRE never shows an older session. |
| `CANONICAL_REPORT` (POST: MISSING) | Run the REPORT job first. The POST's inline fallback is a recovery path, not "ready". |
| `EDITION_WINDOW` | PRE runs before 09:15 IST; POST runs after 15:40 for a completed session. |
| `SESSION` NOT_A_SESSION | There is no edition today. Nothing to do. |
| `BENCHMARK` stale | The provider did not have the session yet. Re-run the REPORT later. |
| `TEMPORAL_SAFETY` | The report did not exist at the cutoff, or describes the wrong session. Rebuild or wait. |
| `EDITORIAL_PLAN` | A planner / storyboard defect. Do not render; inspect the trace. |
| `PUBLICATION` | A content / template defect. Nothing may be rendered for publication. |
| `FFMPEG` / `RENDERER` / `FONTS` | Fix the venv (`scripts\check_setup.bat`, `pip install -r requirements.txt`). |
| `STORAGE` | Permissions, disk space, or a missing history database (run the REPORT job). |

3. DEGRADED needs no action to generate. The listed sections are omitted honestly. Re-running the REPORT later often clears a not-yet-published source (FII/DII ~19:00 IST).

PK-C deliberately has no automated repair; recovery is a later packet.

## Validation

- `python -m pytest tests/test_readiness.py -q` runs offline in a clean checkout.
- `python validate_readiness.py` is the bounded historical / shadow REPLAY over stored editions (PRE and POST, normal, Monday, holiday boundary, quiet, degraded scenarios). It writes `output/readiness_validation/` and reports the distribution as it is.
