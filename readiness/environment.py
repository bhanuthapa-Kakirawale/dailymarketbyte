"""Lightweight render / storage preflight - before an expensive render, never a render.

Each check takes its I/O as an injectable seam so tests can simulate a missing ffmpeg, an
unwritable output root or a renderer import failure without touching the machine.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile

from .matrix import requirement
from .models import PASS, SKIP, CheckResult, unmet

RENDER_PACKAGES = ("numpy", "pandas", "matplotlib", "PIL", "imageio_ffmpeg")
MIN_FREE_FAIL = 200 * 1024 * 1024        # below this a render (frames + MP4) cannot finish
MIN_FREE_WARN = 1024 * 1024 * 1024
MIN_PYTHON = (3, 10)


def _mk(edition, check_id, category, capability, status, message, **kw) -> CheckResult:
    return CheckResult(check_id=check_id, category=category, capability=capability,
                       requirement=requirement(capability, edition), status=status,
                       message=message, **kw)


def check_renderer(edition: str, importer=None) -> CheckResult:
    importer = importer or importlib.import_module
    req = requirement("RENDERER", edition)
    if sys.version_info[:2] < MIN_PYTHON:
        return _mk(edition, "RENDERER", "RENDERING", "RENDERER", unmet(req),
                   f"Python {sys.version.split()[0]} is older than "
                   f"{'.'.join(map(str, MIN_PYTHON))}", remediation="use the project venv")
    missing = []
    for name in RENDER_PACKAGES:
        try:
            importer(name)
        except Exception as exc:
            missing.append(f"{name} ({type(exc).__name__})")
    try:
        comp = importer("daily_video.composer")
        scenes = len(getattr(comp, "SCENE_CLASSES", {}) or {})
    except Exception as exc:
        missing.append(f"daily_video.composer ({type(exc).__name__}: {exc})")
        scenes = 0
    if missing:
        return _mk(edition, "RENDERER", "RENDERING", "RENDERER", unmet(req),
                   "renderer cannot be imported: " + ", ".join(missing),
                   remediation="pip install -r requirements.txt inside the venv "
                               "(scripts\\check_setup.bat lists what is missing)")
    return _mk(edition, "RENDERER", "RENDERING", "RENDERER", PASS,
               f"Composer imports ({scenes} scene classes), Python {sys.version.split()[0]}",
               observed=scenes)


def _resolve_ffmpeg() -> str:
    from video import _ffmpeg
    return _ffmpeg()


def _run_version(exe: str) -> bool:
    proc = subprocess.run([exe, "-version"], capture_output=True, timeout=15)
    return proc.returncode == 0


def check_ffmpeg(edition: str, resolver=None, runner=None) -> CheckResult:
    """The binary the Composer pipes frames into (`video._ffmpeg`: imageio-ffmpeg, else PATH)
    and that `qa.video_qa.probe_media` reads back. One `-version` call - no encode."""
    req = requirement("FFMPEG", edition)
    resolver = resolver or _resolve_ffmpeg
    runner = runner or _run_version
    try:
        exe = resolver()
    except Exception as exc:
        exe, err = None, f"{type(exc).__name__}: {exc}"
    else:
        err = None
    path = exe if exe and os.path.isabs(exe) else (shutil.which(exe) if exe else None)
    if not path or not os.path.exists(path):
        return _mk(edition, "FFMPEG", "RENDERING", "FFMPEG", unmet(req),
                   f"ffmpeg not found ({err or exe or 'unresolved'})", observed=exe,
                   remediation="pip install imageio-ffmpeg in the venv, or put ffmpeg in PATH")
    try:
        ok = runner(path)
    except Exception as exc:
        ok, err = False, f"{type(exc).__name__}: {exc}"
    if not ok:
        return _mk(edition, "FFMPEG", "RENDERING", "FFMPEG", unmet(req),
                   f"ffmpeg at {path} did not run ({err or 'non-zero exit'})", observed=path,
                   remediation="reinstall imageio-ffmpeg in the venv")
    return _mk(edition, "FFMPEG", "RENDERING", "FFMPEG", PASS, f"ffmpeg runs ({path})",
               observed=path)


def _font_report() -> dict:
    from daily_video.typography import font_report
    return font_report()


def check_fonts(edition: str, reporter=None) -> CheckResult:
    """A TrueType font must resolve for both weights; PIL's bitmap default cannot draw the
    Short. The repo ships no bundled font (assets/fonts holds only .keep), so a system font
    (Windows Arial / DejaVu) is the normal, passing state."""
    req = requirement("FONTS", edition)
    rep = (reporter or _font_report)()
    bad = [w for w, r in rep.items() if not r.get("resolved_path")]
    observed = {w: r.get("resolved_path") for w, r in rep.items()}
    if bad:
        return _mk(edition, "FONTS", "RENDERING", "FONTS", unmet(req),
                   "no TrueType font resolved for " + ", ".join(bad) + " (PIL default font)",
                   observed=observed,
                   remediation="add a .ttf to assets/fonts/ or install Arial / DejaVu Sans")
    fallback = any(r.get("fallback_used") for r in rep.values())
    return _mk(edition, "FONTS", "RENDERING", "FONTS", PASS,
               "fonts resolve" + (" (system font - no bundled font in assets/fonts, expected)"
                                  if fallback else ""), observed=observed)


def check_audio(edition: str) -> CheckResult:
    try:
        from daily_video.composer import AUDIO_ENABLED
    except Exception:
        AUDIO_ENABLED = False
    if not AUDIO_ENABLED:
        return _mk(edition, "AUDIO_ASSETS", "RENDERING", "AUDIO_ASSETS", SKIP,
                   "silent video (AUDIO_ENABLED is False) - no music asset needed")
    return _mk(edition, "AUDIO_ASSETS", "RENDERING", "AUDIO_ASSETS", PASS,
               "audio enabled - music.py generates a track when no asset exists")


def _probe_write(directory: str) -> None:
    from operations.run_context import guard_write
    guard_write(directory, "readiness write probe")
    os.makedirs(directory, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=".readiness_probe_", dir=directory)
    os.close(fd)
    os.remove(path)


def check_storage(edition: str, out_dir: str, history_ok: bool, history_detail: str,
                  write_probe=None, disk_usage=None) -> CheckResult:
    """Output root writable (one temp file, created and removed), temp dir writable, history
    database readable, enough disk for frames + MP4."""
    req = requirement("STORAGE", edition)
    write_probe = write_probe or _probe_write
    disk_usage = disk_usage or shutil.disk_usage
    problems, notes = [], []
    for label, directory in (("output root", out_dir), ("temp dir", tempfile.gettempdir())):
        try:
            write_probe(directory)
        except Exception as exc:
            problems.append(f"{label} {directory} not writable ({type(exc).__name__}: {exc})")
    if not history_ok:
        problems.append(f"history database unreadable: {history_detail}")
    free = None
    try:
        free = disk_usage(out_dir if os.path.isdir(out_dir) else os.path.dirname(out_dir) or ".").free
    except Exception as exc:
        notes.append(f"disk space unknown ({type(exc).__name__})")
    if free is not None and free < MIN_FREE_FAIL:
        problems.append(f"only {free // (1024 * 1024)} MB free")
    if problems:
        return _mk(edition, "STORAGE", "STORAGE", "STORAGE", unmet(req), "; ".join(problems),
                   observed={"out_dir": out_dir, "free_bytes": free},
                   remediation="fix permissions / free disk space on the output drive; run the "
                               "REPORT job if the history database does not exist yet")
    msg = f"output root writable, history readable, {free // (1024 * 1024)} MB free" \
        if free is not None else "output root writable, history readable"
    if free is not None and free < MIN_FREE_WARN:
        # enough for one render (above the FAIL floor) - a note, not a degradation: the video
        # itself is unaffected
        notes.append("LOW DISK SPACE - clean old output (scripts\\clean_dev_artifacts.py)")
    if notes:
        msg += "; " + "; ".join(notes)
    return _mk(edition, "STORAGE", "STORAGE", "STORAGE", PASS, msg,
               observed={"out_dir": out_dir, "free_bytes": free})


__all__ = ["check_renderer", "check_ffmpeg", "check_fonts", "check_audio", "check_storage",
           "RENDER_PACKAGES", "MIN_FREE_FAIL", "MIN_FREE_WARN"]
