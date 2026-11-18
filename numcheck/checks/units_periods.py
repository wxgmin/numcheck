"""Unit and period consistency checks."""

from __future__ import annotations

from collections import defaultdict

from numcheck.checks import IssueCollector
from numcheck.config import Config
from numcheck.models import DeckNumber, IssueType


def check_units_periods(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    groups: dict[str, list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        if d.metric_key and d.metric_key != "unnamed":
            groups[d.metric_key].append(d)

    for metric, nums in groups.items():
        _check_period_conflicts(metric, nums, collector)
        _check_scale_conflicts(metric, nums, config, collector)


def _check_period_conflicts(metric: str, nums: list[DeckNumber], collector: IssueCollector) -> None:
    periods = [d.period for d in nums if d.period]
    if not periods:
        return
    counts: dict[str, int] = {}
    for p in periods:
        counts[p] = counts.get(p, 0) + 1
    majority = max(counts, key=counts.get)

    fy_years = {p[2:] for p in counts if p.startswith("fy") and len(p) > 2}
    cy_years = {p[2:] for p in counts if p.startswith("cy") and len(p) > 2}
    fy_cy_conflict = bool(fy_years & cy_years)
    ltm_ntm_conflict = "ltm" in counts and "ntm" in counts

    for d in nums:
        if not d.period:
            continue
        flag = False
        if fy_cy_conflict and (d.period.startswith("fy") or d.period.startswith("cy")) and d.period[2:] in (fy_years & cy_years):
            flag = d.period != majority
        elif ltm_ntm_conflict and d.period in ("ltm", "ntm"):
            flag = d.period != majority
        if flag:
            _add_period_issue(metric, d, collector)


def _add_period_issue(metric: str, d: DeckNumber, collector: IssueCollector) -> None:
    collector.add(
        slide_number=d.slide_number,
        deck_value=d.raw_text,
        expected_value="",
        source_cell="",
        issue_type=IssueType.UNIT_PERIOD_MISMATCH,
        message=f"'{metric}' uses inconsistent period label '{d.period or '-'}'",
        metric_key=metric,
        shape_id=d.shape_id,
    )


def _check_scale_conflicts(metric: str, nums: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    scales: dict[str, list[DeckNumber]] = defaultdict(list)
    for d in nums:
        if d.scale:
            scales[d.scale].append(d)
    if len(scales) <= 1:
        return
    is_key = any(k in metric for k in config.key_metrics)
    if not is_key:
        return
    majority = max(scales, key=lambda k: len(scales[k]))
    for scale, ds in scales.items():
        if scale == majority:
            continue
        for d in ds:
            collector.add(
                slide_number=d.slide_number,
                deck_value=d.raw_text,
                expected_value="",
                source_cell="",
                issue_type=IssueType.UNIT_PERIOD_MISMATCH,
                message=f"'{metric}' shown in '{scale}' here but '{majority}' elsewhere",
                metric_key=metric,
                shape_id=d.shape_id,
            )
