"""Messy real-world models: three workbooks (operating model, DCF, comps) for a June-FYE company.

Mess planted in the models:
* numbers stored as text ("1,820.4", "238.9", "14.2%"), "n.m." and "-" cells
* named ranges for headline outputs
* hidden "Backup" sheet = older copy of the P&L with different values
* units in a Unit column ("$m", "%", "x") instead of banners
* merged group headers (fiscal vs calendar years; "Trading multiples" / "Operating metrics")
* a transposed sheet (periods down column A)
* an #N/A error cell that a deck number maps to
* June fiscal year (FY2024 = Jul-23..Jun-24) next to calendar CY2024 columns
"""

from __future__ import annotations

from pathlib import Path

from openpyxl.styles import Font

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario


def build(out: Path) -> Scenario:
    # ------------------------------------------------------------------ operating model
    m = Model()
    ws = m.sheet("P&L")
    ws["A1"] = "Operating model - summary P&L"
    ws["A1"].font = Font(bold=True)
    ws["A2"] = "Fiscal year ends 30 June. FY2024 = Jul-23 to Jun-24; CY = calendar year (Jan-Dec)."
    ws["C4"] = "Fiscal year (June YE)"
    ws.merge_cells("C4:E4")
    ws["G4"] = "Calendar year"
    ws.merge_cells("G4:H4")
    for c, h in zip("ABCDEGH", ["Metric", "Unit", "FY2023", "FY2024", "FY2025E", "CY2023", "CY2024"]):
        ws[f"{c}5"] = h
        ws[f"{c}5"].font = Font(bold=True)
    NUM, PCT, X, USD = "#,##0.0", "0.0%", '0.0"x"', "$0.00"

    def row(r, label, unit, vals, fmt):
        ws.cell(r, 1, label)
        ws.cell(r, 2, unit)
        for col, v in zip((3, 4, 5, 7, 8), vals):
            if v is None:
                continue
            c = ws.cell(r, col, v)
            c.number_format = "@" if isinstance(v, str) and v[:1].isdigit() else fmt

    row(6, "Revenue", "$m", ["1,820.4", 2015.7, 2210.0, 1901.2, 2088.9], NUM)       # FY23 stored as text
    row(7, "Revenue growth", "%", ["n.m.", 0.1073, 0.0964, "n.m.", 0.0987], PCT)
    row(8, "EBITDA", "$m", [382.3, 443.5, 508.3, 401.6, 466.0], NUM)
    row(9, "EBITDA margin", "%", [0.21001, 0.22003, 0.23000, 0.21124, 0.22308], PCT)
    row(10, "Net income", "$m", [201.4, "238.9", 276.5, None, None], NUM)             # FY24 stored as text
    row(11, "Net debt", "$m", [655.9, 612.4, 540.0, None, None], NUM)
    row(12, "Net debt / EBITDA", "x", [1.7157, 1.3808, 1.0624, None, None], X)
    row(13, "ROIC", "%", ["n.m.", "14.2%", 0.153, None, None], PCT)
    row(14, "DPS", "$", ["-", 0.45, 0.52, None, None], USD)
    m.name("Rev_FY24", "'P&L'!$D$6")
    m.name("EBITDA_FY24", "'P&L'!$D$8")
    m.name("NetDebt_FY24", "'P&L'!$D$11")

    bk = m.sheet("Backup", hidden=True)
    bk["A1"] = "P&L - OLD (v3, pre-restatement)"
    for c, h in zip("ABCDE", ["Metric", "Unit", "FY2023", "FY2024", "FY2025E"]):
        bk[f"{c}5"] = h
    for r, (lab, unit, vals, fmt) in enumerate([
        ("Revenue", "$m", [1801.2, 1987.3, 2176.0], NUM),
        ("Revenue growth", "%", ["n.m.", 0.1033, 0.0950], PCT),
        ("EBITDA", "$m", [375.0, 431.8, 489.6], NUM),
        ("EBITDA margin", "%", [0.2082, 0.2173, 0.2250], PCT),
        ("Net income", "$m", [198.0, 231.2, 268.0], NUM),
        ("Net debt", "$m", [661.2, 628.9, 555.0], NUM),
    ], start=6):
        bk.cell(r, 1, lab)
        bk.cell(r, 2, unit)
        for j, v in enumerate(vals):
            bk.cell(r, 3 + j, v).number_format = fmt

    cf = m.sheet("Cash flow")      # transposed: periods down column A
    cf["A1"] = "Cash flow summary"
    cf["A1"].font = Font(bold=True)
    for c, h in zip("ABCD", ["Period", "Capex", "D&A", "Free cash flow"]):
        cf[f"{c}3"] = h
    cf["A4"], cf["B4"], cf["C4"], cf["D4"] = "Unit", "$m", "$m", "$m"
    for r, (p, a, b, c) in enumerate([
        ("FY2023", 85.6, 58.2, 142.3), ("FY2024", 98.2, 64.5, 171.8),
        ("FY2025E", 104.0, 71.0, 198.5), ("CY2024", 101.3, 66.1, 176.9),
    ], start=5):
        cf.cell(r, 1, p)
        for j, v in enumerate((a, b, c)):
            cf.cell(r, 2 + j, v).number_format = "#,##0.0"
    op = m.save(out / "operating_model.xlsx")

    # ------------------------------------------------------------------ DCF
    d = Model()
    ds = d.sheet("DCF")
    ds["A1"] = "DCF valuation - base case"
    ds["A1"].font = Font(bold=True)
    ds["A3"] = "Assumptions"
    ds["A3"].font = Font(bold=True)
    for r, (lab, u, v, f) in enumerate([
            ("WACC", "%", 0.095, "0.0%"), ("Terminal growth rate", "%", 0.025, "0.0%")], start=4):
        ds.cell(r, 1, lab)
        ds.cell(r, 2, u)
        ds.cell(r, 3, v).number_format = f
    ds["A7"] = "Projections"
    ds["A7"].font = Font(bold=True)
    for j, h in enumerate(["FY2025E", "FY2026E", "FY2027E", "FY2028E", "FY2029E"]):
        ds.cell(8, 3 + j, h)
    ds["A9"], ds["B9"] = "Unlevered free cash flow", "$m"
    for j, v in enumerate([198.5, 224.1, 249.6, 272.3, 291.0]):
        ds.cell(9, 3 + j, v).number_format = "#,##0.0"
    ds["A11"] = "Valuation"
    ds["A11"].font = Font(bold=True)
    for r, (lab, u, v, f) in enumerate([
        ("PV of free cash flows", "$m", 1009.2, "#,##0.0"),
        ("PV of terminal value", "$m", 4422.9, "#,##0.0"),
        ("Enterprise value", "$m", 5432.1, "#,##0.0"),
        ("Less: net debt", "$m", 612.4, "#,##0.0"),
        ("Equity value", "$m", 4819.7, "#,##0.0"),
        ("Diluted shares", "m", 152.4, "#,##0.0"),
        ("Implied share price", "$", 31.62, "$0.00"),
        ("Implied exit EV / EBITDA", "x", "#N/A", '0.0"x"'),
    ], start=12):
        ds.cell(r, 1, lab)
        ds.cell(r, 2, u)
        ds.cell(r, 3, v).number_format = f
    d.name("DCF_EV", "DCF!$C$14")
    d.name("DCF_WACC", "DCF!$C$4")
    d.name("DCF_Price", "DCF!$C$18")
    dcf = d.save(out / "dcf.xlsx")

    # ------------------------------------------------------------------ comps
    c = Model()
    cs = c.sheet("Comps")
    cs["A1"] = "Trading comparables"
    cs["A1"].font = Font(bold=True)
    cs["B4"] = "Trading multiples"
    cs.merge_cells("B4:C4")
    cs["D4"] = "Operating metrics"
    cs.merge_cells("D4:E4")
    for col, h in zip("ABCDE", ["Company", "EV / EBITDA CY2024", "EV / EBITDA CY2025E",
                                "EBITDA margin CY2024", "Revenue growth CY2024"]):
        cs[f"{col}5"] = h
    for col, u in zip("BCDE", ["x", "x", "%", "%"]):
        cs[f"{col}6"] = u
    comps = [
        ("Alpha Corp", 11.2, 10.2, 0.241, 0.063),
        ("Beta Holdings", 10.1, 9.1, 0.215, 0.048),
        ("Gamma Group", 12.6, 11.3, 0.263, 0.081),
        ("Delta plc", "n.m.", "-", "n.m.", "-"),
        ("Epsilon Inc", 10.6, 9.8, 0.198, 0.035),
    ]
    for r, (n, *vals) in enumerate(comps, start=7):
        cs.cell(r, 1, n)
        for j, v in enumerate(vals):
            cs.cell(r, 2 + j, v).number_format = '0.0"x"' if j < 2 else "0.0%"
    for r, (n, vals) in enumerate([("Median", [10.9, 10.0, 0.228]), ("Mean", [11.125, 10.1, 0.22925])], start=13):
        cs.cell(r, 1, n)
        for j, v in enumerate(vals):
            cs.cell(r, 2 + j, v).number_format = '0.0"x"' if j < 2 else "0.0%"
    comps_f = c.save(out / "comps.xlsx")

    # ------------------------------------------------------------------ deck
    k = Deck()
    k.new_slide("Executive summary", "Project Cobalt - fiscal year ends 30 June")
    k.bullets([
        "FY2024 (year to June 2024) revenue of $2.0bn, up 10.7% year-on-year",
        "FY2024 EBITDA of $443.5m at a 22.0% margin",
        "Net income of $238.9m in FY2024 and ROIC of 14.2%",
        "CY2024 revenue of $2,015.7m, up 9.9% on a calendar-year basis",      # ERROR: CY2024 is 2,088.9 (2,015.7 = FY2024)
        "Net debt / EBITDA of 1.4x at FY2024",
    ])
    k.new_slide("Financial summary", "($ in millions; fiscal year ending 30 June)")
    k.table([
        ["", "FY2023A", "FY2024A", "FY2025E"],
        ["Revenue", "1,820.4", "1,987.3", "2,210.0"],                          # ERROR: 1,987.3 pasted from hidden Backup (live 2,015.7)
        ["EBITDA", "382.3", "443.5", "508.3"],
        ["EBITDA margin", "21.0%", "22.0%", "23.0%"],
        ["Net income", "201.4", "238.9", "276.5"],
        ["Capex", "85.6", "98.2", "140.0"],                                    # ERROR: FY2025E capex 104.0
    ])
    k.footnote("Source: Company filings, management model. Fiscal year ends 30 June.")
    k.new_slide("Calendarised financials", "($ in millions; December year-end)")
    k.table([
        ["", "CY2023", "CY2024"],
        ["Revenue", "1,901.2", "2,088.9"],
        ["EBITDA", "401.6", "466.0"],
        ["EBITDA margin", "21.1%", "22.3%"],
    ])
    k.bullets(["Fiscal basis: FY2024 EBITDA of $466.0m, ahead of plan"])      # ERROR: FY2024 EBITDA is 443.5 (466.0 = CY2024)
    k.new_slide("Trading comparables", "EV / EBITDA and operating metrics")
    k.table([
        ["Company", "EV / EBITDA CY2024", "EV / EBITDA CY2025E", "EBITDA margin CY2024", "Revenue growth CY2024"],
        ["Alpha Corp", "11.2x", "10.2x", "24.1%", "6.3%"],
        ["Beta Holdings", "10.1x", "9.1x", "21.5%", "4.8%"],
        ["Gamma Group", "12.9x", "11.3x", "26.3%", "8.1%"],                  # ERROR: 12.6x
        ["Delta plc", "n.m.", "–", "n.m.", "–"],
        ["Epsilon Inc", "10.6x", "9.8x", "19.8%", "3.5%"],
        ["Median", "10.9x", "10.4x", "22.8%", ""],                             # ERROR: CY2025E median 10.0x
        ["Mean", "11.1x", "10.1x", "22.9%", ""],
    ], first_col=2.2, col_width=1.9)
    k.footnote("Source: FactSet as of 15-Nov-2024. n.m. = not meaningful.")
    k.new_slide("DCF valuation summary", "($ in millions, except per share data)")
    k.table([
        ["", "Base case"],
        ["WACC", "9.5%"],
        ["Terminal growth rate", "2.5%"],
        ["PV of free cash flows", "1,009.2"],
        ["PV of terminal value", "4,422.9"],
        ["Enterprise value", "5,432.1"],
        ["Less: net debt", "(621.4)"],                                         # ERROR: 612.4 transposed
        ["Diluted shares (m)", "152.4"],
        ["Implied share price", "$31.62"],
    ])
    k.bullets(["Implied exit EV / EBITDA multiple of 11.4x"])                  # ERROR: model cell is #N/A -> source_error
    k.new_slide("Cash flow and returns")
    k.bullets([
        "Net debt of $612.4m at 30-Jun-2024",
        "Capex of $98.2m in FY2024",
        "Free cash flow of $178.1m in FY2024",                                 # ERROR: 171.8
        "Dividend per share of $0.45 in FY2024",
    ])
    deck = k.save(out / "deck.pptx")

    return Scenario(
        name="messy_models", deck=deck, models=[op, dcf, comps_f],
        description="Three messy workbooks: text numbers, hidden Backup, FY(Jun) vs CY, #N/A, units column",
        expects=[
            Expect(1, ("mismatch", "period_ambiguous", "unit_period_mismatch", "stale"), "2,015.7",
                   "CY2024 revenue shows FY2024 value"),
            Expect(2, ("mismatch", "stale", "ratio_reconcile"), "1,987.3", "pasted from hidden Backup sheet"),
            Expect(2, ("mismatch",), "140.0", "capex FY2025E (transposed sheet)"),
            Expect(3, ("mismatch", "period_ambiguous", "unit_period_mismatch"), "466.0",
                   "FY2024 EBITDA shows CY value"),
            Expect(4, ("mismatch",), "12.9x", "Gamma EV/EBITDA typo"),
            Expect(4, ("mismatch",), "10.4x", "median CY2025E wrong"),
            Expect(5, ("mismatch", "sign_error", "sum_error"), "621.4", "net debt transposed digits"),
            Expect(5, ("source_error",), "11.4x", "model cell is #N/A"),
            Expect(6, ("mismatch",), "178.1", "FCF FY2024"),
        ],
        controls=[
            Control(1, "$2.0bn"), Control(1, "10.7%"), Control(1, "$443.5m"), Control(1, "22.0%"),
            Control(1, "$238.9m"), Control(1, "14.2%"), Control(1, "9.9%"), Control(1, "1.4x"),
            Control(2, "1,820.4"), Control(2, "443.5"), Control(2, "238.9"), Control(2, "98.2"),
            Control(2, "85.6"), Control(2, "2,210.0"), Control(2, "276.5"),
            Control(3, "1,901.2"), Control(3, "2,088.9"), Control(3, "401.6"), Control(3, "22.3%"),
            Control(4, "11.2x"), Control(4, "10.1x"), Control(4, "26.3%"), Control(4, "10.9x"),
            Control(4, "11.1x"), Control(4, "22.8%"), Control(4, "22.9%"), Control(4, "10.6x"),
            Control(4, "19.8%"),
            Control(5, "9.5%"), Control(5, "5,432.1"), Control(5, "4,422.9"), Control(5, "152.4"),
            Control(5, "$31.62"),
            Control(6, "$612.4m"), Control(6, "$98.2m"), Control(6, "$0.45"),
        ],
        min_verified_pct=70.0,
        max_extra_high=1,
    )
