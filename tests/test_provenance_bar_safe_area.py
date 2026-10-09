"""The provenance-bar safe-area defect PK-D's real 2026-10-08 PRE reconstruction exposed:
`draw_provenance` measured each full "ROLE: value [ · ROLE: value ...]" line with `fit()`, which
only shrinks a font down to `theme.MIN_FONT` and never wraps - so a line too wide even at the
floor (PRE's overnight-cue "DATA AS OF ... · FETCHED ..." combination, which happens on every
reconstruction of a past morning, not just this one date) silently overflowed past the Shorts
action rail. The fix (`_rows`/`_layout`) splits such a line across rows at its existing " · "
segment boundaries - deterministic, content-preserving wrapping, never a smaller font floor and
never a special case for 2026-10-08.

Covers: a direct unit check of the wrapping logic, the literal failing string from the real run,
and full-scene freeze-frame QA worst cases (long source, long timestamp, four cues, normal case).
"""
from __future__ import annotations

import datetime as dt

import pytest

from daily_video import Composer, theme
from daily_video.provenance_bar import PAD, SIZE, X0, X1, _layout, _rows
from daily_video.storyboard import SceneSpec, Storyboard
from daily_video.typography import font, tlen
from presentation.provenance_label import ProvenanceLabel

WIDTH = X1 - X0 - 2 * PAD

# the literal string from the real 2026-10-08 PRE reconstruction's freeze-frame QA failure
REAL_FAILURE_AS_OF = ("DATA AS OF: US CLOSE 7 OCT 2026 · 7:45 AM IST · "
                      "FETCHED: 9 OCT 2026 9:30 AM IST")


# --------------------------------------------------------------------------- 1. unit: wrapping
def test_a_line_that_fits_is_never_split():
    f = font(SIZE, True)
    short = "SOURCE: NSE"
    assert _layout([short], WIDTH) == [(short, f)]


def test_the_real_failing_line_no_longer_overflows_at_any_row():
    f = font(theme.MIN_FONT, True)
    rows = _rows(REAL_FAILURE_AS_OF, f, WIDTH)
    assert len(rows) >= 2                              # it needed more than one row
    assert rows[0] == "DATA AS OF: US CLOSE 7 OCT 2026 · 7:45 AM IST"
    assert rows[-1].startswith("FETCHED:")
    for row in rows:
        assert tlen(row, f) <= WIDTH, row


def test_rows_never_split_inside_a_role_value_segment():
    """Every row produced is itself a ' · '-join of whole, untouched original segments - no
    word is ever cut, no segment is ever dropped or truncated."""
    f = font(theme.MIN_FONT, True)
    rows = _rows(REAL_FAILURE_AS_OF, f, WIDTH)
    reassembled = " · ".join(rows)
    assert reassembled == REAL_FAILURE_AS_OF


def test_layout_picks_min_font_only_when_the_full_line_does_not_fit_at_normal_size():
    rows = _layout([REAL_FAILURE_AS_OF], WIDTH)
    assert all(f.size == theme.MIN_FONT for _, f in rows)
    assert len(rows) >= 2


@pytest.mark.parametrize("as_of", [
    "DATA AS OF: 25 SEP 2026 · 3:30 PM IST",                      # normal short case (unchanged)
    REAL_FAILURE_AS_OF,                                           # the real 2026-10-08 case
    "DATA AS OF: US CLOSE 6 OCT 2026 · 1:30 AM IST · FETCHED: 6 OCT 2026 7:12 AM IST",  # fetched same-format, different date
])
def test_layout_always_fits_every_row_within_the_available_width(as_of):
    for row, f in _layout([as_of], WIDTH):
        assert tlen(row, f) <= WIDTH, (row, f.size)


def test_a_long_multi_role_source_line_also_wraps_safely():
    """`ProvenanceLabel.source_line()` can itself be a ' · '-join of several "ROLE: SOURCE"
    pairs (ProvenanceLabel's `roles` field, as `ipo_watch.watch._provenance` and
    `presentation.legacy_public` actually build it) - the same overflow risk as the as_of line,
    on the FIRST line this time, using the longest role/label text this codebase currently
    produces (`publication.classify.SOURCE_LABELS`). The general fix covers it too."""
    label = ProvenanceLabel(source="", data_as_of="25 SEP 2026 · 3:30 PM IST", roles=(
        ("ISSUE DATA", "NSE"), ("OFFER DOCUMENT", "SEBI offer document"),
        ("HISTORY", "Daily Market Byte calculation")))
    source_line = label.source_line()
    for row, f in _layout([source_line], WIDTH):
        assert tlen(row, f) <= WIDTH, (row, f.size)


# --------------------------------------------------------------------------- 2. scene-level QA
def _overnight_scene(as_of: str, n_cues: int = 2, duration: float = 7.8) -> SceneSpec:
    names = ["S&P 500", "NASDAQ", "DOW JONES", "NIKKEI 225", "HANG SENG"][:n_cues]
    cues_tx = [{"when": f"AT 7:45 AM IST", "name": n, "value": f"{'+' if i % 2 else '-'}0.{i}2%"}
              for i, n in enumerate(names)]
    cues_data = [{"positive": i % 2 == 0, "numeric": 0.12 * (i + 1), "role": "cue"}
                for i in range(n_cues)]
    return SceneSpec(
        kind="PRE_OVERNIGHT", section="OVERNIGHT", duration=duration,
        headline="What changed overnight", takeaway="A mixed night across global markets.",
        texts={"headline": "What changed overnight", "takeaway": "A mixed night across global markets.",
              "cues": cues_tx, "provenance": {"source": "SOURCE: YAHOO FINANCE", "as_of": as_of}},
        data={"cues": cues_data, "gift": None},
        freeze={"t": round(duration - 0.6, 2), "what": "overnight cues", "where": "cue cards",
               "why": "what changed overnight", "mode": "PRE_OVERNIGHT"})


def _freeze(spec: SceneSpec):
    sb = Storyboard(session_date=dt.date(2026, 10, 9), date_label="FRI 09 OCT 2026",
                    kicker="BEFORE THE BELL", scenes=[spec])
    return Composer(sb).freeze(0)


def test_normal_short_provenance_passes_qa_unchanged():
    _, rec, qa = _freeze(_overnight_scene("DATA AS OF: 25 SEP 2026 · 3:30 PM IST", n_cues=2))
    assert qa["passed"], qa["issues"]


def test_the_real_2026_10_08_failure_string_now_passes_qa():
    _, rec, qa = _freeze(_overnight_scene(REAL_FAILURE_AS_OF, n_cues=2))
    assert qa["passed"], qa["issues"]
    prov = [tb for tb in rec.texts if tb.role == "provenance"]
    assert prov
    for tb in prov:
        assert tb.box[2] <= theme.RIGHT_RAIL_X, tb.box
        assert tb.box[0] >= 30 and tb.box[1] >= 40
        assert tb.box[3] <= theme.RESERVED_BOTTOM


def test_four_global_cues_with_the_real_failure_string_still_passes_qa():
    _, rec, qa = _freeze(_overnight_scene(REAL_FAILURE_AS_OF, n_cues=4))
    assert qa["passed"], qa["issues"]


def test_long_source_plus_long_timestamp_combination_passes_qa():
    long_as_of = ("DATA AS OF: US CLOSE 31 DEC 2026 · 11:59 PM IST · "
                 "FETCHED: 31 DEC 2026 11:59 PM IST")
    spec = _overnight_scene(long_as_of, n_cues=3)
    spec.texts["provenance"]["source"] = ("PRICES: YAHOO FINANCE END OF DAY · "
                                          "UNIVERSE & SECTORS: NATIONAL STOCK EXCHANGE OF INDIA")
    _, rec, qa = _freeze(spec)
    assert qa["passed"], qa["issues"]


def test_provenance_text_is_never_hidden_or_truncated_by_the_fix():
    """The fix must redistribute text across rows, never drop content: every original segment
    of the as_of line is still present, verbatim, somewhere in the rendered frame."""
    _, rec, qa = _freeze(_overnight_scene(REAL_FAILURE_AS_OF, n_cues=2))
    rendered = " ".join(tb.text for tb in rec.texts if tb.role == "provenance")
    for fragment in ("US CLOSE 7 OCT 2026", "7:45 AM", "9 OCT 2026", "9:30 AM"):
        assert fragment in rendered, (fragment, rendered)
