"""Deterministic post-render checks on the artifact that is about to be published.

Everything here is measurement, not judgement: file sizes, stream properties, durations,
frame statistics. No model is involved, because a publication gate has to give the same
answer every time for the same file.

The probing boundary (`probe_media`, `sample_frame_stats`) is a single seam so tests can
stub the media layer without weakening the production implementation.

Blank-frame policy is deliberately conservative. The design uses dark backgrounds by choice,
so "this frame is dark" is normal and must never block. Only a frame that is effectively one
flat colour (no variance at all) or fails to decode is treated as a rendering failure;
merely-dark frames are a WARNING, and warnings do not block publication.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum

# Fractions of the video sampled for blank/corrupt detection.
SAMPLE_POINTS = (0.02, 0.25, 0.50, 0.75, 0.97)

# A real frame of this design has a gradient background, cards and text, so its pixel
# standard deviation is far above this. A flat fill sits at ~0.
FLAT_FRAME_STDDEV = 1.0
# Dark-but-textured frames are normal here; this only raises a warning.
DARK_FRAME_MEAN = 6.0

MIN_PLAUSIBLE_BYTES = 200_000       # a 75s 1080x1920 H.264 short is megabytes, not kilobytes


class QAStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class QACheck:
    name: str
    status: QAStatus
    expected: str = ""
    actual: str = ""
    message: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status.value, "expected": self.expected,
                "actual": self.actual, "message": self.message}


@dataclass
class VideoQAResult:
    status: QAStatus = QAStatus.PASS
    checks: list = field(default_factory=list)
    blocking_issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    checked_at: dt.datetime | None = None
    video_path: str = ""

    @property
    def passed(self) -> bool:
        """Warnings do not block. Only a FAIL stops publication."""
        return self.status is not QAStatus.FAIL

    def add(self, check: QACheck) -> QACheck:
        self.checks.append(check)
        if check.status is QAStatus.FAIL:
            self.blocking_issues.append(f"{check.name}: {check.message}")
            self.status = QAStatus.FAIL
        elif check.status is QAStatus.WARN:
            self.warnings.append(f"{check.name}: {check.message}")
            if self.status is QAStatus.PASS:
                self.status = QAStatus.WARN
        return check

    def to_dict(self) -> dict:
        return {"status": self.status.value, "passed": self.passed, "video_path": self.video_path,
                "checked_at": self.checked_at.isoformat() if self.checked_at else None,
                "checks": [c.to_dict() for c in self.checks],
                "blocking_issues": list(self.blocking_issues), "warnings": list(self.warnings)}


# --------------------------------------------------------------------- probing boundary
def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_VIDEO_RE = re.compile(r"Stream #\S+: Video: (\w+).*?, (\d{2,5})x(\d{2,5})[^,]*,(?:[^,]*,)*?\s*"
                       r"(\d+(?:\.\d+)?) (?:fps|tbr)", re.S)
_AUDIO_RE = re.compile(r"Stream #\S+: Audio: (\w+).*?(\d+) Hz")


def probe_media(path: str) -> dict:
    """Inspect a media file with the ffmpeg binary the video stack already depends on.

    Returns {} when the file cannot be inspected at all - the caller turns that into a
    blocking check rather than an exception, so one unreadable artifact cannot crash the run
    before the failure is recorded.

    ffmpeg (not ffprobe) because imageio-ffmpeg ships only the former, and adding a media
    dependency for a handful of fields is not worth it.
    """
    try:
        proc = subprocess.run([_ffmpeg(), "-hide_banner", "-i", path],
                              capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    text = proc.stderr or ""
    info: dict = {"raw": text}

    m = _DURATION_RE.search(text)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info["duration"] = h * 3600 + mi * 60 + s

    m = _VIDEO_RE.search(text)
    if m:
        info["video"] = {"codec": m.group(1), "width": int(m.group(2)),
                         "height": int(m.group(3)), "fps": float(m.group(4))}

    m = _AUDIO_RE.search(text)
    if m:
        info["audio"] = {"codec": m.group(1), "sample_rate": int(m.group(2))}
    return info


def sample_frame_stats(path: str, timestamps: list) -> list:
    """Mean and standard deviation of pixel intensity at each timestamp.

    A frame that fails to decode comes back as `{"decoded": False}` rather than raising, so a
    single bad sample is reported as a finding instead of aborting QA.
    """
    out = []
    for stamp in timestamps:
        try:
            proc = subprocess.run(
                [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{stamp:.2f}",
                 "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, timeout=120)
            if proc.returncode != 0 or not proc.stdout:
                out.append({"t": stamp, "decoded": False})
                continue
            import io

            import numpy as np
            from PIL import Image
            frame = Image.open(io.BytesIO(proc.stdout)).convert("L")
            arr = np.asarray(frame).astype("float64")
            out.append({"t": stamp, "decoded": True, "mean": float(arr.mean()),
                        "stddev": float(arr.std())})
        except Exception as exc:
            out.append({"t": stamp, "decoded": False, "error": f"{type(exc).__name__}: {exc}"})
    return out


# --------------------------------------------------------------------- checks
def check_video(video_path: str, expected_duration: float, metadata_path: str | None = None,
                report_path: str | None = None, expected_width: int = 1080,
                expected_height: int = 1920, duration_tolerance: float = 1.0,
                fps_range: tuple = (24.0, 61.0), now: dt.datetime | None = None,
                probe=probe_media, sample=sample_frame_stats) -> VideoQAResult:
    """Validate a rendered artifact. `probe`/`sample` are injectable for offline tests."""
    result = VideoQAResult(checked_at=now or dt.datetime.now(dt.timezone.utc),
                           video_path=video_path)

    exists = os.path.exists(video_path)
    result.add(QACheck("file_exists", QAStatus.PASS if exists else QAStatus.FAIL,
                       expected="file present", actual=str(exists),
                       message="" if exists else f"no video at {video_path}"))
    if not exists:
        return result

    size = os.path.getsize(video_path)
    if size == 0:
        status, message = QAStatus.FAIL, "rendered file is empty"
    elif size < MIN_PLAUSIBLE_BYTES:
        status, message = QAStatus.FAIL, f"file is implausibly small ({size} bytes)"
    else:
        status, message = QAStatus.PASS, ""
    result.add(QACheck("file_size", status, expected=f">= {MIN_PLAUSIBLE_BYTES} bytes",
                       actual=f"{size} bytes", message=message))
    if status is QAStatus.FAIL:
        return result

    info = probe(video_path)
    inspectable = bool(info) and "error" not in info and ("video" in info or "duration" in info)
    result.add(QACheck("container_readable", QAStatus.PASS if inspectable else QAStatus.FAIL,
                       expected="inspectable container", actual="yes" if inspectable else "no",
                       message="" if inspectable else f"could not inspect: {info.get('error', 'no streams found')}"))
    if not inspectable:
        return result

    video = info.get("video") or {}
    result.add(QACheck("video_stream", QAStatus.PASS if video else QAStatus.FAIL,
                       expected="one video stream", actual=video.get("codec", "none"),
                       message="" if video else "no video stream found"))

    if video:
        width, height = video.get("width"), video.get("height")
        ok = (width, height) == (expected_width, expected_height)
        result.add(QACheck("resolution", QAStatus.PASS if ok else QAStatus.FAIL,
                           expected=f"{expected_width}x{expected_height}",
                           actual=f"{width}x{height}",
                           message="" if ok else "wrong resolution for a vertical Short"))

        fps = video.get("fps")
        ok = fps is not None and fps_range[0] <= fps <= fps_range[1]
        result.add(QACheck("frame_rate", QAStatus.PASS if ok else QAStatus.FAIL,
                           expected=f"{fps_range[0]}-{fps_range[1]} fps", actual=str(fps),
                           message="" if ok else "implausible frame rate"))

    duration = info.get("duration")
    if duration is None:
        result.add(QACheck("duration", QAStatus.FAIL, expected=f"{expected_duration}s",
                           actual="unknown", message="no duration reported"))
    else:
        drift = abs(duration - expected_duration)
        ok = drift <= duration_tolerance
        result.add(QACheck("duration", QAStatus.PASS if ok else QAStatus.FAIL,
                           expected=f"{expected_duration}s +/- {duration_tolerance}s",
                           actual=f"{duration}s",
                           message="" if ok else f"off target by {drift:.2f}s"))

    audio = info.get("audio") or {}
    result.add(QACheck("audio_stream", QAStatus.PASS if audio else QAStatus.FAIL,
                       expected="one audio stream", actual=audio.get("codec", "none"),
                       message="" if audio else "no audio stream found"))

    for label, path in (("metadata_json", metadata_path), ("report_json", report_path)):
        if path is None:
            continue
        present = os.path.exists(path)
        result.add(QACheck(label, QAStatus.PASS if present else QAStatus.FAIL,
                           expected="file present", actual=str(present),
                           message="" if present else f"missing companion artifact {path}"))

    if duration:
        _check_frames(result, video_path, duration, sample)
    return result


def _check_frames(result: VideoQAResult, video_path: str, duration: float, sample) -> None:
    """Catastrophic-render detection only: flat frames and decode failures.

    Deliberately blunt. Anything subtler would either need real computer vision or produce
    false positives on a design that is legitimately dark, and a gate that cries wolf gets
    switched off.
    """
    timestamps = [round(duration * point, 2) for point in SAMPLE_POINTS]
    try:
        stats = sample(video_path, timestamps)
    except Exception as exc:
        result.add(QACheck("frame_sampling", QAStatus.WARN, expected="frames decodable",
                           actual="sampling failed",
                           message=f"could not sample frames ({type(exc).__name__}: {exc})"))
        return

    undecodable = [s for s in stats if not s.get("decoded")]
    if undecodable:
        result.add(QACheck("frame_decode", QAStatus.FAIL, expected="all sampled frames decode",
                           actual=f"{len(undecodable)}/{len(stats)} failed",
                           message=f"frames failed to decode at {[s['t'] for s in undecodable]}"))
        return
    result.add(QACheck("frame_decode", QAStatus.PASS, expected="all sampled frames decode",
                       actual=f"{len(stats)}/{len(stats)} decoded"))

    flat = [s for s in stats if s.get("stddev", 99) < FLAT_FRAME_STDDEV]
    if flat:
        result.add(QACheck("blank_frames", QAStatus.FAIL, expected="frames have visible content",
                           actual=f"{len(flat)} flat frame(s)",
                           message=f"effectively single-colour frames at {[s['t'] for s in flat]} "
                                   "- the renderer produced nothing there"))
    else:
        dark = [s for s in stats if s.get("mean", 99) < DARK_FRAME_MEAN]
        if dark:
            result.add(QACheck("blank_frames", QAStatus.WARN,
                               expected=f"mean intensity >= {DARK_FRAME_MEAN}",
                               actual=f"{len(dark)} very dark frame(s)",
                               message=f"unusually dark but textured frames at {[s['t'] for s in dark]}"
                                       " - dark scenes are by design, so this only warns"))
        else:
            result.add(QACheck("blank_frames", QAStatus.PASS,
                               expected="frames have visible content",
                               actual=f"{len(stats)} sampled frames have content"))


# --------------------------------------------------------------------- artifact
def write_qa_artifact(result: VideoQAResult, out_dir: str, report, report_path: str | None = None,
                      video_path: str | None = None, demo: bool = False,
                      content_qa: dict | None = None, readability: dict | None = None,
                      editorial: dict | None = None) -> str:
    """Write the QA record beside the run's other artifacts.

    This is the operational half of the record: what happened when we tried to render and
    publish the report. It POINTS AT the canonical report rather than copying it - a second
    copy could drift from the immutable original - and it is where the final publication scan
    is recorded, because that verdict describes an execution rather than the market.

    `content_safety` here is the report's own pre-finalisation sanitisation summary, included
    for context; `content_qa` is the final publication scan, which never enters the report.
    """
    directory = os.path.join(out_dir, "qa")
    os.makedirs(directory, exist_ok=True)
    suffix = "_DEMO" if demo else ""
    path = os.path.join(directory, f"qa_{report.report_date:%Y-%m-%d}{suffix}.json")
    payload = {
        "report_id": report.report_id,
        "report_json_artifact": report_path,
        "video_path": video_path or result.video_path,
        "checked_at": result.checked_at.isoformat() if result.checked_at else None,
        "overall_status": result.status.value,
        "passed": result.passed,
        "checks": [c.to_dict() for c in result.checks],
        "blocking_issues": list(result.blocking_issues),
        "warnings": list(result.warnings),
        "content_safety": report.content_safety,
        "final_content_qa": content_qa,
        "readability_qa": readability,
        # The editorial plan is derived presentation state, recorded here so a published
        # Short can be explained later without re-deriving what it chose to say.
        "editorial_plan": editorial,
        "data_validation": report.validation_summary.to_dict(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    return path


__all__ = ["check_video", "write_qa_artifact", "probe_media", "sample_frame_stats",
           "VideoQAResult", "QACheck", "QAStatus", "SAMPLE_POINTS", "FLAT_FRAME_STDDEV",
           "DARK_FRAME_MEAN", "MIN_PLAUSIBLE_BYTES"]
