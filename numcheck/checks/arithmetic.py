"""Arithmetic checks: table/pie sums, margins and ratios, growth and CAGR.

Every comparison uses *interval arithmetic* on the displayed numbers: a shown
"25%" stands for [24.5, 25.5], "$300m" for [299.5, 300.5], so a ratio of two
rounded figures is itself an interval, and we flag only when that interval does
not overlap the interval of the displayed result. Rounding can therefore never
cause a false alarm, and ``7%`` can never "match" ``10%``.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass

from numcheck.checks import (
    IssueCollector,
    canonical_of,
    interval_of,
    intervals_overlap,
    location_fields,
    metric_key_of,
    ref_dict,
    tokens,
)
from numcheck.config import Config
from numcheck.matching.normalizer import format_like
from numcheck.models import DeckNumber, IssueType
from numcheck.periods import parse_span, period_affinity, previous_period

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

_NON_ADDITIVE = ("percent", "multiple", "bps")
_FILLER = {"the", "of", "a", "an", "and", "in", "for", "on", "multiple", "ratio", "x",
           "ltm", "ntm", "ttm", "adj", "to"}


def _dim(d: DeckNumber, config: Config) -> str:
    return canonical_of(d, config)[0]


def _same_dim(a: str, b: str) -> bool:
    return a == b or "none" in (a, b)


def _ignorable(config: Config, vocab: frozenset[str] = frozenset()) -> set[str]:
    """Tokens that never distinguish a metric: filler plus configured qualifiers.

    Qualifier words that are also part of a relation's vocabulary ("pre tax
    income", "tax rate") are kept, otherwise "tax" would vanish.
    """
    out = set(_FILLER)
    for q in config.qualifiers:
        out.update(t for t in tokens(q) if t not in vocab)
    return out


def _metric_tokens(d: DeckNumber, config: Config, ignore: set[str] | None = None) -> frozenset[str]:
    ign = ignore if ignore is not None else _ignorable(config)
    return frozenset(t for t in tokens(metric_key_of(d)) if t not in ign)


def _half_width(lo: float, hi: float) -> float:
    return (hi - lo) / 2.0


# --------------------------------------------------------------------------- #
# Sums
# --------------------------------------------------------------------------- #

_TOTAL_TOKENS = {"total", "subtotal"}
_OF_TOTAL = re.compile(r"%\s*(?:of\s+)?total|percent(?:age)?\s+(?:of\s+)?total|\bof\s+total\b|\bpct\s+(?:of\s+)?total", re.I)
_SHARE = re.compile(
    r"%\s*(?:of\s+)?total|percent(?:age)?\s+of\s+total|\bmix\b|(?<!per\s)(?<!market\s)\bshare\b"
    r"(?!\s*(?:price|count|repurchase|buyback|capital))", re.I)


def _is_share_label(label: str) -> bool:
    return bool(_SHARE.search(label or ""))


_NET_FLOW = re.compile(r"^\s*net\s+(?:cash\s+flow|change|increase|decrease|movement)\b", re.I)
_CLOSING = re.compile(r"^\s*(?:closing|ending|end\s+of\s+(?:period|year)|balance\s+at\s+end)\b", re.I)
_OPENING = re.compile(r"^\s*(?:opening|beginning|starting|balance\s+at\s+(?:start|beginning))\b", re.I)
_RATIO_WORDS = re.compile(
    r"/|\b(?:leverage|coverage|cover|multiple|margin|ratio|yield|growth|cagr|turns|per\s+share)\b|\bx\s+ebitda\b",
    re.I)
_CUMULATIVE = re.compile(r"\bcum(?:\.|ulative)?(?![a-z])", re.I)
# Metrics that are never additive components of a plain "Total <something>" line.
_HETERO = {"ebitda", "ebit", "ev", "enterprise", "eps", "margin", "leverage", "multiple", "coverage",
           "ratio", "yield", "irr", "moic"}
_SOURCES = {"sources", "funding", "funds"}
_USES = {"uses"}


def _is_total_label(label: str) -> bool:
    if _OF_TOTAL.search(label or ""):
        return False
    if _NET_FLOW.search(label or "") or _CLOSING.search(label or ""):
        return True
    return bool(set(tokens(label)) & _TOTAL_TOKENS)


def _is_cumulative(label: str) -> bool:
    return bool(_CUMULATIVE.search(label or ""))


def _hetero_to(total_label: str, comp_label: str) -> bool:
    """True when a component is a different kind of metric than the total (EBITDA in 'Total debt')."""
    tt = set(tokens(total_label))
    extra = (set(tokens(comp_label)) & _HETERO) - tt
    return bool(extra) and bool(tt - _TOTAL_TOKENS)


def _is_subtract_label(label: str) -> bool:
    t = (label or "").lower()
    return "(-)" in t or bool(set(tokens(t)) & {"less", "minus"})


def _row_label(d: DeckNumber) -> str:
    return d.row_label or d.label


def _col_label(d: DeckNumber) -> str:
    return d.col_label or ""


def _signed_interval(d: DeckNumber, label: str, config: Config) -> tuple[float, float, float]:
    """(lo, hi, centre) of a component, negated when it is a 'less:' line shown positive."""
    _dim_, lo, hi = interval_of(d, config)
    val = canonical_of(d, config)[1]
    if _is_subtract_label(label) and val > 0:
        return -hi, -lo, -val
    return lo, hi, val


def _sum_of(comps, label_of, config) -> tuple[float, float, float]:
    lo = hi = centre = 0.0
    for c in comps:
        a, b, v = _signed_interval(c, label_of(c), config)
        lo, hi, centre = lo + a, hi + b, centre + v
    return lo, hi, centre


def _emit_total_issue(total, comps, sum_lo, sum_hi, centre, label_of, config, collector, axis, kind) -> None:
    tdim, tlo, thi = interval_of(total, config)
    tval = canonical_of(total, config)[1]
    expected = format_like(total, centre, config)
    tol = _half_width(sum_lo, sum_hi) + _half_width(tlo, thi)
    what = _row_label(total) if axis == "column" else (_col_label(total) or _row_label(total))
    msg = (f"'{what}' shows {total.raw_text} but its {len(comps)} components ({kind}) add up to "
           f"{expected} (tolerance +/-{tol:,.4g} from display rounding)")
    collector.add(
        total.slide_number, total.raw_text, expected, "", IssueType.SUM_ERROR, msg,
        metric_key=metric_key_of(total), shape_id=total.shape_id,
        suggestion=expected, difference=tval - centre, tolerance=tol, check="sum",
        related=[ref_dict(c) for c in comps],
        details={"axis": axis, "components": len(comps), "computed_sum": centre,
                 "relative_difference": abs(tval - centre) / max(abs(centre), 1e-12),
                 "expected_canonical": centre},
        **location_fields(total),
    )


def _percent_sum_plausible(total, comps, config) -> bool:
    """Percent lines add only when they are shares of a whole (the sum or total is ~100)."""
    if not comps:
        return False
    vals = [canonical_of(c, config)[1] for c in comps]
    centre = sum(vals)
    tval = canonical_of(total, config)[1]
    if 95.0 <= centre <= 105.0:
        return True
    # A ~100% total only sums shares when each component is a fraction of it: an occupancy, margin
    # or yield "Total" of 95.4% over regions of 94-96% is an average, never a sum.
    return 95.0 <= tval <= 105.0 and max(vals) < 0.9 * tval


def _is_cumulative_block(total, comps, config) -> bool:
    """Running totals: values never decrease and the last component already equals the total."""
    if len(comps) < 2:
        return False
    vals = [canonical_of(c, config)[1] for c in comps]
    if any(b < a for a, b in zip(vals, vals[1:], strict=False)):
        return False
    _d, tlo, thi = interval_of(total, config)
    _d, llo, lhi = interval_of(comps[-1], config)
    return intervals_overlap(tlo, thi, llo, lhi)


def _evaluate_total(total, flat, outstanding, label_of, config, collector, axis) -> None:
    """Check one total against (a) its block and (b) a roll-up of earlier subtotals.

    A grand total either sums the plain rows since the previous total (flat
    block) or the earlier subtotals plus any plain rows after the last subtotal
    (roll-up). Either reading that reconciles is accepted; when neither does we
    report the closer one.
    """
    tdim, tlo, thi = interval_of(total, config)
    tlabel = label_of(total)
    if tdim in ("multiple", "bps") or _RATIO_WORDS.search(tlabel or ""):
        outstanding.append(total)  # non-additive: multiples and ratios never sum
        return
    flat_c = [c for c in flat if _same_dim(_dim(c, config), tdim) and not _hetero_to(tlabel, label_of(c))]
    out_c = [o for o in outstanding if _same_dim(_dim(o, config), tdim) and not _hetero_to(tlabel, label_of(o))]
    if tdim == "percent" and not _percent_sum_plausible(total, flat_c + out_c, config):
        outstanding.append(total)  # percents of different bases are not additive
        return
    if _is_cumulative_block(total, flat_c, config):
        outstanding.append(total)
        return
    hyps: list[tuple[str, list]] = []
    if len(flat_c) >= 2:
        hyps.append(("block", flat_c))
    if axis == "row" and len(flat_c) >= 3:
        # "Invested | Realised | Unrealised | Total value": a horizontal total often sums only the columns
        # immediately before it. Accept any trailing run of >= 2 columns that reconciles.
        for k in range(2, len(flat_c)):
            hyps.append(("block", flat_c[-k:]))
    if out_c and len(out_c) + len(flat_c) >= 2:
        hyps.append(("rollup", out_c + flat_c))
    if not hyps:
        outstanding.append(total)
        return
    results = []
    for name, comps in hyps:
        lo, hi, centre = _sum_of(comps, label_of, config)
        results.append((name, comps, lo, hi, centre, intervals_overlap(lo, hi, tlo, thi)))
    ok = [r for r in results if r[5]]
    if ok:
        chosen = next((r for r in ok if r[0] == "block"), ok[0])
    else:
        tval = canonical_of(total, config)[1]
        chosen = min(results, key=lambda r: abs(r[4] - tval))
        name, comps, lo, hi, centre, _ = chosen
        kind = "plain rows since the previous total" if name == "block" else "earlier subtotals plus rows after them"
        _emit_total_issue(total, comps, lo, hi, centre, label_of, config, collector, axis, kind)
    if chosen[0] == "rollup":
        outstanding[:] = [total]
    else:
        outstanding.append(total)


_SIGN_CUE = re.compile(r"\b(?:less|minus|repay\w*|redemption|amorti[sz]\w*|outflow|paid|payments?|drawdowns?|"
                       r"plus|add|issuance|proceeds|inflow)\b|\(\s*[-+]\s*\)|^\s*[-+]", re.I)


def _roll_forward(total, opening, flows, label_of, config, collector, axis) -> bool:
    """opening + flows = closing. Returns True when it handled (reconciled or flagged) the closing line."""
    tdim, tlo, thi = interval_of(total, config)
    flows = [f for f in flows if _same_dim(_dim(f, config), tdim)]
    if not flows or not _same_dim(_dim(opening, config), tdim) or tdim in ("multiple", "bps"):
        return False
    signed = any(canonical_of(f, config)[1] < 0 or _SIGN_CUE.search(label_of(f) or "") for f in flows)
    if len(flows) > 1 and not signed:
        # Debt schedules list CFADS, interest, principal, debt service... all shown positive. Without
        # sign cues we cannot tell inflows from outflows: do not guess (and do not flag).
        return True
    comps = [opening] + flows
    lo, hi, centre = _sum_of(comps, label_of, config)
    if not intervals_overlap(lo, hi, tlo, thi):
        _emit_total_issue(total, comps, lo, hi, centre, label_of, config, collector, axis,
                          "opening balance plus the period's movement")
    return True


def _walk_line(items, is_total, label_of, config, collector, axis) -> None:
    flat: list[DeckNumber] = []
    outstanding: list[DeckNumber] = []
    opening: DeckNumber | None = None
    for d in items:
        label = label_of(d) or ""
        if is_total(d):
            if opening is not None and _CLOSING.search(label):
                flows = flat or outstanding[-1:]
                if _roll_forward(d, opening, flows, label_of, config, collector, axis):
                    opening, flat, outstanding = None, [], [d]
                    continue
            if opening is None and _CLOSING.search(label) and not set(tokens(label)) & _TOTAL_TOKENS:
                # "Ending ARR" in a KPI table is a balance, not the sum of the P&L lines above it; a closing
                # balance only reconciles against an opening balance plus flows.
                continue
            _evaluate_total(d, flat, outstanding, label_of, config, collector, axis)
            flat = []
        elif _OPENING.search(label):
            opening = d
        else:
            flat.append(d)


def _table_cells(deck_numbers):
    tables: dict[tuple[int, str], dict[tuple[int, int], DeckNumber]] = defaultdict(dict)
    for d in deck_numbers:
        if d.source.value == "table" and d.table_row >= 0 and d.table_col >= 0:
            tables[(d.slide_number, d.shape_id)].setdefault((d.table_row, d.table_col), d)
    return tables


def _check_vertical(cells, config, collector) -> None:
    for col in sorted({c for _, c in cells}):
        items = [d for (r, c), d in sorted(cells.items()) if c == col]
        if len(items) < 3 or any(_is_share_label(_col_label(d)) for d in items):
            continue
        if any(_is_cumulative(_col_label(d)) for d in items):
            continue  # running totals: each row already includes the ones above it
        _walk_line(items, lambda d: _is_total_label(_row_label(d)), _row_label, config, collector, "column")


def _check_horizontal(cells, config, collector) -> None:
    for row in sorted({r for r, _ in cells}):
        items = [d for (r, c), d in sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1])) if r == row]
        if len(items) < 3 or _is_share_label(_row_label(items[0])):
            continue
        if not any(_is_total_label(_col_label(d)) for d in items):
            continue
        if any(_is_cumulative(_row_label(d)) for d in items):
            continue
        _walk_line(items, lambda d: _is_total_label(_col_label(d)), _col_label, config, collector, "row")


def _check_shares(cells, config, collector) -> None:
    """Columns/rows labelled '% of total', 'mix' or 'share' must add to 100."""
    by_col: dict[int, list[DeckNumber]] = defaultdict(list)
    by_row: dict[int, list[DeckNumber]] = defaultdict(list)
    for (r, c), d in sorted(cells.items()):
        by_col[c].append(d)
        by_row[r].append(d)
    for col, ds in by_col.items():
        if any(_is_share_label(_col_label(d)) for d in ds):
            comps = [d for d in ds if d.unit == "percent" and not _is_total_label(_row_label(d))]
            _check_100(comps, f"column '{_col_label(ds[0])}'", config, collector, "% of total column")
    for row, ds in by_row.items():
        if any(_is_share_label(_row_label(d)) for d in ds):
            comps = [d for d in ds if d.unit == "percent" and not _is_total_label(_col_label(d))]
            _check_100(comps, f"row '{_row_label(ds[0])}'", config, collector, "% of total row")


def _check_100(comps, where, config, collector, kind) -> None:
    """Flag when displayed percentages cannot add up to 100 within rounding."""
    if len(comps) < 2:
        return
    lo = hi = centre = 0.0
    for c in comps:
        _d, a, b = interval_of(c, config)
        lo, hi, centre = lo + a, hi + b, centre + canonical_of(c, config)[1]
    if intervals_overlap(lo, hi, 100.0, 100.0):
        return
    if len(comps) >= 3:
        # A "Group" / "Total" column showing 100.0% alongside the parts: drop it and re-check.
        _d, a, b = interval_of(comps[-1], config)
        if intervals_overlap(a, b, 100.0, 100.0) and intervals_overlap(lo - a, hi - b, 100.0, 100.0):
            return
    anchor = comps[0]
    tol = _half_width(lo, hi)
    msg = (f"Percentages in {where} add up to {centre:.1f}% instead of 100% "
           f"({len(comps)} values, tolerance +/-{tol:.2f}pp)")
    collector.add(
        anchor.slide_number, anchor.raw_text, "100%", "", IssueType.SUM_ERROR, msg,
        metric_key=metric_key_of(anchor), shape_id=anchor.shape_id,
        suggestion="", difference=centre - 100.0, tolerance=tol, check="sum_100",
        related=[ref_dict(c) for c in comps],
        details={"kind": kind, "computed_sum": centre, "relative_difference": abs(centre - 100.0) / 100.0,
                 "expected_canonical": 100.0},
        **location_fields(anchor),
    )


def _check_pies(deck_numbers, config, collector) -> None:
    series: dict[tuple, list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        ct = (d.chart_type or "").lower()
        if ("pie" in ct or "doughnut" in ct) and d.unit == "percent":
            series[(d.slide_number, d.shape_id, d.series_index)].append(d)
    for comps in series.values():
        name = comps[0].chart_series_name or comps[0].chart_type or "pie"
        _check_100(comps, f"pie/doughnut series '{name}'", config, collector, "pie")


def _check_sources_uses(deck_numbers, config, collector) -> None:
    """'Total sources' must equal 'Total uses' (or 'Total funding') on the same slide."""
    by_slide: dict[int, dict[str, list[DeckNumber]]] = defaultdict(lambda: {"s": [], "u": []})
    for d in deck_numbers:
        if d.source.value != "table" or d.unit != "currency" or d.skip_reason:
            continue
        label = _row_label(d)
        if not _is_total_label(label):
            continue
        toks = set(tokens(label))
        if toks & _USES:
            by_slide[d.slide_number]["u"].append(d)
        elif toks & _SOURCES:
            by_slide[d.slide_number]["s"].append(d)
    for sides in by_slide.values():
        srcs, uses = sides["s"], sides["u"]
        if not srcs or not uses:
            continue
        for u in uses:
            _d, ulo, uhi = interval_of(u, config)
            if any(intervals_overlap(ulo, uhi, *interval_of(x, config)[1:]) for x in srcs):
                continue
            s0 = srcs[0]
            sc, uc = canonical_of(s0, config)[1], canonical_of(u, config)[1]
            tol = _half_width(*interval_of(s0, config)[1:]) + _half_width(ulo, uhi)
            expected = format_like(u, sc, config)
            msg = (f"'{_row_label(u)}' shows {u.raw_text} but '{_row_label(s0)}' shows {s0.raw_text}; "
                   f"sources and uses must be equal (tolerance +/-{tol:,.4g} from display rounding)")
            collector.add(
                u.slide_number, u.raw_text, expected, "", IssueType.SUM_ERROR, msg,
                metric_key=metric_key_of(u), shape_id=u.shape_id, suggestion=expected, difference=uc - sc,
                tolerance=tol, check="sources_uses", related=[ref_dict(x) for x in srcs],
                details={"kind": "sources_uses", "computed_sum": sc, "expected_canonical": sc,
                         "relative_difference": abs(uc - sc) / max(abs(sc), 1e-12)},
                **location_fields(u),
            )


def check_sums(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    """Vertical/horizontal table totals, share columns summing to 100, pie charts, sources = uses."""
    for cells in _table_cells(deck_numbers).values():
        _check_vertical(cells, config, collector)
        _check_horizontal(cells, config, collector)
        _check_shares(cells, config, collector)
    _check_sources_uses(deck_numbers, config, collector)
    _check_pies(deck_numbers, config, collector)


# --------------------------------------------------------------------------- #
# Ratios (margins, multiples, premiums, tax rate, ...)
# --------------------------------------------------------------------------- #

def _ts(*sets: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(s.split()) for s in sets)


@dataclass(frozen=True)
class Relation:
    """result = numerator / denominator (kind 'pct' x100, 'multiple', 'premium' = n/d - 1 in %)."""

    name: str
    results: tuple[frozenset[str], ...]
    numerator: tuple[frozenset[str], ...]
    denominator: tuple[frozenset[str], ...]
    kind: str = "pct"
    num_name: str = ""
    den_name: str = ""


RELATIONS: list[Relation] = [
    Relation("ebitda margin", _ts("ebitda margin"), _ts("ebitda"), _ts("revenue"), "pct", "EBITDA", "revenue"),
    Relation("gross margin", _ts("gross margin", "gross profit margin"), _ts("gross profit"), _ts("revenue"),
             "pct", "gross profit", "revenue"),
    Relation("operating margin", _ts("operating margin", "ebit margin"), _ts("ebit"), _ts("revenue"),
             "pct", "EBIT", "revenue"),
    Relation("net margin", _ts("net margin", "net income margin", "net profit margin"), _ts("net income"),
             _ts("revenue"), "pct", "net income", "revenue"),
    Relation("fcf conversion", _ts("free cash flow conversion", "fcf conversion", "cash conversion"),
             _ts("free cash flow"), _ts("ebitda"), "pct", "free cash flow", "EBITDA"),
    Relation("tax rate", _ts("tax rate", "effective tax rate"), _ts("tax", "income tax", "taxes", "income taxes",
                                                                    "tax expense", "income tax expense"),
             _ts("pre tax income", "pretax income", "ebt", "pre tax profit", "income before tax",
                 "earnings before tax"), "pct", "tax", "pre-tax income"),
    Relation("ev/ebitda", _ts("enterprise value ebitda", "ev ebitda"), _ts("enterprise value"), _ts("ebitda"),
             "multiple", "enterprise value", "EBITDA"),
    Relation("ev/revenue", _ts("enterprise value revenue", "ev revenue"), _ts("enterprise value"), _ts("revenue"),
             "multiple", "enterprise value", "revenue"),
    Relation("p/e", _ts("p e", "price earnings"), _ts("share price", "stock price", "price per share"),
             _ts("eps", "earnings per share"), "multiple", "share price", "EPS"),
    Relation("interest cover", _ts("interest cover", "interest coverage", "interest cover ratio",
                                   "interest coverage ratio", "ebitda interest cover", "ebitda interest coverage",
                                   "cash interest cover", "cash interest coverage"),
             _ts("ebitda"),
             _ts("interest", "cash interest", "interest expense", "cash interest expense", "net interest expense",
                 "net interest", "finance costs", "net finance costs"),
             "multiple", "EBITDA", "interest"),
    Relation("net leverage", _ts("net leverage", "net leverage ratio", "net debt ebitda"), _ts("net debt"),
             _ts("ebitda"), "multiple", "net debt", "EBITDA"),
    Relation("total leverage", _ts("total leverage", "leverage", "leverage ratio", "gross leverage",
                                   "total debt ebitda", "total leverage ratio", "debt ebitda"),
             _ts("total debt", "debt", "gross debt"), _ts("ebitda"), "multiple", "total debt", "EBITDA"),
    Relation("premium", _ts("premium", "offer premium", "takeover premium"),
             _ts("offer price", "offer price per share", "offer price share"),
             _ts("unaffected price", "unaffected share price", "unaffected price per share",
                 "undisturbed price", "undisturbed share price"), "premium", "offer price", "unaffected price"),
]


def _div_interval(nlo: float, nhi: float, dlo: float, dhi: float) -> tuple[float, float] | None:
    """Interval of n/d for a strictly positive denominator interval (else None)."""
    if dlo <= 0:
        return None
    corners = [nlo / dlo, nlo / dhi, nhi / dlo, nhi / dhi]
    return min(corners), max(corners)


def _pick_by_period(cands: list[DeckNumber], period: str, *, allow_unknown: bool = True) -> list[DeckNumber]:
    """Best-affinity candidates for *period*: exact first, then compatible, then undated."""
    if not cands:
        return []
    if not period:
        if not allow_unknown:
            return []
        return cands if len({c.period for c in cands}) <= 1 else []
    scored: list[tuple[float, DeckNumber]] = []
    for c in cands:
        aff = period_affinity(period, c.period)
        if aff is None:
            if allow_unknown:
                scored.append((0.01, c))
        elif aff > 0:
            scored.append((aff, c))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [c for s, c in scored if s == best]


def _ratio_result_of(d: DeckNumber, rel: Relation, ign: set[str]) -> bool:
    toks = frozenset(t for t in tokens(metric_key_of(d)) if t not in ign)
    return toks in rel.results and d.unit == ("percent" if rel.kind in ("pct", "premium") else "multiple")


def _operands(pool: list[tuple[DeckNumber, frozenset[str]]], sets, d: DeckNumber) -> list[DeckNumber]:
    return [n for n, toks in pool if n is not d and toks in sets and n.unit not in _NON_ADDITIVE]


def check_percentages(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector,
                      relations: list[Relation] | None = None) -> None:
    """Margins, multiples and premiums must reconcile with the numbers on the same slide."""
    rels = relations if relations is not None else RELATIONS
    vocab = frozenset(t for r in rels for group in (r.results, r.numerator, r.denominator)
                      for st in group for t in st)
    ign = _ignorable(config, vocab)
    by_slide: dict[int, list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        by_slide[d.slide_number].append(d)
    for nums in by_slide.values():
        pool = [(n, frozenset(t for t in tokens(metric_key_of(n)) if t not in ign)) for n in nums]
        for d in nums:
            rel = next((r for r in rels if _ratio_result_of(d, r, ign)), None)
            if rel is not None:
                _check_relation(d, rel, pool, config, collector)


def _check_relation(d, rel, pool, config, collector) -> None:
    nums = _pick_by_period(_operands(pool, rel.numerator, d), d.period)
    dens = _pick_by_period(_operands(pool, rel.denominator, d), d.period)
    if not nums or not dens:
        return
    _rd, rlo, rhi = interval_of(d, config)
    first = None
    for n, dn in itertools.islice(itertools.product(nums, dens), 16):
        ndim, nlo, nhi = interval_of(n, config)
        ddim, dlo, dhi = interval_of(dn, config)
        if not _same_dim(ndim, ddim):
            continue
        iv = _div_interval(nlo, nhi, dlo, dhi)
        if iv is None:
            continue
        scale = 100.0
        lo, hi = iv[0] * scale, iv[1] * scale
        if rel.kind == "multiple":
            lo, hi = iv
        elif rel.kind == "premium":
            lo, hi = lo - 100.0, hi - 100.0
        if intervals_overlap(lo, hi, rlo, rhi):
            return
        if first is None:
            first = (n, dn, lo, hi)
    if first is None:
        return
    n, dn, lo, hi = first
    nv, dv = canonical_of(n, config)[1], canonical_of(dn, config)[1]
    ratio = nv / dv
    centre = ratio if rel.kind == "multiple" else (ratio * 100.0 if rel.kind == "pct" else (ratio - 1) * 100.0)
    expected = format_like(d, centre, config)
    dval = canonical_of(d, config)[1]
    formula = f"{rel.num_name} / {rel.den_name}" + (" - 1" if rel.kind == "premium" else "")
    msg = (f"'{d.label or metric_key_of(d)}' shows {d.raw_text} but {formula} ({n.raw_text} / {dn.raw_text}) "
           f"implies {expected} (range {lo:,.2f} to {hi:,.2f} after display rounding)")
    collector.add(
        d.slide_number, d.raw_text, expected, "",
        IssueType.PCT_RECONCILE if rel.kind == "pct" else IssueType.RATIO_RECONCILE, msg,
        metric_key=metric_key_of(d), shape_id=d.shape_id,
        suggestion=expected, difference=dval - centre, tolerance=_half_width(lo, hi) + _half_width(rlo, rhi),
        check=f"ratio:{rel.name}",
        related=[ref_dict(n, role="numerator"), ref_dict(dn, role="denominator")],
        details={"relation": rel.name, "formula": formula, "computed": centre,
                 "relative_difference": abs(dval - centre) / max(abs(centre), 1e-12), "expected_canonical": centre},
        **location_fields(d),
    )


# --------------------------------------------------------------------------- #
# Growth and CAGR
# --------------------------------------------------------------------------- #

GROWTH_WORDS = {"growth", "yoy", "cagr"}
_GROWTH_NOISE = GROWTH_WORDS | {"rate", "annual", "annualized", "compound", "over", "year", "yearly", "y"}


def _base_candidates(base: frozenset[str], slide: int, pool_all, period: str) -> list[DeckNumber]:
    """Values of the base metric for *period*: same slide first, then anywhere."""
    matching = [n for n, toks in pool_all if toks == base and n.unit not in _NON_ADDITIVE]
    for cands in ([n for n in matching if n.slide_number == slide], matching):
        hit = _pick_by_period(cands, period, allow_unknown=False)
        if hit:
            return hit
    return []


def check_growth(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector) -> None:
    """Growth % must follow from the base metric's values; CAGR needs a parseable span."""
    ign = _ignorable(config)
    pool_all = [(n, frozenset(t for t in tokens(metric_key_of(n)) if t not in ign)) for n in deck_numbers]
    for d in deck_numbers:
        if d.unit != "percent":
            continue
        toks = frozenset(tokens(metric_key_of(d)))
        if not (toks & GROWTH_WORDS):
            continue
        base = frozenset(t for t in toks if t not in _GROWTH_NOISE and t not in ign)
        if not base:
            continue
        if "cagr" in toks:
            _check_cagr(d, base, deck_numbers, pool_all, config, collector)
        else:
            _check_yoy(d, base, deck_numbers, pool_all, config, collector)


def _growth_interval(end, start, config, years: int) -> tuple[float, float, float] | None:
    """(lo, hi, centre) in percent for the growth from *start* to *end* over *years*."""
    edim, elo, ehi = interval_of(end, config)
    sdim, slo, shi = interval_of(start, config)
    if not _same_dim(edim, sdim) or slo <= 0 or elo <= 0 and years > 1:
        return None
    iv = _div_interval(elo, ehi, slo, shi)
    if iv is None:
        return None
    ec, sc = canonical_of(end, config)[1], canonical_of(start, config)[1]
    if years == 1:
        return (iv[0] - 1) * 100.0, (iv[1] - 1) * 100.0, (ec / sc - 1) * 100.0
    if ec <= 0:
        return None
    root = 1.0 / years
    return (iv[0] ** root - 1) * 100.0, (iv[1] ** root - 1) * 100.0, ((ec / sc) ** root - 1) * 100.0


def _flag_growth(d, ends, starts, years, config, collector, label: str) -> None:
    _gd, glo, ghi = interval_of(d, config)
    first = None
    for e, s in itertools.islice(itertools.product(ends, starts), 16):
        if e is s:
            continue
        g = _growth_interval(e, s, config, years)
        if g is None:
            continue
        lo, hi, centre = g
        if intervals_overlap(lo, hi, glo, ghi):
            return
        if first is None:
            first = (e, s, lo, hi, centre)
    if first is None:
        return
    e, s, lo, hi, centre = first
    expected = format_like(d, centre, config)
    dval = canonical_of(d, config)[1]
    msg = (f"'{d.label or metric_key_of(d)}' shows {d.raw_text} but {label} ({s.raw_text} -> {e.raw_text}) "
           f"implies {expected} (range {lo:.2f}% to {hi:.2f}% after display rounding)")
    collector.add(
        d.slide_number, d.raw_text, expected, "", IssueType.GROWTH_RECONCILE, msg,
        metric_key=metric_key_of(d), shape_id=d.shape_id,
        suggestion=expected, difference=dval - centre, tolerance=_half_width(lo, hi) + _half_width(glo, ghi),
        check="growth" if years == 1 else "cagr",
        related=[ref_dict(s, role="start"), ref_dict(e, role="end")],
        details={"years": years, "computed": centre, "expected_canonical": centre,
                 "relative_difference": abs(dval - centre) / max(abs(centre), 1e-12)},
        **location_fields(d),
    )


def _check_yoy(d, base, nums, pool_all, config, collector) -> None:
    prev_p = previous_period(d.period) if d.period else ""
    if not prev_p:
        return
    ends = _base_candidates(base, d.slide_number, pool_all, d.period)
    starts = _base_candidates(base, d.slide_number, pool_all, prev_p)
    if ends and starts:
        _flag_growth(d, ends, starts, 1, config, collector, "the underlying figures")


def _check_cagr(d, base, nums, pool_all, config, collector) -> None:
    span = parse_span(d.label) or parse_span(d.col_label) or parse_span(d.context)
    if span is None:
        return  # never treat a CAGR as one-year growth
    start_p, end_p, years = span
    ends = _base_candidates(base, d.slide_number, pool_all, end_p)
    starts = _base_candidates(base, d.slide_number, pool_all, start_p)
    if ends and starts:
        _flag_growth(d, ends, starts, years, config, collector, f"{years}-year CAGR of the underlying figures")
