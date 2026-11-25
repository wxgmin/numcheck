"""Rules found by running blind, independently built deck/model packs (healthcare CIM, cross-border
merger board deck, leveraged-finance lender deck) through the tool. Each test pins one general behaviour."""
from __future__ import annotations

from numcheck.config import Config
from numcheck.extraction.chart_extractor import merge_unit_hints
from numcheck.extraction.pptx_extractor import _is_dependent_row
from numcheck.extraction.xlsx_structure import _is_attribute_cell, _is_block_title
from numcheck.matching.normalizer import is_unit_price_label, parse_unit_banner
from numcheck.periods import find_periods, period_affinity, period_key
from numcheck.runner import run_review
from tests.scenarios.harness import Deck, Model


def test_except_clause_names_an_exception_not_the_unit():
    h = parse_unit_banner("($ in millions, except multiples)")
    assert (h.dimension, h.scale) == ("currency", "m")
    assert parse_unit_banner("Run-rate").dimension != "percent"      # an annualised amount, not a rate


def test_periods_in_real_headers():
    assert period_key("Dec-22A") == "fy22" and period_key("Jun-25E") == "fy25"   # period-end actual / estimate
    assert period_key("Dec-22") == "y22mo12"                                    # a plain monthly column
    assert period_key("LTM Sep-25A") == "ltm"
    assert period_key("fiscal year ending June 30") == ""                       # a day, not June 2030
    assert period_key("Capitalization at March 31, 2025") == "y25mo03"
    assert [p.key for p, _s, _e in find_periods("CAGR FY22A-24A")] == ["fy22", "fy24"]
    assert [p.key for p, _s, _e in find_periods("FY2024-25")] == ["fy24"]        # one UK / Indian fiscal year
    assert period_affinity("y25mo03", "fy25q3") == 0.0                         # FYE unknown: never guessed


def test_counts_and_prices_keep_their_own_units_on_a_millions_slide():
    slide = parse_unit_banner("($ in millions)")
    visits = merge_unit_hints([parse_unit_banner("Visits (000s)"), slide])
    clinics = merge_unit_hints([parse_unit_banner("Clinics"), slide])
    assert (visits.dimension, visits.scale) == ("count", "k")
    assert (clinics.dimension, clinics.scale) == ("count", "")
    assert is_unit_price_label("Revenue / visit ($)") and is_unit_price_label("Castellane 52-week high (EUR)")
    assert not is_unit_price_label("Equity purchase price")


def test_table_structure_helpers():
    assert _is_dependent_row("% margin", "  % margin") and _is_dependent_row("% of total", "% of total")
    assert not _is_dependent_row("EBITDA margin", "EBITDA margin")
    assert _is_attribute_cell("Pricing", "SOFR + 350") and _is_attribute_cell("Rating", "B2")
    assert _is_block_title("Brenner Industrietechnik AG") and not _is_block_title("Fiscal year")


def _review(tmp_path, deck: Deck, model: Model):
    return run_review(deck.save(tmp_path / "deck.pptx"), [model.save(tmp_path / "model.xlsx")],
                      out_dir=str(tmp_path / "out"), annotate=False, config=Config(llm="off"))


def test_blind_pack_patterns(tmp_path):
    m = Model()
    ws = m.sheet("IS")
    m.block(ws, 1, "Income statement", "($ in millions, except multiples)", ["FY22A", "FY23A", "FY24A"], [
        ("Revenue", [612.3, 701.8, 794.2]),
        ("Gross profit", [192.2, 223.3, 258.0]),
        ("  % margin", [0.314, 0.318, 0.325]),
        ("Adjusted EBITDA", [109.8, 127.8, 150.6]),
        ("  % margin", [0.1794, 0.1821, 0.1897]),
    ], fmt={"  % margin": "0.0%"})
    cap = m.sheet("Cap")
    cap["A1"], cap["A2"] = "Capitalization", "($ in millions)"
    for c, h in enumerate(["Tranche", "Amount", "Pricing", "Maturity", "x EBITDA"], start=1):
        cap.cell(4, c, h)
    for r, row in enumerate([("Term Loan B", 1000.0, "SOFR + 350", 2032, 3.6),
                             ("Senior Notes", 500.0, "Fixed", 2033, 5.3),
                             ("Total debt", 1500.0, None, None, None)], start=5):
        for c, v in enumerate(row, start=1):
            if v is not None:
                cap.cell(r, c, v)
        cap.cell(r, 5).number_format = '0.0"x"'
    d = Deck()
    d.new_slide("Financial summary", "($ in millions)")
    d.table([["", "FY22A", "FY23A", "FY24A"], ["Revenue", "612.3", "701.8", "794.2"],
             ["Adj. EBITDA", "109.8", "127.8", "150.6"], ["  % margin", "17.9%", "18.2%", "19.0%"]])
    d.bullets(["Adj. EBITDA increased to $150.6m in FY24A; margin expanded 76bps from 18.2% to 19.0%"])
    d.new_slide("Capitalization", "($ in millions)")
    d.table([["Tranche", "Amount", "% of debt"], ["Term Loan B", "1,000.0", "66.7%"],
             ["Senior Notes", "500.0", "33.3%"], ["Total debt", "1,500.0", "100.0%"]])
    r = _review(tmp_path, d, m)
    found = [(x.deck.slide_number, x.deck.raw_text, x) for x in r.matches]
    for key in [(1, "17.9%"), (1, "18.2%"), (1, "19.0%"), (1, "76bps"), (2, "66.7%"), (2, "33.3%"), (2, "100.0%")]:
        hits = [x for s, raw, x in found if (s, raw) == key]
        assert hits and all(x.status == "matched" for x in hits), (key, [(x.status, x.note) for x in hits])
    for s, raw, x in found:
        if raw in ("17.9%", "19.0%"):   # the Adj. EBITDA margin row, never the gross margin row above it
            assert "Adjusted EBITDA" in (x.model.section_label or ""), (raw, x.model.ref, x.model.section_label)
    assert not [i for i in r.issues if i.severity.value == "high"]
