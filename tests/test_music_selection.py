"""Background Music Rotation V1 (docs/BACKGROUND_MUSIC.md): deterministic manifest-driven
selection in `music.get_music`, pre-flight validation + ffmpeg audio stage in
`daily_video.composer.Composer.render`, and the dynamic `expect_audio` QA wiring in `main.py`.

Rotation is keyed on the VIDEO's own production/edition date (`Composer.music_rotation_date`),
never the underlying market session a PRE or POST edition discusses - owner correction after the
first validation replay surfaced a PRE/POST mismatch. `SESSION`/`PREV` below are plain calendar
dates used as rotation keys in these tests; they do not represent market sessions.

Fully offline: real (tiny) WAV fixtures and the real ffmpeg binary already bundled by
imageio-ffmpeg - no network, no copyrighted/downloaded audio.
"""
import datetime as dt
import json
import os
import wave

import numpy as np
import pytest

import music

SESSION, PREV = dt.date(2026, 9, 25), dt.date(2026, 9, 24)


# --------------------------------------------------------------------------- fixtures
def _write_wav(path: str, seconds: float, freq: float = 440.0) -> str:
    sr = 44100
    t = np.arange(int(seconds * sr)) / sr
    data = (0.2 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    stereo = np.stack([data, data], axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(stereo.tobytes())
    return path


def _manifest(assets_dir: str, entries: list) -> str:
    music_dir = os.path.join(assets_dir, "music")
    os.makedirs(music_dir, exist_ok=True)
    path = os.path.join(music_dir, music.MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh)
    return music_dir


def _cleared(track_id, filename, enabled=True, duration=2.0):
    return {"track_id": track_id, "filename": filename, "title": f"Track {track_id}",
            "license_status": music.CLEARED, "enabled": enabled, "source_note": "test fixture",
            "duration_seconds": duration}


# --------------------------------------------------------------------------- selection
def test_no_cleared_tracks_falls_back_to_procedural(tmp_path):
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(assets, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    sel = music.get_music(str(assets), str(out), seconds=3.0, date=SESSION)
    assert sel.status == music.APPLIED_PROCEDURAL
    assert sel.path and os.path.exists(sel.path)
    assert sel.track_id.startswith("procedural:")


def test_manifest_rotation_is_deterministic_per_session_date(tmp_path):
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(out, exist_ok=True)
    music_dir = _manifest(str(assets), [_cleared("a", "a.wav"), _cleared("b", "b.wav")])
    _write_wav(os.path.join(music_dir, "a.wav"), 1.0)
    _write_wav(os.path.join(music_dir, "b.wav"), 1.0)

    sel1 = music.get_music(str(assets), str(out), seconds=1.0, date=SESSION)
    sel2 = music.get_music(str(assets), str(out), seconds=1.0, date=SESSION)
    assert sel1.status == sel2.status == music.APPLIED_REAL
    assert sel1.track_id == sel2.track_id == ("a" if SESSION.toordinal() % 2 == 0 else "b")
    assert sel1.filename == sel2.filename


def test_pre_and_post_share_one_track_for_the_same_session(tmp_path):
    """PRE and POST never select independently - both key off the same `session_date`."""
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(out, exist_ok=True)
    music_dir = _manifest(str(assets), [_cleared("a", "a.wav"), _cleared("b", "b.wav"),
                                        _cleared("c", "c.wav")])
    for name in ("a.wav", "b.wav", "c.wav"):
        _write_wav(os.path.join(music_dir, name), 1.0)

    pre_sel = music.get_music(str(assets), str(out), seconds=1.0, date=SESSION)
    post_sel = music.get_music(str(assets), str(out), seconds=1.0, date=SESSION)
    assert pre_sel.track_id == post_sel.track_id
    assert pre_sel.filename == post_sel.filename


def test_rotation_differs_across_consecutive_session_dates(tmp_path):
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(out, exist_ok=True)
    tracks = [_cleared(str(i), f"{i}.wav") for i in range(3)]
    music_dir = _manifest(str(assets), tracks)
    for i in range(3):
        _write_wav(os.path.join(music_dir, f"{i}.wav"), 1.0)

    dates = [SESSION + dt.timedelta(days=i) for i in range(5)]
    picked = [music.get_music(str(assets), str(out), seconds=1.0, date=d).track_id for d in dates]
    expected = [str(d.toordinal() % 3) for d in dates]
    assert picked == expected
    assert len(set(picked)) > 1          # a 5-day span over 3 tracks must actually rotate


def test_manifest_excludes_disabled_and_unreviewed_tracks(tmp_path):
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(out, exist_ok=True)
    music_dir = _manifest(str(assets), [
        _cleared("a", "a.wav", enabled=False),
        {"track_id": "b", "filename": "b.wav", "title": "Unreviewed",
         "license_status": music.NEEDS_RIGHTS_CONFIRMATION, "enabled": True},
        _cleared("c", "c.wav"),
    ])
    _write_wav(os.path.join(music_dir, "c.wav"), 1.0)
    sel = music.get_music(str(assets), str(out), seconds=1.0, date=SESSION)
    assert sel.status == music.APPLIED_REAL
    assert sel.track_id == "c"           # the only CLEARED + enabled entry


def test_manifest_selected_file_missing_is_silent_not_procedural(tmp_path):
    """A manifest entry that IS eligible but whose file is missing is a real asset problem -
    it must not be silently papered over by the procedural generator."""
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(out, exist_ok=True)
    _manifest(str(assets), [_cleared("a", "missing.wav")])
    sel = music.get_music(str(assets), str(out), seconds=1.0, date=SESSION)
    assert sel.status == music.SILENT_FALLBACK
    assert sel.path is None
    assert "missing.wav" in sel.reason


def test_malformed_manifest_is_treated_as_no_tracks(tmp_path):
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    music_dir = os.path.join(assets, "music")
    os.makedirs(music_dir, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(music_dir, music.MANIFEST_NAME), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    sel = music.get_music(str(assets), str(out), seconds=3.0, date=SESSION)
    assert sel.status == music.APPLIED_PROCEDURAL, sel.reason


# --------------------------------------------------------------------------- composer wiring
def _tiny_storyboard():
    """A one-scene storyboard just long enough to exercise the audio stage without a full
    POST render - fps=1 keeps the frame-writing loop trivial."""
    from test_public_publication import _post
    sb = _post()
    sb.scenes = sb.scenes[-1:]
    return sb


def test_composer_applies_a_short_track_looped_and_trimmed_to_duration(tmp_path, monkeypatch):
    import config
    import daily_video.composer as comp
    from qa.video_qa import probe_media

    assets = tmp_path / "assets"
    music_dir = _manifest(str(assets), [_cleared("a", "a.wav")])
    _write_wav(os.path.join(music_dir, "a.wav"), 1.0)     # shorter than the rendered video
    monkeypatch.setattr(config, "ASSETS_DIR", str(assets))
    monkeypatch.setattr(comp, "AUDIO_ENABLED", True)

    sb = _tiny_storyboard()
    assert sb.total_duration > 1.0
    out = tmp_path / "short_track.mp4"
    render = comp.Composer(sb).render(str(out), fps=1)
    assert render["ok"]
    assert render["music"]["status"] == music.APPLIED_REAL
    assert render["music"]["origin"] == music.ORIGIN_REAL
    assert render["music"]["rotation_date"] == sb.session_date.isoformat()
    info = probe_media(str(out))
    assert info.get("audio"), "looped short track must still produce an audio stream"
    assert abs(info["duration"] - sb.total_duration) < 1.0


def test_composer_trims_a_long_track_to_duration(tmp_path, monkeypatch):
    import config
    import daily_video.composer as comp
    from qa.video_qa import probe_media

    assets = tmp_path / "assets"
    music_dir = _manifest(str(assets), [_cleared("a", "a.wav")])
    _write_wav(os.path.join(music_dir, "a.wav"), 30.0)    # longer than the rendered video
    monkeypatch.setattr(config, "ASSETS_DIR", str(assets))
    monkeypatch.setattr(comp, "AUDIO_ENABLED", True)

    sb = _tiny_storyboard()
    out = tmp_path / "long_track.mp4"
    render = comp.Composer(sb).render(str(out), fps=1)
    assert render["ok"]
    info = probe_media(str(out))
    assert info.get("audio")
    assert abs(info["duration"] - sb.total_duration) < 1.0     # video duration is authoritative


def test_composer_falls_back_silent_for_a_corrupt_track(tmp_path, monkeypatch):
    import config
    import daily_video.composer as comp
    from qa.video_qa import probe_media

    assets = tmp_path / "assets"
    music_dir = _manifest(str(assets), [_cleared("a", "a.mp3")])
    with open(os.path.join(music_dir, "a.mp3"), "wb") as fh:
        fh.write(os.urandom(2048))                        # exists, but not decodable audio
    monkeypatch.setattr(config, "ASSETS_DIR", str(assets))
    monkeypatch.setattr(comp, "AUDIO_ENABLED", True)

    sb = _tiny_storyboard()
    out = tmp_path / "corrupt.mp4"
    render = comp.Composer(sb).render(str(out), fps=1)
    assert render["ok"], "a corrupt music asset must never fail the render"
    assert render["music"]["status"] == music.SILENT_FALLBACK
    info = probe_media(str(out))
    assert not info.get("audio")


def test_composer_uses_procedural_track_when_library_is_empty(tmp_path, monkeypatch):
    import config
    import daily_video.composer as comp
    from qa.video_qa import probe_media

    assets = tmp_path / "assets"
    os.makedirs(assets, exist_ok=True)
    monkeypatch.setattr(config, "ASSETS_DIR", str(assets))
    monkeypatch.setattr(comp, "AUDIO_ENABLED", True)

    sb = _tiny_storyboard()
    out = tmp_path / "procedural.mp4"
    render = comp.Composer(sb).render(str(out), fps=1)
    assert render["ok"]
    assert render["music"]["status"] == music.APPLIED_PROCEDURAL
    assert render["music"]["origin"] == music.ORIGIN_PROCEDURAL
    assert render["music"]["reason_code"] == music.REASON_NO_CLEARED_REAL_TRACK
    info = probe_media(str(out))
    assert info.get("audio")


def test_composer_music_rotation_date_defaults_to_storyboard_session_date(tmp_path):
    """Composer.music_rotation_date defaults to storyboard.session_date when no explicit
    override is given (the right default for direct/review callers that have no separate
    "edition" concept)."""
    import daily_video.composer as comp

    sb = _tiny_storyboard()
    c = comp.Composer(sb)
    assert c.music_rotation_date == sb.session_date


def test_music_rotation_key_is_the_edition_date_not_the_session_discussed(tmp_path, monkeypatch):
    """Owner correction: rotation must use the VIDEO's own production/edition date, never the
    underlying market session either edition discusses. A PRE storyboard's own `session_date`
    IS its edition date (`brief.pre_date`); POST's `session_date` is the session it RECAPS, one
    or more days earlier than its own edition date (`report.report_date`) - so Composer must be
    given an explicit `music_rotation_date` override for POST (main.py passes `report.report_date`)
    while PRE needs none (products/premarket.py still passes `brief.pre_date` explicitly, for
    clarity, even though it equals `sb.session_date` already)."""
    import config
    import daily_video.composer as comp

    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(out, exist_ok=True)
    music_dir = _manifest(str(assets), [_cleared("a", "a.wav"), _cleared("b", "b.wav"),
                                        _cleared("c", "c.wav")])
    for name in ("a.wav", "b.wav", "c.wav"):
        _write_wav(os.path.join(music_dir, name), 1.0)
    monkeypatch.setattr(config, "ASSETS_DIR", str(assets))

    pre_edition_date = dt.date(2026, 10, 13)          # Tuesday: PRE's own `brief.pre_date`
    pre_previous_session = dt.date(2026, 10, 12)      # Monday's close - what PRE DISCUSSES
    post_edition_date = pre_edition_date              # POST airs the SAME Tuesday morning
    post_session_date = pre_previous_session          # POST recaps the SAME Monday close
    assert pre_edition_date != pre_previous_session   # the exact case this guards against

    sb_pre = _tiny_storyboard()
    sb_pre.session_date = pre_edition_date
    sb_post = _tiny_storyboard()
    sb_post.session_date = post_session_date          # POST's session_date is NOT its edition

    c_pre = comp.Composer(sb_pre, music_rotation_date=pre_edition_date)
    c_post = comp.Composer(sb_post, music_rotation_date=post_edition_date)
    assert c_pre.music_rotation_date == c_post.music_rotation_date == pre_edition_date

    sel_pre = comp._select_music(c_pre.music_rotation_date, 1.0, str(out))
    sel_post = comp._select_music(c_post.music_rotation_date, 1.0, str(out))
    assert sel_pre.track_id == sel_post.track_id      # SAME edition day -> SAME track
    expected = music.get_music(str(assets), str(out), seconds=1.0, date=pre_edition_date)
    assert sel_pre.track_id == expected.track_id

    # a naive composer that forgot the override (defaulting to storyboard.session_date) would
    # have picked a DIFFERENT track for POST - proving the override is load-bearing, not a no-op
    naive_post_sel = comp._select_music(sb_post.session_date, 1.0, str(out))
    assert naive_post_sel.track_id != sel_post.track_id or post_session_date == post_edition_date


def test_rotation_key_rotates_across_consecutive_edition_dates(tmp_path):
    """Section 15.C: several consecutive PRODUCTION/EDITION dates - never market sessions -
    rotate deterministically, and re-querying the same edition date always repeats (15.D)."""
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(out, exist_ok=True)
    tracks = [_cleared(str(i), f"{i}.wav") for i in range(3)]
    music_dir = _manifest(str(assets), tracks)
    for i in range(3):
        _write_wav(os.path.join(music_dir, f"{i}.wav"), 1.0)

    edition_dates = [SESSION + dt.timedelta(days=i) for i in range(5)]
    picked = [music.get_music(str(assets), str(out), seconds=1.0, date=d).track_id
             for d in edition_dates]
    assert picked == [str(d.toordinal() % 3) for d in edition_dates]
    assert len(set(picked)) > 1
    # re-rendering the first edition date again selects the same track
    repeat = music.get_music(str(assets), str(out), seconds=1.0, date=edition_dates[0])
    assert repeat.track_id == picked[0]


def test_procedural_generation_failure_falls_back_to_silent(tmp_path, monkeypatch):
    """Section 15.H: if the approved first-party procedural generator itself raises, the result
    is SILENT_FALLBACK - never an exception propagating into the renderer."""
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    os.makedirs(assets, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    monkeypatch.setattr(music, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sel = music.get_music(str(assets), str(out), seconds=3.0, date=SESSION)
    assert sel.status == music.SILENT_FALLBACK
    assert sel.reason_code == music.REASON_PROCEDURAL_GENERATION_FAILED
    assert sel.origin == music.ORIGIN_NONE


def test_composer_is_silent_when_audio_phase_disabled(monkeypatch, tmp_path):
    import daily_video.composer as comp
    from qa.video_qa import probe_media

    monkeypatch.setattr(comp, "AUDIO_ENABLED", False)
    sb = _tiny_storyboard()
    out = tmp_path / "disabled.mp4"
    render = comp.Composer(sb).render(str(out), fps=1)
    assert render["ok"]
    assert render["music"]["status"] == music.SILENT_FALLBACK
    assert render["music"]["origin"] == music.ORIGIN_NONE
    assert render["music"]["reason_code"] == music.REASON_AUDIO_PHASE_DISABLED
    assert render["music"]["audio_enabled"] is False
    assert render["music"]["rotation_date"] == sb.session_date.isoformat()
    info = probe_media(str(out))
    assert not info.get("audio")


# --------------------------------------------------------------------------- QA wiring
def test_video_qa_expect_audio_is_threaded_through(monkeypatch, tmp_path):
    import main

    captured = {}

    def _fake_check_video(*a, **kw):
        captured["expect_audio"] = kw.get("expect_audio")
        from types import SimpleNamespace
        return SimpleNamespace(passed=True, warnings=[], status=SimpleNamespace(value="PASS"))

    monkeypatch.setattr(main, "check_video", _fake_check_video)
    main.video_qa("x.mp4", "x.json", "report.json", 50.0, False, expect_audio=True)
    assert captured["expect_audio"] is True
    main.video_qa("x.mp4", "x.json", "report.json", 50.0, False, expect_audio=False)
    assert captured["expect_audio"] is False
