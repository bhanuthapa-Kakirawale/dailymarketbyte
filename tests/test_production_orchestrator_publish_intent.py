"""PK-D must never bypass publication rights, however it is invoked.

1. A behavioural proof: --intent publish against a RIGHTS_BLOCK_ONLY artifact (the normal shadow-
   trial shape) is BLOCKED/FAILED by the real PK-C post_render gate, never SUCCESS, and nothing
   is ever uploaded.
2. A static guard (mirrors tests/test_immutability.py's ast-based style): production_orchestrator
   never imports `upload` and never spells the literal string "--upload" anywhere in its source -
   the guarantee must hold even if a future edit tries to add it.
"""
from __future__ import annotations

import ast
import glob
import os

from production_orchestrator import models as M
from production_orchestrator import post as PO
from test_evening_full import FRI, Runner, _manifest, _record  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "production_orchestrator")


def _ready_gate(session, as_of, out_dir, calendar, intent):
    from readiness import ReadinessResult
    return ReadinessResult(edition="POST", as_of=as_of), None


def test_publish_intent_is_blocked_on_a_rights_block_only_artifact(tmp_path):
    import datetime as dt
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    r = Runner(str(tmp_path))      # default _manifest: failed=("publication_rights",) -> RIGHTS_BLOCK_ONLY
    m = PO.run_post(clock=lambda: dt.datetime(2026, 9, 18, 19, 30, tzinfo=IST), runner=r,
                    out_dir=str(tmp_path), readiness_fn=_ready_gate, intent="publish")
    assert m.orchestrator_status in (M.FAILED, M.BLOCKED)
    assert m.orchestrator_status != M.SUCCESS
    assert m.readiness_status_post_render == "BLOCKED"


def test_shadow_intent_on_the_same_artifact_is_not_blocked(tmp_path):
    """Sanity check for the test above: the SAME artifact passes under the default shadow
    intent (RIGHTS_BLOCK_ONLY is expected and accepted pre-review) - the difference is intent,
    not a bug in the artifact."""
    import datetime as dt
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    r = Runner(str(tmp_path))
    m = PO.run_post(clock=lambda: dt.datetime(2026, 9, 18, 19, 30, tzinfo=IST), runner=r,
                    out_dir=str(tmp_path), readiness_fn=_ready_gate, intent="shadow")
    assert m.orchestrator_status == M.SUCCESS


def test_no_module_imports_upload():
    for path in glob.glob(os.path.join(PKG_DIR, "*.py")):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "upload", f"{path} imports upload"
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                assert mod != "upload", f"{path} imports from upload"


def test_no_module_spells_the_upload_flag():
    for path in glob.glob(os.path.join(PKG_DIR, "*.py")):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "--upload" not in src, f"{path} contains the literal string --upload"
