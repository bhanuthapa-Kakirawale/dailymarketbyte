"""Official daily snapshots + durable state (Acquisition -> Durable state -> Publication).

Fully offline: official lists come from synthetic, structurally identical payloads (real
exchange payloads are never committed); the state store is a local directory or a fake GCS
client. "Two runners" = two separate output directories sharing only the StateStore.
"""
import datetime as dt
import json
import os
import subprocess
import sys

import pytest

import config
import main
import state
from exchange_watch import Change, EventFamily
from exchange_watch import sources as exs
from exchange_watch.watch import build_model, select_events
from ipo_watch import sources as ips
from official_snapshots import (ASM, FNO_BAN, GSM, IPO, OfficialDailySnapshotService,
                                OfficialSnapshot, detect, exchange_events, load_current,
                                load_manifest, write_revision)
from products import public_fixtures as PF
from test_pipeline import _Args, offline_pipeline  # noqa: F401  (fixture re-export)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the real fetchers, captured before conftest's autouse offline stubs replace them
REAL_FETCH_FO_BAN, REAL_FETCH_SURVEILLANCE = exs.fetch_fo_ban, exs.fetch_surveillance
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
THURSDAY, FRIDAY, MONDAY = dt.date(2026, 9, 17), dt.date(2026, 9, 18), dt.date(2026, 9, 21)
THU_EVENING = dt.datetime(2026, 9, 17, 19, 30, tzinfo=IST)
EVENING = dt.datetime(2026, 9, 18, 19, 30, tzinfo=IST)
MORNING = dt.datetime(2026, 9, 21, 7, 40, tzinfo=IST)
SATURDAY_LATER = dt.datetime(2026, 9, 26, 11, 0, tzinfo=IST)


# --------------------------------------------------------------------------- synthetic sources
def _nse_ipo_row(name, sym, start, end, band="Rs.258 to Rs.272"):
    return {"companyName": name, "symbol": sym, "series": "EQ",
            "issueStartDate": start.strftime("%d-%b-%Y"), "issueEndDate": end.strftime("%d-%b-%Y"),
            "issuePrice": band}


class Sources:
    """Synthetic official lists. `fail` makes every fetch an error (proves nothing fetched)."""

    def __init__(self, fno=(), fno_date=MONDAY, asm=(), asm_date=FRIDAY, ipo_rows=(), fail=None):
        self.fno, self.fno_date, self.asm, self.asm_date = list(fno), fno_date, list(asm), asm_date
        self.ipo_rows, self.fail, self.calls = list(ipo_rows), fail, []

    def ban(self, now_iso, get=None):
        self.calls.append("FNO_BAN")
        if self.fail:
            raise AssertionError(self.fail)
        return exs.parse_fo_ban(PF.fo_ban_text(self.fno_date, self.fno), now_iso,
                                "synthetic://fo_secban.csv")

    def surv(self, now_iso, nse=None):
        self.calls.append("SURVEILLANCE")
        if self.fail:
            raise AssertionError(self.fail)
        payload = {"longterm": {"data": [
            {"asmSurvIndicator": st, "asmTime": self.asm_date.strftime("%d-%b-%Y"),
             "companyName": f"Demo {s} Ltd.", "symbol": s,
             "survDesc": "Long Term Additional Surveillance Measure"} for s, st in self.asm]},
            "shortterm": {"data": []}}
        return [exs.parse_asm(payload, now_iso, "synthetic://reportASM"),
                exs.parse_gsm([], now_iso, "synthetic://reportGSM")]

    def ipo(self, read_day, now_iso, nse=None):
        self.calls.append("IPO")
        if self.fail:
            raise AssertionError(self.fail)
        evs, notes = ips.parse_nse_issues(self.ipo_rows, "CURRENT", read_day, now_iso)
        return {"events": evs, "notes": notes,
                "lists": [{"name": "CURRENT", "status": "OK" if self.ipo_rows else "EMPTY",
                           "connectivity": "REACHABLE", "rows": len(self.ipo_rows)},
                          {"name": "UPCOMING", "status": "EMPTY", "connectivity": "REACHABLE",
                           "rows": 0}]}

    def install(self, monkeypatch):
        import exchange_watch
        import ipo_watch
        for mod in (exchange_watch, exs):
            monkeypatch.setattr(mod, "fetch_fo_ban", self.ban)
            monkeypatch.setattr(mod, "fetch_surveillance", self.surv)
        for mod in (ipo_watch, ips):
            monkeypatch.setattr(mod, "fetch_nse_issue_lists", self.ipo, raising=False)
        return self

    def service(self, out_dir):
        return OfficialDailySnapshotService(str(out_dir), self.ban, self.surv, self.ipo)


def _intel(out_dir, **kw):
    from presentation.public_intelligence import load_public_intelligence
    kw.setdefault("now", MORNING)
    return load_public_intelligence(kw.pop("structure_session", FRIDAY), MONDAY, str(out_dir),
                                    snapshot_session=kw.pop("snapshot_session", FRIDAY), **kw)


def _sections(intel, day=FRIDAY, universe=True):
    from presentation.public_intelligence import plan_public_sections
    from publication import PublicationGate, resolve_profile
    if universe and not intel.universe_symbols:
        uni = PF.universe()
        intel.universe_symbols, intel.known_securities = uni.symbols(), uni.companies()
    ps = plan_public_sections(PublicationGate(resolve_profile(None)), intel, day, "POST")
    return ps, ps.audit["omitted_sections"]


def _use(monkeypatch, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    monkeypatch.setattr(main, "OUT_DIR", str(out_dir))
    monkeypatch.setattr(config, "OUT_DIR", str(out_dir))


def _radar_with_structure(session, out_dir, as_of):
    """The REPORT job's Radar step, which writes the session's Market Structure snapshot."""
    import market_structure as ms
    snap, uni, obs = PF.structure(session, THURSDAY, "CONCENTRATED")
    ms.save_snapshot(snap, obs, uni, out_dir)
    return {"status": "BUILT", "pipeline_status": "OK", "stories": 0}


def _manifest(out_dir):
    import glob
    paths = glob.glob(os.path.join(str(out_dir), "post", "*", "production_manifest.json"))
    assert len(paths) == 1, paths
    return json.load(open(paths[0], encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_state_session():
    state.reset()
    yield
    state.reset()


# --------------------------------------------------------------------------- two runners
@pytest.fixture
def two_runners(offline_pipeline, monkeypatch, tmp_path):
    """Runner A: the 19:30 REPORT job for Friday captures + persists. Runner B: a CLEAN output
    directory hydrates from the store and renders Monday's POST - with every official fetcher
    wired to fail, so anything B shows came from the persisted snapshots."""
    from products.report_job import run_report_job
    store = state.LocalStateStore(str(tmp_path / "store"))
    runner_a, runner_b = tmp_path / "runnerA", tmp_path / "runnerB"
    _use(monkeypatch, runner_a)
    src_a = Sources(fno=["STOCK-011", "STOCK-042"], asm=[("STOCK-077", "Stage I")],
                    ipo_rows=[_nse_ipo_row("DEMO IPO A LTD", "DEMOA", FRIDAY - dt.timedelta(2),
                                           FRIDAY)]).install(monkeypatch)
    state.hydrate(str(runner_a), store, job="REPORT")          # an empty store: nothing yet
    record = run_report_job(now=EVENING, radar_fn=_radar_with_structure)
    persisted_a = state.persist(str(runner_a), store, job="REPORT")
    import hashlib
    import shutil
    a_checksums = {k: load_current(str(runner_a), FRIDAY, k)[0].checksum
                   for k in (FNO_BAN, ASM, GSM, IPO)}
    ms_file = runner_a / "market_structure" / "market_structure_2026-09-18.json"
    a_structure_sha = hashlib.sha256(ms_file.read_bytes()).hexdigest()
    state.reset()
    shutil.rmtree(runner_a)                                      # the runner disappears

    _use(monkeypatch, runner_b)
    assert not os.listdir(runner_b), "runner B starts clean"
    src_b = Sources(fail="runner B must not acquire - it reads durable state").install(monkeypatch)
    hydrated = state.hydrate(str(runner_b), store, job="POST")
    out = main.run(_Args())
    audit_path = next(os.path.join(dp, f) for dp, _, fs in os.walk(runner_b / "publication")
                      for f in fs if f == "publication_audit.json")
    return {"store": store, "a_checksums": a_checksums, "a_structure_sha": a_structure_sha,
            "a_destroyed": not runner_a.exists(), "b": runner_b, "record": record, "src_a": src_a,
            "src_b": src_b, "hydrated": hydrated, "persisted_a": persisted_a, "video": out,
            "manifest": _manifest(runner_b), "rendered": offline_pipeline,
            "audit": json.load(open(audit_path, encoding="utf-8"))}


# 1 ------------------------------------------------------------------------------------------
def test_report_captures_ipo_snapshot_even_when_no_ipo_scene_is_rendered(offline_pipeline,
                                                                         monkeypatch, tmp_path):
    from products.report_job import run_report_job
    later = MONDAY + dt.timedelta(days=2)                      # opens Wednesday - not an event on Fri
    Sources(ipo_rows=[_nse_ipo_row("DEMO IPO B LTD", "DEMOB", later, later + dt.timedelta(2))]
            ).install(monkeypatch)
    rec = run_report_job(now=EVENING, radar_fn=lambda *a: {"status": "BUILT", "stories": 0})
    snap, _ = load_current(str(tmp_path), FRIDAY, IPO)
    assert snap.status == "SUCCESS" and snap.record_count == 1
    assert snap.records[0]["company_name"] == "DEMO IPO B LTD" and "gmp" not in json.dumps(
        snap.records).lower()
    assert rec["details"]["ipo_snapshot_status"] == "SUCCESS"
    main.run(_Args())
    opt = _manifest(tmp_path)["optional_sections"]["IPO_WATCH"]
    assert opt["code"] == "NO_ELIGIBLE_IPO_EVENT" and opt["snapshot_status"] == "SUCCESS"


# 2 ------------------------------------------------------------------------------------------
def test_report_captures_exchange_snapshots_even_when_nothing_changed(offline_pipeline,
                                                                      monkeypatch, tmp_path):
    from products.report_job import run_report_job
    same = dict(asm=[("STOCK-077", "Stage I")])
    Sources(fno_date=FRIDAY, **same).service(tmp_path).ensure(THURSDAY, THU_EVENING)
    Sources(fno_date=MONDAY, **same).install(monkeypatch)
    rec = run_report_job(now=EVENING, radar_fn=lambda *a: {"status": "BUILT", "stories": 0})
    for kind in (FNO_BAN, ASM, GSM):
        snap, _ = load_current(str(tmp_path), FRIDAY, kind)
        assert snap is not None and snap.validated, kind
    assert rec["details"]["exchange_snapshot_status"] == "SUCCESS"
    assert rec["details"]["esm_status"] == "NOT_SUPPORTED"      # recorded, never queried
    intel = _intel(tmp_path)
    assert intel.exchange_snapshots[ASM]["changes"] == {"UNCHANGED": 1}
    _, codes = _sections(intel)
    assert codes["EXCHANGE_WATCH"]["code"] == "NO_NEW_EVENT"


# 3 ------------------------------------------------------------------------------------------
def test_persisted_snapshots_survive_into_a_separate_runner(two_runners):
    r = two_runners
    assert r["record"]["details"]["official_snapshot_status"] == "SUCCESS"
    assert r["record"]["details"]["persisted_to_state_store"] is True
    assert r["record"]["details"]["state_store_backend"] == "local"
    assert r["hydrated"]["hydrated"] is True
    assert r["a_destroyed"], "runner A no longer exists when runner B runs"
    for kind in (FNO_BAN, ASM, GSM, IPO):
        b, _ = load_current(str(r["b"]), FRIDAY, kind)
        assert b is not None and b.checksum == r["a_checksums"][kind] and b.validated, kind
    assert r["src_b"].calls == [], "runner B acquired nothing"
    assert r["manifest"]["report_source"] == "REUSED_CANONICAL"


# 4 ------------------------------------------------------------------------------------------
def test_post_consumes_the_persisted_market_structure_snapshot(two_runners):
    inputs = two_runners["manifest"]["inputs"]["market_structure"]
    assert inputs["source"] == "PERSISTED_SNAPSHOT" and inputs["session_date"] == "2026-09-18"
    assert two_runners["a_structure_sha"] == inputs["sha256"], "exactly the evening's snapshot"
    assert "STRUCTURE" in two_runners["manifest"]["scenes"]


# 5 ------------------------------------------------------------------------------------------
def test_post_consumes_the_persisted_ipo_snapshot(two_runners):
    m = two_runners["manifest"]
    ipo = m["inputs"]["ipo_watch"]
    assert ipo["source"] == "PERSISTED_SNAPSHOT"
    assert ipo["snapshot"]["status"] == "SUCCESS" and ipo["snapshot"]["capture_mode"] == "REPORT_JOB"
    assert m["optional_sections"]["IPO_WATCH"]["code"] == "RENDERED"
    assert m["optional_sections"]["IPO_WATCH"]["input_source"] == "PERSISTED_SNAPSHOT"


# 6 ------------------------------------------------------------------------------------------
def test_post_consumes_the_persisted_exchange_snapshot(two_runners):
    m = two_runners["manifest"]
    ex = m["inputs"]["exchange_watch"]
    assert ex["source"] == "PERSISTED_SNAPSHOT"
    assert ex["snapshots"]["FNO_BAN"]["source_date"] == "2026-09-21"      # the trade date
    assert ex["snapshots"]["FNO_BAN"]["baseline"] is None                 # no Thursday snapshot
    assert m["optional_sections"]["EXCHANGE_WATCH"]["code"] == "RENDERED"
    assert m["optional_sections"]["EXCHANGE_WATCH"]["input_source"] == "PERSISTED_SNAPSHOT"
    assert two_runners["audit"]["inputs"]["exchange_watch"]["source"] == "PERSISTED_SNAPSHOT"


# 7 ------------------------------------------------------------------------------------------
def test_historical_replay_never_uses_todays_live_data(offline_pipeline, monkeypatch, tmp_path):
    from products.report_job import run_report_job
    run_report_job(now=EVENING, radar_fn=lambda *a: {"status": "BUILT", "stories": 0},
                   official_fn=lambda *a: {"official_snapshot_status": "NOT_CAPTURED"})
    src = Sources(fail="a replay must never fetch today's pages").install(monkeypatch)
    args = _Args()
    args.session_date = FRIDAY.isoformat()
    assert main.run(args)
    assert src.calls == []
    m = _manifest(tmp_path)
    assert m["inputs"]["exchange_watch"]["source"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"
    assert m["inputs"]["ipo_watch"]["source"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"
    # a past session is never captured from today's pages, even by a live (fetch=True) load
    intel = _intel(tmp_path / "later", fetch=True, now=SATURDAY_LATER, fo_ban_fn=src.ban,
                   surveillance_fn=src.surv, ipo_fn=src.ipo)
    assert src.calls == [] and intel.exchange_status == "HISTORICAL_SNAPSHOT_UNAVAILABLE"


# 8 ------------------------------------------------------------------------------------------
def test_missing_historical_snapshot_is_historical_snapshot_unavailable(tmp_path):
    intel = _intel(tmp_path, replay=True)
    _, codes = _sections(intel)
    for key in ("EXCHANGE_WATCH", "IPO_WATCH", "MARKET_STRUCTURE"):
        assert codes[key]["code"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE", key
        assert codes[key]["code"] != "SOURCE_UNAVAILABLE"


# 9 ------------------------------------------------------------------------------------------
def test_live_fetch_failure_is_source_unavailable(tmp_path):
    intel = _intel(tmp_path, fetch=True)              # conftest: every request BLOCKED
    snaps = intel.exchange_snapshots
    assert {snaps[k]["status"] for k in (FNO_BAN, ASM, GSM)} == {"SOURCE_UNAVAILABLE"}
    assert snaps[FNO_BAN]["connectivity_status"] == "BLOCKED"
    assert intel.exchange_status == intel.ipo_status == "CAPTURED_THIS_RUN"
    _, codes = _sections(intel)
    for key in ("EXCHANGE_WATCH", "IPO_WATCH"):
        assert codes[key]["code"] == "SOURCE_UNAVAILABLE"
        assert codes[key]["connectivity_status"] == "BLOCKED"
        assert codes[key]["snapshot_status"] == "SOURCE_UNAVAILABLE"


def test_connectivity_is_classified_at_the_request():
    import requests
    from operations.connectivity import classify_exception

    def boom(exc):
        def get(url):
            raise exc
        return get
    assert REAL_FETCH_FO_BAN("t", get=boom(requests.exceptions.Timeout("slow"))).connectivity == "TIMEOUT"
    resp = requests.models.Response()
    resp.status_code = 403
    r = REAL_FETCH_FO_BAN("t", get=boom(requests.exceptions.HTTPError(response=resp)))
    assert r.status == "UNAVAILABLE" and r.connectivity == "BLOCKED"
    assert classify_exception(ValueError("Expecting value")) == "REACHABLE"   # answered, unparsable

    class NSE:
        def get(self, path):
            raise ValueError("Expecting value: line 1 column 1")
    res = REAL_FETCH_SURVEILLANCE("t", nse=NSE())
    assert [(x.list_name, x.status, x.connectivity) for x in res] == [
        ("ASM", "INVALID", "REACHABLE"), ("GSM", "INVALID", "REACHABLE")]
    snap = OfficialDailySnapshotService._exchange_snapshot(ASM, res[0], MONDAY, {
        "session_date": "2026-09-18", "capture_mode": "REPORT_JOB", "expected_list_date": None},
        exact=False)
    assert snap.status == "PARSE_ERROR" and snap.connectivity_status == "REACHABLE"


# 10 -----------------------------------------------------------------------------------------
def test_successful_source_with_no_qualifying_event_is_no_eligible_event(tmp_path):
    Sources(fno=[], asm=[("OUTSIDE-1", "Stage I")]).service(tmp_path).ensure(FRIDAY, EVENING)
    intel = _intel(tmp_path)
    assert intel.exchange_snapshots[FNO_BAN]["status"] == "NO_DATA"
    _, codes = _sections(intel)
    assert codes["EXCHANGE_WATCH"]["code"] == "NO_ELIGIBLE_EVENT"
    assert codes["EXCHANGE_WATCH"]["snapshot_status"] == "SUCCESS"


# 11 -----------------------------------------------------------------------------------------
def test_successful_ipo_source_with_no_qualifying_event_is_no_eligible_ipo_event(tmp_path):
    later = MONDAY + dt.timedelta(days=3)
    Sources(ipo_rows=[_nse_ipo_row("DEMO IPO C LTD", "DEMOC", later, later + dt.timedelta(2))]
            ).service(tmp_path).ensure(FRIDAY, EVENING)
    empty = tmp_path / "empty"
    Sources().service(empty).ensure(FRIDAY, EVENING)
    for out, count in ((tmp_path, 1), (empty, 0)):
        intel = _intel(out)
        assert intel.ipo_snapshot["record_count"] == count
        ps, codes = _sections(intel)
        assert codes["IPO_WATCH"]["code"] == "NO_ELIGIBLE_IPO_EVENT"
        assert ps.audit["ipo"]["derived_events"] == []


def test_ipo_events_are_derived_from_the_stored_snapshot(tmp_path):
    Sources(ipo_rows=[_nse_ipo_row("DEMO IPO A LTD", "DEMOA", FRIDAY - dt.timedelta(2), FRIDAY),
                      _nse_ipo_row("DEMO IPO B LTD", "DEMOB", FRIDAY, FRIDAY + dt.timedelta(4))]
            ).service(tmp_path).ensure(FRIDAY, EVENING)
    ps, codes = _sections(_intel(tmp_path))
    kinds = sorted((e["company"], e["event_type"]) for e in ps.audit["ipo"]["derived_events"])
    assert kinds == [("DEMO IPO A LTD", "CLOSES_TODAY"), ("DEMO IPO B LTD", "OPENS_TODAY")]
    assert codes["IPO_WATCH"]["code"] == "RENDERED"


def test_an_incomplete_ipo_read_is_never_the_days_state(tmp_path):
    def partial(read_day, now_iso, nse=None):
        evs, _ = ips.parse_nse_issues([_nse_ipo_row("DEMO IPO A LTD", "DEMOA", FRIDAY, FRIDAY)],
                                      "CURRENT", read_day, now_iso)
        return {"events": evs, "notes": [], "lists": [
            {"name": "CURRENT", "status": "OK", "connectivity": "REACHABLE"},
            {"name": "UPCOMING", "status": "UNAVAILABLE", "connectivity": "TIMEOUT"}]}
    OfficialDailySnapshotService(str(tmp_path), ipo_fn=partial).ensure(FRIDAY, EVENING)
    snap, _ = load_current(str(tmp_path), FRIDAY, IPO)
    assert snap.status == "SOURCE_UNAVAILABLE" and snap.records == [] and snap.rejected
    assert snap.connectivity_status == "TIMEOUT"


# 12 -----------------------------------------------------------------------------------------
def _snap(kind, records, session=FRIDAY, source_date=MONDAY, status="SUCCESS"):
    return OfficialSnapshot(kind=kind, session_date=session.isoformat(),
                            source_date=source_date.isoformat(), source_name="nse_fo_secban",
                            source_reference="synthetic://list", retrieved_at="t", status=status,
                            connectivity_status="REACHABLE", records=records).seal()


def _rows(kind, pairs):
    lst = "longterm" if kind == ASM else kind
    return [{"symbol": s, "company": "", "list": lst, "status": st, "detail": "", "row_date": "x"}
            for s, st in pairs]


def test_change_detection_identifies_entered_exited_and_stage_changed():
    prev = _snap(FNO_BAN, _rows(FNO_BAN, [("A", "IN BAN PERIOD"), ("B", "IN BAN PERIOD")]),
                 THURSDAY, FRIDAY)
    cur = _snap(FNO_BAN, _rows(FNO_BAN, [("B", "IN BAN PERIOD"), ("C", "IN BAN PERIOD")]))
    assert {s: c for s, c, _ in detect(FNO_BAN, cur, prev)} == {
        "C": Change.ENTERED_BAN, "B": Change.REMAINS_IN_BAN, "A": Change.EXITED_BAN}
    prev = _snap(ASM, _rows(ASM, [("X", "Long-term ASM · Stage I"), ("Y", "Long-term ASM · Stage I"),
                                  ("Z", "Long-term ASM · Stage I")]), THURSDAY, THURSDAY)
    cur = _snap(ASM, _rows(ASM, [("X", "Long-term ASM · Stage II"), ("Y", "Long-term ASM · Stage I"),
                                 ("W", "Long-term ASM · Stage I")]), FRIDAY, FRIDAY)
    assert {s: c for s, c, _ in detect(ASM, cur, prev)} == {
        "X": Change.STAGE_CHANGED, "Y": Change.UNCHANGED, "W": Change.ENTERED, "Z": Change.REMOVED}
    # public selection: changes first, unchanged surveillance is not an event
    evs = exchange_events(ASM, cur, prev)
    chosen, omitted = select_events(evs, {"X", "Y", "Z", "W"})
    assert [e.change for e in chosen] == [Change.ENTERED, Change.STAGE_CHANGED, Change.REMOVED]
    assert any("no new event" in o["reason"] for o in omitted)
    # an F&O exit card never claims the ban is in effect
    fno = exchange_events(
        FNO_BAN, _snap(FNO_BAN, _rows(FNO_BAN, [("B", "IN BAN PERIOD")])),
        _snap(FNO_BAN, _rows(FNO_BAN, [("A", "IN BAN PERIOD"), ("B", "IN BAN PERIOD")]),
              THURSDAY, FRIDAY))
    model = build_model(select_events(fno)[0], "POST")
    exit_card = next(c for c in model["cards"] if c["name"] == "A")
    assert exit_card["status"].startswith("NOT IN BAN") and "ban period" not in model["headline"]
    assert "Not on the exchange's ban list" in exit_card["line"]


# 13 -----------------------------------------------------------------------------------------
def test_no_previous_snapshot_never_claims_entered(tmp_path):
    cur = _snap(FNO_BAN, _rows(FNO_BAN, [("C", "IN BAN PERIOD")]))
    for prev in (None, _snap(FNO_BAN, [], THURSDAY, FRIDAY, status="SOURCE_UNAVAILABLE")):
        evs = exchange_events(FNO_BAN, cur, prev)
        assert [(e.symbol, e.change) for e in evs] == [("C", Change.CHANGE_UNKNOWN)]
        card = build_model(evs, "POST")["cards"][0]
        assert card["change"] == "" and "entered" not in json.dumps(card).lower()
    # a TWO-sessions-old snapshot never stands in for the missing previous one
    src = Sources(fno=["C"], fno_date=THURSDAY)
    src.service(tmp_path).ensure(dt.date(2026, 9, 16),
                                 dt.datetime(2026, 9, 16, 19, 30, tzinfo=IST))
    Sources(fno=["C", "D"]).service(tmp_path).ensure(FRIDAY, EVENING)
    intel = _intel(tmp_path)
    assert {e.change for e in intel.exchange_events if e.family is EventFamily.FNO_BAN} == {
        Change.CHANGE_UNKNOWN}
    assert intel.exchange_snapshots[FNO_BAN]["baseline"] is None


# immutability ---------------------------------------------------------------------------------
def test_a_validated_snapshot_is_never_overwritten_and_failures_are_retried(tmp_path):
    first = Sources(fno=["A"], fno_date=FRIDAY)                  # 19:30: still Friday's file
    first.service(tmp_path).ensure(FRIDAY, EVENING)
    snap, name = load_current(str(tmp_path), FRIDAY, FNO_BAN)
    assert snap.status == "VALIDATION_FAILED" and snap.records == [] and name == "fno_ban_snapshot.json"
    retry = Sources(fno=["A"], fno_date=MONDAY)                  # 21:30 retry: Monday's file
    retry.service(tmp_path).ensure(FRIDAY, EVENING.replace(hour=21))
    snap, name = load_current(str(tmp_path), FRIDAY, FNO_BAN)
    assert snap.status == "SUCCESS" and name == "fno_ban_snapshot.r2.json" and snap.revision == 2
    assert retry.calls.count("FNO_BAN") == 1
    again = Sources(fno=["A", "B"], fno_date=MONDAY)             # a later, DIFFERENT reading
    summary = again.service(tmp_path).ensure(FRIDAY, EVENING.replace(hour=22))
    assert "FNO_BAN" not in again.calls and "FNO_BAN" in summary["already_validated"]
    snap, _ = load_current(str(tmp_path), FRIDAY, FNO_BAN)
    assert [r["symbol"] for r in snap.records] == ["A"], "validated state is immutable"
    with pytest.raises(ValueError):
        write_revision(str(tmp_path), _snap(FNO_BAN, []))
    entry = load_manifest(str(tmp_path), FRIDAY)["snapshots"][FNO_BAN]
    assert [r["status"] for r in entry["revisions"]] == ["VALIDATION_FAILED", "SUCCESS"]
    # tampering is detected, never used
    path = tmp_path / "official_snapshots" / "2026-09-18" / "fno_ban_snapshot.r2.json"
    d = json.load(open(path, encoding="utf-8"))
    d["records"].append({"symbol": "FAKE"})
    json.dump(d, open(path, "w", encoding="utf-8"))
    snap, _ = load_current(str(tmp_path), FRIDAY, FNO_BAN)
    assert snap.status == "VALIDATION_FAILED" and snap.records == []


def test_capture_window_is_the_current_session_only(tmp_path):
    svc = Sources(fno=["A"]).service(tmp_path)
    assert svc.capture_allowed(FRIDAY, EVENING)[1] == "CURRENT"
    assert svc.capture_allowed(FRIDAY, MORNING)[1] == "CURRENT"              # before Monday's open
    assert svc.capture_allowed(FRIDAY, MORNING.replace(hour=9, minute=20))[1] == "WINDOW_CLOSED"
    assert svc.capture_allowed(THURSDAY, EVENING)[1] == "HISTORICAL_SESSION"
    assert svc.capture_allowed(FRIDAY, EVENING.replace(hour=14))[1] == "NOT_FINAL"
    assert svc.ensure(THURSDAY, EVENING)["capture"] == "REFUSED"
    assert load_manifest(str(tmp_path), THURSDAY) is None


# 14 -----------------------------------------------------------------------------------------
def test_local_state_store_works_fully_offline(tmp_path):
    from state import LocalStateStore, StateConflict
    store = LocalStateStore(str(tmp_path / "store"))
    f = tmp_path / "f.json"
    f.write_text("{}", encoding="utf-8")
    v1 = store.put("reports/r.json", str(f))
    with pytest.raises(StateConflict):
        store.put("reports/r.json", str(f))                     # create-only
    f.write_text('{"a": 1}', encoding="utf-8")
    v2 = store.put("reports/r.json", str(f), expected=v1)
    with pytest.raises(StateConflict):
        store.put("reports/r.json", str(f), expected=v1)        # stale version
    assert store.list("reports") == {"reports/r.json": {"md5": v2, "version": v2}}
    store.get("reports/r.json", str(tmp_path / "back.json"))
    assert (tmp_path / "back.json").read_text(encoding="utf-8") == '{"a": 1}'
    with pytest.raises(state.StateStoreError):
        store.put("../escape.json", str(f))


def test_default_local_backend_is_existing_behaviour(tmp_path):
    assert state.from_env(str(tmp_path), {}) is None
    assert state.from_env(str(tmp_path), {"DMB_STATE_DIR": str(tmp_path)}) is None
    assert state.hydrate(str(tmp_path), env={})["hydrated"] is False
    assert state.persist(str(tmp_path), env={})["persisted_to_state_store"] is False
    with pytest.raises(state.StateStoreError):
        state.from_env(str(tmp_path), {"DMB_STATE_BACKEND": "s3"})
    with pytest.raises(state.StateStoreError):
        state.from_env(str(tmp_path), {"DMB_STATE_BACKEND": "gcs"})        # no bucket


# 15 -----------------------------------------------------------------------------------------
class PreconditionFailed(Exception):
    pass


class FakeGCS:
    """The slice of google.cloud.storage the store uses - in memory, generations included."""

    def __init__(self):
        self.objects, self.gen = {}, 0

    def bucket(self, name):
        return _FakeBucket(self, name)

    def list_blobs(self, bucket, prefix=""):
        return [_FakeBlob(self, bucket, k) for k in sorted(self.objects)
                if k[0] == bucket and k[1].startswith(prefix)]


class _FakeBucket:
    def __init__(self, fake, name):
        self.fake, self.name = fake, name

    def blob(self, key):
        return _FakeBlob(self.fake, self.name, (self.name, key))


class _FakeBlob:
    def __init__(self, fake, bucket, key):
        self.fake, self.key = fake, key if isinstance(key, tuple) else (bucket, key)
        self.name = self.key[1]

    @property
    def generation(self):
        return self.fake.objects.get(self.key, (None, 0))[1]

    @property
    def md5_hash(self):
        import base64
        import hashlib
        data = self.fake.objects[self.key][0]
        return base64.b64encode(hashlib.md5(data).digest()).decode()

    def upload_from_filename(self, path, if_generation_match=None):
        if if_generation_match is not None and self.generation != if_generation_match:
            raise PreconditionFailed(self.name)
        self.fake.gen += 1
        self.fake.objects[self.key] = (open(path, "rb").read(), self.fake.gen)

    def download_to_filename(self, path):
        open(path, "wb").write(self.fake.objects[self.key][0])


def test_gcs_state_store_with_a_fake_client(tmp_path):
    from state import GCSStateStore, StateConflict
    fake = FakeGCS()
    store = GCSStateStore("demo-bucket", "dmb/prod", client=fake)
    f = tmp_path / "x.json"
    f.write_text("{}", encoding="utf-8")
    g1 = store.put("reports/x.json", str(f))
    assert ("demo-bucket", "dmb/prod/reports/x.json") in fake.objects
    with pytest.raises(StateConflict):
        store.put("reports/x.json", str(f))                          # generation 0 = must not exist
    g2 = store.put("reports/x.json", str(f), expected=g1)
    with pytest.raises(StateConflict):
        store.put("reports/x.json", str(f), expected=g1)
    listing = store.list("reports")
    assert list(listing) == ["reports/x.json"] and listing["reports/x.json"]["version"] == g2
    store.get("reports/x.json", str(tmp_path / "y.json"))
    # full round trip through hydrate/persist, two runners
    a, b = tmp_path / "A", tmp_path / "B"
    (a / "official_snapshots" / "2026-09-18").mkdir(parents=True)
    (a / "official_snapshots" / "2026-09-18" / "fno_ban_snapshot.json").write_text("{}")
    (a / "data").mkdir()
    (a / "data" / "market_history.db").write_bytes(b"db")
    res = state.persist(str(a), store, require_hydrated=False)
    assert res["persisted_to_state_store"] and res["uploaded"] == 2
    state.hydrate(str(b), store)
    assert (b / "official_snapshots" / "2026-09-18" / "fno_ban_snapshot.json").exists()


def test_persist_refuses_without_a_successful_hydration(tmp_path):
    store = state.LocalStateStore(str(tmp_path / "store"))
    res = state.persist(str(tmp_path), store)
    assert res["persisted_to_state_store"] is False and "not hydrated" in res["reason"]


def test_a_misconfigured_store_stops_the_scheduled_job_before_it_runs(tmp_path):
    env = dict(os.environ, DMB_STATE_BACKEND="gcs", DMB_GCS_BUCKET="",
               DAILY_BYTE_OUT=str(tmp_path / "out"))
    res = subprocess.run([sys.executable, "main.py", "--mode", "report", "--skip-radar"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert res.returncode == 1 and "hydration FAILED" in res.stdout
    assert not os.path.exists(tmp_path / "out" / "reports"), "no job ran on missing state"


# 16 -----------------------------------------------------------------------------------------
def test_no_credentials_or_bucket_names_reach_logs_or_audits(tmp_path, monkeypatch, capsys):
    secret_bucket, secret_key = "acct-private-bucket-7781", "/secret/sa-key.json"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", secret_key)
    store = state.GCSStateStore(secret_bucket, "dmb", client=FakeGCS())
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "r.json").write_text("{}")
    blob = json.dumps([state.persist(str(tmp_path), store, require_hydrated=False),
                       state.hydrate(str(tmp_path / "b"), store), store.describe()])
    out = capsys.readouterr().out
    for secret in (secret_bucket, secret_key):
        assert secret not in blob and secret not in out
    names = {ns.local for ns in state.NAMESPACES}
    assert not names & {".env", "token.json", "client_secret.json"}
    assert all(ns.pattern in ("*.json", "*.db") for ns in state.NAMESPACES)


# 17 / 18 ------------------------------------------------------------------------------------
def test_rights_block_and_post_unified_are_unchanged(two_runners, monkeypatch):
    m, audit = two_runners["manifest"], two_runners["audit"]
    assert m["product"] == "POST_UNIFIED" and m["legacy_renderer_invoked"] is False
    assert audit["rights_policy"] == {"PUBLIC_REVIEW_REQUIRED_POLICY": "BLOCK"}
    assert "publication_rights" in audit["failed_checks"] and audit["final"] == "BLOCK"
    assert m["upload"].startswith("NOT_ATTEMPTED")


# 19 -----------------------------------------------------------------------------------------
REAL_24 = os.path.join(ROOT, "output", "reports", "premarket_2026-09-25.json")


@pytest.mark.skipif(not os.path.exists(REAL_24), reason="real 24 Sep artifacts are local only")
def test_approved_24_sep_sequence_is_unchanged_from_stored_inputs(monkeypatch):
    import render_daily_market_byte as review
    from presentation.public_intelligence import load_public_intelligence
    from products import post_unified as PU
    from publication import resolve_profile
    out = os.path.join(ROOT, "output")
    prof = resolve_profile(None)
    report, plan = review.load_report_and_plan(REAL_24, profile=prof)
    intel = load_public_intelligence(dt.date(2026, 9, 24), dt.date(2026, 9, 25), out,
                                     replay=True, snapshot_session=dt.date(2026, 9, 24))
    sb, _ = PU.build_post_storyboard(report, plan, profile=prof, intelligence=intel,
                                     radar_dir=os.path.join(out, "radar"))
    assert [s.kind for s in sb.scenes] == ["DYNAMIC_HOOK", "NIFTY", "SECTORS", "FLOWS",
                                           "STRUCTURE", "STRUCTURE", "CLOSING"]
    assert 36.0 <= sb.total_duration <= 38.5
    inputs = sb.public_audit["inputs"]
    assert inputs["market_structure"]["source"] == "PERSISTED_SNAPSHOT"
    codes = sb.public_audit["omitted_sections"]
    assert codes["IPO_WATCH"]["code"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"
    assert codes["EXCHANGE_WATCH"]["code"] == "HISTORICAL_SNAPSHOT_UNAVAILABLE"


# workflows ------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["market_report.yml", "pre_shadow.yml", "daily_byte.yml"])
def test_scheduled_workflows_use_the_state_store_not_the_cache(name):
    wf = open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8").read()
    assert "DMB_STATE_BACKEND: ${{ vars.DMB_STATE_BACKEND || 'local' }}" in wf
    assert "id-token: write" in wf and "google-github-actions/auth@v2" in wf
    assert "workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}" in wf
    assert "credentials_json" not in wf, "no long-lived JSON service-account key"
    # actions/cache only as the non-canonical fallback of the local backend
    for line in [ln for ln in wf.splitlines() if "actions/cache/" in ln]:
        block = wf[:wf.index(line)].rsplit("- name:", 1)[1]
        assert "env.DMB_STATE_BACKEND != 'gcs'" in block, name
    assert "output/market_structure" in wf and "output/official_snapshots" in wf
    assert "group: daily-byte-state" in wf
