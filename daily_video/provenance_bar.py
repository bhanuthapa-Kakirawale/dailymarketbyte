"""The provenance bar: SOURCE / DATA AS OF (/ FETCHED) on every factual scene.

One component, one position, one type size for the whole video - so a viewer learns where to
look. It sits in a reserved band above the on-screen disclaimer, left of the Shorts action rail,
on a dark plate, in bold 26 px: visually secondary to the scene's fact, never microscopic. It is
drawn onto the scene's own layer, so it arrives and leaves with its scene (no flashing) and is
visible for the scene's whole duration. Plain text attribution only - no exchange logo.

A scene opts in by declaring `texts["provenance"] = {"source": ..., "as_of": ...}` (declared, so
the content scan and the publication audit see exactly what is drawn).
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


def draw_provenance(ctx, prov: dict, p: float = 1.0) -> None:
    """`prov` = {"source": "SOURCE: NSE", "as_of": "DATA AS OF: 25 SEP 2026 · 3:30 PM IST"}."""
    lines = [s for s in (prov.get("source"), prov.get("as_of")) if s]
    if not lines or p <= 0:
        return
    width = X1 - X0 - 2 * PAD
    fonts = [fit(s, width, SIZE, bold=True, min_size=theme.MIN_FONT) for s in lines]
    line_h = [int(f.size * 1.25) for f in fonts]
    h = sum(line_h) + 2 * PAD - 6
    top = BAND_BOTTOM - h
    hr = HiRes((X0, top, X1, BAND_BOTTOM))
    hr.rect((X0, top, X1, BAND_BOTTOM), fill=alpha(theme.PANEL, 0.88 * p), radius=14,
            outline=alpha(theme.PANEL_BORDER, 0.9 * p), width=1.4)
    hr.rect((X0, top + 12, X0 + 5, BAND_BOTTOM - 12), fill=alpha(theme.BRAND, p), radius=2.5)
    hr.composite(ctx.layer)
    y = top + PAD - 3
    for s, f, lh in zip(lines, fonts, line_h):
        head, sep, rest = s.partition(": ")
        x = X0 + PAD + 8
        if sep:
            ctx.ink.text(ctx.d, (x, y), head + ":", f, alpha(theme.TEXT_SECONDARY, p), "provenance")
            x += tlen(head + ": ", f)
            ctx.ink.text(ctx.d, (x, y), rest, f, alpha(theme.TEXT_PRIMARY, p), "provenance")
        else:
            ctx.ink.text(ctx.d, (x, y), s, f, alpha(theme.TEXT_PRIMARY, p), "provenance")
        y += lh
    ctx.mark("provenance", (X0, top, X1, BAND_BOTTOM))


def provenance_texts(label) -> dict:
    """ProvenanceLabel -> the two declared strings the bar draws."""
    lines = label.lines()
    return {"source": lines[0], "as_of": lines[1]}


__all__ = ["draw_provenance", "provenance_texts", "BAND_TOP", "BAND_BOTTOM", "SIZE"]
