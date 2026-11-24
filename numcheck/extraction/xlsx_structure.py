"""Sheet structure detection for Excel models.

Why this module exists: a bank model is a *picture* (title rows, a units banner,
a header row of years, blank spacer columns, indented labels, several blocks per
sheet), not a database table.  To label a number correctly we must understand the
picture.  The work is done **once per sheet** on an in-memory grid, never per cell:

1. classify every cell (text / number / numeric text / error / date);
2. find header rows (period-like or text headers) and glue multi-row headers
   (a merged "Company A" above "FY24") into one label per column;
3. split the sheet into blocks at header rows; per block detect label columns,
   period-label columns (transposed layouts) and unit columns;
4. walk the rows once, producing for every value cell its row label, column
   label, section label and the unit evidence found on its row/block/sheet.

The result is a list of :class:`LocCell` that ``xlsx_extractor`` turns into
``ModelValue`` objects (unit resolution needs number formats, which live there).
Nothing here is O(cells x sheet width).
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Any

from numcheck.matching.normalizer import ParsedNumber, UnitHint, parse_unit_banner, scan_numbers
from numcheck.numfmt import CURRENCY_SYMBOLS, SCALE_WORDS
from numcheck.periods import YEAR_MAX, YEAR_MIN, find_periods

ERROR_VALUES = frozenset({
    "#N/A", "#DIV/0!", "#REF!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!",
    "#GETTING_DATA", "#FIELD!", "#BLOCKED!", "#CONNECT!", "#UNKNOWN!", "#EXTERNAL!",
})

#: label-only text that stands in for "no number here" inside a numeric column
_NULL_MARKERS = frozenset({"n.a.", "n/a", "na", "n.m.", "nm", "n.m", "-", "--", "—", "–", "nmf", "tbd", "n.d.", "nd", "*"})

_NOTE_RE = re.compile(r"^\s*(?:source|sources|note|notes|memo|disclaimer|footnote|\(\d+\)|\*|†|‡|[¹²³⁴⁵⁶⁷⁸⁹])", re.I)
_MONTH_YEAR_RE = re.compile(r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s\-/']+(?:\d{2}|\d{4})[ae]?$", re.I)
_NUMTEXT_PREFIX_RE = re.compile(r"^[\s(\-−–+$€£¥~]*[\d.]")

_BANNER_VOCAB = frozenset({
    "in", "figures", "amounts", "all", "numbers", "except", "per", "share", "data", "and", "currency",
    "units", "unit", "millions", "million", "billions", "billion", "thousands", "thousand", "usd", "eur",
    "gbp", "chf", "jpy", "cad", "aud", "us", "dollars", "dollar", "euros", "euro", "pounds", "stated",
    "expressed", "reported", "mm", "m", "bn", "b", "k", "mn", "000s", "000", "'000", "'000s", "$", "€", "£",
    "¥", "%", "x", "bps", "the", "for", "unless", "otherwise", "noted", "shown", "are", "is", "pts", "t",
    "tn", "mio", "mln", "bln", "eps", "dps", "usdm", "usdbn", "gbpm", "eurm", "gbpbn", "eurbn",
    "pence", "cents", "sterling", "yen", "francs", "sek", "nok", "dkk", "hkd", "sgd", "inr", "cny",
    "rmb", "nzd", "zar", "brl", "mxn", "krw", "indicated", "specified", "where", "of",
})
_BANNER_TOKEN_RE = re.compile(r"'?000s?|[a-z]+|[$€£¥%]")

_UNIT_RE = re.compile(
    r"^\(?\s*(?P<cur>usd|us\$|eur|gbp|chf|jpy|cad|aud|a\$|c\$|\$|€|£|¥)?\s*(?:in\s+)?"
    r"(?P<scale>k|m|mm|mn|bn|b|t|tn|thousands?|millions?|billions?|trillions?|'?000s?)?\s*\)?$",
    re.I,
)
_UNIT_WORDS = {
    "%": "percent", "pct": "percent", "percent": "percent", "% of sales": "percent",
    "x": "multiple", "×": "multiple", "multiple": "multiple", "times": "multiple",
    "bps": "bps", "bp": "bps", "basis points": "bps",
    "pp": "percent", "pts": "percent",
}
_UNIT_HEADERS = frozenset({"unit", "units", "uom", "currency", "ccy", "unit of measure"})


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #


@dataclass
class SheetGrid:
    """Raw sheet content, sparse. Coordinates are 1-based ``(row, col)``."""

    name: str
    cells: dict[tuple[int, int], Any] = field(default_factory=dict)
    fmts: dict[tuple[int, int], str] = field(default_factory=dict)       # number formats of numeric cells
    formulas: dict[tuple[int, int], str] = field(default_factory=dict)
    merged: list[tuple[int, int, int, int]] = field(default_factory=list)
    indent: dict[tuple[int, int], int] = field(default_factory=dict)      # text cells with indentation
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)
    cached_blank: set[tuple[int, int]] = field(default_factory=set)       # formulas whose cached text is ""
    hidden: bool = False
    read_only: bool = False


@dataclass
class LocCell:
    """A value-bearing cell with all the context found for it."""

    row: int
    col: int
    kind: str                       # "num" | "err" | "ntext"
    payload: Any                    # float | error text | ParsedNumber
    fmt: str
    row_label: str
    col_label: str
    section: str
    unit_hint: UnitHint | None      # from the row's unit column
    banner: UnitHint | None         # nearest banner above the block
    sheet_banner: UnitHint | None
    group_label: str = ""           # column-group header text (e.g. company name)


@dataclass
class SheetLayout:
    cells: list[LocCell] = field(default_factory=list)
    header_row: int = 0
    label_col: int = 0
    unit_hint_text: str = ""
    failed_rows: int = 0


# --------------------------------------------------------------------------- #
# Cell classification
# --------------------------------------------------------------------------- #

Entry = tuple  # (col, kind, payload, is_extra)


def _clean(s: str) -> str:
    return " ".join(s.replace("\xa0", " ").split())


@lru_cache(maxsize=65536)
def _period_cover(s: str) -> float:
    """Fraction of *s* covered by recognised periods (0 when none)."""
    if not s or len(s) > 40:
        return 0.0
    spans = find_periods(s)
    if not spans:
        return 1.0 if _MONTH_YEAR_RE.match(s) else 0.0
    return sum(e - st for _, st, e in spans) / max(len(s), 1)


def text_period_like(s: str) -> bool:
    return _period_cover(s) >= 0.4


def text_period_strict(s: str) -> bool:
    return _period_cover(s) >= 0.8


@lru_cache(maxsize=65536)
def _numeric_text(s: str) -> ParsedNumber | None:
    """Parse a whole-cell numeric string like ``1,234``, ``(45)``, ``12.5%``."""
    if len(s) > 28 or not _NUMTEXT_PREFIX_RE.match(s):
        return None
    try:
        found = scan_numbers(s)
    except Exception:  # noqa: BLE001 - scanner must never abort a sheet
        return None
    if len(found) != 1:
        return None
    p = found[0]
    if p.skip_reason or p.raw.strip() != s.strip() or not math.isfinite(p.value):
        return None
    return p


def classify(v: Any) -> tuple[str, Any]:
    """Return ``(kind, payload)``; kind "" means: ignore the cell."""
    if v is None or isinstance(v, bool):
        return "", None
    if isinstance(v, str):
        s = _clean(v)
        if not s:
            return "", None
        if s.upper() in ERROR_VALUES:
            return "err", s.upper()
        p = _numeric_text(s)
        if p is not None:
            return "ntext", p
        return "text", s
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return "date", v
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        if not math.isfinite(f):
            return "", None
        return "num", f
    return "", None


def is_year_value(v: float, fmt: str | None) -> bool:
    """Integer in the plausible year range, displayed without separators/decimals/units."""
    if v != int(v) or not (YEAR_MIN <= v <= YEAR_MAX):
        return False
    if fmt and any(ch in fmt for ch in ",%$€£¥.#"):
        return False
    return True


def period_label(kind: str, payload: Any, fmt: str | None) -> str | None:
    """Header text if the cell is period-like, else None."""
    if kind == "num":
        return str(int(payload)) if is_year_value(payload, fmt) else None
    if kind == "text":
        return payload if text_period_like(payload) else None
    if kind == "date":
        if isinstance(payload, (_dt.datetime, _dt.date)):
            return f"{payload:%b %Y}"
        return None
    return None


def _num_text(kind: str, payload: Any, fmt: str | None) -> str:
    """Printable label for a numeric/date cell that acts as a row label (transposed layouts)."""
    if kind == "num":
        return str(int(payload)) if payload == int(payload) else f"{payload:g}"
    if kind == "ntext":
        return payload.raw
    if kind == "date" and isinstance(payload, (_dt.datetime, _dt.date)):
        return f"{payload:%b %Y}"
    return str(payload)


# --------------------------------------------------------------------------- #
# Unit evidence from text
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=16384)
def unit_token_hint(text: str) -> tuple[UnitHint, bool] | None:
    """Interpret a unit-column cell (``$m``, ``%``, ``x``, ``USDm``, ``€bn``, ``bps``).

    Returns ``(hint, strong)`` or None. *strong* means the token is unmistakably a
    unit (so a one-row unit column is accepted); bare ``m``/``b`` are weak.
    """
    t = text.strip().lower()
    if not t or len(t) > 24:
        return None
    t = t.strip("()[] ")
    dim = _UNIT_WORDS.get(t)
    if dim:
        return UnitHint(dimension=dim), True
    m = _UNIT_RE.match(t)
    if m and (m.group("cur") or m.group("scale")):
        cur = CURRENCY_SYMBOLS.get((m.group("cur") or "").lower(), "") if m.group("cur") else ""
        scale_w = (m.group("scale") or "").lower()
        scale = SCALE_WORDS.get(scale_w, SCALE_WORDS.get(scale_w.rstrip("s"), SCALE_WORDS.get(scale_w.strip("'"), "")))
        hint = UnitHint(dimension="currency" if cur else "none", scale=scale, currency=cur,
                        scale_known=bool(scale) or (cur != "" and t in ("$", "€", "£", "¥")))
        strong = bool(cur and scale) or t in ("$", "€", "£", "¥")
        return hint, strong
    return None


def unit_hint_of(text: str) -> UnitHint | None:
    """Unit hint for a (forced) unit-column cell; falls back to banner parsing."""
    tok = unit_token_hint(text)
    if tok:
        return tok[0]
    h = parse_unit_banner(text)
    return None if h.is_empty() else h


@lru_cache(maxsize=16384)
def banner_hint(text: str) -> UnitHint | None:
    """Unit banner in a title/header cell, e.g. ``($ in millions)`` or ``Figures in US$m``.

    Strict on purpose: only *pure* banners (every word is unit vocabulary) or a
    parenthetical that states a scale/currency count, so "Margin analysis" or a
    note sentence never sets units for a whole block.
    """
    t = text.strip()
    if not t or len(t) > 140:
        return None
    candidates: list[str] = []
    if _is_pure_banner(t):
        candidates.append(t)
    else:
        for seg in re.findall(r"\(([^)]*)\)", t):
            if _is_pure_banner(seg):
                h = parse_unit_banner(seg)
                if h.scale_known or h.currency:
                    candidates.append(seg)
    for c in candidates:
        h = parse_unit_banner(c)
        if not h.is_empty():
            return h
    return None


_ATTR_DATE_HDR_RE = re.compile(
    r"\b(?:date|dated|vintage|maturity|matures|entry|exit|closing|closed|acquired|acquisition|founded|"
    r"inception|expiry|expiration|signed|announced|completed)\b", re.I)

# Clauses after the unit statement that qualify it rather than make it prose: exceptions and the fiscal
# year convention ("$ in millions, fiscal year ending December 31", "€m; FYE 31-Mar").
_BANNER_TAIL_RE = re.compile(
    r"[,;]?\s*\b(?:except|unless|other\s+than|excluding|excl\.?|fiscal\s+years?|fye|fy\s+end(?:ing|ed|s)?|"
    r"years?\s+end(?:ing|ed|s)?|year[- ]end(?:ing|ed)?|as\s+(?:of|at)|for\s+the\s+(?:fiscal\s+)?years?)\b|;", re.I)


def _is_pure_banner(t: str) -> bool:
    # Only the primary clause must be pure unit vocabulary: whatever follows
    # "except ..." / "unless ..." ("except per share data in pence", "unless
    # otherwise indicated") is an exception list, not a sign that this is prose.
    head = _BANNER_TAIL_RE.split(t, maxsplit=1)[0]
    if head.strip(" (") and head != t:
        t = head
    toks = _BANNER_TOKEN_RE.findall(t.lower())
    return bool(toks) and all(tok in _BANNER_VOCAB for tok in toks) and not parse_unit_banner(t).is_empty()


def _pure_banner_cell(t: str) -> bool:
    """A cell that is only a units banner ("($ in millions)"), not a header with a unit suffix ("EV ($bn)")."""
    return banner_hint(t) is not None and not strip_banner(t)


def strip_banner(text: str) -> str:
    """Remove a trailing unit parenthetical from a title ("Income Statement ($m)" -> "Income Statement")."""
    out = re.sub(r"\(([^)]*)\)", lambda m: "" if _is_pure_banner(m.group(1)) else m.group(0), text)
    return _clean(out)


# --------------------------------------------------------------------------- #
# Sheet analysis
# --------------------------------------------------------------------------- #


@dataclass
class _RowStat:
    plabels: list = field(default_factory=list)   # (col, label, strict, is_text)
    nn: int = 0                                   # non-year numerics + numeric text
    nerr: int = 0
    texts: list = field(default_factory=list)     # (col, text) non-period text
    has_value: bool = False


@dataclass
class _Group:
    start: int
    core_start: int
    core_end: int
    end: int
    hdr_label: dict[int, str]
    group_label: dict[int, str]
    forced_unit_cols: set[int]
    banner: UnitHint | None
    period_row: int
    col_unit: dict[int, UnitHint] = field(default_factory=dict)   # from a units row under the header


def analyze_sheet(g: SheetGrid) -> SheetLayout:
    """Compute labels/units context for every value cell of *g* (see module docstring)."""
    layout = SheetLayout()
    rows = _index_rows(g)
    if not rows:
        return layout
    srows = sorted(rows)
    stats = {r: _row_stat(rows[r], g, r) for r in srows}
    sheet_banner, sheet_banner_text = _sheet_banner(rows, srows, stats)
    layout.unit_hint_text = sheet_banner_text

    groups = _find_groups(rows, srows, stats, g)
    layout.header_row = groups[0].period_row if groups else 0

    # Region k: (group or None, first_row, last_row). Region 0 precedes the first header.
    regions: list[tuple[_Group | None, int, int]] = []
    if not groups:
        regions.append((None, srows[0], srows[-1]))
    else:
        regions.append((None, srows[0], groups[0].start - 1))
        for i, grp in enumerate(groups):
            nxt = groups[i + 1].start - 1 if i + 1 < len(groups) else srows[-1]
            regions.append((grp, grp.start, nxt))

    state = _WalkState()
    label_cols: Counter = Counter()
    tail_banner: UnitHint | None = None
    for grp, lo, hi in regions:
        region_rows = [r for r in srows if lo <= r <= hi]
        if grp is not None:
            region_rows = [r for r in region_rows if r > grp.end]
        if not region_rows and grp is None:
            continue
        state.banner = tail_banner
        if grp is not None and grp.banner is not None:
            state.banner = grp.banner
        tail_banner = _walk_region(g, rows, stats, grp, region_rows, state, sheet_banner, layout, label_cols)
    if label_cols:
        layout.label_col = label_cols.most_common(1)[0][0]
    return layout


@dataclass
class _WalkState:
    top_section: str = ""
    stack: list = field(default_factory=list)       # [(indent, label)]
    banner: UnitHint | None = None
    last_text_in_col: dict = field(default_factory=dict)
    last_row: int = 0
    broken: bool = False        # a new titled block started below the header: its sparse rows own no header


def _index_rows(g: SheetGrid) -> dict[int, list]:
    rows: dict[int, list] = {}
    for (r, c), v in g.cells.items():
        kind, payload = classify(v)
        if not kind:
            continue
        rows.setdefault(r, []).append((c, kind, payload, False))
    # Merged ranges: propagate the anchor's label-like value (text / year / date) over the range.
    for r1, c1, r2, c2 in g.merged:
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 20000:
            continue
        v = g.cells.get((r1, c1))
        kind, payload = classify(v)
        if kind not in ("text", "date") and not (kind == "num" and is_year_value(payload, g.fmts.get((r1, c1)))):
            continue
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1) and (r, c) not in g.cells:
                    rows.setdefault(r, []).append((c, kind, payload, True))
    for lst in rows.values():
        lst.sort(key=lambda e: e[0])
    return rows


def _row_stat(entries: list, g: SheetGrid, r: int) -> _RowStat:
    st = _RowStat()
    for c, kind, payload, extra in entries:
        if kind == "num":
            if is_year_value(payload, g.fmts.get((r, c))):
                st.plabels.append((c, str(int(payload)), True, False))
            else:
                st.nn += 1
                st.has_value |= not extra
        elif kind == "ntext":
            st.nn += 1
            st.has_value |= not extra
        elif kind == "err":
            st.nerr += 1
            st.has_value |= not extra
        elif kind == "date":
            lab = period_label(kind, payload, None)
            if lab:
                st.plabels.append((c, lab, True, False))
        elif kind == "text":
            if text_period_like(payload):
                st.plabels.append((c, payload, text_period_strict(payload), True))
            else:
                st.texts.append((c, payload))
    return st


def _sheet_banner(rows, srows, stats) -> tuple[UnitHint | None, str]:
    """First units banner in the top rows of the sheet (title area)."""
    for r in srows:
        if r > 12:
            break
        if stats[r].nn or stats[r].nerr:
            continue
        for c, kind, payload, _extra in rows[r]:
            if kind == "text":
                h = banner_hint(payload)
                if h is not None:
                    return h, payload
    return None, ""


def _numeric_cols(entries: list, g_fmts: dict, r: int) -> set[int]:
    out = set()
    for c, kind, payload, extra in entries:
        if extra:
            continue
        if kind == "ntext" or (kind == "num" and not is_year_value(payload, g_fmts.get((r, c)))):
            out.add(c)
    return out


def _find_groups(rows, srows, stats, g: SheetGrid) -> list[_Group]:
    """Locate header groups: contiguous header rows plus their group/sub-header rows."""
    header_rows: dict[int, dict[int, str]] = {}   # row -> {col: label}
    for i, r in enumerate(srows):
        st = stats[r]
        if st.nn or st.nerr:
            continue
        prev = srows[i - 1] if i > 0 else None
        if prev is not None and prev in header_rows and r - prev <= 2 and _units_row(rows[r], st) is not None:
            continue                                   # a units row under a text header is not a header itself
        nxt = srows[i + 1] if i + 1 < len(srows) else None
        reach = 2
        # a units row between the header and the data ("x", "%", "$m") is skipped when looking beneath
        if nxt is not None and nxt - r <= 2 and _units_row(rows[nxt], stats[nxt]) is not None:
            data = srows[i + 2] if i + 2 < len(srows) else None
            nxt = data if data is not None and data - r <= 3 else None
            reach = 3
        if len(st.plabels) >= 2:
            hdr = {c: lab for c, lab, _s, _t in st.plabels}
            if nxt is not None:
                # mixed header row ("Share price ($)", "EV / Revenue NTM", "LTM", "NTM"):
                # every text cell with numbers beneath is a column header too
                below = _numeric_cols(rows[nxt], g.fmts, nxt)
                for c, t in st.texts:
                    if c in below and c not in hdr and not _pure_banner_cell(t) and t.lower() not in _NULL_MARKERS:
                        hdr[c] = t
            header_rows[r] = hdr
            continue
        if len(st.plabels) == 1:
            c, lab, strict, is_text = st.plabels[0]
            if is_text and strict and c >= 2 and nxt is not None and nxt - r <= reach \
                    and c in _numeric_cols(rows[nxt], g.fmts, nxt):
                header_rows[r] = {c: lab}
            continue
        # text header (comps tables, transposed sheets): >=2 texts right of the leftmost text,
        # with numbers beneath them
        texts = [(c, t) for c, t in st.texts if not _pure_banner_cell(t) and t.lower() not in _NULL_MARKERS]
        if len(texts) >= 2 and nxt is not None and nxt - r <= reach:
            below = _numeric_cols(rows[nxt], g.fmts, nxt)
            # the leftmost text is the stub caption ("Company") unless it sits over numbers itself
            # ("Property | Motor | Liability | Group" with an empty stub cell)
            body = texts if texts[0][0] in below else texts[1:]
            hits = sum(1 for c, _t in body if c in below)
            transposed = len(body) >= 1 and rows[nxt] and _first_is_period(rows[nxt], g, nxt)
            if hits >= 1 and hits >= 0.5 * len(body) and (len(body) >= 2 or transposed):
                header_rows[r] = {c: t for c, t in body}
    groups: list[_Group] = []
    consumed_until = 0
    for r in sorted(header_rows):
        if r <= consumed_until:
            continue
        core_start, core_end = r, r
        while (core_end + 1) in header_rows:
            core_end += 1
        # a sparse leading header row ("2024 .... 2025" over Q1..Q4 Q1..Q4) is a group row of the denser one
        while core_start < core_end and len(header_rows[core_start]) < len(header_rows[core_start + 1]) \
                and set(header_rows[core_start]) <= set(header_rows[core_start + 1]):
            core_start += 1
        hdr_cols: dict[int, str] = {}
        for rr in range(core_start, core_end + 1):
            hdr_cols.update({c: lab for c, lab in header_rows.get(rr, {}).items()})
        start, end = core_start, core_end
        # group-header rows above
        up = core_start - 1
        while up >= 1 and up in rows and core_start - up <= 3 and up > consumed_until:
            if not _is_group_row(rows[up], stats[up], hdr_cols):
                break
            start = up
            up -= 1
        # sub-header rows below
        dn = core_end + 1
        units_rows: dict[int, dict[int, UnitHint]] = {}
        while dn in rows and dn - core_end <= 2 and dn not in header_rows:
            urow = _units_row(rows[dn], stats[dn])
            if urow is not None:
                units_rows[dn] = urow
            elif not _is_subheader_row(rows[dn], stats[dn], hdr_cols):
                break
            end = dn
            dn += 1
        grp = _build_group(rows, stats, start, core_start, core_end, end, hdr_cols, units_rows)
        groups.append(grp)
        consumed_until = end
    return groups


def _first_is_period(entries: list, g: SheetGrid, r: int) -> bool:
    for c, kind, payload, extra in entries:
        return period_label(kind, payload, g.fmts.get((r, c))) is not None
    return False


def _is_group_row(entries: list, st: _RowStat, hdr_cols: dict[int, str]) -> bool:
    if st.nn or st.nerr:
        return False
    hits = [(c, p) for c, k, p, _e in entries if c in hdr_cols and k in ("text", "date", "num")]
    if not hits:
        return False
    texts = [p for _c, p in hits if isinstance(p, str)]
    return not (texts and all(banner_hint(t) is not None for t in texts) and len(texts) == len(hits))


def _is_subheader_row(entries: list, st: _RowStat, hdr_cols: dict[int, str]) -> bool:
    if st.nn or st.nerr:
        return False
    hits = [p for c, k, p, _e in entries if c in hdr_cols and k in ("text", "date", "num")]
    return len(hits) >= 2


_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}$")
_PART_ONLY_RE = re.compile(r"^[QH][1-4]$", re.I)


def _units_row(entries: list, st: _RowStat) -> dict[int, UnitHint] | None:
    """A row made only of unit tokens ("x", "%", "$m", "USDm", "bps") - the units line under a text header.

    A leading "Unit"/"Units" caption is allowed. Returns ``{col: hint}`` or None.
    """
    if st.nn or st.nerr or st.plabels:
        return None
    out: dict[int, UnitHint] = {}
    for c, kind, payload, extra in entries:
        if extra:
            continue
        if kind != "text":
            return None
        if payload.lower() in _UNIT_HEADERS:
            continue
        tok = unit_token_hint(payload)
        if tok is None:
            return None
        out[c] = tok[0]
    return out or None


def _compose_header(parts: list[str]) -> str:
    """Join header pieces; a bare year over a bare quarter/half reads "Q1 2024" (so the period parses)."""
    if len(parts) == 2 and _YEAR_ONLY_RE.match(parts[0]) and _PART_ONLY_RE.match(parts[1]):
        return f"{parts[1].upper()} {parts[0]}"
    return " ".join(parts)


def _build_group(rows, stats, start, core_start, core_end, end, hdr_cols, units_rows=None) -> _Group:
    units_rows = units_rows or {}
    col_unit: dict[int, UnitHint] = {}
    for urow in units_rows.values():
        for c, h in urow.items():
            col_unit.setdefault(c, h)
    cols = sorted(hdr_cols)
    parts: dict[int, list[str]] = {c: [] for c in cols}
    group_label: dict[int, str] = {}
    forced_unit: set[int] = set()
    banner: UnitHint | None = None
    for r in range(start, end + 1):
        if r not in rows or r in units_rows:
            continue
        by_col = {c: (k, p) for c, k, p, _e in rows[r] if not (k == "text" and p.lower() in _NULL_MARKERS)}
        if r < core_start:  # group row: forward-fill within contiguous column runs
            cur = ""
            prev_c = None
            for c in cols:
                if prev_c is not None and c - prev_c > 1:
                    cur = ""
                kp = by_col.get(c)
                if kp and kp[0] in ("text", "date", "num"):
                    cur = _num_text(kp[0], kp[1], None)
                if cur:
                    parts[c].append(cur)
                    group_label.setdefault(c, cur)
                prev_c = c
        else:
            for c in cols:
                kp = by_col.get(c)
                if kp and kp[0] in ("text", "date", "num"):
                    parts[c].append(_num_text(kp[0], kp[1], None))
        # label-area cells in header rows: banners and unit-column markers
        for c, k, p, _e in rows[r]:
            if c in hdr_cols or k != "text":
                continue
            h = banner_hint(p)
            if h is not None and banner is None:
                banner = h
            if p.lower() in _UNIT_HEADERS:
                forced_unit.add(c)
    hdr_label: dict[int, str] = {}
    for c in cols:
        dedup: list[str] = []
        for p in parts[c]:
            if not dedup or dedup[-1].lower() != p.lower():
                dedup.append(p)
        hdr_label[c] = _compose_header(dedup)
    period_row = core_start
    for rr in range(core_start, core_end + 1):
        if stats.get(rr) is not None and len(stats[rr].plabels) >= 1:
            period_row = rr
            break
    return _Group(start, core_start, core_end, end, hdr_label, group_label, forced_unit, banner, period_row,
                  col_unit)


# --------------------------------------------------------------------------- #
# Region walk
# --------------------------------------------------------------------------- #


def _profile_columns(rows, region_rows, g, grp: _Group | None):
    """Decide each column's role in this block: label text, numbers, unit, or period-label."""
    num, text, pl, ne, unit_c, strong = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    for r in region_rows:
        for c, kind, payload, extra in rows[r]:
            if extra:
                continue
            ne[c] += 1
            if kind in ("num", "ntext", "err"):
                num[c] += 1
                if kind == "num" and is_year_value(payload, g.fmts.get((r, c))):
                    pl[c] += 1
            elif kind == "date":
                pl[c] += 1
            elif kind == "text":
                if text_period_like(payload):
                    pl[c] += 1
                if payload.lower() in _NULL_MARKERS:
                    continue
                text[c] += 1
                tok = unit_token_hint(payload)
                if tok:
                    unit_c[c] += 1
                    strong[c] += tok[1]
    hdr_cols = grp.hdr_label if grp else {}
    forced = grp.forced_unit_cols if grp else set()
    kinds: dict[int, str] = {}
    for c in ne:
        if pl[c] >= 2 and pl[c] >= 0.8 * ne[c] and c not in hdr_cols:
            kinds[c] = "rowhdr"
        elif text[c] and c not in hdr_cols and (
                (unit_c[c] >= 2 and unit_c[c] >= 0.6 * text[c])
                or (unit_c[c] == 1 and text[c] == 1 and strong[c] == 1)
                or c in forced):
            kinds[c] = "unit"
        elif num[c] and num[c] >= text[c]:
            kinds[c] = "num"
        else:
            kinds[c] = "text"
    return kinds


def _indent_of(g: SheetGrid, r: int, c: int, text: str) -> int:
    raw = g.cells.get((r, c))
    lead = 0
    if isinstance(raw, str):
        lead = len(raw) - len(raw.lstrip(" \t\xa0"))
    return max(g.indent.get((r, c), 0), (lead + 1) // 2 if lead else 0)


def _walk_region(g, rows, stats, grp, region_rows, state: _WalkState, sheet_banner, layout: SheetLayout, label_cols) -> UnitHint | None:
    kinds = _profile_columns(rows, region_rows, g, grp)
    hdr_label = grp.hdr_label if grp else {}
    group_label = grp.group_label if grp else {}
    tail_banner = state.banner
    state.broken = False
    state.last_row = grp.end if grp is not None else 0
    for r in region_rows:
        try:
            tail_banner = _walk_row(g, rows[r], r, kinds, hdr_label, group_label, grp, state, sheet_banner,
                                    layout, label_cols, tail_banner, stats.get(r))
        except Exception:  # noqa: BLE001 - one malformed row never aborts the sheet
            layout.failed_rows += 1
        state.last_row = r
    return tail_banner


def _walk_row(g, entries, r, kinds, hdr_label, group_label, grp, state: _WalkState, sheet_banner,
              layout: SheetLayout, label_cols, tail_banner, st: _RowStat | None = None):
    # a row of bare numeric years (no label, no other numbers) is a header row, not data
    year_row = st is not None and not st.nn and not st.nerr and not st.texts
    pending: list[str] = []
    pend_indent = -1
    first_label_col = 0
    last_num = False
    seg_label, seg_indent, seg_unit = "", 0, None
    pend_unit: UnitHint | None = None
    out: list[tuple[int, str, Any, str, str, UnitHint | None, int]] = []
    texts_seen: list[tuple[int, str]] = []
    trailing_unit: UnitHint | None = None

    def start_segment():
        nonlocal last_num, seg_label, seg_indent, seg_unit
        if not last_num:
            seg_label = _join(pending)
            seg_indent = max(pend_indent, 0)
            seg_unit = pend_unit
            last_num = True

    for c, kind, payload, extra in entries:
        ck = kinds.get(c, "text")
        is_value = kind == "err" or (kind in ("num", "ntext") and ck not in ("rowhdr",) and not extra
                                      and not (kind == "ntext" and ck in ("text", "unit")))
        if kind == "num" and ck in ("text",) and not extra:
            is_value = True
        if is_value and year_row and kind == "num" and is_year_value(payload, g.fmts.get((r, c))):
            continue
        if is_value and not extra:
            start_segment()
            fmt = g.fmts.get((r, c), "")
            out.append((c, kind, payload, fmt, seg_label, seg_unit, seg_indent))
            continue
        # label-ish content
        if kind == "text":
            texts_seen.append((c, payload))
            if ck == "num":
                continue
            if ck == "unit":
                if extra:
                    continue
                h = unit_hint_of(payload)
                if h is not None:
                    if last_num and out and trailing_unit is None and all(o[5] is None for o in out):
                        trailing_unit = h
                    elif not last_num:
                        pend_unit = h
                continue
            if payload.lower() in _NULL_MARKERS and last_num:
                continue
            if pending and not last_num and _ATTR_DATE_HDR_RE.search(hdr_label.get(c, "") or ""):
                continue   # "Entry date: Mar-2019" describes the row; it is not its period
            if last_num:
                pending, pend_indent, pend_unit, last_num = [], -1, None, False
            if not pending:
                pend_indent = _indent_of(g, r, c, payload)
                first_label_col = first_label_col or c
            pending.append(payload)
        elif kind in ("num", "ntext", "date") and ck in ("rowhdr", "text") or (kind == "date" and not last_num):
            if kind == "date" and ck == "num":
                continue
            if pending and _ATTR_DATE_HDR_RE.search(hdr_label.get(c, "") or ""):
                continue   # "Entry date: Mar-2019" describes the row; it is not its period
            if last_num:
                pending, pend_indent, pend_unit, last_num = [], -1, None, False
            txt = _num_text(kind, payload, g.fmts.get((r, c)))
            if not pending:
                pend_indent = 0
                first_label_col = first_label_col or c
            pending.append(txt)

    if trailing_unit is not None:
        out = [(c, k, p, f, lb, trailing_unit if u is None else u, ind) for c, k, p, f, lb, u, ind in out]

    if not out:
        return _standalone_row(g, r, texts_seen, state, tail_banner)
    if grp is not None and len(hdr_label) >= 2:
        # After a titled block break, rows touching fewer than two header columns belong to the new
        # block (e.g. "Valuation" under projections), not to the projection years.
        n_hdr = sum(1 for o in out if o[0] in hdr_label)
        if n_hdr >= 2:
            state.broken = False
    use_hdr = not (state.broken and grp is not None and len(hdr_label) >= 2
                   and sum(1 for o in out if o[0] in hdr_label) < 2)

    # --- value row ---
    label_cols[first_label_col] += 1 if first_label_col else 0
    label = seg_label
    indent = seg_indent
    section = _section_for(state, label, indent) if label else _compose(state.top_section, [])
    if not section and group_label:
        pass
    tail = None  # a value row ends the "just above the block" banner window
    banner = state.banner
    last_label = None
    for c, kind, payload, fmt, lb, unit, ind in out:
        if lb != last_label:
            # section depends on the segment's own label (side-by-side tables)
            section = _section_for(state, lb, ind) if lb else _compose(state.top_section, [])
            last_label = lb
        col_label = hdr_label.get(c, "") if use_hdr else ""
        grp_lab = group_label.get(c, "") if use_hdr else ""
        if unit is None and grp is not None and use_hdr:
            unit = grp.col_unit.get(c)
        if not col_label and grp is None:
            col_label = state.last_text_in_col.get(c, "")
        layout.cells.append(LocCell(
            row=r, col=c, kind=kind, payload=payload, fmt=fmt, row_label=lb, col_label=col_label,
            section=section or grp_lab, unit_hint=unit, banner=banner, sheet_banner=sheet_banner,
            group_label=grp_lab,
        ))
    return tail


def _standalone_row(g, r, texts_seen, state: _WalkState, tail_banner):
    """A row without values: banner, section title, sub-heading or note."""
    title = None
    title_indent = 0
    banner = None
    after_gap = r - state.last_row > 1
    for c, t in texts_seen:
        h = banner_hint(t)
        if h is not None:
            banner = h
        state.last_text_in_col[c] = t if not text_period_like(t) else state.last_text_in_col.get(c, "")
    if banner is not None:
        state.banner = banner
        tail_banner = banner
    content = [(c, t) for c, t in texts_seen
               if t.strip() and t.lower() not in _NULL_MARKERS and not (banner_hint(t) is not None and _is_pure_banner(t))]
    if len({c for c, _t in content}) >= 2:
        # Several text cells across columns ("Low | High", "Company A | Company B") form a header
        # row, not a section title: never let "Low" become the section of the whole block.
        return tail_banner
    for c, t in texts_seen:
        if _NOTE_RE.match(t) or is_note_text(t) or t.lower() in _NULL_MARKERS:
            continue
        if banner_hint(t) is not None and _is_pure_banner(t):
            continue
        title = strip_banner(t)
        title_indent = _indent_of(g, r, c, t)
        break
    if title:
        if title_indent == 0 and not title.endswith(":"):
            state.top_section = title
            state.stack = []
            state.broken = after_gap
        else:
            while state.stack and state.stack[-1][0] >= title_indent:
                state.stack.pop()
            state.stack.append((title_indent, title.rstrip(":")))
    return tail_banner


_SENTENCE_RE = re.compile(r"[.!?;…]\s*$|[.;]\s+\S")


def is_note_text(t: str) -> bool:
    """Long or sentence-like text is a note (context), never a section title."""
    if len(t) > 60:
        return True
    if _SENTENCE_RE.search(t):
        # "U.S. Revenue" / "Inc." style abbreviations are not sentences: need several words
        return len(t.split()) >= 4 or t.rstrip().endswith((";", "…", "!", "?"))
    return False


def _section_for(state: _WalkState, label: str, indent: int) -> str:
    """Section = top-level title plus parent rows (smaller indent) of this row; registers the row as a parent."""
    while state.stack and state.stack[-1][0] >= indent:
        state.stack.pop()
    section = _compose(state.top_section, [lab for _i, lab in state.stack])
    state.stack.append((indent, label))
    return section


def _compose(top: str, parents: list[str]) -> str:
    return " / ".join(p for p in [top, *parents] if p)


def _join(parts: list[str]) -> str:
    out: list[str] = []
    for p in parts:
        p = _clean(p)
        if p and (not out or out[-1].lower() != p.lower()):
            out.append(p)
    return " ".join(out)
