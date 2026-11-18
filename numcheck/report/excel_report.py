"""Excel report (styled, one row per issue)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from numcheck.models import Issue
from numcheck.report import SEVERITY_COLORS, summarize

HEADERS = [
    "ID", "Slide", "Issue Type", "Severity", "Metric",
    "Deck Value", "Expected Value", "Source Cell", "Message",
]


def write_excel_report(issues: list[Issue], out_path: str | Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Issues"

    header_fill = PatternFill("solid", fgColor="1F3864")
    header_font = Font(color="FFFFFF", bold=True)
    for c, h in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")

    for r, i in enumerate(issues, start=2):
        row = [
            i.id,
            i.slide_number,
            i.issue_type.value,
            i.severity.value,
            i.metric_key,
            i.deck_value,
            i.expected_value,
            i.source_cell,
            i.message,
        ]
        for c, val in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=val)

    # severity fill on the severity column
    for r, i in enumerate(issues, start=2):
        sev_cell = ws.cell(row=r, column=4)
        color = SEVERITY_COLORS.get(i.severity.value, "ffffff")
        sev_cell.fill = PatternFill("solid", fgColor=color.replace("#", ""))
        sev_cell.font = Font(bold=True)

    widths = [6, 7, 26, 10, 22, 18, 18, 22, 70]
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"

    summary = summarize(issues)
    s = wb.create_sheet("Summary")
    s.cell(row=1, column=1, value="Total issues").font = Font(bold=True)
    s.cell(row=1, column=2, value=summary["total"])
    row = 3
    s.cell(row=2, column=1, value="By severity").font = Font(bold=True)
    for k, v in summary["by_severity"].items():
        s.cell(row=row, column=1, value=k)
        s.cell(row=row, column=2, value=v)
        row += 1
    row += 1
    s.cell(row=row, column=1, value="By type").font = Font(bold=True)
    row += 1
    for k, v in summary["by_type"].items():
        s.cell(row=row, column=1, value=k)
        s.cell(row=row, column=2, value=v)
        row += 1

    wb.save(out_path)
