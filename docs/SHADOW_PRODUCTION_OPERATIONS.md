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
| `dependency.py` | scheduler-level dependency ordering: `intelligence_am` before `pre`, `intelligence_pm` before `post` |
| `runner.py` | the thin runner: dependency wait, lock, launch, capture, record, release |
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
| `RUNNING` | transient - written as a checkpoint right after the lock is acquired (or once the dependency wait resolves) | - |
| `WAITING_DEPENDENCY` | transient - written as a checkpoint while `pre`/`post` is genuinely blocked waiting for its own upstream intelligence job | - |
| `COMPLETED` | the child launched and exited 0 | `0` |
| `CHILD_NONZERO` | the child launched and exited nonzero - a business outcome, preserved verbatim | the child's own exit code |
| `LOCKED` | the global scheduler lock was still held past the bounded wait | `3` |
| `FAILED` | the child could not even be launched (missing executable, launch exception) | `4` |
| `DEPENDENCY_FAILED` | the upstream intelligence job never reached a usable outcome today (its own `FAILED`/`LOCKED`/`EXPIRED`), or the dependency state could not be re-confirmed immediately before the lock/child launch - the child was deliberately never launched | `5` |
| `EXPIRED` | invoked (or finished waiting for the lock, or for the dependency) at/after the job's own 1-hour execution-window deadline - the child was deliberately never launched | `0` |

`3`/`4`/`5` are deliberately disjoint from `production_orchestrator`'s `{0,20,30,40}` and
`intelligence_refresh`'s `{0,20,30}` - never used for anything except these infra-only
cases, so a reader can never mistake a scheduler infra code for a business one. `DEPENDENCY_FAILED`
is nonzero (unlike `EXPIRED`) because, unlike an unremarkable deliberately-skipped window, it is
worth a look - but it must never trigger a Windows Task Scheduler retry; `RestartCount=0` is
already configured for all 4 tasks (see "PowerShell layer" below). `EXPIRED`'s exit code is
deliberately `0`, not one of those infra codes: there is no infrastructure failure to report
(the lock and every cmdlet worked fine) and nothing about a skipped-window invocation should
ever be auto-retried by Task Scheduler - only the `runner_status` field (`EXPIRED`, never
`COMPLETED`) actually distinguishes it from an ordinary successful run; see the run-record
fields below.

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

## Dependency ordering (PK-E follow-up)

**The wake-up race this solves**: on a laptop that was off overnight, `StartWhenAvailable` can
fire `intelligence_am` and `pre` (or `intelligence_pm` and `post`) almost simultaneously on
resume. The scheduler lock alone only guarantees the two don't *overlap* - it says nothing
about *order*. If `pre` happened to run to completion before `intelligence_am` had self-healed
the latest canonical REPORT, PK-C's readiness gate could legitimately `BLOCK` `pre` even though
`intelligence_am` would have fixed the underlying state moments later. This is scheduler-level
ordering only - **PK-C remains the sole authority on whether a render is actually safe.**

A small static, trusted map (`config.DEPENDENCIES`), never derived from data/config/content:

```
pre              -> intelligence_am
post             -> intelligence_pm
intelligence_am  -> (no dependency)
intelligence_pm  -> (no dependency)
```

`shadow_scheduler/dependency.py` answers exactly one question - "has TODAY's upstream
invocation reached a terminal, attempted outcome?" - by reading shadow_scheduler's OWN run
records for the upstream job (never business state: the canonical report, readiness, Radar,
Market Structure, publication status are never inspected here).

- **Satisfied**: upstream's `runner_status` is `COMPLETED` or `CHILD_NONZERO` *today* (IST). A
  nonzero business exit code (e.g. PK-D's own `BLOCKED=20`) still counts - "the refresh ATTEMPT
  finished" is all this layer asks; duplicating PK-C's business judgement here is exactly what
  must not happen.
- **Failed** (never satisfies, and never worth waiting out): upstream's `runner_status` is
  `FAILED`, `LOCKED`, or `EXPIRED` *today* - a terminal, non-satisfying attempt. If `pre`/`post`
  sees this immediately (e.g. `intelligence_am` was itself invoked after its own 07:40 deadline
  and reached `EXPIRED` at 07:45, while `pre` still has until 08:00), it stops right away rather
  than waiting out the rest of its own window pointlessly.
- **Pending** (keep waiting): no record for today, a record from a **prior day** (current-day
  only - yesterday's completion never satisfies today's dependency), or still `RUNNING`/
  `WAITING_DEPENDENCY`.

**Critical lock rule**: the wait happens entirely **before** the global scheduler lock is even
attempted - a job must never wait for its dependency *while holding* that lock, or the two jobs
could starve each other (the dependency can only ever acquire the lock once the waiting job
gives up or releases it). `dependency.wait()` never touches `lock.py` at all.

**Bounded by the waiting job's own execution-window deadline**, never indefinite: each poll
iteration checks the deadline *first* - even if the dependency becomes `SATISFIED` at the exact
instant the deadline is reached, this is still `TIMED_OUT` → `EXPIRED`, never a late launch. The
window's deadline authority always wins. A dependency that only finishes after the dependent's
own deadline (e.g. `intelligence_am` finishes at 08:01, after PRE's 08:00 cutoff) means `pre`
simply never launches that day - no extension, no fallback to a stale prior refresh.

Checked at **three points** (state can change while waiting or acquiring the lock): once before
any wait, once immediately before attempting the lock, and once immediately before the child
actually launches (lock already held). The second and third checks are cheap, non-blocking
re-validations, not a renewed wait - any regression at that late stage (in practice only a
manually re-run upstream job mid-window) is treated as `DEPENDENCY_FAILED`, fail-closed, rather
than opening a second nested wait loop.

**No retry, no second scheduler**: `pre`/`post` never invoke
`scripts\run_intelligence_refresh.bat` themselves - the upstream job remains its own
independently scheduled Task Scheduler entry; the dependent only waits/observes. A manual
`python -m shadow_scheduler run pre` obeys the same ordering as the scheduled task (both call
the same `runner.run_job`) - there is no bypass flag in V1.

**Healthy daily operation** (the common case - refresh already completed before the dependent
job starts) costs one or two cheap local JSON reads, no sleep, no network call, negligible
latency.

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
instants, IST ISO-8601), `child_started` (`False` only for `LOCKED`/`FAILED`/`EXPIRED`/
`DEPENDENCY_FAILED` - no child process was ever launched), plus the dependency-ordering fields
(all `None` for `intelligence_am`/`intelligence_pm`, which have no dependency):
`dependency_job` (e.g. `"intelligence_am"`), `dependency_status` (`"SATISFIED"`/`"FAILED"`/
`"TIMED_OUT"`/`None`), `dependency_run_id` (the upstream job's own `run_id`, once `SATISFIED`),
`dependency_wait_seconds`, `dependency_satisfied_at` (UTC ISO-8601, only when `SATISFIED`).

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

Once a run record exists **for today** (PK-E follow-up, 2026-10-16), a job's own
`runner_status` (`COMPLETED`/`CHILD_NONZERO`/`LOCKED`/`FAILED`/`EXPIRED`/`WAITING_DEPENDENCY`/
`DEPENDENCY_FAILED`) always takes display precedence over this `missed_state` - see
`render_human`'s `shown = runner_status or missed_state`. A `pre`/`post` job genuinely waiting
on its upstream intelligence job shows `WAITING_DEPENDENCY` live (not a generic `WAITING`)
while the wait is in progress.

### TODAY vs HISTORICAL (PK-E follow-up, 2026-10-16)

`job_status()` only treats `runner_status`/`business_status` as belonging to today when that
record's own `started_at` (IST; the business side falls back to `completed_at`) converts to
TODAY's calendar date - never simply "the most recent record, from whichever day it's from".
This fixed a real defect: before this change, `status` read each job's single most-recent
record with no date filter at all, so at 07:30 IST it could show `Intelligence PM`/`POST` as
`COMPLETED business=SUCCESS` from **yesterday evening**, hours before today's own 19:15/19:30
runs. The raw historical value is still available under each job's `last_run` field (date,
`runner_status`, `business_status`) for a separate "most recent result" view - it must never be
read as today's operational state, and `overall_status()`'s HEALTHY/ATTENTION/BROKEN counters
are computed only from the today-filtered fields, so a stale prior-day record can never inflate
them (e.g. PM/POST still `PENDING` at 07:30 IST must not push health to `ATTENTION`).

One further nuance: `intelligence_am` and `intelligence_pm` share ONE
`intelligence_refresh/latest.json` (one module, invoked on two separate schedules) - showing
"today's" business result for either of them also requires THAT job's own scheduler record to
be from today, not just the shared file's timestamp. Otherwise the morning AM run's `SUCCESS`
would bleed into the PM row's `business=` before the evening PM run has even happened.

`render_human()`'s table now shows each job's actual run time (`ran=HH:MM` IST, or `-` if it
hasn't run today) beside its scheduled time:

```
DMB SHADOW OPERATIONS
--------------------------------------------------------------
Intelligence AM    COMPLETED   sched=06:40 ran=07:36  SUCCESS
PRE                COMPLETED   sched=07:00 ran=07:36  DEGRADED
Intelligence PM    PENDING     sched=19:15 ran=-
POST               PENDING     sched=19:30 ran=-
--------------------------------------------------------------
```

**HEALTHY / ATTENTION / BROKEN** (overall): a business `BLOCKED`/`FAILED`/`LOCKED`, a scheduler
`LOCKED`/`EXPIRED`/`DEPENDENCY_FAILED`, or a `MISSED` job is `ATTENTION` - still a business/
operational outcome, not an infrastructure failure. `WAITING_DEPENDENCY` is deliberately
**not** an ATTENTION trigger - an in-flight wait is not itself unhealthy, the same treatment the
ordinary `RUNNING` checkpoint already gets. Only a runner-level `FAILED` (a launch failure) is
`BROKEN`.
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

**UPDATE never unregisters first (PK-E follow-up, 2026-10-16).** A real owner `-Install` once
hit the parameter-name bug above while building the replacement settings object - but by then
the previous version of this script had already unregistered the existing, live
`DMB-Shadow-Intelligence-AM` task, so the construction failure left it genuinely `MISSING`
instead of merely drifted. `New-ShadowScheduledTask` now builds the FULL replacement (action,
trigger, settings, principal) first; only once every step has succeeded does it call
`Register-ScheduledTask -Force`, which replaces an existing same-path task atomically with no
separate `Unregister-ScheduledTask` call and therefore no window where the old task is gone
but the new one isn't registered yet. A construction or registration failure during an UPDATE
now leaves the pre-existing task exactly as it was.

Registration settings: `MultipleInstances=IgnoreNew`, `StartWhenAvailable=$true`,
`ExecutionTimeLimit=PT50M` (50 minutes - comfortably above every observed PRE/POST/refresh
runtime, well under the "hung process" range), `RestartCount=0` (no automatic restart-on-
failure loop - a nonzero result may be an intentional PK-C safety block), plus the power policy
below. Principal: the current interactive user, `LogonType=Interactive`, no stored password,
never SYSTEM.

**Cmdlet parameter names vs. live settings property names (PK-E follow-up, 2026-10-16) -
these are NOT the same surface, and a real owner `-Install` broke on exactly this.**
`Get-Command New-ScheduledTaskSettingsSet -Syntax` on this machine's ScheduledTasks module
shows plain switches `-AllowStartIfOnBatteries` / `-DontStopIfGoingOnBatteries` / `-WakeToRun`
- there is no `-DisallowStartIfOnBatteries` or `-StopIfGoingOnBatteries` parameter on this
cmdlet at all. `install_shadow_tasks.ps1` passes those three switches unconditionally (the
policy is fixed, never varies per task). Once registered, the resulting **live task's own
`.Settings` object** exposes the opposite-named properties -
`DisallowStartIfOnBatteries=$false`, `StopIfGoingOnBatteries=$false`, `WakeToRun=$true` - which
is what `Compare-ShadowTask`'s drift check actually reads back and compares (that part was
always correct; only the construction call used the wrong names). Never assume a cmdlet
accepts a parameter with the same name as the object property it produces - check
`Get-Command -Syntax` against the actual local module first.

### Power policy (battery / sleep / wake) - PK-E follow-up, 2026-10-16

Root cause of a real miss: on 2026-10-16 the Intelligence AM / PRE jobs did not run while the
laptop was on battery, because `New-ScheduledTaskSettingsSet` was never given explicit power
settings and so fell back to the cmdlet's own defaults - `DisallowStartIfOnBatteries=$true`,
`StopIfGoingOnBatteries=$true`, `WakeToRun=$false`. The four DMB tasks now explicitly request
the opposite: DMB is a lightweight scheduled workflow and is allowed to run inside its own
one-hour execution window even on battery. The execution windows themselves (AM 06:40-07:40,
PRE 07:00-08:00, PM 19:15-20:15, POST 19:30-20:30) remain the actual safety boundary - no new
retry/reconnect logic is added; `StartWhenAvailable` plus the existing window is the whole
mechanism.

What these settings can and cannot do:

- **SLEEP**: the task may wake the machine if Windows' own power plan and the hardware's wake
  timers permit it (`WakeToRun=$true` asks Task Scheduler to try; it is not a guarantee on
  every machine/BIOS).
- **HIBERNATE**: depends on the same machine/Windows wake-timer support as sleep - not
  guaranteed either.
- **FULL SHUTDOWN**: Task Scheduler cannot power on a shut-down laptop under any setting here.
  `StartWhenAvailable=$true` only means the task runs after the laptop is next booted by hand,
  and only if the job's own execution window (above) is still open at that moment - a task
  whose window has already closed is simply skipped, exactly as today.

`Compare-ShadowTask`'s `CONFIG_DRIFT` check now also compares these three settings against the
live task, so a task installed before this change shows `CONFIG_DRIFT` in both
`install_shadow_tasks.ps1`'s preview and `check_shadow_tasks.ps1` until the owner re-runs the
installer with `-Install` (see "Installing for real" below) - this packet never does that
automatically.

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
- No developer bypass flag for dependency ordering in V1 (deliberate) - a manual
  `python -m shadow_scheduler run pre` during a genuine dependency wait will actually block for
  up to its own window's remaining time, the same as the scheduled task.
- If the `pre`/`post` process is killed mid-dependency-wait (not mid-lock), its
  `WAITING_DEPENDENCY` checkpoint is left stuck until the next day's run overwrites it - the
  same latent class of gap the ordinary `RUNNING` checkpoint already has if a process is killed
  right after acquiring the lock but before the terminal record is written; not a new
  regression, just inherited.

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
actual machine (each task reported `MISSING` before the owner's first real `-Install`, or
`Exists=Yes` afterward - the test asserts read-only behavior only, never which state it finds).
Covers: the
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

`test_shadow_scheduler_dependency.py` covers `dependency.check()`/`wait()` in isolation: every
`runner_status` mapping (`SATISFIED`/`FAILED`/`PENDING`), current-day-only semantics (a
prior-day record is rejected), immediate-satisfy with zero overhead, poll-then-satisfy/
poll-then-fail (a fake `sleep` mutates the dependency's own run record mid-wait, the same idiom
`test_shadow_scheduler_lock.py` uses for "lock goes stale mid-wait"), and the exact-boundary
cases (satisfied strictly before the deadline vs. the deadline winning even when the dependency
completes at the same instant). The `# --- dependency ordering ---` section of
`test_shadow_scheduler_runner.py` covers the full `run_job()` integration end to end: the
morning/evening wake-up races (proving the global lock is acquirable by a second `RunLock`
instance *while* the dependent job is mid-wait), every upstream terminal state
(`CHILD_NONZERO`→proceeds, `FAILED`/`EXPIRED`→`DEPENDENCY_FAILED` fast, never waiting out the
rest of the window), and the window-deadline interaction.

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
