"""Generates a soft lo-fi background track (copyright-free, made from maths).
Drop your own file at assets/music.mp3 (or .wav) to use that instead."""
import os
import wave

import numpy as np

SR = 44100


def _f(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def generate(path: str, seconds: float = 76.0, bpm: int = 80, seed: int = 7, swells=()):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    L, R = np.zeros(n), np.zeros(n)
    beat = 60 / bpm
    bar = beat * 4
    chords = [[57, 60, 64, 67], [53, 57, 60, 64], [48, 55, 59, 64], [55, 59, 62, 64]]  # Am7 Fmaj7 Cmaj7 G6

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


def get_music(assets_dir: str, out_dir: str, seconds: float = 76.0, swells=()) -> str:
    for name in ("music.mp3", "music.wav", "music.m4a"):
        p = os.path.join(assets_dir, name)
        if os.path.exists(p):
            return p
    return generate(os.path.join(out_dir, "bg_music.wav"), seconds + 1, swells=swells)
