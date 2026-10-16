"""install_shadow_tasks.ps1's planning/comparison logic, exercised for real in PowerShell but
with `Get-ScheduledTask`/`Get-TimeZone` shadowed by same-named functions returning fixture
objects - plain PowerShell function-shimming, no Pester, and crucially NO real
Register-ScheduledTask/Unregister-ScheduledTask call ever happens in these tests (either the
script's own refusal logic stops it, or the test overrides the package's own
`New-ShadowScheduledTask` wrapper to just record a call instead of touching the OS).

Test isolation hardening (PK-E follow-up, 2026-10-12): a development test iteration once
left Register-ScheduledTask unmocked in one test while mocking only New-ScheduledTaskSettingsSet
to throw - a plain (non-advanced) PowerShell function's parameter-binding failure (its shim
declared -StartWhenAvailable as a normal parameter, not [switch], so the real cmdlet's bare
switch syntax failed to bind) turned out to be NON-terminating for that call, so the intended
"throw" never ran, $settings silently came back $null, and execution fell through to the
REAL, unmocked Register-ScheduledTask, which accepted a $null Settings argument and genuinely
registered 4 tasks on the machine. `_run_ps` below now injects default-throwing guard stubs
for every real mutating Task Scheduler cmdlet into EVERY test's PowerShell scope before the
test's own body runs; a test that wants to simulate success defines its own same-named
function afterward (which overrides the guard, exactly as `Get-ScheduledTask` is already
overridden below) - but a test that forgets to mock one no longer has a silent path to the
real OS: the guard stub throws immediately and loudly instead. `_real_task_scheduler_untouched`
independently re-verifies, directly against the real environment (no shimming involved), that
the \\DMB\\ task set is byte-identical before and after this entire module runs.

Covers packet tests 6 (idempotent install planning), 7 (foreign-task collision refusal), 8
(wrong-timezone refusal, preview may still show it).
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_PS1 = os.path.join(REPO_ROOT, "scripts", "install_shadow_tasks.ps1")

pytestmark = pytest.mark.skipif(shutil.which("powershell") is None,
                                reason="powershell.exe not available on this machine")

# Defense in depth: every mutating Task Scheduler cmdlet the production script can reach is
# shadowed here with a function that throws by default. A test that needs the success/failure
# path defines its OWN same-named function further down in its body - PowerShell resolves the
# most-recently-defined function in the same scope, so that override wins - but a test that
# forgets to mock one of these can no longer fall through to the real OS cmdlet; it hits this
# guard instead, loudly, every time.
_MUTATION_GUARD = r"""
function Register-ScheduledTask {
    param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, [switch]$Force, $ErrorAction)
    throw "TEST SAFETY GUARD: real Register-ScheduledTask called - this test must mock it explicitly."
}
function Unregister-ScheduledTask {
    param($TaskName, $TaskPath, $Confirm, $ErrorAction)
    throw "TEST SAFETY GUARD: real Unregister-ScheduledTask called - this test must mock it explicitly."
}
function Set-ScheduledTask {
    param($TaskName, $TaskPath, $Settings, $ErrorAction)
    throw "TEST SAFETY GUARD: real Set-ScheduledTask called - this test must mock it explicitly."
}
function Enable-ScheduledTask {
    param($TaskName, $TaskPath, $ErrorAction)
    throw "TEST SAFETY GUARD: real Enable-ScheduledTask called - this test must mock it explicitly."
}
function Disable-ScheduledTask {
    param($TaskName, $TaskPath, $ErrorAction)
    throw "TEST SAFETY GUARD: real Disable-ScheduledTask called - this test must mock it explicitly."
}
"""


def _run_ps(tmp_path, body: str) -> subprocess.CompletedProcess:
    script = tmp_path / "harness.ps1"
    header = f'. "{INSTALL_PS1}"\n' + _MUTATION_GUARD
    script.write_text(header + body, encoding="utf-8")
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=60)


def _live_dmb_task_names(timeout: int = 30) -> set[str]:
    """Reads the REAL \\DMB\\ task names directly - no function shimming involved, so this
    is independent evidence of actual OS state, not just of what the harness intended."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         'Get-ScheduledTask -TaskPath "\\DMB\\" -ErrorAction SilentlyContinue | '
         'Select-Object -ExpandProperty TaskName'],
        capture_output=True, text=True, timeout=timeout)
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


@pytest.fixture(scope="module", autouse=True)
def _real_task_scheduler_untouched():
    """Module-wide safety net (defense in depth): independently verifies the real \\DMB\\ task
    set is byte-identical before and after every test in this file runs, regardless of which
    shims any individual test did or didn't define."""
    before = _live_dmb_task_names()
    yield
    after = _live_dmb_task_names()
    assert before == after, (
        "A test in this module mutated the REAL Windows Task Scheduler \\DMB\\ folder: "
        f"before={before!r} after={after!r}")


def test_no_live_tasks_plans_create_for_all_four(tmp_path):
    body = r"""
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $null }
$plan = Get-ShadowTaskPlan -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
$plan | ForEach-Object { Write-Host "$($_.Name)=$($_.Plan)" }
"""
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    for name in ("DMB-Shadow-Intelligence-AM", "DMB-Shadow-PRE", "DMB-Shadow-Intelligence-PM",
                "DMB-Shadow-POST"):
        assert f"{name}=CREATE" in proc.stdout


def test_matching_live_tasks_plan_unchanged_for_all_four_second_time(tmp_path):
    body = r"""
function Get-ScheduledTask {
    param($TaskPath, $TaskName, $ErrorAction)
    $expected = Get-ExpectedShadowTasks -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
    $match = $expected | Where-Object { $_.Name -eq $TaskName }
    if (-not $match) { return $null }
    $action = [pscustomobject]@{ Execute = $match.Execute; Arguments = $match.Arguments; WorkingDirectory = $match.WorkingDirectory }
    $trigger = [pscustomobject]@{ StartBoundary = "2026-10-09T$($match.Time):00"; DaysOfWeek = 62 }
    $settings = [pscustomobject]@{ MultipleInstances = "IgnoreNew"; StartWhenAvailable = $true; ExecutionTimeLimit = "PT50M"; RestartCount = 0;
        DisallowStartIfOnBatteries = $false; StopIfGoingOnBatteries = $false; WakeToRun = $true }
    [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; State = "Ready"; Actions = @($action); Triggers = @($trigger); Settings = $settings }
}
$plan = Get-ShadowTaskPlan -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
$plan | ForEach-Object { Write-Host "$($_.Name)=$($_.Plan)" }
"""
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    for name in ("DMB-Shadow-Intelligence-AM", "DMB-Shadow-PRE", "DMB-Shadow-Intelligence-PM",
                "DMB-Shadow-POST"):
        assert f"{name}=UNCHANGED" in proc.stdout


def test_old_power_defaults_on_a_live_task_are_config_drift(tmp_path):
    """PK-E power hardening: a task installed before this change (cmdlet's own power
    defaults - DisallowStartIfOnBatteries=True, StopIfGoingOnBatteries=True, WakeToRun=False)
    must show CONFIG_DRIFT until the owner re-runs the installer - it must never silently
    read as UNCHANGED just because every other field already matches."""
    body = r"""
function Get-ScheduledTask {
    param($TaskPath, $TaskName, $ErrorAction)
    $expected = Get-ExpectedShadowTasks -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
    $match = $expected | Where-Object { $_.Name -eq $TaskName }
    if (-not $match) { return $null }
    $action = [pscustomobject]@{ Execute = $match.Execute; Arguments = $match.Arguments; WorkingDirectory = $match.WorkingDirectory }
    $trigger = [pscustomobject]@{ StartBoundary = "2026-10-09T$($match.Time):00"; DaysOfWeek = 62 }
    $settings = [pscustomobject]@{ MultipleInstances = "IgnoreNew"; StartWhenAvailable = $true; ExecutionTimeLimit = "PT50M"; RestartCount = 0;
        DisallowStartIfOnBatteries = $true; StopIfGoingOnBatteries = $true; WakeToRun = $false }
    [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; State = "Ready"; Actions = @($action); Triggers = @($trigger); Settings = $settings }
}
$plan = Get-ShadowTaskPlan -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
$plan | ForEach-Object { Write-Host "$($_.Name)=$($_.Plan) DRIFT=$($_.Drift -join ',')" }
"""
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    for name in ("DMB-Shadow-Intelligence-AM", "DMB-Shadow-PRE", "DMB-Shadow-Intelligence-PM",
                "DMB-Shadow-POST"):
        assert f"{name}=UPDATE DRIFT=CONFIG_DRIFT" in proc.stdout


def test_foreign_task_is_collision_and_install_refuses_the_whole_run(tmp_path):
    body = r"""
function Get-ScheduledTask {
    param($TaskPath, $TaskName, $ErrorAction)
    if ($TaskName -ne "DMB-Shadow-POST") { return $null }
    $action = [pscustomobject]@{ Execute = "C:\Windows\notepad.exe"; Arguments = ""; WorkingDirectory = "" }
    [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; State = "Ready"; Actions = @($action); Triggers = @(); Settings = $null }
}
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
$created = New-Object System.Collections.Generic.List[string]
function New-ShadowScheduledTask { param($Expected) $created.Add($Expected.Name) }
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
Write-Host "CREATED_COUNT=$($created.Count)"
"""
    proc = _run_ps(tmp_path, body)
    assert "EXIT=1" in proc.stdout
    assert "CREATED_COUNT=0" in proc.stdout   # the collision aborted BEFORE touching anything


def test_wrong_timezone_refuses_install_by_default(tmp_path):
    body = r"""
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $null }
function Get-TimeZone { [pscustomobject]@{ Id = "Pacific Standard Time" } }
$created = New-Object System.Collections.Generic.List[string]
function New-ShadowScheduledTask { param($Expected) $created.Add($Expected.Name) }
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
Write-Host "CREATED_COUNT=$($created.Count)"
"""
    proc = _run_ps(tmp_path, body)
    assert "EXIT=1" in proc.stdout
    assert "CREATED_COUNT=0" in proc.stdout


def test_wrong_timezone_preview_still_shows_the_plan(tmp_path):
    body = r"""
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $null }
function Get-TimeZone { [pscustomobject]@{ Id = "Pacific Standard Time" } }
Show-ShadowTaskPlanPreview -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe" | Out-Null
"""
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    assert "CREATE" in proc.stdout
    assert "India Standard Time" in proc.stdout   # the NOTE line names the expected timezone


def test_wrong_timezone_with_explicit_skip_switch_proceeds_without_touching_the_os(tmp_path):
    body = r"""
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $null }
function Get-TimeZone { [pscustomobject]@{ Id = "Pacific Standard Time" } }
$created = New-Object System.Collections.Generic.List[string]
function New-ShadowScheduledTask { param($Expected) $created.Add($Expected.Name) }
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe" -SkipTimezoneCheck
Write-Host "EXIT=$code"
Write-Host "CREATED_COUNT=$($created.Count)"
"""
    proc = _run_ps(tmp_path, body)
    assert "EXIT=0" in proc.stdout
    assert "CREATED_COUNT=4" in proc.stdout
    assert "TIMEZONE CHECK SKIPPED" in proc.stderr or "TIMEZONE CHECK SKIPPED" in proc.stdout


# Full, safe shims for every Task Scheduler cmdlet New-ShadowScheduledTask touches - used by
# every test below so NONE of them can ever fall through to the real cmdlets (a function
# shadows a same-named cmdlet in the same scope, but only if it is actually defined - leaving
# even one out risks a REAL Register-ScheduledTask call, which must never happen in a test).
_SAFE_SHIMS = r"""
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskTrigger { param([switch]$Weekly, $DaysOfWeek, $At, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { param($MultipleInstances, [switch]$StartWhenAvailable, $ExecutionTimeLimit, $RestartCount, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$WakeToRun, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel, $ErrorAction) [pscustomobject]@{} }
function Register-ScheduledTask { param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, [switch]$Force, $ErrorAction) $null }
"""


def test_execution_time_limit_passed_to_the_cmdlet_is_a_50_minute_timespan(tmp_path):
    """Test A: New-ScheduledTaskSettingsSet must receive a System.TimeSpan of exactly 50
    minutes, never the ISO-8601 string "PT50M" that caused the real conversion error. This
    shim's param list is EXACTLY the real local cmdlet's own switch names
    (`Get-Command New-ScheduledTaskSettingsSet -Syntax`: -AllowStartIfOnBatteries /
    -DontStopIfGoingOnBatteries / -WakeToRun, not -DisallowStartIfOnBatteries:$bool /
    -StopIfGoingOnBatteries:$bool, which don't exist on this cmdlet at all and is the exact bug
    that made a real owner -Install fail) - if the production script ever passes a parameter
    name this shim doesn't declare, PowerShell's own parameter binding fails loudly here."""
    body = _SAFE_SHIMS + r"""
$script:CapturedLimit = $null
$script:CapturedPower = $null
function New-ScheduledTaskSettingsSet {
    param($MultipleInstances, [switch]$StartWhenAvailable, $ExecutionTimeLimit, $RestartCount,
         [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$WakeToRun,
         $ErrorAction)
    $script:CapturedLimit = $ExecutionTimeLimit
    $script:CapturedPower = [pscustomobject]@{ AllowStartIfOnBatteries = [bool]$AllowStartIfOnBatteries;
        DontStopIfGoingOnBatteries = [bool]$DontStopIfGoingOnBatteries; WakeToRun = [bool]$WakeToRun }
    [pscustomobject]@{}
}
$expected = Get-ExpectedShadowTasks -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
New-ShadowScheduledTask -Expected $expected[0]
Write-Host "TYPE=$($script:CapturedLimit.GetType().FullName)"
Write-Host "MINUTES=$($script:CapturedLimit.TotalMinutes)"
Write-Host "ALLOWBATT=$($script:CapturedPower.AllowStartIfOnBatteries)"
Write-Host "DONTSTOPBATT=$($script:CapturedPower.DontStopIfGoingOnBatteries)"
Write-Host "WAKETORUN=$($script:CapturedPower.WakeToRun)"
"""
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    assert "TYPE=System.TimeSpan" in proc.stdout
    assert "MINUTES=50" in proc.stdout
    # Test B: these three switches passed (all True) are what produce the resulting live task
    # settings DisallowStartIfOnBatteries=False / StopIfGoingOnBatteries=False / WakeToRun=True
    # - the laptop missed the 2026-10-16 AM/PRE run because the previous version of this
    # script never passed any of them at all.
    assert "ALLOWBATT=True" in proc.stdout
    assert "DONTSTOPBATT=True" in proc.stdout
    assert "WAKETORUN=True" in proc.stdout


def test_registration_failure_cannot_produce_a_created_message(tmp_path):
    """Tests B + C: a Register-ScheduledTask-path failure (surfaced here via a failing
    New-ScheduledTaskSettingsSet, exactly like the real "PT50M" conversion error) must never
    let CREATED print, and the installer must fail visibly (non-zero exit). Every other
    Task Scheduler cmdlet is still shimmed safely so nothing reaches the real OS."""
    body = _SAFE_SHIMS + r"""
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $null }
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
function New-ScheduledTaskSettingsSet {
    param($MultipleInstances, [switch]$StartWhenAvailable, $ExecutionTimeLimit, $RestartCount,
         [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$WakeToRun,
         $ErrorAction)
    throw "Cannot convert value `"PT50M`" to type `"System.TimeSpan`"."
}
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
"""
    proc = _run_ps(tmp_path, body)
    assert "EXIT=1" in proc.stdout
    assert "CREATED" not in proc.stdout


def test_successful_registration_still_reports_created(tmp_path):
    """Test D: with every step genuinely succeeding, CREATED must still print for all four
    tasks and the installer must exit 0 - the fail-fast hardening must not break the
    successful path."""
    body = _SAFE_SHIMS + r"""
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $null }
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
"""
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    assert "EXIT=0" in proc.stdout
    for name in ("DMB-Shadow-Intelligence-AM", "DMB-Shadow-PRE", "DMB-Shadow-Intelligence-PM",
                "DMB-Shadow-POST"):
        assert f"CREATED   {name}" in proc.stdout


def test_a_missing_mock_for_register_scheduled_task_is_caught_by_the_safety_guard(tmp_path):
    """Regression guard (packet item 5): this test deliberately omits ANY override for
    Register-ScheduledTask - the one real mutating call a successful CREATE reaches - to prove
    that the default `_MUTATION_GUARD` injected by `_run_ps`, not a test-supplied mock, is what
    stops it. If a future test ever forgets to mock a mutating cmdlet, this is the mechanism
    that keeps it from silently reaching the real Windows Task Scheduler."""
    body = r"""
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $null }
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskTrigger { param([switch]$Weekly, $DaysOfWeek, $At, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { param($MultipleInstances, [switch]$StartWhenAvailable, $ExecutionTimeLimit, $RestartCount, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$WakeToRun, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel, $ErrorAction) [pscustomobject]@{} }
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
"""
    proc = _run_ps(tmp_path, body)
    assert "EXIT=1" in proc.stdout
    assert "CREATED" not in proc.stdout
    assert "TEST SAFETY GUARD" in proc.stderr or "TEST SAFETY GUARD" in proc.stdout


# --- safe update ordering (PK-E follow-up, 2026-10-16: a real owner -Install unregistered
# DMB-Shadow-Intelligence-AM, then failed to build the replacement settings object because of
# the unsupported-parameter-name bug above, leaving AM MISSING) ----------------------------

def _owned_live_task_fn(power_healthy: bool = False) -> str:
    """A `Get-ScheduledTask` shim returning a provably-owned, live task for every expected
    name - `power_healthy=False` (the real pre-fix state) makes every task plan UPDATE via
    CONFIG_DRIFT; the one real mutating call an UPDATE should ever reach is
    Register-ScheduledTask (with -Force), never Unregister-ScheduledTask first."""
    power = ("DisallowStartIfOnBatteries = $false; StopIfGoingOnBatteries = $false; WakeToRun = $true"
            if power_healthy else
            "DisallowStartIfOnBatteries = $true; StopIfGoingOnBatteries = $true; WakeToRun = $false")
    return (r"""
function Get-ScheduledTask {
    param($TaskPath, $TaskName, $ErrorAction)
    $expected = Get-ExpectedShadowTasks -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
    $match = $expected | Where-Object { $_.Name -eq $TaskName }
    if (-not $match) { return $null }
    $action = [pscustomobject]@{ Execute = $match.Execute; Arguments = $match.Arguments; WorkingDirectory = $match.WorkingDirectory }
    $trigger = [pscustomobject]@{ StartBoundary = "2026-10-09T$($match.Time):00"; DaysOfWeek = 62 }
    $settings = [pscustomobject]@{ MultipleInstances = "IgnoreNew"; StartWhenAvailable = $true; ExecutionTimeLimit = "PT50M"; RestartCount = 0; """ + power + r""" }
    [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; State = "Ready"; Actions = @($action); Triggers = @($trigger); Settings = $settings }
}
""")


_UNREGISTER_MUST_NOT_BE_CALLED = r"""
function Unregister-ScheduledTask {
    param($TaskName, $TaskPath, $Confirm, $ErrorAction)
    throw "TEST FAILURE: Unregister-ScheduledTask was called for $TaskName - an UPDATE must " +
          "build the replacement first and use Register-ScheduledTask -Force, never " +
          "unregister the existing task before the replacement exists."
}
"""


def test_settings_construction_failure_during_update_never_unregisters_the_old_task(tmp_path):
    """Test C: a construction failure (settings object) during an UPDATE must leave the
    pre-existing, provably-owned task untouched - proven here by Unregister-ScheduledTask
    never being called at all (it must not be part of the UPDATE path any more)."""
    body = (_owned_live_task_fn(power_healthy=False) + _UNREGISTER_MUST_NOT_BE_CALLED + r"""
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskTrigger { param([switch]$Weekly, $DaysOfWeek, $At, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet {
    param($MultipleInstances, [switch]$StartWhenAvailable, $ExecutionTimeLimit, $RestartCount,
         [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$WakeToRun,
         $ErrorAction)
    throw "A parameter cannot be found that matches parameter name 'DisallowStartIfOnBatteries'."
}
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
"""
           )
    proc = _run_ps(tmp_path, body)
    assert "EXIT=1" in proc.stdout
    assert "UPDATED" not in proc.stdout
    assert "FAILED to update" in proc.stderr or "FAILED to update" in proc.stdout
    assert "TEST FAILURE" not in proc.stdout and "TEST FAILURE" not in proc.stderr


def test_registration_failure_during_update_never_unregisters_the_old_task(tmp_path):
    """Test D: a Register-ScheduledTask failure (construction already succeeded) during an
    UPDATE must still never have called Unregister-ScheduledTask first."""
    body = (_owned_live_task_fn(power_healthy=False) + _SAFE_SHIMS +
           _UNREGISTER_MUST_NOT_BE_CALLED + r"""
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
function Register-ScheduledTask {
    param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, [switch]$Force, $ErrorAction)
    throw "access denied (simulated registration failure)"
}
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
"""
           )
    proc = _run_ps(tmp_path, body)
    assert "EXIT=1" in proc.stdout
    assert "UPDATED" not in proc.stdout
    assert "TEST FAILURE" not in proc.stdout and "TEST FAILURE" not in proc.stderr


def test_successful_update_registers_with_force_and_never_unregisters_first(tmp_path):
    """Test E: a genuinely successful UPDATE must call Register-ScheduledTask with -Force
    (never Unregister-ScheduledTask beforehand), and must report UPDATED for all four tasks
    (all four plan UPDATE here, via CONFIG_DRIFT on the old power defaults)."""
    body = (_owned_live_task_fn(power_healthy=False) + _UNREGISTER_MUST_NOT_BE_CALLED + r"""
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskTrigger { param([switch]$Weekly, $DaysOfWeek, $At, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { param($MultipleInstances, [switch]$StartWhenAvailable, $ExecutionTimeLimit, $RestartCount, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$WakeToRun, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel, $ErrorAction) [pscustomobject]@{} }
$script:CapturedForce = New-Object System.Collections.Generic.List[bool]
function Register-ScheduledTask {
    param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, [switch]$Force, $ErrorAction)
    $script:CapturedForce.Add([bool]$Force)
    $null
}
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
Write-Host "FORCE_COUNT=$($script:CapturedForce.Count)"
Write-Host "FORCE_ALL_TRUE=$((($script:CapturedForce | Where-Object { -not $_ }).Count) -eq 0)"
"""
           )
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    assert "EXIT=0" in proc.stdout
    assert "FORCE_COUNT=4" in proc.stdout
    assert "FORCE_ALL_TRUE=True" in proc.stdout
    for name in ("DMB-Shadow-Intelligence-AM", "DMB-Shadow-PRE", "DMB-Shadow-Intelligence-PM",
                "DMB-Shadow-POST"):
        assert f"UPDATED   {name}" in proc.stdout


def test_missing_task_is_created_while_owned_tasks_are_updated_not_recreated(tmp_path):
    """Test F / real-scenario reproduction (packet item 3): exactly the owner's actual state -
    DMB-Shadow-Intelligence-AM MISSING, the other three present + owned with the old power
    defaults (CONFIG_DRIFT). The plan must be CREATE for AM and UPDATE for the other three;
    after a successful install, AM is registered without -Force and the other three WITH
    -Force, and Unregister-ScheduledTask is never called for any of them."""
    body = (_UNREGISTER_MUST_NOT_BE_CALLED + r"""
function Get-ScheduledTask {
    param($TaskPath, $TaskName, $ErrorAction)
    if ($TaskName -eq "DMB-Shadow-Intelligence-AM") { return $null }
    $expected = Get-ExpectedShadowTasks -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
    $match = $expected | Where-Object { $_.Name -eq $TaskName }
    $action = [pscustomobject]@{ Execute = $match.Execute; Arguments = $match.Arguments; WorkingDirectory = $match.WorkingDirectory }
    $trigger = [pscustomobject]@{ StartBoundary = "2026-10-09T$($match.Time):00"; DaysOfWeek = 62 }
    $settings = [pscustomobject]@{ MultipleInstances = "IgnoreNew"; StartWhenAvailable = $true; ExecutionTimeLimit = "PT50M"; RestartCount = 0;
        DisallowStartIfOnBatteries = $true; StopIfGoingOnBatteries = $true; WakeToRun = $false }
    [pscustomobject]@{ TaskName = $TaskName; TaskPath = $TaskPath; State = "Ready"; Actions = @($action); Triggers = @($trigger); Settings = $settings }
}
function Get-TimeZone { [pscustomobject]@{ Id = "India Standard Time" } }
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskTrigger { param([switch]$Weekly, $DaysOfWeek, $At, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { param($MultipleInstances, [switch]$StartWhenAvailable, $ExecutionTimeLimit, $RestartCount, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$WakeToRun, $ErrorAction) [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel, $ErrorAction) [pscustomobject]@{} }
$script:CapturedForce = @{}
function Register-ScheduledTask {
    param($TaskName, $TaskPath, $Action, $Trigger, $Settings, $Principal, [switch]$Force, $ErrorAction)
    $script:CapturedForce[$TaskName] = [bool]$Force
    $null
}
$plan = Get-ShadowTaskPlan -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
$plan | ForEach-Object { Write-Host "PLAN:$($_.Name)=$($_.Plan)" }
$code = Invoke-ShadowTaskInstall -RepoRoot "D:\repo" -PythonExe "D:\repo\venv\Scripts\python.exe"
Write-Host "EXIT=$code"
Write-Host "FORCE_AM=$($script:CapturedForce['DMB-Shadow-Intelligence-AM'])"
Write-Host "FORCE_PRE=$($script:CapturedForce['DMB-Shadow-PRE'])"
"""
           )
    proc = _run_ps(tmp_path, body)
    assert proc.returncode == 0, proc.stderr
    assert "PLAN:DMB-Shadow-Intelligence-AM=CREATE" in proc.stdout
    assert "PLAN:DMB-Shadow-PRE=UPDATE" in proc.stdout
    assert "PLAN:DMB-Shadow-Intelligence-PM=UPDATE" in proc.stdout
    assert "PLAN:DMB-Shadow-POST=UPDATE" in proc.stdout
    assert "EXIT=0" in proc.stdout
    assert "CREATED   DMB-Shadow-Intelligence-AM" in proc.stdout
    assert "UPDATED   DMB-Shadow-PRE" in proc.stdout
    assert "FORCE_AM=False" in proc.stdout
    assert "FORCE_PRE=True" in proc.stdout
