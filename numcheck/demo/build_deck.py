"""Build a 12-slide pitchbook with 10 planted errors + 1 control."""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt

NAVY = RGBColor(0x1F, 0x38, 0x64)
BLANK = 6


def build_deck(path: str | Path) -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    _title_slide(prs, "Acme Corp", "Company Overview & Valuation")
    _exec_summary(prs)
    _comps(prs)
    _dcf_summary(prs)
    _revenue_build(prs)
    _financial_summary(prs)
    _valuation(prs)
    _guidance(prs)
    _tam(prs)
    _appendix(prs)
    _operating_metrics(prs)
    _dcf_assumptions(prs)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    prs.save(path)


def _add_slide(prs: Presentation, title: str):
    slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
    box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.3), Inches(0.9))
    tf = box.text_frame
    tf.text = title
    tf.paragraphs[0].font.size = Pt(28)
    tf.paragraphs[0].font.bold = True
    tf.paragraphs[0].font.color.rgb = NAVY
    return slide


def _bullets(slide, lines, top: float = 1.5, size: int = 20):
    box = slide.shapes.add_textbox(Inches(0.7), Inches(top), Inches(11.5), Inches(5.6))
    tf = box.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.size = Pt(size)


def _table(slide, data, top: float, widths=None):
    rows, cols = len(data), len(data[0])
    shape = slide.shapes.add_table(rows, cols, Inches(0.7), Inches(top), Inches(12), Inches(0.4 * rows))
    table = shape.table
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.text = str(val)
            if r == 0:
                cell.text_frame.paragraphs[0].font.bold = True
    return table


def _title_slide(prs, title, subtitle):
    slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
    box = slide.shapes.add_textbox(Inches(1.0), Inches(2.6), Inches(11.3), Inches(1.5))
    box.text_frame.text = title
    box.text_frame.paragraphs[0].font.size = Pt(54)
    box.text_frame.paragraphs[0].font.bold = True
    box.text_frame.paragraphs[0].font.color.rgb = NAVY
    sub = slide.shapes.add_textbox(Inches(1.0), Inches(4.1), Inches(11.3), Inches(0.8))
    sub.text_frame.text = subtitle
    sub.text_frame.paragraphs[0].font.size = Pt(24)


def _exec_summary(prs):
    slide = _add_slide(prs, "Executive Summary")
    _bullets(slide, [
        "FY24 Revenue: $2,450m",
        "FY24 EBITDA: $512m",
        "FY24 Net Income: $385m",
        "Market Cap: $6.2bn",
    ])


def _comps(prs):
    slide = _add_slide(prs, "Trading Comps")
    _bullets(slide, [
        "Market Cap: $6,234m",
        "EV: $5,400m",
        "Revenue FY24: $2,415m",
        "EBITDA FY24: $540m",
        "P/E: 18.4x",
        "EV/EBITDA: 10.5x",
    ])


def _dcf_summary(prs):
    slide = _add_slide(prs, "DCF Summary")
    _bullets(slide, [
        "FY24 EBITDA: $512m",
        "Implied EV: $5.2bn",
        "WACC: 8.0%",
        "Terminal Growth: 2.5%",
    ], top=1.4)

    chart_data = CategoryChartData()
    chart_data.categories = ["FY25", "FY26", "FY27", "FY28", "FY29"]
    chart_data.add_series("FCF ($m)", (640, 680, 720, 760, 790))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(6.4), Inches(1.4), Inches(6.2), Inches(5.2),
        chart_data,
    )


def _revenue_build(prs):
    slide = _add_slide(prs, "Revenue Build — FY24")
    _table(slide, [
        ["Metric", "FY24 ($m)"],
        ["Product Revenue", "$1,300m"],
        ["Services Revenue", "$700m"],
        ["Other Revenue", "$415m"],
        ["Total Revenue", "$2,450m"],
    ], top=1.5)


def _financial_summary(prs):
    slide = _add_slide(prs, "Financial Summary")
    _bullets(slide, [
        "FY23 Revenue: $2,230m",
        "FY24 Revenue: $2,415m",
        "FY24 Revenue Growth: 8.5%",
        "FY24 EBITDA: $512m",
        "FY24 EBITDA Margin: 22.4%",
        "FY24 Net Income: $385m",
    ])


def _valuation(prs):
    slide = _add_slide(prs, "Valuation Summary")
    _bullets(slide, [
        "EV: $5.4bn",
        "WACC: 8.0%",
    ])


def _guidance(prs):
    slide = _add_slide(prs, "Management Guidance")
    _bullets(slide, [
        "CY24 Net Income: $385m",
    ])


def _tam(prs):
    slide = _add_slide(prs, "Market Opportunity")
    _bullets(slide, [
        "Total Addressable Market: $48bn",
    ])


def _appendix(prs):
    slide = _add_slide(prs, "Appendix")
    _bullets(slide, [
        "Source: Company filings, Q3 2025.",
    ], top=1.5, size=14)


def _operating_metrics(prs):
    slide = _add_slide(prs, "Operating Metrics")
    _bullets(slide, [
        "FY24 Revenue: $2,415m",
        "FY24 EBITDA: $512m",
        "FY24 Net Income: $385m",
    ])


def _dcf_assumptions(prs):
    slide = _add_slide(prs, "DCF Assumptions")
    _bullets(slide, [
        "WACC: 8.0%",
        "Terminal Growth: 2.5%",
    ])
