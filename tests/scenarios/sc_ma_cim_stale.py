"""M&A scenario: sell-side CIM / teaser after a model refresh (Project Falcon, FYE Dec).

The model was refreshed after Q3 actuals: Q4 FY24E / FY2024E / FY2025E were cut. model_old is
the pre-refresh version. The deck mixes refreshed and stale numbers and has a hidden appendix.

Planted errors:
  stale (3):  s1 FY2024E revenue $1,248.0m; s2 FY2024E Adj. EBITDA 258.0; s5 (hidden) FY2025E revenue 1,380.0
  s3  Q2 FY24A revenue 310.2 (model 301.2): quarters no longer sum to FY2024E
  s1  LTM Sep-24 Adj. EBITDA ($240.1m) labelled as FY2024
  s4  segment total FY2024E 1,236.5 does not foot (segments sum to 1,226.5)
"""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

PCT = "0.0%"


def _model(path: Path, old: bool) -> str:
    m = Model()
    # ---- Quarterly + LTM --------------------------------------------------
    q4_rev, q4_ebitda = (343.6, 72.8) if old else (322.1, 68.3)
    rev = [290.4, 301.2, 312.8, q4_rev]
    ebitda = [58.1, 61.7, 65.4, q4_ebitda]
    fy_rev, fy_e = round(sum(rev), 1), round(sum(ebitda), 1)
    ltm_rev, ltm_e = round(281.0 + sum(rev[:3]), 1), round(54.9 + sum(ebitda[:3]), 1)
    qs = m.sheet("Quarterly")
    m.block(qs, 1, "Quarterly financials", "($ in millions)",
            ["Q1 FY24A", "Q2 FY24A", "Q3 FY24A", "Q4 FY24E", "FY2024E", "LTM Sep-24"], [
                ("Revenue", rev + [fy_rev, ltm_rev]),
                ("Adj. EBITDA", ebitda + [fy_e, ltm_e]),
                ("Adj. EBITDA margin", [round(e / r, 5) for e, r in zip(ebitda + [fy_e, ltm_e], rev + [fy_rev, ltm_rev])]),
            ], fmt={"Adj. EBITDA margin": PCT})

    # ---- Annual -----------------------------------------------------------
    r25, e25 = (1380.0, 300.0) if old else (1341.0, 288.0)
    arev = [1052.0, 1141.6, fy_rev, r25, 1463.0]
    aeb = [196.0, 223.4, fy_e, e25, 322.0]
    an = m.sheet("Annual")
    m.block(an, 1, "Annual summary financials", "($ in millions)",
            ["FY2022A", "FY2023A", "FY2024E", "FY2025E", "FY2026E"], [
                ("Revenue", arev), ("Adj. EBITDA", aeb),
                ("Adj. EBITDA margin", [round(e / r, 5) for e, r in zip(aeb, arev)]),
            ], fmt={"Adj. EBITDA margin": PCT})

    # ---- Segments (foot to Annual revenue) --------------------------------
    seg = m.sheet("Segments")
    fy24 = [612.3, 398.9, round(fy_rev - 612.3 - 398.9, 1)] if old else [612.3, 398.9, 215.3]
    m.block(seg, 1, "Revenue by segment", "($ in millions)", ["FY2022A", "FY2023A", "FY2024E"], [
        ("Industrial", [531.0, 574.5, fy24[0]]),
        ("Commercial", [345.8, 372.4, fy24[1]]),
        ("Services", [175.2, 194.7, fy24[2]]),
        ("Total revenue", [1052.0, 1141.6, round(sum(fy24), 1)]),
    ])
    return m.save(path), dict(fy_rev=fy_rev, fy_e=fy_e, ltm_rev=ltm_rev, ltm_e=ltm_e)


def build(out: Path) -> Scenario:
    model, v = _model(out / "model.xlsx", old=False)
    model_old, _ = _model(out / "model_old.xlsx", old=True)
    assert v["fy_rev"] == 1226.5 and v["fy_e"] == 253.5 and v["ltm_rev"] == 1185.4 and v["ltm_e"] == 240.1

    d = Deck()
    d.new_slide("Project Falcon: Investment highlights", "Confidential Information Memorandum")
    d.bullets([
        "FY2024E revenue of $1,248.0m across three diversified segments",                  # STALE (1,226.5)
        "FY2024 Adj. EBITDA of $240.1m reflecting continued operating leverage",           # ERROR: LTM labelled FY
        "LTM Sep-24 revenue of $1,185.4m",
        "FY2026E revenue of $1,463.0m, a 2022A–2026E trajectory supported by backlog",
    ])
    d.new_slide("Summary financials", "($ in millions)")
    d.table([
        ["", "FY2022A", "FY2023A", "FY2024E", "FY2025E", "FY2026E"],
        ["Revenue", "1,052.0", "1,141.6", "1,226.5", "1,341.0", "1,463.0"],
        ["Adj. EBITDA", "196.0", "223.4", "258.0", "288.0", "322.0"],                      # STALE (253.5)
        ["Adj. EBITDA margin", "18.6%", "19.6%", "20.7%", "21.5%", "22.0%"],
    ])
    d.footnote("Source: Company management; FY2024E reflects nine months of actuals and Q4 forecast.")

    d.new_slide("Quarterly performance", "($ in millions)")
    d.table([
        ["", "Q1 FY24A", "Q2 FY24A", "Q3 FY24A", "Q4 FY24E", "FY2024E", "LTM Sep-24"],
        ["Revenue", "290.4", "310.2", "312.8", "322.1", "1,226.5", "1,185.4"],              # ERROR Q2 (301.2)
        ["Adj. EBITDA", "58.1", "61.7", "65.4", "68.3", "253.5", "240.1"],
        ["Adj. EBITDA margin", "20.0%", "20.5%", "20.9%", "21.2%", "20.7%", "20.3%"],
    ], col_width=1.3, first_col=2.6)

    d.new_slide("Revenue by segment", "($ in millions)")
    d.table([
        ["", "FY2022A", "FY2023A", "FY2024E"],
        ["Industrial", "531.0", "574.5", "612.3"],
        ["Commercial", "345.8", "372.4", "398.9"],
        ["Services", "175.2", "194.7", "215.3"],
        ["Total revenue", "1,052.0", "1,141.6", "1,236.5"],                                 # ERROR does not foot
    ])

    d.new_slide("Appendix: management projections", "($ in millions)", hidden=True)
    d.table([
        ["", "FY2025E", "FY2026E"],
        ["Revenue", "1,380.0", "1,463.0"],                                                  # STALE (1,341.0)
        ["Adj. EBITDA", "288.0", "322.0"],
    ])
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="ma_cim_stale", deck=deck, models=[model], model_old=model_old,
        description="Sell-side CIM after a model refresh, with hidden appendix, LTM vs FY and segment footing",
        expects=[
            Expect(1, ("stale",), "1,248.0", "stale FY2024E revenue (prose)"),
            Expect(2, ("stale",), "258.0", "stale FY2024E EBITDA"),
            Expect(5, ("stale",), "1,380.0", "stale FY2025E revenue in hidden appendix"),
            Expect(3, ("mismatch", "sum_error"), "310.2", "Q2 does not sum to FY"),
            Expect(1, ("mismatch", "period_ambiguous", "unit_period_mismatch"), "240.1",
                   "LTM EBITDA labelled FY2024"),
            Expect(4, ("sum_error", "mismatch"), "1,236.5", "segment total does not foot"),
        ],
        controls=[
            Control(1, "$1,185.4m"), Control(1, "$1,463.0m"),
            Control(2, "1,052.0"), Control(2, "1,141.6"), Control(2, "1,226.5"), Control(2, "1,341.0"),
            Control(2, "196.0"), Control(2, "223.4"), Control(2, "288.0"), Control(2, "322.0"),
            Control(2, "20.7%"), Control(2, "18.6%"), Control(2, "19.6%"), Control(2, "21.5%"), Control(2, "22.0%"),
            Control(3, "290.4"), Control(3, "312.8"), Control(3, "322.1"), Control(3, "1,226.5"),
            Control(3, "1,185.4"), Control(3, "58.1"), Control(3, "61.7"), Control(3, "65.4"), Control(3, "68.3"),
            Control(3, "253.5"), Control(3, "240.1"), Control(3, "20.3%"), Control(3, "20.9%"),
            Control(4, "531.0"), Control(4, "574.5"), Control(4, "612.3"), Control(4, "345.8"),
            Control(4, "372.4"), Control(4, "398.9"), Control(4, "175.2"), Control(4, "194.7"),
            Control(4, "215.3"), Control(4, "1,052.0"), Control(4, "1,141.6"),
            Control(5, "1,463.0"), Control(5, "288.0"), Control(5, "322.0"),
        ],
        min_verified_pct=60.0, max_extra_high=1,
    )
