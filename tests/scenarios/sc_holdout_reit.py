"""Holdout: REIT / real estate deck (NOI, cap rates, FFO/AFFO per share, leverage, regional portfolio)."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PCT = "0.0%"
PCT_NEG = "0.0%;(0.0%)"


def _model(path: Path, affo25_ps: float) -> str:
    m = Model()
    a = m.sheet("Annual")
    m.block(a, 1, "Marlow Realty Trust - annual summary", "($ in millions, except per share data)",
            ["FY2023A", "FY2024A", "FY2025E"], [
        ("Net operating income", [412.0, 436.8, 459.0]),
        ("Adj. EBITDA", [475.0, 495.0, 515.0]),
        ("FFO", [300.0, 318.5, 339.0]),
        ("AFFO", [264.6, 281.2, 300.0]),
        ("Diluted shares (m)", [147.0, 148.0, 150.0]),
        ("FFO per share", [2.0408, 2.1520, 2.26]),
        ("AFFO per share", [1.80, 1.90, affo25_ps]),
        ("Dividend per share", [1.44, 1.52, 1.60]),
        ("Occupancy", [0.938, 0.954, 0.960]),
        ("Same-property NOI growth", [0.032, -0.014, 0.021]),
        ("Net debt", [3150.0, 3210.0, 3240.0]),
        ("Net debt / Adj. EBITDA", [6.6316, 6.4848, 6.2913]),
    ], fmt={"Net operating income": "#,##0.0", "Adj. EBITDA": "#,##0.0", "FFO": "#,##0.0",
            "AFFO": "#,##0.0", "Diluted shares (m)": "#,##0.0", "FFO per share": "$0.00",
            "AFFO per share": "$0.00", "Dividend per share": "$0.00", "Occupancy": PCT,
            "Same-property NOI growth": PCT_NEG, "Net debt": "#,##0.0",
            "Net debt / Adj. EBITDA": '0.0"x"'})
    q = m.sheet("Quarterly")
    m.block(q, 1, "Quarterly results - FY2024", "($ in millions, except per share data)",
            ["Q1-24", "Q2-24", "Q3-24", "Q4-24"], [
        ("Net operating income", [106.4, 108.2, 110.1, 112.1]),
        ("FFO per share", [0.52, 0.53, 0.54, 0.56]),
        ("AFFO per share", [0.46, 0.47, 0.48, 0.49]),
        ("Occupancy", [0.942, 0.947, 0.950, 0.954]),
        ("Same-property NOI growth", [-0.021, -0.018, -0.010, -0.007]),
    ], fmt={"Net operating income": "#,##0.0", "FFO per share": "$0.00", "AFFO per share": "$0.00",
            "Occupancy": PCT, "Same-property NOI growth": PCT_NEG})
    p = m.sheet("Portfolio")
    m.block(p, 1, "Portfolio by region (31-Dec-24)", "($ in millions; SF in millions)",
            ["Properties", "SF (m)", "NOI", "Cap rate", "Implied value", "Occupancy"], [
        ("Northeast", [38, 14.2, 152.4, 0.054, 2822.2, 0.961]),
        ("Southeast", [31, 12.5, 118.6, 0.059, 2010.2, 0.948]),
        ("Midwest", [24, 9.8, 81.3, 0.064, 1270.3, 0.942]),
        ("West", [29, 11.9, 84.5, 0.056, 1508.9, 0.959]),
        ("Total portfolio", [122, 48.4, 436.8, 0.0574, 7611.6, 0.954]),
    ])
    for r in range(6, 11):
        p.cell(r, 4).number_format = "#,##0.0"
        p.cell(r, 5).number_format = "#,##0.0"
        p.cell(r, 6).number_format = "0.0%"
        p.cell(r, 7).number_format = "#,##0.0"
        p.cell(r, 8).number_format = "0.0%"
    v = m.sheet("Valuation")
    m.block(v, 1, "Valuation and leverage", "($ in millions, except per share data)", ["Value"], [
        ("Share price", [31.00]),
        ("Dividend yield", [0.0490]),
        ("AFFO payout ratio", [0.80]),
        ("Blended cap rate", [0.0574]),
        ("Implied gross asset value", [7611.6]),
        ("Net debt", [3210.0]),
        ("Loan-to-value", [0.4217]),
        ("Net debt / Adj. EBITDA", [6.4848]),
    ], fmt={"Share price": "$0.00", "Dividend yield": PCT, "AFFO payout ratio": "0.0%",
            "Blended cap rate": "0.00%", "Implied gross asset value": "#,##0.0",
            "Net debt": "#,##0.0", "Loan-to-value": PCT, "Net debt / Adj. EBITDA": '0.0"x"'})
    return m.save(path)


def build(out: Path) -> Scenario:
    model = _model(out / "model.xlsx", 2.00)
    old = _model(out / "model_old.xlsx", 1.94)

    d = Deck()
    d.new_slide("Marlow Realty Trust: investment highlights", "($ in millions, except per share data)")
    d.bullets([
        "FY2024 NOI of $436.8m, up 6.0% year on year, on a portfolio of 122 properties",
        "FFO per share of $2.15 and AFFO per share of $1.90 in FY2024",
        "Occupancy reached 95.4% at year end; same-property NOI declined 1.4% as expected",
        "Net debt / Adj. EBITDA of 5.6x with a loan-to-value of 42.2%",
        "Blended cap rate of 5.7% implies a gross asset value of c.$7.6bn",
    ], size=14)
    d.new_slide("Financial summary", "($ in millions, except per share data)")
    d.table([
        ["", "FY2023A", "FY2024A", "FY2025E"],
        ["Net operating income", "412.0", "436.8", "459.0"],
        ["Adj. EBITDA", "475.0", "495.0", "515.0"],
        ["FFO", "300.0", "318.5", "339.0"],
        ["AFFO", "264.6", "281.2", "300.0"],
        ["FFO per share", "$2.04", "$2.15", "$2.26"],
        ["AFFO per share", "$1.80", "$1.90", "$1.94"],             # ERROR stale (old model 1.94, now 2.00)
        ["Dividend per share", "$1.44", "$1.52", "$1.60"],
        ["Occupancy", "93.8%", "95.4%", "96.0%"],
        ["Same-property NOI growth", "3.2%", "1.4%", "2.1%"],      # ERROR sign: (1.4%)
    ])
    d.footnote("Source: Company filings, management projections. FFO and AFFO per NAREIT definitions.")
    d.new_slide("Quarterly performance - FY2024", "($ in millions, except per share data)")
    d.table([
        ["", "Q1-24", "Q2-24", "Q3-24", "Q4-24"],
        ["Net operating income", "106.4", "108.2", "110.1", "112.1"],
        ["FFO per share", "$0.52", "$0.53", "$0.54", "$0.56"],
        ["AFFO per share", "$0.46", "$0.47", "$0.48", "$0.49"],
        ["Occupancy", "94.2%", "94.7%", "95.0%", "95.4%"],
        ["Same-property NOI growth", "(2.1%)", "(1.8%)", "(1.0%)", "(0.7%)"],
    ])
    d.new_slide("Portfolio by region", "($ in millions; SF in millions)")
    d.table([
        ["Region", "Properties", "SF (m)", "NOI", "Cap rate", "Implied value", "Occupancy"],
        ["Northeast", "38", "14.2", "152.4", "5.4%", "2,822.2", "96.1%"],
        ["Southeast", "31", "12.5", "118.6", "5.9%", "2,010.2", "94.8%"],
        ["Midwest", "24", "9.8", "81.8", "6.4%", "1,270.3", "94.2%"],   # ERROR 81.8 vs 81.3
        ["West", "29", "11.9", "84.5", "5.6%", "1,508.9", "95.9%"],
        ["Total portfolio", "122", "48.4", "436.8", "5.7%", "7,611.6", "95.4%"],
    ], col_width=1.3, first_col=2.2)
    d.footnote("Source: Company filings as of 31-Dec-24. Implied value = annualised NOI / cap rate.")
    d.new_slide("Valuation and leverage", "($ in millions, except per share data)")
    d.table([
        ["", "Value"],
        ["Share price", "$31.00"],
        ["Dividend yield", "5.4%"],                                      # ERROR 4.9%
        ["AFFO payout ratio", "80.0%"],
        ["Blended cap rate", "5.74%"],
        ["Implied gross asset value", "7,611.6"],
        ["Net debt", "3,210.0"],
        ["Loan-to-value", "42.2%"],
        ["Net debt / Adj. EBITDA", "6.5x"],
    ], col_width=1.6)
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="holdout_reit", deck=deck, models=[model], model_old=old,
        expects=[
            Expect(1, ("mismatch", "ratio_reconcile", "unmatched"), "5.6x", "leverage multiple transposed"),
            Expect(2, ("stale",), "$1.94", "AFFO/sh FY25E stale"),
            Expect(2, ("sign_error", "mismatch"), "1.4%", "same-property growth sign"),
            Expect(4, ("mismatch", "sum_error"), "81.8", "Midwest NOI"),
            Expect(5, ("mismatch", "ratio_reconcile"), "5.4%", "dividend yield"),
        ],
        controls=[
            Control(1, "$436.8m"), Control(1, "122"), Control(1, "$2.15"), Control(1, "$1.90"),
            Control(1, "95.4%"), Control(1, "42.2%"), Control(1, "5.7%"), Control(1, "7.6bn"),
            Control(2, "412.0"), Control(2, "495.0"), Control(2, "318.5"), Control(2, "281.2"),
            Control(2, "$2.04"), Control(2, "$2.26"), Control(2, "$1.52"), Control(2, "93.8%"),
            Control(2, "96.0%"), Control(2, "3.2%"), Control(2, "2.1%"),
            Control(3, "106.4"), Control(3, "112.1"), Control(3, "$0.56"), Control(3, "$0.46"),
            Control(3, "94.7%"), Control(3, "(2.1%)"), Control(3, "(0.7%)"),
            Control(4, "152.4"), Control(4, "118.6"), Control(4, "84.5"), Control(4, "436.8"),
            Control(4, "2,822.2"), Control(4, "7,611.6"), Control(4, "48.4"), Control(4, "96.1%"),
            Control(5, "$31.00"), Control(5, "80.0%"), Control(5, "5.74%"), Control(5, "3,210.0"),
            Control(5, "42.2%"), Control(5, "6.5x"),
        ],
        min_verified_pct=70.0, max_extra_high=0,
    )
