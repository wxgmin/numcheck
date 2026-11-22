"""Tests for numcheck.extraction.xlsx_extractor (models are built programmatically)."""

from __future__ import annotations

import datetime as dt
import math
import time

import openpyxl
import pytest
from openpyxl.styles import Alignment
from openpyxl.workbook.defined_name import DefinedName

from numcheck.config import Config
from numcheck.extraction import fileio, xlsx_extractor
from numcheck.extraction.xlsx_extractor import extract_model, extract_model_full
from numcheck.models import NumCheckInputError


def save(wb, tmp_path, name="model.xlsx"):
    p = tmp_path / name
    wb.save(p)
    return str(p)


def by_addr(res, sheet=None):
    return {(v.sheet_name, v.cell_address): v for v in res.values if sheet in (None, v.sheet_name)}


def find(res, label, period=None, sheet=None):
    out = [v for v in res.values if v.row_label.lower() == label.lower()
           and (period is None or v.period == period) and (sheet is None or v.sheet_name == sheet)]
    assert out, f"no value for {label!r}/{period}: {[(v.row_label, v.period) for v in res.values]}"
    return out[0]


def banker_workbook():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A1"] = "Income Statement"
    ws["A2"] = "($ in millions)"
    ws["C4"], ws["D4"], ws["E4"] = 2023, 2024, "2025E"
    rows = [
        ("Revenue", 1000.0, 1100.0, 1250.0),
        ("EBITDA", 250.0, 280.0, 330.0),
        ("EBITDA margin", 0.25, 0.2545, 0.264),
    ]
    for i, (lab, a, b, c) in enumerate(rows, start=6):
        ws.cell(i, 1, lab)
        for j, v in enumerate((a, b, c), start=3):
            ws.cell(i, j, v)
    for col in "CDE":
        ws[f"{col}6"].number_format = "#,##0.0"
        ws[f"{col}7"].number_format = "#,##0.0"
        ws[f"{col}8"].number_format = "0.0%"
    return wb


# ----------------------------------------------------------------- layouts


def test_banker_layout(tmp_path):
    res = extract_model_full(save(banker_workbook(), tmp_path))
    vals = by_addr(res)
    # header year cells are not values
    for addr in ("C4", "D4", "E4"):
        assert ("Model", addr) not in vals
    rev = vals[("Model", "D6")]
    assert rev.row_label == "Revenue"
    assert rev.period == "y24"
    assert rev.unit == "currency" and rev.currency == "USD"
    assert rev.scale == "m" and rev.scale_known
    assert rev.section_label == "Income Statement"
    assert vals[("Model", "C6")].period == "y23"
    assert vals[("Model", "E6")].period == "y25"
    m = vals[("Model", "D8")]
    assert m.row_label == "EBITDA margin"
    assert m.unit == "percent" and m.pct_basis == "ratio"
    assert m.display_decimals == 1
    assert m.workbook == "model.xlsx" and m.uid == "model.xlsx|Model!D8"
    assert (m.row, m.col) == (8, 4)
    info = res.sheets[0]
    assert info.header_row == 4 and info.label_col == 1
    assert "millions" in info.unit_hint_text
    assert info.value_count == 9


def test_extract_model_compat_returns_values(tmp_path):
    p = save(banker_workbook(), tmp_path)
    assert len(extract_model(p)) == 9


def test_capex_tax_opex_not_multiples_but_ev_ebitda_is(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["B1"], ws["C1"] = "Metric", 2023, 2024
    for i, lab in enumerate(["Capex", "Tax rate", "Opex", "EV / EBITDA (x)", "Total debt / EBITDA"], start=2):
        ws.cell(i, 1, lab)
        ws.cell(i, 2, 12.0)
        ws.cell(i, 3, 13.0)
    res = extract_model_full(save(wb, tmp_path))
    assert find(res, "Capex", "y24").unit != "multiple"
    assert find(res, "Opex", "y24").unit != "multiple"
    assert find(res, "Tax rate", "y24").unit != "multiple"
    assert find(res, "EV / EBITDA (x)", "y24").unit == "multiple"


def test_margin_percent_label_affects_only_that_row(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "($ in millions)"
    ws["B3"], ws["C3"] = 2023, 2024
    ws["A4"], ws["B4"], ws["C4"] = "Revenue", 500, 600
    ws["A5"], ws["B5"], ws["C5"] = "Margin (%)", 25, 30
    ws["A6"], ws["B6"], ws["C6"] = "Gross profit", 200, 230
    res = extract_model_full(save(wb, tmp_path))
    m = find(res, "Margin (%)", "y24")
    assert m.unit == "percent" and m.pct_basis == "points"
    assert find(res, "Revenue", "y24").unit == "currency"
    assert find(res, "Gross profit", "y24").unit == "currency"
    assert find(res, "Gross profit", "y24").scale == "m"


def test_number_formats_drive_units(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = "Item", "FY24"
    ws["A2"], ws["C2"] = "Pct", 0.125
    ws["C2"].number_format = "0.0%"
    ws["A3"], ws["C3"] = "Big", 2_400_000_000.0
    ws["C3"].number_format = "#,##0.0,,"
    ws["A4"], ws["C4"] = "Mult", 12.5
    ws["C4"].number_format = '0.0"x"'
    ws["A5"], ws["C5"] = "Dollars", 1234.5
    ws["C5"].number_format = "$#,##0.00"
    ws["A6"], ws["C6"] = "EurBn", 3.2
    ws["C6"].number_format = '[$€-407] #,##0.0"bn"'
    res = extract_model_full(save(wb, tmp_path))
    pct = find(res, "Pct")
    assert pct.unit == "percent" and pct.pct_basis == "ratio" and pct.display_decimals == 1
    big = find(res, "Big")
    assert big.display_divisor == 1e6 and big.scale == "m" and big.number_format == "#,##0.0,,"
    mult = find(res, "Mult")
    assert mult.unit == "multiple" and mult.scale_known
    dol = find(res, "Dollars")
    assert dol.unit == "currency" and dol.currency == "USD" and dol.display_decimals == 2
    eur = find(res, "EurBn")
    assert eur.currency == "EUR" and eur.scale == "bn" and eur.scale_known


def test_divisor_with_thousands_model_and_millions_banner(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "($ in millions)"
    ws["B3"], ws["C3"] = 2023, 2024
    ws["A4"], ws["B4"], ws["C4"] = "Revenue", 2_400_000, 2_600_000
    ws["B4"].number_format = ws["C4"].number_format = "#,##0,"
    res = extract_model_full(save(wb, tmp_path))
    v = find(res, "Revenue", "y24")
    assert v.display_divisor == 1000.0 and v.scale == "m"


def test_dates_booleans_skipped(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = "x", "FY24"
    ws["A2"], ws["C2"] = "Revenue", 10
    ws["A3"], ws["C3"] = "As of", dt.datetime(2024, 12, 31)
    ws["A4"], ws["C4"] = "Flag", True
    res = extract_model_full(save(wb, tmp_path))
    assert [v.row_label for v in res.values] == ["Revenue"]


def test_unit_column_and_indented_children(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Operating Summary"
    ws["A3"], ws["B3"], ws["C3"], ws["D3"] = "Metric", "Unit", 2023, 2024
    data = [("Revenue", "$m", 100, 120), ("EBITDA margin", "%", 20, 22), ("EV / EBITDA", "x", 9.5, 10.1),
            ("Spread", "bps", 150, 160), ("Revenue (EUR)", "€bn", 1.1, 1.3)]
    for i, (lab, unit, a, b) in enumerate(data, start=4):
        ws.cell(i, 1, lab)
        ws.cell(i, 2, unit)
        ws.cell(i, 3, a)
        ws.cell(i, 4, b)
    res = extract_model_full(save(wb, tmp_path))
    v = find(res, "Revenue", "y24")
    assert v.unit == "currency" and v.scale == "m" and v.scale_known
    assert find(res, "EBITDA margin", "y24").unit == "percent"
    assert find(res, "EBITDA margin", "y24").pct_basis == "points"
    assert find(res, "EV / EBITDA", "y24").unit == "multiple"
    assert find(res, "Spread", "y24").unit == "bps"
    eur = find(res, "Revenue (EUR)", "y24")
    assert eur.currency == "EUR" and eur.scale == "bn"
    # the unit column itself yields no values
    assert not any(v.col == 2 for v in res.values)


def test_indented_children_take_parent_section(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"] = "Revenue by segment"
    for i, (lab, a, b) in enumerate([("North America", 10, 11), ("Europe", 5, 6)], start=3):
        ws.cell(i, 1, lab).alignment = Alignment(indent=1)
        ws.cell(i, 2, a)
        ws.cell(i, 3, b)
    res = extract_model_full(save(wb, tmp_path))
    v = find(res, "Europe", "y24")
    assert "Revenue by segment" in v.section_label


def test_blank_rows_and_second_block_with_own_headers(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Income Statement"
    ws["B2"], ws["C2"] = 2023, 2024
    ws["A3"], ws["B3"], ws["C3"] = "Revenue", 1, 2
    ws["A6"] = "Balance Sheet"
    ws["B7"], ws["C7"] = "FY25", "FY26"
    ws["A8"], ws["B8"], ws["C8"] = "Total assets", 50, 60
    res = extract_model_full(save(wb, tmp_path))
    assert find(res, "Revenue", "y24").section_label == "Income Statement"
    ta = find(res, "Total assets")
    assert ta.section_label == "Balance Sheet"
    assert {v.period for v in res.values if v.row_label == "Total assets"} == {"fy25", "fy26"}


@pytest.mark.parametrize("streaming", [False, True])
def test_merged_company_headers(tmp_path, streaming):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Comps"
    ws["B2"], ws["D2"] = "Company A", "Company B"
    ws.merge_cells("B2:C2")
    ws.merge_cells("D2:E2")
    ws["B3"], ws["C3"], ws["D3"], ws["E3"] = "FY24", "FY25", "FY24", "FY25"
    ws["A4"] = "Revenue"
    for j, v in enumerate((10, 11, 20, 22), start=2):
        ws.cell(4, j, v)
    res = extract_model_full(save(wb, tmp_path), Config(large_workbook_cells=1) if streaming else Config())
    v = {v.cell_address: v for v in res.values}
    assert "Company A" in v["B4"].col_label and "FY24" in v["B4"].col_label
    assert "Company A" in v["C4"].col_label and "FY25" in v["C4"].col_label
    assert "Company B" in v["D4"].col_label and "FY24" in v["D4"].col_label
    assert "Company B" in v["E4"].col_label
    assert v["D4"].period == "fy24" and v["E4"].period == "fy25"


def test_transposed_sheet(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["B1"], ws["C1"] = "Year", "Revenue", "EBITDA"
    for i, (y, r, e) in enumerate([(2022, 100.0, 20.0), (2023, 110.0, 24.0), (2024, 125.0, 30.0)], start=2):
        ws.cell(i, 1, y)
        ws.cell(i, 2, r)
        ws.cell(i, 3, e)
    res = extract_model_full(save(wb, tmp_path))
    v = {x.cell_address: x for x in res.values}
    assert "A2" not in v and "A3" not in v          # the year column is not data
    assert len(res.values) == 6
    assert v["C4"].col_label == "EBITDA" and v["C4"].period == "y24" and v["C4"].value == 30.0
    assert v["B3"].col_label == "Revenue" and v["B3"].period == "y23"


# ----------------------------------------------------------------- errors / formulas


def test_error_cells(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"], ws["B2"], ws["C2"] = "Revenue", "#DIV/0!", 5
    ws["A3"], ws["B3"] = "Margin", "#N/A"
    res = extract_model_full(save(wb, tmp_path))
    errs = [v for v in res.values if v.is_error]
    assert {(v.cell_address, v.error_text) for v in errs} == {("B2", "#DIV/0!"), ("B3", "#N/A")}
    e = next(v for v in errs if v.cell_address == "B2")
    assert math.isnan(e.value) and e.row_label == "Revenue" and e.period == "y23"
    assert res.sheets[0].error_count == 2


def test_uncached_formulas_one_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(fileio, "soffice_binary", lambda: None)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"], ws["B2"] = "Revenue", 100
    ws["C2"] = "=B2*1.1"
    ws["A3"], ws["B3"], ws["C3"] = "Cost", 40, "=XNPV(B3,B2)"      # unsupported by the built-in evaluator
    ws2 = wb.create_sheet("Other")
    ws2["A1"], ws2["B1"], ws2["C1"] = "x", 2023, 2024
    ws2["A2"], ws2["B2"], ws2["C2"] = "Y", 1, "=B2"
    res = extract_model_full(save(wb, tmp_path))
    w = [x for x in res.warnings if x.code == "uncalculated_formulas"]
    assert len(w) == 1 and w[0].count == 3
    assert "Open the workbook in Excel and save it so formula results are stored" in w[0].message
    assert "1 of them could not be calculated" in w[0].message and "built-in formula engine" in w[0].message
    assert "Sheet" in w[0].message and "Other" in w[0].message
    info = {s.name: s for s in res.sheets}
    assert info["Sheet"].formula_count == 2 and info["Sheet"].uncached_formula_count == 1
    assert info["Other"].uncached_formula_count == 0
    assert find(res, "Revenue", "y24").value == pytest.approx(110.0)       # computed by the fallback
    assert not any(v.cell_address == "C3" and v.sheet_name == "Sheet" for v in res.values)


def test_libreoffice_fallback_fills_cache(tmp_path, monkeypatch):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"], ws["B2"], ws["C2"] = "Revenue", 100, "=B2*1.1"
    p = save(wb, tmp_path)

    def fake_convert(src, out_dir, target, timeout=180):
        w2 = openpyxl.Workbook()
        s = w2.active
        s["B1"], s["C1"] = 2023, 2024
        s["A2"], s["B2"], s["C2"] = "Revenue", 100, 110.0
        out = f"{out_dir}/recalc.xlsx"
        w2.save(out)
        return out

    monkeypatch.setattr(fileio, "soffice_binary", lambda: "soffice")
    monkeypatch.setattr(fileio, "soffice_convert", fake_convert)
    res = extract_model_full(p)
    v = find(res, "Revenue", "y24")
    assert v.value == 110.0 and v.formula == "=B2*1.1"
    w = [x for x in res.warnings if x.code == "uncalculated_formulas"]
    assert len(w) == 1 and "LibreOffice" in w[0].message
    assert res.sheets[0].uncached_formula_count == 0


def test_cached_values_used_for_formulas(tmp_path):
    """A workbook with cached results (simulated by patching the loader) keeps value and formula text."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"], ws["B2"], ws["C2"] = "Revenue", 100, "=B2*1.1"
    p = save(wb, tmp_path)
    real_open = xlsx_extractor._open

    def patched(path, name, *, data_only, read_only):
        book = real_open(path, name, data_only=data_only, read_only=read_only)
        if data_only and not read_only:
            book.active["C2"] = 110.0
        return book

    import unittest.mock as mock
    with mock.patch.object(xlsx_extractor, "_open", patched):
        res = extract_model_full(p)
    v = find(res, "Revenue", "y24")
    assert v.value == 110.0 and v.formula == "=B2*1.1"
    assert not [x for x in res.warnings if x.code == "uncalculated_formulas"]


def test_numbers_stored_as_text(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"], ws["B2"], ws["C2"] = "Revenue", "1,234", "(45)"
    ws["A3"], ws["B3"] = "Growth", "12.5%"
    res = extract_model_full(save(wb, tmp_path))
    v = {x.cell_address: x for x in res.values}
    assert v["B2"].value == 1234 and v["C2"].value == -45
    assert v["B3"].value == 12.5 and v["B3"].unit == "percent" and v["B3"].pct_basis == "points"
    w = [x for x in res.warnings if x.code == "numbers_stored_as_text"]
    assert len(w) == 1 and w[0].count == 3


# ----------------------------------------------------------------- sheets / names


@pytest.mark.parametrize("streaming", [False, True])
def test_hidden_sheet_rows_cols_and_include_hidden(tmp_path, streaming):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Visible"
    ws["B1"], ws["C1"], ws["D1"] = 2023, 2024, 2025
    ws["A2"], ws["B2"], ws["C2"], ws["D2"] = "Revenue", 1, 2, 3
    ws["A3"], ws["B3"], ws["C3"], ws["D3"] = "Cost", 4, 5, 6
    ws.row_dimensions[3].hidden = True
    ws.column_dimensions["D"].hidden = True
    hid = wb.create_sheet("Secret")
    hid["B1"], hid["C1"] = 2023, 2024
    hid["A2"], hid["B2"], hid["C2"] = "Revenue", 9, 10
    hid.sheet_state = "hidden"
    p = save(wb, tmp_path)
    config = Config(large_workbook_cells=1) if streaming else Config()
    res = extract_model_full(p, config)
    v = {(x.sheet_name, x.cell_address): x for x in res.values}
    assert v[("Visible", "B2")].hidden is False
    assert v[("Visible", "B3")].hidden is True
    assert v[("Visible", "D2")].hidden is True
    assert v[("Secret", "B2")].hidden is True
    assert next(s for s in res.sheets if s.name == "Secret").hidden
    config.include_hidden = False
    res2 = extract_model_full(p, config)
    v2 = {(x.sheet_name, x.cell_address) for x in res2.values}
    assert v2 == {("Visible", "B2"), ("Visible", "C2")}
    assert any(w.code == "hidden_excluded" for w in res2.warnings)


def test_priority_and_excluded_sheets(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    for name in ("Backup_old", "Scratchpad"):
        s = wb.create_sheet(name)
        s["B1"], s["C1"] = 2023, 2024
        s["A2"], s["B2"], s["C2"] = "Revenue", 1, 2
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"], ws["B2"], ws["C2"] = "Revenue", 1, 2
    p = save(wb, tmp_path)
    res = extract_model_full(p, Config(exclude_sheets=["Scratch*"]))
    pr = {s.name: s.priority for s in res.sheets}
    assert pr == {"Summary": 1.0, "Backup_old": 0.5}
    assert all(v.sheet_priority == (0.5 if v.sheet_name == "Backup_old" else 1.0) for v in res.values)
    assert not any(v.sheet_name == "Scratchpad" for v in res.values)
    w = [x for x in res.warnings if x.code == "sheets_excluded"]
    assert len(w) == 1 and "Scratchpad" in w[0].message


def test_named_ranges_workbook_and_sheet_scoped(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "My Sheet"
    ws["B1"], ws["C1"] = 2023, 2024
    ws["A2"], ws["B2"], ws["C2"] = "Revenue", 100, 120
    ws["A3"], ws["B3"], ws["C3"] = "EBITDA", 30, 36
    wb.defined_names["Rev_FY24"] = DefinedName("Rev_FY24", attr_text="'My Sheet'!$C$2")
    wb.defined_names["EBITDA_Hist"] = DefinedName("EBITDA_Hist", attr_text="'My Sheet'!$B$3:$C$3")
    ws.defined_names["Local_Rev"] = DefinedName("Local_Rev", attr_text="'My Sheet'!$B$2")
    p = save(wb, tmp_path)
    res = extract_model_full(p)
    v = {x.cell_address: x for x in res.values}
    assert v["C2"].named_range == "Rev_FY24"
    assert v["B2"].named_range == "Local_Rev"
    assert v["B3"].named_range == "EBITDA_Hist" and v["C3"].named_range == "EBITDA_Hist"


def test_named_lone_cell_without_layout_is_still_emitted(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["D20"] = 0.095
    wb.defined_names["WACC"] = DefinedName("WACC", attr_text="Sheet!$D$20")
    res = extract_model_full(save(wb, tmp_path))
    v = [x for x in res.values if x.cell_address == "D20"]
    assert len(v) == 1 and v[0].named_range == "WACC"


def test_external_links_warning(tmp_path, monkeypatch):
    p = save(banker_workbook(), tmp_path)
    real = xlsx_extractor._load_pair

    def patched(path, name, streaming):
        a, b = real(path, name, streaming)
        b._external_links = ["link1"]
        return a, b

    monkeypatch.setattr(xlsx_extractor, "_load_pair", patched)
    res = extract_model_full(p)
    assert any(w.code == "external_links" for w in res.warnings)


def test_xlsm_extension_allowed(tmp_path):
    wb = banker_workbook()
    wb.save(tmp_path / "m.xlsx")
    # .xlsm content-types differ, but the same package under an .xlsm name must still load
    (tmp_path / "m.xlsm").write_bytes((tmp_path / "m.xlsx").read_bytes())
    res = extract_model_full(str(tmp_path / "m.xlsm"))
    assert res.workbook == "m.xlsm" and res.values


# ----------------------------------------------------------------- errors / streaming / perf


def test_non_xlsx_raises_with_hint(tmp_path):
    bad = tmp_path / "x.xlsx"
    bad.write_text("this is not a workbook")
    with pytest.raises(NumCheckInputError) as ei:
        extract_model_full(str(bad))
    assert ei.value.hint
    with pytest.raises(NumCheckInputError):
        extract_model_full(str(tmp_path / "missing.xlsx"))


def test_openpyxl_load_error_wrapped(tmp_path, monkeypatch):
    p = save(banker_workbook(), tmp_path)

    def boom(*a, **k):
        raise KeyError("workbook.xml")

    monkeypatch.setattr(openpyxl, "load_workbook", boom)
    with pytest.raises(NumCheckInputError) as ei:
        extract_model_full(p)
    assert ei.value.hint


def test_streaming_mode_warns_and_still_extracts(tmp_path):
    p = save(banker_workbook(), tmp_path)
    res = extract_model_full(p, Config(large_workbook_cells=5))
    assert any(w.code == "large_workbook_streaming" for w in res.warnings)
    rev = find(res, "Revenue", "y24")
    assert rev.scale == "m" and rev.unit == "currency"
    assert find(res, "EBITDA margin", "y24").pct_basis == "ratio"


def test_large_sheet_performance(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Big model"
    ws["A2"] = "($ in millions)"
    for j in range(2, 22):
        ws.cell(4, j, 2000 + j)
    for i in range(5, 2505):
        ws.cell(i, 1, f"Line item {i}")
        for j in range(2, 22):
            ws.cell(i, j, i * j * 1.5)
    p = save(wb, tmp_path)
    t0 = time.time()
    res = extract_model_full(p)
    assert time.time() - t0 < 30
    assert len(res.values) == 2500 * 20
    v = {x.cell_address: x for x in res.values}
    assert v["C10"].row_label == "Line item 10" and v["C10"].period == "y03"
    assert v["C10"].scale == "m" and v["C10"].scale_known


def test_disk_roundtrip_matches_sheetinfo_counts(tmp_path):
    res = extract_model_full(save(banker_workbook(), tmp_path))
    assert sum(s.value_count for s in res.sheets) == len(res.values)
