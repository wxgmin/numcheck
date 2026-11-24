"""Report writers (HTML, Excel, JSON, annotated deck) and the shared serialisation helpers."""

from __future__ import annotations

import enum
import math
from collections import Counter
from dataclasses import asdict, is_dataclass
from typing import Any

from numcheck.models import Issue, Match, ReviewResult, ReviewWarning, format_model_value

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Severity colours for light backgrounds (muted brick / ochre / slate); AA against white.
SEVERITY_COLORS = {
    "high": "#9B2C2C",
    "medium": "#8A5A00",
    "low": "#475569",
}

STATUS_LABELS = {
    "matched": "Verified",
    "matched_value_only": "Value only",
    "mismatch": "Mismatch",
    "stale": "Stale",
    "ambiguous": "Ambiguous",
    "unmatched": "Unmatched",
    "skipped": "Skipped",
}

TYPE_LABELS = {
    "mismatch": "Model mismatch",
    "inconsistent_across_slides": "Inconsistent across slides",
    "sum_error": "Sum error",
    "pct_reconcile": "Percent reconcile",
    "growth_reconcile": "Growth reconcile",
    "ratio_reconcile": "Ratio reconcile",
    "unit_period_mismatch": "Unit / period",
    "stale": "Stale",
    "unmatched": "Unmatched",
    "ambiguous": "Ambiguous",
    "period_ambiguous": "Period ambiguous",
    "sign_error": "Sign error",
    "scale_error": "Scale error",
    "rounding": "Rounding",
    "source_error": "Source error",
    "footnote": "Footnote",
}

MATCH_ROW_CAP = 5000


def type_label(value: str) -> str:
    return TYPE_LABELS.get(value, value.replace("_", " ").capitalize())


def summarize(issues: list[Issue]) -> dict:
    by_severity = dict(Counter(i.severity.value for i in issues))
    by_type = dict(Counter(i.issue_type.value for i in issues))
    return {"total": len(issues), "by_severity": by_severity, "by_type": by_type}


def as_result(obj: "ReviewResult | list[Issue]") -> ReviewResult:
    """Accept a ReviewResult or a plain list of issues."""
    if isinstance(obj, ReviewResult):
        return obj
    return ReviewResult(issues=list(obj))


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (SEVERITY_ORDER.get(i.severity.value, 9), i.slide_number, i.id))


def clean(value: Any) -> Any:
    """Make *value* JSON-safe: enums to values, dataclasses to dicts, NaN/inf to None."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if is_dataclass(value) and not isinstance(value, type):
        return clean(asdict(value))
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def issue_to_dict(i: Issue) -> dict[str, Any]:
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
        "confidence": clean(i.confidence),
        "deck_label": i.deck_label,
        "model_label": i.model_label,
        "suggestion": i.suggestion,
        "difference": clean(i.difference),
        "tolerance": clean(i.tolerance),
        "difference_text": i.difference_text,
        "tolerance_text": i.tolerance_text,
        "check": i.check,
        "related": clean(i.related),
        "deck_uid": i.deck_uid,
        "char_start": i.char_start,
        "char_end": i.char_end,
        "paragraph_index": i.paragraph_index,
        "table_row": i.table_row,
        "table_col": i.table_col,
        "details": clean(i.details),
    }


def warning_to_dict(w: ReviewWarning) -> dict[str, Any]:
    return clean(w)


def match_to_row(m: Match) -> dict[str, Any]:
    d = m.deck
    candidate = None
    if m.status in {"unmatched", "ambiguous", "matched_value_only"} and m.source_options:
        candidate = next((value for value, _ in m.source_options if value.ref == m.source_suggestion),
                         m.source_options[0][0])
    value = format_model_value(candidate or m.model)
    if m.method == "derived" and m.comparison is not None:
        from numcheck.matching.normalizer import format_canonical

        value = format_canonical(m.comparison.dimension, m.comparison.model_value)
    return {
        "slide_number": d.slide_number,
        "raw_text": d.raw_text,
        "label": d.label,
        "context": d.context,
        "status": m.status,
        "source_cell": m.model.ref if m.model is not None else "",
        "model_value": value,
        "method": "AI suggestion" if candidate and m.source_suggestion else m.method,
        "confidence": clean(round(m.confidence, 3)),
        "deck_uid": d.uid,
        "shape_id": d.shape_id,
        "period": d.period,
        "note": m.note,
        "derivation_cells": [cell.ref for cell in m.derivation_inputs],
        "possible_source_count": len(m.source_options or ()),
        "source_suggestion": m.source_suggestion,
        "best_source": candidate.ref if candidate else "",
    }


def match_rows(result: ReviewResult, cap: int | None = None) -> list[dict[str, Any]]:
    rows = [match_to_row(m) for m in result.matches]
    rows.sort(key=lambda r: (r["slide_number"], r["shape_id"]))
    return rows[:cap] if cap else rows


def result_to_dict(result: ReviewResult, match_cap: int | None = None) -> dict[str, Any]:
    """Complete machine-readable representation shared by the JSON report and the web API."""
    issues = [issue_to_dict(i) for i in sort_issues(result.issues)]
    total_matches = len(result.matches)
    rows = match_rows(result, match_cap)
    return {
        "inputs": clean(result.inputs),
        "settings": clean(result.settings),
        "summary": summarize(result.issues),
        "coverage": clean(result.coverage),
        "source_review": clean(result.source_review),
        "warnings": [warning_to_dict(w) for w in result.warnings],
        "slides": [clean(s) for s in result.slides],
        "issues": issues,
        "matches": rows,
        "matches_total": total_matches,
        "matches_truncated": len(rows) < total_matches,
        "elapsed": clean(result.elapsed),
        "deck_numbers": result.deck_numbers,
        "model_values": result.model_values,
    }


def coverage_pct(result: ReviewResult) -> float | None:
    v = result.coverage.get("verified_pct") if result.coverage else None
    return float(v) if isinstance(v, (int, float)) else None
