"""Deck-internal checks, issue building, severity and coverage.

Public entry points (the runner calls these):

* :func:`numcheck.checks.issues.build_issues` - match outcomes -> issues
* :func:`run_checks` - sums, ratios, growth, cross-slide, units/periods, footnotes
* :func:`finalize_issues` - severity, de-duplication, ordering, ids
* :func:`numcheck.checks.coverage.compute_coverage`

This module also holds the small helpers shared by every check.
"""

from __future__ import annotations

import logging
import re
from dataclasses import fields, replace

from numcheck.checks.severity import (  # noqa: F401  (re-exported)
    assign_severity,
    contains_sequence,
    is_key_metric,
    severity_for,
    tokens,
)
from numcheck.config import Config
from numcheck.matching.normalizer import canonical_deck, deck_interval, parsed_from_deck
from numcheck.models import DeckNumber, Issue, IssueType, Match, Severity

log = logging.getLogger(__name__)

_ISSUE_FIELDS = {f.name for f in fields(Issue)}

_STOPWORDS = {
    "the", "a", "an", "of", "and", "in", "for", "on", "at", "to", "by", "with", "from",
    "usd", "us", "m", "mm", "bn", "k", "x", "fy", "cy", "ltm", "ntm", "ye", "year", "ended",
}


def canonical_of(deck: DeckNumber, config: Config) -> tuple[str, float]:
    """(dimension, canonical value) of a deck number; bps are folded into percent."""
    return canonical_deck(parsed_from_deck(deck), config.unit_scales)


def values_close(a: float, b: float, rel_tol: float) -> bool:
    if a == 0 and b == 0:
        return True
    return abs(a - b) / max(abs(a), abs(b), 1e-12) <= rel_tol


def metric_key_of(d: DeckNumber) -> str:
    """The matcher's metric key, or a simple fallback (alpha tokens minus stopwords)."""
    if d.metric_key:
        return d.metric_key
    toks = [t for t in re.findall(r"[a-z]+", (d.row_label or d.label or "").lower()) if t not in _STOPWORDS]
    return " ".join(toks) if toks else "unnamed"


def interval_of(d: DeckNumber, config: Config) -> tuple[str, float, float]:
    """Display interval of a deck number (dimension, low, high), canonical units."""
    return deck_interval(d, config)


def intervals_overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> bool:
    """Closed-interval overlap with a float-noise epsilon."""
    eps = 1e-9 * max(abs(a_lo), abs(a_hi), abs(b_lo), abs(b_hi), 1.0)
    return a_lo <= b_hi + eps and b_lo <= a_hi + eps


def location_fields(d: DeckNumber) -> dict:
    """Issue fields that locate a deck number precisely (used for highlighting)."""
    return {
        "deck_uid": d.uid,
        "char_start": d.char_start,
        "char_end": d.char_end,
        "paragraph_index": d.paragraph_index,
        "table_row": d.table_row,
        "table_col": d.table_col,
        "deck_label": d.label or d.row_label,
    }


def deck_key(d: DeckNumber) -> tuple:
    """Stable identity for a deck number even when ``uid`` is unset."""
    if d.uid:
        return ("uid", d.uid)
    return (d.slide_number, d.shape_id, d.paragraph_index, d.char_start, d.table_row, d.table_col, d.raw_text)


def ref_dict(d: DeckNumber, **extra) -> dict:
    """A compact reference to a deck number for ``Issue.related``."""
    out = {
        "slide_number": d.slide_number,
        "shape_id": d.shape_id,
        "deck_value": d.raw_text,
        "deck_uid": d.uid,
        "label": d.label or d.row_label,
        "period": d.period,
    }
    out.update(extra)
    return out


class IssueCollector:
    """Accumulates issues; ``add`` accepts any extra :class:`Issue` field as a keyword."""

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
        **extra,
    ) -> Issue:
        unknown = set(extra) - _ISSUE_FIELDS
        if unknown:
            raise TypeError(f"unknown Issue field(s): {sorted(unknown)}")
        for list_field in ("related",):
            if list_field in extra:
                extra[list_field] = list(extra[list_field])
        details = dict(extra.pop("details", {}) or {})
        if severity is not None:
            # An explicit level is the check's own starting point; finalize still
            # applies the confidence / hidden-slide downgrades on top of it.
            details.setdefault("base_severity", severity.value)
        sev = severity if severity is not None else severity_for(issue_type, metric_key, self.config)
        issue = Issue(
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
            details=details,
            **extra,
        )
        self._issues.append(issue)
        return issue

    @property
    def issues(self) -> list[Issue]:
        return self._issues


def run_checks(deck_numbers, matches, slides, footnotes, config, collector) -> None:
    """Run every deck-internal check; one failing check never aborts the rest."""
    from numcheck.checks.arithmetic import check_growth, check_percentages, check_sums
    from numcheck.checks.consistency import check_cross_slide
    from numcheck.checks.footnotes import check_footnotes
    from numcheck.checks.units_periods import check_units_periods

    nums = [d if d.metric_key else replace(d, metric_key=metric_key_of(d))
            for d in deck_numbers if not d.skip_reason]
    steps = (
        ("sums", lambda: check_sums(nums, config, collector)),
        ("ratios", lambda: check_percentages(nums, config, collector)),
        ("growth", lambda: check_growth(nums, config, collector)),
        ("cross_slide", lambda: check_cross_slide(nums, config, collector, matches)),
        ("units_periods", lambda: check_units_periods(nums, config, collector, matches)),
        ("footnotes", lambda: check_footnotes(nums, slides, footnotes, config, collector)),
    )
    for name, step in steps:
        try:
            step()
        except Exception:  # noqa: BLE001 - principle 6: never abort a review
            log.exception("check %r failed", name)
    _drop_echoes(collector, matches)


_ARITHMETIC = {IssueType.SUM_ERROR, IssueType.PCT_RECONCILE, IssueType.GROWTH_RECONCILE, IssueType.RATIO_RECONCILE}


def _drop_echoes(collector, matches) -> None:
    """Remove arithmetic findings caused by an input the model already proved wrong.

    If EBITDA on a slide is wrong (and reported as a model mismatch), the margin next
    to it will not reconcile even when the margin itself is correct. Flagging the
    correct margin would send the analyst to fix the wrong number.
    """
    wrong = {m.deck.uid for m in matches or [] if m.deck.uid and m.status in ("mismatch", "stale")}
    # A number the model verified in exactly its stated period (FY2024 vs CY2024 columns both exist,
    # e.g. a June year-end company) is not mislabelled, whatever other slides use.
    verified_period = {m.deck.uid for m in matches or []
                       if m.deck.uid and m.status == "matched" and m.model is not None
                       and m.deck.period and m.model.period == m.deck.period}
    # The model is the authority: an output number the model itself verifies (e.g. a margin that equals
    # the model's own margin cell) is not overruled by re-deriving it from other rounded figures.
    verified = {m.deck.uid for m in matches or [] if m.deck.uid and m.status == "matched" and m.method != "derived"}
    keep = []
    for i in collector.issues:
        if i.issue_type in _ARITHMETIC:
            inputs = {r.get("deck_uid") for r in i.related if isinstance(r, dict)}
            if inputs & wrong:
                continue
            if i.issue_type != IssueType.SUM_ERROR and i.deck_uid in verified:
                continue
        if i.issue_type == IssueType.UNIT_PERIOD_MISMATCH and i.check == "period_label" and i.deck_uid in verified_period:
            continue
        keep.append(i)
    collector.issues[:] = keep


# Re-exports placed last: these modules import helpers defined above.
from numcheck.checks.coverage import compute_coverage  # noqa: E402,F401
from numcheck.checks.issues import build_issues, finalize_issues  # noqa: E402,F401

__all__ = [
    "IssueCollector", "assign_severity", "build_issues", "canonical_of", "compute_coverage",
    "finalize_issues", "run_checks", "severity_for", "values_close", "Match",
]
