"""Extract labelled values from an Excel model (openpyxl)."""

from __future__ import annotations

import re

import openpyxl

from numcheck.matching.normalizer import detect_scale, extract_period, parse_unit_hint
from numcheck.models import ModelValue

_CELL_REF_RE = re.compile(r"^(?:'([^']+)'|([^!]+))!\$?([A-Z]{1,3}\$?\d+)$", re.IGNORECASE)


def extract_model(path: str) -> list[ModelValue]:
    wb_values = openpyxl.load_workbook(path, data_only=True, read_only=False)
    wb_formulas = openpyxl.load_workbook(path, data_only=False, read_only=False)
    named = _named_ranges(wb_values)
    out: list[ModelValue] = []

    for ws in wb_values.worksheets:
        ws_f = wb_formulas[ws.title] if ws.title in wb_formulas.sheetnames else None
        header_dim, header_scale = _detect_sheet_units(ws)
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                if isinstance(cell.value, bool):
                    continue
                if not isinstance(cell.value, (int, float)):
                    continue
                r, c = cell.row, cell.column
                row_label = _left_labels(ws, r, c)
                col_label = _up_labels(ws, r, c)
                formula = ""
                if ws_f is not None:
                    fv = ws_f.cell(r, c).value
                    if isinstance(fv, str) and fv.startswith("="):
                        formula = fv
                dim, scale = _cell_unit(cell, row_label, col_label, header_dim, header_scale)
                period = extract_period(f"{col_label} {row_label}")
                named_range = named.get((ws.title.lower(), cell.coordinate), "")
                out.append(
                    ModelValue(
                        sheet_name=ws.title,
                        cell_address=cell.coordinate,
                        value=float(cell.value),
                        row_label=row_label,
                        col_label=col_label,
                        named_range=named_range,
                        formula=formula,
                        unit=dim,
                        scale=scale,
                        period=period,
                    )
                )
    return out


def _left_labels(ws, row: int, col: int) -> str:
    labels: list[str] = []
    for c in range(col - 1, 0, -1):
        v = ws.cell(row=row, column=c).value
        if v is None or str(v).strip() == "":
            break
        if isinstance(v, str):
            labels.append(v.strip())
    return " ".join(reversed(labels))


def _up_labels(ws, row: int, col: int) -> str:
    labels: list[str] = []
    for r in range(row - 1, 0, -1):
        v = ws.cell(row=r, column=col).value
        if v is None or str(v).strip() == "":
            break
        if isinstance(v, str):
            labels.append(v.strip())
    return " ".join(reversed(labels))


def _named_ranges(wb) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for name, defn in wb.defined_names.items():
        parsed = _parse_cell_ref(defn.attr_text)
        if parsed:
            out[parsed] = name
    return out


def _parse_cell_ref(dest: str) -> tuple[str, str] | None:
    m = _CELL_REF_RE.match((dest or "").strip())
    if not m:
        return None
    sheet = (m.group(1) or m.group(2) or "").strip("'")
    coord = m.group(3).replace("$", "")
    return (sheet.lower(), coord)


def _dim_from_format(fmt: str) -> str:
    f = (fmt or "").lower()
    if "%" in f:
        return "percent"
    if "$" in f or "£" in f or "€" in f or "usd" in f:
        return "currency"
    if "x" in f:
        return "multiple"
    return "none"


def _detect_sheet_units(ws) -> tuple[str, str]:
    texts: list[str] = []
    for c in range(1, min(ws.max_column or 1, 20) + 1):
        v = ws.cell(row=1, column=c).value
        if v is not None:
            texts.append(str(v))
    for r in range(1, min(ws.max_row or 1, 20) + 1):
        v = ws.cell(row=r, column=1).value
        if v is not None:
            texts.append(str(v))
    joined = " ".join(texts)
    lower = joined.lower()
    dim = "none"
    if "%" in lower:
        dim = "percent"
    elif "x" in lower:
        dim = "multiple"
    elif "$" in lower or "usd" in lower:
        dim = "currency"
    scale = detect_scale(joined)
    return dim, scale


def _cell_unit(
    cell,
    row_label: str,
    col_label: str,
    header_dim: str,
    header_scale: str,
) -> tuple[str, str]:
    combined = f"{row_label} {col_label}"
    dim = _dim_from_format(cell.number_format)
    scale = ""
    if dim == "none":
        dim, scale = parse_unit_hint(combined)
    if dim == "none":
        dim = header_dim
    if dim == "currency" and scale == "":
        scale = detect_scale(cell.number_format) or detect_scale(combined) or header_scale
    return dim, scale
