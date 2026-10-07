"""Run context guards - the fail-closed half of isolated test runs (docs/TESTING_GUIDE.md).

A process started with DMB_RUN_CONTEXT=test is a TEST run. `config.py` already refuses to start
one whose output root is not a run folder under output/test_runs/. The guards here are the
second line, called by the writers of durable state:

    guard_write(path, what)   refuse to write / open for writing anything that resolves into
                              the PRODUCTION output tree (output/ minus output/test_runs/)
    guard_upload()            refuse any upload: UPLOAD_DISABLED_TEST_CONTEXT

Outside a test run both are no-ops, so the normal daily flow is unchanged.
"""
from __future__ import annotations

import os

UPLOAD_DISABLED = "UPLOAD_DISABLED_TEST_CONTEXT"


class ProductionPathError(RuntimeError):
    """A test run tried to write into production state."""


class UploadDisabledError(RuntimeError):
    """A test run tried to upload."""


def is_test() -> bool:
    return os.getenv("DMB_RUN_CONTEXT", "").strip().lower() == "test"


def _roots() -> tuple:
    import config
    return (os.path.realpath(config.PRODUCTION_OUT_DIR),
            os.path.realpath(config.TEST_RUNS_DIR))


def is_production_path(path: str) -> bool:
    """True when `path` resolves into output/ but not into output/test_runs/ (symlinks and
    `..` are resolved first)."""
    prod, tests = _roots()
    real = os.path.realpath(os.path.abspath(path))
    inside_prod = real == prod or real.startswith(prod + os.sep)
    inside_tests = real == tests or real.startswith(tests + os.sep)
    return inside_prod and not inside_tests


def guard_write(path: str, what: str = "artifact") -> None:
    if is_test() and path and is_production_path(path):
        raise ProductionPathError(f"TEST CONTEXT: refusing to write {what} into production "
                                  f"state: {path}")


def guard_upload() -> None:
    if is_test():
        raise UploadDisabledError(f"{UPLOAD_DISABLED}: a test run never uploads")


__all__ = ["is_test", "is_production_path", "guard_write", "guard_upload",
           "ProductionPathError", "UploadDisabledError", "UPLOAD_DISABLED"]
