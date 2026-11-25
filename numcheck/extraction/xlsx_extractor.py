"""Extract labelled values from an Excel model (openpyxl).

Pipeline (see docs/DESIGN.md 1.2):

1. validate the file (``fileio.sniff_office_file``) and load it twice, once for cached
   values and once for formulas (streaming ``read_only`` above ``large_workbook_cells``);
2. materialise every sheet into a sparse :class:`~numcheck.extraction.xlsx_structure.SheetGrid`
   *once* - all label/header/unit context is then computed per row/column, never per cell;
3. turn each located cell into a :class:`~numcheck.models.ModelValue`, resolving
   units in the documented order (number format -> unit column -> row label -> column
   label -> block banner -> sheet banner);
4. report everything that reduces coverage as :class:`~numcheck.models.ReviewWarning`.
"""

from __future__ import annotations

import fnmatch
import logging
import math
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import openpyxl
from lxml import etree
from openpyxl.utils import get_column_letter, range_boundaries

from numcheck.config import Config
from numcheck.extraction import fileio
from numcheck.extraction.formula_eval import evaluate_uncached
from numcheck.extraction.xlsx_structure import (
    LocCell,
    SheetGrid,
    SheetLayout,
    analyze_sheet,
    classify,
)
from numcheck.matching.normalizer import UnitHint, explicit_currency_unit, is_fx_rate_label, parse_unit_banner
from numcheck.models import ModelExtraction, ModelText, ModelValue, NumCheckInputError, ReviewWarning, SheetInfo
from numcheck.numfmt import DIVISOR_SCALE, FormatInfo, parse_number_format
from numcheck.periods import header_date_period, header_period_key, period_key

log = logging.getLogger(__name__)

_RANGE_MAX_CELLS = 50                      # named ranges larger than this are not expanded
_PER_SHARE_RE = re.compile(r"per[\s-]share|\beps\b|\bdps\b|\bshare price\b|\bprice per\b", re.I)
_UNCALC_HINT = "Open the workbook in Excel and save it so formula results are stored"
_REAL_DIMS = ("currency", "percent", "multiple", "bps")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def extract_model(path: str, config: Config | None = None) -> list[ModelValue]:
    """Compat wrapper: just the values of :func:`extract_model_full`."""
    return extract_model_full(path, config).values


def extract_model_full(path: str, config: Config | None = None) -> ModelExtraction:
    """Extract every numeric cell of the workbook with labels, units, periods and provenance."""
    config = config or Config()
    real = fileio.sniff_office_file(path, "xlsx", config.password, config.max_file_mb)
    name = os.path.basename(str(path))
    result = ModelExtraction(workbook=name)

    streaming = _estimate_cells(real, name) > config.large_workbook_cells
    wb_v, wb_f = _load_pair(real, name, streaming)
    try:
        if streaming:
            result.warnings.append(ReviewWarning(
                code="large_workbook_streaming",
                message=f"{name} was read in streaming mode to reduce memory use.",
                file=name))
        ext_links = getattr(wb_f, "_external_links", None) or getattr(wb_v, "_external_links", None)
        if ext_links:
            result.warnings.append(ReviewWarning(
                code="external_links",
                message=(f"{name} links to {len(ext_links)} external workbook(s). Cached values were used; "
                         "they may be stale if the linked files changed."),
                file=name, count=len(ext_links)))
        _extract_sheets(wb_v, wb_f, real, name, streaming, config, result)
    finally:
        for wb in (wb_v, wb_f):
            try:
                wb.close()
            except Exception:  # noqa: BLE001
                pass
    return result


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _open(path: str, name: str, *, data_only: bool, read_only: bool):
    try:
        return openpyxl.load_workbook(path, data_only=data_only, read_only=read_only, keep_vba=False)
    except NumCheckInputError:
        raise
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for damaged files
        raise NumCheckInputError(
            f"Could not read {name} as an Excel workbook: {exc}", file=name,
            hint="Open the file in Excel, save it as a fresh .xlsx and try again.") from exc


def _estimate_cells(path: str, name: str) -> int:
    """Sum of declared sheet areas, read lazily from the sheet dimension tags."""
    wb = _open(path, name, data_only=True, read_only=True)
    try:
        total = 0
        for ws in wb.worksheets:
            total += int(getattr(ws, "max_row", 0) or 0) * int(getattr(ws, "max_column", 0) or 0)
        return total
    finally:
        wb.close()


def _load_pair(path: str, name: str, streaming: bool):
    wb_v = _open(path, name, data_only=True, read_only=streaming)
    try:
        wb_f = _open(path, name, data_only=False, read_only=streaming)
    except Exception:
        wb_v.close()
        raise
    return wb_v, wb_f


# --------------------------------------------------------------------------- #
# Sheet grids
# --------------------------------------------------------------------------- #


def _iter_cells(ws, streaming: bool):
    """Yield populated cells. Normal mode walks the sparse cell dict, avoiding empty-grid scans."""
    if not streaming:
        cells = getattr(ws, "_cells", None)
        if isinstance(cells, dict):
            yield from cells.values()
            return
    try:
        ws.reset_dimensions()          # streaming: iterate real rows only, ignore inflated <dimension>
    except Exception:  # noqa: BLE001
        pass
    for row in ws.iter_rows():
        for cell in row:
            yield cell


def _formula_text(v: Any) -> str:
    if isinstance(v, str):
        return v
    text = getattr(v, "text", None)
    if isinstance(text, str):
        return text if text.startswith("=") else "=" + text
    return "=<formula>"


def _cell_indent(cell) -> int:
    try:
        return int(cell.alignment.indent or 0)
    except Exception:  # noqa: BLE001
        return 0


def _build_grid(ws_v, ws_f, streaming: bool) -> SheetGrid:
    g = SheetGrid(name=ws_v.title, read_only=streaming)
    state = getattr(ws_v, "sheet_state", "visible")
    g.hidden = state in ("hidden", "veryHidden")
    for cell in _iter_cells(ws_v, streaming):
        v = getattr(cell, "value", None)
        if v is None:
            continue
        key = (cell.row, cell.column)
        g.cells[key] = v
        if isinstance(v, str):
            ind = _cell_indent(cell)
            if ind:
                g.indent[key] = ind
        else:
            fmt = getattr(cell, "number_format", "General")
            if fmt and fmt != "General":
                g.fmts[key] = fmt
    for cell in _iter_cells(ws_f, streaming):
        if getattr(cell, "data_type", "") == "f":
            key = (cell.row, cell.column)
            g.formulas[key] = _formula_text(cell.value)
            if key not in g.cells and key not in g.fmts:   # uncached: keep its format for a fallback result
                fmt = getattr(cell, "number_format", "General")
                if fmt and fmt != "General":
                    g.fmts[key] = fmt
    if streaming:
        _read_streaming_structure(ws_v, g)
    else:
        _read_structure(ws_v, g)
    return g


def _read_streaming_structure(ws, g: SheetGrid) -> None:
    """Read worksheet metadata omitted by openpyxl's read-only cell iterator."""
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    tags = tuple(main + name for name in ("row", "col", "mergeCell"))
    with ws._get_source() as stream:
        for _, element in etree.iterparse(stream, events=("end",), tag=tags, no_network=True, resolve_entities=False):
            if element.tag == main + "mergeCell" and element.get("ref"):
                left, top, right, bottom = range_boundaries(element.get("ref"))
                if None not in (left, top, right, bottom):
                    g.merged.append((top, left, bottom, right))
            elif element.get("hidden") in ("1", "true"):
                if element.tag == main + "row" and element.get("r", "").isdigit():
                    g.hidden_rows.add(int(element.get("r")))
                elif element.tag == main + "col":
                    lo, hi = element.get("min", "0"), element.get("max", "0")
                    if lo.isdigit() and hi.isdigit() and 1 <= int(lo) <= int(hi) <= 16_384:
                        g.hidden_cols.update(range(int(lo), int(hi) + 1))
            parent = element.getparent()
            element.clear()
            if parent is not None:
                while element.getprevious() is not None:
                    del parent[0]


def _read_structure(ws, g: SheetGrid) -> None:
    """Read merged ranges and hidden rows/columns from an in-memory sheet."""
    try:
        for rng in ws.merged_cells.ranges:
            g.merged.append((rng.min_row, rng.min_col, rng.max_row, rng.max_col))
    except Exception:  # noqa: BLE001
        pass
    try:
        for idx, dim in ws.row_dimensions.items():
            if dim.hidden:
                g.hidden_rows.add(int(idx))
        for dim in ws.column_dimensions.values():
            if dim.hidden:
                lo = dim.min or 0
                hi = dim.max or lo
                if lo and hi - lo < 20000:
                    g.hidden_cols.update(range(lo, hi + 1))
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Uncalculated formulas
# --------------------------------------------------------------------------- #


def _uncached(g: SheetGrid) -> list[tuple[int, int]]:
    return [k for k in g.formulas if k not in g.cells and k not in g.cached_blank]


def _recalculate(real: str, grids: dict[str, SheetGrid], streaming: bool, name: str) -> tuple[int, str]:
    """Best effort: LibreOffice recalculates and stores results; copy them into the grids.

    Returns ``(recovered_cells, note)``; never raises.
    """
    try:
        if not fileio.soffice_binary():
            return 0, ""
        with tempfile.TemporaryDirectory(prefix="numcheck_recalc_") as tmp:
            out = fileio.soffice_convert(real, tmp, "xlsx")
            if not out:
                return 0, "LibreOffice recalculation failed."
            wb = _open(out, name, data_only=True, read_only=streaming)
            try:
                recovered = 0
                for ws in wb.worksheets:
                    g = grids.get(ws.title)
                    if g is None:
                        continue
                    missing = set(_uncached(g))
                    if not missing:
                        continue
                    for cell in _iter_cells(ws, streaming):
                        key = (cell.row, cell.column)
                        v = getattr(cell, "value", None)
                        if key in missing and v is not None:
                            g.cells[key] = v
                            if not isinstance(v, str):
                                fmt = getattr(cell, "number_format", "General")
                                if fmt and fmt != "General":
                                    g.fmts[key] = fmt
                            recovered += 1
                return recovered, f"{recovered} value(s) were recalculated with LibreOffice."
            finally:
                wb.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("LibreOffice recalculation failed: %s", exc)
        return 0, "LibreOffice recalculation failed."


# --------------------------------------------------------------------------- #
# Named ranges
# --------------------------------------------------------------------------- #


def _defined_items(container) -> list[tuple[str, Any]]:
    try:
        return list(container.items())
    except Exception:  # noqa: BLE001
        return []


def _destinations(defn) -> list[tuple[str, str]]:
    try:
        return [(s, r) for s, r in defn.destinations]
    except Exception:  # noqa: BLE001
        return []


def _collect_names(wb) -> dict[tuple[str, int, int], str]:
    """Map ``(sheet, row, col)`` to a defined name (workbook- and sheet-scoped; ranges <= 50 cells)."""
    out: dict[tuple[str, int, int], str] = {}
    pairs: list[tuple[str, Any]] = _defined_items(getattr(wb, "defined_names", {}))
    for ws in wb.worksheets:
        pairs.extend(_defined_items(getattr(ws, "defined_names", {})))
    for nm, defn in pairs:
        try:
            for sheet, ref in _destinations(defn):
                c1, r1, c2, r2 = range_boundaries(str(ref).replace("$", ""))
                if None in (c1, r1, c2, r2) or (r2 - r1 + 1) * (c2 - c1 + 1) > _RANGE_MAX_CELLS:
                    continue
                single = (r1, c1) == (r2, c2)
                for r in range(r1, r2 + 1):
                    for c in range(c1, c2 + 1):
                        key = (sheet, r, c)
                        if key not in out or (single and out[key].startswith("\0")):
                            out[key] = nm if single else "\0" + nm
        except Exception:  # noqa: BLE001
            continue
    return {k: v.lstrip("\0") for k, v in out.items()}


# --------------------------------------------------------------------------- #
# Unit resolution
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=32768)
def _label_hint(text: str) -> UnitHint | None:
    """Token-level unit evidence in a label ("Margin (%)", "EV / EBITDA (x)", "Revenue ($m)")."""
    if not text:
        return None
    h = parse_unit_banner(text)
    return None if h.is_empty() else h


def _currency_only(h: UnitHint | None, label: str) -> UnitHint | None:
    """A label such as "Term loan B (USD)" names a currency, not a scale: it must merge with the
    banner scale ("in millions") instead of pinning the scale to "units".  Per-share labels keep it."""
    if h is None or not (h.currency and not h.scale and h.scale_known) or _PER_SHARE_RE.search(label or ""):
        return h
    from numcheck.matching.normalizer import _PER_UNIT_RE, is_unit_price_label

    if _PER_UNIT_RE.search(label or "") or is_unit_price_label(label or ""):
        return h   # "PPA price ($/MWh)", "52-week high (EUR)": a price keeps raw scale; the banner must not apply
    return UnitHint(dimension=h.dimension, scale="", currency=h.currency, scale_known=False,
                    per_share_exception=h.per_share_exception, source=h.source)


@dataclass
class _Unit:
    unit: str = "none"
    scale: str = ""
    scale_known: bool = False
    currency: str = ""
    pct_basis: str = ""


def _format_hint(fi: FormatInfo) -> UnitHint | None:
    dim = fi.dimension if fi.dimension in _REAL_DIMS else "none"
    literal = fi.scale if (fi.divisor == 1.0 and fi.scale) else ""
    if dim == "none" and not literal and not fi.currency:
        return None
    return UnitHint(dimension=dim, scale=literal, currency=fi.currency, scale_known=bool(literal))


_MONEY_COL_RE = re.compile(r"\b(?:proceeds|consideration|cost|price)\b|[$€£¥]", re.I)


def _resolve_unit(lc: LocCell, fi: FormatInfo, value: float, token: Any = None) -> _Unit:
    """First source that knows a field wins: format, unit column, row label, column label, block banner, sheet banner."""
    if is_fx_rate_label(lc.row_label) and fi.dimension not in ("percent",):
        # "Average GBP/USD" under a "$ in millions" banner is an exchange rate, not $1.27m.
        return _Unit(unit="none", scale="", scale_known=True, currency="")
    sources: list[UnitHint | None] = []
    if token is not None:       # numeric text such as "12.5%" or "$1,200m" states its own unit
        sources.append(UnitHint(dimension=token.dimension if token.dimension in _REAL_DIMS else "none",
                                scale=token.scale, currency=token.currency,
                                scale_known=bool(token.scale_known)))
    row_h = _currency_only(_label_hint(lc.row_label), lc.row_label)
    col_h = _currency_only(_label_hint(lc.col_label), lc.col_label)
    if row_h is not None and row_h.dimension == "count" and _MONEY_COL_RE.search(lc.col_label or ""):
        row_h = None   # "Primary shares" x "Gross proceeds": the column says money, the row names the item
    if explicit_currency_unit(lc.col_label) and col_h is not None and row_h is not None \
            and col_h.currency and row_h.currency and col_h.currency != row_h.currency:
        # "Senior EUR tranche" x "USD equiv. ($m)": the column's explicit unit states the value's currency;
        # the row merely names the instrument.
        row_h, col_h = col_h, row_h
    sources += [_format_hint(fi), lc.unit_hint, row_h, col_h, lc.banner, lc.sheet_banner]
    dim, currency, scale, scale_known, scale_src = "none", "", "", False, None
    dim_src = None
    for h in sources:
        if h is None:
            continue
        if dim == "none" and h.dimension in (*_REAL_DIMS, "count"):
            dim, dim_src = h.dimension, h
        if not currency and h.currency:
            currency = h.currency
            dim_src = dim_src or h
        if not scale_known and h.scale_known:
            scale, scale_known, scale_src = h.scale, True, h
    if dim == "none" and currency:
        dim = "currency"
    banner_only = dim_src is not None and (dim_src is lc.banner or dim_src is lc.sheet_banner)
    if (banner_only and dim == "currency" and fi.dimension in ("none",) and token is None
            and 0 < abs(value) < 1 and value != int(value)):
        # A General-format fraction (PoS 0.65, a 0.35 conversion rate) under a "$ in millions" banner is a
        # ratio, not $0.65m: leave the dimension unknown (wildcard) rather than force currency.
        return _Unit(unit="none", scale="", scale_known=False, currency="")
    if dim in ("percent", "multiple", "bps"):
        return _Unit(unit=dim, scale="", scale_known=True, currency="",
                     pct_basis=_pct_basis(dim, fi, value, token))
    if fi.divisor > 1.0 and not scale_known and fi.scale:
        scale, scale_known = DIVISOR_SCALE.get(fi.divisor, fi.scale), True
    if dim == "currency" and scale_src is not None and scale_src.per_share_exception \
            and _PER_SHARE_RE.search(lc.row_label or ""):
        scale, scale_known = "", True
    return _Unit(unit=dim, scale=scale, scale_known=scale_known, currency=currency if dim == "currency" else "")


def _pct_basis(dim: str, fi: FormatInfo, value: float, token: Any) -> str:
    if dim != "percent":
        return ""
    if fi.dimension == "percent":
        return "ratio"
    if token is not None:
        return "points"
    return "points" if abs(value) > 1.5 else ""


# --------------------------------------------------------------------------- #
# Fallback labelling
# --------------------------------------------------------------------------- #


def _fallback_cells(g: SheetGrid) -> list[LocCell]:
    """Per-cell nearest-label labelling used only if structure analysis crashed (still O(cells))."""
    out: list[LocCell] = []
    row_text: dict[int, str] = {}
    col_text: dict[int, str] = {}
    for (r, c) in sorted(g.cells):
        kind, payload = classify(g.cells[(r, c)])
        if kind == "text":
            row_text.setdefault(r, payload)
            col_text[c] = payload
        elif kind in ("num", "err", "ntext"):
            out.append(LocCell(row=r, col=c, kind=kind, payload=payload, fmt=g.fmts.get((r, c), ""),
                               row_label=row_text.get(r, ""), col_label=col_text.get(c, ""), section="",
                               unit_hint=None, banner=None, sheet_banner=None))
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


@dataclass
class _Tally:
    text_numbers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    uncached: dict[str, int] = field(default_factory=dict)
    uncalc_sheets: list[str] = field(default_factory=list)   # sheets that had uncached formulas before recalculation
    hidden_skipped: int = 0
    excluded: list[str] = field(default_factory=list)
    failed_cells: int = 0
    failed_rows: dict[str, int] = field(default_factory=dict)
    failed_sheets: list[str] = field(default_factory=list)


def _extract_sheets(wb_v, wb_f, real: str, name: str, streaming: bool, config: Config,
                    result: ModelExtraction) -> None:
    tally = _Tally()
    grids: dict[str, SheetGrid] = {}
    formula_sheets = {ws.title: ws for ws in wb_f.worksheets}
    for ws in wb_v.worksheets:
        if any(fnmatch.fnmatch(ws.title.lower(), pat.lower()) for pat in config.exclude_sheets):
            tally.excluded.append(ws.title)
            continue
        try:
            grids[ws.title] = _build_grid(ws, formula_sheets.get(ws.title, ws), streaming)
        except Exception as exc:  # noqa: BLE001 - a broken sheet never aborts the workbook
            tally.failed_sheets.append(ws.title)
            log.warning("could not read sheet %s: %s", ws.title, exc)

    recovered, recalc_note = 0, ""
    tally.uncalc_sheets = [t for t, g in grids.items() if _uncached(g)]
    if tally.uncalc_sheets:
        recovered, recalc_note = _recalculate(real, grids, streaming, name)
        if any(_uncached(g) for g in grids.values()):
            computed = evaluate_uncached(grids, _uncached)
            if computed:
                recovered += computed
                recalc_note = (recalc_note + " " if recalc_note else "") + (
                    f"{computed} value(s) were calculated by NumCheck's built-in formula engine.")

    names = _collect_names(wb_v)
    for title, g in grids.items():
        tally.uncached[title] = len(_uncached(g))
        _emit_sheet(g, name, config, names, tally, result)
        try:
            result.texts.extend(_figure_texts(g, name, config))
        except Exception:  # noqa: BLE001 - optional evidence only
            log.debug("text cells skipped on %s", title, exc_info=True)

    _add_warnings(result, name, config, tally, recovered, recalc_note)


_DIGIT_RE = re.compile(r"\d")
_MAX_FIGURE_TEXTS = 20000


def _figure_texts(g: SheetGrid, workbook: str, config: Config) -> list[ModelText]:
    """Text cells that contain a digit ("Revolving credit facility ($250m)", "S+275", "6.375%").

    Kept as evidence for deck figures that a workbook states in words rather than in a value cell.
    """
    out: list[ModelText] = []
    by_row: dict[int, list[tuple[int, str]]] = defaultdict(list)
    by_col: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for (r, c), v in g.cells.items():
        if isinstance(v, str) and v.strip():
            by_row[r].append((c, v.strip()))
            by_col[c].append((r, v.strip()))
    for lst in (*by_row.values(), *by_col.values()):
        lst.sort()
    for r, items in by_row.items():
        for c, text in items:
            if not _DIGIT_RE.search(text) or text.startswith("=") or len(text) > 160:
                continue
            hidden = g.hidden or r in g.hidden_rows or c in g.hidden_cols
            if hidden and not config.include_hidden:
                continue
            row_label = next((t for cc, t in items if cc < c), "")
            col_label = next((t for rr, t in reversed(by_col[c]) if r - 15 <= rr < r and not _DIGIT_RE.search(t)), "")
            out.append(ModelText(workbook=workbook, sheet_name=g.name, cell_address=f"{get_column_letter(c)}{r}",
                                 text=text, row=r, col=c, row_label=row_label, col_label=col_label, hidden=hidden))
            if len(out) >= _MAX_FIGURE_TEXTS:
                return out
    return out


def _emit_sheet(g: SheetGrid, workbook: str, config: Config, names: dict, tally: _Tally,
                result: ModelExtraction) -> None:
    title = g.name
    priority = 0.5 if any(fnmatch.fnmatch(title.lower(), pat.lower()) for pat in config.deprioritized_sheets) else 1.0
    info = SheetInfo(name=title, hidden=g.hidden, priority=priority,
                     formula_count=len(g.formulas), uncached_formula_count=tally.uncached.get(title, 0))
    try:
        layout = analyze_sheet(g)
    except Exception as exc:  # noqa: BLE001
        log.warning("structure detection failed on %s: %s", title, exc)
        tally.failed_sheets.append(title)
        layout = SheetLayout(cells=_fallback_cells(g))
    if layout.failed_rows:
        tally.failed_rows[title] = layout.failed_rows
    info.header_row, info.label_col, info.unit_hint_text = layout.header_row, layout.label_col, layout.unit_hint_text

    cells = list(layout.cells)
    cells += _named_extras(g, layout, names)
    seen: set[tuple[int, int]] = set()
    for lc in cells:
        if (lc.row, lc.col) in seen:
            continue
        seen.add((lc.row, lc.col))
        hidden = g.hidden or lc.row in g.hidden_rows or lc.col in g.hidden_cols
        if hidden and not config.include_hidden:
            tally.hidden_skipped += 1
            continue
        try:
            mv = _to_model_value(lc, g, workbook, priority, hidden, names.get((title, lc.row, lc.col), ""), tally)
        except Exception as exc:  # noqa: BLE001
            tally.failed_cells += 1
            log.warning("skipped %s!%s%d: %s", title, get_column_letter(lc.col), lc.row, exc)
            continue
        if mv is None:
            continue
        result.values.append(mv)
        info.value_count += 1
        info.error_count += mv.is_error
    result.sheets.append(info)


def _named_extras(g: SheetGrid, layout: SheetLayout, names: dict) -> list[LocCell]:
    """Named numeric cells the layout did not label (a lone input cell): label them by their name."""
    have = {(c.row, c.col) for c in layout.cells}
    extra: list[LocCell] = []
    for (sheet, r, c), nm in names.items():
        if sheet != g.name or (r, c) in have or (r, c) not in g.cells:
            continue
        kind, payload = classify(g.cells[(r, c)])
        if kind in ("num", "err"):
            extra.append(LocCell(row=r, col=c, kind=kind, payload=payload, fmt=g.fmts.get((r, c), ""),
                                 row_label=nm.replace("_", " ").replace(".", " "), col_label="", section="",
                                 unit_hint=None, banner=None, sheet_banner=None))
    return extra


def _to_model_value(lc: LocCell, g: SheetGrid, workbook: str, priority: float, hidden: bool,
                    named: str, tally: _Tally) -> ModelValue | None:
    fi = parse_number_format(lc.fmt)
    if fi.dimension == "date":
        return None
    token = None
    error_text = ""
    if lc.kind == "err":
        value, error_text = float("nan"), str(lc.payload)
    elif lc.kind == "ntext":
        token = lc.payload
        value = float(token.value)
        tally.text_numbers[g.name] += 1
    else:
        value = float(lc.payload)
        if not math.isfinite(value):
            return None
    unit = _resolve_unit(lc, fi, 0.0 if error_text else value, token)
    addr = f"{get_column_letter(lc.col)}{lc.row}"
    row_period = period_key(lc.row_label)
    if row_period and "mo" in row_period and re.sub(r"\b[a-z]{3,9}[-\s/']+\d{2,4}\b", "", lc.row_label,
                                                    flags=re.I).strip(" -/"):
        row_period = ""   # "Norvik Logistics Mar-2019": an entry/maturity date attribute, not the row's period
    col_period = header_date_period(lc.col_label) or header_period_key(lc.col_label)
    if (row_period and col_period and row_period != col_period
            and re.fullmatch(r"(?:fy|cy|y)\d{2}", row_period) and re.fullmatch(r"(?:fy|cy|y)\d{2}", col_period)):
        # "Implied EV / FY24A Adj. EBITDA" in a summary block below a FY25E projection header: the row
        # names its own year, the column header is just the nearest one above.
        col_period = ""
    period = col_period or row_period or period_key(lc.section)
    return ModelValue(
        sheet_name=g.name, cell_address=addr, value=value,
        row_label=lc.row_label, col_label=lc.col_label, named_range=named,
        formula=g.formulas.get((lc.row, lc.col), ""),
        unit=unit.unit, scale=unit.scale, period=period or "",
        workbook=workbook, row=lc.row, col=lc.col, number_format=lc.fmt,
        display_decimals=fi.decimals, display_divisor=fi.divisor, pct_basis=unit.pct_basis,
        currency=unit.currency, scale_known=unit.scale_known, hidden=hidden, sheet_priority=priority,
        section_label=lc.section, is_error=bool(error_text), error_text=error_text,
        uid=f"{workbook}|{g.name}!{addr}",
    )


def _add_warnings(result: ModelExtraction, name: str, config: Config, t: _Tally, recovered: int,
                  recalc_note: str) -> None:
    w = result.warnings
    total_uncached = sum(t.uncached.values())
    original = total_uncached + recovered
    if original:
        sheets = sorted(set(t.uncalc_sheets) or {s for s, n in t.uncached.items() if n})
        sheets_txt = ", ".join(sheets) if sheets else "several sheets"
        if total_uncached:
            msg = (f"{original} formula cell(s) in {name} have no stored result ({sheets_txt}); "
                   f"{total_uncached} of them could not be calculated and are missing. {_UNCALC_HINT}.")
        else:
            msg = (f"{original} formula cell(s) in {name} had no stored result ({sheets_txt}); all were "
                   f"recalculated. To be certain, {_UNCALC_HINT[0].lower() + _UNCALC_HINT[1:]}.")
        if recalc_note:
            msg += f" {recalc_note}"
        w.append(ReviewWarning(code="uncalculated_formulas", message=msg, file=name,
                               sheet=sheets_txt, count=original))
    if t.text_numbers:
        n = sum(t.text_numbers.values())
        w.append(ReviewWarning(
            code="numbers_stored_as_text",
            message=(f"{n} number(s) in {name} are stored as text (e.g. '1,234'); they were parsed, but check "
                     f"them in sheet(s): {', '.join(sorted(t.text_numbers))}."),
            file=name, sheet=", ".join(sorted(t.text_numbers)), count=n))
    if t.excluded:
        w.append(ReviewWarning(
            code="sheets_excluded",
            message=f"Sheet(s) excluded by configuration and not checked: {', '.join(t.excluded)}.",
            file=name, sheet=", ".join(t.excluded), count=len(t.excluded)))
    if t.hidden_skipped:
        w.append(ReviewWarning(
            code="hidden_excluded",
            message=(f"{t.hidden_skipped} value(s) on hidden sheets/rows/columns were excluded "
                     "(include_hidden is off)."),
            file=name, count=t.hidden_skipped))
    for sheet in t.failed_sheets:
        w.append(ReviewWarning(
            code="sheet_degraded",
            message=f"Sheet '{sheet}' could not be fully analysed; labels may be less accurate or values missing.",
            file=name, sheet=sheet))
    for sheet, n in t.failed_rows.items():
        w.append(ReviewWarning(code="rows_skipped", message=f"{n} row(s) on sheet '{sheet}' could not be analysed.",
                               file=name, sheet=sheet, count=n))
    if t.failed_cells:
        w.append(ReviewWarning(code="cells_skipped", message=f"{t.failed_cells} cell(s) could not be read.",
                               file=name, count=t.failed_cells))
