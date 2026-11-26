"""Excel report: Summary, Issues, Coverage (tick & tie), Warnings. Formula-injection safe."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from numcheck.models import Issue, ReviewResult
from numcheck.report import (
    STATUS_LABELS,
    as_result,
    match_rows,
    sort_issues,
    type_label,
)

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
_HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_SEV_FILL = {
    "high": PatternFill("solid", fgColor="F3D9D6"),
    "medium": PatternFill("solid", fgColor="F6E7C1"),
    "low": PatternFill("solid", fgColor="E2E6EB"),
}
_STATUS_FILL = {
    "mismatch": _SEV_FILL["high"], "stale": _SEV_FILL["medium"],
    "ambiguous": _SEV_FILL["medium"], "unmatched": _SEV_FILL["low"],
}
_THIN = Side(style="thin", color="DCD9D0")
_WRAP = Alignment(wrap_text=True, vertical="top")
_TOP = Alignment(vertical="top")


def _put(ws: Worksheet, row: int, col: int, value: Any):
    """Write a value; any string that could be read as a formula is stored as text."""
    cell = ws.cell(row=row, column=col)
    if isinstance(value, str) and value.startswith(_DANGEROUS):
        cell.value = value
        cell.data_type = "s"
        cell.quotePrefix = True
    else:
        cell.value = value
    return cell


def _header(ws: Worksheet, headers: list[str], widths: list[int]) -> None:
    for c, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = _put(ws, 1, c, h)
        cell.fill, cell.font = _HEADER_FILL, _HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _finish_filter(ws: Worksheet, ncols: int) -> None:
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}{max(ws.max_row, 1)}"


def _summary_sheet(ws: Worksheet, res: ReviewResult) -> None:
    ws.title = "Summary"
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 60
    rows: list[tuple[str, Any]] = [("NumCheck review", "")]
    inp = res.inputs or {}
    rows += [
        ("Deck", inp.get("deck", "")),
        ("Models", ", ".join(str(m) for m in inp.get("models", []) or [])),
        ("Previous model", inp.get("model_old", "") or ""),
        ("Run time (s)", round(res.elapsed, 2)),
        ("", ""),
        ("Issues", len(res.issues)),
    ]
    for sev in ("high", "medium", "low"):
        rows.append((f"  {sev.capitalize()}", sum(1 for i in res.issues if i.severity.value == sev)))
    cov = res.coverage or {}
    rows.append(("", ""))
    rows.append(("Coverage", ""))
    for key, label in (("checkable", "Figures reviewed"), ("verified", "Verified"), ("verified_pct", "Verified %"),
                       ("matched", "Matched"), ("matched_value_only", "Matched by value only"),
                       ("mismatch", "Mismatch"), ("stale", "Stale"), ("ambiguous", "Ambiguous"),
                       ("unmatched", "Unmatched")):
        if key in cov:
            rows.append((label, cov[key]))
    for reason, n in (cov.get("skipped") or {}).items():
        rows.append((f"  Skipped: {reason}", n))
    if res.settings:
        rows += [("", ""), ("Settings", "")]
        rows += [(f"  {k}", v if isinstance(v, (int, float, str)) else str(v)) for k, v in res.settings.items()]
    for r, (a, b) in enumerate(rows, start=1):
        ca, cb = _put(ws, r, 1, a), _put(ws, r, 2, b)
        cb.alignment = Alignment(horizontal="left")
        if a in ("NumCheck review", "Issues", "Coverage", "Settings"):
            ca.font = Font(bold=True, size=13 if r == 1 else 11)


def _issues_sheet(ws: Worksheet, issues: list[Issue]) -> None:
    ws.title = "Issues"
    headers = ["#", "Severity", "Slide", "Type", "Metric", "Deck value", "Expected", "Suggestion",
               "Difference", "Tolerance", "Source cell", "Confidence", "Message"]
    _header(ws, headers, [6, 10, 7, 24, 26, 14, 14, 16, 12, 12, 34, 11, 80])
    for r, i in enumerate(sort_issues(issues), start=2):
        vals = [i.id, i.severity.value.upper(), i.slide_number, type_label(i.issue_type.value),
                i.deck_label or i.metric_key, i.deck_value, i.expected_value, i.suggestion,
                i.difference_text or i.difference, i.tolerance_text or i.tolerance, i.source_cell, i.confidence, i.message]
        for c, v in enumerate(vals, start=1):
            cell = _put(ws, r, c, v)
            cell.alignment = _WRAP if c == len(vals) else _TOP
        ws.cell(row=r, column=2).fill = _SEV_FILL.get(i.severity.value, _SEV_FILL["low"])
        ws.cell(row=r, column=2).font = Font(bold=True)
        ws.cell(row=r, column=12).number_format = "0%"
    _finish_filter(ws, len(headers))


def _coverage_sheet(ws: Worksheet, res: ReviewResult) -> None:
    ws.title = "Coverage"
    headers = ["Slide", "Deck value", "Label", "Status", "Source cell", "Model value", "Method", "Confidence",
               "Possible sources", "AI-suggested cell"]
    _header(ws, headers, [7, 16, 44, 18, 38, 16, 14, 11, 17, 38])
    for r, row in enumerate(match_rows(res), start=2):
        vals = [row["slide_number"], row["raw_text"], row["label"], STATUS_LABELS.get(row["status"], row["status"]),
                row["source_cell"], row["model_value"], row["method"], row["confidence"],
                row["possible_source_count"] or None, row["source_suggestion"]]
        for c, v in enumerate(vals, start=1):
            _put(ws, r, c, v).alignment = _TOP
        fill = _STATUS_FILL.get(row["status"])
        if fill:
            ws.cell(row=r, column=4).fill = fill
        ws.cell(row=r, column=8).number_format = "0%"
    _finish_filter(ws, len(headers))


def _warnings_sheet(ws: Worksheet, res: ReviewResult) -> None:
    ws.title = "Warnings"
    headers = ["Code", "Message", "File", "Slide", "Sheet", "Count"]
    _header(ws, headers, [26, 100, 28, 7, 20, 8])
    for r, w in enumerate(res.warnings, start=2):
        for c, v in enumerate([w.code, w.message, w.file, w.slide_number, w.sheet, w.count], start=1):
            _put(ws, r, c, v).alignment = _WRAP if c == 2 else _TOP
    _finish_filter(ws, len(headers))


def write_excel_report(result: "ReviewResult | list[Issue]", out_path: str | Path) -> None:
    res = as_result(result)
    wb = Workbook()
    _summary_sheet(wb.active, res)
    _issues_sheet(wb.create_sheet(), res.issues)
    _coverage_sheet(wb.create_sheet(), res)
    _warnings_sheet(wb.create_sheet(), res)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
