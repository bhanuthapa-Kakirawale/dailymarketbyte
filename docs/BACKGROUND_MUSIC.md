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

Selection is keyed on the **VIDEO's own production/edition date** - never wall-clock time, run
id, random state, and never the underlying market session either edition discusses.
Concretely that's `Composer.music_rotation_date`:

- **POST**: `main.py` passes `report.report_date` explicitly - the canonical report's own "next
  edition" field, i.e. the calendar morning POST is published for. POST's `session_date` is the
  session it *recaps* (typically one or more calendar days earlier - a weekend/holiday gap makes
  this more than one day), so it is never used for rotation.
- **PRE**: `products/premarket.py` passes `brief.pre_date` explicitly - the calendar morning PRE
  is published for. PRE's `brief.previous_session` (the close it discusses) is never used for
  rotation either.

**Owner correction (2026-10-14):** an earlier version of this feature keyed POST on
`storyboard.session_date` and PRE on `brief.previous_session` - i.e. both on the *market session
discussed*. That happened to agree for a same-morning PRE+POST pair in the common case (PRE's
previous session and POST's session are usually the same session), but it keyed on the wrong
concept: a PRE edition and the POST edition that airs the same morning are the right pairing, and
the fix makes that explicit and correct rather than incidental. See
`tests/test_music_selection.py::test_music_rotation_key_is_the_edition_date_not_the_session_discussed`.

1. Build the list of CLEARED + enabled manifest entries, sorted by `track_id`.
2. If non-empty (N tracks): `idx = rotation_date.toordinal() % N` picks the track.
3. If empty (N = 0, today's real state): the approved first-party procedural generator
   (`music.generate`, unchanged) synthesizes an original instrumental track - it is its OWN
   explicit approval class (`origin: "PROCEDURAL"`, owner-approved, self-synthesized so it never
   needs to pass the external CLEARED-manifest rights gate a real file does), not an implicit
   bypass of that gate.

Because POST and PRE for the same production morning both resolve to the same `rotation_date`,
**that PRE+POST pair always gets the same track** - brand consistency for that day's edition, per
owner decision. Re-rendering the same edition date (a replay) selects the same track again, as
long as the enabled library/order hasn't materially changed; if the owner's manifest changes
between a session and a later replay of it, a historical replay may pick a different real track
(expected, not a bug) - this is not separately persisted into a new historical database beyond
the immutable render manifest each render already writes.

A manifest entry that IS eligible (CLEARED + enabled) but whose file is missing or fails an audio
probe is **not** silently swapped for the generator - that would mask a real asset problem. It
falls back to a fully silent render instead (`SILENT_FALLBACK`), with the reason recorded.

### Selection priority (owner policy)

A. One or more CLEARED + enabled real tracks exist → select from those, deterministically.
B. Zero cleared real tracks exist → the approved first-party procedural preset library.
C. Procedural generation itself fails → silent.

A missing/corrupt real asset never falls through to procedural generation - that would mask the
specific asset problem being hidden (it falls back to silence for that render instead, with the
reason recorded); only a *genuinely empty* eligible library routes to procedural (path B).

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
  "origin": "REAL | PROCEDURAL | NONE",
  "track_id": "...", "filename": "...", "title": "...",
  "selection_method": "manifest_rotation | procedural_rotation | procedural | error",
  "reason_code": "OK | NO_CLEARED_REAL_TRACK | TRACK_FILE_MISSING | TRACK_FAILED_PROBE | "
                 "PROCEDURAL_GENERATION_FAILED | MUX_FAILED | AUDIO_PHASE_DISABLED | SELECTION_ERROR",
  "reason": "...",
  "rotation_date": "2026-10-13",
  "volume": 0.22, "fade_in_seconds": 0.75, "fade_out_seconds": 1.25,
  "audio_enabled": true
}
```

`origin` is always derived from `status` (`APPLIED_REAL`→`REAL`, `APPLIED_PROCEDURAL`→`PROCEDURAL`,
`SILENT_FALLBACK`→`NONE`) - the two can never disagree. `reason_code` is the explicit, inspectable
*why* behind `status`; `reason` is the free-text detail. `rotation_date` is always populated
(ISO date), even when audio is disabled, so the audit never has to guess what would have been
selected. Procedural music is never represented as a CLEARED external asset - `track_id` for a
procedural selection is always prefixed `procedural:`, and `selection_method`/`origin` make the
distinction explicit.

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
