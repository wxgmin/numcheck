"""Holdout 2: P&C insurer investor deck (GWP, combined ratio bps, PYD, solvency/RBC, ROE, BVPS; GBP + some USD)."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PCT = "0.0%"
NUM = "#,##0.0"
NEG = "#,##0.0;(#,##0.0)"
BPS = '+#,##0" bps";(#,##0)" bps"'
PENCE = '0.0"p"'
LINES = ["Property", "Motor", "Liability", "Specialty", "Group"]


def _model(path: Path, solv24: float) -> str:
    m = Model()
    g = m.sheet("Group KPIs")
    m.block(g, 1, "Harbourgate Insurance Group plc - Group key performance indicators",
            "(GBP in millions, except per share data in pence)",
            ["FY2022A", "FY2023A", "FY2024A"], [
        ("Gross written premium", [3412.0, 3688.5, 4021.3]),
        ("GWP growth", [None, 0.0810, 0.0902]),
        ("Net earned premium", [2905.4, 3172.6, 3455.9]),
        ("NEP growth", [None, 0.0920, 0.0893]),
        ("Loss ratio", [0.648, 0.629, 0.617]),
        ("Expense ratio", [0.316, 0.319, 0.324]),
        ("Combined ratio", [0.964, 0.948, 0.941]),
        ("Combined ratio change YoY (bps)", [None, -160, -70]),
        ("Underwriting result", [104.6, 165.0, 203.9]),
        ("Favourable prior-year reserve development", [-48.2, -61.7, -72.4]),
        ("PYD benefit to loss ratio", [0.0166, 0.0194, 0.0209]),
        ("Net investment income", [150.2, 202.3, 231.5]),
        ("Investment portfolio", [5240.0, 5612.0, 5935.0]),
        ("Investment yield", [0.0287, 0.0360, 0.0390]),
        ("Profit before tax", [213.5, 322.2, 386.8]),
        ("Net income", [173.0, 241.7, 290.1]),
        ("Shareholders' equity", [2148.0, 2262.0, 2386.0]),
        ("Return on equity", [0.0910, 0.1096, 0.1248]),
        ("Diluted shares (m)", [622.5, 618.0, 612.4]),
        ("Earnings per share", [27.79, 39.11, 47.37]),
        ("Dividend per share", [17.0, 19.5, 22.0]),
        ("Payout ratio", [0.6117, 0.4986, 0.4644]),
        ("Book value per share", [345.06, 366.02, 389.62]),
        ("Book value per share growth", [None, 0.0607, 0.0644]),
        ("Solvency II ratio", [1.92, 1.98, solv24]),
    ], fmt={"Gross written premium": NUM, "GWP growth": PCT, "Net earned premium": NUM, "NEP growth": PCT,
            "Loss ratio": PCT, "Expense ratio": PCT, "Combined ratio": PCT,
            "Combined ratio change YoY (bps)": BPS, "Underwriting result": NUM,
            "Favourable prior-year reserve development": NEG, "PYD benefit to loss ratio": PCT,
            "Net investment income": NUM, "Investment portfolio": NUM, "Investment yield": PCT,
            "Profit before tax": NUM, "Net income": NUM, "Shareholders' equity": NUM,
            "Return on equity": PCT, "Diluted shares (m)": NUM, "Earnings per share": PENCE,
            "Dividend per share": PENCE, "Payout ratio": PCT, "Book value per share": PENCE,
            "Book value per share growth": PCT, "Solvency II ratio": "0%"})

    for yr, rows in (("2024", [
        ("Gross written premium", [1120.4, 1046.8, 872.5, 981.6, 4021.3]),
        ("GWP mix", [0.279, 0.260, 0.217, 0.244, 1.0]),
        ("Net earned premium", [962.1, 918.7, 744.0, 831.1, 3455.9]),
        ("Loss ratio", [0.590, 0.701, 0.615, 0.557, 0.617]),
        ("Expense ratio", [0.334, 0.298, 0.351, 0.317, 0.324]),
        ("Combined ratio", [0.924, 0.999, 0.966, 0.874, 0.941]),
        ("Combined ratio change YoY (bps)", [-60, -50, 80, -170, -70]),
        ("Favourable prior-year reserve development", [-18.9, 4.3, -29.6, -28.2, -72.4]),
    ]), ("2023", [
        ("Gross written premium", [1034.7, 998.2, 801.4, 854.2, 3688.5]),
        ("GWP mix", [0.281, 0.271, 0.217, 0.232, 1.0]),
        ("Net earned premium", [891.5, 880.4, 688.3, 712.4, 3172.6]),
        ("Loss ratio", [0.596, 0.715, 0.620, 0.573, 0.629]),
        ("Expense ratio", [0.334, 0.289, 0.338, 0.318, 0.319]),
        ("Combined ratio", [0.930, 1.004, 0.958, 0.891, 0.948]),
        ("Favourable prior-year reserve development", [-17.2, 2.1, -25.8, -20.8, -61.7]),
    ])):
        ws = m.sheet(f"LoB {yr}")
        m.block(ws, 1, f"Underwriting results by line of business - FY{yr}A",
                "(GBP in millions)", LINES, rows,
                fmt={"Gross written premium": NUM, "GWP mix": PCT, "Net earned premium": NUM,
                     "Loss ratio": PCT, "Expense ratio": PCT, "Combined ratio": PCT,
                     "Combined ratio change YoY (bps)": '+#,##0;(#,##0)',
                     "Favourable prior-year reserve development": NEG})

    us = m.sheet("US Specialty")
    m.block(us, 1, "Harbourgate America - US specialty segment", "(USD in millions unless stated)",
            ["FY2023A", "FY2024A"], [
        ("Gross written premium (USD)", [748.0, 812.4]),
        ("GWP growth", [None, 0.0861]),
        ("Net earned premium (USD)", [701.5, 764.3]),
        ("Combined ratio", [0.984, 0.972]),
        ("RBC ratio (company action level)", [4.01, 4.28]),
        ("Average GBP/USD", [1.2436, 1.2700]),
        ("Gross written premium (GBP equivalent, GBP m)", [601.5, 639.7]),
        ("Share of Group GWP", [0.1631, 0.1591]),
    ], fmt={"Gross written premium (USD)": NUM, "GWP growth": PCT, "Net earned premium (USD)": NUM,
            "Combined ratio": PCT, "RBC ratio (company action level)": "0%", "Average GBP/USD": "0.0000",
            "Gross written premium (GBP equivalent, GBP m)": NUM, "Share of Group GWP": PCT})

    fx = m.sheet("FX_Monthly", hidden=True)
    rates = [1.2712, 1.2630, 1.2655, 1.2562, 1.2650, 1.2740, 1.2866, 1.2951, 1.3220, 1.2992, 1.2841]
    rates.append(round(1.27 * 12 - sum(rates), 4))
    m.block(fx, 1, "GBP/USD monthly average 2024 (backup)", "(USD per GBP)",
            ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            [("GBP/USD", rates)], fmt={"GBP/USD": "0.0000"})
    return m.save(path)


def build(out: Path) -> Scenario:
    model = _model(out / "model.xlsx", 2.03)
    old = _model(out / "model_old.xlsx", 1.99)

    d = Deck()
    d.new_slide("Harbourgate Insurance Group: FY2024 investment highlights",
                "(GBP in millions, except per share data)")
    d.bullets([
        "Gross written premium of £4,021.3m, up 9.0% year on year; net earned premium of £3,455.9m",
        "Combined ratio of 94.1%, an improvement of 70bps, with a loss ratio of 61.7% and expense ratio of 32.4%",
        "Underwriting profit of £2.04bn including £72.4m of favourable prior-year reserve development",  # ERROR scale: 203.9m
        "Return on equity of 12.5% and investment yield of 3.9%; dividend of 22.0p per share",
        "Solvency II ratio of 203% and US subsidiary RBC ratio of 428%",
    ], size=14)

    d.new_slide("Group financial summary", "(GBP in millions, except per share data; favourable PYD shown as negative)")
    d.table([
        ["", "FY2022A", "FY2023A", "FY2024A"],
        ["Gross written premium", "3,412.0", "3,688.5", "4,021.3"],
        ["Net earned premium", "2,905.4", "3,172.6", "3,455.9"],
        ["Loss ratio", "64.8%", "62.9%", "61.7%"],
        ["Expense ratio", "31.6%", "31.9%", "32.4%"],
        ["Combined ratio", "96.4%", "94.8%", "94.1%"],
        ["Favourable prior-year reserve development", "(48.2)", "(61.7)", "72.4"],     # ERROR sign
        ["Investment yield", "2.9%", "3.6%", "3.9%"],
        ["Return on equity", "9.1%", "11.0%", "12.5%"],
        ["Book value per share", "345.1p", "366.0p", "389.6p"],
        ["Solvency II ratio", "192%", "198%", "199%"],                                 # ERROR stale (203%)
    ], col_width=1.5, first_col=4.0)
    d.footnote("Source: Company accounts. Combined ratio = loss ratio + expense ratio. "
               "Solvency II ratio shown on the Group's partial internal model basis.")

    d.new_slide("Underwriting performance by line of business - FY2024", "(GBP in millions)")
    d.table([
        ["", "Property", "Motor", "Liability", "Specialty", "Group"],
        ["Gross written premium", "1,120.4", "1,046.8", "872.5", "981.6", "4,021.3"],
        ["Net earned premium", "962.1", "918.7", "744.0", "831.1", "3,445.9"],        # ERROR sum/total (3,455.9)
        ["Loss ratio", "59.0%", "70.1%", "61.5%", "55.7%", "61.7%"],
        ["Expense ratio", "33.4%", "29.8%", "35.1%", "31.7%", "32.4%"],
        ["Combined ratio", "92.4%", "99.9%", "96.6%", "87.4%", "94.1%"],
        ["GWP mix", "27.9%", "26.0%", "21.7%", "24.4%", "100.0%"],
    ], col_width=1.4, first_col=2.8)
    d.footnote("Source: Company accounts. Specialty includes the US specialty segment (see page 7).")

    d.new_slide("Combined ratio movement and reserve development by line", "(GBP in millions)")
    d.table([
        ["", "Property", "Motor", "Liability", "Specialty", "Group"],
        ["Combined ratio FY2023A", "93.0%", "100.4%", "95.8%", "89.1%", "94.8%"],
        ["Combined ratio FY2024A", "92.4%", "99.9%", "96.6%", "87.4%", "94.1%"],
        ["Change YoY (bps)", "(60)", "(50)", "(80)", "(170)", "(70)"],              # ERROR sign: Liability +80
        ["Favourable prior-year reserve development", "(18.9)", "4.3", "(29.6)", "(28.2)", "(72.4)"],
    ], col_width=1.3, first_col=3.6)
    d.chart("column", ["Property", "Motor", "Liability", "Specialty"], {
        "FY2023A": [0.930, 1.004, 0.958, 0.981],                                    # ERROR transposition: Specialty 89.1%
        "FY2024A": [0.924, 0.999, 0.966, 0.874],
    }, number_format="0.0%", title="Combined ratio by line", height=2.8)
    d.text("Adverse development of £4.3m in Motor was more than offset by £76.7m of favourable development "
           "across the other lines", size=12)

    d.new_slide("Investment portfolio and capital", "(GBP in millions)")
    d.table([
        ["", "FY2022A", "FY2023A", "FY2024A"],
        ["Investment portfolio", "5,240.0", "5,612.0", "5,935.0"],
        ["Net investment income", "150.2", "202.3", "231.5"],
        ["Investment yield", "2.9%", "3.6%", "3.9%"],
        ["Shareholders' equity", "2,148.0", "2,262.0", "2,386.0"],
        ["Return on equity", "9.1%", "11.0%", "12.5%"],
        ["Solvency II ratio", "192%", "198%", "203%"],
    ], col_width=1.5, first_col=3.2)
    d.text(["Net investment income of £231.5m on a £5.9bn portfolio, a yield of 3.9% (FY2023A: 3.6%)",
            "Shareholders' equity of £2.39bn after the proposed final dividend"], size=12)

    d.new_slide("Shareholder returns", "(GBP in millions, except per share data in pence)")
    d.table([
        ["", "FY2022A", "FY2023A", "FY2024A"],
        ["Profit before tax", "213.5", "322.2", "386.8"],
        ["Net income", "173.0", "241.7", "290.1"],
        ["Diluted shares (m)", "622.5", "618.0", "612.4"],
        ["Earnings per share", "27.8p", "39.1p", "47.4p"],
        ["Dividend per share", "17.0p", "19.5p", "22.0p"],
        ["Payout ratio", "61.2%", "49.9%", "46.4%"],
        ["Book value per share", "345.1p", "366.0p", "389.6p"],
    ], col_width=1.5, first_col=3.0)
    d.text(["Book value per share of 398.6p at 31 December 2024, up 6.4% (FY2023A: 366.0p)",   # ERROR transposition
            "Net income of £290.1m; EPS of 47.4p and a dividend of 22.0p, a payout of 46.4%"], size=12)

    d.new_slide("US specialty segment", "(USD in millions unless stated)")
    d.table([
        ["", "FY2023A", "FY2024A"],
        ["Gross written premium (USD)", "748.0", "812.4"],
        ["Net earned premium (USD)", "701.5", "764.3"],
        ["Combined ratio", "98.4%", "97.2%"],
        ["RBC ratio (company action level)", "401%", "428%"],
        ["Average GBP/USD", "1.2436", "1.2700"],
        ["Gross written premium (GBP m equivalent)", "601.5", "639.7"],
    ], col_width=1.5, first_col=3.8)
    d.text(["US GWP of $812.4m (£639.7m at $1.27/£), up 8.6%, and 15.9% of Group GWP",
            "Combined ratio improved by 120bps to 97.2%; RBC ratio of 428% against an internal minimum of 300%"],
           size=12)

    d.new_slide("Outlook and financial targets")
    d.bullets([
        "Through-the-cycle combined ratio target of below 95%",
        "FY2025 gross written premium growth guidance of 6-8% at constant exchange rates",
        "Medium-term return on equity target of at least 13%; payout ratio range of 45-50%",
        "Lloyd's Syndicate 2468 capacity of £850m for the 2025 year of account",
        "Full-year results on 27 February 2025; Capital Markets Day on 14 May 2025",
    ], size=14)
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="holdout2_insurance", deck=deck, models=[model], model_old=old,
        expects=[
            Expect(1, ("scale_error", "mismatch", "unit_period_mismatch", "unmatched"), "2.04", "underwriting profit 10x"),
            Expect(2, ("sign_error", "mismatch"), "72.4", "favourable PYD sign (should be (72.4))"),
            Expect(2, ("stale",), "199%", "solvency stale (old 199%, now 203%)"),
            Expect(3, ("mismatch", "sum_error"), "3,445.9", "Group NEP total vs 3,455.9"),
            Expect(4, ("sign_error", "mismatch"), "(80)", "Liability bps should be +80"),
            Expect(4, ("mismatch", "inconsistent_across_slides"), "98.1%", "chart Specialty FY23 CR 89.1%"),
            Expect(6, ("mismatch", "inconsistent_across_slides"), "398.6p", "BVPS transposed 389.6p"),
        ],
        controls=[
            Control(1, "£4,021.3m"), Control(1, "9.0%"), Control(1, "£3,455.9m"), Control(1, "94.1%"),
            Control(1, "70bps"), Control(1, "61.7%"), Control(1, "32.4%"), Control(1, "£72.4m"),
            Control(1, "12.5%"), Control(1, "3.9%"), Control(1, "22.0p"), Control(1, "203%"), Control(1, "428%"),
            Control(2, "3,688.5"), Control(2, "2,905.4"), Control(2, "62.9%"), Control(2, "31.9%"),
            Control(2, "94.8%"), Control(2, "(61.7)"), Control(2, "(48.2)"), Control(2, "3.6%"),
            Control(2, "11.0%"), Control(2, "366.0p"), Control(2, "389.6p"), Control(2, "192%"),
            Control(3, "1,120.4"), Control(3, "1,046.8"), Control(3, "872.5"), Control(3, "981.6"),
            Control(3, "962.1"), Control(3, "744.0"), Control(3, "59.0%"), Control(3, "70.1%"),
            Control(3, "35.1%"), Control(3, "55.7%"), Control(3, "99.9%"), Control(3, "87.4%"),
            Control(3, "27.9%"), Control(3, "24.4%"),
            Control(4, "93.0%"), Control(4, "100.4%"), Control(4, "(60)"), Control(4, "(170)"),
            Control(4, "(18.9)"), Control(4, "4.3", allow=("unmatched",), note="no single model cell; reported as unverifiable (low)"), Control(4, "(29.6)"), Control(4, "£76.7m", allow=("unmatched",)),
            Control(5, "£5.9bn"), Control(5, "£2.39bn"), Control(5, "231.5"), Control(5, "5,935.0"),
            Control(6, "47.4p"), Control(6, "19.5p"), Control(6, "49.9%"), Control(6, "612.4"),
            Control(6, "6.4%"), Control(6, "£290.1m"),
            Control(7, "$812.4m"), Control(7, "£639.7m"), Control(7, "$1.27/£"), Control(7, "8.6%"),
            Control(7, "15.9%"), Control(7, "120bps"), Control(7, "1.2436"), Control(7, "401%"),
            Control(7, "300%", allow=("unmatched",)),
            Control(8, "95%", allow=("unmatched",)), Control(8, "13%", allow=("unmatched",)),
            Control(8, "£850m", allow=("unmatched",)), Control(8, "2468", allow=("unmatched",)),
        ],
        min_verified_pct=70.0, max_extra_high=0,
    )
