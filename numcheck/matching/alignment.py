"""Structural alignment of deck tables and chart series onto model grids.

Why this exists: labels are the weakest link in real decks (a table whose header
says "Segment A" while the model row says "Div 1 net rev").  Values are the
strongest: if most numbers of a deck table land on a model rectangle, the table
*is* that rectangle, and any cell inside it that disagrees is a confident
mismatch even though no label ever matched.

* **Table alignment** - for deck tables with >= 4 numbers, find per model sheet
  the offset (and orientation: normal / transposed, raw or gap-compressed
  coordinates) mapping the deck grid onto ``ModelValue.row/col`` with the most value
  matches.  Seeds come from unique-ish value hits; accepted at >= 60 % of the
  table's numbers and >= 3 matches.
* **Series alignment** - a chart series (ordered by ``point_index``) against
  consecutive cells of a model row or column; same acceptance rule.
* **Value-only** is implemented in the pipeline on top of ``ValueIndex.hits``.

Only index *pruning* is heuristic; every accepted cell is verified with
``normalizer.compare``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from numcheck.config import Config
from numcheck.matching.index import ModelIndex
from numcheck.matching.normalizer import compare
from numcheck.models import DeckNumber, ReviewWarning

MIN_TABLE_NUMBERS = 4
MIN_MATCHES = 3
MIN_RATIO = 0.60
MAX_ANCHORS = 60
MAX_ANCHOR_HITS = 8
TOP_OFFSETS = 6


@dataclass
class AlignResult:
    """The model cell a deck number sits on according to a structural alignment."""

    idx: int                    # index into ModelIndex.values
    comparison: object          # normalizer.Comparison or None (error cell)
    ok: bool
    method: str                 # "table_align" | "series_align"
    block: str = ""             # human-readable block id for notes
    support: float = 0.0        # matches / numbers in the block


class _Grids:
    """Lazy per-sheet coordinate maps (raw and gap-compressed) for a ModelIndex."""

    def __init__(self, index: ModelIndex):
        self.index = index
        self._grid: dict[tuple, dict[tuple[int, int], int]] = {}
        self._rank: dict[tuple, tuple[dict[int, int], dict[int, int]]] = {}
        self._lines: dict[tuple, list[tuple[int, int]]] = {}
        self._built: set[tuple] = set()

    def _build(self, sheet: tuple[str, str]) -> None:
        if sheet in self._built:
            return
        self._built.add(sheet)
        rows: set[int] = set()
        cols: set[int] = set()
        cells: list[tuple[int, int, int]] = []
        for i, mv in enumerate(self.index.values):
            if (mv.workbook, mv.sheet_name) != sheet or mv.row <= 0 or mv.col <= 0:
                continue
            rows.add(mv.row)
            cols.add(mv.col)
            cells.append((mv.row, mv.col, i))
        rr = {r: k for k, r in enumerate(sorted(rows))}
        cr = {c: k for k, c in enumerate(sorted(cols))}
        self._rank[sheet] = (rr, cr)
        raw: dict[tuple[int, int], int] = {}
        rank: dict[tuple[int, int], int] = {}
        rowlines: dict[int, list[tuple[int, int]]] = defaultdict(list)
        collines: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for r, c, i in cells:
            raw[(r, c)] = i
            rank[(rr[r], cr[c])] = i
            rowlines[r].append((c, i))
            collines[c].append((r, i))
        self._grid[(sheet, "raw")] = raw
        self._grid[(sheet, "rank")] = rank
        for r, lst in rowlines.items():
            lst.sort()
            self._lines[(sheet, "row", r)] = lst
        for c, lst in collines.items():
            lst.sort()
            self._lines[(sheet, "col", c)] = lst

    def coord(self, sheet: tuple[str, str], system: str, mv) -> tuple[int, int]:
        self._build(sheet)
        if system == "raw":
            return mv.row, mv.col
        rr, cr = self._rank[sheet]
        return rr[mv.row], cr[mv.col]

    def cell(self, sheet: tuple[str, str], system: str, r: int, c: int) -> int | None:
        self._build(sheet)
        return self._grid[(sheet, system)].get((r, c))

    def line(self, sheet: tuple[str, str], kind: str, key: int) -> list[tuple[int, int]]:
        self._build(sheet)
        return self._lines.get((sheet, kind, key), [])


def _sheet_key(mv) -> tuple[str, str]:
    return (mv.workbook, mv.sheet_name)


def _eval_cell(d: DeckNumber, idx: int | None, index: ModelIndex, config: Config) -> tuple[str, object, int | None]:
    """('ok'|'bad'|'absent', comparison, idx) for a deck number against one model cell."""
    if idx is None:
        return "absent", None, None
    mv = index.values[idx]
    if mv.is_error:
        return "bad", None, idx
    c = compare(d, mv, config)
    if c is None:
        return "absent", None, None
    return ("ok" if c.ok else "bad"), c, idx


def _accept(matches: int, n: int, matched_values: set[float]) -> bool:
    if matches < MIN_MATCHES or matches < MIN_RATIO * n:
        return False
    # A block of zeros / one repeated value matches by coincidence everywhere.
    return len(matched_values) >= 2


def _align_table(members: list[tuple[int, DeckNumber]], index: ModelIndex, grids: _Grids,
                 config: Config, block_id: str) -> dict[int, AlignResult]:
    vindex = index.value_index()
    n = len(members)
    votes: Counter = Counter()
    for _, d in members[:MAX_ANCHORS]:
        hits = vindex.hits(d)
        if not hits or len(hits) > MAX_ANCHOR_HITS:
            continue
        for h, _c in hits:
            mv = index.values[h]
            if mv.row <= 0 or mv.col <= 0:
                continue
            sheet = _sheet_key(mv)
            for system in ("raw", "rank"):
                R, C = grids.coord(sheet, system, mv)
                votes[(sheet, system, "n", R - d.table_row, C - d.table_col)] += 1
                votes[(sheet, system, "t", R - d.table_col, C - d.table_row)] += 1
    best: tuple | None = None
    best_res: dict[int, AlignResult] = {}
    for key, _v in votes.most_common(TOP_OFFSETS):
        sheet, system, orient, dr, dc = key
        res: dict[int, AlignResult] = {}
        matches = 0
        values: set[float] = set()
        for pos, d in members:
            if orient == "n":
                r, c = d.table_row + dr, d.table_col + dc
            else:
                r, c = d.table_col + dr, d.table_row + dc
            state, cmp_, idx = _eval_cell(d, grids.cell(sheet, system, r, c), index, config)
            if state == "absent":
                continue
            if state == "ok":
                matches += 1
                values.add(round(cmp_.model_value, 9))
            res[pos] = AlignResult(idx, cmp_, state == "ok", "table_align", block_id)
        if not _accept(matches, n, values):
            continue
        score = (matches, -len(res))
        if best is None or score > best[0]:
            best, best_res = (score, key), res
    if best is None:
        return {}
    support = best[0][0] / max(n, 1)
    for r in best_res.values():
        r.support = support
    return best_res


def _align_series(points: list[tuple[int, DeckNumber]], index: ModelIndex, grids: _Grids,
                  config: Config, block_id: str) -> dict[int, AlignResult]:
    vindex = index.value_index()
    n = len(points)
    votes: Counter = Counter()
    for _, d in points[:MAX_ANCHORS]:
        hits = vindex.hits(d)
        if not hits or len(hits) > MAX_ANCHOR_HITS:
            continue
        for h, _c in hits:
            mv = index.values[h]
            if mv.row <= 0 or mv.col <= 0:
                continue
            sheet = _sheet_key(mv)
            for kind, key, along in (("row", mv.row, mv.col), ("col", mv.col, mv.row)):
                line = grids.line(sheet, kind, key)
                pos = next((k for k, (a, _i) in enumerate(line) if a == along), None)
                if pos is not None:
                    votes[(sheet, kind, key, pos - d.point_index)] += 1
    best: tuple | None = None
    best_res: dict[int, AlignResult] = {}
    for key, _v in votes.most_common(TOP_OFFSETS):
        sheet, kind, lkey, off = key
        line = grids.line(sheet, kind, lkey)
        res: dict[int, AlignResult] = {}
        matches = 0
        values: set[float] = set()
        for pos, d in points:
            p = off + d.point_index
            idx = line[p][1] if 0 <= p < len(line) else None
            state, cmp_, idx = _eval_cell(d, idx, index, config)
            if state == "absent":
                continue
            if state == "ok":
                matches += 1
                values.add(round(cmp_.model_value, 9))
            res[pos] = AlignResult(idx, cmp_, state == "ok", "series_align", block_id)
        if not _accept(matches, n, values):
            continue
        score = (matches, -len(res))
        if best is None or score > best[0]:
            best, best_res = (score, key), res
    if best is None:
        return {}
    support = best[0][0] / max(n, 1)
    for r in best_res.values():
        r.support = support
    return best_res


def align_blocks(
    decks: list[DeckNumber],
    live: list[int],
    index: ModelIndex,
    config: Config,
    warnings: list[ReviewWarning] | None = None,
) -> dict[int, AlignResult]:
    """Table and series alignment for the checkable deck numbers at positions *live*.

    Returns ``{position in decks: AlignResult}``.  A failure in one block is
    contained (and recorded as a warning) so the other blocks still align.
    """
    out: dict[int, AlignResult] = {}
    if index.n == 0:
        return out
    tables: dict[tuple, list[tuple[int, DeckNumber]]] = defaultdict(list)
    series: dict[tuple, list[tuple[int, DeckNumber]]] = defaultdict(list)
    for pos in live:
        d = decks[pos]
        if d.label_cell:
            continue
        if d.table_row >= 0 and d.table_col >= 0:
            tables[(d.slide_number, d.shape_id)].append((pos, d))
        elif d.point_index >= 0 and d.series_index >= 0:
            series[(d.slide_number, d.shape_id, d.series_index)].append((pos, d))
    grids = _Grids(index)
    failures = 0
    for key, members in tables.items():
        if len(members) < MIN_TABLE_NUMBERS:
            continue
        try:
            out.update(_align_table(members, index, grids, config, f"table on slide {key[0]}"))
        except Exception:  # noqa: BLE001 - contain per block
            failures += 1
    for key, points in series.items():
        if len(points) < MIN_MATCHES:
            continue
        points.sort(key=lambda t: t[1].point_index)
        try:
            res = _align_series(points, index, grids, config, f"chart series on slide {key[0]}")
            for pos, r in res.items():
                out.setdefault(pos, r)
        except Exception:  # noqa: BLE001
            failures += 1
    if failures and warnings is not None:
        warnings.append(ReviewWarning(
            code="alignment_failed",
            message=f"Table/series alignment failed for {failures} block(s); those used label matching only.",
            count=failures,
        ))
    return out
