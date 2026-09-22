# Production QA

## Three gates, all binding

```
DATA QA        report.publication_ready        (validation: sources, dates, corroboration)
   AND
CONTENT QA     core/content_safety.py          (deterministic recommendation-language filter)
   AND
VIDEO QA       qa/video_qa.py                  (deterministic artifact inspection)
       ↓
    PUBLISH
```

All three must pass before an upload happens. None can override another, and none uses a
model — a publication gate has to give the same answer every time for the same input.

## Where each gate runs

| Stage | Gate | On failure |
| --- | --- | --- |
| After report build, before render | **Data QA** | No video is rendered. Report JSON is written and persisted for diagnosis. |
| After render | **Video QA** | Video, metadata, report and QA artifact are all preserved. No upload. |
| After render | **Content QA** | Same: everything preserved, no upload. |

Failure never deletes anything. Every artifact a failed run produced is left in place,
because a failure you cannot inspect is a failure you cannot fix.

## QA never mutates canonical history

By the time video QA and the final content scan run, the report has been finalized, written
to JSON and persisted. Neither gate may reopen any of that:

- the MarketReport JSON is **not** rewritten to attach a QA verdict,
- the canonical SQLite rows are **not** replaced,
- `report.content_safety` keeps the pre-finalisation sanitisation summary it was built with.

Both verdicts are recorded operationally instead — in `publication_runs` and in the QA
artifact. A QA result describes an execution, not the market, and mixing the two would mean
the canonical record of a session changes depending on whether that day's render happened to
succeed.

### Two content-safety stages

| | When | Where recorded | May change the report? |
| --- | --- | --- | --- |
| **Stage A** — sanitisation | Before the report is finalized | `report.content_safety`, `stage: PRE_REPORT_SANITISATION` | Yes — the report does not exist yet |
| **Stage B** — final publication scan | After render, before upload | `publication_runs.content_qa_status`, QA artifact `final_content_qa` | **No** |

Stage A is unchanged from Phase 1.1 and remains binding: `"Strong BUY with target Rs 500"`
still never becomes canonical report content. Stage B is unchanged as a gate — it still blocks
the upload — it simply no longer writes back into the report.

## Video QA checks

Measured with the ffmpeg binary the video stack already depends on — no new media dependency.
`probe_media` and `sample_frame_stats` are the single mockable seam, so tests never invoke
ffmpeg while the production implementation ships unchanged.

| Check | Blocks when |
| --- | --- |
| `file_exists` | the render produced no file |
| `file_size` | empty, or below ~200 KB (a 75s 1080x1920 Short is megabytes) |
| `container_readable` | the container cannot be inspected at all |
| `video_stream` | no video stream |
| `resolution` | not 1080x1920 |
| `frame_rate` | outside 24–61 fps |
| `duration` | more than 1.0s from the rendered scene total |
| `audio_stream` | no audio stream |
| `metadata_json` | the YouTube metadata companion is missing |
| `report_json` | the MarketReport artifact is missing |
| `frame_decode` | any sampled frame fails to decode |
| `blank_frames` | a sampled frame is effectively one flat colour |

### Blank-frame policy

Deliberately blunt, and deliberately conservative. Five frames are sampled (2%, 25%, 50%,
75%, 97%) and reduced to mean intensity and standard deviation.

- **Flat frame** (stddev < 1.0) → **BLOCK**. The renderer produced nothing there.
- **Decode failure** → **BLOCK**.
- **Dark but textured** (mean < 6.0, stddev normal) → **WARNING only**.
- **Sampling itself fails** → **WARNING only**, never a block.

The design is legitimately dark — deep navy backgrounds, dark cards — so "this frame is dark"
must never block. Measured against a real render, frames sit at mean 30–49 and stddev 30–51,
which is a very wide margin above both thresholds. Anything subtler than this would need real
computer vision and would produce false positives, and a gate that cries wolf gets switched
off. Warnings do not block publication.

## QA artifact

Written for every rendered artifact at `output/qa/qa_YYYY-MM-DD.json` (`_DEMO` suffixed for
demo runs):

```json
{
  "report_id": "20260922_PRE_MARKET",
  "report_json_artifact": "output/reports/premarket_2026-09-22.json",
  "video_path": "output/daily_byte_2026-09-22.mp4",
  "checked_at": "...",
  "overall_status": "PASS",
  "passed": true,
  "checks": [{"name": "...", "status": "...", "expected": "...", "actual": "...", "message": "..."}],
  "blocking_issues": [],
  "warnings": [],
  "content_safety": {"stage": "PRE_REPORT_SANITISATION", ...},
  "final_content_qa": {"stage": "FINAL_PUBLICATION_SCAN", "status": "SAFE", "passed": true,
                       "blocked_fields": []},
  "data_validation": {...}
}
```

It **points at** the report rather than copying it. Duplicating the MarketReport here would
create a second copy that could drift from the immutable original.

`content_safety` is the report's own Stage A summary, included for context.
`final_content_qa` is Stage B — the operational verdict that never enters the report.

## Publication run history

Every execution writes a row to `publication_runs`, which is what makes
"why wasn't the 22 Sep report published?" answerable months later.

Stages: `COLLECTED` → `REPORT_BUILT` → `PERSISTED` → `DATA_QA_PASSED` / `DATA_QA_FAILED` →
`RENDERED` → `VIDEO_QA_PASSED` / `VIDEO_QA_FAILED` → `PUBLISHED` / `NO_UPLOAD` / `FAILED`.

Each row records `mode` (`PRODUCTION` / `LOCAL` / `DEMO`), the three QA statuses,
`publication_status`, `artifact_path`, `youtube_video_id` when one exists, and
`failure_stage` + `failure_reason` when something blocked.

A run that never attempted an upload is still recorded — not uploading is an outcome worth
keeping, not an absence of one.

## Persistence is itself a gate

If the report cannot be written to the historical index, **the run does not publish**.

Auditability is part of publication integrity: publishing a video whose provenance was never
recorded means publishing something nobody can later account for. The JSON artifact is
written first and preserved regardless, and the failure is logged and recorded.
