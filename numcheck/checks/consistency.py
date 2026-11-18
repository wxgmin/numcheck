"""Cross-slide consistency: same metric+period with different values."""

from __future__ import annotations

from collections import defaultdict

from numcheck.checks import IssueCollector, canonical_of, values_close
from numcheck.config import Config
from numcheck.models import DeckNumber, IssueType

CROSS_SLIDE_TOLERANCE = 0.02


def check_cross_slide(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    groups: dict[tuple[str, str], list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        groups[(d.metric_key, d.period)].append(d)

    for (metric, _period), nums in groups.items():
        if metric == "unnamed" or len(nums) < 2:
            continue

        clusters: list[tuple[float, list[DeckNumber]]] = []
        for d in nums:
            _dim, val = canonical_of(d, config)
            placed = False
            for rep, members in clusters:
                if values_close(val, rep, CROSS_SLIDE_TOLERANCE):
                    members.append(d)
                    placed = True
                    break
            if not placed:
                clusters.append((val, [d]))

        if len(clusters) <= 1:
            continue

        # Largest cluster is treated as the consensus.
        clusters.sort(key=lambda c: len(c[1]), reverse=True)
        consensus = clusters[0]
        for rep, members in clusters[1:]:
            for d in members:
                expected = _fmt(consensus[0])
                actual = _fmt(rep)
                collector.add(
                    slide_number=d.slide_number,
                    deck_value=d.raw_text,
                    expected_value=expected,
                    source_cell="",
                    issue_type=IssueType.INCONSISTENT_ACROSS_SLIDES,
                    message=(
                        f"'{d.metric_key}' (period '{d.period or '-'}') appears as {actual} here "
                        f"but {expected} on other slides"
                    ),
                    metric_key=d.metric_key,
                    shape_id=d.shape_id,
                )


def _fmt(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"
