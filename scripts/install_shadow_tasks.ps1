# Daily Market Byte - PK-E: preview/install the 4 DMB-Shadow Windows Task Scheduler entries
# that each run `python -m shadow_scheduler run <job>` on the local shadow production
# schedule (Mon-Fri IST). Default (no switch) and -WhatIf BOTH only print a plan - zero Task
# Scheduler mutation either way. -Install performs the real registration and is never run
# automatically by any Claude Code packet - it is an explicit, owner-approved step.
#
# Never stores a password, never runs as SYSTEM (current interactive user only), never
# overwrites a same-named task that isn't provably DMB-owned (refuses the WHOLE run on any
# such collision), and refuses a real install unless the Windows timezone is "India Standard
# Time" (-SkipTimezoneCheck exists ONLY for testing and must never be used for a real
# install - it prints a loud warning every time it's used).
#
# See docs\SHADOW_PRODUCTION_OPERATIONS.md.

[CmdletBinding()]
param(
    [switch]$WhatIf,
    [switch]$Install,
    [switch]$SkipTimezoneCheck
)

$script:ShadowTaskFolder = "\DMB\"
$script:ShadowExecutionTimeLimit = "PT50M"
# Weekly trigger DaysOfWeek bitmask (Sunday=1 .. Saturday=64); Mon-Fri = 2+4+8+16+32.
$script:ShadowMonFriBitmask = 62

function Find-ShadowRepoRoot {
    Split-Path -Parent $PSScriptRoot
}

function Find-ShadowPythonExecutable {
    param([Parameter(Mandatory)][string]$RepoRoot)
    foreach ($rel in @("venv\Scripts\python.exe", ".venv\Scripts\python.exe")) {
        $candidate = Join-Path $RepoRoot $rel
        if (Test-Path $candidate) { return $candidate }
    }
    throw "No virtual environment found (venv\ or .venv\) under '$RepoRoot' - see docs\USER_GUIDE.md section 1"
}

function Get-ExpectedShadowTasks {
    param([Parameter(Mandatory)][string]$RepoRoot, [Parameter(Mandatory)][string]$PythonExe)
    $rows = @(
        [pscustomobject]@{ Name = "DMB-Shadow-Intelligence-AM"; Job = "intelligence_am"; Time = "06:40" }
        [pscustomobject]@{ Name = "DMB-Shadow-PRE";             Job = "pre";             Time = "07:00" }
        [pscustomobject]@{ Name = "DMB-Shadow-Intelligence-PM"; Job = "intelligence_pm"; Time = "19:15" }
        [pscustomobject]@{ Name = "DMB-Shadow-POST";            Job = "post";            Time = "19:30" }
    )
    foreach ($row in $rows) {
        $row | Add-Member -NotePropertyName Execute -NotePropertyValue $PythonExe
        $row | Add-Member -NotePropertyName Arguments -NotePropertyValue "-m shadow_scheduler run $($row.Job)"
        $row | Add-Member -NotePropertyName WorkingDirectory -NotePropertyValue $RepoRoot
    }
    $rows
}

function Test-ShadowTaskOwnership {
    <# True only when $Task's action is provably ours: python.exe, the shadow_scheduler
       module invocation, and the exact expected \DMB\<Name> path. Never trusts the task
       name alone. #>
    param($Task, [Parameter(Mandatory)][string]$ExpectedName)
    if (-not $Task) { return $false }
    $action = $Task.Actions | Select-Object -First 1
    if (-not $action) { return $false }
    $execOk = [bool]($action.Execute -like "*python.exe")
    $argsOk = [bool]($action.Arguments -like "*-m shadow_scheduler run*")
    $expectedFull = ($script:ShadowTaskFolder.TrimEnd('\') + '\' + $ExpectedName)
    $liveFull = ($Task.TaskPath.TrimEnd('\') + '\' + $Task.TaskName)
    $pathOk = [bool]($liveFull -eq $expectedFull)
    return ($execOk -and $argsOk -and $pathOk)
}

function Compare-ShadowTask {
    <# Decides CREATE / UNCHANGED / UPDATE / COLLISION for one expected task against its live
       Task Scheduler object (or $null if it does not exist). Never mutates anything. #>
    param([Parameter(Mandatory)]$Expected, $LiveTask)

    if (-not $LiveTask) {
        return [pscustomobject]@{ Name = $Expected.Name; Plan = "CREATE"; Drift = @() }
    }
    if (-not (Test-ShadowTaskOwnership -Task $LiveTask -ExpectedName $Expected.Name)) {
        return [pscustomobject]@{ Name = $Expected.Name; Plan = "COLLISION"; Drift = @("FOREIGN_TASK") }
    }

    $drift = New-Object System.Collections.Generic.List[string]
    $action = $LiveTask.Actions | Select-Object -First 1
    if ($action.Execute -ne $Expected.Execute) { $drift.Add("WRONG_PYTHON") }
    if (($action.Arguments -ne $Expected.Arguments) -or
        ($action.WorkingDirectory -ne $Expected.WorkingDirectory)) { $drift.Add("WRONG_PATH") }

    $trigger = $LiveTask.Triggers | Select-Object -First 1
    $liveTime = $null
    if ($trigger -and $trigger.StartBoundary) {
        try { $liveTime = ([datetime]$trigger.StartBoundary).ToString("HH:mm") } catch { $liveTime = $null }
    }
    $liveDays = if ($trigger) { $trigger.DaysOfWeek } else { $null }
    if (($liveTime -ne $Expected.Time) -or ($liveDays -ne $script:ShadowMonFriBitmask)) {
        $drift.Add("WRONG_TIME")
    }

    $settings = $LiveTask.Settings
    if (($settings.MultipleInstances -ne "IgnoreNew") -or (-not $settings.StartWhenAvailable) -or
        ($settings.ExecutionTimeLimit -ne $script:ShadowExecutionTimeLimit) -or
        ($settings.RestartCount -ne 0)) {
        $drift.Add("CONFIG_DRIFT")
    }

    $plan = if ($drift.Count -eq 0) { "UNCHANGED" } else { "UPDATE" }
    [pscustomobject]@{ Name = $Expected.Name; Plan = $plan; Drift = $drift.ToArray() }
}

function Get-ShadowTaskPlan {
    <# The full, read-only install plan: one row per expected task, never mutating anything.
       Calls the real `Get-ScheduledTask` cmdlet - tests shadow it with a same-named function
       returning fixture objects, never touching the real Task Scheduler. #>
    param([Parameter(Mandatory)][string]$RepoRoot, [Parameter(Mandatory)][string]$PythonExe)
    $expected = Get-ExpectedShadowTasks -RepoRoot $RepoRoot -PythonExe $PythonExe
    foreach ($exp in $expected) {
        $live = Get-ScheduledTask -TaskPath $script:ShadowTaskFolder -TaskName $exp.Name -ErrorAction SilentlyContinue
        Compare-ShadowTask -Expected $exp -LiveTask $live
    }
}

function Test-ShadowTimezoneIsIst {
    (Get-TimeZone).Id -eq "India Standard Time"
}

function New-ShadowScheduledTask {
    <# Registers ONE expected task for real. Only ever called from Invoke-ShadowTaskInstall,
       which has already run every refusal check. #>
    param([Parameter(Mandatory)]$Expected)
    $action = New-ScheduledTaskAction -Execute $Expected.Execute -Argument $Expected.Arguments `
        -WorkingDirectory $Expected.WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Expected.Time
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -ExecutionTimeLimit $script:ShadowExecutionTimeLimit -RestartCount 0
    # Current interactive user, no stored password, no SYSTEM.
    $userId = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Expected.Name -TaskPath $script:ShadowTaskFolder `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
}

function Invoke-ShadowTaskInstall {
    <# The ONLY function that mutates Task Scheduler state. Refuses the ENTIRE run (not just
       the offending task) on a wrong timezone or any unproven-ownership collision. #>
    param([Parameter(Mandatory)][string]$RepoRoot, [Parameter(Mandatory)][string]$PythonExe,
         [switch]$SkipTimezoneCheck)

    if (-not (Test-ShadowTimezoneIsIst)) {
        if (-not $SkipTimezoneCheck) {
            Write-Error ("Refusing to install: Windows timezone is '$((Get-TimeZone).Id)', " +
                        "expected 'India Standard Time'. Pass -SkipTimezoneCheck only for " +
                        "testing - never for a real install.")
            return 1
        }
        Write-Warning "TIMEZONE CHECK SKIPPED - this switch must never be used for a real install."
    }

    $plan = Get-ShadowTaskPlan -RepoRoot $RepoRoot -PythonExe $PythonExe
    $collisions = $plan | Where-Object { $_.Plan -eq "COLLISION" }
    if ($collisions) {
        Write-Error ("Refusing to install: the following task name(s) already exist and are " +
                    "NOT provably DMB-owned - inspect them by hand before proceeding: " +
                    ($collisions.Name -join ", "))
        return 1
    }

    $expected = Get-ExpectedShadowTasks -RepoRoot $RepoRoot -PythonExe $PythonExe
    foreach ($exp in $expected) {
        $row = $plan | Where-Object { $_.Name -eq $exp.Name }
        switch ($row.Plan) {
            "CREATE" {
                New-ShadowScheduledTask -Expected $exp
                Write-Host "CREATED   $($exp.Name)"
            }
            "UPDATE" {
                Unregister-ScheduledTask -TaskName $exp.Name -TaskPath $script:ShadowTaskFolder -Confirm:$false
                New-ShadowScheduledTask -Expected $exp
                Write-Host "UPDATED   $($exp.Name)"
            }
            "UNCHANGED" {
                Write-Host "UNCHANGED $($exp.Name)"
            }
        }
    }
    return 0
}

function Show-ShadowTaskPlanPreview {
    param([Parameter(Mandatory)][string]$RepoRoot, [Parameter(Mandatory)][string]$PythonExe)
    Write-Host "DMB Shadow Task Scheduler - install PLAN (preview only, nothing is changed)"
    Write-Host ("Repo root:  {0}" -f $RepoRoot)
    Write-Host ("Python:     {0}" -f $PythonExe)
    Write-Host ""
    $plan = Get-ShadowTaskPlan -RepoRoot $RepoRoot -PythonExe $PythonExe
    foreach ($row in $plan) {
        $driftText = if ($row.Drift.Count -gt 0) { " (" + ($row.Drift -join ", ") + ")" } else { "" }
        Write-Host ("  {0,-28} {1}{2}" -f $row.Name, $row.Plan, $driftText)
    }
    if (-not (Test-ShadowTimezoneIsIst)) {
        Write-Host ""
        Write-Host ("  NOTE: Windows timezone is '{0}', not 'India Standard Time' - a real " +
                    "-Install would refuse unless -SkipTimezoneCheck is also passed (testing " +
                    "only, never for production)." -f (Get-TimeZone).Id)
    }
    $plan
}

# --- entry point --------------------------------------------------------------------------
# Dot-sourcing this script (as check_shadow_tasks.ps1 / remove_shadow_tasks.ps1 and the test
# suite do, to reuse the functions above) must never run any of the logic below.
if ($MyInvocation.InvocationName -ne '.') {
    $RepoRoot = Find-ShadowRepoRoot
    $PythonExe = Find-ShadowPythonExecutable -RepoRoot $RepoRoot

    if ($Install) {
        $code = Invoke-ShadowTaskInstall -RepoRoot $RepoRoot -PythonExe $PythonExe -SkipTimezoneCheck:$SkipTimezoneCheck
        exit $code
    }
    else {
        Show-ShadowTaskPlanPreview -RepoRoot $RepoRoot -PythonExe $PythonExe | Out-Null
        exit 0
    }
}
