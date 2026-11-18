"""Machine-readable JSON report."""

from __future__ import annotations

import json
from pathlib import Path

from numcheck.models import Issue
from numcheck.report import summarize


def write_json_report(issues: list[Issue], out_path: str | Path) -> None:
    data = {
        "summary": summarize(issues),
        "issues": [_issue_to_dict(i) for i in issues],
    }
    Path(out_path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _issue_to_dict(i: Issue) -> dict:
    return {
        "id": i.id,
        "slide_number": i.slide_number,
        "deck_value": i.deck_value,
        "expected_value": i.expected_value,
        "source_cell": i.source_cell,
        "issue_type": i.issue_type.value,
        "severity": i.severity.value,
        "message": i.message,
        "metric_key": i.metric_key,
        "shape_id": i.shape_id,
    }
