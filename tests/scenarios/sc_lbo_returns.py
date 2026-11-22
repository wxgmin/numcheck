"""Sponsor LBO deck: sources & uses, leverage, pricing in bps, returns and sensitivity grids."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

EBITDA = 400.0
MULT = 11.0
TLB, NOTES, ROLL, SPONSOR = 1480.0, 720.0, 350.0, 2000.0
BUYEQ, REFI, FEES = 2600.0, 1800.0, 150.0
CASH = 110.0
DEBT = TLB + NOTES
SRC = DEBT + ROLL + SPONSOR
USES = BUYEQ + REFI + FEES
EQ_IN = ROLL + SPONSOR
YEARS = {"2028E": (3, 520.0, 1750.0), "2029E": (4, 570.0, 1500.0), "2030E": (5, 620.0, 1200.0)}
assert SRC == USES == 4550.0 and BUYEQ + REFI == EBITDA * MULT and DEBT == 2200.0


def moic(mult: float, y: str) -> float:
    n, e, nd = YEARS[y]
    return (mult * e - nd) / EQ_IN


def irr(mult: float, y: str) -> float:
    return moic(mult, y) ** (1 / YEARS[y][0]) - 1


def f1(x: float) -> str:
    return f"{x:,.1f}"


def px(x: float) -> str:
    return f"{x * 100:.1f}%"


def mx(x: float) -> str:
    return f"{x:.1f}x"


def build(out: Path) -> Scenario:
    m = Model()
    ws = m.sheet("S&U")
    r = m.block(ws, 1, "Sources", "($ in millions)", ["$m", "x LTM Adj. EBITDA"], [
        ("Term Loan B", [TLB, TLB / EBITDA]), ("Senior Notes", [NOTES, NOTES / EBITDA]),
        ("Rollover equity", [ROLL, ROLL / EBITDA]), ("Sponsor equity", [SPONSOR, SPONSOR / EBITDA]),
        ("Total sources", [SRC, SRC / EBITDA]),
    ])
    for row in ws.iter_rows(min_row=1, max_row=r, min_col=4, max_col=4):
        for c in row:
            c.number_format = '0.0"x"'
    r = m.block(ws, r, "Uses", "($ in millions)", ["$m"], [
        ("Purchase of equity", [BUYEQ]), ("Refinance existing debt", [REFI]),
        ("Fees & expenses", [FEES]), ("Total uses", [USES]),
    ])
    tm = m.sheet("Transaction")
    m.block(tm, 1, "Transaction summary", "($ in millions)", ["At close"], [
        ("LTM Adj. EBITDA", [EBITDA]), ("Entry EV / LTM Adj. EBITDA", [MULT]),
        ("Enterprise value", [EBITDA * MULT]), ("Total debt", [DEBT]), ("Cash", [CASH]),
        ("Net debt", [DEBT - CASH]), ("Total debt / LTM Adj. EBITDA", [DEBT / EBITDA]),
        ("Net debt / LTM Adj. EBITDA", [(DEBT - CASH) / EBITDA]),
    ], fmt={"Entry EV / LTM Adj. EBITDA": '0.0"x"', "Total debt / LTM Adj. EBITDA": '0.0"x"',
            "Net debt / LTM Adj. EBITDA": '0.0"x"'})
    fi = m.sheet("Financing")
    m.block(fi, 1, "Financing terms", "($ in millions)", ["Amount", "Margin (bps)", "Coupon (%)", "OID", "Tenor (yrs)"], [
        ("Term Loan B", [TLB, 350, None, 99.0, 7]),
        ("Senior Notes", [NOTES, None, 0.0825, 100.0, 8]),
    ])
    for row in fi.iter_rows(min_row=6, max_row=7, min_col=5, max_col=5):
        for c in row:
            c.number_format = "0.00%"
    rt = m.sheet("Returns")
    rows = [
        ("Exit LTM Adj. EBITDA", [YEARS[y][1] for y in YEARS]),
        ("Exit EV / LTM Adj. EBITDA", [MULT] * 3),
        ("Exit enterprise value", [MULT * YEARS[y][1] for y in YEARS]),
        ("Net debt at exit", [YEARS[y][2] for y in YEARS]),
        ("Exit equity value", [MULT * YEARS[y][1] - YEARS[y][2] for y in YEARS]),
        ("MOIC", [moic(MULT, y) for y in YEARS]),
        ("IRR", [irr(MULT, y) for y in YEARS]),
    ]
    r = m.block(rt, 1, "Sponsor returns", "($ in millions)", list(YEARS), rows,
                fmt={"MOIC": '0.0"x"', "IRR": "0.0%", "Exit EV / LTM Adj. EBITDA": '0.0"x"'})
    mults = [10.0, 11.0, 12.0]
    r = m.block(rt, r, "IRR sensitivity", "Exit multiple vs. exit year", list(YEARS),
                [(f"Exit multiple: {mx(k)}", [irr(k, y) for y in YEARS]) for k in mults],
                fmt={f"Exit multiple: {mx(k)}": "0.0%" for k in mults})
    r = m.block(rt, r, "MOIC sensitivity", "Exit multiple vs. exit year", list(YEARS),
                [(f"MOIC multiple: {mx(k)}", [moic(k, y) for y in YEARS]) for k in mults],
                fmt={f"MOIC multiple: {mx(k)}": '0.0"x"' for k in mults})
    model = m.save(out / "model.xlsx")

    d = Deck()
    d.new_slide("Transaction overview", "($ in millions)")
    d.bullets([
        f"Acquisition of Target at an enterprise value of $4.4bn, {mx(MULT)} LTM Adj. EBITDA of ${EBITDA:.1f}m",
        f"Total debt of ${f1(DEBT)}m, equal to {mx(DEBT / EBITDA)} Total debt / LTM Adj. EBITDA",
        f"Net debt / LTM Adj. EBITDA of {mx((DEBT - CASH) / EBITDA)} after ${CASH:.1f}m of retained cash",
        f"Sponsor equity cheque of ${f1(SPONSOR)}m alongside ${f1(ROLL)}m of management rollover",
    ])
    d.new_slide("Sources & uses", "($ in millions)")
    d.table([
        ["Sources", "$m", "x LTM Adj. EBITDA", "Uses", "$m"],
        ["Term Loan B", f1(TLB), mx(TLB / EBITDA), "Purchase of equity", f1(BUYEQ)],
        ["Senior Notes", f1(NOTES), mx(NOTES / EBITDA), "Refinance existing debt", f1(REFI)],
        ["Rollover equity", f1(ROLL), mx(ROLL / EBITDA), "Fees & expenses", f1(FEES)],
        ["Sponsor equity", "2,100.0", mx(SPONSOR / EBITDA), "", ""],                     # ERROR: 2,000.0
        ["Total sources", f1(SRC), mx(SRC / EBITDA), "Total uses", f1(USES)],
    ], col_width=1.6, first_col=2.2)
    d.footnote("Note: Assumes transaction closes 31-Dec-2025. Fees include financing and advisory costs.")
    d.new_slide("Entry valuation and leverage", "($ in millions)")
    d.table([
        ["", "At close"],
        ["LTM Adj. EBITDA", f1(EBITDA)],
        ["Entry EV / LTM Adj. EBITDA", mx(MULT)],
        ["Enterprise value", f1(EBITDA * MULT)],
        ["Total debt", f1(DEBT)],
        ["Total debt / LTM Adj. EBITDA", "5.0x"],                                     # ERROR: 5.5x
        ["Cash", f1(CASH)],
        ["Net debt", f1(DEBT - CASH)],
        ["Net debt / LTM Adj. EBITDA", mx((DEBT - CASH) / EBITDA)],
    ])
    d.new_slide("Financing terms", "($ in millions)")
    d.table([
        ["Tranche", "Amount", "Pricing", "OID", "Tenor"],
        ["Term Loan B", f1(TLB), "SOFR + 350bps", "99.0", "7 years"],
        ["Senior Notes", f1(NOTES), "8.25%", "100.0", "8 years"],
    ], col_width=1.6, first_col=2.2)
    d.bullets([
        "Term Loan B margin of 350bps over SOFR, issued at an OID of 99.0",
        "Senior Notes issued at par with a coupon of 8.25bps",                        # ERROR: 8.25% (825bps)
    ])
    d.new_slide("Sponsor returns", "($ in millions)")
    ir = {y: px(irr(MULT, y)) for y in YEARS}
    mo = {y: mx(moic(MULT, y)) for y in YEARS}
    ir["2029E"] = "18.4%"                                                              # ERROR: IRR typo
    mo["2030E"] = mx(moic(MULT, "2029E"))                                              # ERROR: MOIC of 2029E exit
    d.table([
        ["", *YEARS],
        ["Exit LTM Adj. EBITDA", *[f1(YEARS[y][1]) for y in YEARS]],
        ["Exit EV / LTM Adj. EBITDA", *[mx(MULT)] * 3],
        ["Exit enterprise value", *[f1(MULT * YEARS[y][1]) for y in YEARS]],
        ["Net debt at exit", *[f1(YEARS[y][2]) for y in YEARS]],
        ["Exit equity value", *[f1(MULT * YEARS[y][1] - YEARS[y][2]) for y in YEARS]],
        ["IRR", *ir.values()],
        ["MOIC", *mo.values()],
    ])
    d.footnote("Note: Returns to total equity (sponsor and rollover, pari passu); exit at year-end 31-Dec.")
    d.new_slide("Returns sensitivity", "Exit multiple vs. exit year")
    d.table([["IRR", *YEARS]] + [[mx(k), *[px(irr(k, y)) for y in YEARS]] for k in mults])
    d.table([["MOIC", *YEARS]] + [[mx(k), *[mx(moic(k, y)) for y in YEARS]] for k in mults])
    deck = d.save(out / "deck.pptx")

    controls = [
        Control(1, "$4.4bn"), Control(1, "11.0x"), Control(1, "$400.0m"), Control(1, "5.5x"),
        Control(1, "5.2x"), Control(1, "$110.0m"), Control(1, "2,200.0"), Control(1, "2,000.0"), Control(1, "350.0"),
        Control(2, "1,480.0"), Control(2, "720.0"), Control(2, "350.0"), Control(2, "2,600.0"),
        Control(2, "1,800.0"), Control(2, "150.0"), Control(2, "3.7x"), Control(2, "1.8x"), Control(2, "0.9x"),
        Control(2, "5.0x"), Control(2, "11.4x"),
        Control(3, "4,400.0"), Control(3, "11.0x"), Control(3, "2,200.0"), Control(3, "2,090.0"),
        Control(3, "5.2x"), Control(3, "110.0"),
        Control(4, "1,480.0"), Control(4, "720.0"), Control(4, "350bps"), Control(4, "99.0"), Control(4, "8.25%"),
        Control(5, "520.0"), Control(5, "5,720.0"), Control(5, "3,970.0"), Control(5, "6,270.0"),
        Control(5, "4,770.0"), Control(5, "6,820.0"), Control(5, "5,620.0"), Control(5, "1,750.0"),
        Control(5, "1,200.0"), Control(5, px(irr(MULT, "2028E"))), Control(5, px(irr(MULT, "2030E"))),
        Control(5, mx(moic(MULT, "2028E"))),
    ]
    for k in mults:
        for y in YEARS:
            controls.append(Control(6, px(irr(k, y))))
            if mx(moic(k, y)) != "2.0x":          # substring would collide with the "12.0x" axis label
                controls.append(Control(6, mx(moic(k, y))))
    controls += [Control(6, "10.0x"), Control(6, "11.0x"), Control(6, "12.0x")]   # axis labels are not claims
    return Scenario(
        name="lbo_returns", deck=deck, models=[model],
        expects=[
            Expect(2, ("mismatch", "sum_error"), "2,100.0", "sponsor equity: sources do not foot / equal uses"),
            Expect(3, ("mismatch", "ratio_reconcile"), "5.0x", "leverage inconsistent with 2,200/400 = 5.5x"),
            Expect(4, ("mismatch", "scale_error", "unit_period_mismatch"), "8.25bps", "coupon in bps vs %"),
            Expect(5, ("mismatch",), "18.4%", "IRR typo 2029E"),
            Expect(5, ("mismatch",), "2.0x", "MOIC 2030E taken from 2029E exit"),
        ],
        controls=controls, min_verified_pct=70.0, max_extra_high=0,
        description="Sponsor LBO deck with S&U, leverage, bps pricing, returns and sensitivity grids",
    )
