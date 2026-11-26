"""FIG scenario: regional bank quarterly earnings-style deck, 1Q24-4Q24.

Model: Quarterly sheet 4Q23..4Q24 ($ in millions, except per share; loans/deposits
in $bn; ratios in %; NCO in bps). model_old has the pre-update CET1 for 4Q24.
Deck: highlights, quarterly trend table, YoY / QoQ changes in bps, balance sheet chart.
"""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

Q = ["4Q23", "1Q24", "2Q24", "3Q24", "4Q24"]
NII = [3420, 3455, 3498, 3520, 3561]
NONII = [1310, 1345, 1402, 1388, 1430]
REV = [a + b for a, b in zip(NII, NONII)]
EXP = [2980, 3010, 3035, 3028, 3052]
PROV = [295, 318, 340, 352, 371]
NI = [810, 842, 880, 901, 925]
NIM = [0.0270, 0.0268, 0.0271, 0.0272, 0.0274]
CET1 = [0.114, 0.115, 0.116, 0.118, 0.119]
CET1_OLD_4Q24 = 0.117
ROTCE = [0.148, 0.152, 0.157, 0.159, 0.161]
EFF = [0.630, 0.627, 0.619, 0.617, 0.612]
LOANS = [312.4, 315.1, 318.6, 321.0, 324.2]
DEPS = [398.5, 401.2, 404.9, 407.3, 411.0]
TBVPS = [28.42, 29.05, 29.71, 30.38, 31.02]
NCO = [38, 41, 44, 46, 49]


def _model(path: Path, cet1_last: float) -> str:
    m = Model()
    ws = m.sheet("Quarterly")
    cet1 = CET1[:-1] + [cet1_last]
    m.block(ws, 1, "Quarterly financial summary", "($ in millions, except per share data)", Q, [
        ("Net interest income", NII),
        ("Noninterest income", NONII),
        ("Total revenue", REV),
        ("Noninterest expense", EXP),
        ("Provision for credit losses", PROV),
        ("Net income", NI),
        ("Net interest margin", NIM),
        ("Efficiency ratio", EFF),
        ("ROTCE", ROTCE),
        ("CET1 ratio", cet1),
        ("Tangible book value per share ($)", TBVPS),
        ("Net charge-offs (bps)", NCO),
    ], fmt={"Net interest margin": "0.00%", "Efficiency ratio": "0.0%", "ROTCE": "0.0%",
            "CET1 ratio": "0.0%", "Tangible book value per share ($)": "$0.00"})
    m.block(m.sheet("Balance sheet"), 1, "Balance sheet", "($ in billions)", Q, [
        ("Total loans", LOANS), ("Total deposits", DEPS),
    ], fmt={"Total loans": "0.0", "Total deposits": "0.0"})
    return m.save(path)


def build(out: Path) -> Scenario:
    model = _model(out / "model.xlsx", CET1[-1])
    old = _model(out / "model_old.xlsx", CET1_OLD_4Q24)

    p = lambda x, d=1: f"{x * 100:.{d}f}%"
    d = Deck()
    d.new_slide("4Q24 earnings highlights", "($ in millions, except per share data)")
    d.bullets([
        f"Net income of $925m and total revenue of ${REV[-1]:,}m in 4Q24",
        "Net interest margin of 277 bps in 4Q24, up 4 bps year-over-year",             # ERROR: model 2.74% = 274 bps
        "CET1 ratio of 11.7% at quarter-end",                                          # ERROR: stale (old model 11.7%, now 11.9%)
        "Tangible book value per share of $31.0bn",                                    # ERROR: scale ($31.02)
        f"ROTCE of {p(ROTCE[-1])} and efficiency ratio of {p(EFF[-1])}",
        "Total loans of $324.2bn and total deposits of $411.0bn",
    ])

    d.new_slide("Quarterly trend", "($ in millions, except per share data; loans and deposits in $bn)")
    qs = Q[1:]
    d.table([
        [""] + [f"{q}" for q in qs],
        ["Net interest income"] + [f"{v:,}" for v in NII[1:]],
        ["Noninterest income"] + [f"{v:,}" for v in NONII[1:]],
        ["Total revenue"] + [f"{v:,}" for v in REV[1:]],
        ["Noninterest expense"] + [f"{v:,}" for v in EXP[1:]],
        ["Provision for credit losses"] + [f"{v:,}" for v in PROV[1:]],
        ["Net income"] + [f"{v:,}" for v in NI[1:]],
        ["Net interest margin"] + [p(v, 2) for v in NIM[1:]],
        ["Efficiency ratio"] + [p(v) for v in EFF[1:]],
        ["ROTCE"] + [p(v) for v in ROTCE[1:]],
        ["CET1 ratio"] + [p(v) for v in CET1[1:-1]] + ["11.9%"],
        ["Tangible book value per share ($)"] + [f"${v:.2f}" for v in TBVPS[1:]],
        ["Net charge-offs (bps)"] + [f"{v}" for v in NCO[1:]],
    ], col_width=1.3, first_col=3.4)

    d.new_slide("Key ratios: year-over-year and quarter-over-quarter change")
    d.table([
        ["", "4Q24", "3Q24", "4Q23", "QoQ change", "YoY change"],
        ["Net interest margin", "2.74%", "2.72%", "2.70%", "+5 bps", "+4 bps"],      # ERROR: QoQ is +2 bps
        ["CET1 ratio", "11.9%", "11.8%", "11.4%", "+10 bps", "+50 bps"],
        ["Net charge-offs (bps)", "49", "46", "38", "+3 bps", "+11 bps"],
        ["ROTCE", "16.1%", "15.9%", "14.8%", "+20 bps", "+130 bps"],
    ], col_width=1.4, first_col=3.0)
    d.footnote("Note: ROTCE annualized. Net charge-offs as a percentage of average loans, in basis points.")

    d.new_slide("Balance sheet growth", "($ in billions)")
    d.chart("column", qs, {"Total loans": LOANS[1:], "Total deposits": DEPS[1:]}, number_format="0.0")
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="fig_bank", deck=deck, models=[model], model_old=old,
        description="Bank earnings deck: NIM bps/%, CET1 stale, TBVPS scale, QoQ bps reconcile",
        expects=[
            Expect(1, ("mismatch", "unit_period_mismatch", "pct_reconcile", "scale_error"), "277", "NIM bps vs 2.74%"),
            Expect(1, ("stale", "mismatch"), "11.7%", "CET1 stale vs model_old"),
            Expect(1, ("scale_error", "mismatch"), "31.0", "TBVPS $31.02 shown as $31.0bn"),
            Expect(3, ("growth_reconcile", "pct_reconcile", "ratio_reconcile", "mismatch", "inconsistent_across_slides"),
                   "+5 bps", "QoQ NIM change should be +2 bps"),
        ],
        controls=[
            Control(1, "$925m"), Control(1, "4,991"), Control(1, "4 bps"), Control(1, "16.1%"),
            Control(1, "61.2%"), Control(1, "$324.2bn"), Control(1, "$411.0bn"),
            Control(2, "3,561"), Control(2, "1,430"), Control(2, "4,991"), Control(2, "3,052"),
            Control(2, "371"), Control(2, "2.74%"), Control(2, "2.68%"), Control(2, "61.2%"),
            Control(2, "16.1%"), Control(2, "11.9%"), Control(2, "$31.02"), Control(2, "$29.05"),
            Control(3, "2.72%"), Control(3, "11.4%"), Control(3, "+10 bps"), Control(3, "+50 bps"),
            Control(3, "+3 bps"), Control(3, "+11 bps"), Control(3, "+20 bps"), Control(3, "+130 bps"),
            Control(3, "+4 bps"),
            Control(4, "324.2"), Control(4, "411.0"),
        ],
        min_verified_pct=70.0,
        max_extra_high=1,
    )
