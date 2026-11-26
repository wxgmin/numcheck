"""Machine-readable JSON report."""

from __future__ import annotations

import json
from pathlib import Path

from numcheck import __version__
from numcheck.models import Issue, ReviewResult
from numcheck.report import as_result, result_to_dict

SCHEMA_VERSION = 2


def build_json(result: "ReviewResult | list[Issue]") -> dict:
    res = as_result(result)
    data = {"schema_version": SCHEMA_VERSION, "tool": "numcheck", "version": __version__}
    data.update(result_to_dict(res))
    return data


def write_json_report(result: "ReviewResult | list[Issue]", out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_json(result), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
