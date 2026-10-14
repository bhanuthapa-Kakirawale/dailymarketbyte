# Background Music Rotation V1

Light instrumental background music for PRE and POST, added back to the production renderer
(`daily_video/composer.py`) without changing editorial content, scene timing, PK-C/PK-D/PK-E, or
any analytics/scheduling logic. The legacy `video.py` Short (never reached in production) had its
own, older music wiring; this is a fresh integration into the actual renderer, reusing `music.py`'s
generator and rotation logic.

## Asset location and manifest

Real audio lives at `assets/music/`, described by `assets/music/music_manifest.json` - a JSON
list of entries:

```json
{
  "track_id": "a",
  "filename": "morning_lift.mp3",
  "title": "Morning Lift",
  "license_status": "CLEARED",
  "source_note": "owner-composed, 2026-10",
  "enabled": true,
  "duration_seconds": 90
}
```

Only an entry with `license_status: "CLEARED"` **and** `enabled: true` is ever picked
automatically. Everything else - missing, `"NEEDS_RIGHTS_CONFIRMATION"`, disabled, or a malformed
manifest entirely - is treated as "no real track configured", never a crash and never
auto-promoted to CLEARED.

The repo ships with an **empty** manifest (`[]`) - the owner has not cleared any real tracks yet.

### Owner: adding or replacing a track

1. Copy the audio file to `assets/music/` (mp3/wav/m4a).
2. Add a manifest entry with `license_status: "NEEDS_RIGHTS_CONFIRMATION"` and `enabled: false`.
3. Mark it `"CLEARED"` and `enabled: true` only once you've confirmed you have the rights to use it.
4. Run `python main.py --demo` (or a `--session-date` replay) and check the resulting
   `production_manifest.json`'s `music` block picked it up as expected.

## Deterministic rotation

Selection is keyed on the **market session being covered** - never wall-clock time, run id, or
edition. Concretely that's `Composer.music_session_date`, which defaults to the storyboard's own
`session_date` (correct for POST, whose `session_date` IS the session it recaps) but is passed
explicitly by `products/premarket.py` as `brief.previous_session` for PRE - a PRE storyboard's
`session_date` is its own edition date (`brief.pre_date`, e.g. the morning of the 13th), not the
session it discusses (e.g. the 12th). Without this override PRE and POST for the same session
would pick different tracks (caught in this feature's own validation replay - see
`tests/test_music_selection.py::test_composer_music_session_date_overrides_storyboard_session_date`).

1. Build the list of CLEARED + enabled manifest entries, sorted by `track_id`.
2. If non-empty (N tracks): `idx = session_date.toordinal() % N` picks the track.
3. If empty (N = 0, today's real state): the procedural generator (`music.generate`, unchanged)
   synthesizes an original instrumental track - no rights risk, since nothing is sourced
   externally. This is also the existing, pre-existing behavior of `music.py`.

Because both PRE and POST pass the same `session_date` into selection, **PRE and POST for the
same session always get the same track** - brand consistency for that day's edition, per owner
decision. A session rendered months later (replay) selects the same track as long as the enabled
library/order hasn't materially changed.

A manifest entry that IS eligible (CLEARED + enabled) but whose file is missing or fails an audio
probe is **not** silently swapped for the generator - that would mask a real asset problem. It
falls back to a fully silent render instead (`SILENT_FALLBACK`), with the reason recorded.

## Rendering

`daily_video/composer.py`'s `Composer.render()` does the full ffmpeg audio stage, modeled on the
working pattern from the legacy `video.py`:

```
-stream_loop -1 -i <track> -map 0:v -map 1:a -c:a aac -b:a 160k
-af "volume=<MUSIC_VOLUME>,afade=t=in:st=0:d=<FADE_IN>,afade=t=out:st=<total-FADE_OUT>:d=<FADE_OUT>"
```

- **Short track** (shorter than the video): `-stream_loop -1` loops it; the existing `-t <total>`
  flag trims the looped audio exactly to the video's own duration.
- **Long track** (longer than the video): the same `-t <total>` flag trims it. Video duration is
  always authoritative - music never changes it.
- **Volume**: `config.MUSIC_VOLUME` (env `MUSIC_VOLUME`, default `0.22`) - subtle, mixed low,
  since there is no narration.
- **Fades**: `config.MUSIC_FADE_IN_SECONDS` (default `0.75s`) and `config.MUSIC_FADE_OUT_SECONDS`
  (default `1.25s`); the fade-out always ends exactly with the video.

## Failure fallback

Music is strictly optional. Any problem - no cleared tracks, a selected file missing/corrupt
(fails an ffmpeg audio probe before the frame-writing loop even starts), or the ffmpeg mux itself
failing - falls back to a silent render (`-an`). A music problem never blocks, degrades, or fails
production; it only means the MP4 carries no audio stream. `AUDIO_ENABLED`
(`daily_video/composer.py`) is itself env-gated: `DMB_AUDIO_ENABLED` (default `true`) lets the
owner kill music instantly, without a code revert, if it ever causes a problem in production.

## Manifest / auditability

`Composer.render()`'s return dict carries a `music` sub-dict (a *reference*, not a copy of the
full music manifest):

```json
{
  "status": "APPLIED_REAL | APPLIED_PROCEDURAL | SILENT_FALLBACK",
  "track_id": "...", "filename": "...", "title": "...",
  "selection_method": "manifest_rotation | procedural_rotation | procedural | error",
  "reason": "...",
  "volume": 0.22, "fade_in_seconds": 0.75, "fade_out_seconds": 1.25,
  "audio_enabled": true
}
```

This flows into POST's `production_manifest.json` (`music` key, alongside `qa`/`duration`) and
into PRE's `pre_result_<date>.json` (`render.music`), via `main.py`'s `video_qa(..., expect_audio=...)`
which threads the actual outcome into the dynamic QA check rather than a hardcoded literal.

## Renderer QA

`qa/video_qa.check_video`'s existing `expect_audio` parameter is unchanged; what changed is how
callers compute it. `main.py`'s `video_qa()` now derives `expect_audio` from the render's own
`music.status` (`APPLIED_REAL`/`APPLIED_PROCEDURAL` ⇒ an audio stream is required; `SILENT_FALLBACK`
⇒ an audio stream is a FAILURE) rather than the old hardcoded `False`.

## Scope

Music integration changes exactly one thing: the MP4 gains an audio stream (or doesn't) plus
`music` metadata in the manifest. Scene order, duration, text, layout, provenance, hook, editorial
selection, readiness status and production status are all unchanged. PK-D continues to call the
existing renderer unchanged; PK-E's scheduled tasks need no reinstall.
