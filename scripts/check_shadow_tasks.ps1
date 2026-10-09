# Daily Market Byte - PK-E: READ-ONLY health check of the 4 DMB-Shadow Windows Task
# Scheduler entries. Never calls Register-ScheduledTask / Set-ScheduledTask /
# Unregister-ScheduledTask - no verb in this script ever mutates anything. Reuses the exact
# same comparison logic as install_shadow_tasks.ps1 (dot-sourced below) so there is only ONE
# definition of "what the expected task config is" in this repo. See
# docs\SHADOW_PRODUCTION_OPERATIONS.md.
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot "install_shadow_tasks.ps1")

$RepoRoot = Find-ShadowRepoRoot
$PythonExe = Find-ShadowPythonExecutable -RepoRoot $RepoRoot
$expected = Get-ExpectedShadowTasks -RepoRoot $RepoRoot -PythonExe $PythonExe

Write-Host "DMB Shadow Task Scheduler - status (read-only)"
Write-Host ("Repo root:  {0}" -f $RepoRoot)
Write-Host ("Python:     {0}" -f $PythonExe)
Write-Host ""

foreach ($exp in $expected) {
    $live = Get-ScheduledTask -TaskPath $script:ShadowTaskFolder -TaskName $exp.Name -ErrorAction SilentlyContinue
    $cmp = Compare-ShadowTask -Expected $exp -LiveTask $live

    if (-not $live) {
        Write-Host ("{0,-28} MISSING" -f $exp.Name)
        continue
    }

    $info = $null
    try { $info = Get-ScheduledTaskInfo -TaskPath $script:ShadowTaskFolder -TaskName $exp.Name -ErrorAction Stop } catch {}
    $enabled = $live.State -ne "Disabled"
    $drift = @($cmp.Drift)
    if (-not $enabled) { $drift += "DISABLED" }

    Write-Host ("{0,-28} Exists=Yes Enabled={1}" -f $exp.Name, $enabled)
    Write-Host ("  Action:        {0} {1}" -f ($live.Actions[0].Execute), ($live.Actions[0].Arguments))
    Write-Host ("  Start time:    {0} (configured: {1})" -f ($live.Triggers[0].StartBoundary), $exp.Time)
    if ($info) {
        Write-Host ("  NextRunTime:   {0}" -f $info.NextRunTime)
        Write-Host ("  LastRunTime:   {0}" -f $info.LastRunTime)
        Write-Host ("  LastResult:    {0}" -f $info.LastTaskResult)
    }
    if ($drift.Count -gt 0) {
        Write-Host ("  DRIFT:         {0}" -f ($drift -join ", "))
    }
}
