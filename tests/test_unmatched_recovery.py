"""Regression tests for figures that used to end 'unmatched' although the model backs them."""
from __future__ import annotations

from numcheck.config import Config
from numcheck.demo import ASSET_DIR, DEMO_FILES
from numcheck.extraction.xlsx_structure import _is_pure_banner
from numcheck.matching.normalizer import scan_numbers
from numcheck.runner import run_review
from tests.scenarios.harness import Deck, Model


def test_floating_rate_spreads_are_basis_points():
    assert [(p.value, p.dimension) for p in scan_numbers("S+275")] == [(275.0, "bps")]
    assert [(p.value, p.dimension) for p in scan_numbers("SOFR + 325")] == [(325.0, "bps")]
    assert [p.dimension for p in scan_numbers("growth of +5%")] == ["percent"]
    assert [p.dimension for p in scan_numbers("Revenue +12")] == ["none"]


def test_fiscal_year_clause_keeps_a_units_banner():
    assert _is_pure_banner("($ in millions, fiscal year ending December 31)")
    assert _is_pure_banner("(EUR m; FYE 31-Mar)")
    assert not _is_pure_banner("Revenue for the year ended")


def _review(tmp_path, deck: Deck, model: Model):
    return run_review(deck.save(tmp_path / "deck.pptx"), [model.save(tmp_path / "model.xlsx")],
                      out_dir=str(tmp_path / "out"), annotate=False, config=Config(llm="off"))


def test_prose_and_text_backed_figures_are_verified(tmp_path):
    m = Model()
    ws = m.sheet("Model")
    m.block(ws, 1, "Financials", "($ in millions, fiscal year ending December 31)",
            ["FY23A", "FY24A", "FY25E", "FY26E", "FY27E"], [
                ("Revenue", [900.0, 1000.0, 1080.0, 1150.0, 1225.0]),
                ("Flow Control", [400.0, 450.0, 486.0, 520.0, 550.0]),
                ("Reported EBITDA", [180.0, 200.0, 220.0, 240.0, 260.0]),
                ("Restructuring costs", [3.0, 6.4, 0.0, 0.0, 0.0]),
                ("Adjusted EBITDA", [185.0, 210.0, 225.0, 245.0, 265.0]),
            ])
    cap = m.sheet("Cap")
    cap["A1"], cap["A2"] = "Capitalization", "($ in millions)"
    cap["C4"], cap["D4"] = "FY24A", "Rate"
    for r, (label, amount, rate) in enumerate([("Revolving credit facility ($250m)", 0.0, "S+275"),
                                                ("Term Loan B", 545.0, "S+325"),
                                                ("Total debt", 545.0, "")], start=6):
        cap.cell(r, 1, label)
        cap.cell(r, 3, amount)
        if rate:
            cap.cell(r, 4, rate)
    d = Deck()
    d.new_slide("Overview")
    d.bullets([
        "Restructuring of $6.4m relates to the consolidation of two plants in FY24A",
        "Total debt of $545.0m in FY24A with no maturities before 2029 and an undrawn $250m revolver",
        "Flow Control projected to grow 8.0% in FY25E",
        "Management projects revenue of $1,225m by FY27E, a 7.0% CAGR",
        "FY24A adjustments total $10.0m, or 4.8% of Adjusted EBITDA",
    ])
    d.new_slide("Capitalization", "($ in millions)")
    d.table([["", "Amount", "Pricing"], ["Revolving credit facility ($250m)", "–", "S+275"],
             ["Term Loan B", "545.0", "S+325"]])
    r = _review(tmp_path, d, m)
    status = {(x.deck.slide_number, x.deck.raw_text): x.status for x in r.matches}
    for key in [(1, "$6.4m"), (1, "$545.0m"), (1, "$250m"), (1, "8.0%"), (1, "7.0%"), (1, "$10.0m"), (1, "4.8%"),
                (2, "+275"), (2, "+325")]:
        assert status.get(key) == "matched", (key, status.get(key))
    assert not [i for i in r.issues if i.severity.value == "high"]


def test_demo_deck_only_leaves_figures_the_model_does_not_contain(tmp_path):
    names = {k: v[0] for k, v in DEMO_FILES.items()}
    r = run_review(str(ASSET_DIR / names["deck"]), [str(ASSET_DIR / names["model"])],
                   str(ASSET_DIR / names["model_old"]), out_dir=str(tmp_path / "out"), annotate=False,
                   config=Config(llm="off"))
    open_items = sorted(m.deck.raw_text for m in r.matches if m.status in ("unmatched", "ambiguous"))
    assert open_items == ["40%", "more than 1,400"]          # a customer count and a capacity step-up
    flagged = sorted(i.deck_value for i in r.issues if i.severity.value in ("high", "medium"))
    assert flagged == sorted(["2.8x", "11.6%", "1,056.6", "275.0", "23.6%", "$348.1m", "4.6", "13.9x", "3,740",
                              "2,854", "724.0"])      # the 11 planted errors, nothing else
