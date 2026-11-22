"""Chart-heavy deck: every chart type bankers use, with number formats / units in different places.

Slides: line (0.0% labels), pie (0% labels), bar in $bn via a ',,,"bn"' format, clustered column with
units in the series names, doughnut, stacked column, XY scatter (EV/EBITDA vs growth, one series per
peer), a column chart whose units live only in the slide title, and a text slide with unicode
(en-dash ranges, approx sign, euro) plus a pasted picture (expect an images_not_checked warning).
"""

from __future__ import annotations

from pathlib import Path

from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Inches

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

YEARS = ["2020", "2021", "2022", "2023", "2024", "2025E"]
REV = [1420.5, 1512.9, 1688.4, 1801.7, 1950.3, 2120.0]
EBITDA = [284.1, 317.7, 371.4, 405.4, 458.3, 508.8]
MARGIN = [0.20, 0.21, 0.22, 0.225, 0.235, 0.24]
CAPEX = [62.4, 70.1, 78.9, 85.6, 98.2, 104.0]


def _chart(deck: Deck, kind, cd, labels_fmt=None, legend=True, height=3.6, width=9.0, title=None):
    gf = deck.slide.shapes.add_chart(kind, Inches(0.5), deck._y, Inches(width), Inches(height), cd)
    ch = gf.chart
    ch.has_legend = legend
    if legend:
        ch.legend.position = XL_LEGEND_POSITION.BOTTOM
        ch.legend.include_in_layout = False
    if title:
        ch.has_title = True
        ch.chart_title.text_frame.text = title
    if labels_fmt is not None:
        plot = ch.plots[0]
        plot.has_data_labels = True
        plot.data_labels.show_value = True
        plot.data_labels.number_format = labels_fmt
        plot.data_labels.number_format_is_linked = False
    deck._y += Inches(height + 0.1)
    return ch


def build(out: Path) -> Scenario:
    m = Model()
    s = m.sheet("Summary")
    m.block(s, 1, "Meridian Industries - summary financials", "($ in millions)", YEARS, [
        ("Revenue", REV), ("EBITDA", EBITDA), ("EBITDA margin", MARGIN), ("Capex", CAPEX),
    ], fmt={"Revenue": "#,##0.0", "EBITDA": "#,##0.0", "EBITDA margin": "0.0%", "Capex": "#,##0.0"})

    seg = m.sheet("Segments")
    nxt = m.block(seg, 1, "Revenue by segment", "($ in millions)", ["2022", "2023", "2024"], [
        ("Industrial", [675.4, 720.7, 780.1]), ("Consumer", [506.5, 540.5, 585.1]),
        ("Healthcare", [337.7, 360.4, 390.1]), ("Other", [168.8, 180.1, 195.0]),
        ("Total revenue", [1688.4, 1801.7, 1950.3]),
    ], fmt={k: "#,##0.0" for k in ["Industrial", "Consumer", "Healthcare", "Other", "Total revenue"]})
    m.block(seg, nxt, "Segment mix", "(% of total revenue)", ["2022", "2023", "2024"], [
        ("Industrial mix", [0.40, 0.40, 0.40]), ("Consumer mix", [0.30, 0.30, 0.30]),
        ("Healthcare mix", [0.20, 0.20, 0.20]), ("Other mix", [0.10, 0.10, 0.10]),
    ], fmt={k: "0%" for k in ["Industrial mix", "Consumer mix", "Healthcare mix", "Other mix"]})

    reg = m.sheet("Regions")
    m.block(reg, 1, "Revenue by region", "(% of 2024 revenue)", ["2024"], [
        ("North America", [0.50]), ("Europe", [0.30]), ("APAC", [0.15]), ("Rest of World", [0.05]),
    ], fmt={k: "0%" for k in ["North America", "Europe", "APAC", "Rest of World"]})

    cmp_ = m.sheet("Comps")
    m.block(cmp_, 1, "Trading comparables", "", ["EV / LTM EBITDA", "EBITDA growth (NTM)"], [
        ("Alpha Corp", [11.2, 0.063]), ("Beta Holdings", [10.1, 0.048]),
        ("Gamma Group", [12.6, 0.081]), ("Epsilon Inc", [10.6, 0.035]), ("Zeta Ltd", [9.4, 0.029]),
    ])
    for r in range(6, 11):
        cmp_.cell(r, 3).number_format = '0.0"x"'
        cmp_.cell(r, 4).number_format = "0.0%"

    debt = m.sheet("Debt")
    m.block(debt, 1, "Capital structure", "(in millions)", ["Amount"], [
        ("Eurobond (EUR)", [250.0]), ("Eurobond (USD equivalent)", [270.0]), ("Term loan B (USD)", [400.0]),
    ], fmt={"Eurobond (EUR)": "#,##0.0", "Eurobond (USD equivalent)": "#,##0.0", "Term loan B (USD)": "#,##0.0"})
    model = m.save(out / "model.xlsx")

    d = Deck()

    # 1 line chart, percent labels
    d.new_slide("EBITDA margin expansion", "Adjusted EBITDA margin, 2020 - 2025E")
    cd = CategoryChartData(number_format="0.0%")
    cd.categories = YEARS
    cd.add_series("EBITDA margin", [0.20, 0.21, 0.22, 0.252, 0.235, 0.24])      # ERROR: 2023 25.2% vs 22.5%
    _chart(d, XL_CHART_TYPE.LINE_MARKERS, cd, "0.0%", legend=False)

    # 2 pie 0%
    d.new_slide("2024 revenue mix by segment")
    cd = CategoryChartData(number_format="0%")
    cd.categories = ["Industrial", "Consumer", "Healthcare", "Other"]
    cd.add_series("Revenue mix", [0.40, 0.32, 0.20, 0.10])                       # ERROR: Consumer 32% vs 30% (pie sums to 102%)
    _chart(d, XL_CHART_TYPE.PIE, cd, "0%")

    # 3 bar, $bn via format with raw dollars
    d.new_slide("Revenue growth", "Revenue in $bn")
    cd = CategoryChartData(number_format='$0.0,,,"bn"')
    cd.categories = YEARS
    cd.add_series("Revenue", [1.4205e9, 1.5129e9, 1.868e9, 1.8017e9, 1.9503e9, 2.12e9])  # ERROR: 2022 $1.9bn vs $1.7bn
    _chart(d, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, '$0.0,,,"bn"', legend=False)

    # 4 clustered column, units in series names, '#,##0'
    d.new_slide("Revenue and EBITDA")
    cd = CategoryChartData(number_format="#,##0")
    cd.categories = ["2022", "2023", "2024", "2025E"]
    cd.add_series("Revenue ($m)", [1688, 1802, 1950, 2120])
    cd.add_series("EBITDA ($m)", [371, 405, 485, 509])                           # ERROR: 2024 EBITDA 485 vs 458
    _chart(d, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, "#,##0")

    # 5 doughnut
    d.new_slide("2024 revenue by region")
    cd = CategoryChartData(number_format="0%")
    cd.categories = ["North America", "Europe", "APAC", "Rest of World"]
    cd.add_series("Revenue by region", [0.50, 0.35, 0.15, 0.05])                 # ERROR: Europe 35% vs 30%
    _chart(d, XL_CHART_TYPE.DOUGHNUT, cd, "0%")

    # 6 stacked column
    d.new_slide("Segment revenue build", "($ in millions)")
    cd = CategoryChartData(number_format="#,##0.0")
    cd.categories = ["2022", "2023", "2024"]
    cd.add_series("Industrial", [675.4, 720.7, 780.1])
    cd.add_series("Consumer", [506.5, 504.5, 585.1])                             # ERROR: 2023 504.5 vs 540.5
    cd.add_series("Healthcare", [337.7, 360.4, 390.1])
    cd.add_series("Other", [168.8, 180.1, 195.0])
    _chart(d, XL_CHART_TYPE.COLUMN_STACKED, cd, "#,##0.0")

    # 7 XY scatter, one series per peer
    d.new_slide("Valuation vs growth", "EV / LTM EBITDA vs NTM EBITDA growth")
    xy = XyChartData(number_format="0.0")
    for name, g, mult in [("Alpha Corp", 0.063, 11.2), ("Beta Holdings", 0.048, 10.1),
                          ("Gamma Group", 0.081, 12.9),                           # ERROR: 12.6x
                          ("Epsilon Inc", 0.035, 10.6), ("Zeta Ltd", 0.029, 9.4)]:
        ser = xy.add_series(name)
        ser.add_data_point(g, mult)
    ch = _chart(d, XL_CHART_TYPE.XY_SCATTER, xy, None, legend=True, width=8.0)

    # 8 units only in slide title
    d.new_slide("Capital expenditure ($m)")
    cd = CategoryChartData()
    cd.categories = ["2022", "2023", "2024", "2025E"]
    cd.add_series("Capex", [78.9, 85.6, 89.2, 104.0])                            # ERROR: 2024 89.2 vs 98.2
    _chart(d, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, "0.0", legend=False)

    # 9 unicode text + pasted picture
    d.new_slide("Key figures", "Meridian Industries - 2024 snapshot")
    d.bullets([
        "Revenue CAGR of ≈8.2% over 2020–2024",
        "EBITDA of $458–509m across 2024–2025E",
        "Euro-denominated Eurobond of €250m (≈$270m)",
        "Euro-denominated Eurobond of €205m outstanding",                    # ERROR: EUR 250.0
        "Term loan B of $400m",
    ])
    d.picture_placeholder()
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="charts_everything", deck=deck, models=[model],
        description="Every chart type with data-label formats, units in series name / title, a picture, unicode",
        expects=[
            Expect(1, ("mismatch",), "25.2%", "line chart 2023 margin"),
            Expect(2, ("mismatch", "pct_reconcile", "sum_error"), "32%", "pie slice Consumer"),
            Expect(3, ("mismatch",), "$1.9", "bar $bn 2022 (label shows $1.9bn; model $1.7bn)"),
            Expect(4, ("mismatch",), "485", "EBITDA 2024 col"),
            Expect(5, ("mismatch", "pct_reconcile", "sum_error"), "35%", "doughnut Europe"),
            Expect(6, ("mismatch", "sum_error"), "504.5", "stacked Consumer 2023"),
            Expect(7, ("mismatch",), "12.9", "scatter Gamma multiple"),
            Expect(8, ("mismatch",), "89.2", "capex 2024 (units in title)"),
            Expect(9, ("mismatch",), "205", "EUR bond"),
        ],
        controls=[
            Control(1, "20.0%"), Control(1, "21.0%"), Control(1, "22.0%"), Control(1, "23.5%"), Control(1, "24.0%"),
            Control(2, "40%"), Control(2, "20%"), Control(2, "10%"),
            Control(3, "1.4bn"), Control(3, "1.5bn"), Control(3, "1.8bn"), Control(3, "2.1bn"),
            Control(4, "1,688"), Control(4, "1,802"), Control(4, "1,950"), Control(4, "2,120"),
            Control(4, "371"), Control(4, "405"), Control(4, "509"),
            Control(5, "50%"), Control(5, "15%"),
            Control(6, "675.4"), Control(6, "720.7"), Control(6, "780.1"), Control(6, "506.5"), Control(6, "585.1"),
            Control(6, "337.7"), Control(6, "195.0"),
            Control(7, "11.2"), Control(7, "10.1"), Control(7, "10.6"), Control(7, "9.4"),
            Control(7, "6.3"), Control(7, "4.8"), Control(7, "8.1"), Control(7, "3.5"),
            Control(8, "78.9"), Control(8, "85.6"), Control(8, "104.0"),
            Control(9, "8.2%", allow=("unmatched",), note="CAGR is derived, not a model cell"), Control(9, "$458"), Control(9, "509m"), Control(9, "€250m"), Control(9, "$400m"),
        ],
        min_verified_pct=70.0,
        max_extra_high=2,
    )
