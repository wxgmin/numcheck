"""Holdout 2: SaaS growth-equity / IPO deck (ARR, NRR/GRR, Rule of 40, CAC payback, cohorts; $m and $000; FY ends Jan)."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PCT = "0.0%"
PCT_NEG = "0.0%;(0.0%)"
NUM = "#,##0.0"
NEG = "#,##0.0;(#,##0.0)"
K = "#,##0"
KNEG = "#,##0;(#,##0)"
MULT = '0.0"x"'


def _model(path: Path, rule40_25: float) -> str:
    m = Model()
    s = m.sheet("Summary")
    m.block(s, 1, "Stratus Metrics, Inc. - annual summary (non-GAAP)",
            "($ in millions; fiscal year ends January 31, FY25 = Feb-24 to Jan-25)",
            ["FY23A", "FY24A", "FY25A"], [
        ("Annual recurring revenue (ARR)", [318.4, 412.7, 548.9]),
        ("Net new ARR", [79.8, 94.3, 136.2]),
        ("ARR growth", [0.3344, 0.2962, 0.3300]),
        ("Revenue", [291.5, 377.8, 497.2]),
        ("Revenue growth", [0.3310, 0.2961, 0.3160]),
        ("Gross profit", [214.5, 283.7, 381.8]),
        ("Gross margin", [0.7359, 0.7509, 0.7679]),
        ("Free cash flow", [-8.7, 18.9, 59.7]),
        ("FCF margin", [-0.0298, 0.0500, 0.1201]),
        ("Rule of 40", [0.3012, 0.3460, rule40_25]),
    ], fmt={"Annual recurring revenue (ARR)": NUM, "Net new ARR": NUM, "ARR growth": PCT, "Revenue": NUM,
            "Revenue growth": PCT, "Gross profit": NUM, "Gross margin": PCT, "Free cash flow": NEG,
            "FCF margin": PCT_NEG, "Rule of 40": PCT})

    c = m.sheet("Customers")
    m.block(c, 1, "Customer metrics", "(customer counts in actual numbers; ARR per customer in $ thousands)",
            ["FY23A", "FY24A", "FY25A"], [
        ("Customers >$100k ARR", [884, 1152, 1486]),
        ("Total customers", [14210, 16480, 19060]),
        ("% of ARR from customers >$100k", [0.62, 0.64, 0.67]),
        ("ARR per customer", [22.4, 25.0, 28.8]),
        ("Net revenue retention (NRR)", [1.12, 1.13, 1.17]),
        ("Gross revenue retention (GRR)", [0.91, 0.92, 0.93]),
    ], fmt={"Customers >$100k ARR": K, "Total customers": K, "% of ARR from customers >$100k": "0%",
            "ARR per customer": NUM, "Net revenue retention (NRR)": "0%", "Gross revenue retention (GRR)": "0%"})

    u = m.sheet("Unit Econ")
    m.block(u, 1, "Go-to-market efficiency and operating leverage (non-GAAP)", "($ in millions)",
            ["FY23A", "FY24A", "FY25A"], [
        ("Sales & marketing", [160.5, 183.1, 214.8]),
        ("S&M % of revenue", [0.5506, 0.4846, 0.4320]),
        ("R&D % of revenue", [0.2443, 0.2141, 0.1939]),
        ("G&A % of revenue", [0.1513, 0.1254, 0.1048]),
        ("Non-GAAP operating margin", [-0.2103, -0.0733, 0.0372]),
        ("CAC payback (months)", [32.8, 31.0, 24.6]),
        ("Magic number", [0.4972, 0.5151, 0.6340]),
    ], fmt={"Sales & marketing": NUM, "S&M % of revenue": PCT, "R&D % of revenue": PCT, "G&A % of revenue": PCT,
            "Non-GAAP operating margin": PCT_NEG, "CAC payback (months)": "0.0", "Magic number": '0.00"x"'})

    b = m.sheet("ARR Bridge")
    m.block(b, 1, "ARR bridge", "($ in millions)", ["FY24A", "FY25A"], [
        ("Beginning ARR", [318.4, 412.7]),
        ("New logo ARR", [52.9, 66.0]),
        ("Expansion ARR", [66.9, 99.1]),
        ("Churn and contraction", [-25.5, -28.9]),
        ("Ending ARR", [412.7, 548.9]),
        ("Net new ARR", [94.3, 136.2]),
    ], fmt={k: NEG for k in ["Beginning ARR", "New logo ARR", "Expansion ARR", "Churn and contraction",
                              "Ending ARR", "Net new ARR"]})

    q = m.sheet("Quarterly ($000)")
    m.block(q, 1, "Quarterly results - FY25 (non-GAAP)", "($ in thousands)",
            ["Q1 FY25", "Q2 FY25", "Q3 FY25", "Q4 FY25", "FY25"], [
        ("Revenue", [112380, 119450, 126290, 139080, 497200]),
        ("Subscription revenue", [106900, 113950, 120550, 133200, 474600]),
        ("Gross profit", [85200, 91500, 97500, 107600, 381800]),
        ("Gross margin", [0.7581, 0.7660, 0.7721, 0.7736, 0.7679]),
        ("Sales & marketing", [50900, 53200, 53900, 56800, 214800]),
        ("Free cash flow", [6200, 11800, 18300, 23400, 59700]),
        ("FCF margin", [0.0552, 0.0988, 0.1449, 0.1682, 0.1201]),
        ("Net new ARR", [28900, 31600, 33400, 42300, 136200]),
        ("Ending ARR", [441600, 473200, 506600, 548900, 548900]),
        ("Customers >$100k ARR", [1214, 1279, 1371, 1486, 1486]),
    ], fmt={"Revenue": K, "Subscription revenue": K, "Gross profit": K, "Gross margin": PCT,
            "Sales & marketing": K, "Free cash flow": K, "FCF margin": PCT, "Net new ARR": K,
            "Ending ARR": K, "Customers >$100k ARR": K},
            group_header=[("Fiscal 2025 (Feb-24 to Jan-25)", 5)])

    co = m.sheet("Cohorts ($000)")
    cohort_rows = [
        ("Q1 FY24 cohort (Feb-Apr 23)", [810, 11200, 12900, 1.1518]),
        ("Q2 FY24 cohort (May-Jul 23)", [870, 12500, 14140, 1.1312]),
        ("Q3 FY24 cohort (Aug-Oct 23)", [940, 13600, 15330, 1.1272]),
        ("Q4 FY24 cohort (Nov 23-Jan 24)", [1010, 15600, 17410, 1.1160]),
        ("FY24 cohorts total", [3630, 52900, 59780, 1.1301]),
        ("Q1 FY25 cohort (Feb-Apr 24)", [1010, 14800, None, None]),
        ("Q2 FY25 cohort (May-Jul 24)", [1060, 15600, None, None]),
        ("Q3 FY25 cohort (Aug-Oct 24)", [1100, 16200, None, None]),
        ("Q4 FY25 cohort (Nov 24-Jan 25)", [1230, 19400, None, None]),
        ("FY25 cohorts total", [4400, 66000, None, None]),
    ]
    m.block(co, 1, "New-customer cohorts by fiscal quarter of acquisition", "($ in thousands)",
            ["New logos", "Initial ARR", "ARR after 4 quarters", "Net $ retention"], cohort_rows)
    for r in range(6, 6 + len(cohort_rows)):
        co.cell(r, 3).number_format = "#,##0"   # New logos: a count, not $000
        co.cell(r, 4).number_format = K
        co.cell(r, 5).number_format = K
        co.cell(r, 6).number_format = PCT

    i = m.sheet("IPO")
    m.block(i, 1, "Illustrative IPO valuation", "($ in millions, except per share data)",
            ["Low", "Midpoint", "High"], [
        ("Offer price per share", [28.00, 30.00, 32.00]),
        ("Shares outstanding post-offering (m)", [168.4, 168.4, 168.4]),
        ("Equity value", [4715.2, 5052.0, 5388.8]),
        ("Less: net cash", [-612.0, -612.0, -612.0]),
        ("Enterprise value", [4103.2, 4440.0, 4776.8]),
        ("EV / FY25A ARR", [7.4752, 8.0889, 8.7026]),
        ("EV / FY25A revenue", [8.2527, 8.9300, 9.6073]),
        ("EV / FY26E revenue", [6.4113, 6.9375, 7.4638]),
    ], fmt={"Offer price per share": "$0.00", "Shares outstanding post-offering (m)": NUM, "Equity value": NUM,
            "Less: net cash": NEG, "Enterprise value": NUM, "EV / FY25A ARR": MULT,
            "EV / FY25A revenue": MULT, "EV / FY26E revenue": MULT})
    m.block(i, 14, "Offering structure (at midpoint)", "($ in millions, except shares in millions)",
            ["Shares (m)", "Gross proceeds"], [
        ("Primary shares", [14.0, 420.0]),
        ("Secondary shares", [10.0, 300.0]),
        ("Total offering", [24.0, 720.0]),
    ], fmt={"Primary shares": NUM, "Secondary shares": NUM, "Total offering": NUM})

    g = m.sheet("Guidance")
    m.block(g, 1, "FY26E guidance (fiscal year ending January 31, 2026)", "($ in millions)",
            ["Low", "Midpoint", "High"], [
        ("Revenue", [630.0, 640.0, 650.0]),
        ("Revenue growth", [0.2671, 0.2872, 0.3073]),
        ("Non-GAAP operating margin", [0.05, 0.055, 0.06]),
        ("FCF margin", [0.13, 0.13, 0.13]),
    ], fmt={"Revenue": NUM, "Revenue growth": PCT, "Non-GAAP operating margin": PCT, "FCF margin": "0%"})

    n = m.sheet("Non-GAAP ($000)")
    r = m.block(n, 1, "Gross profit reconciliation - FY25", "($ in thousands)", ["FY25A"], [
        ("GAAP gross profit", [368420]),
        ("Stock-based compensation", [11980]),
        ("Amortization of acquired intangibles", [1400]),
        ("Non-GAAP gross profit", [381800]),
    ], fmt={k: K for k in ["GAAP gross profit", "Stock-based compensation",
                            "Amortization of acquired intangibles", "Non-GAAP gross profit"]})
    m.block(n, r + 1, "Operating income reconciliation - FY25", "($ in thousands)", ["FY25A"], [
        ("Non-GAAP operating income", [18500]),
        ("Stock-based compensation", [-71200]),
        ("Amortization of acquired intangibles", [-3100]),
        ("Other (restructuring, payroll tax on equity)", [-4900]),
        ("GAAP operating loss", [-60700]),
        ("GAAP operating margin", [-0.1221]),
    ], fmt={"Non-GAAP operating income": KNEG, "Stock-based compensation": KNEG,
            "Amortization of acquired intangibles": KNEG,
            "Other (restructuring, payroll tax on equity)": KNEG, "GAAP operating loss": KNEG,
            "GAAP operating margin": PCT_NEG})

    h = m.sheet("Monthly ARR ($000)", hidden=True)
    m.block(h, 1, "Month-end ARR (backup)", "($ in thousands)",
            ["Feb-24", "Mar-24", "Apr-24", "May-24", "Jun-24", "Jul-24", "Aug-24", "Sep-24", "Oct-24",
             "Nov-24", "Dec-24", "Jan-25"],
            [("Ending ARR", [421300, 431200, 441600, 452100, 462300, 473200, 484000, 495100, 506600,
                             520700, 534400, 548900])], fmt={"Ending ARR": K})
    return m.save(path)


def build(out: Path) -> Scenario:
    model = _model(out / "model.xlsx", 0.4361)
    old = _model(out / "model_old.xlsx", 0.4210)

    d = Deck()
    d.new_slide("Stratus Metrics: investment highlights", "($ in millions)")
    d.bullets([
        "ARR of $548.9m at January 31, 2025, up 33.0% year on year",
        "Net new ARR of $94.3m in FY25, with 1,486 customers above $100k ARR",           # ERROR period: FY24 value
        "FY25 revenue of $497.2m, up 33.6%, with non-GAAP gross margin of 76.8%",         # ERROR growth (31.6%)
        "Rule of 40 score of 43.6% on a 12.0% free cash flow margin",
        "Net revenue retention of 117% and gross revenue retention of 93%",
        "Offering of 24.0m shares at an indicative range of $28.00 to $32.00 per share",
    ], size=14)

    d.new_slide("Financial summary", "($ in millions; fiscal year ends January 31)")
    d.table([
        ["", "FY23A", "FY24A", "FY25A"],
        ["ARR", "318.4", "412.7", "548.9"],
        ["Net new ARR", "79.8", "94.3", "136.2"],
        ["ARR growth", "33.4%", "29.6%", "33.0%"],
        ["Revenue", "291.5", "377.8", "497.2"],
        ["Revenue growth", "33.1%", "29.6%", "31.6%"],
        ["Gross margin", "73.6%", "75.1%", "76.8%"],
        ["FCF margin", "3.0%", "5.0%", "12.0%"],                                           # ERROR sign: (3.0%)
        ["Rule of 40", "30.1%", "34.6%", "42.1%"],                                         # ERROR stale (old 42.1%)
    ], col_width=1.5, first_col=3.0)
    d.footnote("Fiscal year ends January 31; FY25 = February 1, 2024 to January 31, 2025. "
               "Rule of 40 = revenue growth + FCF margin. Non-GAAP.")

    d.new_slide("Customer metrics", "(customer counts in actual numbers; ARR per customer in $ thousands)")
    d.table([
        ["", "FY23A", "FY24A", "FY25A"],
        ["Customers >$100k ARR", "884", "1,152", "1,486"],
        ["Total customers", "14,210", "16,480", "19,060"],
        ["% of ARR from customers >$100k", "62%", "64%", "67%"],
        ["ARR per customer ($000)", "22.4", "25.0", "28.8"],
        ["Net revenue retention", "112%", "113%", "117%"],
        ["Gross revenue retention", "91%", "92%", "93%"],
    ], col_width=1.5, first_col=3.4)
    d.text("1,468 customers above $100k ARR at year end, up 29% year on year", size=12)   # ERROR transposition

    d.new_slide("Go-to-market efficiency and operating leverage", "($ in millions)")
    d.table([
        ["", "FY23A", "FY24A", "FY25A"],
        ["Sales & marketing", "160.5", "183.1", "214.8"],
        ["S&M % of revenue", "55.1%", "48.5%", "43.2%"],
        ["R&D % of revenue", "24.4%", "21.4%", "19.4%"],
        ["G&A % of revenue", "15.1%", "12.5%", "10.5%"],
        ["Non-GAAP operating margin", "(21.0%)", "(7.3%)", "3.7%"],
        ["CAC payback (months)", "32.8", "31.0", "24.6"],
        ["Magic number", "0.50x", "0.52x", "0.63x"],
    ], col_width=1.5, first_col=3.0)
    d.text("CAC payback improved to 24.6 months in FY25 from 31.0 months in FY24; "
           "operating margin turned positive at 3.7%", size=12)

    d.new_slide("Quarterly performance - FY25", "($ in thousands)")
    d.table([
        ["", "Q1 FY25 (Apr-24)", "Q2 FY25 (Jul-24)", "Q3 FY25 (Oct-24)", "Q4 FY25 (Jan-25)", "FY25"],
        ["Revenue", "112,380", "119,450", "126,290", "139,080", "497,200"],
        ["Subscription revenue", "106,900", "113,950", "120,550", "133,200", "474,600"],
        ["Gross profit", "85,200", "91,500", "97,500", "107,600", "381,800"],
        ["Gross margin", "75.8%", "76.6%", "77.2%", "77.4%", "76.8%"],
        ["Sales & marketing", "50,900", "53,200", "53,900", "56,800", "214,800"],
        ["Free cash flow", "6,200", "11,800", "18.3", "23,400", "59,700"],                  # ERROR scale: 18,300
        ["FCF margin", "5.5%", "9.9%", "14.5%", "16.8%", "12.0%"],
        ["Net new ARR", "28,900", "31,600", "33,400", "42,300", "136,200"],
        ["Ending ARR", "441,600", "473,200", "506,600", "548,900", "548,900"],
        ["Customers >$100k ARR", "1,214", "1,279", "1,371", "1,486", "1,486"],
    ], col_width=1.55, first_col=2.6)
    d.footnote("Fiscal quarters: Q1 = February-April, Q2 = May-July, Q3 = August-October, Q4 = November-January.")

    d.new_slide("ARR bridge", "($ in millions)")
    d.table([
        ["", "FY24A", "FY25A"],
        ["Beginning ARR", "318.4", "412.7"],
        ["(+) New logo ARR", "52.9", "66.0"],
        ["(+) Expansion ARR", "66.9", "99.1"],
        ["(-) Churn and contraction", "(25.5)", "(28.9)"],
        ["Ending ARR", "412.7", "548.9"],
        ["Net new ARR", "94.3", "136.2"],
        ["Net revenue retention", "113%", "117%"],
        ["Gross revenue retention", "92%", "93%"],
    ], col_width=1.5, first_col=3.0)
    d.chart("column", ["Q1 FY25", "Q2 FY25", "Q3 FY25", "Q4 FY25"],
            {"Ending ARR ($m)": [441.6, 473.2, 506.6, 548.9]}, number_format="#,##0.0",
            title="Ending ARR by quarter ($m)", height=2.4)
    d.text("Record Q4 net new ARR of $42.3m, versus $28.9m in Q1", size=12)

    d.new_slide("Cohort analysis", "($ in thousands)")
    d.table([
        ["Cohort", "New logos (#)", "Initial ARR", "ARR after 4 quarters", "Net $ retention"],
        ["Q1 FY24 (Feb-Apr 23)", "810", "11,200", "12,900", "115.2%"],
        ["Q2 FY24 (May-Jul 23)", "870", "12,500", "14,140", "113.1%"],
        ["Q3 FY24 (Aug-Oct 23)", "940", "13,600", "15,330", "112.7%"],
        ["Q4 FY24 (Nov 23-Jan 24)", "1,010", "15,600", "17,410", "111.6%"],
        ["FY24 cohorts", "3,630", "53,900", "59,780", "113.0%"],                           # ERROR sum: 52,900
        ["Q1 FY25 (Feb-Apr 24)", "1,010", "14,800", "n.a.", "n.a."],
        ["Q2 FY25 (May-Jul 24)", "1,060", "15,600", "n.a.", "n.a."],
        ["Q3 FY25 (Aug-Oct 24)", "1,100", "16,200", "n.a.", "n.a."],
        ["Q4 FY25 (Nov 24-Jan 25)", "1,230", "19,400", "n.a.", "n.a."],
        ["FY25 cohorts", "4,400", "66,000", "n.a.", "n.a."],
    ], col_width=1.7, first_col=2.8)
    d.footnote("Net $ retention = ARR after four quarters / initial ARR. FY25 cohorts have not yet completed four quarters.")

    d.new_slide("Illustrative IPO valuation", "($ in millions, except per share data)")
    d.table([
        ["", "Low", "Midpoint", "High"],
        ["Offer price per share", "$28.00", "$30.00", "$32.00"],
        ["Shares outstanding post-offering (m)", "168.4", "168.4", "168.4"],
        ["Equity value", "4,715.2", "5,052.0", "5,388.8"],
        ["Less: net cash", "(612.0)", "(612.0)", "(612.0)"],
        ["Enterprise value", "4,103.2", "4,440.0", "4,776.8"],
        ["EV / FY25A ARR", "7.5x", "8.1x", "8.7x"],
        ["EV / FY25A revenue", "8.3x", "8.9x", "9.6x"],
        ["EV / FY26E revenue", "6.4x", "6.9x", "7.5x"],
    ], col_width=1.5, first_col=3.6)
    d.text("24.0m shares offered: 14.0m primary ($420.0m) and 10.0m secondary ($300.0m); "
           "gross proceeds of $720.0m at the $30.00 midpoint", size=12)
    d.footnote("FY26E revenue based on the midpoint of guidance ($640.0m).")

    d.new_slide("Guidance and targets")
    d.bullets([
        "FY26 revenue guidance of $630-650m, a midpoint of $640.0m (28.7% growth)",
        "FY26 non-GAAP operating margin guidance of 5-6% and free cash flow margin of 13%",
        "Long-term target model: gross margin of 78-80% and free cash flow margin of 25%",
        "Expected pricing in the week of April 7, 2025; to be listed on the NYSE under 'STRM'",
        "Fiscal Q1 FY26 ends April 30, 2025",
    ], size=14)

    d.new_slide("Appendix: non-GAAP reconciliation - FY25", "($ in thousands)")
    d.table([
        ["Gross profit", "FY25A"],
        ["GAAP gross profit", "368,420"],
        ["Stock-based compensation", "11,980"],
        ["Amortization of acquired intangibles", "1,400"],
        ["Non-GAAP gross profit", "381,800"],
    ], col_width=1.6, first_col=4.2)
    d.table([
        ["Operating income", "FY25A"],
        ["Non-GAAP operating income", "18,500"],
        ["Stock-based compensation", "(71,200)"],
        ["Amortization of acquired intangibles", "(3,100)"],
        ["Other (restructuring, payroll tax on equity)", "(4,900)"],
        ["GAAP operating loss", "(60,700)"],
        ["GAAP operating margin", "(12.2%)"],
    ], col_width=1.6, first_col=4.2)
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="holdout2_saas", deck=deck, models=[model], model_old=old,
        expects=[
            Expect(1, ("growth_reconcile", "mismatch", "pct_reconcile"), "33.6%", "revenue growth is 31.6%"),
            Expect(1, ("period_ambiguous", "mismatch", "unit_period_mismatch"), "94.3", "FY24 net new ARR labelled FY25"),
            Expect(2, ("sign_error", "mismatch"), "3.0%", "FY23 FCF margin should be (3.0%)"),
            Expect(2, ("stale",), "42.1%", "Rule of 40 FY25 stale (now 43.6%)"),
            Expect(3, ("mismatch", "inconsistent_across_slides", "unmatched"), "1,468", "customers >$100k is 1,486"),
            Expect(5, ("scale_error", "mismatch", "unit_period_mismatch"), "18.3", "Q3 FCF in $m on a $000 slide"),
            Expect(7, ("sum_error", "mismatch"), "53,900", "FY24 cohort initial ARR total is 52,900"),
        ],
        controls=[
            Control(1, "$548.9m"), Control(1, "33.0%"), Control(1, "1,486"), Control(1, "$497.2m"),
            Control(1, "76.8%"), Control(1, "43.6%"), Control(1, "12.0%"), Control(1, "117%"), Control(1, "93%"),
            Control(1, "24.0m"), Control(1, "$28.00", allow=("unmatched",)), Control(1, "$32.00", allow=("unmatched",)),
            Control(2, "318.4"), Control(2, "412.7"), Control(2, "548.9"), Control(2, "79.8"),
            Control(2, "136.2"), Control(2, "33.4%"), Control(2, "29.6%"), Control(2, "291.5"),
            Control(2, "377.8"), Control(2, "497.2"), Control(2, "33.1%"), Control(2, "31.6%"),
            Control(2, "73.6%"), Control(2, "75.1%"), Control(2, "5.0%"), Control(2, "12.0%"),
            Control(2, "30.1%"), Control(2, "34.6%"),
            Control(3, "884"), Control(3, "1,152"), Control(3, "14,210"), Control(3, "19,060"),
            Control(3, "62%"), Control(3, "67%"), Control(3, "22.4"), Control(3, "25.0"), Control(3, "28.8"),
            Control(3, "112%"), Control(3, "113%"), Control(3, "91%"), Control(3, "29%", allow=("unmatched",), note="no single model cell; reported as unverifiable (low)"),
            Control(4, "160.5"), Control(4, "214.8"), Control(4, "55.1%"), Control(4, "43.2%"),
            Control(4, "24.4%"), Control(4, "19.4%"), Control(4, "15.1%"), Control(4, "12.5%"),
            Control(4, "(21.0%)"), Control(4, "(7.3%)"), Control(4, "3.7%"), Control(4, "32.8"),
            Control(4, "31.0"), Control(4, "24.6"), Control(4, "0.50x"), Control(4, "0.63x"),
            Control(5, "112,380"), Control(5, "119,450"), Control(5, "139,080"), Control(5, "85,200"),
            Control(5, "107,600"), Control(5, "50,900"), Control(5, "6,200"), Control(5, "11,800"),
            Control(5, "23,400"), Control(5, "28,900"), Control(5, "42,300"), Control(5, "441,600"),
            Control(5, "548,900"), Control(5, "1,214"), Control(5, "1,371"), Control(5, "75.8%"),
            Control(5, "77.4%"), Control(5, "5.5%"), Control(5, "16.8%"),
            Control(6, "52.9"), Control(6, "66.0"), Control(6, "99.1"), Control(6, "(25.5)"),
            Control(6, "(28.9)"), Control(6, "$42.3m"), Control(6, "$28.9m"), Control(6, "113%"),
            Control(6, "92%"),
            Control(7, "810"), Control(7, "1,010"), Control(7, "11,200"), Control(7, "15,600"),
            Control(7, "17,410"), Control(7, "115.2%"), Control(7, "111.6%"), Control(7, "3,630"),
            Control(7, "59,780"), Control(7, "113.0%"), Control(7, "66,000"), Control(7, "4,400"),
            Control(8, "$30.00"), Control(8, "168.4"), Control(8, "5,052.0"), Control(8, "(612.0)"),
            Control(8, "4,440.0"), Control(8, "8.1x"), Control(8, "6.9x"), Control(8, "$420.0m"),
            Control(8, "$720.0m"), Control(8, "14.0m"), Control(8, "$640.0m"),
            Control(9, "$640.0m"), Control(9, "28.7%"), Control(9, "13%"),
            Control(9, "78-80%", allow=("unmatched",)), Control(9, "25%", allow=("unmatched",)),
            Control(9, "650m", allow=("unmatched",)),
            Control(10, "368,420"), Control(10, "11,980"), Control(10, "1,400"), Control(10, "381,800"),
            Control(10, "18,500"), Control(10, "(71,200)"), Control(10, "(3,100)"), Control(10, "(4,900)"),
            Control(10, "(60,700)"), Control(10, "(12.2%)"),
        ],
        min_verified_pct=70.0, max_extra_high=0,
    )
