"""Extract numbers from embedded PPTX chart XML.

Why XML instead of python-pptx's chart API: the high-level API only exposes
plots/series it understands (it rejects XY/bubble ``c:yVal`` values for
``series.values`` on some plot types, drops sparse points, and hides number
formats).  Reading ``c:numCache`` directly lets us handle every plot type with
one code path, honour ``idx`` on sparse points, and see the *displayed* number
format (data-label ``numFmt``), which defines the tolerance of each value.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace

from numcheck.matching.normalizer import UnitHint, parse_unit_banner, significant_digits
from numcheck.models import DeckNumber, ReviewWarning, Source
from numcheck.numfmt import FormatInfo, display_value, parse_number_format
from numcheck.periods import find_periods

_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS = {"c": _C, "a": _A}

PER_SHARE_RE = re.compile(
    r"\beps\b|\bdps\b|per[\s-]share|share\s+price|price\s+per\s+share|/\s?share|\bper\s+unit\b",
    re.I,
)
_CURRENCY_PREFIX = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def _local(el) -> str:
    tag = el.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


# --------------------------------------------------------------------------- #
# Unit hint merging (shared with the table extractor)
# --------------------------------------------------------------------------- #


def merge_unit_hints(hints: list[UnitHint | None]) -> UnitHint:
    """Combine hints in priority order; each field comes from the first hint that knows it.

    The merged ``source`` is the source of the first non-empty hint, so a unit
    inherited from the slide is reported as "slide" while a header unit is "table".
    """
    out = UnitHint()
    for h in hints:
        if h is None or h.is_empty():
            continue
        if not out.source:
            out.source = h.source
        if out.dimension == "none" and h.dimension != "none":
            out.dimension = h.dimension
        if not out.scale and h.scale:
            out.scale = h.scale
        if not out.currency and h.currency:
            out.currency = h.currency
        if h.scale_known:
            out.scale_known = True
        out.per_share_exception = out.per_share_exception or h.per_share_exception
    if out.scale:
        out.scale_known = True
    return out


# --------------------------------------------------------------------------- #
# XML readers
# --------------------------------------------------------------------------- #


def _pts(cache) -> dict[int, str]:
    """``{idx: text}`` for the ``c:pt`` children of a str/num cache (sparse-safe)."""
    out: dict[int, str] = {}
    if cache is None:
        return out
    for n, pt in enumerate(cache.findall("c:pt", _NS)):
        v = pt.find("c:v", _NS)
        if v is None or v.text is None:
            continue
        try:
            idx = int(pt.get("idx", n))
        except ValueError:
            idx = n
        out[idx] = v.text
    return out


def _ref_points(holder) -> dict[int, str]:
    """Points of a ``c:cat`` / ``c:val`` / ``c:xVal`` style element, any flavour."""
    if holder is None:
        return {}
    for path in ("c:numRef/c:numCache", "c:numLit", "c:strRef/c:strCache", "c:strLit"):
        pts = _pts(holder.find(path, _NS))
        if pts:
            return pts
    ml = holder.find("c:multiLvlStrRef/c:multiLvlStrCache", _NS)
    if ml is not None:
        levels = [_pts(lvl) for lvl in ml.findall("c:lvl", _NS)]  # innermost level first
        idxs = sorted({i for lv in levels for i in lv})
        out: dict[int, str] = {}
        for i in idxs:
            parts: list[str] = []
            for lv in reversed(levels):  # outermost first reads naturally: "FY24 Q3"
                t = lv.get(i)
                if t and (not parts or parts[-1] != t):
                    parts.append(t)
            out[i] = " ".join(parts)
        return out
    return {}


def _fmt_code(holder) -> str:
    if holder is None:
        return ""
    for path in ("c:numRef/c:numCache/c:formatCode", "c:numLit/c:formatCode"):
        el = holder.find(path, _NS)
        if el is not None and el.text:
            return el.text.strip()
    return ""


def _series_name(ser) -> str:
    tx = ser.find("c:tx", _NS)
    if tx is None:
        return ""
    parts = [t for t in (_pts(tx.find("c:strRef/c:strCache", _NS)).values())]
    if not parts:
        v = tx.find("c:v", _NS)
        if v is not None and v.text:
            parts = [v.text]
    return " ".join(p.strip() for p in parts).strip()


def _displayed_format(ser, plot) -> str:
    """Data-label number format that is NOT linked to source, series first, then plot."""
    for scope in (ser, plot):
        nf = scope.find("c:dLbls/c:numFmt", _NS)
        if nf is not None and nf.get("sourceLinked", "0").lower() not in ("1", "true"):
            code = nf.get("formatCode")
            if code:
                return code
    return ""


def _is_invisible_stack_base(ser, plot) -> bool:
    """Skip fully transparent padding in stacked bars and columns."""
    grouping = plot.find("c:grouping", _NS)
    if _local(plot) != "barChart" or grouping is None or grouping.get("val") not in ("stacked", "percentStacked"):
        return False
    if ser.find("c:spPr/a:noFill", _NS) is None or ser.find("c:spPr/a:ln/a:noFill", _NS) is None:
        return False
    if ser.findall("c:dPt", _NS) or plot.getparent().find("c:dTable", _NS) is not None:
        return False
    for owner in (ser, plot):
        labels = owner.find("c:dLbls", _NS)
        if labels is None:
            continue
        show_value = labels.find("c:showVal", _NS)
        if (show_value is not None and show_value.get("val", "true") not in ("0", "false")
                or labels.find("c:dLbl", _NS) is not None):
            return False
    return True


def _chart_title(chart_el) -> str:
    title = chart_el.find("c:chart/c:title", _NS)
    if title is None:
        return ""
    return " ".join(t.text or "" for t in title.iter(f"{{{_A}}}t")).strip()


def _effective_format(display_fmt: str, cache_fmt: str) -> FormatInfo:
    disp = parse_number_format(display_fmt) if display_fmt else None
    if disp is not None and not disp.is_general and disp.dimension != "text":
        return disp
    return parse_number_format(cache_fmt)


def _literal_decimals(v: float) -> int:
    text = f"{v:.6f}".rstrip("0").rstrip(".")
    return len(text.split(".", 1)[1]) if "." in text else 0


def _display_text(v: float, info: FormatInfo, decimals: int) -> str:
    body = f"{abs(v):.{decimals}f}" if decimals >= 0 else f"{abs(v):.10g}"
    if decimals >= 0 and abs(v) >= 1000:
        body = f"{abs(v):,.{decimals}f}"
    sign = "-" if v < 0 else ""
    if info.dimension == "percent":
        return f"{sign}{body}%"
    if info.dimension == "multiple":
        return f"{sign}{body}x"
    if info.dimension == "currency" and info.currency in _CURRENCY_PREFIX:
        return f"{sign}{_CURRENCY_PREFIX[info.currency]}{body}"
    return f"{sign}{body}"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def extract_chart_numbers(
    slide_idx: int,
    shape,
    hint: UnitHint | None = None,
    warnings: list | None = None,
    slide_title: str = "",
) -> list[DeckNumber]:
    """Every plotted value of a chart graphic frame as :class:`DeckNumber` objects.

    Units resolve in this order, per attribute: displayed/cached number format ->
    series-name banner -> chart-title banner -> slide hint.  Percent caches are
    ratios and are multiplied by 100 (deck percents are points).
    """
    warnings = warnings if warnings is not None else []
    chart = shape.chart
    chart_space = chart._chartSpace
    shape_id = str(shape.shape_id)
    title_text = _chart_title(chart_space)
    title_hint = parse_unit_banner(title_text) if title_text else UnitHint()
    title_hint = replace(title_hint, source="series")
    slide_hint = replace(hint, source="slide") if hint is not None else UnitHint()
    title_periods = [p.key for p, _s, _e in find_periods(title_text)]

    plot_area = chart_space.find("c:chart/c:plotArea", _NS)
    plots = [el for el in (plot_area if plot_area is not None else []) if _local(el).endswith("Chart")]
    out: list[DeckNumber] = []
    empty_series = 0
    hidden_series = 0
    series_counter = -1
    workbook = None
    workbook_tried = False

    for plot in plots:
        plot_type = _local(plot)
        for ser in plot.findall("c:ser", _NS):
            series_counter += 1
            try:
                if _is_invisible_stack_base(ser, plot):
                    hidden_series += 1
                    continue
                name = _series_name(ser)
                val_holder = ser.find("c:val", _NS)
                if val_holder is None:
                    val_holder = ser.find("c:yVal", _NS)
                cat_holder = ser.find("c:cat", _NS)
                if cat_holder is None:
                    cat_holder = ser.find("c:xVal", _NS)
                vals = _ref_points(val_holder)
                cats = _ref_points(cat_holder)
                cache_fmt = _fmt_code(val_holder)
                if not vals:
                    # Fallback: no numCache. Try the embedded workbook the chart was built from.
                    if not workbook_tried:
                        workbook_tried = True
                        workbook = _embedded_workbook(chart)
                    if workbook is not None:
                        wb_name, cats_wb, vals = _series_from_workbook(workbook, ser)
                        name = name or wb_name
                        cats = cats or cats_wb
                if not vals:
                    empty_series += 1
                    continue
                out.extend(_series_numbers(
                    slide_idx, shape_id, plot_type, plot, ser, series_counter, name, cats, vals,
                    _effective_format(_displayed_format(ser, plot), cache_fmt),
                    series_hint=replace(parse_unit_banner(name), source="series") if name else UnitHint(),
                    title_hint=title_hint, slide_hint=slide_hint, title_periods=title_periods,
                    slide_title=slide_title,
                ))
            except Exception as exc:  # noqa: BLE001 - one bad series must not kill the chart
                warnings.append(ReviewWarning(
                    "chart_series_error", f"Could not read a chart series ({exc}).",
                    slide_number=slide_idx, count=1))
    if not out and (empty_series or not hidden_series):
        warnings.append(ReviewWarning(
            "chart_no_cache",
            f"Chart on slide {slide_idx} has no cached values (linked or empty data), so its numbers were not checked. "
            "Open the deck in PowerPoint, refresh/edit the chart data and save, then rerun.",
            slide_number=slide_idx, count=max(empty_series, 1)))
    elif empty_series:
        warnings.append(ReviewWarning(
            "chart_no_cache",
            f"{empty_series} series of a chart on slide {slide_idx} had no cached values and were not checked.",
            slide_number=slide_idx, count=empty_series))
    return out


def _series_numbers(
    slide_idx: int, shape_id: str, plot_type: str, plot, ser, series_index: int, name: str,
    cats: dict[int, str], vals: dict[int, str], info: FormatInfo, *, series_hint: UnitHint,
    title_hint: UnitHint, slide_hint: UnitHint, title_periods: list[str], slide_title: str,
) -> list[DeckNumber]:
    per_share = bool(slide_hint.per_share_exception and PER_SHARE_RE.search(name or ""))
    dim, scale, scale_known, currency, source = _resolve_units(
        info, [series_hint, title_hint, slide_hint], per_share)
    name_periods = [p.key for p, _s, _e in find_periods(name)]
    out: list[DeckNumber] = []
    for idx in sorted(vals):
        try:
            raw_val = float(vals[idx])
        except (TypeError, ValueError):
            continue  # "#N/A" or text in the cache
        if raw_val != raw_val:
            continue
        shown = round(display_value(raw_val, info), 10)
        cat = cats.get(idx, "")
        found = find_periods(cat)
        period = found[0][0].key if found else (name_periods[0] if name_periods else (
            title_periods[0] if title_periods else ""))
        decimals = info.decimals
        if decimals < 0:
            # General/unknown format: the cache string's own precision is the best
            # evidence of displayed precision (typed "1250" -> +/-0.5; a linked cache
            # "1203.4123" stays precise). Capped so float noise is not "precision".
            decimals = _literal_decimals(shown)
        raw_text = _display_text(shown, replace(info, dimension=dim), decimals)
        digits_src = f"{abs(shown):.{decimals}f}" if decimals >= 0 else f"{abs(shown):.10g}"
        scatter = "scatter" in plot_type.lower() or "bubble" in plot_type.lower()
        out.append(DeckNumber(
            slide_number=slide_idx, shape_id=shape_id, source=Source.CHART,
            raw_text=raw_text, value=shown, unit=dim, scale=scale, period=period,
            # On XY charts the "category" is the x value: it identifies the other axis, not the point.
            label=name.strip() if scatter and name else f"{name} {cat}".strip(),
            context=(f"chart series '{name}', x={cat}" if scatter else f"chart series '{name}', category '{cat}'"),
            chart_series_name=name, significant=significant_digits(digits_src),
            decimals=decimals, currency=currency, comparator="=", unit_source=source,
            scale_known=scale_known, slide_title=slide_title, row_label=name, col_label=cat,
            chart_type=plot_type, series_index=series_index, point_index=idx,
            uid=f"s{slide_idx}-{shape_id}-c{series_index}-{idx}",
        ))
    return out


def _resolve_units(
    info: FormatInfo, hints: list[UnitHint], per_share: bool
) -> tuple[str, str, bool, str, str]:
    """(dimension, scale, scale_known, currency, unit_source) following the documented order."""
    dim = info.dimension if info.dimension in ("currency", "percent", "multiple", "bps") else "none"
    source = "chart_format" if dim != "none" else ""
    currency = info.currency
    scale = info.scale
    scale_known = bool(info.scale)
    for h in hints:
        if h.is_empty():
            continue
        if dim == "none" and h.dimension != "none":
            dim, source = h.dimension, source or h.source
        if not currency and h.currency:
            currency = h.currency
            if dim == "none":
                dim, source = "currency", source or h.source
        if not scale and not scale_known and h.scale and not per_share:
            scale, scale_known = h.scale, True
            source = source or h.source
        elif not scale_known and h.scale_known and not per_share:
            scale_known = True
    if per_share:
        scale, scale_known = "", True
    if dim in ("percent", "multiple", "bps"):
        scale, scale_known = "", True
        currency = ""
    return dim, scale, scale_known, currency, source


# --------------------------------------------------------------------------- #
# Embedded workbook fallback
# --------------------------------------------------------------------------- #


def _embedded_workbook(chart):
    """openpyxl workbook from the chart's embedded xlsx (``None`` when absent/unreadable)."""
    try:
        blob = chart.part.chart_workbook.xlsx_part.blob
        from openpyxl import load_workbook

        return load_workbook(io.BytesIO(blob), data_only=True, read_only=False)
    except Exception:  # noqa: BLE001
        return None


_REF_RE = re.compile(r"^(?:'((?:[^']|'')+)'|([^!]+))!(.+)$")


def _range_values(wb, formula: str) -> list:
    m = _REF_RE.match((formula or "").strip())
    if not m:
        return []
    sheet = (m.group(1) or m.group(2)).replace("''", "'")
    if sheet not in wb.sheetnames:
        return []
    cells = wb[sheet][m.group(3).replace("$", "")]
    if not isinstance(cells, tuple):
        return [cells.value]
    flat: list = []
    for row in cells:
        flat.extend(c.value for c in row) if isinstance(row, tuple) else flat.append(row.value)
    return flat


def _series_from_workbook(wb, ser) -> tuple[str, dict[int, str], dict[int, str]]:
    def ref(path: str) -> str:
        el = ser.find(path, _NS)
        return el.text if el is not None and el.text else ""

    vals_raw = _range_values(wb, ref("c:val/c:numRef/c:f") or ref("c:yVal/c:numRef/c:f"))
    cats_raw = _range_values(wb, ref("c:cat/c:strRef/c:f") or ref("c:cat/c:numRef/c:f")
                             or ref("c:xVal/c:numRef/c:f") or ref("c:xVal/c:strRef/c:f"))
    name_raw = _range_values(wb, ref("c:tx/c:strRef/c:f"))
    vals = {i: str(v) for i, v in enumerate(vals_raw) if isinstance(v, (int, float)) and not isinstance(v, bool)}
    cats = {i: str(v) for i, v in enumerate(cats_raw) if v is not None}
    name = str(name_raw[0]) if name_raw and name_raw[0] is not None else ""
    return name, cats, vals


def unit_from_format(fmt: str) -> tuple[str, str]:
    """Legacy helper: (dimension, scale) for a chart format code."""
    info = parse_number_format(fmt)
    dim = info.dimension if info.dimension in ("currency", "percent", "multiple", "bps") else "none"
    return dim, info.scale
