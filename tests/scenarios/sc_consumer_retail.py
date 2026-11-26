"""Consumer / retail investor presentation scenario.

Model: segment revenue FY2022-FY2024 ($m), mix %, store count, same-store sales by
segment, gross margin, e-commerce. Deck: strategic overview, mix pie + "% of total"
table, stacked segment chart, store / SSS table, margin and e-commerce slide.
"""

from __future__ import annotations

from pathlib import Path

from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

FY = ["FY2022A", "FY2023A", "FY2024A"]
SEG = {
    "Apparel": [2150, 2230, 2310],
    "Footwear": [1020, 1085, 1160],
    "Home & Lifestyle": [640, 655, 670],
    "Beauty": [390, 430, 480],
}
TOTAL = [sum(v[i] for v in SEG.values()) for i in range(3)]       # 4,200 / 4,400 / 4,620
MIX24 = {"Apparel": 50.0, "Footwear": 25.1, "Home & Lifestyle": 14.5, "Beauty": 10.4}
assert TOTAL == [4200, 4400, 4620] and abs(sum(MIX24.values()) - 100) < 1e-9
STORES = [1198, 1223, 1248]
SSS_HOME = [0.015, -0.008, -0.032]
SSS_TOTAL = [0.041, 0.026, 0.019]
GM = [0.384, 0.391, 0.403]
GP = [1612.8, 1720.4, 1861.9]
ECOM_PCT = [0.180, 0.205, 0.231]
ECOM = [756.0, 902.0, 1067.2]


def build(out: Path) -> Scenario:
    m = Model()
    ws = m.sheet("Segments")
    rows = [(k, v) for k, v in SEG.items()] + [("Total net sales", TOTAL),
                                                ("Gross profit", GP), ("Gross margin", GM),
                                                ("E-commerce sales", ECOM),
                                                ("E-commerce penetration", ECOM_PCT)]
    m.block(ws, 1, "Net sales by segment", "($ in millions)", FY, rows,
            fmt={"Gross margin": "0.0%", "E-commerce penetration": "0.0%"})
    mix = m.sheet("Mix")
    m.block(mix, 1, "Revenue mix FY2024", "(% of total net sales)", ["FY2024A"],
            [(k, [v / 100]) for k, v in MIX24.items()], fmt={k: "0.0%" for k in MIX24})
    ops = m.sheet("Operating KPIs")
    m.block(ops, 1, "Store base and comparable sales", "(# of stores; % growth)", FY, [
        ("Store count", [float(s) for s in STORES]),
        ("Same-store sales growth - Total", SSS_TOTAL),
        ("Same-store sales growth - Home & Lifestyle", SSS_HOME),
    ], fmt={"Store count": "#,##0", "Same-store sales growth - Total": "0.0%",
            "Same-store sales growth - Home & Lifestyle": "0.0%"})
    model = m.save(out / "model.xlsx")

    d = Deck()
    d.new_slide("Investor presentation: highlights")
    d.bullets([
        "Net sales of $4.6bn in FY2024, up 5.0% year-over-year",
        "Gross margin expanded 150bps to 40.3%",                                         # ERROR: 40.3 - 39.1 = +120bps
        "Operated 1,248 stores at fiscal year-end; same-store sales grew 1.9%",
        "Home & Lifestyle same-store sales declined 3.2%",
        "E-commerce penetration reached 23.1% of net sales",
    ])

    d.new_slide("Net sales mix by segment", "(FY2024, % of total net sales)")
    d.chart("pie", list(MIX24), {"FY2024A": list(MIX24.values())}, number_format='0.0"%"', height=3.0)
    d.table([
        ["Segment", "FY2024A net sales ($m)", "% of total"],
        ["Apparel", "2,310", "50.0%"],
        ["Footwear", "1,160", "25.1%"],
        ["Home & Lifestyle", "670", "14.5%"],
        ["Beauty", "480", "12.4%"],                                                       # ERROR: 10.4% -> mix sums to 102.0%
        ["Total", "4,620", "100.0%"],
    ], col_width=2.2, first_col=2.6)

    d.new_slide("Net sales by segment", "($ in millions)")
    d.chart("stacked", FY, {
        "Apparel": SEG["Apparel"], "Footwear": SEG["Footwear"],
        "Home & Lifestyle": [640, 565, 670],                                              # ERROR: FY2023 655 -> 565
        "Beauty": SEG["Beauty"],
    }, number_format="#,##0", height=4.2)

    d.new_slide("Store base and same-store sales")
    d.table([
        ["", "FY2022A", "FY2023A", "FY2024A"],
        ["Store count (#)", "1,198", "1,223", "1,248"],
        ["Same-store sales growth - Total", "4.1%", "2.6%", "1.9%"],
        ["Same-store sales growth - Home & Lifestyle", "1.5%", "0.8%", "(3.2)%"],        # ERROR: FY2023 is (0.8)%
    ], col_width=1.5, first_col=4.2)

    d.new_slide("Margin expansion and e-commerce", "($ in millions)")
    d.table([
        ["", "FY2022A", "FY2023A", "FY2024A"],
        ["Total net sales", "4,200", "4,400", "4,620"],
        ["Gross profit", "1,612.8", "1,720.4", "1,861.9"],
        ["Gross margin", "38.4%", "39.1%", "40.3%"],
        ["Change in gross margin (bps)", "n.a.", "+70 bps", "+120 bps"],
        ["E-commerce sales", "756.0", "902.0", "1,067.2"],
        ["E-commerce penetration", "18.0%", "20.5%", "23.1%"],
    ], col_width=1.5, first_col=3.6)
    d.footnote("Source: Company filings. Fiscal year ends on the Saturday closest to 31 January.")
    deck = d.save(out / "deck.pptx")

    return Scenario(
        name="consumer_retail", deck=deck, models=[model],
        description="Retailer deck: mix %, SSS sign wording, stacked chart, bps margin expansion",
        expects=[
            Expect(1, ("mismatch", "ratio_reconcile", "pct_reconcile", "growth_reconcile"), "150",
                   "gross margin expansion bps vs 39.1% -> 40.3%"),
            Expect(2, ("sum_error", "mismatch", "pct_reconcile"), "12.4%", "Beauty mix wrong; mix sums to 102%"),
            Expect(3, ("mismatch", "sum_error"), "565", "stacked chart Home FY2023"),
            Expect(4, ("sign_error", "mismatch"), "0.8%", "FY2023 Home SSS sign"),
        ],
        controls=[
            Control(1, "$4.6bn"), Control(1, "5.0%"), Control(1, "1,248"), Control(1, "1.9%"),
            Control(1, "3.2%"), Control(1, "40.3%"), Control(1, "23.1%"),
            Control(2, "2,310"), Control(2, "1,160"), Control(2, "670"), Control(2, "480"),
            Control(2, "4,620"), Control(2, "50.0%"), Control(2, "25.1%"), Control(2, "14.5%"),
            Control(3, "2,230"), Control(3, "1,085"), Control(3, "2,150"), Control(3, "1,020"),
            Control(3, "390"), Control(3, "430"),
            Control(4, "1,198"), Control(4, "1,223"), Control(4, "1,248"), Control(4, "4.1%"),
            Control(4, "2.6%"), Control(4, "1.5%"), Control(4, "(3.2)%"),
            Control(5, "1,861.9"), Control(5, "39.1%"), Control(5, "38.4%"), Control(5, "+70 bps"),
            Control(5, "+120 bps"), Control(5, "1,067.2"), Control(5, "20.5%"), Control(5, "18.0%"),
        ],
        min_verified_pct=70.0,
        max_extra_high=1,
    )
