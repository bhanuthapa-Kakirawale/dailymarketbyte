"""Private intelligence preservation: the public/compliance work only restricts what reaches
PUBLIC output - the private Radar, its named-stock analysis and its history are intact.
Fully offline (synthetic inputs; real local stores are only opened read-only)."""
import os
import sqlite3

import pytest

from test_public_publication import _post

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_private_profile_keeps_named_stock_radar_the_public_one_suppresses_it():
    private = _post(profile="PRIVATE_ANALYTICS")
    public = _post()                                     # same inputs, default profile
    stories = [s for s in private.scenes if s.kind == "RADAR_STORY"]
    assert stories and all(s.texts.get("symbol") for s in stories)
    assert {s.texts["symbol"] for s in stories} <= {"STOCK-001", "STOCK-002"}
    assert "MOVERS" in [s.kind for s in private.scenes]
    assert not [s for s in public.scenes if s.kind in ("RADAR_STORY", "RADAR_INTRO", "MOVERS")]
    text = " ".join(public.public_text().values())
    assert "STOCK-001" not in text and "STOCK-002" not in text
    # suppression happens at the publication gate - recorded, not deleted
    assert public.publication["publication_profile"] == "PUBLIC_UNREGISTERED"
    reasons = public.publication["block_reasons"]
    assert reasons.get("SECURITY_TECHNICAL_ANALYSIS") and reasons.get("SECURITY_WITHOUT_OFFICIAL_EVENT")


def test_private_analytics_is_never_uploadable():
    from publication import PublicationProfile
    from publication.audit import require_publication_pass
    sb = _post(profile="PRIVATE_ANALYTICS")
    from daily_video.public_storyboard import audit_storyboard
    audit = audit_storyboard(sb, "POST_UNIFIED")
    assert audit["final"] == "BLOCK" and sb.publication_profile == PublicationProfile.PRIVATE_ANALYTICS.value
    with pytest.raises(Exception):
        require_publication_pass(audit, "x.mp4")


def test_radar_engine_modules_are_intact():
    from radar import (composite, daily_pipeline, editorial_selector, novelty, relative,
                       technical, volume)
    assert callable(daily_pipeline.run_daily_radar)
    assert callable(composite.build_candidates)
    for mod in (volume, technical, relative, novelty, editorial_selector):
        assert mod.__file__ and os.path.exists(mod.__file__)


def test_private_history_schemas_still_exist(tmp_path):
    from storage.candidate_history_repository import CandidateHistoryStore
    from storage.editorial_repository import EditorialStore
    from storage.ohlcv_repository import OHLCVStore
    expected = {CandidateHistoryStore: {"radar_candidate_history", "candidate_history_runs"},
                EditorialStore: {"editorial_selections", "radar_publications"},
                OHLCVStore: {"daily_ohlcv"}}
    for cls, tables in expected.items():
        path = str(tmp_path / f"{cls.__name__}.db")
        store = cls(path)
        store.close()
        con = sqlite3.connect(path)
        names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        con.close()
        assert tables <= names, (cls.__name__, names)


LOCAL = os.path.join(ROOT, "output", "data")


@pytest.mark.skipif(not os.path.exists(os.path.join(LOCAL, "radar_candidate_history.db")),
                    reason="local private history is not part of the repository")
def test_local_private_history_is_present_and_readable():
    counts = {}
    for db, table in (("radar_candidate_history.db", "radar_candidate_history"),
                      ("market_ohlcv.db", "daily_ohlcv"),
                      ("editorial_selections.db", "editorial_selections")):
        from storage.readonly import connect_readonly
        con = connect_readonly(os.path.join(LOCAL, db))
        counts[table] = con.execute(f"select count(*) from {table}").fetchone()[0]
        con.close()
    assert all(n > 0 for n in counts.values()), counts
