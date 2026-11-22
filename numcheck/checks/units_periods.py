"""Unit and period consistency checks.

* FY and CY labels for the *same year* on the same metric: one of them is a typo.
* LTM and NTM showing identical values for a metric: one label is wrong.
* The same figure shown at different scale styles ("bn" here, "m" elsewhere):
  not an error in the numbers, so LOW, and only when the values agree.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from numcheck.checks import (
    IssueCollector,
    deck_key,
    interval_of,
    intervals_overlap,
    location_fields,
    metric_key_of,
    ref_dict,
)
from numcheck.config import Config
from numcheck.models import DeckNumber, IssueType, Severity
from numcheck.periods import parse_key

_BANNER_SOURCES = ("slide", "table", "hint")


def _skip_metric(metric: str) -> bool:
    return not metric or metric == "unnamed"


def _verified_cells(matches) -> dict[tuple, tuple]:
    out: dict[tuple, tuple] = {}
    for m in matches or []:
        if m.status in ("matched", "matched_value_only") and m.model is not None:
            mv = m.model
            out[deck_key(m.deck)] = (mv.workbook, mv.sheet_name, mv.cell_address)
    return out


def check_units_periods(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector,
                        matches=None) -> None:
    cells = _verified_cells(matches)
    groups: dict[str, list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        m = metric_key_of(d)
        if not _skip_metric(m) and not d.skip_reason:
            groups[m].append(d)
    for metric, nums in groups.items():
        for check in (_check_fy_cy, _check_ltm_ntm, _check_scale_style):
            try:
                check(metric, nums, config, collector, cells)
            except Exception:  # noqa: BLE001 - one metric never aborts the rest
                continue


def _add(metric: str, d: DeckNumber, message: str, collector: IssueCollector, others: list[DeckNumber],
         severity: Severity, check: str, **details) -> None:
    collector.add(
        d.slide_number, d.raw_text, "", "", IssueType.UNIT_PERIOD_MISMATCH, message,
        metric_key=metric, shape_id=d.shape_id, severity=severity, check=check,
        related=[ref_dict(o) for o in others[:10]], details=details, **location_fields(d),
    )


def _distinct_cells(a: DeckNumber, b: DeckNumber, cells: dict[tuple, tuple] | None) -> bool:
    if not cells:
        return False
    ca, cb = cells.get(deck_key(a)), cells.get(deck_key(b))
    return ca is not None and cb is not None and ca != cb


def _check_fy_cy(metric: str, nums: list[DeckNumber], config: Config, collector: IssueCollector,
                 cells: dict[tuple, tuple] | None = None) -> None:
    """FY and CY labels for one year: flag the minority basis ('cy24' among 'fy24's)."""
    by_year: dict[int, dict[str, list[DeckNumber]]] = defaultdict(lambda: defaultdict(list))
    for d in nums:
        p = parse_key(d.period)
        if p and p.basis in ("fy", "cy") and p.year is not None:
            by_year[p.year][d.period].append(d)
    for year, by_key in by_year.items():
        bases = {parse_key(k).basis for k in by_key}  # type: ignore[union-attr]
        if bases != {"fy", "cy"}:
            continue
        counts = Counter({k: len(v) for k, v in by_key.items()})
        # Minority loses; on a tie the calendar label is assumed to be the slip.
        keys = sorted(counts, key=lambda k: (-counts[k], 0 if k.startswith("fy") else 1))
        majority = keys[0]
        for k in keys[1:]:
            for d in by_key[k]:
                # Both bases verified against different model cells: the model really
                # has both (e.g. a June year-end company), so the labels are fine.
                refs = [o for o in by_key[majority] if not _distinct_cells(d, o, cells)]
                if not refs:
                    continue
                _add(metric, d, f"'{metric}' uses inconsistent period label '{d.period}' "
                                f"(elsewhere '{majority}' is used for the same year)",
                     collector, refs, Severity.MEDIUM, "period_label",
                     majority_period=majority, year=year)


def _check_ltm_ntm(metric: str, nums: list[DeckNumber], config: Config, collector: IssueCollector,
                   cells: dict[tuple, tuple] | None = None) -> None:
    """Identical values under LTM and NTM labels: probably one label is a mislabel."""
    ltm = [d for d in nums if d.period == "ltm"]
    ntm = [d for d in nums if d.period == "ntm"]
    for n in ntm:
        ndim, nlo, nhi = interval_of(n, config)
        for lt in ltm:
            ldim, llo, lhi = interval_of(lt, config)
            if ldim != ndim or not intervals_overlap(llo, lhi, nlo, nhi):
                continue
            if lt.slide_number == n.slide_number and lt.shape_id == n.shape_id and lt.uid == n.uid:
                continue
            if _distinct_cells(n, lt, cells):
                continue   # the model vouches for each against its own LTM / NTM cell: equal by coincidence
            _add(metric, n, f"'{metric}' is {n.raw_text} for both LTM (slide {lt.slide_number}) and NTM "
                            f"(slide {n.slide_number}); one period label is likely wrong",
                 collector, [lt], Severity.MEDIUM, "ltm_ntm", ltm_value=lt.raw_text)
            break


def _check_scale_style(metric: str, nums: list[DeckNumber], config: Config, collector: IssueCollector,
                       cells: dict[tuple, tuple] | None = None) -> None:
    """Same figure at a different scale style ('bn' vs 'm'); LOW, and only when values agree.

    Restricted to configured key metrics: headline figures shown in bn on a cover and
    m in a table are normal for secondary metrics and would only add noise.
    """
    from numcheck.checks.severity import is_key_metric

    if not is_key_metric(metric, config):
        return
    by_ctx: dict[tuple, list[DeckNumber]] = defaultdict(list)
    for d in nums:
        if d.unit == "currency" and d.scale:
            # Compare like with like: a "$1.2bn" headline next to a "$m" table is
            # normal banker style; two tables (or two text mentions) disagreeing is not.
            kind = "text" if d.source.value in ("text_box", "notes") else d.source.value
            by_ctx[(d.period, d.currency, kind)].append(d)
    for _ctx, ds in by_ctx.items():
        scales = Counter(d.scale for d in ds)
        if len(scales) < 2:
            continue
        majority = max(scales, key=lambda s: (scales[s], s))
        ref = [d for d in ds if d.scale == majority]
        flagged: list[DeckNumber] = []
        agreeing: list[DeckNumber] = []
        for d in ds:
            if d.scale == majority:
                continue
            # A scale inherited from the slide's own banner ("EUR 000s" vs "EUR in millions")
            # is a deliberate per-slide choice, not a style slip.
            if d.unit_source in _BANNER_SOURCES:
                continue
            _dim, lo, hi = interval_of(d, config)
            agree = [r for r in ref if intervals_overlap(lo, hi, *interval_of(r, config)[1:])]
            if not agree:
                continue
            flagged.append(d)
            agreeing.extend(a for a in agree if a not in agreeing)
        if flagged:
            # One deck-level note per metric, not one per cell.
            first = flagged[0]
            extra = f" (and {len(flagged) - 1} more)" if len(flagged) > 1 else ""
            _add(metric, first, f"'{metric}' shown in '{first.scale}' here{extra} but '{majority}' elsewhere",
                 collector, flagged[1:] + agreeing, Severity.LOW, "scale_style", scale=first.scale,
                 majority_scale=majority, count=len(flagged))
