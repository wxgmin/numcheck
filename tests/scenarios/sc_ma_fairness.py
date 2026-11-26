"""M&A scenario: fairness opinion / valuation summary (football field, DCF, WACC x TGR grid,
precedent transactions with Median/Mean rows, trading comps).

Planted errors (4):
  1. s5  one wrong cell in the WACC x TGR sensitivity grid (9.5% / 2.5%)
  2. s6  precedent EV / LTM EBITDA median shown as 10.5x (model 10.0x)
  3. s2  football-field precedent range high end $33.10 (model $32.10)
  4. s4  DCF implied value per share computed on basic shares (137.8m) instead of diluted (148.2m)
"""

from __future__ import annotations

import statistics
from pathlib import Path

from openpyxl.styles import Font

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

NET_DEBT, DIL_SH, BASIC_SH = 596.0, 148.2, 137.8
FCF = [255.0, 275.0, 295.0, 315.0, 335.0]
WACCS = [0.08, 0.085, 0.09, 0.095, 0.10]
TGRS = [0.02, 0.025, 0.03]


def _dcf(w: float, g: float) -> tuple[float, float, float]:
    pv = sum(f / (1 + w) ** (i + 1) for i, f in enumerate(FCF))
    tv = FCF[-1] * (1 + g) / (w - g)
    return pv, tv / (1 + w) ** 5, tv


def _price(w: float, g: float) -> float:
    pv, pvtv, _ = _dcf(w, g)
    return round((pv + pvtv - NET_DEBT) / DIL_SH, 2)


PRECEDENTS = [  # target, acquirer, date, EV ($m), EV/LTM Rev, EV/LTM EBITDA
    ("Brightwell Corp.", "Castor Holdings", "Mar-24", 1850.0, 1.9, 8.4),
    ("Delmar Systems", "Orion Group", "Jan-24", 2420.0, 2.2, 9.1),
    ("Evergreen Tech", "Pallas Inc.", "Nov-23", 3150.0, 2.5, 10.3),
    ("Fulton Labs", "Quanta Partners", "Sep-23", 960.0, 2.8, 11.2),
    ("Granite Software", "Roth Capital", "Jul-23", 4280.0, 2.1, 9.7),
    ("Harbor Analytics", "Stellar Corp.", "May-23", 5100.0, 3.1, 12.5),
    ("Ionic Devices", "Titan Industries", "Feb-23", 2730.0, 2.6, 10.8),
    ("Juniper Networks Ltd.", "Umbra Group", "Dec-22", 1340.0, 1.8, 8.9),
    ("Kestrel Data", "Vantage Holdings", "Oct-22", 3890.0, 2.9, 11.6),
    ("Lumen Controls", "Westbrook plc", "Aug-22", 2060.0, 2.3, 9.4),
]
COMPS = [  # EV/NTM Revenue, EV/NTM EBITDA, NTM P/E
    ("Aldridge Corp.", 1.9, 8.6, 14.2), ("Bexley Group", 2.3, 9.4, 15.8),
    ("Calloway Inc.", 2.1, 9.0, 13.9), ("Dunmore plc", 2.8, 10.7, 17.4),
    ("Ellison Holdings", 1.7, 8.1, 12.6), ("Fairmont Ltd.", 2.5, 10.1, 16.3),
]


def _stat_rows(data: list[tuple[float, ...]]) -> tuple[list[float], list[float]]:
    cols = list(zip(*data))
    return ([round(statistics.median(c), 2) for c in cols], [round(statistics.mean(c), 2) for c in cols])


def build(out: Path) -> Scenario:
    m = Model()
    # ---- Football field ---------------------------------------------------
    dcf_lo, dcf_hi = _price(0.095, 0.02), _price(0.085, 0.03)
    ff = [("52-week trading range", 21.30, 29.80), ("Analyst price targets", 23.00, 31.00),
          ("Trading comparables", 22.40, 27.90), ("Precedent transactions", 26.20, 32.10),
          ("Discounted cash flow", dcf_lo, dcf_hi), ("LBO analysis", 21.80, 25.90)]
    ws = m.sheet("Football field")
    m.block(ws, 1, "Valuation summary: implied price per share", "($ per share)", ["Low", "High"],
            [(n, [lo, hi]) for n, lo, hi in ff], fmt={n: "$0.00" for n, _, _ in ff})
    sm = m.sheet("Summary")
    m.block(sm, 1, "Offer summary", "($ per share)", ["Value"], [
        ("Offer price per share", [27.00]), ("Unaffected share price", [21.90]),
        ("Premium to unaffected", [0.2329]),
    ], fmt={"Offer price per share": "$0.00", "Unaffected share price": "$0.00", "Premium to unaffected": "0.0%"})

    # ---- DCF --------------------------------------------------------------
    pv, pvtv, tv = _dcf(0.09, 0.025)
    pv, pvtv = round(pv, 1), round(pvtv, 1)
    ev = round(pv + pvtv, 1)
    eq = round(ev - NET_DEBT, 1)
    price = round(eq / DIL_SH, 2)
    wrong_price = round(eq / BASIC_SH, 2)
    d = m.sheet("DCF")
    m.block(d, 1, "DCF summary", "($ in millions, except per share data)", ["Base case"], [
        ("WACC", [0.09]), ("Terminal growth rate", [0.025]), ("Sum of PV of free cash flow", [pv]),
        ("PV of terminal value", [pvtv]), ("Enterprise value", [ev]), ("Less: net debt", [NET_DEBT]),
        ("Equity value", [eq]), ("Diluted shares outstanding (m)", [DIL_SH]),
        ("Basic shares outstanding (m)", [BASIC_SH]),
        ("Implied value per share", [price]), ("Implied terminal EV / EBITDA", [9.4]),
    ], fmt={"WACC": "0.0%", "Terminal growth rate": "0.0%", "Implied value per share": "$0.00",
            "Implied terminal EV / EBITDA": '0.0"x"'})

    # ---- Sensitivity grid -------------------------------------------------
    grid = {(w, g): _price(w, g) for w in WACCS for g in TGRS}
    grid[(0.09, 0.025)] = price
    sg = m.sheet("DCF sensitivity")
    sg["A1"] = "Implied value per share: WACC vs. terminal growth rate"
    sg["A1"].font = Font(bold=True)
    sg["A2"] = "($ per share)"
    sg["C4"] = "Terminal growth rate"
    sg.merge_cells("C4:E4")
    sg["B5"] = "WACC"
    for j, g in enumerate(TGRS):
        c = sg.cell(5, 3 + j, g)
        c.number_format = "0.0%"
    for i, w in enumerate(WACCS):
        c = sg.cell(6 + i, 2, w)
        c.number_format = "0.0%"
        for j, g in enumerate(TGRS):
            sg.cell(6 + i, 3 + j, grid[(w, g)]).number_format = "$0.00"
    bad = round(grid[(0.095, 0.025)] + 1.30, 2)   # planted wrong cell

    # ---- Precedents -------------------------------------------------------
    pdata = [(ev_, r, e) for _, _, _, ev_, r, e in PRECEDENTS]
    med, mean = _stat_rows(pdata)
    pr = m.sheet("Precedents")
    m.block(pr, 1, "Precedent transactions", "($ in millions)", ["EV", "EV / LTM Revenue", "EV / LTM EBITDA"],
            [(t, [e, r, x]) for t, _, _, e, r, x in PRECEDENTS] + [("Median", med), ("Mean", mean)],
            fmt={})
    for row in pr.iter_rows(min_row=6, min_col=4, max_col=5):
        for c in row:
            c.number_format = '0.0"x"'

    # ---- Trading comps ----------------------------------------------------
    cmed, cmean = _stat_rows([c[1:] for c in COMPS])
    tc = m.sheet("Comps")
    m.block(tc, 1, "Trading comparables summary", "(market data as of 15-Nov-2024)",
            ["EV / NTM Revenue", "EV / NTM EBITDA", "NTM P/E"],
            [(n, [a, b, c]) for n, a, b, c in COMPS] + [("Median", cmed), ("Mean", cmean)])
    for row in tc.iter_rows(min_row=6, min_col=3, max_col=5):
        for c in row:
            c.number_format = '0.0"x"'
    model = m.save(out / "model.xlsx")

    # ======================================================================= DECK
    def rng(lo: float, hi: float) -> str:
        return f"${lo:,.2f} – ${hi:,.2f}"

    k = Deck()
    k.new_slide("Summary of financial analyses", "Fairness opinion: Board of Directors of Meridian Holdings")
    k.bullets([
        "Offer price of $27.00 per share represents a 23.3% premium to the unaffected price of $21.90",
        f"DCF analysis implies a value of ${price:.2f} per share at a 9.0% WACC and 2.5% terminal growth rate",
        f"Implied enterprise value of ${ev:,.1f}m at the base case",
    ])
    k.new_slide("Football field: implied price per share", "($ per share)")
    rows = [["Methodology", "Implied price range", "Key assumptions"]]
    ass = {"52-week trading range": "Low / high closing price", "Analyst price targets": "Street price targets",
           "Trading comparables": "8.5x – 10.0x NTM EBITDA", "Precedent transactions": "9.0x – 11.0x LTM EBITDA",
           "Discounted cash flow": "WACC 8.5% – 9.5%; TGR 2.0% – 3.0%", "LBO analysis": "20% – 25% IRR"}
    for n, lo, hi in ff:
        hi_shown = 33.10 if n == "Precedent transactions" else hi   # ERROR: model 32.10
        rows.append([n, rng(lo, hi_shown), ass[n]])
    k.table(rows, col_width=3.0, first_col=3.0)
    k.footnote("Source: Company filings, FactSet as of 15-Nov-2024. Offer price: $27.00 per share.")

    k.new_slide("Football field", "($ per share)")
    k.chart("bar", [n for n, _, _ in ff], {"Low": [lo for _, lo, _ in ff], "High": [hi for _, _, hi in ff]},
            number_format="$0.00", height=4.2)

    k.new_slide("Discounted cash flow summary", "($ in millions, except per share data)")
    k.table([
        ["", "Base case"],
        ["WACC", "9.0%"], ["Terminal growth rate", "2.5%"],
        ["Sum of PV of free cash flow", f"{pv:,.1f}"], ["PV of terminal value", f"{pvtv:,.1f}"],
        ["Enterprise value", f"{ev:,.1f}"], ["Less: net debt", f"{NET_DEBT:,.1f}"],
        ["Equity value", f"{eq:,.1f}"], ["Diluted shares outstanding (m)", f"{DIL_SH:,.1f}"],
        ["Implied value per share", f"${wrong_price:.2f}"],                       # ERROR: basic share count
        ["Implied terminal EV / EBITDA", "9.4x"],
    ])

    k.new_slide("DCF sensitivity analysis", "Implied value per share ($)")
    hdr = [["WACC / TGR"] + [f"{g:.1%}" for g in TGRS]]
    body = []
    for w in WACCS:
        r = [f"{w:.1%}"]
        for g in TGRS:
            v = bad if (w, g) == (0.095, 0.025) else grid[(w, g)]
            r.append(f"${v:.2f}")
        body.append(r)
    k.table(hdr + body, col_width=1.4, first_col=1.8)

    k.new_slide("Precedent transactions", "($ in millions)")
    trows = [["Target", "Acquirer", "Date", "EV", "EV / LTM Revenue", "EV / LTM EBITDA"]]
    for t, a, dt, e, r, x in PRECEDENTS:
        trows.append([t, a, dt, f"{e:,.1f}", f"{r:.1f}x", f"{x:.1f}x"])
    trows.append(["Median", "", "", f"{med[0]:,.1f}", f"{med[1]:.1f}x", "10.5x"])         # ERROR: 10.0x
    trows.append(["Mean", "", "", f"{mean[0]:,.1f}", f"{mean[1]:.1f}x", f"{mean[2]:.1f}x"])
    k.table(trows, col_width=1.5, first_col=2.4)
    k.footnote("Source: Company filings, press releases, Capital IQ.")

    k.new_slide("Trading comparables summary", "(market data as of 15-Nov-2024)")
    crows = [["Company", "EV / NTM Revenue", "EV / NTM EBITDA", "NTM P/E"]]
    for n, a, b, c in COMPS:
        crows.append([n, f"{a:.1f}x", f"{b:.1f}x", f"{c:.1f}x"])
    crows.append(["Median", f"{cmed[0]:.1f}x", f"{cmed[1]:.1f}x", f"{cmed[2]:.1f}x"])
    crows.append(["Mean", f"{cmean[0]:.1f}x", f"{cmean[1]:.1f}x", f"{cmean[2]:.1f}x"])
    k.table(crows)
    k.footnote("Source: FactSet as of 15-Nov-2024.")
    deck = k.save(out / "deck.pptx")

    controls = [
        Control(1, "$27.00"), Control(1, "23.3%"), Control(1, "$21.90"), Control(1, f"${price:.2f}"),
        Control(1, f"${ev:,.1f}m"),
        Control(2, rng(21.30, 29.80)), Control(2, rng(23.00, 31.00)), Control(2, rng(22.40, 27.90)),
        Control(2, rng(dcf_lo, dcf_hi)), Control(2, rng(21.80, 25.90)),
        Control(3, "21.30"), Control(3, "29.80"),
        Control(4, f"{pv:,.1f}"), Control(4, f"{pvtv:,.1f}"), Control(4, f"{ev:,.1f}"), Control(4, f"{eq:,.1f}"),
        Control(4, "148.2"), Control(4, "596.0"), Control(4, "9.4x"), Control(4, "9.0%"),
        Control(5, f"${grid[(0.08, 0.02)]:.2f}"), Control(5, f"${grid[(0.09, 0.03)]:.2f}"),
        Control(5, f"${grid[(0.10, 0.02)]:.2f}"), Control(5, f"${grid[(0.085, 0.025)]:.2f}"),
        Control(5, f"${grid[(0.09, 0.025)]:.2f}"), Control(5, f"${grid[(0.095, 0.03)]:.2f}"),
        Control(6, "1,850.0"), Control(6, "8.4x"), Control(6, "12.5x"), Control(6, "2.3x"),
        Control(6, f"{med[0]:,.1f}"), Control(6, f"{mean[0]:,.1f}"), Control(6, f"{mean[2]:.1f}x"),
        Control(7, "8.6x"), Control(7, "17.4x"), Control(7, f"{cmed[1]:.1f}x"), Control(7, f"{cmean[2]:.1f}x"),
    ]
    return Scenario(
        name="ma_fairness", deck=deck, models=[model],
        description="Fairness opinion valuation summary with football field, DCF grid, precedents, comps",
        expects=[
            Expect(5, ("mismatch",), f"${bad:.2f}", "wrong cell in WACC x TGR grid"),
            Expect(6, ("mismatch", "sum_error"), "10.5x", "wrong median"),
            Expect(2, ("mismatch", "source_error"), "33.10", "range endpoint vs model"),
            Expect(4, ("mismatch", "scale_error", "source_error"), f"${wrong_price:.2f}", "per-share on basic shares"),
        ],
        controls=controls, min_verified_pct=60.0, max_extra_high=1,
    )
