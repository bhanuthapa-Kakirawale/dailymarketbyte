"""Post-render artifact QA. The media layer is stubbed so tests never need ffmpeg."""
import datetime as dt
import json
import os

import pytest

from conftest import NOW, REPORT_DATE, build_test_report

from qa.video_qa import (DARK_FRAME_MEAN, FLAT_FRAME_STDDEV, MIN_PLAUSIBLE_BYTES, QAStatus,
                         VideoQAResult, check_video, write_qa_artifact)

GOOD_PROBE = {"duration": 75.0,
              "video": {"codec": "h264", "width": 1080, "height": 1920, "fps": 30.0},
              "audio": {"codec": "aac", "sample_rate": 44100}}


def _good_frames(path, timestamps):
    """Frames as the real renderer produces them: dark by design, but textured."""
    return [{"t": t, "decoded": True, "mean": 40.0, "stddev": 45.0} for t in timestamps]


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "out.mp4"
    path.write_bytes(b"\0" * (MIN_PLAUSIBLE_BYTES + 1000))
    return str(path)


@pytest.fixture
def companions(tmp_path):
    meta, report = tmp_path / "out.json", tmp_path / "report.json"
    meta.write_text("{}", encoding="utf-8")
    report.write_text("{}", encoding="utf-8")
    return str(meta), str(report)


def _check(video_file, companions, probe=None, sample=_good_frames, **kw):
    meta_path, report_path = companions
    return check_video(video_file, expected_duration=kw.pop("expected_duration", 75.0),
                       metadata_path=meta_path, report_path=report_path, now=NOW,
                       probe=lambda p: (probe if probe is not None else GOOD_PROBE),
                       sample=sample, **kw)


def _named(result, name):
    return next(c for c in result.checks if c.name == name)


# --------------------------------------------------------------------- the happy path
def test_valid_artifact_passes(video_file, companions):
    result = _check(video_file, companions)
    assert result.status is QAStatus.PASS
    assert result.passed
    assert result.blocking_issues == [] and result.warnings == []
    assert {c.name for c in result.checks} >= {
        "file_exists", "file_size", "container_readable", "video_stream", "resolution",
        "frame_rate", "duration", "audio_stream", "metadata_json", "report_json",
        "frame_decode", "blank_frames"}


# --------------------------------------------------------------------- blocking failures
def test_missing_file_blocks(tmp_path, companions):
    result = _check(str(tmp_path / "nope.mp4"), companions)
    assert not result.passed
    assert _named(result, "file_exists").status is QAStatus.FAIL


def test_zero_byte_file_blocks(tmp_path, companions):
    path = tmp_path / "empty.mp4"
    path.write_bytes(b"")
    result = _check(str(path), companions)
    assert not result.passed
    assert _named(result, "file_size").status is QAStatus.FAIL


def test_implausibly_small_file_blocks(tmp_path, companions):
    path = tmp_path / "tiny.mp4"
    path.write_bytes(b"\0" * 512)
    result = _check(str(path), companions)
    assert not result.passed
    assert "implausibly small" in _named(result, "file_size").message


def test_uninspectable_container_blocks(video_file, companions):
    result = _check(video_file, companions, probe={"error": "Invalid data found"})
    assert not result.passed
    assert _named(result, "container_readable").status is QAStatus.FAIL


def test_missing_video_stream_blocks(video_file, companions):
    probe = {"duration": 75.0, "audio": GOOD_PROBE["audio"]}
    result = _check(video_file, companions, probe=probe)
    assert not result.passed
    assert _named(result, "video_stream").status is QAStatus.FAIL


def test_wrong_resolution_blocks(video_file, companions):
    probe = dict(GOOD_PROBE, video={"codec": "h264", "width": 1920, "height": 1080, "fps": 30.0})
    result = _check(video_file, companions, probe=probe)
    assert not result.passed
    check = _named(result, "resolution")
    assert check.status is QAStatus.FAIL
    assert check.expected == "1080x1920" and check.actual == "1920x1080"


def test_unreasonable_duration_blocks(video_file, companions):
    probe = dict(GOOD_PROBE, duration=41.0)
    result = _check(video_file, companions, probe=probe)
    assert not result.passed
    assert _named(result, "duration").status is QAStatus.FAIL


def test_duration_within_tolerance_passes(video_file, companions):
    result = _check(video_file, companions, probe=dict(GOOD_PROBE, duration=75.4))
    assert result.passed


def test_missing_duration_blocks(video_file, companions):
    probe = {"video": GOOD_PROBE["video"], "audio": GOOD_PROBE["audio"]}
    result = _check(video_file, companions, probe=probe)
    assert not result.passed
    assert _named(result, "duration").status is QAStatus.FAIL


def test_missing_audio_blocks(video_file, companions):
    probe = {"duration": 75.0, "video": GOOD_PROBE["video"]}
    result = _check(video_file, companions, probe=probe)
    assert not result.passed
    assert _named(result, "audio_stream").status is QAStatus.FAIL


def test_implausible_frame_rate_blocks(video_file, companions):
    probe = dict(GOOD_PROBE, video=dict(GOOD_PROBE["video"], fps=3.0))
    result = _check(video_file, companions, probe=probe)
    assert not result.passed
    assert _named(result, "frame_rate").status is QAStatus.FAIL


def test_missing_companion_artifacts_block(video_file, tmp_path):
    result = check_video(video_file, expected_duration=75.0,
                         metadata_path=str(tmp_path / "absent.json"),
                         report_path=str(tmp_path / "absent_report.json"),
                         now=NOW, probe=lambda p: GOOD_PROBE, sample=_good_frames)
    assert not result.passed
    assert _named(result, "metadata_json").status is QAStatus.FAIL
    assert _named(result, "report_json").status is QAStatus.FAIL


# --------------------------------------------------------------------- frame policy
def test_flat_frame_blocks(video_file, companions):
    """A single-colour frame means the renderer produced nothing there."""
    def _flat(path, timestamps):
        frames = _good_frames(path, timestamps)
        frames[2] = {"t": timestamps[2], "decoded": True, "mean": 0.0, "stddev": 0.0}
        return frames

    result = _check(video_file, companions, sample=_flat)
    assert not result.passed
    assert _named(result, "blank_frames").status is QAStatus.FAIL


def test_frame_decode_failure_blocks(video_file, companions):
    def _broken(path, timestamps):
        return [{"t": t, "decoded": False} for t in timestamps]

    result = _check(video_file, companions, sample=_broken)
    assert not result.passed
    assert _named(result, "frame_decode").status is QAStatus.FAIL


def test_dark_but_textured_frames_only_warn(video_file, companions):
    """The design is legitimately dark. A gate that cries wolf gets switched off, so
    merely-dark frames warn and publication continues."""
    def _dark(path, timestamps):
        return [{"t": t, "decoded": True, "mean": DARK_FRAME_MEAN - 1, "stddev": 20.0}
                for t in timestamps]

    result = _check(video_file, companions, sample=_dark)
    assert result.status is QAStatus.WARN
    assert result.passed, "warnings must not block publication"
    assert result.warnings and not result.blocking_issues


def test_real_renderer_frame_statistics_pass(video_file, companions):
    """Measured from an actual render: mean ~30-49, stddev ~30-51. The thresholds must leave
    a wide margin so ordinary dark scenes never trip them."""
    def _real(path, timestamps):
        return [{"t": t, "decoded": True, "mean": 29.8, "stddev": 30.6} for t in timestamps]

    result = _check(video_file, companions, sample=_real)
    assert result.status is QAStatus.PASS
    assert 29.8 > DARK_FRAME_MEAN and 30.6 > FLAT_FRAME_STDDEV


def test_sampling_failure_warns_rather_than_blocks(video_file, companions):
    def _explode(path, timestamps):
        raise OSError("ffmpeg unavailable")

    result = _check(video_file, companions, sample=_explode)
    assert result.passed
    assert _named(result, "frame_sampling").status is QAStatus.WARN


# --------------------------------------------------------------------- serialisation
def test_result_serialises(video_file, companions):
    payload = _check(video_file, companions).to_dict()
    assert payload["status"] == "PASS" and payload["passed"] is True
    assert payload["checked_at"] == NOW.isoformat()
    assert all({"name", "status", "expected", "actual", "message"} <= set(c)
               for c in payload["checks"])
    json.dumps(payload)                                  # must be JSON-serialisable


def test_qa_artifact_is_written_and_points_at_the_report(tmp_path, video_file, companions,
                                                         market_dict):
    report = build_test_report(market_dict,
                               content_safety={"status": "SAFE", "sanitized_count": 0,
                                               "blocked_count": 0})
    result = _check(video_file, companions)
    path = write_qa_artifact(result, str(tmp_path), report, report_path=companions[1],
                             video_path=video_file)

    assert os.path.basename(path) == f"qa_{REPORT_DATE:%Y-%m-%d}.json"
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["report_id"] == report.report_id
    assert payload["overall_status"] == "PASS"
    assert payload["report_json_artifact"] == companions[1]
    assert payload["data_validation"]["total_facts"] == len(report.facts)
    assert payload["content_safety"]["status"] == "SAFE"
    assert "facts" not in payload, "the QA artifact points at the report, it does not copy it"


def test_demo_qa_artifact_is_named_separately(tmp_path, video_file, companions, market_dict):
    report = build_test_report(market_dict, demo=True)
    path = write_qa_artifact(_check(video_file, companions), str(tmp_path), report, demo=True)
    assert path.endswith("_DEMO.json")


def test_failed_result_records_blocking_issues(video_file, companions):
    result = _check(video_file, companions,
                    probe=dict(GOOD_PROBE, video={"codec": "h264", "width": 640,
                                                  "height": 480, "fps": 30.0}))
    payload = result.to_dict()
    assert payload["status"] == "FAIL" and payload["passed"] is False
    assert any("resolution" in issue for issue in payload["blocking_issues"])
