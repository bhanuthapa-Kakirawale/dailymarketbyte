"""Generates copyright-free procedural background tracks (made from maths, no samples).
Five distinct mood/tempo presets rotate by calendar date so consecutive sessions don't sound
the same. Real audio can override the generator via assets/music/music_manifest.json (see
docs/BACKGROUND_MUSIC.md): only entries marked license_status="CLEARED" and enabled=true are
ever picked automatically - everything else (missing, unreviewed, disabled) is treated as "no
real track" and falls back to the procedural generator, which carries no rights risk since
nothing is sourced externally.
Playback volume/fades are set in config.py (MUSIC_VOLUME, MUSIC_FADE_IN_SECONDS,
MUSIC_FADE_OUT_SECONDS), not here."""
import datetime as dt
import json
import os
import wave
from dataclasses import dataclass

import numpy as np

SR = 44100
MANIFEST_NAME = "music_manifest.json"
CLEARED = "CLEARED"
NEEDS_RIGHTS_CONFIRMATION = "NEEDS_RIGHTS_CONFIRMATION"

APPLIED_REAL = "APPLIED_REAL"
APPLIED_PROCEDURAL = "APPLIED_PROCEDURAL"
SILENT_FALLBACK = "SILENT_FALLBACK"
NO_CLEARED_TRACK = "NO_CLEARED_TRACK"

# Five presets (tempo + chord progression) so the rotation actually sounds different each day.
PRESETS = [
    {"name": "lofi_dreamy", "bpm": 80, "seed": 7,
     "chords": [[57, 60, 64, 67], [53, 57, 60, 64], [48, 55, 59, 64], [55, 59, 62, 64]]},   # Am7 Fmaj7 Cmaj7 G6
    {"name": "jazzy_hop", "bpm": 76, "seed": 21,
     "chords": [[50, 53, 57, 60], [55, 59, 62, 65], [60, 64, 67, 71], [57, 60, 64, 67]]},   # Dm7 G7 Cmaj7 Am7
    {"name": "morning_lift", "bpm": 90, "seed": 33,
     "chords": [[53, 57, 60, 64], [55, 59, 62, 67], [52, 55, 59, 62], [57, 60, 64, 67]]},   # Fmaj7 G Em7 Am7
    {"name": "warm_pop", "bpm": 84, "seed": 45,
     "chords": [[60, 64, 67, 71], [57, 60, 64, 67], [50, 53, 57, 60], [55, 59, 62, 65]]},   # Cmaj7 Am7 Dm7 G7
    {"name": "bright_piano", "bpm": 70, "seed": 59,
     "chords": [[52, 55, 59, 62], [57, 61, 64, 67], [50, 54, 57, 61], [59, 62, 66, 69]]},   # Em7 A7 Dmaj7 Bm7
]


def _f(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def generate(path: str, seconds: float = 76.0, bpm: int = 80, seed: int = 7, chords=None, swells=()):
    chords = chords or PRESETS[0]["chords"]
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    L, R = np.zeros(n), np.zeros(n)
    beat = 60 / bpm
    bar = beat * 4

    t_bar = np.arange(int(bar * SR)) / SR
    env = np.minimum(1, t_bar / 0.5) * np.minimum(1, (bar - t_bar) / 0.6)
    k = 0
    while k * bar < seconds:
        s = int(k * bar * SR)
        e = min(n, s + len(t_bar))
        ch = chords[k % 4]
        pad_l = sum(np.sin(2 * np.pi * _f(m) * 0.998 * t_bar) + 0.25 * np.sin(4 * np.pi * _f(m) * t_bar) for m in ch)
        pad_r = sum(np.sin(2 * np.pi * _f(m) * 1.002 * t_bar) + 0.25 * np.sin(4 * np.pi * _f(m) * t_bar) for m in ch)
        bass = np.sin(2 * np.pi * _f(ch[0] - 12) * t_bar)
        L[s:e] += ((pad_l * 0.05 + bass * 0.12) * env)[: e - s]
        R[s:e] += ((pad_r * 0.05 + bass * 0.12) * env)[: e - s]
        # soft arpeggio (eighth notes)
        pattern = [0, 1, 2, 3, 2, 1, 2, 3]
        for j, p in enumerate(pattern):
            ps = s + int(j * beat / 2 * SR)
            tt = np.arange(int(beat * SR)) / SR
            note = np.sin(2 * np.pi * _f(ch[p] + 12) * tt) * np.exp(-tt * 5) * 0.07
            pe = min(n, ps + len(tt))
            if ps < n:
                pan = 0.35 + 0.3 * (j % 2)
                L[ps:pe] += note[: pe - ps] * (1 - pan)
                R[ps:pe] += note[: pe - ps] * pan
        # gentle kick on 1 & 3, hats on off-beats
        for b in range(4):
            bs = s + int(b * beat * SR)
            if bs >= n:
                continue
            if b in (0, 2):
                tt = np.arange(int(0.35 * SR)) / SR
                freq = 45 + 70 * np.exp(-tt * 25)
                kick = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-tt * 10) * 0.17
                ke = min(n, bs + len(tt))
                L[bs:ke] += kick[: ke - bs]
                R[bs:ke] += kick[: ke - bs]
            hs = bs + int(beat / 2 * SR)
            tt = np.arange(int(0.08 * SR)) / SR
            hat = np.diff(rng.standard_normal(len(tt) + 1)) * np.exp(-tt * 60) * 0.02
            he = min(n, hs + len(tt))
            if hs < n:
                L[hs:he] += hat[: he - hs]
                R[hs:he] += hat[: he - hs] * 0.8
        k += 1

    # gentle "whoosh" swell into each scene change
    for b in swells:
        pre, post = 0.6, 0.35
        s0, s1 = int((b - pre) * SR), int((b + post) * SR)
        if s0 < 0 or s1 > n:
            continue
        noise = np.convolve(rng.standard_normal(s1 - s0), np.ones(60) / 60, mode="same")
        tt = np.arange(s1 - s0) / SR
        env = np.where(tt < pre, (tt / pre) ** 2, np.exp(-(tt - pre) * 12))
        sw = noise * env * 0.35
        L[s0:s1] += sw
        R[s0:s1] += sw[::-1]

    st = np.stack([L, R], axis=1)
    st = st / (np.abs(st).max() + 1e-9) * 0.55
    fade_in, fade_out = int(1.0 * SR), int(2.5 * SR)
    st[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    st[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    data = (st * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return path


def _variant_index(date: dt.date) -> int:
    return date.toordinal() % len(PRESETS)


@dataclass
class MusicSelection:
    """What get_music() picked, for QA/manifest reporting - never used to drive content."""
    path: str | None
    status: str                      # APPLIED_REAL | APPLIED_PROCEDURAL | SILENT_FALLBACK
    track_id: str | None = None
    filename: str | None = None
    title: str | None = None
    selection_method: str = ""
    reason: str = ""


def _load_manifest(assets_dir: str) -> list:
    """Only CLEARED + enabled entries are eligible for automatic rotation. A missing/malformed
    manifest is treated as "no real tracks" (falls through to the procedural generator), never
    a crash. Sorted by track_id for a stable rotation order."""
    path = os.path.join(assets_dir, "music", MANIFEST_NAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    tracks = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("license_status") != CLEARED or not entry.get("enabled"):
            continue
        track_id, filename = entry.get("track_id"), entry.get("filename")
        if not track_id or not filename:
            continue
        tracks.append({"track_id": str(track_id), "filename": str(filename),
                       "title": entry.get("title", "")})
    tracks.sort(key=lambda t: t["track_id"])
    return tracks


def get_music(assets_dir: str, out_dir: str, seconds: float = 76.0, swells=(),
             date: dt.date = None) -> MusicSelection:
    """Picks the day's track, keyed off `date` - the DMB session date, never wall-clock - so the
    same session always resolves to the same track and PRE/POST share it for that session.

    1. A CLEARED + enabled real track from assets/music/music_manifest.json, chosen
       deterministically by `date` among the eligible tracks (docs/BACKGROUND_MUSIC.md).
    2. If none are configured at all, the procedural generator - self-synthesized, so it carries
       no rights risk, and is the right default while the owner's cleared library is still empty.

    A manifest entry that IS eligible but whose file is missing/unreadable is NOT silently
    swapped for the generator (that would mask a real asset problem going unnoticed) - it comes
    back as SILENT_FALLBACK and the caller (daily_video/composer.py) renders without audio."""
    date = date or dt.date.today()
    tracks = _load_manifest(assets_dir)
    if tracks:
        idx = date.toordinal() % len(tracks)
        chosen = tracks[idx]
        path = os.path.join(assets_dir, "music", chosen["filename"])
        if os.path.exists(path):
            return MusicSelection(path=path, status=APPLIED_REAL, track_id=chosen["track_id"],
                                  filename=chosen["filename"], title=chosen["title"],
                                  selection_method="manifest_rotation")
        return MusicSelection(
            path=None, status=SILENT_FALLBACK, track_id=chosen["track_id"],
            filename=chosen["filename"], title=chosen["title"], selection_method="manifest_rotation",
            reason=f"manifest track {chosen['track_id']!r} file not found: {chosen['filename']}")
    idx = _variant_index(date)
    preset = PRESETS[idx]
    out = os.path.join(out_dir, f"bg_music_{idx}.wav")
    try:
        path = generate(out, seconds + 1, bpm=preset["bpm"], seed=preset["seed"],
                        chords=preset["chords"], swells=swells)
    except Exception as exc:
        return MusicSelection(path=None, status=SILENT_FALLBACK, selection_method="procedural",
                              reason=f"procedural generation failed: {type(exc).__name__}: {exc}")
    return MusicSelection(path=path, status=APPLIED_PROCEDURAL,
                          track_id=f"procedural:{preset['name']}", filename=os.path.basename(path),
                          title=preset["name"], selection_method="procedural_rotation",
                          reason="no CLEARED/enabled real track configured")
