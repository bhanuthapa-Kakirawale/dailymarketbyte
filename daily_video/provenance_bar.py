"""The provenance bar: SOURCE / DATA AS OF (/ FETCHED) on every factual scene.

One component, one position, one type size for the whole video - so a viewer learns where to
look. It sits in a reserved band above the on-screen disclaimer, left of the Shorts action rail,
on a dark plate, in bold 26 px: visually secondary to the scene's fact, never microscopic. It is
drawn onto the scene's own layer, so it arrives and leaves with its scene (no flashing) and is
visible for the scene's whole duration. Plain text attribution only - no exchange logo.

A scene opts in by declaring `texts["provenance"] = {"source": ..., "as_of": ...}` (declared, so
the content scan and the publication audit see exactly what is drawn).

A logical line (e.g. an "as_of" carrying both a DATA AS OF and a FETCHED timestamp - PRE's
overnight cues are the one case that combines both today) can be too wide for the band even at
the font floor. `fit()` only shrinks a font, it never wraps, so that case used to overflow past
the Shorts action rail undetected. `_rows()` below splits such a line across as many rows as it
needs, breaking only at its existing " · " segment boundaries (never inside a "ROLE: value"
pair, never truncating or dropping a segment) - deterministic, content-preserving wrapping, not
a smaller font and not a special case for any one date/source.
"""
from __future__ import annotations

from .chartkit import HiRes
from .scenes import alpha
from . import theme
from .typography import fit, font, tlen

BAND_TOP = 1382              # the plate's top edge; the disclaimer sits at theme.FOOTER_Y (1484)
BAND_BOTTOM = 1462
X0 = theme.X0
X1 = theme.RIGHT_RAIL_X - 10  # never under the Shorts action rail
SIZE = theme.T_LABEL          # 26 px bold - above the 24 px minimum, below body text
PAD = 16


def _rows(s: str, f, maxw: float) -> list:
    """`s`'s " · "-joined segments, packed into as few rows as fit `maxw` at font `f`. A
    single segment is never split - if it alone exceeds `maxw` it still gets its own row,
    which `draw_provenance` reports as a FONT_FIT note rather than silently drawing past
    bounds."""
    segments = s.split(" · ")
    rows, cur = [], []
    for seg in segments:
        trial = " · ".join(cur + [seg])
        if cur and tlen(trial, f) > maxw:
            rows.append(" · ".join(cur))
            cur = [seg]
        else:
            cur.append(seg)
    if cur:
        rows.append(" · ".join(cur))
    return rows


def _layout(lines: list, width: float) -> list:
    """[(row_text, font), ...] across every logical line, each row guaranteed to fit `width`
    at its font unless even a single bare segment cannot (reported by the caller, never hidden)."""
    out = []
    for s in lines:
        f = fit(s, width, SIZE, bold=True, min_size=theme.MIN_FONT)
        if tlen(s, f) <= width:
            out.append((s, f))
        else:
            out.extend((row, f) for row in _rows(s, f, width))
    return out


def draw_provenance(ctx, prov: dict, p: float = 1.0) -> None:
    """`prov` = {"source": "SOURCE: NSE", "as_of": "DATA AS OF: 25 SEP 2026 · 3:30 PM IST"}."""
    lines = [s for s in (prov.get("source"), prov.get("as_of")) if s]
    if not lines or p <= 0:
        return
    width = X1 - X0 - 2 * PAD
    rows = _layout(lines, width)
    line_h = [int(f.size * 1.25) for _, f in rows]
    h = sum(line_h) + 2 * PAD - 6
    top = BAND_BOTTOM - h
    hr = HiRes((X0, top, X1, BAND_BOTTOM))
    hr.rect((X0, top, X1, BAND_BOTTOM), fill=alpha(theme.PANEL, 0.88 * p), radius=14,
            outline=alpha(theme.PANEL_BORDER, 0.9 * p), width=1.4)
    hr.rect((X0, top + 12, X0 + 5, BAND_BOTTOM - 12), fill=alpha(theme.BRAND, p), radius=2.5)
    hr.composite(ctx.layer)
    y = top + PAD - 3
    for s, f in rows:
        x = X0 + PAD + 8
        # every "ROLE:" label (SOURCE:, PRICES:, DATA AS OF:, FETCHED: ...) in the secondary
        # colour, its value in the primary one - one run per " · " segment
        for i, seg in enumerate(s.split(" · ")):
            if i:
                ctx.ink.text(ctx.d, (x, y), " · ", f, alpha(theme.TEXT_SECONDARY, p), "provenance")
                x += tlen(" · ", f)
            head, sep, rest = seg.partition(": ")
            if sep:
                ctx.ink.text(ctx.d, (x, y), head + ":", f, alpha(theme.TEXT_SECONDARY, p),
                             "provenance")
                x += tlen(head + ": ", f)
                seg = rest
            ctx.ink.text(ctx.d, (x, y), seg, f, alpha(theme.TEXT_PRIMARY, p), "provenance")
            x += tlen(seg, f)
        y += int(f.size * 1.25)
    ctx.mark("provenance", (X0, top, X1, BAND_BOTTOM))


def provenance_texts(label) -> dict:
    """ProvenanceLabel -> the two declared strings the bar draws."""
    lines = label.lines()
    return {"source": lines[0], "as_of": lines[1]}


__all__ = ["draw_provenance", "provenance_texts", "BAND_TOP", "BAND_BOTTOM", "SIZE"]
