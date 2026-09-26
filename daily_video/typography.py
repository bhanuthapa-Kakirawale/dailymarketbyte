"""Text: one font resolver for the whole video, and a recorder that remembers where every
string landed so QA can check bounds and overlaps from the actual draw calls.

Fonts resolve through `video.font` - the same chain the original Daily Market Byte renderer
uses - so the main sections and the Radar section can never end up in different families.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from video import _BOLD, _REG, ellipsize, fit, font, tlen, wrap

from . import theme


def font_report() -> dict:
    """Which font file each weight actually resolved to on this machine (packet: font
    diagnosis). `video.font` walks `_BOLD`/`_REG` in order and takes the first that exists."""
    def _resolve(chain):
        for p in chain:
            if os.path.exists(p):
                return p, chain.index(p)
        return None, -1

    out = {}
    for weight, chain in (("bold", _BOLD), ("regular", _REG)):
        path, idx = _resolve(chain)
        out[weight] = {
            "requested_chain": list(chain),
            "resolved_path": path,
            "resolved_family": os.path.splitext(os.path.basename(path))[0] if path else "PIL default",
            "fallback_used": idx > 0,
            "loaded_path": getattr(font(40, weight == "bold"), "path", None),
        }
    return out


@dataclass
class TextBox:
    text: str
    role: str
    box: tuple          # (x0, y0, x1, y1)
    size: int


@dataclass
class Recorder:
    """Collects every text box and annotation marker drawn in one frame."""
    texts: list = field(default_factory=list)
    marks: list = field(default_factory=list)   # (kind, box)

    def mark(self, kind: str, box) -> None:
        self.marks.append((kind, tuple(int(v) for v in box)))


class Ink:
    """Thin wrapper over ImageDraw.text that records every box. Scenes draw all viewer text
    through this, never through `ImageDraw.text` directly."""

    def __init__(self, recorder: Recorder | None = None):
        self.rec = recorder

    def text(self, d, xy, s, f, fill, role="text", anchor=None):
        if not s:
            return (xy[0], xy[1], xy[0], xy[1])
        d.text(xy, s, font=f, fill=fill, anchor=anchor)
        box = d.textbbox(xy, s, font=f, anchor=anchor)
        if self.rec is not None:
            self.rec.texts.append(TextBox(s, role, tuple(int(v) for v in box), f.size))
        return box

    def centered(self, d, cx, y, s, f, fill, role="text"):
        return self.text(d, (cx - tlen(s, f) / 2, y), s, f, fill, role)

    def right(self, d, x1, y, s, f, fill, role="text"):
        return self.text(d, (x1 - tlen(s, f), y), s, f, fill, role)

    def mark(self, kind, box):
        if self.rec is not None:
            self.rec.mark(kind, box)


__all__ = ["font", "fit", "wrap", "tlen", "ellipsize", "font_report", "Recorder", "Ink",
           "TextBox"]
