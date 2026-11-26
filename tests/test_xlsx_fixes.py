"""Focused tests for the XLSX structure/units fixes found by the industry scenarios."""

from __future__ import annotations

import datetime as dt

import openpyxl
from openpyxl.styles import Font

from numcheck.extraction.xlsx_extractor import extract_model_full


def _save(wb, tmp_path, name="m.xlsx"):
    p = tmp_path / name
    wb.save(p)
    return str(p)


def _vals(wb, tmp_path):
    return extract_model_full(_save(wb, tmp_path)).values


def _at(vals, addr):
    for v in vals:
        if v.cell_address == addr:
            return v
    raise AssertionError(f"no value at {addr}: {[v.cell_address for v in vals]}")


def _wb(title="S"):
    wb = openpyxl.Workbook()
    wb.active.title = title
    return wb, wb.active


# 1. mixed header rows ---------------------------------------------------------


def test_mixed_period_and_text_headers_keep_all_columns(tmp_path):
    wb, ws = _wb("Comps")
    hdr = ["Company", "Share price ($)", "Market cap ($bn)", "EV ($bn)", "EV / Revenue NTM", "EV / EBITDA NTM",
           "LTM", "NTM"]
    for j, h in enumerate(hdr, start=1):
        ws.cell(5, j, h)
    for r, n in enumerate(["Alpha", "Beta"], start=6):
        ws.cell(r, 1, n)
        for j, v in enumerate([10.5, 20.0, 25.0, 4.6, 12.1, 3.0, 3.3], start=2):
            ws.cell(r, j, v)
    vals = _vals(wb, tmp_path)
    assert _at(vals, "E6").col_label == "EV / Revenue NTM"
    assert _at(vals, "F6").col_label == "EV / EBITDA NTM"
    assert _at(vals, "C6").col_label == "Market cap ($bn)"
    assert _at(vals, "G6").col_label == "LTM"
    assert _at(vals, "H6").col_label == "NTM"


# 2. units rows / null markers ------------------------------------------------


def test_units_row_under_text_header(tmp_path):
    wb, ws = _wb("Comps")
    for j, h in enumerate(["Company", "EV / EBITDA CY2024", "EBITDA margin CY2024", "Net debt"], start=1):
        ws.cell(5, j, h)
    for j, u in enumerate(["x", "%", "$m"], start=2):
        ws.cell(6, j, u)
    rows = [("Alpha", 11.2, 0.241, 500.0), ("Beta", 10.1, 0.215, 300.0), ("Delta", "n.m.", "n.m.", "-")]
    for r, (n, *v) in enumerate(rows, start=7):
        ws.cell(r, 1, n)
        for j, x in enumerate(v, start=2):
            ws.cell(r, j, x)
    ws["B7"].number_format = ws["B8"].number_format = '0.0"x"'
    vals = _vals(wb, tmp_path)
    b7 = _at(vals, "B7")
    assert b7.col_label == "EV / EBITDA CY2024" and b7.row_label == "Alpha" and b7.unit == "multiple"
    c8 = _at(vals, "C8")
    assert c8.col_label == "EBITDA margin CY2024"
    assert c8.unit == "percent"
    d7 = _at(vals, "D7")
    assert d7.col_label == "Net debt" and d7.unit == "currency" and d7.scale == "m"
    for v in vals:
        assert v.col_label.lower() not in {"x", "%", "$m", "n.m.", "-"}
        assert v.row_label.lower() not in {"n.m.", "-"}


# 3. transposed with units row -------------------------------------------------


def test_transposed_sheet_with_units_row(tmp_path):
    wb, ws = _wb("Cash flow")
    ws["A1"] = "Cash flow summary"
    for c, h in zip("ABCD", ["Period", "Capex", "D&A", "Free cash flow"]):
        ws[f"{c}3"] = h
    ws["A4"], ws["B4"], ws["C4"], ws["D4"] = "Unit", "$m", "$m", "$m"
    for r, (p, a, b, c) in enumerate([("FY2023", 85.6, 58.2, 142.3), ("FY2024", 98.2, 64.5, 171.8)], start=5):
        ws.cell(r, 1, p)
        for j, v in enumerate((a, b, c), start=2):
            ws.cell(r, j, v)
    vals = _vals(wb, tmp_path)
    capex = _at(vals, "B6")
    assert capex.col_label == "Capex" and capex.row_label == "FY2024"
    assert capex.period == "fy24"
    assert capex.unit == "currency" and capex.scale == "m"
    assert _at(vals, "D5").col_label == "Free cash flow"


# 4. periods must not leak across blocks ---------------------------------------


def test_period_header_does_not_leak_into_valuation_block(tmp_path):
    wb, ws = _wb("DCF")
    ws["A7"] = "Projections"
    for j, h in enumerate(["FY2025E", "FY2026E", "FY2027E"]):
        ws.cell(8, 3 + j, h)
    ws["A9"], ws["B9"] = "Unlevered free cash flow", "$m"
    for j, v in enumerate([198.5, 224.1, 249.6]):
        ws.cell(9, 3 + j, v)
    ws["A11"] = "Valuation"
    for r, (lab, v) in enumerate([("PV of free cash flows", 1009.2), ("Enterprise value", 5432.1)], start=12):
        ws.cell(r, 1, lab)
        ws.cell(r, 2, "$m")
        ws.cell(r, 3, v)
    vals = _vals(wb, tmp_path)
    assert _at(vals, "C9").period == "fy25"
    ev = _at(vals, "C13")
    assert ev.period == "" and ev.col_label == ""
    assert ev.section_label == "Valuation"


def test_shared_header_sections_still_inherit_period(tmp_path):
    wb, ws = _wb("Model")
    for j, h in enumerate(["FY2024", "FY2025E"]):
        ws.cell(4, 3 + j, h)
    ws["A5"], ws["C5"], ws["D5"] = "Revenue", 100.0, 110.0
    ws["A7"] = "Balance sheet"
    ws["A8"], ws["C8"], ws["D8"] = "Cash", 10.0, 12.0
    vals = _vals(wb, tmp_path)
    assert _at(vals, "D8").period == "fy25"


# 5. section label ---------------------------------------------------------------


def test_note_row_is_not_section_label(tmp_path):
    wb, ws = _wb("Model")
    ws["A1"] = "Operating model"
    ws["A1"].font = Font(bold=True)
    ws["A2"] = "Fiscal year ends 30 June. FY2024 = Jul-23 to Jun-24; CY = calendar year (Jan-Dec)."
    ws["C4"], ws["D4"] = "FY2024", "FY2025E"
    ws["A5"], ws["C5"], ws["D5"] = "Revenue", 100.0, 110.0
    vals = _vals(wb, tmp_path)
    assert _at(vals, "C5").section_label == "Operating model"


def test_short_note_with_period_is_not_section(tmp_path):
    wb, ws = _wb("Model")
    ws["A1"] = "Operating model"
    ws["A2"] = "Fiscal year ends 30 June."
    ws["C4"], ws["D4"] = "FY2024", "FY2025E"
    ws["A5"], ws["C5"], ws["D5"] = "Revenue", 100.0, 110.0
    assert _at(_vals(wb, tmp_path), "C5").section_label == "Operating model"


# 6. currency-only hint merges with banner scale -------------------------------


def test_currency_only_label_merges_with_banner_scale(tmp_path):
    wb, ws = _wb("Debt")
    ws["A1"] = "Capital structure"
    ws["A2"] = "(in millions)"
    ws["C4"], ws["D4"] = "FY2024", "FY2025E"
    ws["A5"], ws["C5"], ws["D5"] = "Term loan B (USD)", 400.0, 380.0
    ws["A6"], ws["C6"], ws["D6"] = "Eurobond (EUR)", 250.0, 250.0
    vals = _vals(wb, tmp_path)
    tl = _at(vals, "C5")
    assert tl.scale == "m" and tl.scale_known and tl.currency == "USD"
    eb = _at(vals, "C6")
    assert eb.scale == "m" and eb.currency == "EUR"


# 7. monthly / quarterly headers -----------------------------------------------


def test_monthly_and_quarterly_headers(tmp_path):
    for hdrs, keys in [
        (["Jan-24", "Feb-24", "Sep-24"], ["y24mo01", "y24mo02", "y24mo09"]),
        (["Q1-24", "Q2-24", "Q3-24"], ["y24q1", "y24q2", "y24q3"]),
        (["Q1-2024", "Q2-2024", "Q3-2024"], ["y24q1", "y24q2", "y24q3"]),
        (["1Q24", "2Q24", "3Q24"], ["y24q1", "y24q2", "y24q3"]),
    ]:
        wb, ws = _wb("Q")
        for j, h in enumerate(hdrs):
            ws.cell(4, 3 + j, h)
        ws["A5"] = "Revenue"
        for j in range(3):
            ws.cell(5, 3 + j, 10.0 + j)
        vals = _vals(wb, tmp_path)
        assert [_at(vals, f"{c}5").period for c in "CDE"] == keys, hdrs
        assert not any(v.row == 4 for v in vals)


def test_date_typed_monthly_headers(tmp_path):
    wb, ws = _wb("M")
    for j in range(3):
        c = ws.cell(4, 3 + j, dt.datetime(2024, 1 + j, 1))
        c.number_format = "mmm-yy"
    ws["A5"] = "Revenue"
    for j in range(3):
        ws.cell(5, 3 + j, 10.0 + j)
    vals = _vals(wb, tmp_path)
    assert [_at(vals, f"{c}5").period for c in "CDE"] == ["y24mo01", "y24mo02", "y24mo03"]


# 8. capex label must not make the row percent ---------------------------------


def test_growth_word_inside_label_is_not_percent(tmp_path):
    wb, ws = _wb("CF")
    ws["C4"], ws["D4"] = "FY2024", "FY2025E"
    ws["A5"], ws["C5"], ws["D5"] = "Fund capex and growth investments", 45.0, 50.0
    ws["A6"], ws["C6"], ws["D6"] = "Revenue growth", 0.1, 0.12
    ws["C6"].number_format = ws["D6"].number_format = "0.0%"
    vals = _vals(wb, tmp_path)
    assert _at(vals, "C5").unit != "percent"
    assert _at(vals, "C6").unit == "percent"


# 9. numeric years above quarter headers are not values ------------------------


def test_numeric_year_headers_over_quarters_not_values(tmp_path):
    wb, ws = _wb("Q")
    ws["C3"], ws["G3"] = 2024, 2025
    for j, h in enumerate(["Q1", "Q2", "Q3", "Q4", "Q1", "Q2"]):
        ws.cell(4, 3 + j, h)
    ws["A5"] = "Revenue"
    for j in range(6):
        ws.cell(5, 3 + j, 10.0 + j)
    vals = _vals(wb, tmp_path)
    assert not any(v.row in (3, 4) for v in vals)
    assert _at(vals, "C5").period == "y24q1"
    assert _at(vals, "H5").period == "y25q2"
