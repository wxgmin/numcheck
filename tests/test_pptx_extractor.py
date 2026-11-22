"""Tests for deck, chart and OCR extraction (workstream B)."""

from __future__ import annotations

import copy
import struct
import zlib

import pytest
from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt

from numcheck.config import Config
from numcheck.extraction import ocr_extractor
from numcheck.extraction.pptx_extractor import (
    extract_deck,
    extract_deck_full,
    extract_footnotes,
    parse_as_of_dates,
)
from numcheck.models import NumCheckInputError, Source

TITLE_ONLY, BLANK = 5, 6


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def new_deck():
    prs = Presentation()
    return prs


def add_slide(prs, title: str | None = None):
    slide = prs.slides.add_slide(prs.slide_layouts[TITLE_ONLY if title is not None else BLANK])
    if title is not None:
        slide.shapes.title.text = title
    return slide


def add_text(slide, text: str, left=0.5, top=1.5, width=9.0, height=1.0, size: int | None = None):
    tb = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    lines = text.split("\n")
    tf = tb.text_frame
    tf.text = lines[0]
    for line in lines[1:]:
        tf.add_paragraph().text = line
    if size:
        for p in tf.paragraphs:
            for r in p.runs:
                r.font.size = Pt(size)
    return tb


def add_table(slide, rows: list[list[str]], left=0.5, top=2.0, width=9.0, height=3.0):
    gf = slide.shapes.add_table(len(rows), len(rows[0]), Inches(left), Inches(top), Inches(width), Inches(height))
    tbl = gf.table
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            tbl.cell(r, c).text = text
    return gf, tbl


def save(prs, tmp_path, name="deck.pptx") -> str:
    p = tmp_path / name
    prs.save(str(p))
    return str(p)


def by_raw(nums, raw):
    found = [n for n in nums if n.raw_text == raw]
    assert found, f"{raw!r} not in {[n.raw_text for n in nums]}"
    return found[0]


def png_bytes() -> bytes:
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- #
# Prose
# --------------------------------------------------------------------------- #


def test_prose_labels_and_period(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Highlights")
    add_text(s, "Revenue grew 9% year-over-year to $1.2bn in FY24, with EBITDA of $300m")
    res = extract_deck_full(save(prs, tmp_path))
    nums = res.numbers
    assert [n.raw_text for n in nums] == ["9%", "$1.2bn", "$300m"]
    assert [n.label for n in nums] == ["Revenue", "Revenue", "EBITDA"] or \
        [n.label.lower() for n in nums] == ["revenue", "revenue", "ebitda"]
    assert all(n.period == "fy24" for n in nums)
    # no leaked numbers in labels
    assert not any(ch.isdigit() for n in nums for ch in n.label)
    pct, bn, m = nums
    assert (pct.unit, pct.value) == ("percent", 9.0)
    assert (bn.unit, bn.scale, bn.value, bn.currency) == ("currency", "bn", 1.2, "USD")
    assert (m.scale, m.value) == ("m", 300.0)
    assert bn.unit_source == "token"
    assert bn.char_start == bn.context.index("$1.2bn") and bn.char_end == bn.char_start + 6
    assert bn.uid.startswith("s1-") and bn.uid.endswith(f"-p0-{bn.char_start}")
    assert bn.slide_title == "Highlights"


def test_label_after_number_when_nothing_precedes(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "$1.2bn revenue")
    nums = extract_deck(save(prs, tmp_path))
    assert nums[0].label.lower() == "revenue"


def test_clause_boundaries_and_qualifier(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Gross margin: 45%. Adjusted EBITDA margin of 22.5%; net debt was $80m")
    nums = extract_deck(save(prs, tmp_path))
    labels = [n.label.lower() for n in nums]
    assert labels == ["gross margin", "adjusted ebitda margin", "net debt"]
    assert "adjusted" in nums[1].qualifiers
    assert nums[1].qualifiers and not nums[0].qualifiers


def test_comparator_and_range(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Synergies of c.$50m and a multiple of 8.0x-9.0x")
    nums = extract_deck(save(prs, tmp_path))
    assert nums[0].comparator == "~"
    assert nums[1].is_range and nums[1].range_role == "low"
    assert nums[2].range_role == "high"


def test_years_and_markers_are_skipped_not_numbers(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Revenue(1) was $5.0bn in 2024")
    res = extract_deck_full(save(prs, tmp_path))
    assert [n.raw_text for n in res.numbers] == ["$5.0bn"]
    reasons = {n.skip_reason for n in res.skipped}
    assert {"footnote_marker", "year"} <= reasons
    assert res.numbers[0].footnote_refs == ["1"]  # marker on the metric label attaches to its figure
    assert res.numbers[0].period == "y24"


def test_slide_number_placeholder_ignored(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Revenue was $5m")
    sp = copy.deepcopy(s.shapes[-1]._element)
    ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    nv = sp.find("p:nvSpPr/p:nvPr", ns)
    ph = etree.SubElement(nv, "{%s}ph" % ns["p"])
    ph.set("type", "sldNum")
    sp.find("p:nvSpPr/p:cNvPr", ns).set("id", "99")
    for t in sp.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"):
        t.text = "17"
    s.shapes._spTree.append(sp)
    nums = extract_deck(save(prs, tmp_path))
    assert [n.raw_text for n in nums] == ["$5m"]


def test_group_shapes_walked(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    grp = s.shapes.add_group_shape()
    tb = grp.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    tb.text_frame.text = "EBITDA was $42m"
    inner = grp.shapes.add_group_shape()
    tb2 = inner.shapes.add_textbox(Inches(1), Inches(3), Inches(4), Inches(1))
    tb2.text_frame.text = "Capex was $7m"
    nums = extract_deck(save(prs, tmp_path))
    assert sorted(n.raw_text for n in nums) == ["$42m", "$7m"]


def test_speaker_notes_only_when_enabled(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Revenue was $5m")
    s.notes_slide.notes_text_frame.text = "Cost was $3m"
    path = save(prs, tmp_path)
    assert [n.raw_text for n in extract_deck(path)] == ["$5m"]
    cfg = Config(read_speaker_notes=True)
    nums = extract_deck(path, cfg)
    notes = [n for n in nums if n.source == Source.NOTES]
    assert [n.raw_text for n in notes] == ["$3m"]


# --------------------------------------------------------------------------- #
# Unit banners / per share
# --------------------------------------------------------------------------- #


def test_title_banner_applies_to_unitless_table(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Financial summary ($ in millions)")
    add_table(s, [["", "FY23", "FY24"], ["Revenue", "1,000", "1,200"], ["EBITDA", "200", "260"]])
    res = extract_deck_full(save(prs, tmp_path))
    assert res.slides[0].unit_hint_text == "Financial summary ($ in millions)"
    for n in res.numbers:
        assert n.unit == "currency" and n.scale == "m" and n.currency == "USD"
        assert n.unit_source == "slide" and n.scale_known


def test_per_share_exception(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($ in millions, except per share data)")
    add_table(s, [["", "FY24"], ["Revenue", "1,200"], ["Diluted EPS", "2.45"]])
    nums = extract_deck(save(prs, tmp_path))
    rev, eps = by_raw(nums, "1,200"), by_raw(nums, "2.45")
    assert rev.scale == "m"
    assert eps.scale == "" and eps.scale_known


def test_per_share_exception_in_prose(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($ in millions, except per share data)")
    add_text(s, "Revenue of 1,200 and EPS of 2.45")
    nums = extract_deck(save(prs, tmp_path))
    assert by_raw(nums, "1,200").scale == "m"
    assert by_raw(nums, "2.45").scale == "" and by_raw(nums, "2.45").scale_known


def test_textbox_banner_not_a_number_and_inherited(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "(€ in billions)", top=1.0, height=0.3)
    add_text(s, "Revenue was 4.5", top=3.0)
    res = extract_deck_full(save(prs, tmp_path))
    n = res.numbers[0]
    assert n.scale == "bn" and n.currency == "EUR" and n.unit_source == "slide"
    assert res.slides[0].unit_hint_text == "(€ in billions)"


def test_slide_period_hint_from_title(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "FY2024 Financial Summary")
    add_text(s, "Revenue was $5m")
    res = extract_deck_full(save(prs, tmp_path))
    assert res.slides[0].period_hint == "fy24"
    assert res.numbers[0].period == "fy24"


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #


def test_period_headers_apply_per_cell_and_header_years_skipped(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($m)")
    add_table(s, [["", "FY22", "FY23", "FY24"], ["Revenue", "900", "1,000", "1,200"],
                  ["EBITDA", "150", "200", "260"]])
    res = extract_deck_full(save(prs, tmp_path))
    periods = {(n.row_label, n.raw_text): n.period for n in res.numbers}
    assert periods[("Revenue", "900")] == "fy22"
    assert periods[("Revenue", "1,200")] == "fy24"
    assert periods[("EBITDA", "200")] == "fy23"
    assert len(res.numbers) == 6
    n = by_raw(res.numbers, "260")
    assert n.label == "EBITDA"  # pure-period header parts stay out of the label; period is separate
    assert (n.row_label, n.col_label) == ("EBITDA", "FY24")
    assert (n.table_row, n.table_col) == (2, 3)
    # header cells produce no checkable numbers (FY22/23/24 are periods)
    assert not any(x.table_row == 0 for x in res.numbers)


def test_bare_year_headers(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($m)")
    add_table(s, [["", "2023A", "2024E"], ["Revenue", "1,000", "1,200"]])
    res = extract_deck_full(save(prs, tmp_path))
    assert by_raw(res.numbers, "1,000").period == "y23"
    assert by_raw(res.numbers, "1,200").period == "y24"
    assert not any(n.raw_text in ("2023A", "2024E") for n in res.numbers)


def test_header_unit_capex_and_tax_rate(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($ in millions)")
    add_table(s, [["", "FY24", "Capex ($m)"], ["Tax rate", "25.0%", ""], ["Revenue", "30.0", "40"]])
    res = extract_deck_full(save(prs, tmp_path))
    tax = by_raw(res.numbers, "25.0%")
    assert tax.unit == "percent"
    capex = by_raw(res.numbers, "40")
    assert capex.unit == "currency" and capex.scale == "m" and capex.unit_source == "table"
    assert capex.unit != "multiple"
    assert "Capex" in capex.label


def test_percent_header_and_row_units(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($ in millions)")
    add_table(s, [["", "FY23", "Margin %"], ["Revenue", "1,000", "22.5"], ["EBITDA (%)", "20.0", "21.0"]])
    res = extract_deck_full(save(prs, tmp_path))
    assert by_raw(res.numbers, "22.5").unit == "percent"
    assert by_raw(res.numbers, "20.0").unit == "percent"      # "(%)" on the row header
    assert by_raw(res.numbers, "1,000").unit == "currency"


def test_negatives_and_na_cells(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($m)")
    add_table(s, [["", "FY23", "FY24"], ["Net debt", "(45)", "n.m."], ["Free cash flow", "NM", "n/a"],
                  ["Other", "–", "-"], ["Capex", "(12.5)", "30"]])
    res = extract_deck_full(save(prs, tmp_path))
    assert by_raw(res.numbers, "(45)").value == -45.0
    assert by_raw(res.numbers, "(12.5)").value == -12.5
    assert sorted(n.raw_text for n in res.numbers) == ["(12.5)", "(45)", "30"]


def test_merged_header_cells(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($m)")
    gf, tbl = add_table(s, [["", "FY23", "", "FY24", ""], ["", "Actual", "Budget", "Actual", "Budget"],
                            ["Revenue", "100", "105", "120", "125"]])
    tbl.cell(0, 1).merge(tbl.cell(0, 2))
    tbl.cell(0, 3).merge(tbl.cell(0, 4))
    res = extract_deck_full(save(prs, tmp_path))
    periods = {n.raw_text: n.period for n in res.numbers}
    assert periods == {"100": "fy23", "105": "fy23", "120": "fy24", "125": "fy24"}
    assert by_raw(res.numbers, "125").col_label.endswith("Budget")
    assert "FY24" in by_raw(res.numbers, "125").col_label


def test_transposed_table_periods_as_rows(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($m)")
    add_table(s, [["", "Revenue", "EBITDA"], ["FY22", "900", "150"], ["FY23", "1,000", "200"],
                  ["FY24", "1,200", "260"]])
    res = extract_deck_full(save(prs, tmp_path))
    n = by_raw(res.numbers, "260")
    assert n.period == "fy24"
    assert "EBITDA" in n.label
    assert by_raw(res.numbers, "1,000").period == "fy23"
    assert len(res.numbers) == 6


def test_label_column_not_first(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($m)")
    add_table(s, [["#", "Metric", "FY24"], ["1", "Revenue", "1,200"], ["2", "EBITDA", "260"]])
    res = extract_deck_full(save(prs, tmp_path))
    n = by_raw(res.numbers, "260")
    assert n.row_label == "EBITDA"
    assert n.period == "fy24"


def test_units_column(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary")
    add_table(s, [["Metric", "Unit", "FY24"], ["Revenue", "$m", "1,200"], ["EBITDA margin", "%", "21.7"],
                  ["Leverage", "x", "2.5"]])
    res = extract_deck_full(save(prs, tmp_path))
    rev, mar, lev = by_raw(res.numbers, "1,200"), by_raw(res.numbers, "21.7"), by_raw(res.numbers, "2.5")
    assert (rev.unit, rev.scale) == ("currency", "m")
    assert mar.unit == "percent"
    assert lev.unit == "multiple"
    assert not any(n.raw_text in ("$m", "%", "x") for n in res.numbers)


def test_multi_row_header(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Summary ($m)")
    add_table(s, [["", "Standalone", "Standalone"], ["", "FY23", "FY24"], ["Revenue", "100", "120"]])
    res = extract_deck_full(save(prs, tmp_path))
    n = by_raw(res.numbers, "120")
    assert n.period == "fy24"
    assert "Standalone" in n.col_label


def test_table_caption_hint_beats_slide_hint(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview ($ in millions)")
    add_text(s, "(€ in billions)", top=1.4, height=0.4)
    add_table(s, [["", "FY24"], ["Revenue", "4.5"]], top=2.0)
    res = extract_deck_full(save(prs, tmp_path))
    n = res.numbers[0]
    assert n.scale == "bn" and n.currency == "EUR" and n.unit_source == "table"


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #


def add_chart(slide, kind, data, left=0.5, top=2.0):
    return slide.shapes.add_chart(kind, Inches(left), Inches(top), Inches(6), Inches(3.5), data)


def test_column_chart_periods_and_slide_unit(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Revenue ($ in millions)")
    cd = CategoryChartData()
    cd.categories = ["FY23", "FY24"]
    cd.add_series("Revenue", (1000.0, 1200.0))
    add_chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd)
    res = extract_deck_full(save(prs, tmp_path))
    ch = [n for n in res.numbers if n.source == Source.CHART]
    assert [(n.period, n.value) for n in ch] == [("fy23", 1000.0), ("fy24", 1200.0)]
    assert all(n.unit == "currency" and n.scale == "m" and n.unit_source == "slide" for n in ch)
    assert ch[0].label == "Revenue FY23"
    assert (ch[0].row_label, ch[0].col_label) == ("Revenue", "FY23")
    assert ch[0].chart_type == "barChart" and ch[0].series_index == 0 and ch[1].point_index == 1
    assert ch[0].uid != ch[1].uid


def test_chart_percent_format_converts_ratio_to_points(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Margins")
    cd = CategoryChartData(number_format="0.0%")
    cd.categories = ["FY23", "FY24"]
    cd.add_series("EBITDA margin", (0.25, 0.3))
    add_chart(s, XL_CHART_TYPE.LINE, cd)
    ch = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART]
    assert [n.value for n in ch] == [25.0, 30.0]
    assert all(n.unit == "percent" and n.decimals == 1 and n.unit_source == "chart_format" for n in ch)
    assert ch[0].chart_type == "lineChart"
    assert ch[0].raw_text == "25.0%"


def test_chart_title_unit_and_series_name_unit(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    cd = CategoryChartData()
    cd.categories = ["FY23"]
    cd.add_series("Capex ($m)", (40.0,))
    gf = add_chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd)
    n = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART][0]
    assert n.unit == "currency" and n.scale == "m" and n.unit_source == "series"
    assert gf is not None


def test_pie_chart_type(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Mix")
    cd = CategoryChartData(number_format="0%")
    cd.categories = ["North", "South"]
    cd.add_series("Share of revenue", (0.6, 0.4))
    add_chart(s, XL_CHART_TYPE.PIE, cd)
    ch = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART]
    assert {n.chart_type for n in ch} == {"pieChart"}
    assert [n.value for n in ch] == [60.0, 40.0]
    assert ch[0].label == "Share of revenue North"


def test_xy_scatter_chart(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Scatter")
    cd = XyChartData()
    ser = cd.add_series("Multiple vs growth")
    ser.add_data_point(5.0, 12.5)
    ser.add_data_point(8.0, 15.0)
    add_chart(s, XL_CHART_TYPE.XY_SCATTER, cd)
    ch = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART]
    assert [n.value for n in ch] == [12.5, 15.0]
    assert {n.chart_type for n in ch} == {"scatterChart"}
    assert [float(n.col_label) for n in ch] == [5.0, 8.0]  # xVal used as the category label


def test_bar_chart_series_hint_and_combo(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview ($ in millions)")
    cd = CategoryChartData()
    cd.categories = ["FY23", "FY24"]
    cd.add_series("Revenue", (10.0, 12.0))
    cd.add_series("Cost", (5.0, 6.0))
    add_chart(s, XL_CHART_TYPE.COLUMN_STACKED, cd)
    ch = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART]
    assert len(ch) == 4
    assert {n.series_index for n in ch} == {0, 1}


def test_chart_sparse_points_respect_idx(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview ($ in millions)")
    cd = CategoryChartData()
    cd.categories = ["FY22", "FY23", "FY24"]
    cd.add_series("Revenue", (10.0, 11.0, 12.0))
    gf = add_chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd)
    # remove the middle point from the numCache (a gap in the data)
    c = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    cache = gf.chart._chartSpace.find(f".//{{{c}}}val//{{{c}}}numCache")
    pts = cache.findall(f"{{{c}}}pt")
    cache.remove(pts[1])
    ch = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART]
    assert [(n.point_index, n.period, n.value) for n in ch] == [(0, "fy22", 10.0), (2, "fy24", 12.0)]


@pytest.mark.parametrize("show_label", [False, True])
def test_stacked_bar_transparent_padding_is_checked_only_when_labelled(tmp_path, show_label):
    prs = new_deck()
    slide = add_slide(prs, "Valuation")
    data = CategoryChartData()
    data.categories = ["DCF"]
    data.add_series("Offset", (100.0,))
    data.add_series("Range", (12.0,))
    chart = add_chart(slide, XL_CHART_TYPE.BAR_STACKED, data).chart
    offset = chart.series[0]
    offset.format.fill.background()
    offset.format.line.fill.background()
    if show_label:
        offset.data_labels.show_value = True
    values = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART]
    assert [(n.series_index, n.value) for n in values] == ([(0, 100), (1, 12)] if show_label else [(1, 12)])


def test_grouped_numeric_callout_inherits_a_single_caption(tmp_path):
    prs = new_deck()
    slide = add_slide(prs, "Overview")
    card = slide.shapes.add_group_shape()
    card.shapes.add_textbox(Inches(1), Inches(2), Inches(2), Inches(.5)).text = "$56m"
    card.shapes.add_textbox(Inches(1), Inches(2.5), Inches(2), Inches(.5)).text = "FY24A Adjusted EBITDA"
    number = by_raw(extract_deck(save(prs, tmp_path)), "$56m")
    assert number.label == "FY24A Adjusted EBITDA"
    assert number.period == "fy24"
    assert number.char_start == 0


def test_extended_powerpoint_chart_is_reported_instead_of_silently_skipped(tmp_path):
    prs = new_deck()
    slide = add_slide(prs, "Valuation")
    data = CategoryChartData()
    data.categories = ["DCF"]
    data.add_series("Value", (100.0,))
    frame = add_chart(slide, XL_CHART_TYPE.BAR_STACKED, data)
    graphic_data = frame._element.graphic.graphicData
    graphic_data.remove(graphic_data[0])
    namespace = "http://schemas.microsoft.com/office/drawing/2014/chartex"
    graphic_data.set("uri", namespace)
    etree.SubElement(graphic_data, f"{{{namespace}}}chart")
    result = extract_deck_full(save(prs, tmp_path))
    assert not result.numbers
    assert any(w.code == "extended_chart_not_checked" and w.slide_number == 1 for w in result.warnings)


def test_chart_without_cache_warns(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    cd = CategoryChartData()
    cd.categories = ["FY23"]
    cd.add_series("Revenue", (10.0,))
    gf = add_chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd)
    c = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    for cache in list(gf.chart._chartSpace.iter(f"{{{c}}}numCache")):
        cache.getparent().remove(cache)
    # also drop the embedded workbook relationship so there is no fallback
    ext = gf.chart._chartSpace.find(f"{{{c}}}externalData")
    if ext is not None:
        gf.chart._chartSpace.remove(ext)
    res = extract_deck_full(save(prs, tmp_path))
    assert not [n for n in res.numbers if n.source == Source.CHART]
    assert any(w.code == "chart_no_cache" and w.slide_number == 1 for w in res.warnings)


def test_chart_no_cache_falls_back_to_embedded_workbook(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    cd = CategoryChartData()
    cd.categories = ["FY23", "FY24"]
    cd.add_series("Revenue", (10.0, 12.0))
    gf = add_chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd)
    c = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    for cache in list(gf.chart._chartSpace.find(f".//{{{c}}}val").iter(f"{{{c}}}numCache")):
        cache.getparent().remove(cache)
    ch = [n for n in extract_deck(save(prs, tmp_path)) if n.source == Source.CHART]
    assert sorted(n.value for n in ch) == [10.0, 12.0]


# --------------------------------------------------------------------------- #
# Pictures, hidden slides, SmartArt
# --------------------------------------------------------------------------- #


def test_picture_counted_and_warned(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(png_bytes())
    prs = new_deck()
    s = add_slide(prs, "Overview")
    s.shapes.add_picture(str(img), Inches(1), Inches(2), Inches(2), Inches(2))
    s2 = add_slide(prs, "Two")
    add_text(s2, "Revenue $5m")
    res = extract_deck_full(save(prs, tmp_path))
    assert res.slides[0].image_count == 1 and res.slides[1].image_count == 0
    warns = [w for w in res.warnings if w.code == "images_not_checked"]
    assert len(warns) == 1 and "1" in warns[0].message


def test_hidden_slide_flag(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Hidden")
    add_text(s, "Revenue $5m")
    s._element.set("show", "0")
    add_slide(prs, "Visible")
    res = extract_deck_full(save(prs, tmp_path))
    assert res.slides[0].hidden and not res.slides[1].hidden
    assert len(res.numbers) == 1  # still extracted


def test_number_count_per_slide(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "A")
    add_text(s, "Revenue $5m and EBITDA $1m")
    add_slide(prs, "B")
    res = extract_deck_full(save(prs, tmp_path))
    assert [x.number_count for x in res.slides] == [2, 0]


def test_smartart_text_scanned(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    from pptx.opc.package import Part
    from pptx.opc.packuri import PackURI

    dgm = ('<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
           'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><dgm:ptLst><dgm:pt>'
           '<dgm:t><a:bodyPr/><a:p><a:r><a:t>Revenue of $9m</a:t></a:r></a:p></dgm:t></dgm:pt>'
           '</dgm:ptLst></dgm:dataModel>').encode()
    part = Part(PackURI("/ppt/diagrams/data1.xml"), "application/vnd.openxmlformats-officedocument.drawingml."
                "diagramData+xml", prs.part.package, dgm)
    rid = s.part.relate_to(part, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData")
    frame = etree.fromstring(
        '<p:graphicFrame xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<p:nvGraphicFramePr><p:cNvPr id="50" name="Diagram"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
        '<p:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></p:xfrm><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
        f'<dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" r:dm="{rid}" '
        'r:lo="rId99" r:qs="rId98" r:cs="rId97"/></a:graphicData></a:graphic></p:graphicFrame>')
    s.shapes._spTree.append(frame)
    res = extract_deck_full(save(prs, tmp_path))
    assert [n.raw_text for n in res.numbers] == ["$9m"]


def test_smartart_unreadable_warns(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    frame = etree.fromstring(
        '<p:graphicFrame xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:nvGraphicFramePr><p:cNvPr id="50" name="Diagram"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
        '<p:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></p:xfrm><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram"/></a:graphic></p:graphicFrame>')
    s.shapes._spTree.append(frame)
    res = extract_deck_full(save(prs, tmp_path))
    assert any(w.code == "smartart_not_read" for w in res.warnings)


# --------------------------------------------------------------------------- #
# Footnotes, sources, as-of dates
# --------------------------------------------------------------------------- #


def test_footnote_source_and_as_of(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Revenue was $5m")
    add_text(s, "Source: Company filings; market data as of 30-Sep-2024. Share price $12.30", top=6.8, size=8)
    path = save(prs, tmp_path)
    res = extract_deck_full(path)
    info = res.slides[0]
    assert info.sources and info.sources[0].startswith("Company filings")
    assert info.as_of_dates == ["2024-09-30"]
    assert [n.raw_text for n in res.numbers] == ["$5m"]  # footnote numbers are not checkable
    assert len(info.footnotes) == 1 and res.footnotes[0]["slide_number"] == 1
    assert extract_footnotes(path) == res.footnotes


def test_note_and_marker_footnotes(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Revenue was $5m")
    add_text(s, "Note: adjusted for one-offs of $2m\n(1) Excludes FX of 3%", top=6.0, size=9)
    res = extract_deck_full(save(prs, tmp_path))
    assert [n.raw_text for n in res.numbers] == ["$5m"]
    assert len(res.footnotes) == 2


def test_bottom_small_text_is_footnote_but_big_body_is_not(tmp_path):
    prs = new_deck()
    s = add_slide(prs, "Overview")
    add_text(s, "Methodology uses $3m of cost", top=6.9, size=8)
    add_text(s, "Revenue was $5m", top=3.0, size=18)
    res = extract_deck_full(save(prs, tmp_path))
    assert [n.raw_text for n in res.numbers] == ["$5m"]


@pytest.mark.parametrize("text,expected", [
    ("as of 30-Sep-2024", ["2024-09-30"]),
    ("as of September 30, 2024", ["2024-09-30"]),
    ("as at 15/11/2024", ["2024-11-15"]),
    ("market data as of 11/15/24", ["2024-11-15"]),
    ("as of Sep-24", ["2024-09-01"]),
    ("as of 2024-09-30", ["2024-09-30"]),
    ("no date here", []),
])
def test_as_of_dates(text, expected):
    assert parse_as_of_dates(text) == expected


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #


def test_non_pptx_raises(tmp_path):
    p = tmp_path / "bad.pptx"
    p.write_text("not a deck")
    with pytest.raises(NumCheckInputError):
        extract_deck_full(str(p))


def test_xlsx_in_deck_slot_raises(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    p = tmp_path / "m.xlsx"
    wb.save(str(p))
    with pytest.raises(NumCheckInputError):
        extract_deck_full(str(p))


def test_missing_file_raises(tmp_path):
    with pytest.raises(NumCheckInputError):
        extract_deck_full(str(tmp_path / "nope.pptx"))


def test_corrupt_package_wrapped(tmp_path):
    import zipfile

    p = tmp_path / "c.pptx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", "<Types>presentationml</Types>")
    with pytest.raises(NumCheckInputError) as ei:
        extract_deck_full(str(p))
    assert ei.value.hint


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #


def test_ocr_lines_from_data_orders_words():
    data = {
        "text": ["Revenue", "", "$1.2bn", "EBITDA", "300"],
        "conf": ["96", "-1", "90", "95", "91"],
        "block_num": [1, 1, 1, 1, 1], "par_num": [1, 1, 1, 1, 1], "line_num": [1, 1, 1, 2, 2],
        "left": [10, 0, 200, 10, 200],
    }
    assert ocr_extractor.lines_from_data(data) == ["Revenue $1.2bn", "EBITDA 300"]


def test_ocr_numbers_deduped_against_native():
    from numcheck.models import DeckNumber

    native = [DeckNumber(slide_number=1, shape_id="3", source=Source.TEXT_BOX, raw_text="$5m", value=5.0,
                         unit="currency", scale="m")]
    out = ocr_extractor.numbers_from_lines(
        1, ["Revenue $5m", "Net income $2m"], native, Config())
    assert [n.raw_text for n in out] == ["$2m"]
    assert out[0].source == Source.OCR and out[0].confidence == 0.7
    assert out[0].label.lower() == "net income"


def test_ocr_unavailable_warns_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_extractor, "_find_tesseract_deps", lambda: (None, "pytesseract is not installed"))
    warns = []
    assert ocr_extractor.extract_deck_ocr(str(tmp_path / "x.pptx"), existing=[], warnings=warns) == []
    assert warns and warns[0].code == "ocr_unavailable" and "pytesseract" in warns[0].message


def test_ocr_off_never_called_and_on_but_unavailable_keeps_image_warning(tmp_path, monkeypatch):
    img = tmp_path / "x.png"
    img.write_bytes(png_bytes())
    prs = new_deck()
    s = add_slide(prs, "Overview")
    s.shapes.add_picture(str(img), Inches(1), Inches(2), Inches(2), Inches(2))
    path = save(prs, tmp_path)
    monkeypatch.setattr(ocr_extractor, "_find_tesseract_deps", lambda: (None, "missing"))
    res = extract_deck_full(path, Config(ocr=True))
    codes = {w.code for w in res.warnings}
    assert {"ocr_unavailable", "images_not_checked"} <= codes


def test_ocr_full_pipeline_with_fakes(tmp_path, monkeypatch):
    """pptx -> (fake PDF/PNG/tesseract) -> OCR numbers merged into the deck extraction."""
    img = tmp_path / "x.png"
    img.write_bytes(png_bytes())
    prs = new_deck()
    s = add_slide(prs, "Overview")
    s.shapes.add_picture(str(img), Inches(1), Inches(2), Inches(2), Inches(2))
    path = save(prs, tmp_path)

    class FakeTess:
        class Output:
            DICT = "dict"

        @staticmethod
        def image_to_data(_img, config="", output_type=None):
            return {"text": ["Revenue", "$7m"], "conf": ["95", "93"], "block_num": [1, 1], "par_num": [1, 1],
                    "line_num": [1, 1], "left": [5, 50]}

    monkeypatch.setattr(ocr_extractor, "_find_tesseract_deps", lambda: (FakeTess, ""))
    monkeypatch.setattr(ocr_extractor.fileio, "soffice_binary", lambda: "soffice")
    monkeypatch.setattr(ocr_extractor, "_can_render_pages", lambda: True)
    monkeypatch.setattr(ocr_extractor.fileio, "soffice_convert", lambda src, out, target, timeout=180: "fake.pdf")
    monkeypatch.setattr(ocr_extractor, "_render_pdf_pages", lambda pdf, out: [str(img)])
    res = extract_deck_full(path, Config(ocr=True))
    ocr = [n for n in res.numbers if n.source == Source.OCR]
    assert [n.raw_text for n in ocr] == ["$7m"] and ocr[0].confidence == 0.7 and ocr[0].slide_number == 1
    assert not any(w.code == "images_not_checked" for w in res.warnings)
    assert res.slides[0].number_count == 1
