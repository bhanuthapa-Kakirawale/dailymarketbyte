# Shadow Production Operations V1 (PK-E)

```
python -m shadow_scheduler run intelligence_am
python -m shadow_scheduler run pre
python -m shadow_scheduler run intelligence_pm
python -m shadow_scheduler run post
python -m shadow_scheduler status [--json]       # read-only, no side effects
python -m shadow_scheduler audit [--from D] [--to D] [--json]   # read-only window replay
python -m shadow_scheduler reconcile [--json]    # DRY RUN: classify orphaned checkpoints
python -m shadow_scheduler reconcile --execute   # owner-approved only: append-only markers
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
| `config.py` | the static, trusted job registry (`JOB_REGISTRY`), lock/schedule constants, and `resolve_occurrence` (the one invocation-attribution rule) |
| `lock.py` | the scheduler-wide run lock (PID + hostname + liveness) + bounded wait |
| `dependency.py` | scheduler-level dependency ordering: `intelligence_am` before `pre`, `intelligence_pm` before `post` |
| `runner.py` | the thin runner: dependency wait, lock, launch, capture, record, release |
| `records.py` | reading/writing shadow_scheduler's OWN run records + `latest/<job>.json` + append-only `reconciliations/<run_id>.json` markers |
| `orphans.py` | read-only classification of non-terminal checkpoints (ACTIVE/ABANDONED/UNVERIFIABLE/RECONCILED/TERMINAL) |
| `reconcile.py` | dry-run-first, idempotent writer of ABANDONED markers (via `records.write_reconciliation`) |
| `audit.py` | read-only replay of existing run records under the current occurrence rule |
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
| `FAILED` | the child could not even be launched (missing executable, launch exception), **or** the runner itself hit an unexpected exception after its run record existed (PK-OPS-1: `run_job` finalizes it as `FAILED` with the exception text in `error` and releases the global lock, instead of leaving a `RUNNING` checkpoint and a stale lock) | `4` |
| `DEPENDENCY_FAILED` | the upstream intelligence job reached a terminal non-satisfying outcome for the same occurrence (its own `FAILED`/`LOCKED`/`EXPIRED`/`DEPENDENCY_FAILED`, or an abandoned checkpoint), or the dependency state could not be re-confirmed immediately before the lock/child launch - the child was deliberately never launched | `5` |
| `EXPIRED` | a LATE invocation (attributed to a past occurrence whose window had already closed), or the window / runner wait budget closed while waiting for the dependency or the lock - the child was deliberately never launched | `0` |
| `EARLY` | (PK-OPS-1) invoked before its occurrence's `window_start`, within `EARLY_ATTRIBUTION_MINUTES` of it - rejected, nothing waited for, nothing rescheduled, never a successful scheduled execution | `0` |
| `ABANDONED` | (PK-OPS-1) **never written by the runner.** The effective status of a `RUNNING`/`WAITING_DEPENDENCY` checkpoint whose runner is verifiably gone, recorded only by an append-only `reconciliations/<run_id>.json` marker; the run file itself is never rewritten. Never a success - nothing is claimed about what the child did | - |

`ORPHAN_SUSPECTED` is a **display value only** (`orphans.effective_status`), never stored in a
run record: an unreconciled checkpoint that `orphans.classify` proves ABANDONED right now. It
becomes `ABANDONED` only once an owner-approved `reconcile --execute` writes the marker.
`RUNNING`/`WAITING_DEPENDENCY` are the only non-terminal statuses (`NON_TERMINAL_STATUSES`).

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
fields below. `EARLY` exits `0` for the same reason.

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
was off, asleep or hibernating at the scheduled moment - that setting is unchanged and is
**not** how the window is enforced. The trusted Python runner is. **PK-C remains the final
safety authority even inside the window** - landing inside the window only means the runner
will *attempt* to launch; PK-C/PK-D still decide whether rendering is actually safe at that
moment, exactly as before.

### Occurrence attribution (PK-OPS-1, 2026-10-24)

Every invocation is attributed to exactly ONE scheduled occurrence - (job, IST
`scheduled_date`) - by `config.resolve_occurrence(spec, now)`, the one rule the runner enforces
and `status`/`audit`/`orphans` report with:

| class | rule | runner outcome |
|---|---|---|
| `IN_WINDOW` | `window_start <= now < window_end` of an occurrence on a scheduled weekday | may proceed (dependency, lock, rechecks) |
| `EARLY` | before the NEXT occurrence's `window_start` by at most `EARLY_ATTRIBUTION_MINUTES` (= 60) | `EARLY`, attributed to that next occurrence, no child |
| `LATE` | anything else outside a window | `EXPIRED`, attributed to the most recent PAST occurrence whose window has closed, no child |

Task Scheduler triggers never fire *before* their start time, so an `EARLY` invocation is a
manual run or clock skew just ahead of the window; everything further away belongs to the past
occurrence it is catching up on (a `StartWhenAvailable` catch-up of last night's 19:30 POST at
06:48 is LATE for last night, never "before tonight's deadline"; Friday's job fired on Saturday
or Monday morning is LATE for Friday). Occurrences exist on `JobSpec.weekdays` only (Mon-Fri) -
deliberately **not** the NSE session calendar: the tasks fire Mon-Fri and the children
themselves already SKIP a holiday, so session semantics are unchanged.

`config.execution_window(spec, now_ist)` still exists, but only answers "where is the window on
`now_ist`'s own date" (used by `status`'s PENDING/WAITING/MISSED); it is never the launch test.
`window_start`/`window_end` in a run record are the ATTRIBUTED occurrence's window.

### Window rechecks and the runner wait budget

The window (start AND end of the same attributed occurrence - never re-attributed mid-run) is
checked at the invocation, and again:

1. after the dependency wait, immediately before attempting the global lock (an upstream that
   only became SATISFIED at/after this job's deadline never makes it eligible);
2. when a lock wait times out (`EXPIRED`, not `LOCKED`, if the window/budget has closed by
   then - the deadline, not the lock contention, is what stopped it);
3. immediately before launching the child (the lock may have been acquired only after a wait
   that consumed the rest of the window).

Every wait (dependency and lock) ends at `min(window_end, runner_start +
RUNNER_WAIT_BUDGET_SECONDS)`. The budget is 45 minutes - Task Scheduler's `ExecutionTimeLimit`
(`PT50M`, `EXECUTION_TIME_LIMIT_SECONDS`) minus 5 minutes of margin - so a runner always
writes its own terminal record before Task Scheduler can terminate it (a terminated runner's
`finally` never runs). It never changes a schedule or a window, only how long a runner waits;
it only binds for an invocation in the first 15 minutes of its window. An exhausted budget is
`EXPIRED` with the budget named in `error`.

No historical PRE/POST production is created to compensate for an expired window - intelligence
refresh self-heals recent data on its own next valid run; a missed/expired PRE or POST is
simply not rendered that day.

### Incident 2026-10-21 / 2026-10-24 (root cause of PK-OPS-1)

From read-only forensics, 2026-10-24:

- **Root cause.** The old `config.execution_window` built the window on the INVOCATION's own
  calendar date, and `runner.py` only checked `now >= deadline` - no `now >= window_start`, no
  weekday check. Task Scheduler's `StartWhenAvailable` fires missed triggers late (the tasks
  have no `EndBoundary`).
- **2026-10-21 06:48:44 IST.** Tue 10-20's missed 19:15/19:30 triggers fired after the laptop
  resumed from hibernation (10-20 11:05 -> 10-21 06:42). They compared themselves against
  10-21's 19:15/19:30 window, passed "before the deadline", and proceeded. (10-20 was an NSE
  holiday.)
- **2026-10-24 (Sat) 08:29:10 IST.** Task Scheduler launched PM/POST at Fri 20:30:37 (already
  past both deadlines); the process was frozen by hibernation, resumed Saturday 08:29, computed
  a Saturday window and launched. POST rendered session 2026-10-23 (DEGRADED).
- **Orphans left behind.** `20261019T013855Z_5b89936c` (intelligence_am `RUNNING`, pid 25160,
  no log, child never launched), `20261021T011844Z_d675f122` (intelligence_pm `RUNNING`, pid
  14996, no log), `20261021T011844Z_8937f41d` (post `WAITING_DEPENDENCY`, pid 8380). Most
  likely cause of the two `RUNNING` ones: a Windows sharing violation - `os.replace` onto
  `latest/<job>.json` raises `PermissionError` when the paired dependent job (launched in the
  same millisecond) has it open; that exception was uncaught between the `RUNNING` checkpoint
  and the child launch, leaving `RUNNING` + a stale lock. Reproduced in a scratch script, but
  not proven (the runner's stderr was not captured and the TaskScheduler/Operational log is
  disabled). The `WAITING_DEPENDENCY` one was most likely terminated by the `PT50M`
  `ExecutionTimeLimit` while waiting (dependency waits could then run up to 60 minutes). A
  missing log file is NORMAL for any record whose child never launched (`EXPIRED`/`EARLY`/
  `DEPENDENCY_FAILED`/waiting), because `log_path` is assigned at record creation.
- **Manual POST runs with no scheduler record** (10-21 21:17, 10-22 19:59, 10-23 07:14 with
  `--session-date 2026-10-22`) are separate operational events and are never counted as
  scheduled execution.
- **Tasks unchanged.** The 4 live tasks were inspected read-only (`StartWhenAvailable=True`,
  `AllowStartIfOnBatteries`, `DontStopIfGoingOnBatteries`, `WakeToRun=True`,
  `ExecutionTimeLimit PT50M`, `IgnoreNew`, `RestartCount 0`, Mon-Fri). No reinstall is needed;
  the Python runner defends itself.
- **Fixes.** Occurrence attribution + start-of-window/weekday checks (above); the runner wait
  budget; crash finalization (`FAILED`, lock released); `os.replace` retry on
  `PermissionError` with a per-process tmp file (see "Run records"); child-launch checkpoints;
  orphan classification + append-only reconciliation (below).
- **Incident replay** (on a byte copy): equivalent 06:48 and Saturday 08:29 invocations now end
  `EXPIRED` with no child, attributed to 2026-10-20 / 2026-10-23; the three orphans classify
  `ABANDONED`; `reconcile --execute` writes 3 markers, a second pass reports
  `ALREADY_RECONCILED` x3, run files and latest pointers stay byte-identical, and the
  production fingerprint is unchanged. **Production records were NOT reconciled** - that
  requires a separate owner-approved `python -m shadow_scheduler reconcile --execute`.

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

`shadow_scheduler/dependency.py` answers exactly one question - "has the upstream invocation
for THIS occurrence reached a terminal, attempted outcome?" - by reading shadow_scheduler's OWN
run records for the upstream job (never business state: the canonical report, readiness, Radar,
Market Structure, publication status are never inspected here).

**Occurrence matching (PK-OPS-1).** "This occurrence" is the waiting job's own attributed
`scheduled_date`, never the wall-clock date. An upstream record counts only if IT is attributed
to the same `scheduled_date` (its stored field, or - for a record written before that field
existed - `resolve_occurrence` applied to its `started_at`). Before this change the match was
"upstream started on the same wall-clock date", which let last night's PM job fired at 06:48
satisfy last night's POST fired at 06:48. An `EARLY` upstream record is ignored entirely (the
real in-window run may still come).

- **Satisfied**: an upstream record for the occurrence is `COMPLETED` or `CHILD_NONZERO`. A
  nonzero business exit code (e.g. PK-D's own `BLOCKED=20`) still counts - "the refresh ATTEMPT
  finished" is all this layer asks; duplicating PK-C's business judgement here is exactly what
  must not happen.
- **Failed** (never satisfies, and never worth waiting out): the upstream record is `FAILED`,
  `LOCKED`, `EXPIRED` or `DEPENDENCY_FAILED`, or an abandoned checkpoint (effective status
  `ABANDONED` / `ORPHAN_SUSPECTED` - it will never finish, so it fails fast instead of being
  waited on until the deadline). If `pre`/`post` sees this immediately (e.g. `intelligence_am`
  was itself invoked after its own 07:40 deadline and reached `EXPIRED`, while `pre` still has
  until 08:00), it stops right away rather than waiting out the rest of its own window.
- **Pending** (keep waiting): no record for the occurrence, a record attributed to another
  occurrence (yesterday's completion never satisfies today's dependency), or still `RUNNING`/
  `WAITING_DEPENDENCY` with a runner that is not provably gone.

**Critical lock rule**: the wait happens entirely **before** the global scheduler lock is even
attempted - a job must never wait for its dependency *while holding* that lock, or the two jobs
could starve each other (the dependency can only ever acquire the lock once the waiting job
gives up or releases it). `dependency.wait()` never touches `lock.py` at all.

**Bounded by the waiting job's own execution-window deadline** (and the runner wait budget,
whichever is first), never indefinite: each poll
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
output/shadow_scheduler/reconciliations/<run_id>.json   append-only orphan marker (PK-OPS-1)
```

`log_path` is assigned when the record is created, but the log file is only opened when the
child launches - so a record whose child never launched (`EXPIRED`/`EARLY`/`DEPENDENCY_FAILED`/
`LOCKED`, or a checkpoint that died before launch) normally has no log file.

Fields: `schema_version`, `job`, `run_id`, `started_at`/`completed_at` (UTC - `started_at` is
the actual invocation time), `duration`, `hostname`, `pid`, `repo_path`, `python_executable`,
`child_command` (the literal argv - just the `.bat` absolute path, never a shell string),
`child_exit_code`, `runner_status`, `log_path`, `business_latest_ref` (a path *reference* to
the business package's own `latest.json`, never its content), `reclaimed_stale_lock`, `error`
(free-text diagnostic - a launch-failure message, or the `EXPIRED` reason), plus the execution-
window fields: `scheduled_time` (`"HH:MM"`), `window_start`/`window_end` (the scheduled/deadline
instants, IST ISO-8601), `child_started` (`False` only for `LOCKED`/`FAILED`/`EXPIRED`/
`EARLY`/`DEPENDENCY_FAILED` - no child process was ever launched; on a non-terminal checkpoint
it is only the default, never evidence), plus the dependency-ordering fields
(all `None` for `intelligence_am`/`intelligence_pm`, which have no dependency):
`dependency_job` (e.g. `"intelligence_am"`), `dependency_status` (`"SATISFIED"`/`"FAILED"`/
`"TIMED_OUT"`/`None`), `dependency_run_id` (the upstream job's own `run_id`, once `SATISFIED`),
`dependency_wait_seconds`, `dependency_satisfied_at` (UTC ISO-8601, only when `SATISFIED`),
plus (PK-OPS-1) the occurrence identity `scheduled_date` (IST `YYYY-MM-DD` of the attributed
occurrence) and `invocation` (`IN_WINDOW`/`EARLY`/`LATE`), and the child-launch checkpoints
`child_phase` (`NOT_LAUNCHED` -> `LAUNCHING`, checkpointed immediately before the launch, a
child may exist from here -> `LAUNCHED`, PID known), `child_pid` and `child_started_at` (UTC).
The child is started with `subprocess.Popen` (fixed argv, no shell) so its PID can be
checkpointed in a second `RUNNING` write *before* the runner waits on it. Records written
before PK-OPS-1 have none of these; readers infer `scheduled_date`/`invocation` from
`started_at` with the same `resolve_occurrence` rule (`orphans.attribution`).

Write order is correctness-critical, the same rule as every other package here: the immutable
run file is written first (tmp + `os.replace`), **then** the job's `latest/<job>.json` pointer
is updated. The tmp file is per-process (`<path>.<pid>.tmp`), so two writers never share one,
and `os.replace` is retried (40 x 50 ms) on `PermissionError`: Windows refuses to replace a file
another process has open at that instant (e.g. a dependent job reading `latest/<job>.json` in
the same millisecond). A persistent failure still raises - and is then finalized as `FAILED` by
the runner's crash handler.

`reconciliations/<run_id>.json` is exclusive-create (`O_CREAT|O_EXCL`): a second write for the
same run is a no-op, never an overwrite. Writing one never touches the run file or the latest
pointer.

## Orphaned run records and reconciliation (PK-OPS-1, 2026-10-24)

A `RUNNING`/`WAITING_DEPENDENCY` checkpoint is left behind whenever the runner process dies
before its own `finally` writes the terminal record - a hard kill (Task Scheduler's
`ExecutionTimeLimit`), a power loss, or a shutdown mid-run. `shadow_scheduler/orphans.py`
classifies such records **read-only** (no write-mode `open`, no `os.replace`, no process
signal):

| classification | meaning |
|---|---|
| `TERMINAL` | the record is already terminal - nothing to do |
| `RECONCILED` | an append-only marker already exists for this run |
| `ACTIVE` | provably still running: the runner PID is alive and is the same process, or the runner is gone but its recorded `child_pid` is still running |
| `ABANDONED` | proven gone (rules below) |
| `UNVERIFIABLE` | cannot be proven either way - another host, a PID whose liveness/identity can't be read, an unrecognised status, or a run too recently active |

`ABANDONED` needs positive evidence, never age or a missing log alone:

1. the record was written on **this host** (a cross-host PID can't be checked -> `UNVERIFIABLE`);
2. its runner PID is verifiably gone - not running, or **reused**: Windows reuses PIDs, so a
   live process whose creation time (`GetProcessTimes`) is more than
   `PID_IDENTITY_SLACK_SECONDS` (5 s) after the record's own start is a different process.
   Probing uses `OpenProcess`/`GetExitCodeProcess` only, never `os.kill(pid, 0)`; only "no such
   process" counts as dead (access denied is `UNVERIFIABLE`);
3. no child it may have launched can still be running:
   - `WAITING_DEPENDENCY` never launches a child, by construction;
   - `RUNNING` with a recorded `child_pid`: that PID must be gone/reused too (alive -> `ACTIVE`);
   - `RUNNING` with `child_phase=NOT_LAUNCHED`: the runner died before its `LAUNCHING`
     checkpoint, so no child exists;
   - `RUNNING` with no `child_pid` (a pre-PK-OPS-1 record, or `LAUNCHING` without a PID): the
     child is unknowable, so the run must also have been quiet - no start and no log write -
     for `LEGACY_QUIET_SECONDS` (2 h, > 2x the 50-minute `ExecutionTimeLimit` and every
     observed runtime). A missing log is one input to this, **never sufficient by itself**.

A stale global lock that still names an abandoned run is reported as evidence only; the next
runner's own `acquire()` reclaims it. A business `latest.json` started after the record is
shown as evidence only - never used to claim the child succeeded.

```
python -m shadow_scheduler reconcile [--json]            # DRY RUN (default): writes nothing
python -m shadow_scheduler reconcile --execute [--json]  # owner-approved only
```

- **Dry run first.** Without `--execute` every record is classified and listed
  (`WOULD_RECONCILE` / `ALREADY_RECONCILED` / `LEFT_ALONE`); nothing is written.
- **`--execute` is owner-approved only** - never run automatically by any packet or task. It
  re-classifies each record against the file as it is at that moment and writes a marker only
  for one still proven `ABANDONED`; `ACTIVE`/`UNVERIFIABLE` are always left alone, so a
  genuinely running job can never be reconciled.
- **Append-only.** The marker (`reconciliations/<run_id>.json`) records `effective_status:
  ABANDONED`, the original status/start, the original record's SHA-256, `abandoned_kind`, the
  reasons and the evidence. The original run file and the latest pointer are never rewritten.
- **Idempotent.** A second pass finds the marker and reports `ALREADY_RECONCILED` - never a
  duplicate, never an overwrite.
- **Never a success.** `ABANDONED` says nothing about what the child did. A later manual
  replay is its own run record, never merged into this one.
- **Never** touches the global lock, kills or signals a process, or launches anything.
  `reconcile` always exits 0.

```
python -m shadow_scheduler audit [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]
```

`audit` is a read-only replay of every run record (filtered by IST invocation date) under the
CURRENT occurrence rule: what the runner recorded at the time next to what `resolve_occurrence`
says now, and a verdict - `IN_WINDOW`, `CORRECTLY_REJECTED` (out of window, no child),
`OUT_OF_WINDOW_EXECUTION` (out of window but the pre-fix runner launched the child - the
2026-10-21 06:48 / 2026-10-24 08:29 defect), `OUT_OF_WINDOW_ATTEMPT` (out of window, the
pre-fix runner only got as far as a checkpoint) - plus the orphan classification of every
non-terminal record. It never changes a record, a lock or a task; exits 0.

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
`DEPENDENCY_FAILED`, or the effective `ORPHAN_SUSPECTED`/`ABANDONED`; "for today" means an
in-window record attributed to today's occurrence, see below) always takes display precedence over this `missed_state` - see
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

### Today's run = an in-window record for today's occurrence (PK-OPS-1, 2026-10-24)

"Today's run" is now a record **attributed to today's scheduled occurrence whose invocation was
`IN_WINDOW`** (stored `scheduled_date`/`invocation`, or `resolve_occurrence` applied to a legacy
record's `started_at`). Merely *starting* today is not enough: last night's 19:30 POST fired at
06:48 belongs to last night (LATE -> `EXPIRED`), and a 19:14 manual PM run is `EARLY` - neither
is ever counted as today's scheduled execution. A today's checkpoint whose runner is verifiably
gone shows `ORPHAN_SUSPECTED` (or `ABANDONED` once reconciled), never a healthy `RUNNING`.
Point-in-time: a record invoked after the `now` passed in does not exist yet for that `now`, so
a status taken at a past instant (e.g. an incident replay) stays honest.

Per job, additionally:

- `rejected_invocations` - every record of the job **invoked today** (IST, up to `now`) outside
  any window, whichever occurrence it belongs to, with `kind`: `EARLY` / `EXPIRED` (correctly
  rejected), `OUT_OF_WINDOW_ATTEMPT` (a pre-fix runner got as far as a checkpoint; no child
  provably ran), `OUT_OF_WINDOW_EXECUTION` (a pre-fix runner launched the child). Any entry
  pushes overall to `ATTENTION`.
- `previous_occurrence` - the most recent occurrence whose window has closed: its `state`
  (`OK`/`MISSED`/`NOT_EXPECTED`/`UNKNOWN`), runner status and run id. **Information only** -
  like `last_run`, it never feeds today's counters or the overall verdict.

Overall, `orphan_records` lists every unresolved non-terminal record on disk, **any day**: one
classified `ABANDONED`, or `UNVERIFIABLE` and older than `LEGACY_QUIET_SECONDS` (a fresh
in-flight record that merely can't be probed is not an orphan). It is `ATTENTION` until an
owner-approved `reconcile --execute` records it.

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

Each rejected invocation is printed under its job (`- invoked 06:48 for the 2026-10-20
occurrence: rejected LATE (EXPIRED)`), a `MISSED` previous occurrence is printed as an
information-only line, and the footer counters are: `Missed tasks`, `Lock conflicts`, `Expired
windows`, `Dependency failures`, `Child nonzero`, `Abandoned today` (`ABANDONED` or
`ORPHAN_SUSPECTED`), `Early rejections`, `Late rejections` (every non-EARLY rejected
invocation), `Unresolved orphans` (with each orphan listed and a pointer to `reconcile`).

**HEALTHY / ATTENTION / BROKEN** (overall): a business `BLOCKED`/`FAILED`/`LOCKED`, a scheduler
`LOCKED`/`EXPIRED`/`DEPENDENCY_FAILED`/`ABANDONED`/`ORPHAN_SUSPECTED` for today's run, a
`MISSED` job, any `rejected_invocations` entry, or any `orphan_records` entry is `ATTENTION` -
still a business/operational outcome, not an infrastructure failure. `WAITING_DEPENDENCY` is
deliberately **not** an ATTENTION trigger - an in-flight wait is not itself unhealthy, the same
treatment the ordinary `RUNNING` checkpoint already gets. Only a runner-level `FAILED` (a launch
failure or a finalized runner exception) is `BROKEN` - today's run, or a rejected invocation
from today that ended `FAILED`.
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
- `StartWhenAvailable=$true` still fires missed triggers late (after boot, resume from
  sleep/hibernation, or a frozen process thawing), and the tasks have no `EndBoundary`. The
  runner launches such a start normally **only if it is still inside that job's own 1-hour
  window of the occurrence it belongs to**; anything else is rejected (`EXPIRED`/`EARLY`) - see
  "Execution windows" above. PK-C alone decides whether a late-but-in-window start is actually
  safe to render.
- No developer bypass flag for dependency ordering in V1 (deliberate) - a manual
  `python -m shadow_scheduler run pre` during a genuine dependency wait will actually block for
  up to its own window's remaining time (and the runner wait budget), the same as the scheduled
  task.
- Stuck checkpoints: the runner wait budget (45 min < `PT50M`) and crash finalization
  (`FAILED`, lock released) mean no live code path leaves a `RUNNING`/`WAITING_DEPENDENCY`
  checkpoint behind any more. A hard kill or power loss still can - `status` then lists it
  under `orphan_records` (`ORPHAN_SUSPECTED`), and an owner-approved `reconcile --execute`
  records it `ABANDONED`.
- A child launched with little of the 50-minute `ExecutionTimeLimit` left (the budget bounds
  only the *waits*) may still be terminated by Task Scheduler mid-run.
- A process frozen by sleep/hibernation **after** the child launched cannot be stopped by the
  window: the window decides only whether to *start* the child.
- The orphan check is same-host only: a record written on another host is always
  `UNVERIFIABLE` and is never reconciled.
- The Task Scheduler Operational event log is disabled on the owner machine, so a runner killed
  by Task Scheduler leaves no event trail. Enabling it for diagnostics is a separate owner
  action: `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true`.

## Manual recovery contract

There are four distinct things an operator can do after a missed or rejected occurrence. They
are not interchangeable:

| | what | command | rules |
|---|---|---|---|
| (a) | normal scheduled invocation | `python -m shadow_scheduler run <job>` | subject to the **same** window, dependency and lock as the Task Scheduler entry. Outside the window it is rejected `EARLY`/`EXPIRED` and launches nothing - it is **never** a way to recover a missed occurrence |
| (b) | manual shadow replay | `scripts\run_morning_pre.bat` (`python -m readiness pre`, then `main.py --mode premarket --shadow`), `scripts\run_evening_full.bat` (`python -m operations.evening_full`), or `python main.py --mode premarket --shadow` | never uploads; its own runs/artifacts, no scheduler record |
| (c) | production orchestrator replay | `python -m production_orchestrator {pre\|post} [--session-date D] [--resume] [--force]`, or `scripts\run_production_pre.bat` / `scripts\run_production_post.bat` (same command, `%*` passed through) | PK-C readiness still decides (`BLOCKED` is final); `--resume` continues a leftover `RUNNING` PK-D manifest under the same run_id; `--force` only bypasses PK-D's own already-completed shortcuts and never adds a flag to the underlying `main.py` calls (docs/PRODUCTION_ORCHESTRATOR.md). Writes its own PK-D manifest, no scheduler record |
| (d) | publication eligibility | - | never automatic. `PUBLIC_REVIEW_REQUIRED_POLICY=BLOCK` is unchanged and nothing in this layer, (b) or (c) uploads |

Rules:

- Never automatically publish.
- Never bypass PRE/POST readiness (PK-C) - a replay that PK-C blocks stays blocked.
- Never backdate a normal production run. A replay is its own run record (a PK-D manifest or
  shadow artifact, attributed to when it actually ran) and never rewrites, completes or
  counterfeits the scheduler's record for the missed occurrence; that occurrence stays
  `EXPIRED`/`MISSED`/`ABANDONED` in `status` and `audit`. Manual replays (e.g. the POST runs of
  10-21 21:17, 10-22 19:59, 10-23 07:14) are separate operational events, never counted as
  scheduled execution.
- If PRE is already past its window, do not force it just to produce a video; historical
  reconstruction is for debugging only.

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

`test_shadow_scheduler_window_recovery.py` (PK-OPS-1) covers, with every clock injected: the
execution-window cases A-I (occurrence attribution, EARLY/LATE rejection, window rechecks, the
runner wait budget, occurrence-matched dependencies), orphan classification and reconciliation
J-N, a manual replay never counted as scheduled execution (O), the Windows tasks untouched (P),
and public/private outputs unchanged (Q).

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
