"""Private Trading Intelligence Desk (Phase 1): read-only, local-only, observation-only.

Every test runs against a SYNTHETIC output tree built in tmp_path with the real store classes
(placeholder symbols SYMA..SYMF). The candidate history is produced by the Radar's own detector
chain over that synthetic OHLCV - the same way the evening run produces it - so the desk's
replay/reconciliation is tested against genuine detector output, not hand-written expectations.
One test opens the REAL local stores (skipped when absent) to prove the desk changes nothing.
"""
import ast
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3

import pytest

from conftest import SESSION

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESK = os.path.join(ROOT, "private_desk")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PLACEHOLDER_HOLIDAY = dt.date(2026, 9, 14)          # NSE holiday inside the fixture window
EDITION = dt.date(2026, 9, 21)
CONSTITUENTS = {
    "SYMA": {"company": "Synthetic A Ltd.", "industry": "Healthcare"},
    "SYMB": {"company": "Synthetic B Ltd.", "industry": "Financial Services"},
    "SYMC": {"company": "Synthetic C Ltd.", "industry": "Financial Services"},
    "SYMD": {"company": "Synthetic D Ltd.", "industry": "Information Technology"},
    "SYME": {"company": "Synthetic E Ltd.", "industry": "Healthcare"},
    "SYMF": {"company": "Synthetic F Ltd.", "industry": "Automobile and Auto Components"},
}
FORBIDDEN_WORDS = re.compile(r"\b(buy|sell|hold|target price|stop[- ]loss|entry price|"
                             r"expected return|best stocks)\b", re.I)


# ------------------------------------------------------------------ synthetic data
def _series():
    from core.trading_calendar import SessionCalendar
    days = SessionCalendar().sessions_between(dt.date(2026, 3, 2), SESSION)
    n = len(days)
    rows = {}
    for i, d in enumerate(days):
        osc = 1 + 0.002 * math.sin(i * 1.7)
        k = max(0, i - (n - 21))                       # 0 before the last 20 sessions
        last = i == n - 1
        closes = {
            "^NSEI": 20000 * osc,
            "SYMA": 100 * osc * (1.005 ** k) * (1.06 if last else 1),
            "SYMB": 100 * osc * (0.995 ** k) * (0.94 if last else 1),
            "SYMC": 50 * osc, "SYMD": 80 * osc, "SYME": 120 * osc, "SYMF": 60 * osc,
        }
        for sym, c in closes.items():
            if sym == "SYMD" and last:
                continue                               # SYMD has no bar on the session
            vol = 1e6 * (1 + 0.1 * math.sin(i))
            if sym == "SYMA" and last:
                vol = 5.5e6
            rows.setdefault(sym, []).append((d, c * 0.999, c * 1.003, c * 0.997, c, vol))
    return days, rows


def _write_ohlcv(out):
    from storage.ohlcv_models import OHLCVBar, QualityStatus
    from storage.ohlcv_repository import OHLCVStore, default_db_path
    days, rows = _series()
    bars = [OHLCVBar(symbol=s, session_date=d, open=o, high=h, low=l, close=c, volume=v,
                     source="yahoo", retrieved_at=dt.datetime(2026, 9, 18, 16, 30, tzinfo=IST),
                     quality_status=QualityStatus.OK)
            for s, rs in rows.items() for (d, o, h, l, c, v) in rs]
    # a provider holiday placeholder (O=H=L=C, volume 0) - must never reach a chart or average
    prev = [r for r in rows["SYMC"] if r[0] < PLACEHOLDER_HOLIDAY][-1][4]
    bars.append(OHLCVBar(symbol="SYMC", session_date=PLACEHOLDER_HOLIDAY, open=prev, high=prev,
                         low=prev, close=prev, volume=0, source="yahoo",
                         retrieved_at=dt.datetime(2026, 9, 18, 16, 30, tzinfo=IST),
                         quality_status=QualityStatus.OK))
    store = OHLCVStore(default_db_path(out))
    store.upsert_bars(bars)
    store.close()
    return days, rows


@pytest.fixture
def desk_out(tmp_path):
    """A complete synthetic DMB output tree for SESSION (2026-09-18)."""
    from market_structure import aggregate, build_observations, save_snapshot
    from market_structure.universe import OFFICIAL_SOURCE, UniverseDefinition
    from official_snapshots.models import OfficialSnapshot
    from official_snapshots.store import write_revision
    from private_desk.replay import run_detectors
    from private_desk.repository import DeskRepository
    from radar.daily_pipeline import _to_stored_state
    from storage.candidate_history_repository import CandidateHistoryStore
    from storage.candidate_history_repository import default_db_path as ch_path
    from storage.editorial_models import EditorialSelection
    from storage.editorial_repository import EditorialStore
    from storage.editorial_repository import default_db_path as ed_path

    out = str(tmp_path / "out")
    days, rows = _write_ohlcv(out)
    repo = DeskRepository(out)

    # candidate history for the last 6 sessions, produced by the Radar's own detectors
    store = CandidateHistoryStore(ch_path(out))
    for d in days[-6:]:
        run = run_detectors(repo, d, CONSTITUENTS)
        store.save_candidates(d, [_to_stored_state(c) for c in run["composite"].candidates])
        store.mark_run(d, run["composite"].calculation_version, "COMPLETE",
                       run["composite"].candidate_count, dt.datetime(2026, 9, 18, 22, tzinfo=IST))
    store.close()

    # Market Structure artifact for SESSION over the same detector output
    run = run_detectors(repo, SESSION, CONSTITUENTS)
    uni = UniverseDefinition.from_dict({"index": "SYNTH6", "constituents": CONSTITUENTS,
                                        "source": OFFICIAL_SOURCE,
                                        "source_reference": "synthetic://nifty200",
                                        "retrieved_at": "2026-09-18T22:00:00+05:30"})
    obs = build_observations(uni, SESSION, run["prev_date"], run["volume"], run["technical"],
                             run["dataset"].series_by_symbol)
    save_snapshot(aggregate(obs, uni, SESSION, []), obs, uni, out)

    # editorial selection of SYMA
    ed = EditorialStore(ed_path(out))
    ed.save_selections([EditorialSelection(
        selection_id=f"{SESSION}:SYMA:1.0", session_date=SESSION, instrument="SYMA",
        novelty_type="NEW_CANDIDATE", active_families=("VOLUME",), independent_signal_count=3,
        direction_compatibility="ALIGNED_POSITIVE", attention_level="HIGH_INTEREST",
        selection_bucket="RESERVED_3FAMILY_NEW_CANDIDATE", reserved_3family=True,
        diversity_role="NOT_APPLICABLE", cooldown_status="NOT_RECENTLY_PUBLISHED",
        cooldown_override_reason=None, reason_codes=("VOLUME_EXTREME",),
        selection_reason="Reserved because three independent evidence families were active.",
        selector_version="1.0", selected_at=dt.datetime(2026, 9, 18, 22, tzinfo=IST))])
    ed.close()

    # radar artifact
    os.makedirs(os.path.join(out, "radar"))
    with open(os.path.join(out, "radar", f"daily_radar_{SESSION}.json"), "w") as fh:
        json.dump({"session_date": SESSION.isoformat(), "pipeline_status": "SUCCESS",
                   "generated_at": "2026-09-18T22:05:00+05:30", "issues": [],
                   "issue_counts": {"INFO": 0}, "universe_requested": 6, "universe_usable": 5}, fh)

    # official F&O ban snapshot naming SYMB
    write_revision(out, OfficialSnapshot(
        kind="FNO_BAN", session_date=SESSION.isoformat(), source_date=EDITION.isoformat(),
        source_name="nse_fo_secban", source_reference="synthetic://fo_secban.csv",
        retrieved_at="2026-09-18T16:35:00+00:00", status="SUCCESS",
        connectivity_status="REACHABLE",
        records=[{"symbol": "SYMB", "company": "", "list": "FNO_BAN", "status": "IN BAN PERIOD",
                  "detail": "Security in the F&O ban period", "row_date": EDITION.isoformat()}]
    ).seal())

    # canonical report row + artifact
    rid = f"{EDITION:%Y%m%d}_PRE_MARKET"
    art = os.path.join(out, "reports", f"premarket_{EDITION}.json")
    os.makedirs(os.path.dirname(art))
    with open(art, "w") as fh:
        json.dump({"report_id": rid, "session_date": SESSION.isoformat(),
                   "generated_at": "2026-09-18T22:06:00+05:30",
                   "validation_summary": {"publication_ready": True},
                   "nifty": {"close": 20012.5, "change_pct": 0.25, "india_vix": 13.1},
                   "institutional_flows": {}, "sectors": [{"name": "IT", "change_pct": 0.4}],
                   "facts": [{"metric": "INDEX_CLOSE", "instrument": "NIFTY 50",
                              "validation_status": "VERIFIED"}]}, fh)
    with sqlite3.connect(os.path.join(out, "data", "market_history.db")) as c:
        c.execute("CREATE TABLE reports (report_id TEXT, report_type TEXT, report_date TEXT, "
                  "session_date TEXT, generated_at TEXT, publication_ready INTEGER, is_demo "
                  "INTEGER, json_artifact_path TEXT, created_at TEXT)")
        c.execute("CREATE TABLE publication_runs (run_id TEXT, mode TEXT, job_type TEXT, "
                  "target_date TEXT, source_session_date TEXT, run_status TEXT, started_at TEXT, "
                  "failure_stage TEXT, failure_reason TEXT)")
        c.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?)",
                  (rid, "PRE_MARKET", EDITION.isoformat(), SESSION.isoformat(),
                   "2026-09-18T22:06:00+05:30", 1, 0, art, "2026-09-18T16:36:00"))
    return out


NOW_AFTER = dt.datetime(2026, 9, 18, 20, 0, tzinfo=IST)     # SESSION is the latest completed


@pytest.fixture
def svc(desk_out, tmp_path, monkeypatch):
    import config
    from private_desk.services import DeskService
    from private_desk.settings import DeskSettings
    monkeypatch.setattr(config, "now_ist", lambda: NOW_AFTER)
    return DeskService(DeskSettings.from_out_dir(desk_out, cache_dir=str(tmp_path / "cache")))


@pytest.fixture
def client(svc):
    pytest.importorskip("fastapi", reason="desk web deps: pip install -r requirements-desk.txt")
    from fastapi.testclient import TestClient
    from private_desk.app import create_app
    app = create_app(svc.settings)
    return TestClient(app, raise_server_exceptions=True)


def _fingerprint(root, exclude=("private_desk",)):
    out = {}
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if os.path.relpath(os.path.join(dirpath, d), root)
                       not in exclude]
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _desk_sources():
    for dirpath, _, files in os.walk(DESK):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


ALL_PAGES = ["/", "/radar", "/radar.csv", "/stock", "/stock/SYMA", "/stock/SYMB", "/stock/SYMD",
             "/stock?q=SYMC", "/api/chart/SYMA", "/api/chart/SYMC", "/sectors", "/history",
             "/history?family=VOLUME", "/quality", "/healthz"]


# ------------------------------------------------------------------ 1. read-only access
def test_all_sqlite_access_goes_through_the_read_only_helper():
    for path in _desk_sources():
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "connect":
                owner = getattr(node.func.value, "id", "")
                assert owner != "sqlite3", f"{path}: direct sqlite3.connect"
        for banned in ("CandidateHistoryStore(", "EditorialStore(", "OHLCVStore(", "MarketHistory(",
                       "save_report", "upsert_bars", "save_candidates", "save_selections",
                       "mark_run", "write_revision", "save_snapshot", "run_daily_radar",
                       "invalidate_sessions", ".ensure("):
            assert banned not in src, f"{path} references writer {banned}"


def test_read_only_connection_refuses_writes_and_creates_nothing(desk_out, tmp_path):
    from private_desk.db import SourceUnavailable, open_ro
    db = os.path.join(desk_out, "data", "radar_candidate_history.db")
    before = set(os.listdir(os.path.dirname(db)))
    conn = open_ro(db)
    with pytest.raises(sqlite3.Error):
        conn.execute("DELETE FROM radar_candidate_history")
    conn.close()
    assert set(os.listdir(os.path.dirname(db))) == before            # no -wal / -shm created
    missing = str(tmp_path / "nope" / "x.db")
    with pytest.raises(SourceUnavailable):
        open_ro(missing)
    assert not os.path.exists(os.path.dirname(missing))


# ------------------------------------------------------------------ 2-4. pages render real content
def test_dashboard_loads_from_stored_data(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "PRIVATE / LOCAL ONLY" in r.text and SESSION.isoformat() in r.text
    assert "What changed today" in r.text and "Sector pulse" in r.text
    text = r.text.replace("BULLISH/BEARISH", "")         # only the "no regime label" note
    assert "BULLISH" not in text and "BEARISH" not in text


def test_every_page_renders(client):
    for url in ALL_PAGES:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "failed safely" not in r.text, url


def test_radar_candidates_display(client, svc):
    stored = {c.instrument for c in svc.repo.candidates(SESSION)}
    assert {"SYMA", "SYMB"} <= stored                     # the detectors flagged them
    html = client.get("/radar").text
    for sym in stored:
        assert f"/stock/{sym}?session={SESSION}" in html
    assert "SELECTED" in html                              # SYMA's editorial selection
    csv = client.get("/radar.csv").text
    assert csv.splitlines()[0].startswith("session_date,symbol")
    assert "SYMA" in csv and "not a trade recommendation" in csv


def test_named_stock_private_intelligence_displays(client):
    html = client.get("/stock/SYMA").text
    for part in ("Synthetic A Ltd.", "Why this stock?", "Structure", "Volume intelligence",
                 "Relative performance", "Sector context", "Radar history", "Official events"):
        assert part in html, part
    assert "IN BAN PERIOD" in client.get("/stock/SYMB").text   # official F&O ban list


# ------------------------------------------------------------------ 5. WHY maps to detector facts
def test_why_this_stock_maps_to_actual_detector_facts(svc):
    _, rows = _series()
    views = {v["symbol"]: v for v in svc.radar(SESSION)["views"]}
    a = views["SYMA"]
    assert a["replay_status"] == "MATCHED"
    assert [i["code"] for i in a["why"]] == a["reason_codes"]      # one item per recorded code
    assert all(i["verified"] for i in a["why"])
    syma = rows["SYMA"]
    prior20_high = max(r[2] for r in syma[-21:-1])
    vol = syma[-1][5]
    prior20_mean = sum(r[5] for r in syma[-21:-1]) / 20
    by_code = {i["code"]: i for i in a["why"]}
    brk = by_code["STRUCTURE_BREAK_ABOVE_20D_RANGE"]["values"]
    assert brk["prior_high"] == pytest.approx(prior20_high)
    assert brk["close"] == pytest.approx(syma[-1][4])
    vol_item = next(i for i in a["why"] if i["family"] == "VOLUME")
    assert vol_item["values"]["relative_volume"] == pytest.approx(vol / prior20_mean)
    assert by_code["RELATIVE_PERSISTENT_POSITIVE"]["values"]["vs_nifty_20d_pp"] > 1.5
    assert "rule" in vol_item and "RVOL" in vol_item["rule"]


def test_unreconciled_candidate_shows_no_values(svc, desk_out):
    """A recorded candidate the replay cannot reproduce keeps its codes but shows no numbers."""
    from private_desk import replay as rp
    from storage.candidate_history_models import StoredCandidateState
    from private_desk.services.candidates import why_items
    fake = StoredCandidateState(SESSION, "SYME", ("VOLUME",), ("VOLUME_UNUSUAL",), 2, None,
                                "NOTABLE", None, 0.1, "1.0")
    assert rp.reconcile({"candidates": {}}, [fake]) == {"SYME": rp.NOT_REPLAYED}
    items = why_items(fake, rp.NOT_REPLAYED, None, None)
    assert items[0]["values"] == {} and not items[0]["verified"]
    assert "unavailable" in items[0]["text"]


# ------------------------------------------------------------------ 6-8. chart / RVOL / structure
def test_stock_chart_uses_aligned_session_data(client, svc):
    from private_desk.services.candidates import session_spine
    spine = set(session_spine(svc.repo, SESSION))
    c = client.get("/api/chart/SYMC").json()
    dates = {dt.date.fromisoformat(d) for d in c["dates"]}
    assert PLACEHOLDER_HOLIDAY not in dates                 # holiday placeholder filtered out
    assert dates <= spine and max(dates) == SESSION
    assert 0 not in c["volume"]


def test_relative_volume_displayed_correctly(client):
    _, rows = _series()
    syma = rows["SYMA"]
    rvol = syma[-1][5] / (sum(r[5] for r in syma[-21:-1]) / 20)
    html = client.get("/stock/SYMA").text
    assert f"{rvol:.2f}x" in html
    radar = client.get("/radar").text
    assert f"{rvol:.2f}x" in radar


def test_structure_data_displayed_correctly(client):
    from config import fmt_in
    _, rows = _series()
    syma = rows["SYMA"]
    html = client.get("/stock/SYMA").text
    assert fmt_in(max(r[2] for r in syma[-21:-1]), 2) in html      # prior 20-session high
    assert fmt_in(min(r[3] for r in syma[-21:-1]), 2) in html      # prior 20-session low
    assert "BREAK ABOVE 20D RANGE" in html


# ------------------------------------------------------------------ 9-10. sectors / history
def test_sector_page_reconciles_counts(svc, client):
    d = svc.dashboard(SESSION)
    ms = d["ms"]
    assert ms["status"] == "OK"
    for key in ("ADVANCES", "DECLINES", "UNUSUAL_VOLUME", "RANGE_UP", "RANGE_DOWN"):
        col = {"ADVANCES": "advances", "DECLINES": "declines", "UNUSUAL_VOLUME": "unusual_volume",
               "RANGE_UP": "range_up", "RANGE_DOWN": "range_down"}[key]
        assert sum(s[col] for s in d["sectors"]) == ms["metrics"][key]["numerator"]
    assert sum(s["constituents"] for s in d["sectors"]) == len(CONSTITUENTS)
    assert sum(s["radar_candidates"] for s in d["sectors"]) == len(d["views"])
    html = client.get("/sectors").text
    assert "RECONCILES WITH" in html and "MISMATCH" not in html


def test_radar_history_works(svc, client):
    from private_desk.services.history import HistoryFilter
    h = svc.history(HistoryFilter(symbol="SYMA"))
    assert h["rows"] and all(r["symbol"] == "SYMA" for r in h["rows"])
    assert h["summary"][0]["count"] == len(h["rows"])
    hv = svc.history(HistoryFilter(family="VOLUME"))
    assert all("VOLUME" in r["families"] for r in hv["rows"])
    assert client.get("/history?symbol=SYMA").status_code == 200


def test_appearance_rule():
    from private_desk.services.candidates import appearance
    spine = [dt.date(2026, 9, d) for d in (14, 15, 16, 17, 18)]
    assert appearance(spine, [spine[4]], spine[4])["label"] == "FIRST_RECORDED"
    assert appearance(spine, [spine[3], spine[4]], spine[4])["label"] == "CONSECUTIVE"
    a = appearance(spine, [spine[0], spine[4]], spine[4])
    assert a["label"] == "REAPPEARED" and a["sessions_since"] == 4 and a["count"] == 2


def test_desk_novelty_matches_the_radar_classifier(svc):
    """Novelty comes from radar.novelty over recorded history - the same answer the pipeline
    would give (FORTIS/TIINDIA/... matched the editorial records on the real 30 Sep data)."""
    from radar.daily_pipeline import _reconstruct_candidate
    from radar.novelty import classify_history
    from private_desk.services.candidates import session_spine
    spine = session_spine(svc.repo, SESSION)
    prior = [d for d in spine if d < SESSION][-5:]
    sessions = [(d, [_reconstruct_candidate(c) for c in svc.repo.candidates(d)]) for d in prior]
    sessions.append((SESSION, [_reconstruct_candidate(c) for c in svc.repo.candidates(SESSION)]))
    expected = {n.instrument: n.novelty_type.value for n in classify_history(sessions)[SESSION]}
    got = {v["symbol"]: v["novelty_type"] for v in svc.radar(SESSION)["views"]}
    assert got == expected


# ------------------------------------------------------------------ 11-12. unavailable / stale
def test_missing_data_shows_unavailable_not_zero(client, desk_out):
    from private_desk.app import NA, f_num, f_pct
    assert f_num(None) == NA and f_pct(float("nan")) == NA
    assert f_num(0.0) == "0.00"                            # a real zero stays a zero
    html = client.get("/stock/SYMD").text                  # no bar on the session
    assert "UNAVAILABLE" in html and "No detector reading for SYMD" in html
    os.remove(os.path.join(desk_out, "reports", f"premarket_{EDITION}.json"))
    dash = client.get("/").text
    assert "canonical report ARTIFACT_MISSING" in dash
    m = re.search(r"NIFTY 50</div><div class=\"v\">(.*?)</div>", dash)
    assert m and "UNAVAILABLE" in m.group(1)


def test_stale_sessions_are_flagged(svc, monkeypatch, client):
    import config
    from private_desk.services.freshness import STALE, compute_freshness
    later = dt.datetime(2026, 9, 25, 20, 0, tzinfo=IST)
    f = compute_freshness(svc.repo, later)
    assert f.latest_completed == dt.date(2026, 9, 25)
    assert f.by_key["radar"].status == STALE and not f.ok
    monkeypatch.setattr(config, "now_ist", lambda: later)
    html = client.get("/").text
    assert "STALE" in html and "ATTENTION" in html


def test_unknown_session_is_refused_not_substituted(client):
    html = client.get("/?session=2026-09-01").text
    assert "No completed Radar run is recorded for 2026-09-01" in html
    assert "DATA UNAVAILABLE" in html


# ------------------------------------------------------------------ 13-15. local / no broker / no upload
def test_localhost_binding_is_default_and_remote_is_refused():
    from private_desk.app import main
    from private_desk.settings import (DEFAULT_HOST, DEFAULT_PORT, DeskSettings,
                                       RemoteBindRefused, require_loopback)
    assert DEFAULT_HOST == "127.0.0.1" and DEFAULT_PORT == 8765
    assert DeskSettings.from_out_dir(ROOT).host == "127.0.0.1"
    for bad in ("0.0.0.0", "192.168.1.5", "::", "example.com"):
        with pytest.raises(RemoteBindRefused):
            require_loopback(bad)
    assert main(["--host", "0.0.0.0"]) == 2


def test_no_broker_network_or_upload_dependencies():
    banned_modules = ("kiteconnect", "kite", "upload", "googleapiclient", "google_auth_oauthlib",
                      "requests", "yfinance", "providers", "news", "smtplib", "urllib.request",
                      "http.client", "socket", "subprocess", "official_snapshots.service",
                      "official_snapshots.sources", "radar.daily_pipeline.run_daily_radar")
    for path in _desk_sources():
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                for b in banned_modules:
                    assert n != b and not n.startswith(b + "."), f"{path} imports {n}"
        src = open(path, encoding="utf-8").read().lower()
        for word in ("place_order", "modify_order", "cancel_order", "access_token", "api_key"):
            assert word not in src, f"{path}: {word}"


def test_every_route_is_read_only(client):
    for route in client.app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            assert methods <= {"GET", "HEAD"}, route.path
    assert client.post("/").status_code == 405


def test_rendered_pages_carry_no_recommendation_language(client):
    for url in ALL_PAGES:
        r = client.get(url)
        if url.startswith("/api") or url == "/healthz":
            continue
        assert not FORBIDDEN_WORDS.search(re.sub(r"<[^>]+>", " ", r.text)), url


# ------------------------------------------------------------------ 16. public gates unchanged
def test_dmb_critical_path_never_imports_the_desk():
    paths = [os.path.join(ROOT, f) for f in ("main.py", "upload.py", "render_daily_market_byte.py")]
    for pkg in ("products", "publication", "presentation", "daily_video", "operations", "radar",
                "editorial", "hooks", "market_structure", "official_snapshots", "storage"):
        for dirpath, _, files in os.walk(os.path.join(ROOT, pkg)):
            paths += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]
    for p in paths:
        assert "private_desk" not in open(p, encoding="utf-8").read(), p
    for bat in os.listdir(os.path.join(ROOT, "scripts")):
        if bat.endswith(".bat") and "private_desk" not in bat:
            assert "private_desk" not in open(os.path.join(ROOT, "scripts", bat)).read(), bat


def test_public_gate_still_blocks_named_stock_technicals_with_the_desk_loaded(client):
    from test_public_publication import _post
    public = _post()
    assert not [s for s in public.scenes if s.kind in ("RADAR_STORY", "MOVERS")]
    assert public.publication["publication_profile"] == "PUBLIC_UNREGISTERED"
    assert public.publication["block_reasons"].get("SECURITY_TECHNICAL_ANALYSIS")


def test_desk_cache_cannot_write_into_production_paths(desk_out, tmp_path):
    from private_desk.cache import CacheWriteRefused, DeskCache
    with pytest.raises(CacheWriteRefused):
        DeskCache(os.path.join(desk_out, "data"), desk_out)
    c = DeskCache(os.path.join(desk_out, "private_desk", "cache"), desk_out)
    with pytest.raises(CacheWriteRefused):
        c._path(os.path.join("..", "..", "data", "x.json"))


# ------------------------------------------------------------------ 17. candidate packet
def test_candidate_packet_contains_no_order_fields(svc):
    from private_desk.packet import FORBIDDEN_FIELD_TOKENS, SCHEMA_VERSION, PrivateCandidatePacket
    packets = svc.packets(SESSION)
    assert packets and all(p.schema_version == SCHEMA_VERSION for p in packets)

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from keys(v)
    names = list(PrivateCandidatePacket.__dataclass_fields__)
    for p in packets:
        names += list(keys(p.to_dict()))
    for name in names:
        tokens = set(str(name).lower().split("_"))
        assert not tokens & set(FORBIDDEN_FIELD_TOKENS), name
    json.dumps([p.to_dict() for p in packets], default=str)          # serialisable contract


# ------------------------------------------------------------------ 18. nothing modified
def test_no_source_file_modified_by_desk_startup_or_use(desk_out, client):
    before = _fingerprint(desk_out)
    for url in ALL_PAGES + ["/?session=" + SESSION.isoformat(), "/stock/SYMA?session=2026-09-17"]:
        client.get(url)
    from private_desk.check import run
    run(client.app.state.service.settings)
    assert _fingerprint(desk_out) == before


def test_real_local_stores_untouched_by_the_desk(tmp_path):
    out = os.path.join(ROOT, "output")
    dbs = [os.path.join(out, "data", f) for f in ("market_ohlcv.db", "radar_candidate_history.db",
                                                  "editorial_selections.db", "market_history.db")]
    if not all(os.path.isfile(p) for p in dbs):
        pytest.skip("no local production stores on this machine")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from private_desk.app import create_app
    from private_desk.settings import DeskSettings

    def snap():
        state = {}
        for p in dbs:
            with open(p, "rb") as fh:
                state[p] = (hashlib.sha256(fh.read()).hexdigest(), os.path.getmtime(p),
                            os.path.exists(p + "-wal"), os.path.exists(p + "-shm"))
        return state
    before = snap()
    app = create_app(DeskSettings.from_out_dir(out, cache_dir=str(tmp_path / "cache")))
    c = TestClient(app)
    for url in ("/", "/radar", "/sectors", "/history", "/quality"):
        assert c.get(url).status_code == 200
    assert snap() == before


# ------------------------------------------------------------------ Phase 1 final UX: attention set
def _view(sym, *, novelty="CONTINUATION", families=("STRUCTURE", "RELATIVE_PERFORMANCE"),
          selected=False, appearance="CONSECUTIVE", since=1, attention="NOTABLE"):
    return {"symbol": sym, "novelty_type": novelty, "families": list(families),
            "signal_count": len(families), "selected": selected, "appearance": appearance,
            "sessions_since_prior": since, "attention_level": attention}


def _synthetic_views():
    """25 candidates in existing Radar order (HIGH_INTEREST first, then symbol)."""
    vs = [_view("HI1", novelty="CONTINUATION", families=("VOLUME", "STRUCTURE", "RELATIVE_PERFORMANCE"),
                attention="HIGH_INTEREST")]
    vs += [_view(f"C{i:02d}") for i in range(10)]                             # unchanged
    vs += [_view(f"N{i:02d}", novelty="NEW_CANDIDATE", appearance="REAPPEARED", since=i + 6)
           for i in range(8)]
    vs += [_view("NF1", novelty="NEW_CANDIDATE", appearance="FIRST_RECORDED", since=None)]
    vs += [_view(f"S{i}", novelty="MULTIPLE_CHANGES") for i in range(3)]       # state changes
    vs += [_view("SEL", novelty="CONTINUATION", selected=True)]
    vs += [_view("VS1", novelty="NEW_EVIDENCE_FAMILY", families=("VOLUME", "STRUCTURE"))]
    return vs


def test_attention_set_is_capped_deterministic_and_follows_the_documented_tiers():
    from private_desk.services import attention as at
    views = _synthetic_views()
    a1 = at.attention_set(views, story_order=["SEL"])
    a2 = at.attention_set(list(views), story_order=["SEL"])
    syms = [a["view"]["symbol"] for a in a1]
    assert syms == [a["view"]["symbol"] for a in a2]                       # deterministic
    assert len(syms) == at.ATTENTION_MAX < len(views)                     # not the whole Radar
    # tier order: story -> 3 families -> volume+structure -> NEW (first recorded, then longest gap)
    assert syms[:4] == ["SEL", "HI1", "VS1", "NF1"]
    assert syms[4:] == ["N07", "N06", "N05", "N04", "N03", "N02"]         # longest absence first
    assert not any(s.startswith("C") for s in syms)                      # unchanged never fill it
    labels = {a["view"]["symbol"]: a["labels"] for a in a1}
    assert labels["SEL"][0] == "STORY SELECTED" and "3 EVIDENCE FAMILIES" in labels["HI1"]
    assert "VOLUME + STRUCTURE" in labels["VS1"] and labels["NF1"] == ["NEW · FIRST RECORDED"]


def test_quiet_day_fills_with_state_changes_and_ties_keep_radar_order():
    from private_desk.services import attention as at
    views = [_view("B1", novelty="MULTIPLE_CHANGES"), _view("A1", novelty="DIRECTION_TRANSITION"),
             _view("C1")] + [_view(f"Z{i}", novelty="PERSISTENCE_TRANSITION") for i in range(9)]
    syms = [a["view"]["symbol"] for a in at.attention_set(views, [])]
    assert len(syms) == at.ATTENTION_MIN                                   # filled to the minimum
    assert syms[:2] == ["B1", "A1"]                                        # existing order kept
    assert "C1" not in syms


def test_radar_default_order_puts_new_then_changed_then_unchanged():
    from private_desk.services import attention as at
    views = [_view("A", novelty="CONTINUATION"), _view("B", novelty="NEW_CANDIDATE"),
             _view("C", novelty="MULTIPLE_CHANGES"), _view("D", novelty="NEW_CANDIDATE")]
    assert [v["symbol"] for v in at.radar_default_order(views)] == ["B", "D", "C", "A"]


def test_no_new_score_is_introduced():
    """The attention workflow orders by named tiers and existing facts - no computed score."""
    for path in list(_desk_sources()):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, (ast.FunctionDef, ast.arg)):
                names = [getattr(node, "name", None) or getattr(node, "arg", "")]
            elif isinstance(node, ast.Dict):
                names = [k.value for k in node.keys if isinstance(k, ast.Constant)
                         and isinstance(k.value, str)]
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                names = [str(node.slice.value)]
            for n in names:
                assert "score" not in str(n).lower(), f"{path}: {n}"
    for dirpath, _, files in os.walk(os.path.join(DESK, "templates")):
        for f in files:
            src = open(os.path.join(dirpath, f), encoding="utf-8").read()
            assert not re.search(r"\w+_score|score\s*[|}]", src), f


def test_dashboard_shows_attention_subset_counts_and_view_all(client, monkeypatch, svc):
    from private_desk.services import attention as at
    monkeypatch.setattr(at, "ATTENTION_MAX", 1)
    monkeypatch.setattr(at, "ATTENTION_MIN", 1)
    total = len(svc.repo.candidates(SESSION))
    assert total >= 2
    html = client.get("/").text
    assert f"1 attention candidates from {total} Radar candidates" in html
    assert f'href="/radar?session={SESSION}" class="viewall">View all {total} in Radar' in html
    table = html.split('id="attention"')[1].split("</table>")[0]
    assert table.count("<tr class=") == 1                                 # only the subset
    radar = client.get(f"/radar?session={SESSION}").text
    for c in svc.repo.candidates(SESSION):                                # nothing disappears
        assert f"/stock/{c.instrument}?session={SESSION}" in radar


def test_what_changed_summary_reconciles_and_items_link_to_stock_pages(svc, client):
    d = svc.dashboard(SESSION)
    summary = d["change_summary"]
    views = d["views"]
    assert summary["new"] + summary["changed"] + summary["unchanged"] == summary["total"] == len(views)
    counts = {r["key"]: r["count"] for r in summary["rows"]}
    assert counts["NEW"] == sum(v["novelty_type"] == "NEW_CANDIDATE" for v in views)
    assert counts["REAPPEARED"] == sum(v["appearance"] == "REAPPEARED" for v in views)
    assert counts["LOST"] == len(d["changes"]["NO_LONGER_CANDIDATE"])
    html = client.get("/").text
    for item in d["change_items"]:
        assert item["view"]["novelty_type"] != "CONTINUATION"
        assert f'class="citem" href="/stock/{item["symbol"]}?session={SESSION}"' in html
    assert len(d["change_items"]) <= 8
    assert "#filter=NEW" in html                                          # summary -> Radar filter


def test_partial_evidence_is_marked_not_verified(client, monkeypatch):
    from private_desk import replay as rp
    from private_desk.services import candidates as cands
    real = rp.reconcile
    monkeypatch.setattr(cands.rp, "reconcile",
                        lambda replay, stored: {k: rp.NOT_REPLAYED for k in real(replay, stored)})
    html = client.get("/stock/SYMA").text
    assert "VALUES UNAVAILABLE" in html and "EVIDENCE VERIFIED" not in html
    assert "not reproduced by the detector replay" in html
    radar = client.get("/radar").text
    assert "values unavailable" in radar and ">VERIFIED<" not in radar


def test_radar_filters_and_sorting_markup_intact(client):
    html = client.get("/radar").text
    assert 'class="sortable" id="radar-full"' in html and 'class="sort' in html
    for tok in ("NEW", "CHANGED", "REAPPEARED", "VOLUME", "RANGE_UP", "NEW_STRUCTURE", "SELECTED"):
        assert f'data-tok="{tok}"' in html
    assert 'data-tokens="' in html
    js = client.get("/static/desk.js").text
    assert "filter=" in js and "sortable" in js
