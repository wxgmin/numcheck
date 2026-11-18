"""Build a realistic comps + DCF + operating model with openpyxl."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

CURRENCY = "$#,##0"
PERCENT = "0.0%"
MULTIPLE = "0.0\"x\""

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def build_model(path: str | Path, implied_ev: float = 5800.0) -> None:
    wb = Workbook()

    _build_operating(wb)
    _build_comps(wb)
    _build_dcf(wb, implied_ev)

    # Named ranges for high-confidence label matching.
    for name, dest in {
        "Revenue_FY24": "Operating!$C$5",
        "EBITDA_FY24": "Operating!$C$6",
        "NetIncome_FY24": "Operating!$C$7",
        "Market_Cap": "Comps!$B$2",
        "EV": "Comps!$B$3",
        "Implied_EV": "DCF!$B$4",
    }.items():
        wb.defined_names.add(DefinedName(name, attr_text=dest))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _build_operating(wb: Workbook) -> None:
    ws = wb.active
    ws.title = "Operating"
    ws.append(["Metric", "FY23 ($m)", "FY24 ($m)"])
    rows = [
        ("Product Revenue", 1230, 1300),
        ("Services Revenue", 660, 700),
        ("Other Revenue", 340, 415),
        ("Total Revenue", 2230, 2415),
        ("EBITDA", 475, 512),
        ("Net Income", 350, 385),
    ]
    for label, fy23, fy24 in rows:
        ws.append([label, fy23, fy24])
    ws["A8"] = "EBITDA Margin"
    ws["B8"] = 0.213
    ws["C8"] = 0.212
    ws["A9"] = "Revenue Growth"
    ws["C9"] = 0.083

    for row in ws.iter_rows(min_row=2, max_row=7, min_col=2, max_col=3):
        for cell in row:
            cell.number_format = CURRENCY
    ws["B8"].number_format = PERCENT
    ws["C8"].number_format = PERCENT
    ws["C9"].number_format = PERCENT

    _style_header(ws, 1)
    _set_widths(ws, [26, 12, 12])


def _build_comps(wb: Workbook) -> None:
    ws = wb.create_sheet("Comps")
    ws.append(["Metric", "Acme Corp", "Peer 1", "Peer 2", "Peer 3", "Peer 4", "Peer 5"])
    rows = [
        ("Market Cap ($m)", 6234, 7100, 4800, 5200, 8300, 3900),
        ("EV ($m)", 5400, 7300, 5100, 5600, 8900, 4100),
        ("Revenue FY24 ($m)", 2415, 3400, 2200, 2700, 4100, 1800),
        ("EBITDA FY24 ($m)", 512, 720, 460, 580, 900, 370),
        ("P/E (x)", 18.4, 20.1, 15.2, 17.8, 22.3, 13.9),
        ("EV/EBITDA (x)", 10.5, 10.1, 11.1, 9.7, 9.9, 11.1),
        ("EV/Revenue (x)", 2.2, 2.1, 2.3, 2.1, 2.2, 2.3),
        ("Revenue Growth (%)", 0.083, 0.10, 0.06, 0.08, 0.12, 0.05),
    ]
    for label, *values in rows:
        ws.append([label, *values])

    for r in range(2, 6):
        for c in range(2, 8):
            ws.cell(row=r, column=c).number_format = CURRENCY
    for r in range(6, 9):
        for c in range(2, 8):
            ws.cell(row=r, column=c).number_format = MULTIPLE
    for c in range(2, 8):
        ws.cell(row=9, column=c).number_format = PERCENT

    _style_header(ws, 1)
    _set_widths(ws, [22] + [12] * 6)


def _build_dcf(wb: Workbook, implied_ev: float) -> None:
    ws = wb.create_sheet("DCF")
    ws.append(["Assumption", "Value"])
    ws.append(["WACC", 0.08])
    ws.append(["Terminal Growth", 0.025])
    ws.append(["Implied EV ($m)", implied_ev])

    ws.append([])
    ws.append(["FCF ($m)", "FY25 ($m)", "FY26 ($m)", "FY27 ($m)", "FY28 ($m)", "FY29 ($m)"])
    ws.append(["FCF", 640, 680, 720, 760, 790])

    ws["B2"].number_format = PERCENT
    ws["B3"].number_format = PERCENT
    ws["B4"].number_format = CURRENCY
    for c in range(2, 7):
        ws.cell(row=7, column=c).number_format = CURRENCY

    _style_header(ws, 1)
    _style_header(ws, 6)
    _set_widths(ws, [22] + [12] * 6)


def _style_header(ws, row: int) -> None:
    for cell in ws[row]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT


def _set_widths(ws, widths: list[float]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
