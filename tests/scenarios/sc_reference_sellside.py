"""Reference scenario: sell-side "company overview & financial summary" pitch.

Model: banker layout (title, "($ in millions)" banner, blank row, numeric year
headers 2022/2023/2024 + "2025E", spacer column) plus a valuation sheet.
Deck: exec-summary prose, a financial summary table with units in the slide
subtitle, a revenue chart with no number format, and a valuation table.
"""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario


def build(out: Path) -> Scenario:
    m = Model()
    ws = m.sheet("IS")
    m.block(ws, 1, "Income Statement", "($ in millions)", [2022, 2023, 2024, "2025E"], [
        ("Revenue", [1020.4, 1110.8, 1234.6, 1345.0]),
        ("Gross profit", [408.2, 455.4, 518.5, 572.0]),
        ("EBITDA", [204.1, 233.3, 271.6, 302.6]),
        # openpyxl stores no cached results: NumCheck's built-in formula fallback computes these
        ("EBITDA margin", ["=C8/C6", "=D8/D6", "=E8/E6", "=F8/F6"]),
        ("Net income", [98.0, 112.5, 131.9, 150.2]),
    ], fmt={"EBITDA margin": "0.0%"})
    val = m.sheet("Valuation")
    m.block(val, 1, "Valuation summary", "($ in millions, except per share)", ["Low", "Mid", "High"], [
        ("Enterprise value", [2650.0, 2850.0, 3050.0]),
        ("EV / LTM EBITDA", [9.8, 10.5, 11.2]),
        ("Implied share price", [24.10, 26.35, 28.60]),
    ], fmt={"EV / LTM EBITDA": '0.0"x"', "Implied share price": "$0.00"})
    model = m.save(out / "model.xlsx")

    d = Deck()
    d.new_slide("Executive summary")
    d.bullets([
        "Revenue grew 11% to $1.2bn in 2024, with EBITDA of $272m",          # correct (1234.6/1110.8-1 = 11.1%)
        "EBITDA margin expanded to 23.0% in 2024",                            # ERROR: model 22.0%
        "Net income of $131.9m in 2024",                                      # correct
    ])
    d.new_slide("Financial summary", "($ in millions)")
    d.table([
        ["", "2022A", "2023A", "2024A", "2025E"],
        ["Revenue", "1,020.4", "1,110.8", "1,234.6", "1,345.0"],
        ["Gross profit", "408.2", "455.4", "518.5", "572.0"],
        ["EBITDA", "204.1", "233.3", "217.6", "302.6"],                     # ERROR: 217.6 transposed digits
        ["EBITDA margin", "20.0%", "21.0%", "22.0%", "22.5%"],
    ])
    d.new_slide("Revenue trajectory ($m)")
    d.chart("column", ["2022A", "2023A", "2024A", "2025E"], {"Revenue": [1020, 1111, 1235, 1345]})
    d.new_slide("Valuation summary", "($ in millions, except per share data)")
    d.table([
        ["", "Low", "Mid", "High"],
        ["Enterprise value", "2,650", "2,850", "3,050"],
        ["EV / LTM EBITDA", "9.8x", "10.5x", "11.2x"],
        ["Implied share price", "$24.10", "$26.35", "$28.06"],              # ERROR: 28.06 vs 28.60
    ])
    d.footnote("Source: Company filings, management projections. Market data as of 30-Sep-2024.")
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="reference_sellside", deck=deck, models=[model],
        expects=[
            Expect(1, ("mismatch",), "23.0%", "margin overstated"),
            Expect(2, ("mismatch",), "217.6", "transposed digits"),
            Expect(4, ("mismatch",), "$28.06", "per-share high case"),
        ],
        controls=[
            Control(1, "11%"), Control(1, "$1.2bn"), Control(1, "$272m"), Control(1, "$131.9m"),
            Control(2, "1,234.6"), Control(2, "22.0%"), Control(2, "2025"),
            Control(3, "1235"), Control(3, "1111"),
            Control(4, "2,850"), Control(4, "10.5x"), Control(4, "$26.35"),
        ],
        min_verified_pct=75.0,
        max_extra_high=1,
    )
