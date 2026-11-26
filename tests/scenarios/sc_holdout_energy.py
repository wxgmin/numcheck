"""Holdout: renewables project-finance deck (generation, PPA, DSCR sculpting, IRR, EUR/USD tranches)."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

N = 18            # debt tenor / years modelled
RATE = 0.046
FX = 1.08


def _irr(cfs):
    lo, hi = -0.5, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        npv = sum(c / (1 + mid) ** t for t, c in enumerate(cfs))
        lo, hi = (mid, hi) if npv > 0 else (lo, mid)
    return (lo + hi) / 2


def _compute():
    cap, cf = 240.0, 0.38
    gen0 = round(cap * 8760 * cf / 1000, 1)                      # 798.9 GWh
    ys = list(range(1, 26))
    gen = [round(gen0 * 0.996 ** (y - 1), 1) for y in ys]
    price = [round(62.50 * 1.02 ** (y - 1), 2) for y in ys]
    rev = [round(g * p / 1000, 1) for g, p in zip(gen, price)]
    opex = [round(13.5 * 1.025 ** (y - 1), 1) for y in ys]
    ebitda = [round(r - o, 1) for r, o in zip(rev, opex)]
    cfads = [round(e - 1.0, 1) for e in ebitda]
    debt0 = 258.0
    shape = [1.08 if y <= 3 else 1.0 for y in range(1, N + 1)]

    def pv(s):
        return sum(cfads[i] / (s * shape[i]) / (1 + RATE) ** (i + 1) for i in range(N))
    lo, hi = 0.5, 4.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if pv(mid) > debt0 else (lo, mid)
    s = (lo + hi) / 2
    opening, rows = debt0, []
    for i in range(N):
        interest = round(opening * RATE, 1)
        if i == N - 1:
            principal = opening
        else:
            principal = round(cfads[i] / (s * shape[i]) - interest, 1)
        ds = round(interest + principal, 1)
        closing = round(opening - principal, 1)
        rows.append(dict(open=opening, interest=interest, principal=principal, ds=ds,
                         close=closing, dscr=round(cfads[i] / ds, 2), eq=round(cfads[i] - ds, 1)))
        opening = closing
    proj_irr = _irr([-380.0] + cfads)
    eq_cfs = [-122.0] + [r["eq"] for r in rows] + cfads[N:]
    eq_irr = _irr(eq_cfs)
    return dict(gen0=gen0, gen=gen, price=price, rev=rev, opex=opex, ebitda=ebitda, cfads=cfads,
                rows=rows, proj_irr=proj_irr, eq_irr=eq_irr, s=s)


def f1(x):
    return f"{x:,.1f}"


def build(out: Path) -> Scenario:
    c = _compute()
    rows = c["rows"]
    yrs = [2027 + i for i in range(N)]
    dscrs = [r["dscr"] for r in rows]
    mn, avg = min(dscrs), round(sum(dscrs) / N, 2)

    m = Model()
    a = m.sheet("Assumptions")
    m.block(a, 1, "Aurora Wind - key assumptions", "($ in millions unless stated)", ["Value"], [
        ("Installed capacity (MW)", [240.0]),
        ("Net capacity factor", [0.38]),
        ("PPA price, year 1 ($/MWh)", [62.50]),
        ("PPA escalator", [0.02]),
        ("EUR/USD", [FX]),
        ("Total project cost", [380.0]),
        ("Project IRR (pre-tax, unlevered)", [round(c["proj_irr"], 4)]),
        ("Equity IRR", [round(c["eq_irr"], 4)]),
        ("Minimum DSCR", [mn]),
        ("Average DSCR", [avg]),
    ], fmt={"Installed capacity (MW)": "#,##0.0", "Net capacity factor": "0.0%",
            "PPA price, year 1 ($/MWh)": "$0.00", "PPA escalator": "0.0%", "EUR/USD": "0.00",
            "Total project cost": "#,##0.0", "Project IRR (pre-tax, unlevered)": "0.0%",
            "Equity IRR": "0.0%", "Minimum DSCR": '0.00"x"', "Average DSCR": '0.00"x"'})
    op = m.sheet("Operating")
    m.block(op, 1, "Operating case (P50)", "($ in millions unless stated)", yrs, [
        ("Generation (GWh)", c["gen"][:N]),
        ("PPA price ($/MWh)", c["price"][:N]),
        ("Revenue", c["rev"][:N]),
        ("Operating costs", c["opex"][:N]),
        ("EBITDA", c["ebitda"][:N]),
        ("CFADS", c["cfads"][:N]),
    ], fmt={"Generation (GWh)": "#,##0.0", "PPA price ($/MWh)": "$0.00", "Revenue": "#,##0.0",
            "Operating costs": "#,##0.0", "EBITDA": "#,##0.0", "CFADS": "#,##0.0"})
    f = m.sheet("Financing")
    m.block(f, 1, "Sources and uses", "(amounts in millions)", ["USD ($m)", "EUR (EURm)", "USD equiv. ($m)"], [
        ("Senior USD tranche", [150.0, None, 150.0]),
        ("Senior EUR tranche", [None, 100.0, 108.0]),
        ("Sponsor equity", [122.0, None, 122.0]),
        ("Total sources", [None, None, 380.0]),
        ("Gearing", [None, None, 0.6789]),
    ], fmt={"Gearing": "0.0%"})
    s = m.sheet("Sculpting")
    m.block(s, 1, "Debt sculpting", "($ in millions)", yrs, [
        ("CFADS", c["cfads"][:N]),
        ("Opening balance", [r["open"] for r in rows]),
        ("Interest", [r["interest"] for r in rows]),
        ("Principal", [r["principal"] for r in rows]),
        ("Debt service", [r["ds"] for r in rows]),
        ("Closing balance", [r["close"] for r in rows]),
        ("DSCR", [r["dscr"] for r in rows]),
        ("Equity cash flow", [r["eq"] for r in rows]),
    ], fmt={"DSCR": '0.00"x"'})
    model = m.save(out / "model.xlsx")

    d = Deck()
    d.new_slide("Aurora Wind: project overview", "($ in millions unless stated)")
    d.bullets([
        "240.0 MW onshore wind farm with a net capacity factor of 38.0%",
        f"P50 generation of c.{c['gen0']:,.0f} GWh in year 1, contracted under a 20-year PPA",
        "PPA price of $65.20/MWh in year 1 with 2.0% annual escalation",          # ERROR 62.50
        f"Project IRR of {c['proj_irr']*100:.1f}% and equity IRR of {c['eq_irr']*100:.1f}%",
        f"Minimum DSCR of {mn:.2f}x and average DSCR of {avg:.2f}x over the 18-year tenor",
    ], size=14)
    d.new_slide("Operating case", "($ in millions unless stated)")
    gen_row = [f1(x) for x in c["gen"][:5]]
    gen_row[0] = "789.9"                                                          # ERROR transposed
    d.table([
        ["", "2027E", "2028E", "2029E", "2030E", "2031E"],
        ["Generation (GWh)"] + gen_row,
        ["PPA price ($/MWh)"] + [f"${x:.2f}" for x in c["price"][:5]],
        ["Revenue"] + [f1(x) for x in c["rev"][:5]],
        ["Operating costs"] + [f1(x) for x in c["opex"][:5]],
        ["EBITDA"] + [f1(x) for x in c["ebitda"][:5]],
    ], col_width=1.3)
    d.footnote("Source: Independent engineer P50 energy yield report; PPA dated 12-Mar-2025.")
    d.new_slide("Financing structure", "(amounts in millions)")
    d.table([
        ["", "USD ($m)", "EUR (EURm)", "USD equiv. ($m)"],
        ["Senior USD tranche", "150.0", "", "150.0"],
        ["Senior EUR tranche", "", "108.0", "108.0"],                           # ERROR EUR amount shows USD equiv
        ["Sponsor equity", "122.0", "", "122.0"],
        ["Total sources", "", "", "380.0"],
        ["Gearing", "", "", "67.9%"],
    ], col_width=1.6)
    d.footnote("Note: EUR tranche converted at EUR/USD 1.08.")
    d.new_slide("Debt sculpting profile", "($ in millions)")
    k = 8
    tbl = [[""] + [f"{y}E" for y in yrs[:k]],
           ["CFADS"] + [f1(x) for x in c["cfads"][:k]],
           ["Opening balance"] + [f1(r["open"]) for r in rows[:k]],
           ["Interest"] + [f1(r["interest"]) for r in rows[:k]],
           ["Principal"] + [f1(r["principal"]) for r in rows[:k]],
           ["Debt service"] + [f1(r["ds"]) for r in rows[:k]],
           ["Closing balance"] + [f1(r["close"]) for r in rows[:k]],
           ["DSCR"] + [f"{r['dscr']:.2f}x" for r in rows[:k]]]
    d.table(tbl, col_width=1.1, first_col=1.8)
    d.text(f"Minimum DSCR {avg:.2f}x; average DSCR {avg:.2f}x", size=12, height=0.4)   # ERROR min = avg
    d.new_slide("Annual cash flows", "($ in millions)")
    cf_chart = [round(x) for x in c["cfads"][:8]]
    cf_chart[4] = cf_chart[4] + 7                                                     # ERROR chart point
    d.chart("column", [f"{y}E" for y in yrs[:8]],
            {"CFADS": cf_chart, "Debt service": [round(r["ds"]) for r in rows[:8]]},
            number_format="#,##0")
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="holdout_energy", deck=deck, models=[model],
        expects=[
            Expect(1, ("mismatch",), "65.20", "PPA price typo"),
            Expect(2, ("mismatch", "inconsistent_across_slides"), "789.9", "generation transposed"),
            Expect(3, ("mismatch", "unit_period_mismatch", "source_error"), "108.0", "EUR tranche shows USD equivalent"),
            Expect(4, ("mismatch", "inconsistent_across_slides"), f"{avg:.2f}x", "min DSCR = avg"),
            Expect(5, ("mismatch",), str(cf_chart[4]), "chart point wrong"),
        ],
        controls=[
            Control(1, "240.0"), Control(1, "38.0%"), Control(1, f"{c['proj_irr']*100:.1f}%"),
            Control(1, f"{c['eq_irr']*100:.1f}%"), Control(1, "2.0%"),
            Control(2, f1(c["rev"][0])), Control(2, f1(c["ebitda"][0])), Control(2, f1(c["opex"][1])),
            Control(2, f"${c['price'][1]:.2f}"), Control(2, f1(c["gen"][1])), Control(2, f1(c["rev"][4])),
            Control(3, "150.0"), Control(3, "122.0"), Control(3, "380.0"), Control(3, "67.9%"),
            Control(4, f1(rows[0]["open"])), Control(4, f1(rows[0]["interest"])),
            Control(4, f1(rows[2]["ds"])), Control(4, f1(rows[7]["close"])),
            Control(4, f"{rows[0]['dscr']:.2f}x"), Control(4, f1(c["cfads"][3])),
        ],
        min_verified_pct=70.0, max_extra_high=0,
    )
