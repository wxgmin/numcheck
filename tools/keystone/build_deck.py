"""Project Keystone - visual board deck (charts, KPI cards, waterfall, football field) with planted errors.

Builds the NumCheck demo deck from the Excel-recalculated model. Usage:

    python tools/keystone/build_deck.py <model_v4.xlsx> <model_v3_prior.xlsx> <out.pptx>

Writes ``<out>.answers.json`` (the planted errors) next to the deck. The model must
carry cached values. Run build_model.py, then open, recalculate, and save both workbooks
in Excel, keeping the .meta.json files generated alongside them.
"""
from __future__ import annotations

import json
import sys

import openpyxl
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

NAVY = RGBColor(0x0B, 0x25, 0x45)
STEEL = RGBColor(0x3E, 0x6A, 0x96)
SKY = RGBColor(0x9D, 0xB6, 0xCF)
ICE = RGBColor(0xDC, 0xE6, 0xF0)
GREY = RGBColor(0x5F, 0x66, 0x70)
LIGHT = RGBColor(0xF2, 0xF4, 0xF7)
TOTAL = RGBColor(0xE3, 0xE9, 0xF0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLACK = RGBColor(0x1A, 0x1A, 0x1A)
RED = RGBColor(0xB0, 0x3A, 0x2E)
GREEN = RGBColor(0x2E, 0x7D, 0x4F)
PALE_GREEN = RGBColor(0x9D, 0xD0, 0xB4)
FONT = "Arial"


class Builder:
    def __init__(self, model: str, prior: str):
        self.meta = json.load(open(model + ".meta.json"))
        self.wb = openpyxl.load_workbook(model, data_only=True)
        self.wb_old = openpyxl.load_workbook(prior, data_only=True)
        self.COL, self.ROWS = self.meta["COL"], self.meta["rows"]
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(13.333), Inches(7.5)
        self.page = 0
        self.answers: list[dict] = []

    # ------------------------------------------------------------------ data
    def v(self, sheet, key, year, old=False):
        book = self.wb_old if old else self.wb
        return book[sheet][f"{self.COL[year]}{self.ROWS[sheet][key]}"].value

    def plant(self, where, shown, correct, why):
        self.answers.append({"slide": self.page, "where": where, "deck": shown, "model": correct, "why": why})
        return shown

    # ------------------------------------------------------------- primitives
    def tb(self, s, x, y, w, h, text="", size=12, bold=False, color=BLACK, align=PP_ALIGN.LEFT, italic=False,
           anchor=MSO_ANCHOR.TOP, box=None):
        if box is None:
            box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        else:
            box.left, box.top, box.width, box.height = map(Inches, (x, y, w, h))
        tf = box.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = Inches(0.04)
        for i, t in enumerate(text if isinstance(text, list) else [text]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            r = p.add_run()
            r.text = t
            f = r.font
            f.name, f.size, f.bold, f.italic, f.color.rgb = FONT, Pt(size), bold, italic, color
        return box

    def shape(self, s, kind, x, y, w, h, fill, line=None, text="", size=12, bold=False, color=WHITE,
              align=PP_ALIGN.CENTER):
        sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
        if line is None:
            sh.line.fill.background()
        else:
            sh.line.color.rgb = line
        sh.shadow.inherit = False
        if text:
            tf = sh.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            for i, t in enumerate(text if isinstance(text, list) else [text]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.alignment = align
                r = p.add_run()
                r.text = t
                r.font.name, r.font.size, r.font.bold, r.font.color.rgb = FONT, Pt(size), bold, color
        return sh

    def rect(self, s, x, y, w, h, fill, line=None):
        return self.shape(s, MSO_SHAPE.RECTANGLE, x, y, w, h, fill, line)

    def slide(self, title, units="", section=""):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[5])
        self.page += 1
        if section:
            self.tb(s, 0.45, 0.18, 6, 0.25, section.upper(), size=9, bold=True, color=STEEL)
        self.tb(s, 0.45, 0.36, 11.8, 0.6, title, size=24, bold=True, color=NAVY, box=s.shapes.title)
        self.rect(s, 0.45, 0.98, 12.43, 0.03, NAVY)
        if units:
            self.tb(s, 0.45, 1.05, 8, 0.3, units, size=10, italic=True, color=GREY)
        self.rect(s, 0.45, 7.02, 12.43, 0.01, SKY)
        self.tb(s, 0.45, 7.06, 8, 0.3, "Strictly private and confidential  |  Preliminary draft - subject to change",
                size=8, color=GREY)
        self.tb(s, 9.5, 7.06, 2.9, 0.3, "Aldgate Partners", size=8, bold=True, color=NAVY, align=PP_ALIGN.RIGHT)
        self.tb(s, 12.45, 7.06, 0.45, 0.3, str(self.page), size=8, color=GREY, align=PP_ALIGN.RIGHT)
        return s

    def footnote(self, s, lines):
        self.tb(s, 0.45, 6.5, 12.4, 0.5, lines, size=8, color=GREY)

    def bullets(self, s, x, y, w, h, items, size=12, gap=6):
        box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.word_wrap = True
        for i, t in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = Pt(gap)
            r1 = p.add_run()
            r1.text = "■  "
            r1.font.size, r1.font.color.rgb, r1.font.name = Pt(size - 4), STEEL, FONT
            r2 = p.add_run()
            r2.text = t
            r2.font.size, r2.font.color.rgb, r2.font.name = Pt(size), BLACK, FONT
        return box

    def table(self, s, x, y, col_w, data, row_h=0.3, size=10, styles=None, fills=None):
        styles, fills = styles or {}, fills or {}
        nr, nc = len(data), len(data[0])
        shp = s.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(sum(col_w)), Inches(row_h * nr))
        t = shp.table
        tbl_pr = shp._element.graphic.graphicData.tbl.tblPr
        tbl_pr.set("bandRow", "0")
        tbl_pr.set("firstRow", "0")
        for j, w in enumerate(col_w):
            t.columns[j].width = Inches(w)
        for i, row in enumerate(data):
            t.rows[i].height = Inches(row_h)
            st = styles.get(i, "")
            for j, val in enumerate(row):
                cell = t.cell(i, j)
                cell.margin_left = cell.margin_right = Inches(0.06)
                cell.margin_top = cell.margin_bottom = Inches(0.02)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                cell.fill.solid()
                cell.fill.fore_color.rgb = fills.get((i, j)) or (
                    NAVY if i == 0 else TOTAL if st == "total" else LIGHT if st == "section" else WHITE)
                p = cell.text_frame.paragraphs[0]
                p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT
                r = p.add_run()
                r.text = "" if val is None else str(val)
                f = r.font
                f.name, f.size = FONT, Pt(size)
                f.bold = i == 0 or st in ("total", "section")
                f.italic = st == "sub"
                f.color.rgb = WHITE if i == 0 else (GREY if st == "sub" else BLACK)
        return t

    def chart(self, s, kind, x, y, w, h, cats, series, fmt="#,##0.0", labels=True, legend=True, colors=None,
              label_pos=None, gap=60, axis=False, overlap=None):
        cd = CategoryChartData(number_format=fmt)
        cd.categories = cats
        for name, vals in series:
            cd.add_series(name, vals)
        gf = s.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), cd)
        ch = gf.chart
        ch.font.name, ch.font.size = FONT, Pt(9)
        ch.has_legend = legend and len(series) > 1
        if ch.has_legend:
            ch.legend.position = XL_LEGEND_POSITION.BOTTOM
            ch.legend.include_in_layout = False
            ch.legend.font.size = Pt(9)
        plot = ch.plots[0]
        try:
            plot.gap_width = gap
            if overlap is not None:
                plot.overlap = overlap
        except Exception:  # noqa: BLE001 - not every plot has gaps
            pass
        if kind not in (XL_CHART_TYPE.DOUGHNUT, XL_CHART_TYPE.PIE):
            ch.value_axis.visible = axis
            ch.value_axis.has_major_gridlines = False
            ch.category_axis.tick_labels.font.size = Pt(9)
            ch.category_axis.format.line.color.rgb = SKY
        if labels:
            plot.has_data_labels = True
            dl = plot.data_labels
            dl.number_format, dl.number_format_is_linked = fmt, False
            dl.font.size = Pt(9)
            if label_pos is not None:
                dl.position = label_pos
        for ser, c in zip(plot.series, colors or [NAVY, STEEL, SKY, ICE]):
            ser.format.fill.solid()
            ser.format.fill.fore_color.rgb = c
            if kind in (XL_CHART_TYPE.LINE_MARKERS, XL_CHART_TYPE.LINE):
                ser.format.line.color.rgb = c
                ser.format.line.width = Pt(2.25)
                ser.smooth = False
        return ch

    @staticmethod
    def hide_series(ser):
        """Banker trick for waterfalls and football fields: an invisible base series."""
        ser.format.fill.background()
        ser.format.line.fill.background()

    @staticmethod
    def label_value(shape, label):
        shape._element.find(".//" + qn("p:cNvPr")).set("title", label)

    def card(self, s, x, y, w, big, small, accent=STEEL, h=1.15):
        """KPI card as one grouped shape: accent bar + big number + caption."""
        grp = s.shapes.add_group_shape()
        bg = grp.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        bg.fill.solid(); bg.fill.fore_color.rgb = LIGHT; bg.line.fill.background(); bg.shadow.inherit = False
        bar = grp.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(0.07), Inches(h))
        bar.fill.solid(); bar.fill.fore_color.rgb = accent; bar.line.fill.background(); bar.shadow.inherit = False
        for text, yy, size, bold, color in ((big, y + 0.12, 24, True, NAVY), (small, y + 0.68, 10, False, GREY)):
            t = grp.shapes.add_textbox(Inches(x + 0.22), Inches(yy), Inches(w - 0.3), Inches(0.45))
            t.text_frame.word_wrap = True
            r = t.text_frame.paragraphs[0].add_run()
            r.text = text
            r.font.name, r.font.size, r.font.bold, r.font.color.rgb = FONT, Pt(size), bold, color
            if bold:
                self.label_value(t, small)
        return grp

    def save(self, out):
        self.prs.save(out)
        json.dump(self.answers, open(out + ".answers.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def m1(x):
    return f"{x:,.1f}"


def neg(x):
    return f"({abs(x):,.1f})" if x < 0 else f"{x:,.1f}"


def pct(x, d=1):
    return f"{x * 100:.{d}f}%"


def mx(x):
    return f"{x:.1f}x"


def build(model: str, prior: str, out: str) -> list[dict]:
    b = Builder(model, prior)
    wb, ROWS, COL = b.wb, b.ROWS, b.COL
    Y = list(COL)
    HY = ["FY21A", "FY22A", "FY23A", "FY24A", "FY25E", "FY26E", "FY27E"]
    rev = {y: b.v("IS", "Revenue", y) for y in Y}
    gm = {y: b.v("IS", "Gross margin (%)", y) for y in Y}
    grow = {y: b.v("IS", "Revenue growth (%)", y) for y in Y if y != "FY21A"}
    ebitda = {y: b.v("IS", "EBITDA", y) for y in Y}
    adj = {y: b.v("Adj EBITDA", "Adjusted EBITDA", y) for y in Y}
    adjm = {y: b.v("Adj EBITDA", "Adj. EBITDA margin (%)", y) for y in Y}
    adjm_old = {y: b.v("Adj EBITDA", "Adj. EBITDA margin (%)", y, old=True) for y in Y}
    sbc = b.v("Adj EBITDA", "Stock-based compensation", "FY24A")
    restr = b.v("Adj EBITDA", "Restructuring and integration costs", "FY24A")
    trans = b.v("Adj EBITDA", "Transaction-related costs", "FY24A")
    ufcf = {y: b.v("CF", "Unlevered free cash flow", y) for y in Y}
    segs = ["Flow Control", "Aerospace Components", "Aftermarket Services"]
    seg = {n: {y: b.v("Segments", n, y) for y in Y} for n in segs}
    cap = {k: wb["Cap"][f"C{r}"].value for k, r in b.meta["cap"].items()}
    dcf = {k: wb["DCF"][f"C{r}"].value for k, r in ROWS["DCF"].items()}
    dws, val, cws, pws = wb["DCF"], wb["Valuation"], wb["Comps"], wb["Precedents"]
    cagr = (rev["FY27E"] / rev["FY24A"]) ** (1 / 3) - 1

    # ================================================================== 1 cover
    s = b.prs.slides.add_slide(b.prs.slide_layouts[5])
    b.page += 1
    b.rect(s, 8.9, 0, 4.433, 7.5, NAVY)
    b.rect(s, 8.75, 0, 0.15, 7.5, STEEL)
    for i, (big, small) in enumerate(((f"${rev['FY24A']:,.0f}m", "FY24A revenue"),
                                      (f"${adj['FY24A']:,.1f}m", "FY24A Adjusted EBITDA"),
                                      (pct(adjm["FY24A"]), "Adj. EBITDA margin"))):
        b.tb(s, 9.5, 2.0 + i * 1.3, 3.5, 0.6, big, size=28, bold=True, color=WHITE)
        b.tb(s, 9.5, 2.62 + i * 1.3, 3.5, 0.3, small, size=11, color=SKY)
    b.tb(s, 0.9, 2.3, 7.5, 0.9, "Project Keystone", size=40, bold=True, color=NAVY, box=s.shapes.title)
    b.tb(s, 0.9, 3.2, 7.5, 0.8, "Discussion materials for the Board of Directors of Halvorsen Precision Group",
         size=17, color=GREY)
    b.rect(s, 0.9, 4.15, 2.0, 0.05, STEEL)
    b.tb(s, 0.9, 4.35, 6, 0.4, "November 2026", size=13)
    b.tb(s, 0.9, 6.4, 7.5, 0.4, "Strictly private and confidential  |  Preliminary draft", size=10, color=GREY)

    # ======================================================= 2 executive summary
    s = b.slide("Executive summary", section="Overview")
    e_lev = b.plant("KPI card - net leverage", "2.8x", mx(cap["Net debt / Adj. EBITDA"]),
                    "Card not updated after the Q4 refinancing")
    cards = [(f"${rev['FY24A']:,.0f}m", "FY24A revenue"), (f"${adj['FY24A']:,.1f}m", "FY24A Adjusted EBITDA"),
             (pct(adjm["FY24A"]), "FY24A Adj. EBITDA margin"), (e_lev, "Net debt / FY24A Adj. EBITDA")]
    for i, (big, small) in enumerate(cards):
        b.card(s, 0.45 + i * 3.13, 1.35, 2.95, big, small, accent=[NAVY, STEEL, STEEL, SKY][i])
    e_grow = b.plant("Bullet 2 - FY24A revenue growth", "11.6%", pct(grow["FY24A"]), "Growth rate from an old draft")
    b.bullets(s, 0.45, 2.85, 7.9, 3.5, [
        "Halvorsen is a leading designer and manufacturer of engineered flow-control and precision aerospace "
        "components serving more than 1,400 OEM and aftermarket customers",
        f"Revenue grew {e_grow} in FY24A to ${rev['FY24A']:,.1f}m, led by Aerospace Components "
        f"(+{pct(seg['Aerospace Components']['FY24A'] / seg['Aerospace Components']['FY23A'] - 1)})",
        f"Management projects revenue of ${rev['FY27E']:,.0f}m by FY27E, a {pct(cagr)} CAGR, with Adjusted "
        f"EBITDA margin expanding to {pct(adjm['FY27E'])}",
        f"Preliminary enterprise value reference range of ${val['F9'].value / 1000:,.1f}bn – "
        f"${val['G9'].value / 1000:,.1f}bn",
    ], size=12)
    # investment highlights as a chevron process
    b.tb(s, 8.75, 2.85, 4.1, 0.35, "Investment highlights", size=12, bold=True, color=NAVY)
    for i, t in enumerate(["Mission-critical, spec-in products", "Diversified end markets",
                           "Recurring aftermarket base", "Margin expansion runway"]):
        b.shape(s, MSO_SHAPE.CHEVRON if i else MSO_SHAPE.PENTAGON, 8.75, 3.3 + i * 0.75, 4.1, 0.6,
                [NAVY, STEEL, STEEL, SKY][i], text=t, size=11, bold=True)
    b.footnote(s, ["Source: Company management projections (model v4); Aldgate Partners analysis.",
                   "Note: Adjusted EBITDA excludes stock-based compensation, restructuring and transaction costs."])

    # ======================================================== 3 financial overview
    s = b.slide("Financial overview", "($ in millions)", section="Financial overview")
    e_rev23 = b.plant("Column chart - FY23A revenue bar", "1,056.6", m1(rev["FY23A"]),
                      "Transposed digits typed into the chart data sheet")
    rev_series = [round(rev[y], 1) for y in HY]
    rev_series[HY.index("FY23A")] = 1056.6
    adj_old25 = b.v("Adj EBITDA", "Adjusted EBITDA", "FY25E", old=True)
    b.plant("Column chart - FY25E Adjusted EBITDA bar", m1(adj_old25), m1(adj["FY25E"]),
            "Stale: chart data still from the superseded v3 model")
    adj_series = [round(adj[y], 1) for y in HY]
    adj_series[HY.index("FY25E")] = round(adj_old25, 1)
    b.tb(s, 0.45, 1.4, 7.6, 0.3, "Revenue and Adjusted EBITDA", size=12, bold=True, color=NAVY)
    b.chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, 0.45, 1.7, 7.6, 4.2, HY,
            [("Revenue", rev_series), ("Adjusted EBITDA", adj_series)],
            fmt="#,##0.0", label_pos=XL_LABEL_POSITION.OUTSIDE_END, gap=45, overlap=-10)
    # historical / projected band
    b.shape(s, MSO_SHAPE.RECTANGLE, 0.55, 5.95, 4.15, 0.28, ICE, text="Historical", size=9, color=NAVY, bold=True)
    b.shape(s, MSO_SHAPE.RECTANGLE, 4.75, 5.95, 3.2, 0.28, SKY, text="Management projections", size=9,
            color=NAVY, bold=True)
    margins = [round(adjm[y], 4) for y in HY]
    b.tb(s, 8.4, 1.4, 4.5, 0.3, "Margin progression (%)", size=12, bold=True, color=NAVY)
    b.chart(s, XL_CHART_TYPE.LINE_MARKERS, 8.4, 1.7, 4.5, 2.6, HY,
            [("Gross margin", [round(gm[y], 4) for y in HY]), ("Adj. EBITDA margin", margins)],
            fmt="0.0%", label_pos=XL_LABEL_POSITION.ABOVE, colors=[SKY, NAVY])
    _ = adjm_old
    b.bullets(s, 8.4, 4.5, 4.5, 1.9, [
        f"Gross margin up {round((gm['FY24A'] - gm['FY21A']) * 10_000):.0f} bps since FY21A on pricing and mix",
        f"FY24A unlevered free cash flow of ${ufcf['FY24A']:,.1f}m",
    ], size=11)
    b.footnote(s, ["Source: Company audited accounts (FY21A–FY24A) and management projections (model v4)."])

    # ========================================================= 4 segment mix
    s = b.slide("Diversified segment portfolio", "($ in millions)", section="Segment performance")
    mix = [seg[n]["FY24A"] / rev["FY24A"] for n in segs]
    e_mix = b.plant("Doughnut - Aftermarket Services share", "23.6%", pct(mix[2]),
                    "Mix label typed by hand; slices no longer add to 100%")
    mix_shown = [round(mix[0], 3), round(mix[1], 3), 0.236]
    b.tb(s, 0.45, 1.4, 4.6, 0.3, "FY24A revenue mix", size=12, bold=True, color=NAVY)
    ch = b.chart(s, XL_CHART_TYPE.DOUGHNUT, 0.45, 1.7, 4.6, 4.3, segs, [("FY24A revenue mix", mix_shown)],
                 fmt="0.0%", legend=True)
    for i, c in enumerate((NAVY, STEEL, SKY)):
        pt = ch.plots[0].series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = c
    ch.plots[0].data_labels.font.color.rgb = WHITE
    ch.plots[0].data_labels.font.bold = True
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    b.tb(s, 1.85, 3.35, 1.8, 0.5, [f"${rev['FY24A']:,.0f}m", "FY24A"], size=13, bold=True, color=NAVY,
         align=PP_ALIGN.CENTER)
    e_aero = b.plant("Segment card - Aerospace Components FY24A revenue", "$348.1m",
                     f"${seg['Aerospace Components']['FY24A']:,.1f}m", "Transposed digits on the card")
    for i, n in enumerate(segs):
        y = 1.55 + i * 1.6
        g = seg[n]["FY24A"] / seg[n]["FY23A"] - 1
        rev_txt = e_aero if n == "Aerospace Components" else f"${seg[n]['FY24A']:,.1f}m"
        b.rect(s, 5.4, y, 7.45, 1.4, LIGHT)
        b.rect(s, 5.4, y, 0.08, 1.4, [NAVY, STEEL, SKY][i])
        b.tb(s, 5.65, y + 0.12, 4.3, 0.35, n, size=14, bold=True, color=NAVY)
        b.tb(s, 5.65, y + 0.5, 4.3, 0.8, [
            ["Valves, actuators and flow meters for process industries",
             "Precision structural and engine components for narrowbody programmes",
             "Repair, overhaul and spares across the installed base"][i]], size=10, color=GREY)
        b.tb(s, 10.0, y + 0.12, 1.4, 0.3, "FY24A revenue", size=9, color=GREY, align=PP_ALIGN.RIGHT)
        amount = b.tb(s, 10.0, y + 0.42, 1.4, 0.45, rev_txt, size=16, bold=True, color=NAVY, align=PP_ALIGN.RIGHT)
        b.label_value(amount, f"{n} FY24A revenue")
        b.shape(s, MSO_SHAPE.OVAL, 11.65, y + 0.25, 0.95, 0.95, GREEN, text=[f"+{pct(g)}", "YoY"], size=10,
                bold=True)
    b.footnote(s, ["Source: Company management accounts. YoY = FY24A segment revenue growth versus FY23A."])
    _ = e_mix

    # ====================================================== 5 Adj. EBITDA bridge
    s = b.slide("FY24A Adjusted EBITDA bridge", "($ in millions)", section="Adjusted EBITDA")
    e_restr = b.plant("Waterfall - restructuring bar", "4.6", m1(restr),
                      "Digits transposed in the chart data; bridge no longer reaches Adjusted EBITDA")
    restr_shown = 4.6
    cats = ["Reported EBITDA", "Stock-based compensation", "Restructuring and integration", "Transaction costs",
            "Adjusted EBITDA"]
    vis = [round(ebitda["FY24A"], 1), round(sbc, 1), restr_shown, round(trans, 1), round(adj["FY24A"], 1)]
    base = [0, vis[0], vis[0] + vis[1], vis[0] + vis[1] + vis[2], 0]
    ch = b.chart(s, XL_CHART_TYPE.COLUMN_STACKED, 0.45, 1.5, 8.2, 4.8, cats,
                 [("Base", base), ("FY24A Adjusted EBITDA bridge", vis)], fmt="#,##0.0", labels=False,
                 legend=False, gap=40)
    plot = ch.plots[0]
    b.hide_series(plot.series[0])
    ser = plot.series[1]
    ser.data_labels.show_value = True
    ser.data_labels.number_format, ser.data_labels.number_format_is_linked = "#,##0.0", False
    ser.data_labels.position = XL_LABEL_POSITION.INSIDE_END
    ser.data_labels.font.size, ser.data_labels.font.bold = Pt(10), True
    ser.data_labels.font.color.rgb = WHITE
    for i in range(5):
        pt = ser.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = NAVY if i in (0, 4) else PALE_GREEN
        if i not in (0, 4):
            pt.data_label.font.color.rgb = NAVY
            pt.data_label.font.size = Pt(10)
            pt.data_label.font.bold = True
    b.table(s, 9.0, 1.6, [2.6, 1.25], [
        ["Adjustments", "FY24A"],
        ["Stock-based compensation", m1(sbc)],
        ["Restructuring and integration", m1(restr)],
        ["Transaction-related costs", m1(trans)]], row_h=0.38, size=10)
    b.bullets(s, 9.0, 3.75, 3.85, 2.4, [
        "Restructuring relates to the consolidation of two European plants (completed Q3 FY24)",
        "Transaction costs reflect the aborted FY24 bolt-on acquisition",
    ], size=10)
    b.footnote(s, ["Source: Company audited financial statements; management adjustments (unaudited)."])
    _ = e_restr

    # ===================================================== 6 trading comps (bar)
    s = b.slide("Trading comparables", "(EV / NTM EBITDA; market data as of 14-Nov-2026)", section="Valuation")
    rows = list(range(5, 11))
    peers = sorted(((cws[f"A{r}"].value, cws[f"K{r}"].value) for r in rows), key=lambda t: -t[1])
    e_vesper = b.plant("Bar chart - Vesper Precision EV / NTM EBITDA", "13.9x",
                       mx(cws[f"K{ROWS['Comps']['Vesper Precision']}"].value), "Wrong value in chart data")
    vals = [13.9 if n == "Vesper Precision" else round(x, 1) for n, x in peers]
    ch = b.chart(s, XL_CHART_TYPE.BAR_CLUSTERED, 0.45, 1.45, 7.4, 4.9, [n for n, _ in peers][::-1],
                 [("EV / NTM EBITDA", vals[::-1])], fmt='0.0"x"', label_pos=XL_LABEL_POSITION.OUTSIDE_END,
                 legend=False, gap=50)
    med = cws[f"K{ROWS['Comps']['Median']}"].value
    b.shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 2.6, 1.45, 2.4, 0.42, STEEL, text=f"Median: {mx(med)}", size=11,
            bold=True)
    data = [["Company", "EV ($m)", "EV / LTM EBITDA", "NTM growth"]]
    for r in rows:
        data.append([cws[f"A{r}"].value, f"{cws[f'G{r}'].value:,.0f}", mx(cws[f"J{r}"].value),
                     pct(cws[f"L{r}"].value)])
    mrow = ROWS["Comps"]["Median"]
    data.append(["Median", "", mx(cws[f"J{mrow}"].value), pct(cws[f"L{mrow}"].value)])
    b.table(s, 8.15, 1.5, [2.25, 0.95, 0.95, 0.6 + 0.1], data, row_h=0.4, size=9, styles={7: "total"})
    b.footnote(s, ["Source: Company filings and consensus estimates (illustrative). All peers are fictional.",
                   "Note: NTM = next twelve months; EV = equity value plus net debt."])
    _ = e_vesper

    # ==================================================== 7 precedents
    s = b.slide("Precedent transactions", "($ in millions)", section="Valuation")
    data = [["Announced", "Target", "Acquirer", "EV", "EV / LTM EBITDA"]]
    for r in range(5, 11):
        data.append([pws[f"A{r}"].value, pws[f"B{r}"].value, pws[f"C{r}"].value, f"{pws[f'D{r}'].value:,.0f}",
                     mx(pws[f"F{r}"].value)])
    pm = ROWS["Precedents"]["Median"]
    data.append(["", "Median", "", "", mx(pws[f"F{pm}"].value)])
    t = b.table(s, 0.45, 1.5, [1.15, 2.6, 2.4, 0.9, 1.3], data, row_h=0.42, size=10, styles={7: "total"})
    for i in range(len(data)):
        for j in (1, 2):
            t.cell(i, j).text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.tb(s, 9.2, 1.4, 3.7, 0.3, "EV / LTM EBITDA", size=12, bold=True, color=NAVY)
    b.chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, 9.2, 1.7, 3.7, 3.6, [pws[f"A{r}"].value for r in range(5, 11)],
            [("EV / LTM EBITDA", [round(pws[f"F{r}"].value, 1) for r in range(5, 11)])], fmt='0.0"x"',
            label_pos=XL_LABEL_POSITION.OUTSIDE_END, legend=False, colors=[STEEL])
    b.footnote(s, ["Source: Press releases and filings (illustrative). All transactions are fictional."])

    # ====================================================== 8 DCF + heatmap
    s = b.slide("Discounted cash flow analysis", "($ in millions, except per share data)", section="Valuation")
    dc = b.meta["dcol"]
    dy = list(dc)
    r_uf, r_pv = ROWS["DCF"]["Unlevered free cash flow"], ROWS["DCF"]["PV of unlevered free cash flow"]
    fcf_table = b.table(s, 0.45, 1.45, [3.0] + [1.0] * 5, [
        ["", "Projected cash flows", "", "", "", ""],
        [""] + dy,
        ["Unlevered free cash flow"] + [m1(dws[f"{dc[y]}{r_uf}"].value) for y in dy],
        ["PV of unlevered free cash flow"] + [m1(dws[f"{dc[y]}{r_pv}"].value) for y in dy]],
        row_h=0.34, size=10, styles={3: "total"})
    fcf_table.cell(0, 1).merge(fcf_table.cell(0, 5))
    fcf_table.cell(0, 1).text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    steps = [("Sum of PV of FCF", dcf["Sum of PV of free cash flows"]), ("PV of terminal value", dcf["PV of terminal value"]),
             ("Enterprise value", dcf["Implied enterprise value"]), ("Net debt", dcf["Less: net debt"]),
             ("Equity value", dcf["Implied equity value"])]
    b.tb(s, 0.45, 2.85, 7.5, 0.3, "Enterprise to equity value bridge", size=12, bold=True, color=NAVY)
    for i, (lab, v_) in enumerate(steps):
        x = 0.45 + i * 1.6
        fill = NAVY if lab in ("Enterprise value", "Equity value") else (RED if v_ < 0 else STEEL)
        b.shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 3.25, 1.45, 1.05, fill, text=[neg(v_), lab], size=11, bold=True)
    b.shape(s, MSO_SHAPE.OVAL, 2.4, 4.6, 2.3, 1.5, NAVY,
            text=[f"${dcf['Implied share price ($)']:,.2f}", "implied value per share"], size=14, bold=True)
    b.tb(s, 0.45, 4.75, 1.9, 1.2, ["WACC 9.0%", "TGR 2.5%", f"{dcf['Diluted shares outstanding (m)']:.1f}m shares"],
         size=10, color=GREY)
    e_sens = b.plant("Sensitivity heatmap - WACC 9.5% / TGR 2.5%", "2,854", f"{dws.cell(39, 4).value:,.0f}",
                     "One cell overwritten by hand in the pasted sensitivity table")
    sens = [["WACC \\ TGR", "2.0%", "2.5%", "3.0%"]]
    fills = {}
    lo, hi = dws.cell(39, 3).value, dws.cell(37, 5).value
    for i, w in enumerate(("8.5%", "9.0%", "9.5%")):
        row = [w]
        for j in range(3):
            v_ = dws.cell(37 + i, 3 + j).value
            row.append(e_sens if (i, j) == (2, 1) else f"{v_:,.0f}")
            t_ = (v_ - lo) / (hi - lo)
            fills[(i + 1, j + 1)] = RGBColor(int(0xF2 - t_ * 0x40), int(0xF4 - t_ * 0x2A), int(0xF7 - t_ * 0x12))
        sens.append(row)
    b.tb(s, 8.3, 2.85, 4.5, 0.3, "Implied enterprise value sensitivity ($m)", size=12, bold=True, color=NAVY)
    b.table(s, 8.3, 3.25, [1.35, 1.05, 1.05, 1.05], sens, row_h=0.5, size=11, fills=fills)
    b.footnote(s, ["Source: Management projections (model v4); Aldgate Partners analysis. End-of-period discounting;",
                   "terminal value by Gordon growth. Valuation date 31-Dec-2024."])

    # ===================================================== 9 football field
    s = b.slide("Preliminary valuation summary", "(Implied enterprise value, $ in millions)", section="Valuation")
    meths = [("Trading comparables\n10.5x–12.5x FY25E Adj. EBITDA", val["F5"].value, val["G5"].value),
             ("Precedent transactions\n11.0x–13.5x FY24A Adj. EBITDA", val["F6"].value, val["G6"].value),
             ("Discounted cash flow\nWACC 8.5%–9.5%, TGR 2.0%–3.0%", val["F7"].value, val["G7"].value)]
    e_ff = b.plant("Football field - DCF high-end label", "3,740", f"{val['G7'].value:,.0f}",
                   "Label typed by hand next to the bar (digits transposed)")
    # The endpoints are editable numeric callouts. The bars show their geometric range.
    plot_l, plot_w = 3.6, 5.1

    def xp(value):
        return plot_l + (value - 2000) / 2000 * plot_w

    for tick in range(2000, 4001, 500):
        x = xp(tick)
        b.rect(s, x, 1.75, 0.01, 3.9, ICE)
    for i, (name, lo_, hi_) in enumerate(meths):
        y = 2.07 + 1.28 * i
        b.tb(s, 0.45, y - 0.04, 2.32, 0.70, name.splitlines(), size=10, color=NAVY)
        b.rect(s, xp(lo_), y, xp(hi_) - xp(lo_), 0.4, STEEL)
        lo_box = b.tb(s, xp(lo_) - 0.83, y, 0.8, 0.35, f"{lo_:,.0f}", size=10, bold=True, align=PP_ALIGN.RIGHT)
        hi_box = b.tb(s, xp(hi_) + 0.05, y, 0.8, 0.35, e_ff if i == 2 else f"{hi_:,.0f}", size=10, bold=True)
        method = ("Trading comparables", "Precedent transactions", "DCF")[i]
        b.label_value(lo_box, f"{method} EV low")
        b.label_value(hi_box, f"{method} EV high")
    rlo, rhi = val["F9"].value, val["G9"].value
    b.rect(s, 9.75, 1.5, 3.1, 4.5, LIGHT)
    b.tb(s, 9.95, 1.65, 2.8, 0.3, "Reference range", size=12, bold=True, color=NAVY)
    b.tb(s, 9.95, 2.05, 2.8, 0.5, f"${rlo:,.0f}m – ${rhi:,.0f}m", size=18, bold=True, color=NAVY)
    b.tb(s, 9.95, 2.6, 2.8, 0.3, "implied enterprise value", size=10, color=GREY)
    b.tb(s, 9.95, 3.2, 2.8, 0.5, f"${val['J9'].value:,.2f} – ${val['K9'].value:,.2f}", size=18, bold=True,
         color=NAVY)
    b.tb(s, 9.95, 3.75, 2.8, 0.3, "implied value per share", size=10, color=GREY)
    b.tb(s, 9.95, 4.35, 2.8, 1.5, [f"{mx(rlo / adj['FY24A'])}–{mx(rhi / adj['FY24A'])} FY24A Adjusted EBITDA "
                                   f"of ${adj['FY24A']:,.1f}m"], size=10, color=GREY)
    b.footnote(s, [f"Source: Aldgate Partners analysis. Net debt of ${cap['Net debt']:,.1f}m and "
                   f"{dcf['Diluted shares outstanding (m)']:.1f}m diluted shares as of 31-Dec-2024."])
    _ = e_ff

    # ===================================================== 10 capitalization
    s = b.slide("Capitalization and leverage", "($ in millions)", section="Capital structure")
    e_td = b.plant("Capitalization table - total debt", "724.0", m1(cap["Total debt"]),
                   "Transposed digits; tranches no longer add to the total")
    data = [["", "Amount", "Pricing", "Maturity"],
            ["Revolving credit facility ($250m)", "–", "SOFR + 275 bps", "2029"],
            ["Term Loan B", m1(cap["Term Loan B"]), "SOFR + 325 bps", "2031"],
            ["6.375% Senior notes", m1(cap["6.375% Senior notes"]), "6.375%", "2032"],
            ["Total debt", e_td, "", ""],
            ["Cash and cash equivalents", m1(cap["Cash and cash equivalents"]), "", ""],
            ["Net debt", m1(cap["Net debt"]), "", ""]]
    b.table(s, 0.45, 1.5, [3.2, 1.2, 1.6, 1.1], data, row_h=0.45, size=11, styles={4: "total", 6: "total"})
    tl, sn = cap["Term Loan B"], cap["6.375% Senior notes"]
    b.tb(s, 7.9, 1.4, 4.9, 0.3, "Debt mix", size=12, bold=True, color=NAVY)
    ch = b.chart(s, XL_CHART_TYPE.DOUGHNUT, 7.9, 1.7, 2.9, 3.0, ["Term Loan B", "Senior notes"],
                 [("Debt mix", [round(tl / (tl + sn), 3), round(sn / (tl + sn), 3)])], fmt="0.0%", legend=True)
    for i, c in enumerate((NAVY, SKY)):
        pt = ch.plots[0].series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = c
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    for i, (big, small) in enumerate(((mx(cap["Total debt / Adj. EBITDA"]), "Total debt / FY24A Adj. EBITDA"),
                                      (mx(cap["Net debt / Adj. EBITDA"]), "Net debt / FY24A Adj. EBITDA"))):
        b.shape(s, MSO_SHAPE.OVAL, 11.05, 1.75 + i * 1.6, 1.3, 1.3, NAVY if i else STEEL, text=big, size=18,
                bold=True)
        b.tb(s, 10.75, 3.07 + i * 1.6, 1.9, 0.3, small, size=8, color=GREY, align=PP_ALIGN.CENTER)
    b.bullets(s, 0.45, 5.0, 7.2, 1.3, ["No maturities before 2029; $250m revolver undrawn at year end",
                                        "Fixed-rate notes represent around a quarter of total debt"], size=11)
    b.footnote(s, ["Source: Company balance sheet as of 31-Dec-2024 (unaudited)."])
    _ = e_td

    # ======================================================= 11 milestones
    s = b.slide("Track record of disciplined growth", section="Overview")
    ms = [("2016", "Founded through carve-out of a diversified industrial group"),
          ("2019", "Acquired Kestrel Seals; entered aftermarket services"),
          ("2021", "Opened Monterrey plant; 40% capacity increase"),
          ("2023", "Selected for two new narrowbody engine programmes"),
          ("FY24A", f"Record revenue of ${rev['FY24A']:,.1f}m and Adj. EBITDA of ${adj['FY24A']:,.1f}m")]
    conn = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(0.9), Inches(3.3), Inches(12.4), Inches(3.3))
    conn.line.color.rgb, conn.line.width = SKY, Pt(3)
    for i, (yr, txt) in enumerate(ms):
        x = 0.7 + i * 2.45
        b.shape(s, MSO_SHAPE.OVAL, x + 0.65, 2.95, 0.7, 0.7, NAVY if i == 4 else STEEL)
        b.tb(s, x, 2.25, 2.0, 0.4, yr, size=16, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
        b.tb(s, x - 0.1, 3.85, 2.2, 1.6, txt, size=11, color=BLACK, align=PP_ALIGN.CENTER)
    b.footnote(s, ["Source: Company information."])

    # ========================================================= 12 notice
    s = b.slide("Important notice")
    b.tb(s, 0.45, 1.4, 12.4, 4.5, [
        "These materials were prepared by Aldgate Partners for the Board of Directors of Halvorsen Precision Group "
        "in connection with Project Keystone and may not be used for any other purpose.", "",
        "THIS DOCUMENT IS FICTIONAL AND WAS GENERATED TO DEMONSTRATE NUMCHECK. All companies, people, transactions "
        "and figures are invented; any resemblance to real entities is coincidental."], size=12, color=GREY)

    _ = Emu
    b.save(out)
    return b.answers


if __name__ == "__main__":
    for a in build(*sys.argv[1:4]):
        print(a)
