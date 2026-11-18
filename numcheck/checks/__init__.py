"""Consistency, arithmetic, unit/period, and staleness checks."""

from __future__ import annotations

from numcheck.config import Config
from numcheck.matching.normalizer import canonical_deck, parsed_from_deck
from numcheck.models import DeckNumber, Issue, IssueType, Severity


def canonical_of(deck: DeckNumber, config: Config) -> tuple[str, float]:
    return canonical_deck(parsed_from_deck(deck), config.unit_scales)


def values_close(a: float, b: float, rel_tol: float) -> bool:
    if a == 0 and b == 0:
        return True
    return abs(a - b) / max(abs(a), abs(b), 1e-12) <= rel_tol


def severity_for(issue_type: IssueType, metric_key: str, config: Config) -> Severity:
    mk = (metric_key or "").lower()
    is_key = any(k in mk for k in config.key_metrics)
    if issue_type in (
        IssueType.MISMATCH,
        IssueType.SUM_ERROR,
        IssueType.INCONSISTENT_ACROSS_SLIDES,
        IssueType.STALE,
        IssueType.UNMATCHED,
        IssueType.AMBIGUOUS,
    ):
        return Severity.HIGH if is_key else Severity.MEDIUM
    if issue_type in (IssueType.PCT_RECONCILE, IssueType.GROWTH_RECONCILE, IssueType.UNIT_PERIOD_MISMATCH):
        return Severity.MEDIUM
    return Severity.LOW


class IssueCollector:
    def __init__(self, config: Config):
        self.config = config
        self._issues: list[Issue] = []

    def add(
        self,
        slide_number: int,
        deck_value: str,
        expected_value: str,
        source_cell: str,
        issue_type: IssueType,
        message: str,
        metric_key: str = "",
        shape_id: str = "",
        severity: Severity | None = None,
    ) -> None:
        sev = severity if severity is not None else severity_for(issue_type, metric_key, self.config)
        self._issues.append(
            Issue(
                id=len(self._issues) + 1,
                slide_number=slide_number,
                deck_value=deck_value,
                expected_value=expected_value,
                source_cell=source_cell,
                issue_type=issue_type,
                severity=sev,
                message=message,
                metric_key=metric_key,
                shape_id=shape_id,
            )
        )

    @property
    def issues(self) -> list[Issue]:
        return self._issues
