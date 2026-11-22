"""Holdout: biotech acquisition deck (pipeline rNPV, SOTP, per share, cash runway)."""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

NEG = '#,##0.0;(#,##0.0)'


def build(out: Path) -> Scenario:
    m = Model()
    ws = m.sheet("Pipeline")
    m.block(ws, 1, "Corvane Biosciences - pipeline rNPV", "($ in millions)",
            ["Phase", "PoS", "Peak sales", "rNPV"], [
        ("CVX-101", ["Phase 3", 0.65, 1200.0, 1010.0]),
        ("CVX-204", ["Phase 2", 0.35, 900.0, 420.0]),
        ("CVX-330", ["Phase 1", 0.15, 1500.0, 180.0]),
        ("CVX-415", ["Preclinical", 0.05, 600.0, 25.0]),
        ("Total pipeline", [None, None, 4200.0, 1635.0]),
    ], fmt={"CVX-101": "#,##0.0", })
    for r in range(7, 12):
        ws.cell(r, 4).number_format = "0%" if r < 11 else "General"
        ws.cell(r, 4).number_format = "0%"
        ws.cell(r, 5).number_format = "#,##0.0"
        ws.cell(r, 6).number_format = "#,##0.0"

    v = m.sheet("SOTP")
    m.block(v, 1, "Sum-of-the-parts valuation", "($ in millions, except per share)", ["Value"], [
        ("Risk-adjusted pipeline NPV", [1635.0]),
        ("Platform and royalty value", [210.0]),
        ("Cash and equivalents", [412.0]),
        ("Debt", [-85.0]),
        ("Equity value", [2172.0]),
        ("Diluted shares (m)", [54.0]),
        ("Value per share", [40.2222]),
        ("Current share price", [31.50]),
        ("Implied premium", [0.2768]),
    ], fmt={"Risk-adjusted pipeline NPV": NEG, "Platform and royalty value": NEG,
            "Cash and equivalents": NEG, "Debt": NEG, "Equity value": NEG,
            "Diluted shares (m)": "#,##0.0", "Value per share": "$0.00",
            "Current share price": "$0.00", "Implied premium": "0.0%"})

    c = m.sheet("Cash")
    m.block(c, 1, "Cash runway", "($ in millions)", ["Q1-25", "Q2-25", "Q3-25"], [
        ("Cash and equivalents", [528.0, 470.0, 412.0]),
        ("Net cash burn", [58.0, 58.0, 58.0]),
        ("Cash runway (months)", [27.3, 24.3, 21.3]),
    ], fmt={"Cash and equivalents": "#,##0.0", "Net cash burn": "#,##0.0",
            "Cash runway (months)": "0.0"})
    model = m.save(out / "model.xlsx")

    d = Deck()
    d.new_slide("Project Helix: transaction overview", "($ in millions, except per share data)")
    d.bullets([
        "Corvane's four-asset pipeline carries a risk-adjusted NPV of $1,635.0m",
        "Sum-of-the-parts equity value of c.$2.2bn, or $40.22 per diluted share",
        "Offer implies a 37.7% premium to the undisturbed share price of $31.50",
        "Cash of $412.0m provides a runway of c.18 months at the current burn rate",
        "Clinical-stage assets carry peak sales potential of $0.9-1.5bn each",
    ], size=14)
    d.new_slide("Pipeline summary and risk-adjusted value", "($ in millions)")
    d.table([
        ["Asset", "Phase", "PoS (%)", "Peak sales", "rNPV"],
        ["CVX-101", "Phase 3", "65%", "1,200.0", "1,010.0"],
        ["CVX-204", "Phase 2", "35%", "900.0", "240.0"],        # ERROR transposed 420
        ["CVX-330", "Phase 1", "25%", "1,500.0", "180.0"],       # ERROR PoS 15%
        ["CVX-415", "Preclinical", "5%", "600.0", "25.0"],
        ["Total pipeline", "", "", "4,200.0", "1,635.0"],
    ], col_width=1.6)
    d.footnote("Source: Company filings, Helix deal team analysis. PoS per BIO/Informa phase transition benchmarks.")
    d.new_slide("Sum-of-the-parts valuation", "($ in millions, except per share data)")
    d.table([
        ["", "Value"],
        ["Risk-adjusted pipeline NPV", "1,635.0"],
        ["Platform and royalty value", "210.0"],
        ["Cash and equivalents", "412.0"],
        ["Debt", "(85.0)"],
        ["Equity value", "2,127.0"],                              # ERROR does not foot / 2,172
        ["Diluted shares (m)", "54.0"],
        ["Value per share", "$40.22"],
        ["Current share price", "$31.50"],
        ["Implied premium", "27.7%"],
    ], col_width=1.6)
    d.new_slide("Cash runway", "($ in millions)")
    d.table([
        ["", "Q1-25", "Q2-25", "Q3-25"],
        ["Cash and equivalents", "528.0", "470.0", "412.0"],
        ["Net cash burn", "58.0", "58.0", "58.0"],
        ["Cash runway (months)", "27.3", "24.3", "21.3"],
    ], col_width=1.4)
    d.footnote("Note: Runway = period-end cash divided by average monthly net burn in the quarter.")
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="holdout_biotech", deck=deck, models=[model],
        expects=[
            Expect(1, ("mismatch", "pct_reconcile", "ratio_reconcile", "growth_reconcile"), "37.7%", "premium"),
            Expect(1, ("mismatch", "unmatched", "ratio_reconcile"), "18", "runway months"),
            Expect(2, ("mismatch", "inconsistent_across_slides"), "240.0", "transposed rNPV"),
            Expect(2, ("mismatch",), "25%", "PoS CVX-330"),
            Expect(3, ("mismatch", "sum_error"), "2,127", "equity value"),
        ],
        controls=[
            Control(1, "1,635.0"), Control(1, "$40.22"), Control(1, "$31.50"), Control(1, "$412.0m"),
            Control(1, "2.2bn"),
            Control(2, "65%"), Control(2, "35%"), Control(2, "5%"), Control(2, "1,200.0"),
            Control(2, "4,200.0"), Control(2, "1,635.0"), Control(2, "1,010.0"), Control(2, "1,500.0"),
            Control(3, "1,635.0"), Control(3, "210.0"), Control(3, "412.0"), Control(3, "(85.0)"),
            Control(3, "54.0"), Control(3, "$40.22"), Control(3, "27.7%"), Control(3, "$31.50"),
            Control(4, "528.0"), Control(4, "470.0"), Control(4, "58.0"), Control(4, "21.3"),
            Control(4, "27.3"), Control(4, "24.3"),
        ],
        min_verified_pct=70.0, max_extra_high=0,
    )
