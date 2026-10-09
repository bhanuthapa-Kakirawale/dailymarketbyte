# Production Orchestration V1 (PK-D)

One command per edition, locally:

```
python -m production_orchestrator pre          # "before the bell" shadow run
python -m production_orchestrator post         # the evening REPORT + POST_UNIFIED run
python -m production_orchestrator status       # read-only: the latest PRE / POST run
```

PK-D is a **sequencing and bookkeeping layer**, nothing more. It calls the exact same commands
`scripts\run_morning_pre.bat` and `scripts\run_evening_full.bat` already call - it never
redesigns or recalculates readiness policy, Editorial V3, Radar, Market Regime, Market Events,
Market Structure, institutional-flow calculations, or publication rights. It adds a run lock,
idempotency, `--resume`, and one immutable JSON run manifest per attempt. It never uploads to
YouTube, regardless of `--intent publish` - the existing rights/readiness gates decide that,
unchanged.

Package: `production_orchestrator/`.

| Module | Role |
|---|---|
| `models.py` | `RunManifest`, `StageRecord`, status vocabulary, exit codes |
| `lock.py` | the run lock (PID + hostname + liveness, cross-platform) |
| `manifest.py` | reading/writing the run manifest + `latest.json` |
| `post.py` | the POST stage sequence |
| `pre.py` | the PRE stage sequence |
| `status.py` | the read-only status read, shared by the CLI and Private Desk |
| `__main__.py` | the CLI |

## Production entry points vs lower-level / diagnostic scripts

| Use | Command |
|---|---|
| **Normal morning production** | `scripts\run_production_pre.bat` |
| **Normal evening production** | `scripts\run_production_post.bat` |
| **Check the latest run** | `scripts\check_production_status.bat` |
| Lower-level / diagnostic (unchanged, still supported) | `scripts\run_morning_pre.bat`, `scripts\run_evening_full.bat`, `scripts\run_post.bat`, `scripts\run_evening.bat`, `scripts\check_daily_run.bat`, `scripts\check_setup.bat` |

The lower-level scripts are untouched. PK-D is additive: it wraps the same commands with a lock,
idempotency and a run manifest, and is the **preferred** way to run production, but nothing
stops working if you keep using the older scripts for diagnosis.

## Lifecycle (stages)

| Stage | PRE | POST |
|---|---|---|
| `SESSION_RESOLUTION` | a non-session day is `SKIPPED` here (no lock taken, nothing rendered) | `products.report_job.resolve_session` - an unresolvable/not-final session is `BLOCKED` here |
| `RUN_LOCK` | acquire `output/production_runs/PRE/<date>/run.lock` | acquire `output/production_runs/POST/<date>/run.lock` |
| `IDEMPOTENCY_CHECK` | a passing prior `shadow_manifest.json`, re-confirmed by `readiness.post_render.evaluate_post_render` → `ALREADY_COMPLETED` | a passing prior `report_job` + POST manifest, re-confirmed the same way → `ALREADY_COMPLETED` |
| `ACQUISITION_OR_REPORT` | *(PRE has none - the renderer is one call)* | `python main.py --mode report` (idempotent on its own) |
| `PREFLIGHT_READINESS` | `readiness.pre.evaluate_pre` (PK-C) | `readiness.post.evaluate_post` (PK-C) |
| `RENDERER` | `python main.py --mode premarket --shadow --session-date D --as-of ISO` | `python main.py --no-fetch-public`, skipped if a passing POST for this session+report already exists (unless `--force`) |
| `POST_RENDER_CHECK` | `readiness.post_render.evaluate_post_render("PRE", ...)` | `readiness.post_render.evaluate_post_render("POST", ...)` |
| `MANIFEST` | write the run manifest + `latest.json` (unless `--no-write`) | same |

BLOCKED at `PREFLIGHT_READINESS` always stops before the renderer. A failure found at
`POST_RENDER_CHECK`, after a render was actually attempted, is `FAILED`, never `BLOCKED` -
those are deliberately different states (see Statuses below).

## Statuses

Reused verbatim from the existing `publication_runs` vocabulary where it already means the same
thing; only `ALREADY_COMPLETED` and `LOCKED` are genuinely new.

| `orchestrator_status` | Meaning | Exit code |
|---|---|---|
| `SUCCESS` | every stage passed, both PK-C verdicts were READY | `0` |
| `DEGRADED` | the render completed; PREFLIGHT and/or POST_RENDER readiness was DEGRADED (optional capability unavailable) | `0` |
| `ALREADY_COMPLETED` | idempotency short-circuit - nothing was re-run | `0` |
| `SKIPPED` | PRE on a non-trading day | `0` |
| `BLOCKED` | PREFLIGHT readiness (or session resolution, or the REPORT gate) stopped the run before any render | `20` |
| `FAILED` | a render was attempted and then failed (subprocess error, POST_RENDER readiness BLOCKED, an exception) | `30` |
| `LOCKED` | another production run already holds the lock for this edition+session | `40` |

`readiness_status_preflight` and `readiness_status_post_render` are stored as **separate**
fields on the manifest (verbatim `READY`/`DEGRADED`/`BLOCKED`) - never folded into
`orchestrator_status`. This is what lets a reader tell "stopped because PREFLIGHT was BLOCKED"
apart from "a render was attempted and POST_RENDER then said BLOCKED" (`FAILED`).

## Run manifest - schema `dmb.production_orchestrator.run/1`

```
output/production_runs/PRE/<session_date>/<run_id>.json    immutable once orchestrator_status
output/production_runs/POST/<session_date>/<run_id>.json   is terminal (not RUNNING)
output/production_runs/PRE/latest.json                     mutable pointer, one per edition
output/production_runs/POST/latest.json
```

Key fields: `run_id`, `edition`, `command`, `session_date`, `intent`, `started_at`,
`completed_at`, `orchestrator_status`, `readiness_status_preflight`,
`readiness_status_post_render`, `stages` (per-stage status/detail), `resumed_from_run_id`,
`forced`, `canonical_report` (`report_id`/`path`/`source`), `readiness_report` (paths to the
PK-C reports it wrote), `video_artifact`, `audit_artifact`, `warnings`, `blocking_reasons`,
`failure`, `reclaimed_stale_lock`, `runtime` (seconds + every subprocess call made), `git_commit`,
`host` (`hostname`/`pid`), `cli` (argv/resume/force).

Write order is correctness-critical: the immutable run file is written first (tmp + `os.replace`,
the same atomic pattern `readiness/report.py` uses), **then** `latest.json` is updated - so
`latest.json` never points at a file that does not exist yet. A run also writes itself as
`RUNNING` right after the lock is acquired (and again right after `PREFLIGHT_READINESS`
succeeds) - this is the checkpoint a later `--resume` finds after a crash. `--no-write`
suppresses every manifest write but never the lock.

## The run lock

`output/production_runs/<EDITION>/<session_date>/run.lock` - scope is one (edition, session).
A PID + hostname + timestamp JSON file, not a bare OS advisory lock, so a crash leaves a
forensically useful trace and staleness is decided deterministically rather than just
OS-auto-released:

- **Same host:** staleness is decided *only* by whether the PID is still alive - never by age.
  Windows liveness uses `OpenProcess`/`CloseHandle` (never a signal) rather than
  `os.kill(pid, 0)`, which is unsafe on Windows: CPython routes any signal other than
  `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` through `TerminateProcess`, so `os.kill(pid, 0)` can
  actually kill the process it is only trying to probe.
- **Other host:** liveness can't be verified remotely, so staleness falls back to age, with a
  generous **4-hour** threshold - every real run here finishes in single-digit minutes, so this
  never fires on a legitimately slow run, while still letting a crashed remote machine's lock be
  recovered within the same operator day.
- A reclaim is never silent: it is recorded in the new run's manifest
  (`reclaimed_stale_lock`) and shown in the console summary.
- `release()` only ever removes a lock whose `run_id` matches the one this process itself
  acquired - it can never remove someone else's lock.

## Idempotency, `--force`, `--resume`

- **Idempotent by default.** If a prior run for this (edition, session) already completed and
  PK-C's `POST_RENDER` check still confirms it (re-read live, never trusted blindly),
  `IDEMPOTENCY_CHECK` short-circuits to `ALREADY_COMPLETED` - nothing is re-run.
- **`--force`** bypasses only PK-D's own shortcuts (the idempotency check, and the renderer's
  "already rendered, don't re-render" skip). It never adds a flag to the underlying `main.py`
  calls - those are always exactly the same `--mode report` / `--no-fetch-public` /
  `--mode premarket --shadow` commands `evening_full`/`run_morning_pre.bat` already use. Since
  `main.py`'s own report-reuse logic is what actually prevents a duplicate canonical report,
  `--force` can never violate canonical-report immutability - it has no path to.
- **`--resume`** looks for the newest manifest for this (edition, session) still `RUNNING`. If
  found, it reuses that run's `run_id` and original `started_at`, and keeps overwriting that
  same file. For PRE specifically, if that leftover manifest already recorded a PREFLIGHT
  readiness verdict for the *same* `as_of`, it is reused rather than re-evaluated - this is the
  one stage whose readiness call touches the network (~8 Yahoo calls for the overnight cues).
  For POST, "don't rebuild REPORT, just render" falls out for free: `main.py --mode report` is
  itself idempotent, and the renderer is skipped only when a passing manifest already exists -
  so resuming after a render-stage crash simply re-enters the sequence and does the minimum work.
  Omitting `--resume` is always safe: a leftover `RUNNING` manifest is left as a forensic trace
  only, since the **lock**, not the manifest, is what prevents real concurrency.

## Console summary

```
================================================================
 DMB POST PRODUCTION RUN
================================================================
  session                2026-10-08
  orchestrator_status    DEGRADED
  readiness (preflight)  DEGRADED
  readiness (post_render) READY
  video                  output/daily_byte_2026-10-08.mp4
  runtime                182.4s
  warning                INSTITUTIONAL_FLOW: ...
  run_id                 20261008T140501Z_a1b2c3d4
================================================================
EXIT CODE: 0
```

`--json` prints the manifest's `to_dict()` only - no narration.

## Status command

```
python -m production_orchestrator status [--json]
scripts\check_production_status.bat
```

Read-only: it only reads each edition's `latest.json`. It never acquires, evaluates readiness,
or renders anything.

## Shadow vs publish intent

Default `--intent shadow` (nothing uploads either way - PK-D has no upload path at all). Passing
`--intent publish` is forwarded unchanged to the existing PK-C readiness calls, so
`PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK` applies exactly as it does today: a `RIGHTS_BLOCK_ONLY`
artifact that would be accepted under shadow is `BLOCKED`/`FAILED` under publish. PK-D adds no
override and never imports `upload` (test-enforced, `tests/test_production_orchestrator_
publish_intent.py`).

## Private Desk

`private_desk/services/production_runs.py` adds a **read-only** "Production runs" panel to the
Data Quality page: the latest PRE/POST `orchestrator_status`, both readiness verdicts, session,
run id and warnings - read straight from `latest.json`. There is no "run production" control and
no new write path (test-enforced).

## Task Scheduler preparation (not installed automatically)

PK-D is safe to invoke from Windows Task Scheduler: the lock already protects against a manual
run overlapping a scheduled one, or two scheduled runs overlapping. A scheduled action simply
runs:

```
scripts\run_production_pre.bat
scripts\run_production_post.bat
```

at the usual times (~07:00 IST and ~19:30 IST - unchanged from today). Readiness still decides
whether the run is actually safe at that moment; PK-D does not encode the clock as a safety rule.
No scheduled task is registered by this change - that remains a manual, owner-approved step.

## Failure recovery

1. Read the console summary's `warning`/`blocking` lines, then the manifest's `stages` for
   exactly where it stopped.
2. `BLOCKED` at `PREFLIGHT_READINESS` → read `docs/PRODUCTION_READINESS.md`'s remediation table;
   do not bypass it.
3. `FAILED` after a render was attempted → inspect the artifact `POST_RENDER_CHECK` names, then
   re-run with `--resume` (safe: REPORT/preflight work already done is not repeated) or
   `--force` (an intentional full rerun).
4. `LOCKED` → another run is genuinely in progress, or a lock needs to age out on a dead remote
   host (4-hour threshold); never delete the lock file by hand.

## Testing

`tests/test_production_orchestrator_*.py` - fully offline, isolated to `tmp_path` (no dependency
on real `output/`, the market database, or the network). Covers: the manifest/exit-code model,
lock acquire/conflict/stale-reclaim (same-host liveness, cross-host age threshold, corrupted
lock), POST and PRE clean runs, BLOCKED-before-render vs FAILED-after-render, idempotency,
`--force`, crash/`--resume` recovery, the publish-intent guard, the CLI, the read-only status
command, and the Private Desk panel.

```
python -m pytest tests/test_production_orchestrator_*.py tests/test_isolated_test_runs.py -q
```
