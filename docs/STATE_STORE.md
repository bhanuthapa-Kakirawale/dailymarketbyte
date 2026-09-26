# Durable state (StateStore)

GitHub Actions runners are ephemeral. Everything the 19:30 REPORT job produces and the next
morning's PRE / POST read must live somewhere that outlives the runner. That is the
**StateStore** (`state/`). `actions/cache` is no longer the canonical store: with the local
backend it survives only as a best-effort fallback, and with GCS it is skipped entirely.

```
runner starts clean -> hydrate (state.sync.hydrate) -> job -> persist (state.sync.persist) -> runner disappears
```

`main.py` does this in-process for every scheduled mode (report / premarket / postmarket), never
for `--demo`. If a configured store cannot be read, the job **does not run**. Persisting a
near-empty runner would overwrite the durable history.

## Backends

| `DMB_STATE_BACKEND` | Store | Use |
|---|---|---|
| `local` (default) | `DMB_STATE_DIR` if set, else none | Development and tests. With no `DMB_STATE_DIR`, `output/` itself is the state (existing behaviour, nothing synced). |
| `gcs` | `GCSStateStore(DMB_GCS_BUCKET, DMB_GCS_PREFIX)` | Production. Application Default Credentials; on GitHub these come from Workload Identity Federation. |

No project ID, bucket name or credential is hard-coded. Run records and audits carry only
`state_store_backend`. The bucket name and credentials never appear in them.

## What is synced (`state.sync.NAMESPACES`)

| Namespace | `output/` dir | Rule |
|---|---|---|
| `reports/` | `reports/*.json` (canonical only, not `unfit/`) | immutable (create-only) |
| `market_structure/` | `market_structure/*.json` | immutable |
| `official_snapshots/` | `official_snapshots/<session>/*.json` | revisions immutable, manifest mutable |
| `databases/` | `data/*.db` (run history, OHLCV, editorial) | mutable |
| `radar/`, `intelligence/` | their `*.json` | mutable |
| `run_history/` | `report_jobs/**.json` | mutable |
| `publication_audits/` | `publication/**.json` | mutable |

MP4s, images, `.env`, `token.json` and `client_secret.json` are never synced.

History rows record the absolute artifact path of the runner that wrote them, and those rows are
never rewritten. A clean runner holds the same immutable file under its own `output/reports/`.
`operations.report_lookup.resolve_report_artifact` resolves it by file name in that directory
only, and the content check (report_id + session) still applies. This was verified by the
two-runner simulation, where runner A's directory no longer exists when runner B runs.

- **Immutable objects are create-only.** A different object under the same key is a
  **conflict**: it is recorded and never overwritten.
- **Mutable objects are replaced only if unchanged since this runner hydrated them.** On GCS this
  uses the object generation (`if_generation_match`). The workflows also share one
  `concurrency` group.

## GCS setup (owner, once; nothing is created automatically)

1. Create a GCS bucket.
   - Uniform bucket-level access, no public access.
   - Object versioning on (an extra safety net for the mutable databases).
   - Optionally a lifecycle rule for old non-current versions.
2. Create a service account with `roles/storage.objectAdmin` on **that bucket only**.
3. Create a Workload Identity Pool and an OIDC provider for GitHub (`https://token.actions.githubusercontent.com`):
   - Attribute condition restricted to this repository, e.g. `assertion.repository == "<owner>/<repo>"`.
   - Grant the pool principal for this repository `roles/iam.workloadIdentityUser` on the service account.
   - **Do not create a JSON key.**
4. Add these GitHub repository **variables** (Settings -> Secrets and variables -> Actions -> Variables):

   | Variable | Value |
   |---|---|
   | `DMB_STATE_BACKEND` | `gcs` |
   | `DMB_GCS_BUCKET` | the bucket name |
   | `DMB_GCS_PREFIX` | optional, e.g. `daily-market-byte/prod` |
   | `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<provider>` |
   | `GCP_STATE_SERVICE_ACCOUNT` | the service account email |

5. Seed the empty bucket once from a machine that holds the current state. With ADC available
   (`gcloud auth application-default login`), run:
   `DMB_STATE_BACKEND=gcs DMB_GCS_BUCKET=... python -m state seed`.
   It refuses a non-empty store. Then `python -m state status` shows object counts.

The workflows (`market_report.yml`, `pre_shadow.yml`, `daily_byte.yml`) request `id-token: write`
and run `google-github-actions/auth@v2` only when `DMB_STATE_BACKEND == 'gcs'`.

## Local two-runner simulation

`DMB_STATE_DIR=<dir>` turns a directory into the store. Point two different `DAILY_BYTE_OUT`
directories at it and you get a clean REPORT runner and a clean POST runner that share nothing
but the store (`tests/test_official_snapshots.py::two_runners` does exactly this).
