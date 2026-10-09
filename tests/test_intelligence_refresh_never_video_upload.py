"""intelligence_refresh must never render video or upload, however it is invoked - a static
ast-based guard (mirrors tests/test_production_orchestrator_publish_intent.py's style),
extended with the video-rendering modules this package has no reason to ever import."""
from __future__ import annotations

import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "intelligence_refresh")
BANNED_MODULES = {"upload", "video", "daily_video", "ffmpeg", "imageio_ffmpeg"}


def test_no_module_imports_a_banned_module():
    for path in glob.glob(os.path.join(PKG_DIR, "*.py")):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in BANNED_MODULES, f"{path} imports {top}"
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                assert mod not in BANNED_MODULES, f"{path} imports from {mod}"


def test_no_module_spells_the_upload_flag():
    for path in glob.glob(os.path.join(PKG_DIR, "*.py")):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "--upload" not in src, f"{path} contains the literal string --upload"


def test_no_module_calls_subprocess_ffmpeg():
    for path in glob.glob(os.path.join(PKG_DIR, "*.py")):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "ffmpeg" not in src.lower(), f"{path} references ffmpeg"
