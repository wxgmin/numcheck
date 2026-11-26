"""M&A scenario: strategic merger board presentation (all-stock deal).

Model: Transaction / Financials (Acquirer | Target | Pro forma column groups) /
Synergies / Accretion / Ownership. ``model_old`` is the pre-update version with a
lower offer price ($44.50). The deck was partly refreshed: prose still says $44.50.

Planted errors (5):
  1. s1  stale offer price $44.50 in the exec-summary bullet (model now $46.20)
  2. s2  premium to unaffected shown as 18.6% (computed off $38.95, not $38.50 -> 20.0%)
  3. s3  target FY2024A EBITDA (612.0) pasted under the Acquirer column
  4. s5  FY2027E accretion 9.3% (EPS 3.12 vs 2.88 gives 8.3%)
  5. s6  ownership pie sums to 102.0% (Target 22.1% vs model 20.1%)
"""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PRE = "0.0%"


def _model(path: Path, old: bool) -> str:
    offer = 44.50 if old else 46.20
    unaff, vwap, acq_px, dil_sh, net_debt = 38.50, 39.80, 55.00, 120.0, 596.0
    eq = round(offer * dil_sh, 1)
    ev = round(eq + net_debt, 1)
    xr = round(offer / acq_px, 4)
    m = Model()
    ws = m.sheet("Transaction")
    m.block(ws, 1, "Transaction overview", "($ in millions, except per share data)", ["Value"], [
        ("Unaffected share price (prior close)", [unaff]),
        ("30-day VWAP", [vwap]),
        ("Offer price per share", [offer]),
        ("Premium to unaffected", [round(offer / unaff - 1, 4)]),
        ("Premium to 30-day VWAP", [round(offer / vwap - 1, 4)]),
        ("Fully diluted shares (m)", [dil_sh]),
        ("Implied equity value", [eq]),
        ("Net debt", [net_debt]),
        ("Implied enterprise value", [ev]),
        ("Implied EV / FY2024A EBITDA", [round(ev / 612.0, 2)]),
        ("Acquirer share price", [acq_px]),
        ("Exchange ratio", [xr]),
    ], fmt={"Unaffected share price (prior close)": "$0.00", "30-day VWAP": "$0.00",
            "Offer price per share": "$0.00", "Premium to unaffected": PRE,
            "Premium to 30-day VWAP": PRE, "Acquirer share price": "$0.00",
            "Exchange ratio": '0.0000"x"', "Implied EV / FY2024A EBITDA": '0.0"x"'})

    fs = m.sheet("Financials")
    rev = {"A": [8420.0, 9010.0], "T": [2860.0, 3120.0]}
    ebitda = {"A": [1850.0, 2010.0], "T": [612.0, 690.0]}
    ni = {"A": [940.0, 1045.0], "T": [288.0, 330.0]}
    pf = lambda d: [round(a + t, 1) for a, t in zip(d["A"], d["T"])]  # noqa: E731
    revp, ebp, nip = pf(rev), pf(ebitda), pf(ni)
    mar = lambda e, r: [round(a / b, 5) for a, b in zip(e, r)]  # noqa: E731
    m.block(fs, 1, "Standalone and pro forma financials", "($ in millions)",
            ["FY2024A", "FY2025E"] * 3, [
                ("Revenue", rev["A"] + rev["T"] + revp),
                ("Adj. EBITDA", ebitda["A"] + ebitda["T"] + ebp),
                ("Adj. EBITDA margin", mar(ebitda["A"], rev["A"]) + mar(ebitda["T"], rev["T"]) + mar(ebp, revp)),
                ("Net income", ni["A"] + ni["T"] + nip),
            ], fmt={"Adj. EBITDA margin": PRE},
            group_header=[("Acquirer", 2), ("Target", 2), ("Pro forma combined", 2)])

    sy = m.sheet("Synergies")
    m.block(sy, 1, "Cost synergies and phasing", "($ in millions)", ["2026E", "2027E", "2028E"], [
        ("Run-rate cost synergies", [210.0, 210.0, 210.0]),
        ("Phasing (% of run-rate)", [0.35, 0.70, 1.00]),
        ("Realized pre-tax cost synergies", [73.5, 147.0, 210.0]),
        ("One-time integration costs", [126.0, 126.0, 63.0]),
    ], fmt={"Phasing (% of run-rate)": "0%"})

    ac = m.sheet("Accretion")
    sa = [2.62, 2.88, 3.15]
    pe = [2.71, 3.05, 3.38] if old else [2.75, 3.12, 3.47]
    m.block(ac, 1, "Accretion / (dilution) analysis", "(per share data)", ["2026E", "2027E", "2028E"], [
        ("Acquirer standalone EPS", sa),
        ("Pro forma EPS", pe),
        ("Accretion / (dilution) %", [round(p / s - 1, 4) for p, s in zip(pe, sa)]),
    ], fmt={"Acquirer standalone EPS": "$0.00", "Pro forma EPS": "$0.00", "Accretion / (dilution) %": PRE})

    ow = m.sheet("Ownership")
    acq_sh = 400.0
    new_sh = round(dil_sh * xr, 1)
    tot = acq_sh + new_sh
    m.block(ow, 1, "Pro forma ownership", "(shares in millions)", ["Shares", "% owned"], [
        ("Acquirer existing shareholders", [acq_sh, round(acq_sh / tot, 4)]),
        ("Target shareholders (new shares issued)", [new_sh, round(new_sh / tot, 4)]),
        ("Total pro forma shares", [tot, 1.0]),
    ])
    for r in range(6, 9):
        ow.cell(r, 4).number_format = PRE
    return m.save(path)


def build(out: Path) -> Scenario:
    model = _model(out / "model.xlsx", old=False)
    model_old = _model(out / "model_old.xlsx", old=True)

    d = Deck()
    d.new_slide("Project Atlas: Board discussion materials", "Strategic merger of Northgate Industries and Halden Systems")
    d.bullets([
        "Proposed offer of $44.50 per share for 100% of Halden, payable in Northgate stock at an exchange ratio of 0.8400x",  # ERROR stale
        "Implied enterprise value of $6,140.0m, or 10.0x Halden FY2024A Adj. EBITDA",
        "Run-rate cost synergies of $210.0m, fully phased in by 2028E",
        "Transaction is accretive to Northgate EPS in every year of the plan",
    ])
    d.new_slide("Transaction overview", "($ in millions, except per share data)")
    d.table([
        ["", "Value"],
        ["Offer price per share", "$46.20"],
        ["Unaffected share price (prior close)", "$38.50"],
        ["Premium to unaffected", "18.6%"],                      # ERROR (20.0%)
        ["Premium to 30-day VWAP", "16.1%"],
        ["Fully diluted shares (m)", "120.0"],
        ["Implied equity value", "5,544.0"],
        ["Net debt", "596.0"],
        ["Implied enterprise value", "6,140.0"],
        ["Exchange ratio", "0.8400x"],
    ])
    d.footnote("Source: Company filings, FactSet as of 15-Nov-2024. Unaffected date: 8-Nov-2024.")

    d.new_slide("Acquirer vs. target financial profile", "($ in millions)")
    d.table([
        ["", "Acquirer", "", "Target", "", "Pro forma combined", ""],
        ["", "FY2024A", "FY2025E", "FY2024A", "FY2025E", "FY2024A", "FY2025E"],
        ["Revenue", "8,420.0", "9,010.0", "2,860.0", "3,120.0", "11,280.0", "12,130.0"],
        ["Adj. EBITDA", "612.0", "2,010.0", "612.0", "690.0", "2,462.0", "2,700.0"],   # ERROR s3 acquirer FY24A
        ["Adj. EBITDA margin", "22.0%", "22.3%", "21.4%", "22.1%", "21.8%", "22.3%"],
        ["Net income", "940.0", "1,045.0", "288.0", "330.0", "1,228.0", "1,375.0"],
    ], col_width=1.3, first_col=2.6, merge=[(0, 1, 0, 2), (0, 3, 0, 4), (0, 5, 0, 6)])
    d.footnote("Note: Pro forma combined is shown before synergies and purchase accounting adjustments.")

    d.new_slide("Synergy summary", "($ in millions)")
    d.text("Run-rate cost synergies of $210.0m identified, with one-time integration costs of $315.0m")
    d.table([
        ["", "2026E", "2027E", "2028E"],
        ["Run-rate cost synergies", "210.0", "210.0", "210.0"],
        ["Phasing (% of run-rate)", "35%", "70%", "100%"],
        ["Realized pre-tax cost synergies", "73.5", "147.0", "210.0"],
        ["One-time integration costs", "126.0", "126.0", "63.0"],
    ])

    d.new_slide("Accretion / (dilution) analysis", "(per share data)")
    d.table([
        ["", "2026E", "2027E", "2028E"],
        ["Acquirer standalone EPS", "$2.62", "$2.88", "$3.15"],
        ["Pro forma EPS", "$2.75", "$3.12", "$3.47"],
        ["Accretion / (dilution) %", "5.0%", "9.3%", "10.2%"],       # ERROR 2027E (8.3%)
    ])

    d.new_slide("Pro forma ownership")
    d.chart("pie", ["Acquirer existing shareholders", "Target shareholders"],
            {"% owned": [0.799, 0.221]}, number_format="0.0%")        # ERROR sums to 102.0%
    d.footnote("Note: Based on 400.0m Acquirer shares and 100.8m new shares issued to Target holders.")
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="ma_merger", deck=deck, models=[model], model_old=model_old,
        description="Strategic merger board deck with column groups and a pre-update model_old",
        expects=[
            Expect(1, ("stale", "mismatch", "inconsistent_across_slides"), "$44.50", "stale offer price"),
            Expect(2, ("mismatch", "pct_reconcile"), "18.6%", "premium off wrong price"),
            Expect(3, ("mismatch", "source_error", "inconsistent_across_slides"), "612.0",
                   "target EBITDA under acquirer column"),
            Expect(5, ("mismatch", "pct_reconcile", "growth_reconcile"), "9.3%", "accretion % vs EPS"),
            Expect(6, ("sum_error", "mismatch"), "22.1", "pie sums to 102%"),
        ],
        controls=[
            Control(1, "0.8400x"), Control(1, "$6,140.0m"), Control(1, "10.0x"), Control(1, "$210.0m"),
            Control(2, "$46.20"), Control(2, "$38.50"), Control(2, "16.1%"), Control(2, "5,544.0"),
            Control(2, "6,140.0"), Control(2, "596.0"), Control(2, "120.0"),
            Control(3, "8,420.0"), Control(3, "9,010.0"), Control(3, "2,010.0"), Control(3, "2,860.0"),
            Control(3, "3,120.0"), Control(3, "690.0"), Control(3, "11,280.0"), Control(3, "12,130.0"),
            Control(3, "2,462.0"), Control(3, "2,700.0"), Control(3, "21.4%"), Control(3, "21.8%"),
            Control(3, "1,228.0"), Control(3, "1,375.0"), Control(3, "940.0"), Control(3, "288.0"),
            Control(4, "73.5"), Control(4, "147.0"), Control(4, "126.0"), Control(4, "63.0"),
            Control(4, "35%"), Control(4, "70%"), Control(4, "$315.0m", allow=("ambiguous", "unmatched")),  # sum of row, no model total
            Control(5, "$2.62"), Control(5, "$2.88"), Control(5, "$3.15"), Control(5, "$2.75"),
            Control(5, "$3.12"), Control(5, "$3.47"), Control(5, "5.0%"), Control(5, "10.2%"),
            Control(6, "79.9"),
        ],
        min_verified_pct=60.0, max_extra_high=1,
    )
