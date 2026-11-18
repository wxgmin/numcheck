"""Extract numbers from embedded PPTX chart XML."""

from __future__ import annotations

from numcheck.matching.normalizer import detect_scale, extract_period, parse_unit_hint, significant_digits
from numcheck.models import DeckNumber, Source

_C_NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}


def extract_chart_numbers(slide_idx: int, shape) -> list[DeckNumber]:
    chart = shape.chart
    chart_space = chart._chartSpace
    out: list[DeckNumber] = []
    for ser in chart_space.findall(".//c:ser", _C_NS):
        name = _first_text(ser, ".//c:tx//c:v") or ""
        cats = [v.text for v in ser.findall(".//c:cat//c:pt/c:v", _C_NS) if v.text is not None]
        vals = [v.text for v in ser.findall(".//c:val//c:pt/c:v", _C_NS) if v.text is not None]
        fmt = ""
        for num_fmt in ser.findall(".//c:numFmt", _C_NS):
            fc = num_fmt.get("formatCode")
            if fc:
                fmt = fc
                break
        unit, scale = unit_from_format(fmt)
        if unit == "none" and name:
            hint_dim, hint_scale = parse_unit_hint(name)
            if hint_dim != "none":
                unit = hint_dim
                scale = hint_scale
        if scale == "" and name:
            scale = detect_scale(name)
        for i, vstr in enumerate(vals):
            try:
                value = float(vstr)
            except (TypeError, ValueError):
                continue
            if unit == "percent":
                # Chart caches store percent values as ratios (0.25); the rest of
                # the pipeline treats percent as points (25), so normalize here.
                value *= 100.0
            cat = cats[i] if i < len(cats) else ""
            label = f"{name} {cat}".strip()
            out.append(
                DeckNumber(
                    slide_number=slide_idx,
                    shape_id=str(shape.shape_id),
                    source=Source.CHART,
                    raw_text=vstr,
                    value=value,
                    unit=unit,
                    scale=scale,
                    period=extract_period(cat),
                    label=label,
                    context=f"chart series '{name}', category '{cat}'",
                    chart_series_name=name,
                    significant=significant_digits(vstr),
                )
            )
    return out


def _first_text(el, xpath: str) -> str:
    v = el.findtext(xpath, default="", namespaces=_C_NS)
    return (v or "").strip()


def unit_from_format(fmt: str) -> tuple[str, str]:
    """Infer dimension from a chart number format code (scale comes from the series name)."""
    f = (fmt or "").lower()
    if "%" in f:
        return ("percent", "")
    if "$" in f or "£" in f or "€" in f:
        return ("currency", "")
    if "x" in f:
        return ("multiple", "")
    return ("none", "")
