"""European restructuring deck in EUR (4:3), thousands and millions banners, losses in parentheses."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

YEARS = ["FY2023A", "FY2024A", "FY2025E"]
REV = [845300, 782100, 701400]             # EUR 000s
EBITDA = [21600, -18900, -42700]
DA = [-48100, -46500, -45000]
FIN = [-41200, -47800, -52300]
TAX = [-1900, -2100, -2400]
EBIT = [a + b for a, b in zip(EBITDA, DA)]
NL = [e + f + t for e, f, t in zip(EBIT, FIN, TAX)]
MONTHS = ["Dec-25", "Jan-26", "Feb-26", "Mar-26"]
OPCF = [-9.6, -11.2, -12.4, -10.1]
CAPEX = [-2.1, -1.8, -1.5, -1.4]
RESTR = [-3.3, -4.0, -4.1, -3.5]
NCF = [round(a + b + c, 1) for a, b, c in zip(OPCF, CAPEX, RESTR)]
OPEN = [62.0]
CLOSE = []
for n in NCF:
    CLOSE.append(round(OPEN[-1] + n, 1))
    OPEN.append(CLOSE[-1])
OPEN = OPEN[:4]
CLASSES = [("Super senior RCF", 120.0, 120.0), ("Senior secured notes", 450.0, 292.5),
           ("Senior unsecured notes", 300.0, 36.0), ("Trade and other creditors", 85.0, 8.5)]
assert CLOSE == [47.0, 30.0, 12.0, -3.0] and NCF == [-15.0, -17.0, -18.0, -15.0], (CLOSE, NCF)
TOT_CLAIM = sum(c[1] for c in CLASSES)
TOT_REC = sum(c[2] for c in CLASSES)


def k(x: float) -> str:
    return f"({abs(x):,.0f})" if x < 0 else f"{x:,.0f}"


def e1(x: float) -> str:
    return f"({abs(x):,.1f})" if x < 0 else f"{x:,.1f}"


def mm(x: float) -> float:
    return round(x / 1000, 1)


def build(out: Path) -> Scenario:
    m = Model()
    pl = m.sheet("P&L (EUR 000s)")
    neg = "#,##0;(#,##0)"
    rows = [("Revenue", REV), ("Adj. EBITDA", EBITDA), ("Depreciation & amortisation", DA), ("EBIT", EBIT),
            ("Net finance costs", FIN), ("Tax", TAX), ("Net loss", NL)]
    m.block(pl, 1, "Consolidated income statement", "(EUR 000s)", YEARS, rows, fmt={r[0]: neg for r in rows})
    sm = m.sheet("Summary (EUR m)")
    neg1 = "#,##0.0;(#,##0.0)"
    rows = [("Revenue", [mm(v) for v in REV]), ("Adj. EBITDA", [mm(v) for v in EBITDA]),
            ("Net loss", [mm(v) for v in NL])]
    m.block(sm, 1, "Key financials", "(EUR in millions)", YEARS, rows, fmt={r[0]: neg1 for r in rows})
    lq = m.sheet("Liquidity (EUR m)")
    rows = [("Opening cash", OPEN), ("Operating cash flow", OPCF), ("Capex", CAPEX),
            ("Restructuring costs", RESTR), ("Net cash flow", NCF), ("Closing cash", CLOSE)]
    m.block(lq, 1, "Liquidity forecast", "(EUR in millions)", MONTHS, rows, fmt={r[0]: neg1 for r in rows})
    rc = m.sheet("Recoveries (EUR m)")
    rows = [(n, [c, r, r / c]) for n, c, r in CLASSES] + [("Total", [TOT_CLAIM, TOT_REC, TOT_REC / TOT_CLAIM])]
    m.block(rc, 1, "Estimated creditor recoveries", "(EUR in millions)",
            ["Claim", "Recovery", "Recovery (%)"], rows)
    for row in rc.iter_rows(min_row=6, max_row=11, min_col=3, max_col=5):
        for c in row:
            c.number_format = "0.0%" if c.column == 5 else "#,##0.0"
    model = m.save(out / "model.xlsx")

    d = Deck(widescreen=False)
    d.new_slide("Situation overview", "(€ in millions)")
    d.bullets([
        "FY2025E revenue of €701.4m, a decline of 10.3% year-on-year",
        "Adj. EBITDA of €(42.7)m and net loss of €(142.4)m in FY2025E",
        "Liquidity exhausted in Mar-26 absent new money",
        "Senior secured notes expected to recover 65.0%",
    ], size=14)
    d.new_slide("Historical income statement", "(€000s)")
    d.table([
        ["", *YEARS],
        ["Revenue", *[k(v) for v in REV]],
        ["Adj. EBITDA", *[k(v) for v in EBITDA]],
        ["Depreciation & amortisation", *[k(v) for v in DA]],
        ["EBIT", *[k(v) for v in EBIT]],
        ["Net finance costs", *[k(v) for v in FIN]],
        ["Tax", *[k(v) for v in TAX]],
        ["Net loss", *[k(v) for v in NL]],
    ], col_width=1.6, first_col=3.0)
    d.footnote("Source: Company management accounts. Figures in EUR thousands.")
    d.new_slide("Key financials", "(€ in millions)")
    d.table([
        ["", *YEARS],
        ["Revenue", *[e1(mm(v)) for v in REV]],
        ["Adj. EBITDA", e1(mm(EBITDA[0])), e1(mm(EBITDA[1])), "(42,700)"],            # ERROR: scale slip (42.7)
        ["Net loss", *[e1(mm(v)) for v in NL]],
    ], col_width=1.6, first_col=3.0)
    d.new_slide("Liquidity runway", "(€ in millions)")
    ncf = [e1(v) for v in NCF]
    ncf[2] = "(16.0)"                                                                   # ERROR: components sum to (18.0)
    clo = [e1(v) for v in CLOSE]
    clo[3] = "3.0"                                                                      # ERROR: should be (3.0)
    d.table([
        ["", *MONTHS],
        ["Opening cash", *[e1(v) for v in OPEN]],
        ["Operating cash flow", *[e1(v) for v in OPCF]],
        ["Capex", *[e1(v) for v in CAPEX]],
        ["Restructuring costs", *[e1(v) for v in RESTR]],
        ["Net cash flow", *ncf],
        ["Closing cash", *clo],
    ], col_width=1.3, first_col=2.6)
    d.footnote("Note: Closing cash below zero indicates funding shortfall. Excludes proceeds of new money.")
    d.new_slide("Creditor recoveries", "(€ in millions)")
    pct = [f"{r / c * 100:.1f}%" for _, c, r in CLASSES]
    pct[1] = "75.0%"                                                                    # ERROR: 292.5/450 = 65.0%
    d.table([
        ["Class", "Claim", "Recovery", "Recovery (%)"],
        *[[n, e1(c), e1(r), p] for (n, c, r), p in zip(CLASSES, pct)],
        ["Total", e1(TOT_CLAIM), e1(TOT_REC), f"{TOT_REC / TOT_CLAIM * 100:.1f}%"],
    ], col_width=1.6, first_col=3.0)
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="restructuring_eur", deck=deck, models=[model],
        expects=[
            Expect(3, ("scale_error", "mismatch"), "42,700", "EUR m slide shows EUR 000s value"),
            Expect(4, ("sign_error", "mismatch"), "3.0", "closing cash (3.0) shown positive"),
            Expect(4, ("sum_error", "mismatch"), "(16.0)", "net cash flow does not foot to (18.0)"),
            Expect(5, ("pct_reconcile", "ratio_reconcile", "mismatch"), "75.0%", "292.5/450.0 = 65.0%"),
        ],
        controls=[
            Control(1, "701.4"), Control(1, "(42.7)"), Control(1, "(142.4)"), Control(1, "65.0%"),
            Control(1, "10.3%"),
            *[Control(2, k(v)) for v in REV + EBITDA + DA + EBIT + FIN + TAX + NL],
            Control(3, "845.3"), Control(3, "782.1"), Control(3, "701.4"), Control(3, "21.6"), Control(3, "(18.9)"),
            Control(3, "(69.6)"), Control(3, "(115.3)"), Control(3, "(142.4)"),
            Control(4, "62.0"), Control(4, "(9.6)"), Control(4, "(15.0)"), Control(4, "(17.0)"), Control(4, "47.0"),
            Control(4, "(12.4)"), Control(4, "(4.1)"), Control(4, "12.0"), Control(4, "(10.1)"),
            Control(5, "120.0"), Control(5, "450.0"), Control(5, "300.0"), Control(5, "292.5"),
            Control(5, "36.0"), Control(5, "8.5"), Control(5, "955.0"), Control(5, "457.0"),
            Control(5, "100.0%"), Control(5, "12.0%"), Control(5, "10.0%"), Control(5, "47.9%"),
        ],
        min_verified_pct=65.0, max_extra_high=0,
        description="EUR restructuring deck: 000s vs millions, negatives in parentheses, recoveries, 4:3 slides",
    )
