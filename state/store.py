"""StateStore - durable, object-style storage for the state that must survive between the
19:30 REPORT job and the next morning's PRE / POST on ephemeral GitHub runners.

    LocalStateStore(root)             a directory (development, tests, the two-runner simulation)
    GCSStateStore(bucket, prefix)     Google Cloud Storage (production-capable)

Keys are POSIX paths ("reports/premarket_2026-09-28.json"). Every object has a VERSION token
(GCS: the object generation; local: its md5) so writes can be conditional:
    put(key, path, expected=None)     create only - fails if the object exists
    put(key, path, expected=token)    replace only if the object is still at `token`
A failed condition raises StateConflict: nothing is silently overwritten.

No credentials are read, stored or logged here. GCS uses Application Default Credentials (on
GitHub: Workload Identity Federation via google-github-actions/auth); `describe()` never
includes the bucket name or any credential.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil


class StateConflict(Exception):
    """A conditional write found the object changed (or already present)."""


class StateStoreError(Exception):
    """The store could not be used (misconfiguration, unreachable)."""


def md5_b64(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii")


class StateStore:
    backend = "abstract"

    def list(self, prefix: str) -> dict:
        """{key: {"md5": b64, "version": token}} for every object under `prefix`."""
        raise NotImplementedError

    def get(self, key: str, local_path: str) -> None:
        raise NotImplementedError

    def put(self, key: str, local_path: str, expected: str | None = None) -> str:
        """Upload; returns the new version token. Raises StateConflict on a failed condition."""
        raise NotImplementedError

    def describe(self) -> dict:
        return {"backend": self.backend}


class LocalStateStore(StateStore):
    backend = "local"

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def _path(self, key: str) -> str:
        p = os.path.abspath(os.path.join(self.root, *key.split("/")))
        if not p.startswith(self.root + os.sep):
            raise StateStoreError(f"key escapes the store: {key!r}")
        return p

    def list(self, prefix: str) -> dict:
        prefix = prefix.strip("/")
        base = self._path(prefix) if prefix else self.root
        out = {}
        if not os.path.isdir(base):
            return out
        for dirpath, _, files in os.walk(base):
            for f in files:
                full = os.path.join(dirpath, f)
                key = os.path.relpath(full, self.root).replace(os.sep, "/")
                m = md5_b64(full)
                out[key] = {"md5": m, "version": m}
        return out

    def get(self, key: str, local_path: str) -> None:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        shutil.copyfile(self._path(key), local_path)

    def put(self, key: str, local_path: str, expected: str | None = None) -> str:
        target = self._path(key)
        exists = os.path.exists(target)
        if expected is None and exists:
            raise StateConflict(f"{key} already exists")
        if expected is not None and (not exists or md5_b64(target) != expected):
            raise StateConflict(f"{key} changed since it was read")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".uploading"
        shutil.copyfile(local_path, tmp)
        os.replace(tmp, target)
        return md5_b64(target)

    def describe(self) -> dict:
        return {"backend": self.backend, "location": "local directory"}


class GCSStateStore(StateStore):
    """Google Cloud Storage. `client` is injectable (tests use a fake; nothing here needs
    network or credentials until a method is called). Object generations make every write
    conditional (`if_generation_match`: 0 = must not exist)."""
    backend = "gcs"

    def __init__(self, bucket: str, prefix: str = "", client=None):
        if not bucket:
            raise StateStoreError("DMB_GCS_BUCKET is not set")
        self._bucket_name = bucket
        self.prefix = prefix.strip("/")
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:     # pragma: no cover - production dependency
                raise StateStoreError("google-cloud-storage is not installed") from exc
            self._client = storage.Client()
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def _strip(self, name: str) -> str:
        return name[len(self.prefix) + 1:] if self.prefix else name

    def list(self, prefix: str) -> dict:
        out = {}
        for blob in self.client.list_blobs(self._bucket_name, prefix=self._key(prefix)):
            out[self._strip(blob.name)] = {"md5": blob.md5_hash, "version": str(blob.generation)}
        return out

    def get(self, key: str, local_path: str) -> None:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        self.client.bucket(self._bucket_name).blob(self._key(key)).download_to_filename(local_path)

    def put(self, key: str, local_path: str, expected: str | None = None) -> str:
        blob = self.client.bucket(self._bucket_name).blob(self._key(key))
        try:
            blob.upload_from_filename(local_path,
                                      if_generation_match=0 if expected is None else int(expected))
        except Exception as exc:
            if type(exc).__name__ in ("PreconditionFailed", "Conflict"):
                raise StateConflict(f"{key}: {type(exc).__name__}") from None
            raise
        return str(blob.generation)

    def describe(self) -> dict:
        # the bucket name is configuration, not a secret - but it is account-specific, so it
        # stays out of run records and audits
        return {"backend": self.backend, "location": "gcs bucket (configured)",
                "prefix": self.prefix}


def from_env(out_dir: str, env=None) -> StateStore | None:
    """The configured store, or None when the state store IS the output directory (the local
    default: existing local behaviour, nothing to hydrate or persist).

        DMB_STATE_BACKEND=local|gcs   (default local)
        DMB_STATE_DIR=<dir>           local store root (default: none -> no-op)
        DMB_GCS_BUCKET=<bucket>       required for gcs
        DMB_GCS_PREFIX=<prefix>       optional
    """
    env = os.environ if env is None else env
    backend = (env.get("DMB_STATE_BACKEND") or "local").strip().lower()
    if backend == "gcs":
        return GCSStateStore(env.get("DMB_GCS_BUCKET", "").strip(),
                             env.get("DMB_GCS_PREFIX", "").strip())
    if backend != "local":
        raise StateStoreError(f"unknown DMB_STATE_BACKEND {backend!r} (expected local|gcs)")
    root = (env.get("DMB_STATE_DIR") or "").strip()
    if not root or os.path.abspath(root) == os.path.abspath(out_dir):
        return None
    return LocalStateStore(root)


__all__ = ["StateStore", "LocalStateStore", "GCSStateStore", "StateConflict", "StateStoreError",
           "from_env", "md5_b64"]
