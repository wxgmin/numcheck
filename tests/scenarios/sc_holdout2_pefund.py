"""Holdout 2: private equity fund quarterly LP report (EUR m; capital account, IRR/MOIC/DPI/RVPI/TVPI,
portfolio table with Total row, gross-to-net bridge, vintage benchmark quartiles)."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PCT = "0.0%"
PCT_NEG = "0.0%;(0.0%)"
X = '0.00"x"'
EUR = "#,##0.0"

QTRS = ["30-Sep-24", "31-Dec-24", "31-Mar-25", "30-Jun-25"]
COMMIT = 700.0
CALLED = [504.0, 525.0, 546.0, 560.0]
DIST = [98.0, 121.0, 142.0, 161.0]
GROSS_IRR = [0.126, 0.134, 0.141, 0.148]

# (company, entry, invested, realised, unrealised, gross IRR)
COMPANIES = [
    ("Norvik Logistics", "Mar-2019", 62.0, 118.4, 0.0, 0.182),
    ("Halden Software", "Nov-2019", 48.5, 0.0, 131.0, 0.201),
    ("Brevia Healthcare", "Jun-2020", 85.0, 22.5, 148.7, 0.159),
    ("Castell Packaging", "Feb-2021", 71.2, 0.0, 64.8, -0.019),
    ("Meridian Foods", "Sep-2021", 90.0, 41.0, 98.5, 0.116),
    ("Tessera Energy Services", "Apr-2022", 64.3, 0.0, 79.9, 0.074),
    ("Lumio Payments", "Jan-2023", 58.0, 0.0, 87.2, 0.193),
    ("Oakridge Education", "Aug-2023", 45.0, 0.0, 41.3, -0.046),
]


def _model(path: Path, nav_jun: float) -> str:
    nav = [548.0, 569.0, 589.0, nav_jun]
    m = Model()

    s = m.sheet("Fund Summary")
    net_irr = [0.089, 0.097, 0.105, 0.113 if nav_jun == 610.0 else 0.109]
    dpi = [d / c for d, c in zip(DIST, CALLED)]
    rvpi = [n / c for n, c in zip(nav, CALLED)]
    tvpi = [a + b for a, b in zip(dpi, rvpi)]
    m.block(s, 1, "Aldgate Capital Partners Fund IV - capital account and returns", "(EUR in millions)", QTRS, [
        ("Total commitments", [COMMIT] * 4),
        ("Paid-in capital (called)", CALLED),
        ("Unfunded commitments", [COMMIT - c for c in CALLED]),
        ("% of commitments called", [c / COMMIT for c in CALLED]),
        ("Cumulative distributions", DIST),
        ("Net asset value (NAV)", nav),
        ("Net IRR", net_irr),
        ("Gross IRR", GROSS_IRR),
        ("DPI", dpi),
        ("RVPI", rvpi),
        ("TVPI (net MOIC)", tvpi),
    ], fmt={"Total commitments": EUR, "Paid-in capital (called)": EUR, "Unfunded commitments": EUR,
            "% of commitments called": PCT, "Cumulative distributions": EUR, "Net asset value (NAV)": EUR,
            "Net IRR": PCT, "Gross IRR": PCT, "DPI": X, "RVPI": X, "TVPI (net MOIC)": X})

    p = m.sheet("Portfolio")
    rows = []
    for name, entry, inv, real, unreal, irr in COMPANIES:
        tv = real + unreal
        rows.append((name, [entry, inv, real, unreal, tv, tv / inv, irr]))
    ti = sum(c[2] for c in COMPANIES)
    tr = sum(c[3] for c in COMPANIES)
    tu = sum(c[4] for c in COMPANIES)
    rows.append(("Total portfolio", [None, ti, tr, tu, tr + tu, (tr + tu) / ti, 0.148]))
    m.block(p, 1, "Fund IV - portfolio company summary (gross, 30-Jun-25)", "(EUR in millions)",
            ["Entry date", "Invested", "Realised", "Unrealised", "Total value", "Gross MOIC", "Gross IRR"], rows)
    for r in range(6, 6 + len(rows)):
        for c in (4, 5, 6, 7):
            p.cell(r, c).number_format = EUR
        p.cell(r, 8).number_format = X
        p.cell(r, 9).number_format = PCT_NEG

    sec = m.sheet("Sector")
    m.block(sec, 1, "Unrealised value by sector (30-Jun-25)", "(EUR in millions)", ["Unrealised value", "% of total"], [
        ("Technology", [218.2, 218.2 / 651.4]),
        ("Healthcare", [148.7, 148.7 / 651.4]),
        ("Consumer", [139.8, 139.8 / 651.4]),
        ("Industrials", [144.7, 144.7 / 651.4]),
        ("Total unrealised", [651.4, 1.0]),
    ], fmt={"Technology": EUR, "Healthcare": EUR, "Consumer": EUR, "Industrials": EUR, "Total unrealised": EUR})
    for r in range(6, 11):
        sec.cell(r, 4).number_format = PCT

    b = m.sheet("Benchmark")
    m.block(b, 1, "Vintage 2019 European buyout benchmark (as of 31-Mar-25)", "(net to LPs)",
            ["Fund IV", "Top quartile", "Median", "Lower quartile", "Fund IV quartile"], [
        ("Net IRR", [0.113, 0.175, 0.128, 0.079, "3rd"]),
        ("Net TVPI", [tvpi[3], 1.52, 1.34, 1.15, "2nd"]),
        ("DPI", [dpi[3], 0.41, 0.27, 0.12, "2nd"]),
    ], fmt={"Net IRR": PCT, "Net TVPI": X, "DPI": X})
    for c in (3, 4, 5, 6):
        b.cell(6, c).number_format = PCT
        b.cell(7, c).number_format = X
        b.cell(8, c).number_format = X

    g = m.sheet("Gross to Net")
    m.block(g, 1, "Gross-to-net bridge (30-Jun-25)", "(multiple of invested / paid-in capital; IRR in %)",
            ["MOIC", "IRR"], [
        ("Gross", [1.5903, 0.148]),
        ("Management fees and fund expenses", [-0.14, -0.025]),
        ("Carried interest", [-0.0735, -0.010]),
        ("Net", [1.3768, 0.113]),
    ])
    for r in range(6, 10):
        g.cell(r, 3).number_format = '0.00"x";(0.00"x")'
        g.cell(r, 4).number_format = PCT_NEG

    a = m.sheet("Q2 Activity")
    m.block(a, 1, "Second quarter 2025 activity", "(EUR in millions)", ["Q2-25"], [
        ("Capital called in quarter", [CALLED[3] - CALLED[2]]),
        ("Distributions in quarter", [DIST[3] - DIST[2]]),
        ("Change in NAV", [nav[3] - nav[2]]),
        ("NAV growth in quarter", [nav[3] / nav[2] - 1]),
    ], fmt={"Capital called in quarter": EUR, "Distributions in quarter": EUR, "Change in NAV": EUR,
            "NAV growth in quarter": PCT})

    t = m.sheet("Fund Terms", hidden=True)
    m.block(t, 1, "Fund terms (backup)", "", ["Value"], [
        ("Carried interest", [0.20]), ("Preferred return", [0.08]), ("LP count", [64]),
        ("Management fee rate", [0.0175]),
    ], fmt={"Carried interest": PCT, "Preferred return": PCT, "Management fee rate": "0.00%"})
    return m.save(path)


def build(out: Path) -> Scenario:
    model = _model(out / "model.xlsx", 610.0)
    old = _model(out / "model_old.xlsx", 598.0)

    d = Deck()
    d.new_slide("Aldgate Capital Partners Fund IV: Q2 2025 LP report", "(EUR in millions unless stated; as of 30-Jun-25)")
    d.bullets([
        "Fund IV (2019 vintage) has commitments of EUR 700.0m, of which 80.0% (EUR 560.0m) has been called",
        "Net IRR of 11.3% and gross IRR of 14.8%; net TVPI of 1.38x with DPI of 0.29x",
        "NAV of EUR 610.0m; cumulative distributions to LPs of EUR 16.1m",
        "Eight portfolio companies at EUR 524.0m invested cost and a gross MOIC of 1.59x",
        "Norvik Logistics fully realised at 1.91x gross MOIC and an 18.2% IRR",
    ], size=14)
    d.footnote("Source: Fund administrator, Aldgate Capital Partners. Net returns after fees, expenses and carried interest.")

    d.new_slide("Capital account", "(EUR in millions)")
    d.table([
        ["", *QTRS],
        ["Total commitments", "700.0", "700.0", "700.0", "700.0"],
        ["Paid-in capital (called)", "504.0", "525.0", "546.0", "560.0"],
        ["Unfunded commitments", "196.0", "175.0", "154.0", "140.0"],
        ["% of commitments called", "72.0%", "75.0%", "78.0%", "80.0%"],
        ["Cumulative distributions", "98.0", "121.0", "142.0", "161.0"],
        ["Net asset value (NAV)", "548.0", "569.0", "589.0", "598.0"],         # ERROR stale (old 598.0; now 610.0)
    ], col_width=1.6, first_col=3.0)

    d.new_slide("Fund performance", "(net to LPs unless stated)")
    d.table([
        ["", *QTRS],
        ["Net IRR", "8.9%", "9.7%", "10.5%", "11.3%"],
        ["Gross IRR", "12.6%", "13.4%", "14.1%", "14.8%"],
        ["DPI", "0.19x", "0.23x", "0.26x", "0.29x"],
        ["RVPI", "1.09x", "1.08x", "1.08x", "1.09x"],
        ["TVPI (net MOIC)", "1.28x", "1.13x", "1.34x", "1.38x"],               # ERROR transposed 1.31x
    ], col_width=1.6, first_col=3.0)

    d.new_slide("Portfolio company summary", "(EUR in millions; gross, as of 30-Jun-25)")
    d.table([
        ["Company", "Entry date", "Invested", "Realised", "Unrealised", "Total value", "Gross MOIC", "Gross IRR"],
        ["Norvik Logistics", "Mar-2019", "62.0", "118.4", "0.0", "118.4", "1.91x", "18.2%"],
        ["Halden Software", "Nov-2019", "48.5", "0.0", "131.0", "131.0", "2.70x", "20.1%"],
        ["Brevia Healthcare", "Jun-2020", "85.0", "22.5", "148.7", "171.2", "2.01x", "15.9%"],
        ["Castell Packaging", "Feb-2021", "71.2", "0.0", "64.8", "64.8", "0.91x", "1.9%"],   # ERROR sign: (1.9%)
        ["Meridian Foods", "Sep-2021", "90.0", "41.0", "98.5", "139.5", "1.55x", "11.6%"],
        ["Tessera Energy Services", "Apr-2022", "64.3", "0.0", "79.9", "79.9", "1.24x", "7.4%"],
        ["Lumio Payments", "Jan-2023", "58.0", "0.0", "87.2", "87.2", "1.50x", "19.3%"],
        ["Oakridge Education", "Aug-2023", "45.0", "0.0", "41.3", "41.3", "0.92x", "(4.6%)"],
        ["Total portfolio", "", "542.0", "181.9", "651.4", "833.3", "1.59x", "14.8%"],     # ERROR total 524.0
    ], col_width=1.2, first_col=2.4)
    d.footnote("Gross MOIC = total value / invested capital. Gross IRR before fund-level fees, expenses and carry.")

    d.new_slide("Unrealised value by sector", "(EUR in millions; 30-Jun-25)")
    d.chart("pie", ["Technology", "Healthcare", "Consumer", "Industrials"],
            {"% of unrealised value": [218.2 / 651.4, 148.7 / 651.4, 139.8 / 651.4, 144.7 / 651.4]},
            number_format="0.0%", title="Share of EUR 651.4m unrealised value")
    d.text("Technology is the largest exposure at 33.5% of unrealised value; Norvik Logistics is fully realised.", size=12)

    d.new_slide("Benchmarking: 2019 vintage European buyout", "(net to LPs; benchmark as of 31-Mar-25)")
    d.table([
        ["", "Fund IV", "Top quartile", "Median", "Lower quartile", "Fund IV quartile"],
        ["Net IRR", "11.3%", "17.5%", "13.8%", "7.9%", "3rd"],                      # ERROR median 12.8%
        ["Net TVPI", "1.38x", "1.52x", "1.34x", "1.15x", "2nd"],
        ["DPI", "0.29x", "0.41x", "0.27x", "0.12x", "2nd"],
    ], col_width=1.6, first_col=2.2)
    d.footnote("Source: Burgiss, European buyout, vintage 2019. Quartile cut-offs as of 31-Mar-25.")

    d.new_slide("Gross-to-net bridge", "(30-Jun-25)")
    d.table([
        ["", "MOIC", "IRR"],
        ["Gross", "1.59x", "14.8%"],
        ["Management fees and fund expenses", "(0.14x)", "(2.5%)"],
        ["Carried interest", "(0.07x)", "(1.0%)"],
        ["Net", "1.38x", "11.3%"],
    ], col_width=1.6, first_col=3.6)
    d.footnote("Carried interest of 20.0% over an 8.0% preferred return; 64 limited partners.")

    d.new_slide("Second quarter activity", "(EUR in millions)")
    d.bullets([
        "EUR 14.0m of capital called in the quarter and EUR 19.0m distributed to LPs",
        "NAV increased by EUR 21.0m, or 4.6%, to EUR 610.0m",                       # ERROR 3.6%
        "Unfunded commitments of EUR 140.0m (20.0% of commitments) remain available for follow-ons",
    ], size=14)
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="holdout2_pefund", deck=deck, models=[model], model_old=old,
        expects=[
            Expect(1, ("mismatch", "scale_error", "unit_period_mismatch"), "16.1", "distributions 161.0 shown as 16.1"),
            Expect(2, ("stale",), "598.0", "NAV 30-Jun-25 stale (old model)"),
            Expect(3, ("mismatch", "ratio_reconcile", "sum_error"), "1.13x", "TVPI Dec-24 transposed"),
            Expect(4, ("sign_error", "mismatch"), "1.9%", "Castell IRR sign"),
            Expect(4, ("sum_error", "mismatch"), "542.0", "total invested"),
            Expect(6, ("mismatch",), "13.8%", "benchmark median net IRR"),
            Expect(8, ("growth_reconcile", "mismatch"), "4.6%", "NAV growth in quarter"),
        ],
        controls=[
            Control(1, "700.0"), Control(1, "80.0%"), Control(1, "560.0", allow=("unmatched",), note="no single model cell; reported as unverifiable (low)"), Control(1, "11.3%"), Control(1, "14.8%"),
            Control(1, "1.38x"), Control(1, "0.29x"), Control(1, "610.0"), Control(1, "524.0"), Control(1, "1.59x"),
            Control(1, "1.91x"), Control(1, "18.2%"),
            Control(2, "700.0"), Control(2, "140.0"), Control(2, "80.0%"), Control(2, "161.0"), Control(2, "569.0"),
            Control(2, "72.0%"),
            Control(3, "8.9%"), Control(3, "0.19x"), Control(3, "1.09x"), Control(3, "1.34x"), Control(3, "1.38x"),
            Control(3, "14.1%"),
            Control(4, "118.4"), Control(4, "833.3"), Control(4, "651.4"), Control(4, "181.9"), Control(4, "2.70x"),
            Control(4, "1.55x"), Control(4, "0.92x"), Control(4, "(4.6%)"), Control(4, "1.24x"), Control(4, "14.8%"),
            Control(4, "Mar-2019"),
            Control(5, "33.5%"), Control(5, "651.4"),
            Control(6, "17.5%"), Control(6, "7.9%"), Control(6, "1.52x"), Control(6, "0.41x"),
            Control(7, "(0.14x)"), Control(7, "(0.07x)"), Control(7, "(2.5%)"), Control(7, "(1.0%)"),
            Control(7, "20.0%"), Control(7, "8.0%"), Control(7, "64"),
            Control(8, "14.0"), Control(8, "19.0"), Control(8, "21.0"), Control(8, "140.0"), Control(8, "20.0%"),
        ],
        min_verified_pct=70.0, max_extra_high=0,
    )
