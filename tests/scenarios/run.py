"""Print scenario reports: python -m tests.scenarios.run [sc_name ...]"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import tempfile
from pathlib import Path

import tests.scenarios as pkg
from tests.scenarios.harness import run_scenario


def main(names: list[str]) -> int:
    all_names = sorted(m.name for m in pkgutil.iter_modules([str(Path(pkg.__file__).parent)])
                       if m.name.startswith("sc_"))
    names = names or all_names
    failed = 0
    for name in names:
        mod = importlib.import_module(f"tests.scenarios.{name}")
        with tempfile.TemporaryDirectory() as tmp:
            score = run_scenario(mod.build(Path(tmp)), Path(tmp))
            print(score.report())
            failed += not score.ok
    print(f"\n{len(names) - failed}/{len(names)} scenarios OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
