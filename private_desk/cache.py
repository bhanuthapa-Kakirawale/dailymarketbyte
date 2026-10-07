"""The desk's only write path: a disposable JSON cache under `<OUT_DIR>/private_desk/cache/`.

Everything here is regeneratable from the read-only stores (deleting the folder only costs a
recompute). A write is refused unless the target is inside the configured cache directory, and
the cache directory itself must be named `private_desk/cache` or live outside the DMB output
root (tests point it at tmp) - so no bug can turn the cache into a write into production state.
A failing cache write is swallowed: the page still renders from the freshly computed value.
"""
from __future__ import annotations

import hashlib
import json
import os

from .settings import DESK_DIRNAME


class CacheWriteRefused(RuntimeError):
    pass


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:            # different drives (Windows)
        return False


def check_cache_dir(cache_dir: str, out_dir: str) -> None:
    """Refuse a cache directory that sits in the DMB output root anywhere other than
    `<OUT_DIR>/private_desk/`."""
    if _inside(cache_dir, out_dir) and not _inside(cache_dir, os.path.join(out_dir, DESK_DIRNAME)):
        raise CacheWriteRefused(
            f"desk cache must live under {DESK_DIRNAME}/ in the output root, not {cache_dir}")


def fingerprint(paths) -> str:
    """Cheap change detector for cache keys: (name, size, mtime_ns) of each input file."""
    h = hashlib.sha256()
    for p in paths:
        try:
            st = os.stat(p)
            h.update(f"{os.path.basename(p)}:{st.st_size}:{st.st_mtime_ns};".encode())
        except OSError:
            h.update(f"{os.path.basename(p)}:missing;".encode())
    return h.hexdigest()[:16]


class DeskCache:
    def __init__(self, cache_dir: str, out_dir: str):
        check_cache_dir(cache_dir, out_dir)
        self.cache_dir = cache_dir

    def _path(self, name: str) -> str:
        path = os.path.join(self.cache_dir, name)
        if not _inside(path, self.cache_dir):
            raise CacheWriteRefused(f"cache path escapes the cache directory: {name}")
        return path

    def get(self, name: str, key: str):
        try:
            with open(self._path(name), encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        return payload.get("value") if payload.get("key") == key else None

    def put(self, name: str, key: str, value) -> None:
        try:
            path = self._path(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"key": key, "value": value}, fh, default=str)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            pass


__all__ = ["DeskCache", "CacheWriteRefused", "check_cache_dir", "fingerprint"]
