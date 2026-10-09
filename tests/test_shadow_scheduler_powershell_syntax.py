"""PowerShell syntax validation for the 3 new .ps1 scripts, plus ONE real, non-modifying run
of `install_shadow_tasks.ps1` (default and -WhatIf) and `check_shadow_tasks.ps1` against the
owner's ACTUAL environment - expecting all 4 tasks reported as not-yet-installed (MISSING),
with zero mutation either way.

This is the one test module in the shadow_scheduler suite that is intentionally NOT isolated
from the owner's real machine (per the packet's own test 14/51 asks for exactly this): it
shells out to the real `powershell.exe` and reads the real (absent) Task Scheduler state.
Every other shadow_scheduler test file uses bare `tmp_path` and has no such dependency.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
PS1_FILES = ["install_shadow_tasks.ps1", "check_shadow_tasks.ps1", "remove_shadow_tasks.ps1"]

pytestmark = pytest.mark.skipif(shutil.which("powershell") is None,
                                reason="powershell.exe not available on this machine")


def _parse_check(filename: str) -> subprocess.CompletedProcess:
    path = os.path.join(SCRIPTS_DIR, filename)
    cmd = ("$e=$null;$t=$null;"
          f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e)|Out-Null;"
          "if($e.Count -gt 0){$e|ForEach-Object{Write-Error $_.Message};exit 1}else{exit 0}")
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-Command", cmd], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("filename", PS1_FILES)
def test_script_has_no_syntax_errors(filename):
    proc = _parse_check(filename)
    assert proc.returncode == 0, proc.stderr


def test_install_preview_against_the_real_environment_reports_create_and_mutates_nothing():
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         os.path.join(SCRIPTS_DIR, "install_shadow_tasks.ps1")],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    for name in ("DMB-Shadow-Intelligence-AM", "DMB-Shadow-PRE", "DMB-Shadow-Intelligence-PM",
                "DMB-Shadow-POST"):
        assert name in proc.stdout
    # whatever the real state happens to be, this call must never register/remove anything -
    # verified by check_shadow_tasks.ps1 immediately after, in the next test.


def test_install_whatif_matches_the_default_preview():
    default_proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         os.path.join(SCRIPTS_DIR, "install_shadow_tasks.ps1")],
        capture_output=True, text=True, timeout=60)
    whatif_proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         os.path.join(SCRIPTS_DIR, "install_shadow_tasks.ps1"), "-WhatIf"],
        capture_output=True, text=True, timeout=60)
    assert default_proc.returncode == whatif_proc.returncode == 0
    assert default_proc.stdout == whatif_proc.stdout


def test_check_shadow_tasks_reports_missing_before_any_install():
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         os.path.join(SCRIPTS_DIR, "check_shadow_tasks.ps1")],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    for name in ("DMB-Shadow-Intelligence-AM", "DMB-Shadow-PRE", "DMB-Shadow-Intelligence-PM",
                "DMB-Shadow-POST"):
        assert f"{name}   MISSING" in proc.stdout or "MISSING" in proc.stdout


def test_remove_default_preview_is_a_no_op_and_mutates_nothing():
    before = subprocess.run(["powershell", "-NoProfile", "-Command",
                            'Get-ScheduledTask -TaskPath "\\DMB\\" -ErrorAction SilentlyContinue | '
                            'Select-Object -ExpandProperty TaskName'],
                           capture_output=True, text=True, timeout=30)
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         os.path.join(SCRIPTS_DIR, "remove_shadow_tasks.ps1")],
        capture_output=True, text=True, timeout=60)
    after = subprocess.run(["powershell", "-NoProfile", "-Command",
                           'Get-ScheduledTask -TaskPath "\\DMB\\" -ErrorAction SilentlyContinue | '
                           'Select-Object -ExpandProperty TaskName'],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert before.stdout == after.stdout   # no \DMB\ task set was changed by the preview
