# Shadow Production Operations V1 (PK-E)

```
python -m shadow_scheduler run intelligence_am
python -m shadow_scheduler run pre
python -m shadow_scheduler run intelligence_pm
python -m shadow_scheduler run post
python -m shadow_scheduler status [--json]       # read-only, no side effects
```

PK-E is a **Windows Task Scheduler wrapper**, nothing more. It only starts one of the four
already-approved DMB entry points - `scripts\run_intelligence_refresh.bat`,
`scripts\run_production_pre.bat`, `scripts\run_production_post.bat` - captures the operational
result, and reports read-only health. It never decides market/business readiness itself, never
reimplements PK-C readiness or PK-D orchestration, never adds a retry around a legitimate
business `BLOCKED`, and never uploads to YouTube (no scheduled command ever passes
`--intent publish`; the two production jobs run with PK-D's own default, `shadow`).

Package: `shadow_scheduler/`.

| Module | Role |
|---|---|
| `models.py` | `JobSpec`, `RunRecord`, runner status vocabulary, infra exit codes |
| `config.py` | the static, trusted job registry (`JOB_REGISTRY`) and lock/schedule constants |
| `lock.py` | the scheduler-wide run lock (PID + hostname + liveness) + bounded wait |
| `runner.py` | the thin runner: lock, launch, capture, record, release |
| `records.py` | reading/writing shadow_scheduler's OWN run records + `latest/<job>.json` |
| `status.py` | the read-only status read (structurally write-free) |
| `__main__.py` | the CLI |

## What this is NOT

PK-E adds zero new market/readiness/business logic. It never computes a readiness verdict,
never evaluates data freshness, never touches Radar, Market Regime, Market Structure,
Editorial Planner V3, Institutional Flow, or Market Events. It never renders a video, never
constructs a shell command from market/log/source text (the child command is built only from
the static `JOB_REGISTRY`, keyed by a fixed job name), and never installs a real Windows
scheduled task on its own - `-Install` is a separate, explicit, owner-run step (see below), not
something any Claude Code packet runs automatically.

## The four jobs

| job | entry point | IST time | business outcome read from |
|---|---|---|---|
| `intelligence_am` | `scripts\run_intelligence_refresh.bat` | 06:40 | `output/intelligence_refresh/latest.json` |
| `pre` | `scripts\run_production_pre.bat` | 07:00 | `output/production_runs/PRE/latest.json` |
| `intelligence_pm` | `scripts\run_intelligence_refresh.bat` | 19:15 | `output/intelligence_refresh/latest.json` |
| `post` | `scripts\run_production_post.bat` | 19:30 | `output/production_runs/POST/latest.json` |

All Mon-Fri. On an NSE holiday the tasks still fire - PK-C/PK-D remain the authority on
whether rendering is actually safe at that moment; PK-E does not encode the trading calendar
into a go/no-go decision of its own (it only *reads* the calendar, read-only, for the
MISSED/NOT_EXPECTED check in `status`).

Note on the local evening schedule: the local `post` job (19:30 IST) runs
`production_orchestrator post`, which bundles the same-evening REPORT build
(`ACQUISITION_OR_REPORT`) and the render in one pass - mirroring `operations.evening_full`'s
own same-evening pattern. This is intentionally different from the GitHub Actions cloud
schedule (REPORT at 19:30 IST, the upload-capable POST the *next* morning at 07:40 IST) - the
two pipelines solve the same problem on different clocks, not a conflict to reconcile.

## Runner statuses

Deliberately a small, own vocabulary - never renamed to match PK-C's `READY`/`DEGRADED`/
`BLOCKED` or PK-D's/intelligence_refresh's `SUCCESS`/`DEGRADED`/`BLOCKED`/`FAILED`/... A child
that launches and returns nonzero (e.g. PK-D's own `BLOCKED`, exit 20) is a **business**
outcome and is `CHILD_NONZERO`, never a scheduler failure, never retried.

| `runner_status` | Meaning | Exit code |
|---|---|---|
| `RUNNING` | transient - written as a checkpoint right after the lock is acquired | - |
| `COMPLETED` | the child launched and exited 0 | `0` |
| `CHILD_NONZERO` | the child launched and exited nonzero - a business outcome, preserved verbatim | the child's own exit code |
| `LOCKED` | the global scheduler lock was still held past the bounded wait | `3` |
| `FAILED` | the child could not even be launched (missing executable, launch exception) | `4` |
| `EXPIRED` | invoked (or finished waiting for the lock) at/after the job's own 1-hour execution-window deadline - the child was deliberately never launched | `0` |

`3`/`4` are deliberately disjoint from `production_orchestrator`'s `{0,20,30,40}` and
`intelligence_refresh`'s `{0,20,30}` - never used for anything except these two infra-only
cases, so a reader can never mistake a scheduler infra code for a business one. `EXPIRED`'s
exit code is deliberately `0`, not one of those infra codes: there is no infrastructure
failure to report (the lock and every cmdlet worked fine) and nothing about a skipped-window
invocation should ever be auto-retried by Task Scheduler - only the `runner_status` field
(`EXPIRED`, never `COMPLETED`) actually distinguishes it from an ordinary successful run; see
the run-record fields below.

## Execution windows (PK-E follow-up, 2026-10-12)

Each job has a strict **1-hour operational execution window**, IST, during which the runner
will even agree to start the child at all:

| job | scheduled | window (start inclusive, end exclusive) |
|---|---|---|
| `intelligence_am` | 06:40 | 06:40-07:40 |
| `pre` | 07:00 | 07:00-08:00 |
| `intelligence_pm` | 19:15 | 19:15-20:15 |
| `post` | 19:30 | 19:30-20:30 |

This is a **different concept** from `ExecutionTimeLimit` (50 minutes, Windows Task
Scheduler's own cap on how long an already-*running* process may keep running) - the window is
about whether the runner will *start* the child at all, not how long it may run once started.
Both are kept, unchanged from each other.

`StartWhenAvailable=$true` still lets Windows Task Scheduler invoke a job late if the machine
was off at the scheduled moment - that setting is unchanged and is **not** how the window is
enforced. The trusted Python runner (`shadow_scheduler/runner.py`,
`config.execution_window(spec, now_ist)`) is what actually refuses to launch the child once
`now_ist >= scheduled_time + 60 minutes`: a job invoked inside its window still launches
exactly as it would on time; a job invoked at/after its deadline becomes `EXPIRED` and the
child is never started. **PK-C remains the final safety authority even inside the window** -
landing inside the window only means the runner will *attempt* to launch; PK-C/PK-D still
decide whether rendering is actually safe at that moment, exactly as before.

The deadline is checked **twice**, since the global scheduler lock sits between "invoked" and
"child launched":

1. Before even attempting the lock (an invocation that's already past its own deadline never
   tries to acquire the lock at all).
2. Immediately before launching the child (a job that started well inside its window can still
   lose the rest of it waiting for another scheduled job to release the lock).

The bounded lock-wait timeout itself is capped at whatever remains of the window
(`min(configured_wait, remaining_window_s)`) so the wait can never carry a job past its own
deadline unnoticed. If the lock is still busy once that capped wait elapses, the outcome is
`EXPIRED` (not `LOCKED`) whenever the deadline has in fact passed by then - the window, not the
lock contention, is what actually stopped it.

No historical PRE/POST production is created to compensate for an expired window - intelligence
refresh self-heals recent data on its own next valid run; a missed/expired PRE or POST is
simply not rendered that day.

## The scheduler lock

**CRITICAL design point**: Windows Task Scheduler's own "do not start a new instance" setting
only protects instances of the *same* task - it does nothing for four *different* tasks
sharing DMB's output tree. `shadow_scheduler/lock.py` adds ONE outer lock
(`output/shadow_scheduler/run.lock`) that intelligence refresh and PRE/POST all acquire before
touching anything, so they can never run concurrently - independent of, and never sharing
code with, PK-D's own per-(edition, session) lock or intelligence_refresh's own module-wide
lock (this repo's established convention: each orchestration layer keeps its own
independently-implemented PID+hostname+liveness lock).

- **Same host:** staleness is decided only by whether the PID is still alive - never by age.
  Windows liveness uses `OpenProcess`/`CloseHandle` (never `os.kill(pid, 0)`, which is unsafe on
  Windows and can actually terminate the process it is only trying to probe).
- **Other host:** liveness can't be verified remotely, so staleness falls back to a 4-hour age
  threshold.
- **Bounded wait** (new here - neither PK-D's nor intelligence_refresh's own lock has this): a
  scheduled job polls every **15 seconds**, up to **15 minutes**, before giving up. Every poll
  is a genuine `acquire()` attempt, so a lock that goes stale mid-wait is reclaimed the moment
  it's found stale, not blindly waited out. On timeout the job records `LOCKED`/BUSY and exits
  (code `3`) - it never kills the holder's process and never deletes a live lock.

## Run records

```
output/shadow_scheduler/runs/<run_id>.json      immutable once terminal
output/shadow_scheduler/latest/<job>.json       mutable pointer, one per job
output/shadow_scheduler/logs/<YYYY-MM-DD>/<job>_<HHMMSS>.log   interleaved stdout+stderr (IST)
```

Fields: `schema_version`, `job`, `run_id`, `started_at`/`completed_at` (UTC - `started_at` is
the actual invocation time), `duration`, `hostname`, `pid`, `repo_path`, `python_executable`,
`child_command` (the literal argv - just the `.bat` absolute path, never a shell string),
`child_exit_code`, `runner_status`, `log_path`, `business_latest_ref` (a path *reference* to
the business package's own `latest.json`, never its content), `reclaimed_stale_lock`, `error`
(free-text diagnostic - a launch-failure message, or the `EXPIRED` reason), plus the execution-
window fields: `scheduled_time` (`"HH:MM"`), `window_start`/`window_end` (the scheduled/deadline
instants, IST ISO-8601), `child_started` (`False` only for `LOCKED`/`FAILED`/`EXPIRED` - no
child process was ever launched).

Write order is correctness-critical, the same rule as every other package here: the immutable
run file is written first (tmp + `os.replace`), **then** the job's `latest/<job>.json` pointer
is updated.

## Status command

```
python -m shadow_scheduler status [--json]
scripts\check_shadow_operations.bat
```

Read-only by construction: `status.py` contains no write-mode `open()` and no `os.replace()`
anywhere in the file (statically enforced, `tests/test_shadow_scheduler_security.py`, not just
behaviorally). It reads this job's own `latest/<job>.json`, plus a plain field-extraction read
of the business package's own `latest.json` - never recomputed.

**PENDING / WAITING / MISSED / NOT_EXPECTED** (per job, today in IST) reuses the real
`core.trading_calendar.SessionCalendar().is_session(today)` - never a second calendar
implementation - plus `config.execution_window()` for the 1-hour window boundary: `PENDING`
before `scheduled_time`, `WAITING` from `scheduled_time` up to (not including) the deadline
with no run yet today, `MISSED` only once the deadline itself has passed with still no run
today. A job inside its window that simply hasn't fired yet (e.g. PRE checked at 07:15, deadline
08:00) is `WAITING`, never `MISSED` - `status` does not call a job missed merely because it
hasn't run yet. `UNKNOWN` (outside the hardcoded holiday-year coverage) is never asserted as
either MISSED or NOT_EXPECTED.

Once a run record exists for today, a job's own `runner_status` (`COMPLETED`/`CHILD_NONZERO`/
`LOCKED`/`FAILED`/`EXPIRED`) always takes display precedence over this `missed_state` - see
`render_human`'s `shown = runner_status or missed_state`.

**HEALTHY / ATTENTION / BROKEN** (overall): a business `BLOCKED`/`FAILED`/`LOCKED`, a scheduler
`LOCKED` or `EXPIRED`, or a `MISSED` job is `ATTENTION` - still a business/operational outcome,
not an infrastructure failure. Only a runner-level `FAILED` (a launch failure) is `BROKEN`.
Task-registration drift (a task missing, disabled, or pointing at the wrong path) is a
**separate** signal, surfaced only by `check_shadow_tasks.ps1` - `status.py` never shells out
to PowerShell or a Task Scheduler API, keeping it read-only in the strictest sense.
"Automation health" is the union of both commands, never one combined signal.

## PowerShell layer (the first `.ps1` scripts in this repo)

| Script | Does | Never does |
|---|---|---|
| `scripts\install_shadow_tasks.ps1` | default / `-WhatIf`: prints the install plan (CREATE/UNCHANGED/UPDATE/COLLISION per task). `-Install`: registers the 4 tasks for real. | mutate anything without `-Install`; install on the wrong timezone without `-SkipTimezoneCheck` (testing only); overwrite a same-named task that isn't provably DMB-owned |
| `scripts\check_shadow_tasks.ps1` | read-only: Exists/Enabled/NextRunTime/LastRunTime/LastTaskResult/action/start-time + drift flags, per task | call any mutating cmdlet |
| `scripts\remove_shadow_tasks.ps1` | default preview; `-Remove`: removes only the 4 DMB-owned tasks, re-verifying ownership first | touch any other task |

Ownership check: a task is provably DMB-owned only when its action's `Execute` ends with
`python.exe`, its `Arguments` contains `-m shadow_scheduler run`, and its path is exactly
`\DMB\<ExpectedName>`. A same-named task that fails this check is reported `COLLISION` and
refuses the *entire* install run (fail closed, not skip-and-continue).

Registration settings: `MultipleInstances=IgnoreNew`, `StartWhenAvailable=$true`,
`ExecutionTimeLimit=PT50M` (50 minutes - comfortably above every observed PRE/POST/refresh
runtime, well under the "hung process" range), `RestartCount=0` (no automatic restart-on-
failure loop - a nonzero result may be an intentional PK-C safety block). Principal: the
current interactive user, `LogonType=Interactive`, no stored password, never SYSTEM.

`check_shadow_tasks.ps1` and `remove_shadow_tasks.ps1` both dot-source
`install_shadow_tasks.ps1` to reuse its comparison/ownership functions directly - there is only
ONE definition of "what the expected task config is" in this repo, never a second table that
could drift out of sync.

## Known limitations (accepted, not solved here)

- The bounded-wait lock is a *process-level* retry loop for a lock conflict only - a job that
  times out waiting is simply `LOCKED` for that invocation; there is no queued retry, and none
  is added (a legitimate PK-C `BLOCKED` must never be retried either).
- `status`'s `BROKEN` classification does not itself detect Task Scheduler drift - run
  `check_shadow_tasks.ps1` separately for that; the two commands together are the full
  picture.
- `StartWhenAvailable=$true` means a missed morning start (PC was off) can still fire late once
  the machine returns, and the runner will launch it normally **as long as it's still inside
  that job's own 1-hour execution window** - PK-C alone decides whether that late-but-in-window
  start is actually safe to render; PK-E never bypasses that decision or encodes any further
  business/market rule of its own. Outside the window, the runner refuses to launch at all
  (`EXPIRED`) - see "Execution windows" above.

## Manual rerun procedure

- Intelligence refresh missed: `scripts\run_intelligence_refresh.bat` (or `python -m
  shadow_scheduler run intelligence_am` / `intelligence_pm`).
- PRE missed but still inside a valid PRE window: `scripts\run_production_pre.bat` (or
  `python -m shadow_scheduler run pre`). If PRE is already past the safe window, do **not**
  force it just to produce a video - let PK-C block it; historical reconstruction is for
  debugging only.
- POST: `scripts\run_production_post.bat` (or `python -m shadow_scheduler run post`). PK-C/PK-D
  decide whether it's valid; nothing here overrides that.

## Testing

`tests/test_shadow_scheduler_*.py` - fully offline, isolated to `tmp_path` (no dependency on
real `output/`, a real Task Scheduler, or a real database), **except**
`test_shadow_scheduler_powershell_syntax.py`, which intentionally runs the real, non-modifying
`install_shadow_tasks.ps1` (default/`-WhatIf`) and `check_shadow_tasks.ps1` against the owner's
actual machine (expecting every task reported `MISSING`, since none is installed). Covers: the
registry/vocabulary/exit-code model, lock acquire/conflict/stale-reclaim/bounded-wait/timeout,
the run record schema and write order, the runner's healthy/`CHILD_NONZERO`/launch-failure/
lock-timeout paths, the CLI, the read-only status aggregation and
PENDING/WAITING/MISSED/NOT_EXPECTED detection, the no-business-logic/no-YouTube architecture
guard, and the PowerShell install planner's CREATE→UNCHANGED idempotency, foreign-task
collision refusal, and wrong-timezone refusal (via plain PowerShell function-shimming of
`Get-ScheduledTask`/`Get-TimeZone` plus default-throwing guard stubs for every real mutating
Task Scheduler cmdlet - no Pester dependency, and no real
`Register-ScheduledTask`/`Unregister-ScheduledTask`/`Set-ScheduledTask`/`Enable-ScheduledTask`/
`Disable-ScheduledTask` call in any automated test; a module-wide fixture independently
re-reads the real `\DMB\` task set before/after the whole PowerShell test module and fails the
suite if it ever changes).

`test_shadow_scheduler_execution_window.py` covers the 1-hour execution window specifically:
exact start/deadline boundaries for all four jobs, the lock-wait-vs-deadline interaction
(a lock wait that would otherwise carry a job past its deadline becomes `EXPIRED`, not
`LOCKED`; a lock that frees up before the deadline still launches normally), a late-but-
in-window invocation launching normally, and the `status` `PENDING`/`WAITING`/`MISSED`
boundary - every clock is injected, no test depends on the real wall clock.

```
python -m pytest tests/test_shadow_scheduler_*.py -q
```

One manual dry run after tests pass: `python -m shadow_scheduler run intelligence_am` for
real (safe - it only invokes the already-approved, non-rendering intelligence refresh), then
`python -m shadow_scheduler status`, confirming the lock file appears and clears, a run record
and log are written, the child's exit code is captured correctly, and status reflects it.

## Installing for real (owner-approved step - never run automatically)

```
powershell -ExecutionPolicy Bypass -File scripts\install_shadow_tasks.ps1 -Install
```

Run this only after reviewing the preview output of the same script without `-Install`. It
refuses outright on a non-IST Windows timezone and on any unproven-ownership task-name
collision. No scheduled task is registered by any packet that produces this code - that
remains this one explicit, owner-run command.
