"""Holdout 2: digital infrastructure / tower company acquisition financing deck (tenancy ratio, site lease
revenue, EBITDAaL, cash conversion, leverage, EV multiple, sources & uses, maturity profile, CPI escalators)."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PCT = "0.0%"
PCT_NEG = "0.0%;(0.0%)"
XM = '0.0"x"'
XM2 = '0.00"x"'
EUR = "#,##0.0"

YEARS = ["FY2023A", "FY2024A", "FY2025E"]
SITE = [198.2, 214.6, 228.9]
PASS = [35.1, 38.4, 40.2]
REV = [233.3, 253.0, 269.1]
GROUND = [58.3, 62.9, 66.2]
OPEX = [55.5, 59.8, 62.3]
MCAPEX = [12.1, 13.0, 14.6]
TOWERS = [4980, 5120, 5300]
TENANCIES = [7670, 8192, 8636]


def _model(path: Path, ebitdaal25: float) -> str:
    ebitdaal = [119.5, 130.3, ebitdaal25]
    m = Model()

    s = m.sheet("Summary")
    m.block(s, 1, "Project Pylon - Altus Tower Holdings financial summary", "(EUR in millions)", YEARS, [
        ("Site lease revenue", SITE),
        ("Energy pass-through and other revenue", PASS),
        ("Total revenue", REV),
        ("Ground lease costs", GROUND),
        ("Other operating costs", OPEX),
        ("EBITDAaL", ebitdaal),
        ("EBITDAaL margin", [e / r for e, r in zip(ebitdaal, REV)]),
        ("Maintenance capex", MCAPEX),
        ("Cash conversion", [(e - c) / e for e, c in zip(ebitdaal, MCAPEX)]),
        ("Site lease revenue growth", [None, SITE[1] / SITE[0] - 1, SITE[2] / SITE[1] - 1]),
        ("EBITDAaL growth", [None, ebitdaal[1] / ebitdaal[0] - 1, ebitdaal[2] / ebitdaal[1] - 1]),
    ], fmt={"Site lease revenue": EUR, "Energy pass-through and other revenue": EUR, "Total revenue": EUR,
            "Ground lease costs": EUR, "Other operating costs": EUR, "EBITDAaL": EUR, "EBITDAaL margin": PCT,
            "Maintenance capex": EUR, "Cash conversion": PCT, "Site lease revenue growth": PCT,
            "EBITDAaL growth": PCT})

    t = m.sheet("Towers")
    m.block(t, 1, "Portfolio KPIs (year end)", "(counts in units; revenue in EUR thousands)", YEARS, [
        ("Towers", TOWERS),
        ("Tenancies", TENANCIES),
        ("Tenancy ratio", [a / b for a, b in zip(TENANCIES, TOWERS)]),
        ("Site lease revenue per tenancy (EUR k)", [s_ / n * 1000 for s_, n in zip(SITE, TENANCIES)]),
    ], fmt={"Towers": "#,##0", "Tenancies": "#,##0", "Tenancy ratio": XM2,
            "Site lease revenue per tenancy (EUR k)": "#,##0.0"})

    tr = m.sheet("Transaction")
    m.block(tr, 1, "Sources and uses at close", "(EUR in millions)", ["EUR m", "% of total"], [
        ("Term Loan B", [600.0, 600.0 / 1618.9]),
        ("Senior secured notes", [380.0, 380.0 / 1618.9]),
        ("Revolving credit facility (drawn)", [30.0, 30.0 / 1618.9]),
        ("Sponsor equity", [608.9, 608.9 / 1618.9]),
        ("Total sources", [1618.9, 1.0]),
        ("Equity purchase price", [713.6, None]),
        ("Refinancing of existing debt", [850.0, None]),
        ("Transaction costs", [31.3, None]),
        ("Financing fees", [24.0, None]),
        ("Total uses", [1618.9, None]),
    ], fmt={})
    for r in range(6, 16):
        tr.cell(r, 3).number_format = EUR
        tr.cell(r, 4).number_format = PCT
    m.block(tr, 18, "Valuation", "(EUR in millions)", ["Value"], [
        ("Enterprise value", [1563.6]),
        ("EV / FY2024A EBITDAaL", [1563.6 / 130.3]),
        ("EV / FY2025E EBITDAaL", [1563.6 / 140.6]),
        ("EV per tower (EUR k)", [1563.6 / 5120 * 1000]),
    ], fmt={"Enterprise value": EUR, "EV / FY2024A EBITDAaL": XM, "EV / FY2025E EBITDAaL": XM,
            "EV per tower (EUR k)": "#,##0.0"})

    d = m.sheet("Leverage")
    m.block(d, 1, "Capital structure and leverage", "(EUR in millions)",
            ["Amount", "Cumulative / FY2024A EBITDAaL", "Cumulative / FY2025E EBITDAaL", "All-in cost", "Maturity"], [
        ("Term Loan B", [600.0, 600.0 / 130.3, 600.0 / 140.6, 0.0625, 2032]),
        ("Senior secured notes", [380.0, 980.0 / 130.3, 980.0 / 140.6, 0.055, 2030]),
        ("Revolving credit facility", [30.0, 1010.0 / 130.3, 1010.0 / 140.6, 0.05, 2029]),
        ("Total debt", [1010.0, 1010.0 / 130.3, 1010.0 / 140.6, 59.9 / 1010.0, None]),
    ])
    for r in range(6, 10):
        d.cell(r, 3).number_format = EUR
        d.cell(r, 4).number_format = XM
        d.cell(r, 5).number_format = XM
        d.cell(r, 6).number_format = "0.00%" if r < 9 else PCT
    m.block(d, 12, "Credit statistics (pro forma)", "", ["FY2024A", "FY2025E"], [
        ("Senior secured debt / EBITDAaL", [980.0 / 130.3, 980.0 / 140.6]),
        ("EBITDAaL / cash interest", [130.3 / 59.9, 140.6 / 59.9]),
        ("Fixed-rate share of debt", [0.75, 0.75]),
        ("Weighted average maturity (years)", [6.3564, 6.3564]),
    ], fmt={"Senior secured debt / EBITDAaL": XM, "EBITDAaL / cash interest": XM,
            "Fixed-rate share of debt": "0%", "Weighted average maturity (years)": "0.0"})

    mat = m.sheet("Debt Maturity")
    yrs = [2026, 2027, 2028, 2029, 2030, 2031, 2032, 2033]
    m.block(mat, 1, "Debt maturity profile", "(EUR in millions, by calendar year of maturity)", yrs, [
        ("Revolving credit facility", [0, 0, 0, 30.0, 0, 0, 0, 0]),
        ("Senior secured notes", [0, 0, 0, 0, 180.0, 200.0, 0, 0]),
        ("Term Loan B", [0, 0, 0, 0, 0, 0, 600.0, 0]),
        ("Total maturities", [0, 0, 0, 30.0, 180.0, 200.0, 600.0, 0]),
    ], fmt={k: EUR for k in ("Revolving credit facility", "Senior secured notes", "Term Loan B",
                             "Total maturities")})

    c = m.sheet("Contracts")
    m.block(c, 1, "Customer contracts (FY2024A)", "(EUR in millions; years remaining weighted by revenue)",
            ["Tenancies", "Site lease revenue", "% of revenue", "Contract years remaining", "Escalator cap / fixed"], [
        ("MNO A", [3150, 82.4, 82.4 / 214.6, 10.2, 0.03]),
        ("MNO B", [2760, 68.0, 68.0 / 214.6, 8.4, 0.03]),
        ("MNO C", [1740, 47.4, 47.4 / 214.6, 7.6, 0.025]),
        ("Other (MVNO, broadband, utilities)", [542, 16.8, 16.8 / 214.6, 4.1, 0.02]),
        ("Total / weighted average", [8192, 214.6, 1.0, 8.579, None]),
    ])
    for r in range(6, 11):
        c.cell(r, 3).number_format = "#,##0"
        c.cell(r, 4).number_format = EUR
        c.cell(r, 5).number_format = PCT
        c.cell(r, 6).number_format = "0.0"
        c.cell(r, 7).number_format = "0.0%"
    m.block(c, 14, "Escalator and CPI assumptions", "", ["Value"], [
        ("CPI-linked share of site lease revenue", [0.922]),
        ("CPI assumption FY2025E", [0.024]),
    ], fmt={"CPI-linked share of site lease revenue": PCT, "CPI assumption FY2025E": PCT})
    m.block(c, 23, "Site lease revenue bridge FY2024A to FY2025E", "(EUR in millions)", ["EUR m"], [
        ("FY2024A site lease revenue", [214.6]),
        ("CPI-linked escalators", [5.0]),
        ("New tenancies / colocation", [8.2]),
        ("Build-to-suit new towers", [2.6]),
        ("Churn and decommissioning", [-1.5]),
        ("FY2025E site lease revenue", [228.9]),
    ], fmt={k: '#,##0.0;(#,##0.0)' for k in ("FY2024A site lease revenue", "CPI-linked escalators",
                                            "New tenancies / colocation", "Build-to-suit new towers",
                                            "Churn and decommissioning", "FY2025E site lease revenue")})

    b = m.sheet("Draft Backup", hidden=True)
    m.block(b, 1, "Draft tower count (superseded)", "(units)", ["FY2024A"], [
        ("Towers incl. rooftops (draft)", [5210]), ("Tenancies (draft)", [8340]),
    ], fmt={"Towers incl. rooftops (draft)": "#,##0", "Tenancies (draft)": "#,##0"})
    return m.save(path)


def build(out: Path) -> Scenario:
    model = _model(out / "model.xlsx", 140.6)
    old = _model(out / "model_old.xlsx", 138.2)

    d = Deck()
    d.new_slide("Project Pylon: acquisition financing overview", "(EUR in millions unless stated)")
    d.bullets([
        "Acquisition of Altus Tower Holdings: 5,120 towers and 8,192 tenancies, a tenancy ratio of 1.60x",
        "FY2024A site lease revenue of EUR 228.9m and EBITDAaL of EUR 130.3m (51.5% margin)",   # ERROR period: 214.6
        "Enterprise value of EUR 1,563.6m, or 12.0x FY2024A EBITDAaL and 11.1x FY2025E",
        "Total debt of EUR 1,010.0m at close; senior secured leverage of 7.5x FY2024A EBITDAaL",
        "CPI-linked contracts represent 82.2% of site lease revenue, with 8.6 years of contract life remaining",  # ERROR 92.2%
    ], size=14)

    d.new_slide("Portfolio KPIs", "(year end)")
    d.table([
        ["", *YEARS],
        ["Towers", "4,980", "5,120", "5,300"],
        ["Tenancies", "7,670", "8,192", "8,636"],
        ["Tenancy ratio", "1.45x", "1.60x", "1.63x"],                                 # ERROR 1.54x
        ["Site lease revenue per tenancy (EUR k)", "25.8", "26.2", "26.5"],
    ], col_width=1.6, first_col=3.6)
    d.footnote("Tenancy ratio = tenancies / towers. Towers exclude rooftop sites.")

    d.new_slide("Financial summary", "(EUR in millions)")
    d.table([
        ["", *YEARS],
        ["Site lease revenue", "198.2", "214.6", "228.9"],
        ["Energy pass-through and other revenue", "35.1", "38.4", "40.2"],
        ["Total revenue", "233.3", "253.0", "269.1"],
        ["Ground lease costs", "(58.3)", "(62.9)", "(66.2)"],
        ["Other operating costs", "(55.5)", "(59.8)", "(62.3)"],
        ["EBITDAaL", "119.5", "130.3", "138.2"],                                      # ERROR stale (old 138.2; now 140.6)
        ["EBITDAaL margin", "51.2%", "51.5%", "52.2%"],
        ["Maintenance capex", "(12.1)", "(13.0)", "(14.6)"],
        ["Cash conversion", "89.9%", "90.0%", "89.6%"],
        ["Site lease revenue growth", "", "8.3%", "6.7%"],
        ["EBITDAaL growth", "", "9.0%", "7.9%"],
    ], col_width=1.5, first_col=3.8)
    d.footnote("EBITDAaL = EBITDA after leases. Cash conversion = (EBITDAaL - maintenance capex) / EBITDAaL.")

    d.new_slide("Site lease revenue bridge", "(EUR in millions; FY2024A to FY2025E)")
    d.table([
        ["", "EUR m"],
        ["FY2024A site lease revenue", "214.6"],
        ["CPI-linked escalators", "5.0"],
        ["New tenancies / colocation", "8.2"],
        ["Build-to-suit new towers", "2.6"],
        ["Churn and decommissioning", "1.5"],                                         # ERROR sign: (1.5)
        ["FY2025E site lease revenue", "228.9"],
    ], col_width=1.6, first_col=3.6)
    d.text("CPI assumption of 2.4% for FY2025E; escalators add 2.3% to site lease revenue.", size=12)

    d.new_slide("Customer contracts and escalators", "(EUR in millions; FY2024A)")
    d.table([
        ["Customer", "Tenancies", "Site lease revenue", "% of revenue", "Contract years remaining", "Escalator cap / fixed"],
        ["MNO A", "3,150", "82.4", "38.4%", "10.2", "3.0%"],
        ["MNO B", "2,760", "68.0", "31.7%", "8.4", "3.0%"],
        ["MNO C", "1,740", "47.4", "22.1%", "7.6", "2.5%"],
        ["Other (MVNO, broadband, utilities)", "542", "16.8", "7.8%", "4.1", "2.0%"],
        ["Total / weighted average", "8,192", "214.6", "100.0%", "8.6", ""],
    ], col_width=1.6, first_col=3.0)
    d.footnote("MNO escalators are CPI-linked subject to the stated annual cap; other customers on fixed escalators.")

    d.new_slide("Sources and uses", "(EUR in millions)")
    d.table([
        ["Sources", "EUR m", "Uses", "EUR m"],
        ["Term Loan B", "600.0", "Equity purchase price", "713.6"],
        ["Senior secured notes", "380.0", "Refinancing of existing debt", "850.0"],
        ["Revolving credit facility (drawn)", "30.0", "Transaction costs", "31.3"],
        ["Sponsor equity", "608.9", "Financing fees", "24.0"],
        ["Total sources", "1,618.9", "Total uses", "1,681.9"],                        # ERROR 1,618.9
    ], col_width=1.8, first_col=3.0)
    d.text("Debt funds 62.4% of total sources and sponsor equity 37.6%.", size=12)

    d.new_slide("Capital structure and leverage", "(EUR in millions)")
    d.table([
        ["Tranche", "Amount", "Cum. debt / FY24A EBITDAaL", "Cum. debt / FY25E EBITDAaL", "All-in cost", "Maturity"],
        ["Term Loan B", "600.0", "4.6x", "4.3x", "6.25%", "2032"],
        ["Senior secured notes", "380.0", "7.5x", "7.0x", "5.50%", "2030"],
        ["Revolving credit facility", "30.0", "7.4x", "7.2x", "5.00%", "2029"],         # ERROR 7.8x
        ["Total debt", "1,010.0", "7.8x", "7.2x", "5.9%", ""],
    ], col_width=1.6, first_col=2.6)
    d.text("EBITDAaL / cash interest of 2.3x in FY2025E; 75% of debt fixed-rate.", size=12)

    d.new_slide("Debt maturity profile", "(EUR in millions, by year of maturity)")
    d.chart("column", ["2026", "2027", "2028", "2029", "2030", "2031", "2032", "2033"],
            {"RCF": [0, 0, 0, 30.0, 0, 0, 0, 0],
             "Senior secured notes": [0, 0, 0, 0, 180.0, 220.0, 0, 0],                 # ERROR 200.0
             "Term Loan B": [0, 0, 0, 0, 0, 0, 600.0, 0]},
            number_format="#,##0.0", title="Maturities by year (EUR m)")
    d.text("Weighted average maturity of 6.4 years; no maturities before 2029.", size=12)
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="holdout2_infra", deck=deck, models=[model], model_old=old,
        expects=[
            Expect(1, ("period_ambiguous", "mismatch", "unit_period_mismatch"), "228.9", "FY2024A revenue is FY2025E value"),
            Expect(1, ("mismatch", "pct_reconcile", "unmatched"), "82.2%", "CPI-linked share 92.2%"),
            Expect(2, ("mismatch", "ratio_reconcile"), "1.45x", "FY23 tenancy ratio transposed"),
            Expect(3, ("stale",), "138.2", "FY25E EBITDAaL stale"),
            Expect(4, ("sign_error", "mismatch"), "1.5", "churn sign"),
            Expect(6, ("sum_error", "mismatch"), "1,681.9", "total uses"),
            Expect(7, ("mismatch", "ratio_reconcile"), "7.4x", "RCF cumulative leverage"),
            Expect(8, ("mismatch", "sum_error"), "220", "2031 maturities"),
        ],
        controls=[
            Control(1, "5,120"), Control(1, "8,192"), Control(1, "1.60x"), Control(1, "130.3"), Control(1, "51.5%"),
            Control(1, "1,563.6"), Control(1, "12.0x"), Control(1, "11.1x"), Control(1, "1,010.0"), Control(1, "7.5x"),
            Control(1, "8.6", allow=("unmatched",), note="no single model cell; reported as unverifiable (low)"),
            Control(2, "4,980"), Control(2, "7,670"), Control(2, "1.63x"), Control(2, "26.2"), Control(2, "25.8"),
            Control(3, "198.2"), Control(3, "253.0"), Control(3, "(62.9)"), Control(3, "119.5"), Control(3, "51.2%"),
            Control(3, "52.2%"), Control(3, "89.9%"), Control(3, "89.6%"), Control(3, "8.3%"), Control(3, "6.7%"),
            Control(3, "9.0%"), Control(3, "7.9%"),
            Control(4, "214.6"), Control(4, "5.0"), Control(4, "8.2"), Control(4, "228.9"), Control(4, "2.4%"),
            Control(4, "2.3%", allow=("unmatched",), note="no single model cell; reported as unverifiable (low)"),
            Control(5, "3,150"), Control(5, "82.4"), Control(5, "38.4%"), Control(5, "10.2"), Control(5, "3.0%"),
            Control(5, "2.5%"), Control(5, "100.0%"), Control(5, "8.6"), Control(5, "542"),
            Control(6, "600.0"), Control(6, "608.9"), Control(6, "713.6"), Control(6, "850.0"), Control(6, "1,618.9"),
            Control(6, "62.4%", allow=("unmatched",), note="no single model cell; reported as unverifiable (low)"), Control(6, "37.6%"),
            Control(7, "4.6x"), Control(7, "4.3x"), Control(7, "7.0x"), Control(7, "7.8x"), Control(7, "6.25%"),
            Control(7, "5.9%"), Control(7, "2.3x"), Control(7, "75%"), Control(7, "2032"),
            Control(8, "180.0"), Control(8, "600.0"), Control(8, "6.4"),
        ],
        min_verified_pct=70.0, max_extra_high=0,
    )
