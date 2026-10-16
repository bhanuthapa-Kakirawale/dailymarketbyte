"""PRIVATE_ANALYTICS isolation: Trade Candidate Intelligence must never reach PUBLIC video
production, and must never change the Radar candidate set, its order, or the Private Desk
attention set.
"""
import ast
import os

from conftest import SESSION
from test_private_desk import desk_out  # noqa: F401  (reused fixture, house convention)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESK = os.path.join(ROOT, "private_desk")
PRIVATE_CANDIDATES_PKG = os.path.join(ROOT, "private_candidates")


def _public_paths():
    paths = [os.path.join(ROOT, f) for f in ("main.py", "upload.py", "render_daily_market_byte.py",
                                             "render_pre_phase1.py", "render_public_intelligence_v1.py")]
    for pkg in ("products", "publication", "presentation", "daily_video", "operations", "radar",
               "editorial", "hooks", "market_structure", "official_snapshots", "storage"):
        for dirpath, _, files in os.walk(os.path.join(ROOT, pkg)):
            paths += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]
    return [p for p in paths if os.path.isfile(p)]


def test_public_critical_path_never_imports_private_candidates_or_the_desk():
    for p in _public_paths():
        src = open(p, encoding="utf-8").read()
        assert "private_candidates" not in src, p
        assert "private_desk" not in src, p
    for bat in os.listdir(os.path.join(ROOT, "scripts")):
        if bat.endswith(".bat") and "private_desk" not in bat and "private_candidates" not in bat:
            src = open(os.path.join(ROOT, "scripts", bat)).read()
            assert "private_candidates" not in src, bat
            assert "private_desk" not in src, bat


def test_private_candidates_package_has_no_execution_or_model_dependency():
    banned_imports = ("kiteconnect", "upload", "daily_video", "google", "openai", "anthropic",
                      "requests", "socket")
    for f in os.listdir(PRIVATE_CANDIDATES_PKG):
        if not f.endswith(".py"):
            continue
        path = os.path.join(PRIVATE_CANDIDATES_PKG, f)
        src = open(path, encoding="utf-8").read()
        assert "gemini" not in src.lower(), path
        assert "kite" not in src.lower(), path
        for literal in ("--upload", "place_order", "place_gtt_order"):
            assert literal not in src, f"{path}: {literal}"
        for node in ast.walk(ast.parse(src)):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert m.split(".")[0] not in banned_imports, f"{path}: imports {m}"


def _dashboard(desk_out):
    from private_desk.repository import DeskRepository  # noqa: F401 (kept for clarity)
    from private_desk.services import DeskService
    from private_desk.settings import DeskSettings
    svc = DeskService(DeskSettings.from_out_dir(desk_out, cache_dir=desk_out + "_cache"))
    return svc, svc.dashboard(SESSION)


def test_private_candidates_never_changes_radar_order_or_attention_set(desk_out, monkeypatch):
    """Force two very different fake Trade Candidate Intelligence outputs and confirm the
    Radar's own candidate order and the Private Desk attention set stay byte-identical."""
    import private_candidates.service as pc_service

    svc, base = _dashboard(desk_out)
    base_order = [v["symbol"] for v in base["views"]]
    base_attention = [a["view"]["symbol"] for a in base["attention"]]

    def _fake_run_a(out_dir, session, **kw):
        return {"session_date": session.isoformat(), "score_policy_version": "fake-a",
               "capture_mode": "POINT_IN_TIME", "candidate_count": 1, "eligible_count": 1,
               "assessments": [], "raw_ranking": [], "shortlist": [], "demoted": []}

    def _fake_run_b(out_dir, session, **kw):
        raise RuntimeError("Trade Candidate Intelligence deliberately broken for this test")

    for fake in (_fake_run_a, _fake_run_b):
        monkeypatch.setattr(pc_service, "run", fake)
        try:
            svc.trade_candidates(SESSION)          # exercise the new page's own call path
        except RuntimeError:
            pass                                    # the deliberately-broken fake
        svc2, d2 = _dashboard(desk_out)
        assert [v["symbol"] for v in d2["views"]] == base_order
        assert [a["view"]["symbol"] for a in d2["attention"]] == base_attention
        assert d2["radar_order"] == base["radar_order"]

    for f in ("attention.py", "candidates.py", "stock.py", "history.py"):
        src = open(os.path.join(DESK, "services", f), encoding="utf-8").read()
        assert "private_candidates" not in src, f


def test_public_pipeline_never_imports_private_candidates():
    from test_private_desk import test_dmb_critical_path_never_imports_the_desk
    test_dmb_critical_path_never_imports_the_desk()
    test_public_critical_path_never_imports_private_candidates_or_the_desk()
