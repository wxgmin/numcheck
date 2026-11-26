"""Credit / capital structure deck: cap table, maturity profile, coverage, net debt with negative cash."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

EBITDA = 500.0
RCF, TLA, TLB, SSN, SUN = 70.0, 430.0, 900.0, 600.0, 500.0
CASH = 180.0
RCF_COMMIT = 450.0
SECURED = RCF + TLA + TLB + SSN
DEBT = SECURED + SUN
NET = DEBT - CASH
UNDRAWN = RCF_COMMIT - RCF
LIQ = UNDRAWN + CASH
INT = {"LTM": (500.0, 175.0, 60.0), "FY25": (530.0, 172.0, 65.0)}
MAT = {2028: TLA, 2029: RCF, 2030: TLB, 2031: SSN, 2032: SUN}
assert SECURED == 2000.0 and DEBT == 2500.0 and NET == 2320.0 and LIQ == 560.0


def f1(x: float) -> str:
    return f"({abs(x):,.1f})" if x < 0 else f"{x:,.1f}"


def mx(x: float) -> str:
    return f"{x:.1f}x"


def build(out: Path) -> Scenario:
    m = Model()
    cap = m.sheet("Cap table")
    cum = lambda v: v / EBITDA  # noqa: E731
    m.block(cap, 1, "Capitalisation as at 30-Sep-25", "($ in millions)",
            ["Amount", "Cum. x LTM Adj. EBITDA", "Margin (bps)", "Coupon (%)", "Maturity"], [
        ("Revolving credit facility", [RCF, cum(RCF), 250, None, 2029]),
        ("Term Loan A", [TLA, cum(RCF + TLA), 250, None, 2028]),
        ("Term Loan B", [TLB, cum(RCF + TLA + TLB), 325, None, 2030]),
        ("Senior Secured Notes", [SSN, cum(SECURED), None, 0.0525, 2031]),
        ("Total secured debt", [SECURED, cum(SECURED), None, None, None]),
        ("Senior Notes", [SUN, cum(DEBT), None, 0.07, 2032]),
        ("Total debt", [DEBT, cum(DEBT), None, None, None]),
        ("Less: cash", [-CASH, None, None, None, None]),
        ("Net debt", [NET, cum(NET), None, None, None]),
    ])
    for row in cap.iter_rows(min_row=6, max_row=14, min_col=3, max_col=7):
        for c in row:
            col = c.column
            c.number_format = {3: "#,##0.0;(#,##0.0)", 4: '0.0"x"', 5: "0", 6: "0.000%", 7: "0"}[col]
    st = m.sheet("Credit stats")
    r = m.block(st, 1, "Credit statistics", "($ in millions)", ["LTM Sep-25A", "FY2025E"], [
        ("Adj. EBITDA", [INT["LTM"][0], INT["FY25"][0]]),
        ("Cash interest expense", [INT["LTM"][1], INT["FY25"][1]]),
        ("Capex", [INT["LTM"][2], INT["FY25"][2]]),
        ("Adj. EBITDA / cash interest", [INT["LTM"][0] / INT["LTM"][1], INT["FY25"][0] / INT["FY25"][1]]),
        ("(Adj. EBITDA - capex) / cash interest",
         [(INT[k][0] - INT[k][2]) / INT[k][1] for k in INT]),
        ("Minimum interest cover covenant", [2.0, 2.0]),
    ], fmt={k: '0.0"x"' for k in ("Adj. EBITDA / cash interest", "(Adj. EBITDA - capex) / cash interest",
                                  "Minimum interest cover covenant")})
    m.block(st, r, "Liquidity", "($ in millions)", ["30-Sep-25"], [
        ("RCF commitment", [RCF_COMMIT]), ("Undrawn RCF", [UNDRAWN]), ("Total liquidity", [LIQ]),
    ])
    mt = m.sheet("Maturities")
    m.block(mt, 1, "Debt maturity profile", "($ in millions)", list(MAT), [
        ("Debt maturing", list(MAT.values())),
        ("Cumulative maturities", [sum(list(MAT.values())[: i + 1]) for i in range(5)]),
    ], fmt={"Debt maturing": "#,##0.0", "Cumulative maturities": "#,##0.0"})
    model = m.save(out / "model.xlsx")

    d = Deck()
    d.new_slide("Credit summary", "($ in millions)")
    d.bullets([
        f"Gross debt of ${f1(DEBT)}m ({mx(DEBT / EBITDA)} LTM Adj. EBITDA) and net debt of ${f1(NET)}m (c.4.6x)",
        "c.$750m of liquidity at 30-Sep-25 including undrawn RCF",                    # ERROR: 560
        f"Interest cover of {mx(INT['LTM'][0] / INT['LTM'][1])}, comfortably >2.0x covenant minimum",
        "c.$1.4bn of debt matures by 2030",
    ])
    d.new_slide("Capitalisation table", "($ in millions)")
    d.table([
        ["Tranche", "Amount", "Pricing", "Maturity", "x LTM Adj. EBITDA (cum.)"],
        ["Revolving credit facility ($450m)", f1(RCF), "S + 250bps", "2029", mx(cum(RCF))],
        ["Term Loan A", f1(TLA), "S + 250bps", "2028", mx(cum(RCF + TLA))],
        ["Term Loan B", f1(TLB), "S + 325bps", "2030", mx(cum(RCF + TLA + TLB))],
        ["5.250% Senior Secured Notes", f1(SSN), "5.250%", "2031", "3.6x"],           # ERROR: 4.0x
        ["Total secured debt", f1(SECURED), "", "", mx(cum(SECURED))],
        ["7.000% Senior Notes", f1(SUN), "7.000%", "2032", mx(cum(DEBT))],
        ["Total debt", f1(DEBT), "", "", mx(cum(DEBT))],
        ["Less: cash", f1(-CASH), "", "", ""],
        ["Net debt", f1(NET), "", "", "4.6x"],
    ], col_width=1.7, first_col=3.0)
    d.footnote("Source: Company information as at 30-Sep-25. SOFR-based loans shown as margin over SOFR.")
    d.new_slide("Net debt and liquidity", "($ in millions)")
    d.table([
        ["", "30-Sep-25"],
        ["Total debt", f1(DEBT)],
        ["Less: cash", "180.0"],                                                       # ERROR: (180.0)
        ["Net debt", f1(NET)],
        ["Net debt / LTM Adj. EBITDA", "4.6x"],
    ])
    d.bullets([f"Undrawn RCF of ${f1(UNDRAWN)}m; total liquidity of c.$550m"])
    d.new_slide("Debt maturity profile ($m)")
    vals = dict(MAT)
    vals[2030] = 800.0                                                                  # ERROR: 900.0
    d.chart("column", [str(y) for y in vals], {"Debt maturing": list(vals.values())}, number_format="#,##0.0")
    d.footnote("Note: RCF maturity assumes no extension option is exercised.")
    d.new_slide("Interest coverage", "($ in millions)")
    d.table([
        ["", "LTM Sep-25A", "FY2025E"],
        ["Adj. EBITDA", f1(INT["LTM"][0]), f1(INT["FY25"][0])],
        ["Cash interest expense", f1(INT["LTM"][1]), f1(INT["FY25"][1])],
        ["Capex", f1(INT["LTM"][2]), f1(INT["FY25"][2])],
        ["Adj. EBITDA / cash interest", *[mx(INT[k][0] / INT[k][1]) for k in INT]],
        ["(Adj. EBITDA - capex) / cash interest", *[mx((INT[k][0] - INT[k][2]) / INT[k][1]) for k in INT]],
    ], col_width=1.6)
    d.bullets(["Minimum interest cover covenant of 2.0x"])
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="credit_capstructure", deck=deck, models=[model],
        expects=[
            Expect(1, ("mismatch", "stale"), "750", "c.$750m vs model liquidity 560"),
            Expect(2, ("mismatch", "ratio_reconcile"), "3.6x", "cumulative leverage 4.0x"),
            Expect(3, ("sign_error", "mismatch"), "180.0", "cash must be (180.0)"),
            Expect(4, ("mismatch",), "800", "2030 maturity bar 900"),
        ],
        controls=[
            Control(1, "2,500.0"), Control(1, "5.0x"), Control(1, "2,320.0"), Control(1, "4.6x"),
            # "c.$1.4bn matures by 2030" is an aggregate over several maturity buckets with no single
            # source cell: unverifiable (low) is the honest outcome, a mismatch would not be.
            Control(1, "2.9x"), Control(1, ">2.0x"), Control(1, "$1.4bn", allow=("unmatched",)),
            Control(2, "70.0"), Control(2, "430.0"), Control(2, "900.0"), Control(2, "600.0"),
            Control(2, "2,000.0"), Control(2, "500.0"), Control(2, "2,500.0"), Control(2, "(180.0)"),
            Control(2, "2,320.0"), Control(2, "0.1x"), Control(2, "1.0x"), Control(2, "2.8x"), Control(2, "4.0x"),
            Control(2, "5.0x"), Control(2, "4.6x"), Control(2, "5.250%"), Control(2, "7.000%"), Control(2, "325bps"),
            Control(3, "2,500.0"), Control(3, "2,320.0"), Control(3, "4.6x"), Control(3, "380.0"), Control(3, "$550m"),
            Control(4, "430"), Control(4, "70.0"), Control(4, "600"), Control(4, "500"),
            Control(5, "530.0"), Control(5, "175.0"), Control(5, "172.0"), Control(5, "60.0"), Control(5, "65.0"),
            Control(5, "2.9x"), Control(5, "3.1x"), Control(5, "2.5x"), Control(5, "2.7x"), Control(5, "2.0x"),
        ],
        min_verified_pct=70.0, max_extra_high=0,
        description="Credit/cap structure deck: cap table, maturity chart, coverage, negative cash, c. approximations",
    )
