"""A small, dependency-free HTML table extractor (stdlib `html.parser` only - the project has
no bs4/lxml and CDSL/NSDL pages are plain server-rendered ASP.NET HTML tables).

`extract_tables(html)` returns one grid per `<table>`: a list of rows, each a list of cell
texts, with `colspan`/`rowspan` expanded so every row has the same width and a spanned cell's
text is repeated into every cell it covers. This is the structure CDSL's and NSDL's own table
markup needs (merged header bands over repeated sub-columns).

`extract_select_options(html, select_id)` reads one `<select>`'s `<option value=...>label</option>`
pairs, in document order, without executing the page's JavaScript or ASP.NET postback events -
the selection page itself always lists every available fortnight statically.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _TableParser(HTMLParser):
    """Collects every <table> as a list of raw rows; each row is a list of
    (text, colspan, rowspan) for <td>/<th> cells, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list = []
        self._table_depth = 0
        self._cur_table: list | None = None
        self._cur_row: list | None = None
        self._cell_span: tuple | None = None  # (colspan, rowspan) while inside a cell
        self._cell_text: list = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._cur_table = []
        elif tag == "tr" and self._table_depth >= 1:
            if self._table_depth == 1:
                self._cur_row = []
        elif tag in ("td", "th") and self._table_depth >= 1:
            if self._table_depth == 1:
                self._in_cell = True
                self._cell_text = []
                try:
                    colspan = max(1, int(a.get("colspan", 1)))
                except (TypeError, ValueError):
                    colspan = 1
                try:
                    rowspan = max(1, int(a.get("rowspan", 1)))
                except (TypeError, ValueError):
                    rowspan = 1
                self._cell_span = (colspan, rowspan)
        elif tag == "br" and self._in_cell:
            self._cell_text.append(" ")

    def handle_data(self, data):
        if self._in_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._table_depth == 1 and self._in_cell:
            colspan, rowspan = self._cell_span
            self._cur_row.append((_clean("".join(self._cell_text)), colspan, rowspan))
            self._in_cell = False
            self._cell_text = []
        elif tag == "tr" and self._table_depth == 1 and self._cur_row is not None:
            self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag == "table":
            if self._table_depth == 1 and self._cur_table is not None:
                self.tables.append(self._cur_table)
                self._cur_table = None
            self._table_depth = max(0, self._table_depth - 1)


def _expand(raw_rows: list) -> list:
    """Expand colspan/rowspan into a rectangular grid of plain strings."""
    grid: list = []
    pending: dict = {}   # (row_idx, col_idx) -> (text, rows_remaining)
    for r, raw_row in enumerate(raw_rows):
        row, c = [], 0
        cells = list(raw_row)
        ci = 0
        while ci < len(cells) or (r, c) in pending:
            if (r, c) in pending:
                text, remaining = pending.pop((r, c))
                row.append(text)
                if remaining > 1:
                    pending[(r + 1, c)] = (text, remaining - 1)
                c += 1
                continue
            text, colspan, rowspan = cells[ci]
            ci += 1
            for k in range(colspan):
                row.append(text)
                if rowspan > 1:
                    pending[(r + 1, c + k)] = (text, rowspan - 1)
            c += colspan
        grid.append(row)
    width = max((len(r) for r in grid), default=0)
    return [r + [""] * (width - len(r)) for r in grid]


def extract_tables(html: str) -> list:
    """List of grids (list[list[str]]), one per <table> in document order."""
    p = _TableParser()
    p.feed(html or "")
    return [_expand(t) for t in p.tables]


class _SelectParser(HTMLParser):
    def __init__(self, select_id: str):
        super().__init__(convert_charrefs=True)
        self.select_id = select_id
        self.options: list = []
        self._in_target = False
        self._in_option = False
        self._depth = 0
        self._cur_value: str | None = None
        self._cur_text: list = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "select":
            self._depth += 1
            if a.get("id") == self.select_id or a.get("name") == self.select_id:
                self._in_target = True
        elif tag == "option" and self._in_target:
            self._in_option = True
            self._cur_value = a.get("value")
            self._cur_text = []

    def handle_data(self, data):
        if self._in_option:
            self._cur_text.append(data)

    def handle_endtag(self, tag):
        if tag == "option" and self._in_option:
            self.options.append((self._cur_value or "", _clean("".join(self._cur_text))))
            self._in_option = False
        elif tag == "select":
            if self._depth == 1:
                self._in_target = False
            self._depth = max(0, self._depth - 1)


def extract_select_options(html: str, select_id: str) -> list:
    """[(value, label), ...] for one <select id=|name=select_id>, in document order."""
    p = _SelectParser(select_id)
    p.feed(html or "")
    return p.options


__all__ = ["extract_tables", "extract_select_options"]
