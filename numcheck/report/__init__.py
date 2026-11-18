"""Report writers: HTML, Excel, JSON, and annotated deck."""

from __future__ import annotations

from collections import Counter

from numcheck.models import Issue


def summarize(issues: list[Issue]) -> dict:
    by_severity = dict(Counter(i.severity.value for i in issues))
    by_type = dict(Counter(i.issue_type.value for i in issues))
    return {
        "total": len(issues),
        "by_severity": by_severity,
        "by_type": by_type,
    }


SEVERITY_COLORS = {
    "high": "#d9534f",
    "medium": "#f0ad4e",
    "low": "#5bc0de",
}
