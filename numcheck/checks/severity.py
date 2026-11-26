"""Severity assignment for issues.

Why a dedicated module: severity depends on *what kind* of problem it is, how
big the gap is, whether the metric is one the reader cares about (key metric),
and how sure we are. Keeping the rules in one place makes them auditable.

This module must stay import-free of the rest of ``numcheck.checks`` (the
package ``__init__`` imports it), so token helpers live here.
"""

from __future__ import annotations

import re

from numcheck.models import Issue, IssueType, Severity

LARGE_GAP = 0.05          # |relative difference| above which an issue is HIGH
LOW_CONFIDENCE = 0.6

_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
_BY_RANK = {v: k for k, v in _RANK.items()}

_MODEL_TYPES = {
    IssueType.MISMATCH, IssueType.SIGN_ERROR, IssueType.SCALE_ERROR, IssueType.STALE,
    IssueType.SOURCE_ERROR, IssueType.INCONSISTENT_ACROSS_SLIDES, IssueType.SUM_ERROR,
}
_DERIVED_TYPES = {IssueType.PCT_RECONCILE, IssueType.GROWTH_RECONCILE, IssueType.RATIO_RECONCILE}


def severity_rank(sev: Severity) -> int:
    """0 = most severe."""
    return _RANK[sev]


def tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens ("EV/EBITDA" -> ["ev", "ebitda"])."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    """True when *needle* occurs as a contiguous run inside *haystack*."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def is_key_metric(metric_key: str, config) -> bool:
    """Token-level key-metric test.

    Substring matching is wrong ("ev" is inside "revenue", "ebit" inside
    "ebitda"); a configured metric matches only when its tokens appear as a
    whole, contiguous run in the metric key.
    """
    toks = tokens(metric_key)
    return any(contains_sequence(toks, tokens(k)) for k in config.key_metrics)


def _lower(sev: Severity) -> Severity:
    return _BY_RANK[min(_RANK[sev] + 1, 2)]


def _base_severity(issue_type: IssueType, is_key: bool, rel: float | None) -> Severity:
    big = rel is not None and rel > LARGE_GAP
    if issue_type in _MODEL_TYPES:
        return Severity.HIGH if (is_key or big) else Severity.MEDIUM
    if issue_type in _DERIVED_TYPES:
        return Severity.HIGH if (is_key and big) else Severity.MEDIUM
    if issue_type == IssueType.UNMATCHED:
        return Severity.MEDIUM if is_key else Severity.LOW
    if issue_type in (IssueType.AMBIGUOUS, IssueType.PERIOD_AMBIGUOUS, IssueType.UNIT_PERIOD_MISMATCH):
        return Severity.MEDIUM
    return Severity.LOW  # ROUNDING, FOOTNOTE


def severity_for(issue_type: IssueType, metric_key: str, config) -> Severity:
    """Base severity from type and metric alone (used when an issue is first created)."""
    return _base_severity(issue_type, is_key_metric(metric_key, config), None)


def _relative_gap(issue: Issue) -> float | None:
    rel = issue.details.get("relative_difference")
    if isinstance(rel, (int, float)):
        return float(rel)
    exp = issue.details.get("expected_canonical")
    if issue.difference is not None and isinstance(exp, (int, float)) and exp:
        return abs(issue.difference) / abs(exp)
    return None


def assign_severity(issue: Issue, config, slides=None) -> Severity:
    """Compute, store and return the final severity of *issue*.

    A check may pin its own starting level through ``details["base_severity"]``
    (e.g. scale-style differences are LOW). Low confidence or a hidden slide
    lowers the result one level (never below LOW).
    """
    fixed = issue.details.get("base_severity")
    if fixed:
        sev = Severity(fixed)
    else:
        sev = _base_severity(issue.issue_type, is_key_metric(issue.metric_key, config), _relative_gap(issue))
    hidden = any(s.slide_number == issue.slide_number and s.hidden for s in (slides or []))
    if issue.confidence < LOW_CONFIDENCE or hidden:
        sev = _lower(sev)
    issue.severity = sev
    return sev
