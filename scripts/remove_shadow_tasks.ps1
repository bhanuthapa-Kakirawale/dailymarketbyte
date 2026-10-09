# Daily Market Byte - PK-E: remove the 4 DMB-Shadow Windows Task Scheduler entries. Default
# (no switch) and -WhatIf are both a no-op PREVIEW - nothing is removed. -Remove performs the
# real removal and re-verifies ownership of each task immediately before deleting it (never
# trusts the name alone) - removes ONLY these 4 tasks, never anything else. See
# docs\SHADOW_PRODUCTION_OPERATIONS.md.
[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$WhatIf
)

. (Join-Path $PSScriptRoot "install_shadow_tasks.ps1")

$RepoRoot = Find-ShadowRepoRoot
$PythonExe = Find-ShadowPythonExecutable -RepoRoot $RepoRoot
$expected = Get-ExpectedShadowTasks -RepoRoot $RepoRoot -PythonExe $PythonExe

foreach ($exp in $expected) {
    $live = Get-ScheduledTask -TaskPath $script:ShadowTaskFolder -TaskName $exp.Name -ErrorAction SilentlyContinue
    if (-not $live) {
        Write-Host ("{0,-28} not installed - nothing to remove" -f $exp.Name)
        continue
    }
    $owned = Test-ShadowTaskOwnership -Task $live -ExpectedName $exp.Name
    if (-not $owned) {
        Write-Host ("{0,-28} NOT DMB-owned - leaving it alone" -f $exp.Name)
        continue
    }
    if ($Remove) {
        Unregister-ScheduledTask -TaskName $exp.Name -TaskPath $script:ShadowTaskFolder -Confirm:$false
        Write-Host ("{0,-28} REMOVED" -f $exp.Name)
    }
    else {
        Write-Host ("{0,-28} would be removed (pass -Remove to actually remove it)" -f $exp.Name)
    }
}
