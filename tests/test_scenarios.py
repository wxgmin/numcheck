"""Run every scenario module in tests/scenarios/ (files named sc_*.py).

Each scenario's ``build(out_dir)`` returns a Scenario; the harness scores misses,
false positives, unexpected HIGH issues and coverage. Run one scenario's report:

    python -m tests.scenarios.run sc_lbo
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import tests.scenarios as pkg
from numcheck.config import Config
from numcheck.runner import run_review
from tests.scenarios.harness import Deck, Model, run_scenario

MODULES = sorted(m.name for m in pkgutil.iter_modules([str(Path(pkg.__file__).parent)]) if m.name.startswith("sc_"))


@pytest.mark.parametrize("module", MODULES)
def test_scenario(module, tmp_path):
    mod = importlib.import_module(f"tests.scenarios.{module}")
    sc = mod.build(tmp_path)
    score = run_scenario(sc, tmp_path)
    assert score.ok, score.report()


@pytest.mark.parametrize("source_search", [False, True])
@pytest.mark.parametrize(
    ("label", "row", "headers", "section", "expected"),
    [
        ("FY24 Revenue", "Revenue", ("FY24 Low", "FY24 High"), "Operating metrics", True),
        ("FY24 Revenue", "Subscription Revenue", ("FY24 Low", "FY24 High"), "Operating metrics", False),
        ("FY24A Revenue", "Revenue", ("FY24E Low", "FY24E High"), "Operating metrics", False),
        ("Revenue", "Revenue", ("FY24 Low", "FY25 High"), "Operating metrics", False),
        ("FY24 Revenue", "Revenue", ("FY24A Low", "FY24E High"), "Operating metrics", False),
        ("Revenue", "Revenue", ("Actual Low", "Forecast High"), "Operating metrics", False),
        ("FY24 Revenue", "Revenue Growth", ("FY24 Low", "FY24 High"), "Operating metrics", False),
        ("FY24 Revenue", "Revenue", ("FY24 Low", "FY24 High"), "Subscription segment", False),
        ("Subscription\nFY24 Revenue", "Subscription Revenue", ("FY24 Low", "FY24 High"),
         "Operating metrics", True),
    ],
)
def test_range_verification_checks_source_identity(tmp_path, source_search, label, row, headers, section, expected):
    deck = Deck()
    deck.new_slide("Operating metrics")
    deck.text(f"{label} of $100m–$120m")
    model = Model()
    sheet = model.sheet("Model")
    model.block(sheet, 1, section, "($ in millions)", list(headers),
                [(row, [100, 120]), ("Expenses", [75, 80]), ("EBITDA", [25, 40])])
    result = run_review(
        deck.save(tmp_path / "deck.pptx"), [model.save(tmp_path / "model.xlsx")],
        out_dir=str(tmp_path / "report"), annotate=False, config=Config(llm="off"),
        source_search=source_search,
    )
    matches = [m for m in result.matches if m.deck.is_range]
    assert len(matches) == 2
    if expected:
        assert [m.status for m in matches] == ["matched", "matched"]
        assert [m.model.ref for m in matches] == ["[model.xlsx] Model!C6", "[model.xlsx] Model!D6"]
    else:
        assert all(m.status in {"unmatched", "ambiguous"} and m.model is None for m in matches)
        if row != "Revenue Growth":
            assert all(m.source_options for m in matches)
