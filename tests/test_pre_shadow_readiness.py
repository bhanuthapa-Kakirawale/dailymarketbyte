"""PRE shadow readiness: one canonical validation gate, RADAR_SELECTED vs RADAR_PUBLISHED, PRE
stock watch from published stories only, the stray-run cleanup, the Gemini smoke helper, the
GIFT gate and the frozen renderers. Fully offline."""
from __future__ import annotations

import ast
import datetime as dt
import glob
import hashlib
import inspect
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

import main
from conftest import SESSION
from storage import MarketHistory
from storage.editorial_repository import EditorialStore, default_db_path as ed_path
from test_editorial_selector import make_pair
from test_pipeline import _Args, _failing_video_qa, offline_pipeline  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D0 = dt.date(2026, 9, 21)
D1 = dt.date(2026, 9, 22)
D2 = dt.date(2026, 9, 23)


def _db(tmp_path):
    return str(tmp_path / "data" / "market_history.db")


def _reports(tmp_path):
    with MarketHistory(_db(tmp_path)) as h:
        return h.get_reports_between(dt.date(2026, 1, 1), dt.date(2026, 12, 31))


def _unfit(monkeypatch, state):
    from core import MarketReport
    real = MarketReport.publication_ready
    monkeypatch.setattr(MarketReport, "publication_ready",
                        property(lambda self: state["fit"] and real.fget(self)))


def _select(tmp_path, session, symbols):
    import radar.editorial_selector as es
    with EditorialStore(ed_path(str(tmp_path))) as store:
        return es.run_and_persist(session, [make_pair(s, session) for s in symbols], store,
                                  spine=[D0, D1, D2])


def _confirm(tmp_path, session, rendered, qa_passed=True, **kw):
    from products.radar_publication import confirm_radar_publication
    return confirm_radar_publication(session, rendered, qa_passed=qa_passed,
                                     out_dir=str(tmp_path), artifact_path="post.mp4",
                                     qa={"video_qa": "PASS"}, **kw)


def _published(tmp_path, session):
    with EditorialStore(ed_path(str(tmp_path))) as store:
        return [p["instrument"] for p in store.get_session_publications(session)]


# --------------------------------------------------------------- 1-2. one canonical gate
def test_invalid_report_never_becomes_canonical_via_post(offline_pipeline, tmp_path, monkeypatch):
    _unfit(monkeypatch, {"fit": False})
    assert main.run(_Args()) is None
    assert _reports(tmp_path) == [], "POST's inline fallback must not commit an unfit report"
    unfit = os.listdir(tmp_path / "reports" / "unfit")
    assert unfit, "the unfit report is kept for diagnosis"
    with MarketHistory(_db(tmp_path)) as h:
        run = h.get_publication_runs(job_type="POST_MARKET")[0]
    assert run.run_status == "BLOCKED" and run.failure_stage == "DATA_QA"
    assert "unfit" in run.artifact_path
    assert "scenes" not in offline_pipeline


def test_persist_report_refuses_an_unfit_report_directly(market_dict, tmp_path):
    from test_publication_gate import build_test_report
    bad = build_test_report(market_dict, nse_idx={"NIFTY 50": {"last": 25900.0, "pct": 3.4,
                                                              "observed_at": None}})
    assert not bad.publication_ready
    with MarketHistory(_db(tmp_path)) as h:
        ok, err = main.persist_report(bad, "x.json", history=h)
        assert not ok and "may not become canonical" in err
        assert h.get_report(bad.report_id) is None
        # demo stays exempt (is_demo=1, excluded from every query)
        assert main.may_become_canonical(bad, demo=True)


def test_retry_after_an_unfit_attempt_commits_the_valid_report(offline_pipeline, tmp_path,
                                                               monkeypatch):
    state = {"fit": False}
    _unfit(monkeypatch, state)
    assert main.run(_Args()) is None and _reports(tmp_path) == []
    state["fit"] = True
    assert main.run(_Args())
    reports = _reports(tmp_path)
    assert len(reports) == 1 and reports[0].publication_ready
    assert os.path.exists(reports[0].json_artifact_path)


def test_produce_report_has_no_commit_unfit_switch():
    assert "commit_unfit" not in inspect.signature(main.produce_report).parameters
    assert "commit_unfit" not in inspect.getsource(main)


# --------------------------------------------------------------- 3. selection != publication
def test_selection_is_not_publication_and_starts_no_cooldown(tmp_path):
    r0 = _select(tmp_path, D0, ["AAA", "BBB"])
    assert sorted(s.instrument for s in r0.selected) == ["AAA", "BBB"]
    with EditorialStore(ed_path(str(tmp_path))) as store:
        assert store.get_prior_publications(D1, [D0, D1], 3) == {}
        assert {s.lifecycle_state for s in store.get_session_selections(D0)} == {"SELECTED"}
        assert store.get_prior_selections(D1, [D0, D1], 3)       # selection history kept
    # never shown -> the next session may select them again
    r1 = _select(tmp_path, D1, ["AAA", "BBB"])
    assert r1.suppressed_by_cooldown == 0
    assert sorted(s.instrument for s in r1.selected) == ["AAA", "BBB"]


def test_report_job_records_selection_but_publishes_nothing(offline_pipeline, tmp_path):
    from conftest import IST
    from products.report_job import run_report_job

    def radar_fn(session, out_dir, as_of):
        os.makedirs(os.path.join(out_dir, "radar"), exist_ok=True)
        with open(os.path.join(out_dir, "radar", f"daily_radar_{session}.json"), "w") as fh:
            json.dump({"pipeline_status": "OK", "stories": [{"instrument": s} for s in
                                                            ("AAA", "BBB", "CCC")]}, fh)
        return {"status": "BUILT", "pipeline_status": "OK", "stories": 3}

    rec = run_report_job(None, now=dt.datetime(2026, 9, 18, 19, 30, tzinfo=IST),
                         radar_fn=radar_fn)
    sel = rec["details"]["radar"]["selection"]
    assert sel["selected_symbols"] == ["AAA", "BBB", "CCC"] and sel["published_count"] == 0
    assert not os.path.exists(ed_path(str(tmp_path))) or _published(tmp_path, SESSION) == []


def test_report_job_and_radar_never_confirm_publication():
    for rel in ("products/report_job.py", "radar/daily_pipeline.py",
                "radar/editorial_selector.py"):
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert "confirm_radar_publication" not in src and "record_publication" not in src, rel


# --------------------------------------------------------------- 4. failed POST
def test_failed_post_does_not_mark_radar_published(tmp_path):
    _select(tmp_path, D0, ["AAA", "BBB", "CCC"])
    res = _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"], qa_passed=False)
    assert res["status"] == "NOT_CONFIRMED" and res["published"] == []
    assert _published(tmp_path, D0) == []
    with EditorialStore(ed_path(str(tmp_path))) as store:
        assert {s.lifecycle_state for s in store.get_session_selections(D0)} == {"SELECTED"}


def test_demo_never_publishes(tmp_path):
    res = _confirm(tmp_path, D0, ["AAA"], demo=True)
    assert res["status"] == "DEMO_NOT_RECORDED" and not os.path.exists(ed_path(str(tmp_path)))


def test_legacy_post_run_records_selected_vs_published(offline_pipeline, tmp_path, monkeypatch):
    """The production POST renderer has no Radar section: whatever was selected, nothing is
    published, and the run says so - on a QA failure and on success alike."""
    os.makedirs(tmp_path / "radar", exist_ok=True)
    (tmp_path / "radar" / f"daily_radar_{SESSION}.json").write_text(json.dumps(
        {"stories": [{"instrument": s} for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]}))
    monkeypatch.setattr(main, "check_video", _failing_video_qa)
    main.run(_Args())
    with MarketHistory(_db(tmp_path)) as h:
        run = h.get_publication_runs(job_type="POST_MARKET")[0]
    radar = run.details["radar"]
    assert radar["selected_count"] == 5 and radar["published_count"] == 0
    assert radar["published_symbols"] == [] and radar["qa_passed"] is False
    assert radar["confirmation_status"] == "NOT_CONFIRMED"
    assert run.details.get("report_source"), "earlier details are preserved"


# --------------------------------------------------------------- 5. only rendered stories
def _storyboard(symbols):
    scenes = [SimpleNamespace(kind="PULSE", data={}, texts={})]
    scenes += [SimpleNamespace(kind="RADAR_STORY", data={"symbol": s}, texts={"symbol": s})
               for s in symbols]
    return SimpleNamespace(scenes=scenes + [SimpleNamespace(kind="CLOSING", data={}, texts={})])


def test_successful_post_marks_only_rendered_stories_published(tmp_path):
    from products.radar_publication import radar_publication, rendered_radar_stories
    _select(tmp_path, D0, ["AAA", "BBB", "CCC", "DDD", "EEE"])
    rendered = rendered_radar_stories(_storyboard(["CCC", "AAA", "BBB"]))
    assert rendered == ["CCC", "AAA", "BBB"]
    res = _confirm(tmp_path, D0, rendered, run_id="run_x")
    assert res["status"] == "CONFIRMED" and res["published"] == ["CCC", "AAA", "BBB"]
    assert _published(tmp_path, D0) == ["CCC", "AAA", "BBB"]           # on-screen order
    with EditorialStore(ed_path(str(tmp_path))) as store:
        states = {s.instrument: s.lifecycle_state for s in store.get_session_selections(D0)}
    assert states == {"AAA": "PUBLISHED", "BBB": "PUBLISHED", "CCC": "PUBLISHED",
                      "DDD": "SELECTED", "EEE": "SELECTED"}
    pub = radar_publication(D0, str(tmp_path))
    assert pub["published_count"] == 3 and pub["post_run_id"] == "run_x"
    assert pub["artifact_path"] == "post.mp4" and pub["confirmed_at"]


def test_publication_starts_cooldown_only_for_published_stories(tmp_path):
    _select(tmp_path, D0, ["AAA", "BBB", "CCC", "DDD", "EEE"])
    _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"])
    r1 = _select(tmp_path, D1, ["AAA", "DDD"])
    assert [s.instrument for s in r1.selected] == ["DDD"]        # AAA was shown -> cooldown
    assert r1.suppressed_by_cooldown == 1


# --------------------------------------------------------------- 6. idempotent reruns
def test_rerun_does_not_duplicate_publication_history(tmp_path):
    _select(tmp_path, D0, ["AAA", "BBB", "CCC"])
    first = _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"],
                     confirmed_at=dt.datetime(2026, 9, 22, 2, 30, tzinfo=dt.timezone.utc))
    again = _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"])
    assert again["status"] == "ALREADY_CONFIRMED" and again["matches_prior"] is True
    assert again["confirmed_at"] == first["confirmed_at"]
    different = _confirm(tmp_path, D0, ["AAA", "DDD"])
    assert different["status"] == "ALREADY_CONFIRMED" and different["matches_prior"] is False
    assert _published(tmp_path, D0) == ["AAA", "BBB", "CCC"]
    with sqlite3.connect(ed_path(str(tmp_path))) as c:
        assert c.execute("SELECT count(*) FROM radar_publications").fetchone()[0] == 3
    with EditorialStore(ed_path(str(tmp_path))) as store:
        assert store.get_prior_publications(D1, [D0, D1], 3) == {
            "AAA": D0, "BBB": D0, "CCC": D0}


def test_failed_then_successful_rerun_publishes_once(tmp_path):
    _select(tmp_path, D0, ["AAA", "BBB", "CCC"])
    assert _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"], qa_passed=False)["status"] == \
        "NOT_CONFIRMED"
    assert _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"])["status"] == "CONFIRMED"
    assert _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"])["status"] == "ALREADY_CONFIRMED"
    assert _published(tmp_path, D0) == ["AAA", "BBB", "CCC"]


def test_v1_editorial_database_upgrades_without_backfilling_publication(tmp_path):
    from storage.editorial_schema import SCHEMA_SQL
    path = ed_path(str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    v1_sql = SCHEMA_SQL.split("-- v2:")[0]
    with sqlite3.connect(path) as c:
        c.executescript(v1_sql)
        c.execute("PRAGMA user_version = 1")
        c.execute("INSERT INTO editorial_selections (selection_id, session_date, instrument, "
                  "novelty_type, selection_bucket, selector_version, selected_at) VALUES "
                  "('s1','2026-09-21','AAA','NEW_CANDIDATE','B','1.0','2026-09-21T19:30')")
    with EditorialStore(path) as store:
        assert store.schema_version == 2
        assert [s.instrument for s in store.get_session_selections(D0)] == ["AAA"]
        assert store.get_session_publications(D0) == []


# --------------------------------------------------------------- 7. PRE stock watch
@pytest.fixture
def radar_artifacts(tmp_path, monkeypatch):
    """Synthetic Radar presentation + result artifacts for D0 selecting five stories."""
    import presentation.radar_guard as rg
    import radar.visual_evidence as ve
    d = tmp_path / "radar_src"
    (d / "presentation").mkdir(parents=True)
    sel = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    (d / "presentation" / f"radar_presentation_{D0}.json").write_text(json.dumps(
        {"status": "OK", "scenes": [{"role": "STORY", "story": {"instrument": s}} for s in sel]}))
    (d / f"daily_radar_{D0}.json").write_text(json.dumps(
        {"stories": [{"instrument": s} for s in sel]}))
    monkeypatch.setattr(rg, "guard_radar_stories", lambda stories, *a, **k: (list(stories), []))
    monkeypatch.setattr(ve, "build_visual_evidence_for_presentation", lambda rp, rr: {})
    return [str(d)]


def _watch(dirs):
    from products.premarket import published_radar_stories
    stories, _ev, _audit, src = published_radar_stories(D0, None, dirs=dirs)
    return [sp["instrument"] for sp, _ in stories], src


def test_pre_stock_watch_ignores_selected_but_unpublished(tmp_path, radar_artifacts):
    _select(tmp_path, D0, ["AAA", "BBB", "CCC", "DDD", "EEE"])
    _confirm(tmp_path, D0, ["AAA", "BBB", "CCC"], qa_passed=False)       # POST failed
    watch, src = _watch(radar_artifacts)
    assert watch == [] and src["radar_publication"].startswith("NONE_CONFIRMED")


def test_pre_stock_watch_without_any_editorial_database(tmp_path, radar_artifacts):
    watch, _src = _watch(radar_artifacts)
    assert watch == [] and not os.path.exists(ed_path(str(tmp_path)))


def test_pre_stock_watch_uses_only_published_stories_in_on_screen_order(tmp_path,
                                                                         radar_artifacts):
    _select(tmp_path, D0, ["AAA", "BBB", "CCC", "DDD", "EEE"])
    _confirm(tmp_path, D0, ["CCC", "AAA", "EEE"])                       # 5 selected, 3 shown
    watch, src = _watch(radar_artifacts)
    assert watch == ["CCC", "AAA", "EEE"]
    assert "CONFIRMED: CCC, AAA, EEE" in src["radar_publication"]
    assert _watch(radar_artifacts)[0] == watch                            # deterministic


def test_pre_plan_caps_the_published_watch_at_two():
    import dataclasses
    from presentation.pre_plan import plan_pre_sections
    from products.pre_fixtures import synthetic_brief
    base = synthetic_brief("RISK_OFF")
    many = [dict(base.stock_facts[0], symbol=s) for s in ("CCC", "AAA", "EEE")]
    plan = plan_pre_sections(dataclasses.replace(base, stock_facts=many))
    assert [i["symbol"] for i in plan.stock_watch.items] == ["CCC", "AAA"]


# --------------------------------------------------------------- 8. stray cleanup
def _seed_runs(tmp_path, n_stray=2):
    from operations.stray_cleanup import KNOWN_STRAY
    db = _db(tmp_path)
    h = MarketHistory(db)
    stray = []
    for i in range(n_stray):
        rid = f"run_20260925T17{20 + i}00_stray{i}"
        h.start_run("REPORT_BUILD", run_id=rid, job_type="REPORT_BUILD",
                    target_date=dt.date(2026, 9, 18))
        h.finish_run(rid, "FAILED", "BLOCKED", failure_stage="SESSION_RESOLUTION",
                     failure_reason="HISTORICAL_REBUILD_UNSUPPORTED: requested 2026-09-18",
                     run_status="BLOCKED")
        h.conn.execute("UPDATE publication_runs SET started_at = ? WHERE run_id = ?",
                       (f"2026-09-25T17:2{i}:00+00:00", rid))
        h.conn.commit()
        folder = tmp_path / "report_jobs" / KNOWN_STRAY["target_date"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"report_job_{rid}.json").write_text(json.dumps(
            {"run_id": rid, "run_status": "BLOCKED",
             "failure_reason": "HISTORICAL_REBUILD_UNSUPPORTED: requested 2026-09-18"}))
        stray.append(rid)
    # decoys: a legitimate POST run, a legitimate BLOCKED report build on another session,
    # and a same-reason row outside the time window
    h.start_run("LOCAL", run_id="run_legit_post")
    h.finish_run("run_legit_post", "NO_UPLOAD", "NOT_ATTEMPTED", run_status="SUCCESS")
    h.start_run("REPORT_BUILD", run_id="run_legit_report", job_type="REPORT_BUILD",
                target_date=dt.date(2026, 9, 24))
    h.finish_run("run_legit_report", "DATA_QA_FAILED", "BLOCKED", failure_stage="DATA_QA",
                 failure_reason="NIFTY conflict", run_status="BLOCKED")
    h.start_run("REPORT_BUILD", run_id="run_other_day", job_type="REPORT_BUILD",
                target_date=dt.date(2026, 9, 18))
    h.finish_run("run_other_day", "FAILED", "BLOCKED", failure_stage="SESSION_RESOLUTION",
                 failure_reason="HISTORICAL_REBUILD_UNSUPPORTED: x", run_status="BLOCKED")
    h.conn.execute("UPDATE publication_runs SET started_at = '2026-09-26T03:00:00+00:00' "
                   "WHERE run_id = 'run_other_day'")
    h.conn.commit()
    h.close()
    return db, stray


def test_stray_cleanup_removes_only_the_two_known_records(tmp_path):
    from operations.stray_cleanup import cleanup
    db, stray = _seed_runs(tmp_path)
    audit_dir = str(tmp_path / "audit")
    dry = cleanup(db, str(tmp_path), audit_dir)
    assert dry["outcome"] == "DRY_RUN" and dry["matched_run_ids"] == stray
    with MarketHistory(db) as h:
        assert len(h.get_publication_runs(limit=100)) == 5          # dry run changed nothing
    done = cleanup(db, str(tmp_path), audit_dir, apply=True)
    assert done["outcome"] == "REMOVED" and done["rows_removed"] == 2
    with MarketHistory(db) as h:
        left = sorted(r.run_id for r in h.get_publication_runs(limit=100))
    assert left == ["run_legit_post", "run_legit_report", "run_other_day"]
    assert not (tmp_path / "report_jobs" / "2026-09-18").exists()
    backup = done["backup"]
    assert len(json.load(open(backup["rows_export"]))) == 2
    assert os.path.exists(backup["database_snapshot"]) and len(backup["job_records"]) == 2
    assert os.path.exists(os.path.join(audit_dir, "stray_cleanup_audit.json"))


def test_stray_cleanup_refuses_when_the_match_is_not_exactly_two(tmp_path):
    from operations.stray_cleanup import cleanup
    db, _ = _seed_runs(tmp_path, n_stray=3)
    res = cleanup(db, str(tmp_path), str(tmp_path / "audit"), apply=True)
    assert res["outcome"] == "REFUSED"
    with MarketHistory(db) as h:
        assert len(h.get_publication_runs(limit=100)) == 6


def test_pipeline_never_deletes_run_history():
    for rel in ["main.py"] + sorted(glob.glob(os.path.join(ROOT, "products", "*.py"))):
        path = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        tree = ast.parse(open(path, encoding="utf-8").read())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                 and n.attr == "remove_contaminated_runs"]
        assert not calls, f"{rel} must never delete run history"


# --------------------------------------------------------------- 9. Gemini smoke secrecy
class _Resp:
    def __init__(self, status, payload, text, headers):
        self.status_code, self._payload, self.text, self.headers = status, payload, text, headers

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_gemini_smoke_never_exposes_the_key(monkeypatch, capsys):
    from operations import gemini_smoke
    key = "AIzaTEST-SECRET-KEY-000"
    monkeypatch.setenv("GEMINI_API_KEY", key)
    seen = {}

    def post_429(url, json=None, timeout=None, headers=None):
        seen.update(url=url, headers=headers, body=json)
        return _Resp(429, {"error": {"status": "RESOURCE_EXHAUSTED",
                                     "message": f"quota exceeded for key {key}"}},
                     f"quota exceeded for key {key}", {"Retry-After": "30", "X-Key": key})

    res = gemini_smoke.run_smoke_test(post=post_429)
    assert res["request_result"] == "QUOTA_OR_RATE_LIMITED" and res["http_status"] == 429
    assert res["api_status"] == "RESOURCE_EXHAUSTED" and res["next_diagnostic"]
    assert res["production_fallback_needed"] and res["retries"] == 0
    assert key not in json.dumps(res) and key not in capsys.readouterr().out
    assert key not in seen["url"] and seen["headers"]["x-goog-api-key"] == key
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "tools" not in seen["body"]                          # un-grounded, like the hook

    def post_200(url, json=None, timeout=None, headers=None):
        reply = f"not json, echo {key}"
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": reply}]},
                                           "finishReason": "STOP"}]}, reply, {})
    res = gemini_smoke.run_smoke_test(post=post_200)
    assert res["request_result"] == "OK" and res["structured_json_parsed"] is False
    assert key not in json.dumps(res)


def test_gemini_smoke_validates_a_contract_reply(monkeypatch):
    from hooks.engine import plan_hook
    from operations import gemini_smoke
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    sheet, cands, *_ = gemini_smoke.hook_request()
    top = cands[0]
    reply = json.dumps({"candidate_id": top.candidate_id, "archetype": top.archetype.value,
                        "hero_visual": top.default_hero.value,
                        "teaser_beats": list(top.default_beats),
                        "curiosity_line": top.curiosity_line, "summary_line": top.summary_line,
                        "fact_ids_used": []})
    res = gemini_smoke.run_smoke_test(post=lambda *a, **k: _Resp(
        200, {"candidates": [{"content": {"parts": [{"text": reply}]}}]}, reply, {}))
    expected = plan_hook(sheet, client=lambda p, s: reply).source.value
    assert res["contract_validation_passed"] is (expected == "GEMINI")


def test_gemini_smoke_without_a_key(monkeypatch):
    from operations import gemini_smoke
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = gemini_smoke.run_smoke_test(post=lambda *a, **k: pytest.fail("no request without key"))
    assert res["request_result"] == "NO_KEY"


# --------------------------------------------------------------- 10. GIFT gate
def test_gift_publication_flag_remains_off():
    import config  # noqa: F401  (loads .env exactly as production does)
    from operations.gift_policy import ENV_FLAG, gift_publication_policy
    assert gift_publication_policy({}).publication_allowed is False
    assert gift_publication_policy().publication_allowed is False, \
        "GIFT publication must stay OFF until live validation, reachability and rights review"
    for wf in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")):
        text = open(wf, encoding="utf-8").read()
        assert f"{ENV_FLAG}: true" not in text and f"{ENV_FLAG}=true" not in text, wf


# --------------------------------------------------------------- 11. frozen renderers
FROZEN = {
    "daily_video/__init__.py": "e078258212cd57f5881d36aa2530badeee0690d560a360da55c5dfd7b8b96434",
    "daily_video/animations.py": "0a6632848736beb0884772e8bc89610167413716968673907ba14dcc92f14f97",
    "daily_video/annotations.py": "fa13f1470f655792786c9704343f80e6f8649df030cb87d55d1dcc57d43f2ec7",
    "daily_video/chartkit.py": "6b878b0d5152c3d0e2d5c148b758841984f1482dd974951b782023a910ec0706",
    "daily_video/chrome.py": "d702e1707531f90e1ce5db5204ac8eb9951692ef476d19818532fb246ee02ffe",
    "daily_video/composer.py": "e9c0dcc21a09e70039d175480661cc2b0f41a91796bc3199e39f1e77b8c915ca",
    "daily_video/hook_kit.py": "65054d18a31ce549a9da2ea5adbdcadda7fc9e1366283fcff0d71c462e688cee",
    "daily_video/hook_scene.py": "f6af246492bc2d0d5b92f24b18ed84cccd7fdbfddd99f26930ec865d88759d2f",
    "daily_video/market_scenes.py": "e2b981425c12d98def8ed3da62d58a405f54eaab411c168bbd4b8f669820e12d",
    "daily_video/pre_scenes.py": "87ad294e425015ea8bf3e1db150dc69d3de06865010af150d0487117077d70c3",
    "daily_video/pre_storyboard.py": "c436c1381ca609bfc19b0dac5005de9b176d69ad4a9588ff0e9f918d4d07af2d",
    "daily_video/radar_scenes.py": "ecda846e8012b64794641568023fa3582d256b2456c6ff805171cb76738b9647",
    "daily_video/radar_story_scene.py": "e544e5b3d224e6fcd194b9c5602a26749bda5f6c7a1a0228cb9e2ba38b53c64a",
    "daily_video/scenes.py": "4546a7832d833040e87adcc901c4b83e6d0f716972c43e1f3501f7e0cf862195",
    "daily_video/storyboard.py": "60ab4670f19dd99a3879dd472b2e945d4713b6b1dd9d291fa150b46aaa06563a",
    "daily_video/theme.py": "8e1af617da9052c104ede87ddd393de5744355d657b513accfee4414f8b726db",
    "daily_video/typography.py": "d6b5620d928e4ea9eef40647713b37f639f5ce087a47bd112bf0af2c37f152ae",
    "video.py": "22fa879520ddc2c478bd320b0b2a01db83a3229b66d4020b5b9b83a52828ba92",
    "chart.py": "ca402c4a721039861bf86dd0ab6b25cef997024732a3de2edee5c74c26c2f2fa",
}


@pytest.mark.parametrize("rel", sorted(FROZEN))
def test_frozen_renderer_unchanged(rel):
    data = open(os.path.join(ROOT, rel), "rb").read().replace(b"\r\n", b"\n")
    assert hashlib.sha256(data).hexdigest() == FROZEN[rel], \
        f"{rel} is a frozen PRE/POST renderer - a change needs owner approval + a new pin"


def test_no_unpinned_renderer_module():
    present = {p.replace(os.sep, "/").split(ROOT.replace(os.sep, "/") + "/")[-1]
               for p in glob.glob(os.path.join(ROOT, "daily_video", "*.py"))}
    assert present <= set(FROZEN), f"unpinned renderer modules: {present - set(FROZEN)}"
