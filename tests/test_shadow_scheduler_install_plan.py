"""install_shadow_tasks.ps1's planning/comparison logic, exercised for real in PowerShell but
with `Get-ScheduledTask`/`Get-TimeZone` shadowed by same-named functions returning fixture
objects - plain PowerShell function-shimming, no Pester, and crucially NO real
Register-ScheduledTask/Unregister-ScheduledTask call ever happens in these tests (either the
script's own refusal logic stops it, or the test overrides the package's own
`New-ShadowScheduledTask` wrapper to just record a call instead of touching the OS).

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


def _run_ps(tmp_path, body: str) -> subprocess.CompletedProcess:
    script = tmp_path / "harness.ps1"
    header = f'. "{INSTALL_PS1}"\n'
    script.write_text(header + body, encoding="utf-8")
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=60)


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
    $settings = [pscustomobject]@{ MultipleInstances = "IgnoreNew"; StartWhenAvailable = $true; ExecutionTimeLimit = "PT50M"; RestartCount = 0 }
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
