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
from tests.scenarios.harness import run_scenario

MODULES = sorted(m.name for m in pkgutil.iter_modules([str(Path(pkg.__file__).parent)]) if m.name.startswith("sc_"))


@pytest.mark.parametrize("module", MODULES)
def test_scenario(module, tmp_path):
    mod = importlib.import_module(f"tests.scenarios.{module}")
    sc = mod.build(tmp_path)
    score = run_scenario(sc, tmp_path)
    assert score.ok, score.report()
