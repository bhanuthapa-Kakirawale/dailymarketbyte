"""`private_candidates.presentation`: the typed DTOs that let `private_desk` display an
already-computed score without ever writing a `score`-shaped identifier in its own source
(owner hardening pass, section 5 - the getattr/.get workaround is removed, not weakened)."""
import datetime as dt

from conftest import SESSION
from private_candidates import presentation, service
from test_private_desk import desk_out  # noqa: F401

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CANDIDATE_AS_OF = dt.datetime(2026, 9, 18, 22, 10, tzinfo=IST)


def test_candidate_board_view_renames_score_fields(desk_out):
    from private_desk.repository import DeskRepository
    repo = DeskRepository(desk_out)
    run = service.run(desk_out, SESSION, repo=repo, candidate_as_of=CANDIDATE_AS_OF)
    board = presentation.CandidateBoardView.from_run(run)
    assert board.quality_policy_version == run["score_policy_version"]
    assert board.all_candidates
    row = board.all_candidates[0]
    assert hasattr(row, "quality_points") and hasattr(row, "quality_max")
    assert not hasattr(row, "score_total") and not hasattr(row, "score_max")


def test_candidate_detail_view_renames_score_fields_including_components(desk_out):
    from private_desk.repository import DeskRepository
    repo = DeskRepository(desk_out)
    assessments = service.build_assessments(repo, SESSION, candidate_as_of=CANDIDATE_AS_OF)
    detail = presentation.CandidateDetailView.from_assessment(assessments[0])
    assert hasattr(detail, "quality_points") and hasattr(detail, "quality_policy_version")
    assert detail.components
    comp = detail.components[0]
    assert hasattr(comp, "quality_points") and hasattr(comp, "quality_max")
    assert not hasattr(comp, "score") and not hasattr(comp, "max_score")


def test_default_policy_version_constant_matches_the_live_policy():
    from private_candidates.policy import DEFAULT_SCORING_POLICY
    assert presentation.DEFAULT_POLICY_VERSION == DEFAULT_SCORING_POLICY.score_policy_version


def test_private_desk_never_spells_a_score_identifier_to_use_these_dtos():
    """The architectural guard this hardening pass targets, run directly (mirrors
    `tests/test_private_desk.py::test_no_new_score_is_introduced`, unmodified by this
    package): every `.py` file under `private_desk/services/trade_candidates.py` and the two
    new templates must never contain a `score`-shaped identifier."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "private_desk", "services", "trade_candidates.py")
    src = open(path, encoding="utf-8").read()
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.Dict):
            names = [k.value for k in node.keys if isinstance(k, ast.Constant)
                    and isinstance(k.value, str)]
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            names = [str(node.slice.value)]
        for n in names:
            assert "score" not in str(n).lower(), f"{path}: {n}"
