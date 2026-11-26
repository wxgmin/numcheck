"""Project Keystone - banker-style operating model + valuation workbook (formula driven).

Usage: python build_model.py <out.xlsx> [prior]
'prior' builds the previous draft (v3) with more aggressive growth assumptions.
"""
import json
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

OUT = sys.argv[1]
PRIOR = len(sys.argv) > 2 and sys.argv[2] == "prior"

NAVY = "0B2545"
GREY = "F2F4F7"
BLUE = Font(name="Arial", size=9, color="0000FF")
BLACK = Font(name="Arial", size=9, color="000000")
BOLD = Font(name="Arial", size=9, bold=True)
HDR = Font(name="Arial", size=9, bold=True, color="FFFFFF")
TITLE = Font(name="Arial", size=12, bold=True, color=NAVY)
UNITS = Font(name="Arial", size=9, italic=True, color="555555")
FILL_HDR = PatternFill("solid", fgColor=NAVY)
FILL_SEC = PatternFill("solid", fgColor=GREY)
TOP = Border(top=Side(style="thin", color="000000"))
NUM = '#,##0.0_);(#,##0.0);"–"_)'
PCT = '0.0%_);(0.0%);"–"_)'
MULT = '0.0"x"_);(0.0"x")'
DOL = '$#,##0.00_);($#,##0.00)'

YEARS = ["FY21A", "FY22A", "FY23A", "FY24A", "FY25E", "FY26E", "FY27E", "FY28E", "FY29E"]
C0 = 3                       # first year column (C)
COL = {y: L(C0 + i) for i, y in enumerate(YEARS)}
HIST, PROJ = YEARS[:4], YEARS[4:]
rows: dict[str, dict[str, int]] = {}

wb = Workbook()
wb.remove(wb.active)


def sheet(name, widths=(34, 2)):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = widths[0]
    ws.column_dimensions["B"].width = widths[1]
    for i in range(len(YEARS) + 2):
        ws.column_dimensions[L(C0 + i)].width = 11
    rows[name] = {}
    return ws


def header(ws, title, units, r=1, years=YEARS, first=C0):
    ws.cell(r, 1, title).font = TITLE
    ws.cell(r + 1, 1, units).font = UNITS
    hr = r + 3
    for c in range(1, first + len(years)):
        ws.cell(hr, c).fill = FILL_HDR
    for i, y in enumerate(years):
        cell = ws.cell(hr, first + i, y)
        cell.font, cell.alignment = HDR, Alignment(horizontal="right")
    ws.freeze_panes = ws.cell(hr + 1, first)
    return hr + 1


def section(ws, r, text):
    ws.cell(r, 1, text).font = BOLD
    for c in range(1, C0 + len(YEARS)):
        ws.cell(r, c).fill = FILL_SEC
    return r + 1


def line(ws, r, label, vals, fmt=NUM, bold=False, top=False, indent=0, key=None):
    """vals: dict year->value/formula ('{c}' = this column, '{p}' = previous column)."""
    c = ws.cell(r, 1, label)
    c.font = BOLD if bold else BLACK
    c.alignment = Alignment(indent=indent)
    for y, v in vals.items():
        col = COL[y]
        prev = L(C0 + YEARS.index(y) - 1)
        if isinstance(v, str):
            v = v.replace("{c}", col).replace("{p}", prev)
            font = BOLD if bold else BLACK
        else:
            font = BLUE                      # hard-coded input
        cell = ws[f"{col}{r}"]
        cell.value, cell.number_format = v, fmt
        cell.font = Font(name="Arial", size=9, bold=bold, color=font.color)
        if top:
            cell.border = TOP
    rows[ws.title][key or label] = r
    return r + 1


def R(sheet_name, key):
    return rows[sheet_name][key]


# ----------------------------------------------------------------------------- inputs
seg_hist = {"Flow Control": [412.0, 448.6, 489.2, 531.7],
            "Aerospace Components": [268.4, 301.2, 342.9, 384.1],
            "Aftermarket Services": [196.3, 214.8, 233.5, 252.6]}
seg_growth = {"Flow Control": [0.080, 0.075, 0.070, 0.065, 0.060],
              "Aerospace Components": [0.100, 0.090, 0.080, 0.070, 0.060],
              "Aftermarket Services": [0.070, 0.065, 0.060, 0.055, 0.050]}
if PRIOR:   # v3 draft: management's earlier, more aggressive case
    seg_growth = {"Flow Control": [0.095, 0.085, 0.075, 0.070, 0.060],
                  "Aerospace Components": [0.135, 0.110, 0.090, 0.080, 0.070],
                  "Aftermarket Services": [0.080, 0.070, 0.065, 0.060, 0.055]}
gm_hist, gm_proj = [0.365, 0.370, 0.378, 0.384], [0.388, 0.392, 0.395, 0.397, 0.398]
opex_hist, opex_proj = [0.189, 0.187, 0.186, 0.184], [0.183, 0.182, 0.180, 0.179, 0.178]
interest = [38.2, 41.5, 44.0, 46.3, 44.1, 41.0, 37.6, 34.0, 30.2]
sbc_hist, restr_hist, trans_hist = [7.4, 8.1, 9.0, 9.8], [5.2, 3.9, 8.7, 6.4], [0.0, 1.2, 0.0, 3.1]
capex_hist, capex_proj = [0.036, 0.039, 0.041, 0.038], [0.038, 0.037, 0.036, 0.035, 0.035]
dnwc_hist = [7.9, 9.1, 11.8, 12.3]

# ----------------------------------------------------------------------------- Cover
ws = wb.create_sheet("Cover")
ws.sheet_view.showGridLines = False
ws.column_dimensions["B"].width = 90
ws["B2"] = "PROJECT KEYSTONE"; ws["B2"].font = Font(name="Arial", size=20, bold=True, color=NAVY)
ws["B3"] = "Halvorsen Precision Group - Operating model and valuation"; ws["B3"].font = Font(name="Arial", size=12)
ws["B5"] = f"Version: {'v3 (superseded draft)' if PRIOR else 'v4 - Board materials'}"
ws["B6"] = "Prepared by: Aldgate Partners (fictional) - Industrials M&A"
ws["B7"] = "Status: Strictly private and confidential - preliminary draft"
ws["B9"] = "Conventions: blue font = hard-coded input; black = formula. $ in millions unless stated. FYE 31-Dec."
ws["B10"] = "All companies, transactions and figures in this file are fictional and for software testing only."
for r in range(5, 11):
    ws[f"B{r}"].font = Font(name="Arial", size=10, italic=r >= 9)

# ----------------------------------------------------------------------------- Segments
ws = sheet("Segments")
r = header(ws, "Revenue build by segment", "($ in millions, fiscal year ending December 31)")
r = section(ws, r + 1, "Revenue by segment")
seg_rows = {}
for name, hist in seg_hist.items():
    vals = {y: hist[i] for i, y in enumerate(HIST)}
    seg_rows[name] = r
    g_row_placeholder = None
    r = line(ws, r, name, vals, indent=1)
first_seg, last_seg = min(seg_rows.values()), max(seg_rows.values())
r = line(ws, r, "Total revenue", {y: f"=SUM({{c}}{first_seg}:{{c}}{last_seg})" for y in YEARS}, bold=True, top=True)
tot = R("Segments", "Total revenue")
r = line(ws, r, "Revenue growth (%)", {y: f"={{c}}{tot}/{{p}}{tot}-1" for y in YEARS[1:]}, fmt=PCT, indent=1)
r = section(ws, r + 1, "Segment growth (%)")
for name in seg_hist:
    gvals = {y: f"={{c}}{seg_rows[name]}/{{p}}{seg_rows[name]}-1" for y in HIST[1:]}
    gvals.update({y: seg_growth[name][i] for i, y in enumerate(PROJ)})
    gr = r
    r = line(ws, r, f"{name} growth (%)", gvals, fmt=PCT, indent=1)
    for y in PROJ:   # projected segment revenue = prior year x (1 + growth input)
        col, prev = COL[y], L(C0 + YEARS.index(y) - 1)
        cell = ws[f"{col}{seg_rows[name]}"]
        cell.value, cell.number_format, cell.font = f"={prev}{seg_rows[name]}*(1+{col}{gr})", NUM, BLACK
r = section(ws, r + 1, "Segment mix (% of total)")
for name in seg_hist:
    r = line(ws, r, f"{name} (% of total)", {y: f"={{c}}{seg_rows[name]}/{{c}}{tot}" for y in YEARS}, fmt=PCT, indent=1)

# ----------------------------------------------------------------------------- IS
ws = sheet("IS")
r = header(ws, "Income statement", "($ in millions, fiscal year ending December 31)")
r = section(ws, r + 1, "Income statement")
r = line(ws, r, "Revenue", {y: f"=Segments!{{c}}{tot}" for y in YEARS}, bold=True)
rev = R("IS", "Revenue")
r = line(ws, r, "Revenue growth (%)", {y: f"={{c}}{rev}/{{p}}{rev}-1" for y in YEARS[1:]}, fmt=PCT, indent=1)
asm0 = r + 22   # assumption block rows (filled below)
cogs_vals = {y: round(seg_tot * (1 - gm_hist[i]), 1) for i, (y, seg_tot) in
             enumerate(zip(HIST, [sum(v[i] for v in seg_hist.values()) for i in range(4)]))}
cogs_vals.update({y: f"={{c}}{rev}*(1-{{c}}{asm0})" for y in PROJ})
r = line(ws, r, "Cost of goods sold", cogs_vals)
cogs = R("IS", "Cost of goods sold")
r = line(ws, r, "Gross profit", {y: f"={{c}}{rev}-{{c}}{cogs}" for y in YEARS}, bold=True, top=True)
gp = R("IS", "Gross profit")
r = line(ws, r, "Gross margin (%)", {y: f"={{c}}{gp}/{{c}}{rev}" for y in YEARS}, fmt=PCT, indent=1)
opex_vals = {y: round(sum(v[i] for v in seg_hist.values()) * opex_hist[i], 1) for i, y in enumerate(HIST)}
opex_vals.update({y: f"={{c}}{rev}*{{c}}{asm0 + 1}" for y in PROJ})
r = line(ws, r, "SG&A and R&D (excl. D&A)", opex_vals)
opex = R("IS", "SG&A and R&D (excl. D&A)")
r = line(ws, r, "EBITDA", {y: f"={{c}}{gp}-{{c}}{opex}" for y in YEARS}, bold=True, top=True)
ebitda = R("IS", "EBITDA")
r = line(ws, r, "EBITDA margin (%)", {y: f"={{c}}{ebitda}/{{c}}{rev}" for y in YEARS}, fmt=PCT, indent=1)
r = line(ws, r, "Depreciation and amortization", {y: f"={{c}}{rev}*{{c}}{asm0 + 2}" for y in YEARS})
da = R("IS", "Depreciation and amortization")
r = line(ws, r, "EBIT", {y: f"={{c}}{ebitda}-{{c}}{da}" for y in YEARS}, bold=True, top=True)
ebit = R("IS", "EBIT")
r = line(ws, r, "Interest expense", {y: interest[i] for i, y in enumerate(YEARS)})
intr = R("IS", "Interest expense")
r = line(ws, r, "Pre-tax income", {y: f"={{c}}{ebit}-{{c}}{intr}" for y in YEARS}, top=True)
pti = R("IS", "Pre-tax income")
r = line(ws, r, "Income tax expense", {y: f"={{c}}{pti}*{{c}}{asm0 + 3}" for y in YEARS})
tax = R("IS", "Income tax expense")
r = line(ws, r, "Net income", {y: f"={{c}}{pti}-{{c}}{tax}" for y in YEARS}, bold=True, top=True)
r = asm0 - 1
section(ws, r, "Assumptions")
line(ws, asm0, "Gross margin assumption (%)", {y: gm_proj[i] for i, y in enumerate(PROJ)}, fmt=PCT, indent=1)
line(ws, asm0 + 1, "SG&A and R&D (% of revenue)", {y: opex_proj[i] for i, y in enumerate(PROJ)}, fmt=PCT, indent=1)
line(ws, asm0 + 2, "D&A (% of revenue)", {y: 0.035 for y in YEARS}, fmt=PCT, indent=1)
line(ws, asm0 + 3, "Effective tax rate (%)", {y: 0.24 for y in YEARS}, fmt=PCT, indent=1)

# ----------------------------------------------------------------------------- Adjusted EBITDA
ws = sheet("Adj EBITDA")
r = header(ws, "Adjusted EBITDA reconciliation", "($ in millions)")
r = section(ws, r + 1, "Reconciliation")
r = line(ws, r, "Reported EBITDA", {y: f"=IS!{{c}}{ebitda}" for y in YEARS})
rep = R("Adj EBITDA", "Reported EBITDA")
sbc_vals = {y: sbc_hist[i] for i, y in enumerate(HIST)}
sbc_vals.update({y: f"=IS!{{c}}{rev}*0.008" for y in PROJ})
r = line(ws, r, "Stock-based compensation", sbc_vals, indent=1)
r = line(ws, r, "Restructuring and integration costs", {**{y: restr_hist[i] for i, y in enumerate(HIST)},
                                                        **{y: 0.0 for y in PROJ}}, indent=1)
r = line(ws, r, "Transaction-related costs", {**{y: trans_hist[i] for i, y in enumerate(HIST)},
                                              **{y: 0.0 for y in PROJ}}, indent=1)
r = line(ws, r, "Adjusted EBITDA", {y: f"=SUM({{c}}{rep}:{{c}}{r - 1})" for y in YEARS}, bold=True, top=True)
adj = R("Adj EBITDA", "Adjusted EBITDA")
r = line(ws, r, "Adj. EBITDA margin (%)", {y: f"={{c}}{adj}/IS!{{c}}{rev}" for y in YEARS}, fmt=PCT, indent=1)

# ----------------------------------------------------------------------------- Cash flow
ws = sheet("CF")
r = header(ws, "Unlevered free cash flow", "($ in millions)")
r = section(ws, r + 1, "Unlevered free cash flow")
r = line(ws, r, "EBITDA", {y: f"=IS!{{c}}{ebitda}" for y in YEARS})
cf_e = R("CF", "EBITDA")
r = line(ws, r, "Less: taxes on EBIT", {y: f"=-IS!{{c}}{ebit}*IS!{{c}}{asm0 + 3}" for y in YEARS}, indent=1)
capex_vals = {y: f"=-IS!{{c}}{rev}*{capex_hist[i]}" for i, y in enumerate(HIST)}
capex_vals.update({y: f"=-IS!{{c}}{rev}*{capex_proj[i]}" for i, y in enumerate(PROJ)})
r = line(ws, r, "Capital expenditures", capex_vals, indent=1)
capex = R("CF", "Capital expenditures")
nwc_vals = {y: -dnwc_hist[i] for i, y in enumerate(HIST)}
nwc_vals.update({y: f"=-(IS!{{c}}{rev}-IS!{{p}}{rev})*0.12" for y in PROJ})
r = line(ws, r, "(Increase) in net working capital", nwc_vals, indent=1)
r = line(ws, r, "Unlevered free cash flow", {y: f"=SUM({{c}}{cf_e}:{{c}}{r - 1})" for y in YEARS}, bold=True, top=True)
ufcf = R("CF", "Unlevered free cash flow")
r = line(ws, r, "Cash conversion (UFCF / EBITDA, %)", {y: f"={{c}}{ufcf}/{{c}}{cf_e}" for y in YEARS}, fmt=PCT, indent=1)
r = line(ws, r, "Capex (% of revenue)", {y: f"=-{{c}}{capex}/IS!{{c}}{rev}" for y in YEARS}, fmt=PCT, indent=1)

# ----------------------------------------------------------------------------- Capitalization
ws = sheet("Cap", widths=(38, 2))
ws["A1"], ws["A1"].font = "Capitalization at 31-Dec-2024", TITLE
ws["A2"], ws["A2"].font = "($ in millions, except per share data)", UNITS
for c in (1, 2, 3, 4, 5):
    ws.cell(4, c).fill = FILL_HDR
for c, t in ((3, "FY24A"), (4, "Rate"), (5, "Maturity")):
    ws.cell(4, c, t).font, ws.cell(4, c).alignment = HDR, Alignment(horizontal="right")
cap_items = [("Cash and cash equivalents", 118.5, "", ""), ("Revolving credit facility ($250m)", 0.0, "S+275", "2029"),
             ("Term Loan B", 545.0, "S+325", "2031"), ("6.375% Senior notes", 197.0, "6.375%", "2032")]
r = 6
for label, v, rate, mat in cap_items:
    ws.cell(r, 1, label).font = BLACK
    ws.cell(r, 3, v).font, ws.cell(r, 3).number_format = BLUE, NUM
    ws.cell(r, 4, rate).font, ws.cell(r, 5, mat).font = BLACK, BLACK
    rows["Cap"][label] = r
    r += 1
cr = rows["Cap"]
ws.cell(r, 1, "Total debt").font = BOLD
ws.cell(r, 3, f"=SUM(C{cr['Revolving credit facility ($250m)']}:C{r - 1})").number_format = NUM
ws.cell(r, 3).font, ws.cell(r, 3).border = BOLD, TOP
cr["Total debt"] = r; r += 1
ws.cell(r, 1, "Net debt").font = BOLD
ws.cell(r, 3, f"=C{cr['Total debt']}-C{cr['Cash and cash equivalents']}").number_format = NUM
ws.cell(r, 3).font = BOLD
cr["Net debt"] = r; r += 2
ws.cell(r, 1, "FY24A Adjusted EBITDA").font = BLACK
ws.cell(r, 3, f"='Adj EBITDA'!{COL['FY24A']}{adj}").number_format = NUM
cr["FY24A Adjusted EBITDA"] = r; r += 1
ws.cell(r, 1, "Total debt / Adj. EBITDA").font = BLACK
ws.cell(r, 3, f"=C{cr['Total debt']}/C{cr['FY24A Adjusted EBITDA']}").number_format = MULT
cr["Total debt / Adj. EBITDA"] = r; r += 1
ws.cell(r, 1, "Net debt / Adj. EBITDA").font = BLACK
ws.cell(r, 3, f"=C{cr['Net debt']}/C{cr['FY24A Adjusted EBITDA']}").number_format = MULT
cr["Net debt / Adj. EBITDA"] = r; r += 2
ws.cell(r, 1, "Diluted shares outstanding (m)").font = BLACK
ws.cell(r, 3, 84.6).font, ws.cell(r, 3).number_format = BLUE, NUM
cr["Diluted shares outstanding (m)"] = r

# ----------------------------------------------------------------------------- DCF
ws = sheet("DCF")
dyears = PROJ
r = header(ws, "Discounted cash flow analysis", "($ in millions, except per share data)", years=dyears)
dcol = {y: L(C0 + i) for i, y in enumerate(dyears)}
src = {y: COL[y] for y in dyears}      # same year column in CF sheet
ws["A20"], ws["A21"] = "WACC", "Terminal growth rate"
ws["C20"], ws["C21"] = 0.09, 0.025
for a in ("C20", "C21"):
    ws[a].font, ws[a].number_format = BLUE, PCT
rows["DCF"]["WACC"], rows["DCF"]["Terminal growth rate"] = 20, 21
r = section(ws, r + 1, "Free cash flow and present value")
ws.cell(r, 1, "Unlevered free cash flow").font = BLACK
for y in dyears:
    c = ws[f"{dcol[y]}{r}"]; c.value, c.number_format = f"=CF!{src[y]}{ufcf}", NUM
uf = r; rows["DCF"]["Unlevered free cash flow"] = r; r += 1
ws.cell(r, 1, "Discount period (years)").font = BLACK
for i, y in enumerate(dyears):
    c = ws[f"{dcol[y]}{r}"]; c.value, c.number_format, c.font = i + 1, "0.0", BLUE
per = r; r += 1
ws.cell(r, 1, "Discount factor").font = BLACK
for y in dyears:
    c = ws[f"{dcol[y]}{r}"]; c.value, c.number_format = f"=1/(1+$C$20)^{dcol[y]}{per}", "0.000"
dfr = r; r += 1
ws.cell(r, 1, "PV of unlevered free cash flow").font = BLACK
for y in dyears:
    c = ws[f"{dcol[y]}{r}"]; c.value, c.number_format = f"={dcol[y]}{uf}*{dcol[y]}{dfr}", NUM
pvr = r; rows["DCF"]["PV of unlevered free cash flow"] = r
last = dcol[dyears[-1]]
summary = [
    ("Sum of PV of free cash flows", f"=SUM(C{pvr}:{last}{pvr})", NUM),
    ("Terminal value (Gordon growth)", f"={last}{uf}*(1+C21)/(C20-C21)", NUM),
    ("PV of terminal value", f"=C24*{last}{dfr}", NUM),
    ("Implied enterprise value", "=C23+C25", NUM),
    ("Less: net debt", f"=-Cap!C{cr['Net debt']}", NUM),
    ("Implied equity value", "=C26+C27", NUM),
    ("Diluted shares outstanding (m)", f"=Cap!C{cr['Diluted shares outstanding (m)']}", NUM),
    ("Implied share price ($)", "=C28/C29", DOL),
    ("PV of terminal value (% of EV)", "=C25/C26", PCT),
    ("Implied EV / FY24A Adj. EBITDA", f"=C26/'Adj EBITDA'!{COL['FY24A']}{adj}", MULT),
]
for i, (label, f, fmt) in enumerate(summary):
    rr = 23 + i
    ws.cell(rr, 1, label).font = BOLD if "Implied" in label else BLACK
    ws.cell(rr, 3, f).number_format = fmt
    rows["DCF"][label] = rr
# sensitivity: implied EV
ws["A35"], ws["A35"].font = "Sensitivity - implied enterprise value ($m)", BOLD
ws["A36"] = "WACC (down) / terminal growth (across)"
waccs, gs = [0.085, 0.09, 0.095], [0.02, 0.025, 0.03]
for j, g in enumerate(gs):
    c = ws.cell(36, 3 + j, g); c.number_format, c.font = PCT, BLUE
for i, w in enumerate(waccs):
    c = ws.cell(37 + i, 2, w); c.number_format, c.font = PCT, BLUE
    for j in range(3):
        gcell, wcell = f"{L(3 + j)}$36", f"$B{37 + i}"
        f = (f"=NPV({wcell},$C${uf}:${last}${uf})+${last}${uf}*(1+{gcell})/({wcell}-{gcell})/(1+{wcell})^5")
        cc = ws.cell(37 + i, 3 + j, f); cc.number_format = NUM
ws.column_dimensions["B"].width = 8

# ----------------------------------------------------------------------------- Comps
ws = wb.create_sheet("Comps"); ws.sheet_view.showGridLines = False
rows["Comps"] = {}
ws["A1"], ws["A1"].font = "Selected publicly traded comparable companies", TITLE
ws["A2"], ws["A2"].font = "($ in millions, except per share data; market data as of 14-Nov-2026)", UNITS
hdrs = ["Company", "Ticker", "Share price ($)", "Diluted shares (m)", "Equity value", "Net debt",
        "Enterprise value", "LTM EBITDA", "NTM EBITDA", "EV / LTM EBITDA", "EV / NTM EBITDA",
        "NTM revenue growth (%)", "NTM EBITDA margin (%)"]
for j, h in enumerate(hdrs):
    c = ws.cell(4, 1 + j, h); c.font, c.fill = HDR, FILL_HDR
    c.alignment = Alignment(horizontal="left" if j < 2 else "right", wrap_text=True, vertical="center")
ws.row_dimensions[4].height = 30
ws.column_dimensions["A"].width = 26
for j in range(2, 14):
    ws.column_dimensions[L(j)].width = 12
peers = [("Corvane Industries", "CRVN", 86.40, 142.5, 1240.0, 1018.0, 1092.0, 0.058, 0.194),
         ("Ridgeline Flow Systems", "RDGF", 54.10, 96.2, 610.0, 418.0, 451.0, 0.072, 0.211),
         ("Talbot Aerostructures", "TLBA", 122.75, 61.8, 935.0, 652.0, 714.0, 0.094, 0.182),
         ("Northwick Controls", "NWCK", 41.30, 118.0, 455.0, 401.0, 428.0, 0.049, 0.226),
         ("Vesper Precision", "VSPR", 67.85, 74.4, 320.0, 382.0, 417.0, 0.081, 0.203),
         ("Ostrander Motion", "OSTM", 29.60, 210.3, 1105.0, 612.0, 655.0, 0.055, 0.178)]
for i, p in enumerate(peers):
    rr = 5 + i
    name, tic, px, sh, nd, ltm, ntm, gr, mg = p
    vals = [name, tic, px, sh, f"=C{rr}*D{rr}", nd, f"=E{rr}+F{rr}", ltm, ntm, f"=G{rr}/H{rr}", f"=G{rr}/I{rr}", gr, mg]
    fmts = [None, None, DOL, NUM, NUM, NUM, NUM, NUM, NUM, MULT, MULT, PCT, PCT]
    for j, (v, fm) in enumerate(zip(vals, fmts)):
        c = ws.cell(rr, 1 + j, v)
        c.font = BLUE if (j in (2, 3, 5, 7, 8, 11, 12)) else BLACK
        if fm:
            c.number_format = fm
    rows["Comps"][name] = rr
last_p = 5 + len(peers) - 1
for k, (lab, fn) in enumerate((("Median", "MEDIAN"), ("Mean", "AVERAGE"))):
    rr = last_p + 2 + k
    ws.cell(rr, 1, lab).font = BOLD
    for j, fm in ((10, MULT), (11, MULT), (12, PCT), (13, PCT)):
        c = ws.cell(rr, j, f"={fn}({L(j)}5:{L(j)}{last_p})"); c.number_format, c.font = fm, BOLD
        if k == 0:
            c.border = TOP
    rows["Comps"][lab] = rr

# ----------------------------------------------------------------------------- Precedents
ws = wb.create_sheet("Precedents"); ws.sheet_view.showGridLines = False
rows["Precedents"] = {}
ws["A1"], ws["A1"].font = "Selected precedent transactions", TITLE
ws["A2"], ws["A2"].font = "($ in millions)", UNITS
ph = ["Announced", "Target", "Acquirer", "Enterprise value", "LTM EBITDA", "EV / LTM EBITDA"]
for j, h in enumerate(ph):
    c = ws.cell(4, 1 + j, h); c.font, c.fill = HDR, FILL_HDR
    c.alignment = Alignment(horizontal="left" if j < 3 else "right")
for col, w in zip("ABCDEF", (12, 30, 28, 15, 13, 15)):
    ws.column_dimensions[col].width = w
deals = [("Mar-2026", "Brightwater Valve Co.", "Corvane Industries", 2140.0, 171.2),
         ("Oct-2025", "Kestrel Aero Components", "Stonemoor Capital", 1385.0, 104.9),
         ("Jun-2025", "Pellham Fluid Technologies", "Northwick Controls", 865.0, 72.1),
         ("Jan-2025", "Arden Motion Systems", "Halcrest Partners", 3020.0, 241.6),
         ("Aug-2024", "Tidewell Instruments", "Ridgeline Flow Systems", 512.0, 44.5),
         ("Feb-2024", "Calloway Precision", "Ostrander Motion", 1760.0, 139.7)]
for i, (dt, tg, aq, ev, e) in enumerate(deals):
    rr = 5 + i
    for j, v in enumerate([dt, tg, aq, ev, e, f"=D{rr}/E{rr}"]):
        c = ws.cell(rr, 1 + j, v)
        c.font = BLUE if j in (3, 4) else BLACK
        c.number_format = NUM if j in (3, 4) else (MULT if j == 5 else "General")
    rows["Precedents"][tg] = rr
lp = 5 + len(deals) - 1
for k, (lab, fn) in enumerate((("Median", "MEDIAN"), ("Mean", "AVERAGE"))):
    rr = lp + 2 + k
    ws.cell(rr, 2, lab).font = BOLD
    c = ws.cell(rr, 6, f"={fn}(F5:F{lp})"); c.number_format, c.font = MULT, BOLD
    rows["Precedents"][lab] = rr

# ----------------------------------------------------------------------------- Valuation summary
ws = wb.create_sheet("Valuation"); ws.sheet_view.showGridLines = False
rows["Valuation"] = {}
ws["A1"], ws["A1"].font = "Valuation summary", TITLE
ws["A2"], ws["A2"].font = "($ in millions, except per share data)", UNITS
vh = ["Methodology", "Metric", "Metric value", "Multiple - low", "Multiple - high", "EV - low", "EV - high",
      "Equity value - low", "Equity value - high", "Per share - low", "Per share - high"]
for j, h in enumerate(vh):
    c = ws.cell(4, 1 + j, h); c.font, c.fill = HDR, FILL_HDR
    c.alignment = Alignment(horizontal="left" if j < 2 else "right", wrap_text=True)
ws.row_dimensions[4].height = 30
ws.column_dimensions["A"].width = 30; ws.column_dimensions["B"].width = 26
for j in range(3, 12):
    ws.column_dimensions[L(j)].width = 12
nd_ref, sh_ref = f"Cap!$C${cr['Net debt']}", f"Cap!$C${cr['Diluted shares outstanding (m)']}"
methods = [
    ("Trading comparables", "FY25E Adj. EBITDA", f"='Adj EBITDA'!{COL['FY25E']}{adj}", 10.5, 12.5),
    ("Precedent transactions", "FY24A Adj. EBITDA", f"='Adj EBITDA'!{COL['FY24A']}{adj}", 11.0, 13.5),
]
for i, (m, metric, mval, lo, hi) in enumerate(methods):
    rr = 5 + i
    vals = [m, metric, mval, lo, hi, f"=C{rr}*D{rr}", f"=C{rr}*E{rr}", f"=F{rr}-{nd_ref}", f"=G{rr}-{nd_ref}",
            f"=H{rr}/{sh_ref}", f"=I{rr}/{sh_ref}"]
    fm = [None, None, NUM, MULT, MULT, NUM, NUM, NUM, NUM, DOL, DOL]
    for j, (v, f) in enumerate(zip(vals, fm)):
        c = ws.cell(rr, 1 + j, v); c.font = BLUE if j in (3, 4) else BLACK
        if f:
            c.number_format = f
    rows["Valuation"][m] = rr
rr = 7
vals = ["DCF (WACC 8.5%-9.5%, TGR 2.0%-3.0%)", "Unlevered FCF FY25E-FY29E", None, None, None,
        "=MIN(DCF!C37:E39)", "=MAX(DCF!C37:E39)", f"=F{rr}-{nd_ref}", f"=G{rr}-{nd_ref}",
        f"=H{rr}/{sh_ref}", f"=I{rr}/{sh_ref}"]
fm = [None, None, None, None, None, NUM, NUM, NUM, NUM, DOL, DOL]
for j, (v, f) in enumerate(zip(vals, fm)):
    if v is None:
        continue
    c = ws.cell(rr, 1 + j, v); c.font = BLACK
    if f:
        c.number_format = f
rows["Valuation"]["DCF"] = rr
ws["A9"] = "Selected reference range"; ws["A9"].font = BOLD
ws["F9"], ws["G9"] = 2800.0, 3400.0
for a in ("F9", "G9"):
    ws[a].font, ws[a].number_format = BLUE, NUM
ws["H9"], ws["I9"] = f"=F9-{nd_ref}", f"=G9-{nd_ref}"
ws["J9"], ws["K9"] = f"=H9/{sh_ref}", f"=I9/{sh_ref}"
for a, f in (("H9", NUM), ("I9", NUM), ("J9", DOL), ("K9", DOL)):
    ws[a].number_format = f
rows["Valuation"]["Selected reference range"] = 9

wb.active = 0
wb.save(OUT)
meta = {"rows": rows, "COL": COL, "seg_rows": seg_rows, "tot": tot, "adj": adj, "ufcf": ufcf, "rev": rev,
        "gp": gp, "ebitda": ebitda, "capex": capex, "cap": cr, "dcol": dcol}
json.dump(meta, open(OUT + ".meta.json", "w"), indent=1)
print("saved", OUT)
