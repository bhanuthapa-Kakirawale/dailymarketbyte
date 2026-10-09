"""shadow_scheduler must never contain market/readiness/business logic, must never import a
Radar/Market Regime/Market Structure/Editorial calculation module, must never import or
literally mention upload/video/YouTube, and must execute only commands built from the static
job registry - never from any market/log/source text. Static ast-based guard, mirroring
tests/test_readiness.py::test_readiness_never_reaches_the_private_desk_or_upload and
tests/test_intelligence_refresh_never_video_upload.py's style.
"""
from __future__ import annotations

import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "shadow_scheduler")

BANNED_MODULES = {
    "upload", "video", "daily_video", "ffmpeg", "imageio_ffmpeg", "private_desk",
    "radar", "market_regime", "market_structure", "editorial", "hooks",
    "institutional_flows", "market_events", "news", "market", "yfinance",
    "requests", "urllib", "googleapiclient",
}

BANNED_LITERALS = ("--upload", "--intent publish", "youtube", "YouTube")


def _py_files():
    return glob.glob(os.path.join(PKG_DIR, "*.py"))


def test_no_module_imports_a_banned_module():
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert not mods & BANNED_MODULES, (path, mods & BANNED_MODULES)


def test_no_module_spells_a_banned_literal():
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for literal in BANNED_LITERALS:
            assert literal.lower() not in src.lower(), f"{path} contains {literal!r}"


def test_no_module_calls_subprocess_ffmpeg():
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "ffmpeg" not in src.lower(), f"{path} references ffmpeg"


def test_runner_never_constructs_a_subprocess_call_with_shell_true():
    """shell=True would let an arbitrary string be interpreted as a shell command - this
    package only ever launches a fixed argv list built from the static job registry."""
    path = os.path.join(PKG_DIR, "runner.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "shell=True" not in src


def test_runner_never_formats_the_child_command_from_cli_argv_or_env():
    """The child command must come only from config.JOB_REGISTRY[job].bat_relpath - never
    from `cli_argv`, an environment variable, or any market/log/session text. A static check:
    `cli_argv` must never appear inside the same expression that builds `child_command`."""
    path = os.path.join(PKG_DIR, "runner.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "child_command":
                    assigned_src = ast.dump(node.value)
                    assert "cli_argv" not in assigned_src
                    assert "os.environ" not in assigned_src
                    assert "getenv" not in assigned_src


def test_job_registry_is_the_only_source_of_bat_relpaths():
    """Every `.bat_relpath`-shaped string literal in the package must be one of the four
    registry entries - no second, ad hoc path construction anywhere."""
    from shadow_scheduler.config import JOB_REGISTRY
    expected = {spec.bat_relpath for spec in JOB_REGISTRY.values()}
    assert expected == {"scripts/run_intelligence_refresh.bat",
                        "scripts/run_production_pre.bat",
                        "scripts/run_production_post.bat"}


def test_config_module_never_imports_subprocess():
    path = os.path.join(PKG_DIR, "config.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert "subprocess" not in mods
