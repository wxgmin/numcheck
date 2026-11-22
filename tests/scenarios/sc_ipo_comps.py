"""ECM scenario: IPO valuation / trading comps book.

Model: Comps sheet (10 peers, $bn market cap / EV, multiples, Median and Mean rows
computed here), IPO sheet (valuation range, implied multiples, $m), Proceeds sheet.
Deck: exec summary, comps table (some rounding), IPO valuation range, use of
proceeds (must foot), peer snapshot in $m (vs $bn on the comps slide).
"""

from __future__ import annotations

import statistics
from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PEERS = [
    # name, price, mcap $bn, EV $bn, EV/Rev NTM, EV/EBITDA LTM, EV/EBITDA NTM, P/E NTM, rev growth %
    ("Alder Software", 112.40, 42.3, 46.1, 5.2, 19.8, 16.2, 28.4, 14.2),
    ("Bexley Systems", 87.15, 31.8, 33.5, 4.6, 17.9, 15.1, 25.7, 12.8),
    ("Corvane Technologies", 64.30, 25.4, 27.9, 4.1, 16.4, 14.0, 23.9, 11.5),
    ("Dunmore Cloud", 203.75, 58.9, 61.2, 6.3, 21.7, 17.8, 31.2, 16.9),
    ("Eastgate Data", 45.60, 18.7, 21.4, 3.5, 15.2, 13.3, 21.8, 9.7),
    ("Fenwick Digital", 139.20, 36.4, 38.0, 4.9, 18.6, 15.9, 27.0, 13.4),
    ("Garrow Networks", 72.85, 22.1, 25.3, 3.8, 14.9, 12.6, 20.5, 8.9),
    ("Hollis Platforms", 158.90, 47.5, 49.8, 5.7, 20.3, 17.1, 29.6, 15.3),
    ("Ivers Analytics", 51.20, 14.9, 17.6, 3.2, 13.8, 11.9, 19.2, 7.6),
    ("Jarrow Interactive", 96.40, 29.0, 31.7, 4.4, 17.2, 14.7, 24.8, 12.1),
]
HDR = ["Share price ($)", "Market cap ($bn)", "EV ($bn)", "EV / Revenue NTM", "EV / EBITDA LTM",
       "EV / EBITDA NTM", "P / E NTM", "Revenue growth (%)"]


def r1(x: float) -> float:
    return round(x + 1e-9, 1)


def stats():
    cols = list(zip(*[p[1:] for p in PEERS]))
    med = [statistics.median(c) for c in cols]
    mean = [statistics.fmean(c) for c in cols]
    return med, mean


def build(out: Path) -> Scenario:
    med, mean = stats()
    m = Model()
    ws = m.sheet("Comps")
    rows = [(p[0], list(p[1:])) for p in PEERS]
    rows.append(("Median", [round(med[0], 2)] + [r1(x) for x in med[1:]]))
    rows.append(("Mean", [round(mean[0], 2)] + [r1(x) for x in mean[1:]]))
    fmt = {}
    m.block(ws, 1, "Trading comparables - Project Atlas", "($bn, except per share data; multiples in x)",
            HDR, rows, fmt=fmt)
    for row in ws.iter_rows(min_row=6, max_row=18, min_col=3, max_col=10):
        for c in row:
            if c.value is not None:
                c.number_format = "0.0"
    ws["A20"] = "Source: FactSet as of 15-Nov-2024. NTM = next twelve months consensus."

    ipo = m.sheet("IPO")
    # Subject: equity 8,500-9,500; net debt 1,050; LTM EBITDA 520; NTM EBITDA 640; NTM rev 2,350; NTM NI 380
    eq = [8500.0, 9500.0]
    nd = 1050.0
    ev = [e + nd for e in eq]
    m.block(ipo, 1, "IPO valuation range", "($ in millions)", ["Low", "High"], [
        ("Equity value", eq),
        ("Net debt (pro forma)", [nd, nd]),
        ("Enterprise value", ev),
        ("Implied EV / Revenue NTM", [r1(ev[0] / 2350), r1(ev[1] / 2350)]),
        ("Implied EV / EBITDA LTM", [r1(ev[0] / 520), r1(ev[1] / 520)]),
        ("Implied EV / EBITDA NTM", [r1(ev[0] / 640), r1(ev[1] / 640)]),
        ("Implied P / E NTM", [r1(eq[0] / 380), r1(eq[1] / 380)]),
    ])
    uses = [("Repay term loan", 450.0), ("Fund capex and growth investments", 250.0),
            ("Bolt-on acquisitions", 125.0), ("Fees and expenses", 75.0)]
    m.block(m.sheet("Proceeds"), 1, "Use of proceeds", "($ in millions)", ["Amount"],
            [(k, [v]) for k, v in uses] + [("Total primary proceeds", [sum(v for _, v in uses)])])
    model = m.save(out / "model.xlsx")
    total = sum(v for _, v in uses)
    assert total == 900.0

    f1 = lambda x: f"{x:.1f}x"
    d = Deck()
    d.new_slide("Executive summary")
    d.bullets([
        "Indicative equity value range of $8.5bn - $9.5bn",
        f"Low end implies {f1(r1(ev[0] / 520))} NTM EBITDA and high end implies {f1(r1(ev[1] / 640))} NTM EBITDA",  # first: LTM value labelled NTM
        f"Peer median EV / NTM EBITDA of {f1(r1(med[5]))}; peer median revenue growth of {r1(med[7])}%",
        "Primary proceeds of $900m, of which $450m to repay the term loan",
    ])

    d.new_slide("Trading comparables", "(Market cap and EV in $bn, except per share data)")
    return _finish(d, out, model, med, mean, ev, eq, uses, total, f1)


def _finish(d, out, model, med, mean, ev, eq, uses, total, f1):
    """Second half of build (kept separate to keep table construction readable)."""
    # Clean comps table: columns = price, mcap, EV, EV/Rev NTM, EV/EBITDA LTM, EV/EBITDA NTM, P/E NTM, growth
    hdr = ["", "Share price", "Market cap", "EV", "EV / Rev NTM", "EV / EBITDA LTM", "EV / EBITDA NTM",
           "P / E NTM", "Rev. growth"]
    tbl = [hdr]
    for (name, price, mcap, evv, evrev, ltm, ntm, pe, gr) in PEERS:
        ntm_s = "18.7x" if name.startswith("Dunmore") else f1(ntm)   # ERROR: model 17.8x
        tbl.append([name, f"${price:.2f}", f"{mcap:.1f}", f"{evv:.1f}", f1(evrev), f1(ltm), ntm_s, f1(pe),
                    f"{gr:.1f}%"])
    wrong_med_evrev = "4.8x"                                         # ERROR: model median 4.5x
    # Market cap / EV stats are shown exactly as the model stores them (r1 = half-up, like Excel);
    # Python's f"{x:.1f}" rounds half-to-even and would show 35.2 for a model value of 35.25 -> 35.3.
    tbl.append(["Median", f"${med[0]:.2f}", f"{r1(med[1]):.1f}", f"{r1(med[2]):.1f}", wrong_med_evrev, f1(r1(med[4])),
                f1(r1(med[5])), f1(r1(med[6])), f"{r1(med[7]):.1f}%"])
    tbl.append(["Mean", f"${mean[0]:.2f}", f"{r1(mean[1]):.1f}", f"{r1(mean[2]):.1f}", f1(r1(mean[3])), f1(r1(mean[4])),
                f1(r1(mean[5])), f1(r1(mean[6])), f"{r1(mean[7]):.1f}%"])
    # the table was begun on slide 2 by the caller's deck state
    d.table(tbl, col_width=1.15, first_col=2.2)
    d.footnote("Source: FactSet as of 15-Nov-2024. NTM = next twelve months consensus estimates.")

    d.new_slide("Indicative IPO valuation range", "($ in millions)")
    d.table([
        ["", "Low", "High"],
        ["Equity value", "8,500", "9,500"],
        ["Net debt (pro forma)", "1,050", "1,050"],
        ["Enterprise value", f"{ev[0]:,.0f}", f"{ev[1]:,.0f}"],
        ["Implied EV / Revenue NTM", f1(r1(ev[0] / 2350)), f1(r1(ev[1] / 2350))],
        ["Implied EV / EBITDA LTM", f1(r1(ev[0] / 520)), f1(r1(ev[1] / 520))],
        ["Implied EV / EBITDA NTM", f1(r1(ev[0] / 640)), f1(r1(ev[1] / 640))],
        ["Implied P / E NTM", f1(r1(eq[0] / 380)), f1(r1(eq[1] / 380))],
    ])
    d.footnote("Note: Net debt pro forma for the offering.")

    d.new_slide("Use of proceeds", "($ in millions)")
    d.table([
        ["Use", "Amount"],
        *[[k, f"{v:,.1f}"] for k, v in uses],
        ["Total primary proceeds", "925.0"],                         # ERROR: should be 900.0
    ])

    d.new_slide("Selected peer snapshot", "($ in millions)")
    d.table([
        ["", "Market cap", "Enterprise value"],
        ["Alder Software", "42,300", "46,100"],
        ["Corvane Technologies", "2,540", "27,900"],                 # ERROR: market cap 25,400 ($bn 25.4 -> 2,540)
        ["Dunmore Cloud", "58,900", "61,200"],
    ])
    deck = d.save(out / "deck.pptx")

    ev_lo_ltm = f1(r1(ev[0] / 520))
    return Scenario(
        name="ipo_comps", deck=deck, models=[model],
        description="IPO comps: 10 peers, median/mean rows, valuation range, proceeds, $bn vs $m",
        expects=[
            Expect(1, ("mismatch", "period_ambiguous", "unit_period_mismatch"), ev_lo_ltm,
                   "LTM multiple presented as NTM"),
            Expect(2, ("mismatch",), "18.7x", "Dunmore EV/EBITDA NTM typo"),
            Expect(2, ("mismatch",), "4.8x", "median EV/Rev NTM wrong"),
            Expect(4, ("sum_error", "mismatch"), "925.0", "proceeds do not foot"),
            Expect(5, ("scale_error", "mismatch", "inconsistent_across_slides"), "2,540", "$m/$bn slip"),
        ],
        controls=[
            Control(1, "$8.5bn"), Control(1, "$9.5bn"), Control(1, "$900m"), Control(1, "$450m"),
            Control(1, f1(r1(ev[1] / 640))),
            Control(2, "$112.40"), Control(2, "42.3"), Control(2, "46.1"), Control(2, "16.2x"),
            Control(2, "58.9"), Control(2, "21.7x"), Control(2, "$203.75"),
            Control(2, f1(r1(med[5]))), Control(2, f"{r1(med[7]):.1f}%"),
            Control(2, f1(r1(mean[3]))),
            Control(3, "8,500"), Control(3, "9,500"), Control(3, "9,550"), Control(3, "10,550"),
            Control(3, "1,050"), Control(3, f1(r1(eq[1] / 380))),
            Control(4, "450.0"), Control(4, "250.0"), Control(4, "125.0"), Control(4, "75.0"),
            Control(5, "42,300"), Control(5, "58,900"), Control(5, "61,200"), Control(5, "46,100"),
        ],
        min_verified_pct=70.0,
        max_extra_high=1,
    )
