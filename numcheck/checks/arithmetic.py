"""Arithmetic checks: table sums, margin percentages, growth rates."""

from __future__ import annotations

from collections import defaultdict

from numcheck.checks import IssueCollector, canonical_of, values_close
from numcheck.config import Config
from numcheck.models import DeckNumber, IssueType

SUM_TOLERANCE = 0.005
PCT_TOLERANCE = 0.005
GROWTH_TOLERANCE = 0.005

MARGINS: dict[str, tuple[str, str]] = {
    "ebitda margin": ("ebitda", "revenue"),
    "gross margin": ("gross profit", "revenue"),
    "operating margin": ("ebit", "revenue"),
    "net margin": ("net income", "revenue"),
    "tax rate": ("tax", "ebt"),
}

GROWTH_WORDS = ("growth", "yoy", "y/y", "cagr")


def check_sums(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    tables: dict[tuple[int, str], list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        if d.source.value == "table" and d.table_row >= 0 and d.table_col >= 0:
            tables[(d.slide_number, d.shape_id)].append(d)

    for _key, cells in tables.items():
        totals = [d for d in cells if _is_total(d.label)]
        for total in totals:
            components = [
                d
                for d in cells
                if d.table_col == total.table_col
                and d.table_row < total.table_row
                and not _is_total(d.label)
                and d.unit == total.unit
            ]
            if len(components) < 2:
                continue
            total_val = canonical_of(total, config)[1]
            comp_sum = sum(canonical_of(d, config)[1] for d in components)
            if not values_close(total_val, comp_sum, SUM_TOLERANCE):
                collector.add(
                    slide_number=total.slide_number,
                    deck_value=total.raw_text,
                    expected_value=_fmt(comp_sum),
                    source_cell="",
                    issue_type=IssueType.SUM_ERROR,
                    message=f"'{total.label}' does not equal the sum of its components",
                    metric_key=total.metric_key,
                    shape_id=total.shape_id,
                )


def check_percentages(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    by_slide: dict[int, list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        by_slide[d.slide_number].append(d)

    for slide, nums in by_slide.items():
        for d in nums:
            if d.unit != "percent":
                continue
            pair = None
            for key, mapping in MARGINS.items():
                if key in d.metric_key:
                    pair = mapping
                    break
            if pair is None:
                continue
            num_val = _find_metric_value(nums, pair[0], d.period, config)
            den_val = _find_metric_value(nums, pair[1], d.period, config)
            if num_val is None or den_val is None or den_val == 0:
                continue
            expected = num_val / den_val * 100.0
            if not values_close(d.value, expected, PCT_TOLERANCE):
                collector.add(
                    slide_number=d.slide_number,
                    deck_value=d.raw_text,
                    expected_value=f"{expected:.2f}%",
                    source_cell="",
                    issue_type=IssueType.PCT_RECONCILE,
                    message=(
                        f"'{d.label}' is {d.value}% but {pair[0]}/{pair[1]} reconciles to "
                        f"{expected:.2f}%"
                    ),
                    metric_key=d.metric_key,
                    shape_id=d.shape_id,
                )


def check_growth(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    by_slide: dict[int, list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        by_slide[d.slide_number].append(d)

    for slide, nums in by_slide.items():
        for d in nums:
            if d.unit != "percent":
                continue
            if not any(w in d.metric_key for w in GROWTH_WORDS):
                continue
            base = _strip_growth_words(d.metric_key)
            candidates = [
                x
                for x in nums
                if x is not d and x.unit == "currency" and base in x.metric_key and x.period
            ]
            by_period: dict[str, list[float]] = defaultdict(list)
            for x in candidates:
                by_period[x.period].append(canonical_of(x, config)[1])
            periods = sorted(by_period.keys())
            if len(periods) < 2:
                continue
            prior = max(by_period[periods[-2]])
            current = max(by_period[periods[-1]])
            if prior == 0:
                continue
            expected = (current - prior) / prior * 100.0
            if not values_close(d.value, expected, GROWTH_TOLERANCE):
                collector.add(
                    slide_number=d.slide_number,
                    deck_value=d.raw_text,
                    expected_value=f"{expected:.2f}%",
                    source_cell="",
                    issue_type=IssueType.GROWTH_RECONCILE,
                    message=(
                        f"'{d.label}' is {d.value}% but underlying figures imply {expected:.2f}% "
                        f"({periods[-2]} -> {periods[-1]})"
                    ),
                    metric_key=d.metric_key,
                    shape_id=d.shape_id,
                )


def _is_total(label: str) -> bool:
    return any(w in (label or "").lower() for w in ("total", "subtotal", "sum"))


def _find_metric_value(nums: list[DeckNumber], metric: str, period: str, config: Config) -> float | None:
    for d in nums:
        if d.unit == "percent":
            continue
        if metric not in d.metric_key:
            continue
        if period and d.period and d.period != period:
            continue
        if d.unit == "currency":
            return canonical_of(d, config)[1]
        return d.value
    return None


def _strip_growth_words(metric_key: str) -> str:
    out = metric_key
    for w in GROWTH_WORDS:
        out = out.replace(w, " ")
    return " ".join(out.split())


def _fmt(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"
