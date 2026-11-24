"""Verify derived growth rates and ratios against their workbook inputs.

Decks routinely show "Revenue grew 11%", "EBITDA margin of 22%" or "FY22-24 CAGR
of 10%" while the model only stores the underlying revenue and EBITDA lines. Such
numbers would otherwise end "unmatched" (unverifiable). This pass recomputes them
deterministically from the model cells:

* growth   = current / prior - 1            (prior = periods.previous_period)
* CAGR     = (end / start) ** (1 / n) - 1   (span parsed from the label/context)
* margins  = numerator / denominator        (arithmetic.RELATIONS, pct kind)

A match upgrades the deck number to status "matched" (method "derived"); a clear
disagreement becomes a "mismatch" whose expected value is the recomputed figure.
Ambiguous inputs (several distinct candidate values) are left untouched.
"""

from __future__ import annotations

import logging
import math
import re

from numcheck.matching.label_matcher import split_label
from numcheck.matching.normalizer import Comparison, canonical_model, deck_interval, dimensions_compatible
from numcheck.models import DeckNumber, Match, ModelValue, SlideInfo
from numcheck.periods import Period, find_periods, parse_key, parse_span, period_affinity, previous_period

log = logging.getLogger(__name__)

_GROWTH = {"growth", "cagr"}
_NOISE = _GROWTH | {"rate", "annual", "annualized", "annually", "annum", "compound", "compounded",
                    "yoy", "year", "yearly", "y", "over", "grew", "increase", "increased", "up", "rise", "rose",
                    "from", "between", "through"}


_CHANGE_WORDS = {"change", "chg", "delta", "qoq", "yoy", "growth", "expansion", "contraction", "increase", "decrease",
                 "improvement", "vs", "prior", "bps", "q", "y", "sequential", "variance", "var", "in", "of"}
_LEVEL_UNITS = ("currency", "none", "count")
_UNIT_WORDS = frozenset({"bps", "bp", "pct", "percent", "pp", "ppts", "x", "usd", "eur", "gbp", "m", "mm", "bn", "k"})


class _Lookup:
    """model metric tokens -> cells, built once."""

    def __init__(self, values: list[ModelValue], config):
        self.by_tokens: dict[frozenset[str], list[ModelValue]] = {}
        self.config = config
        self.context = ""      # slide title/subtitle of the number being verified
        for mv in values:
            if mv.is_error or mv.unit not in _LEVEL_UNITS + ("percent", "bps", "multiple"):
                continue
            keys = set()
            for label in filter(None, (mv.named_range, mv.row_label)):
                parts = split_label(label, config)
                keys.add((parts.tokens | parts.qualifiers) - _UNIT_WORDS)
            for key in keys - {frozenset()}:
                self.by_tokens.setdefault(key, []).append(mv)

    def periods_of(self, tokens: frozenset[str], units: tuple[str, ...]) -> list[str]:
        from numcheck.periods import sort_key

        ps = {mv.period for mv in self.by_tokens.get(tokens, []) if mv.period and mv.unit in units}
        return sorted(ps, key=sort_key)

    def value(self, tokens: frozenset[str], period: str,
              units: tuple[str, ...] = _LEVEL_UNITS) -> tuple[ModelValue, float] | None:
        cands = []
        for mv in self.by_tokens.get(tokens, []):
            if mv.unit not in units:
                continue
            aff = period_affinity(period, mv.period)
            if aff is None or aff < 0.85:
                continue
            cands.append((aff, mv.sheet_priority, not mv.hidden, mv))
        if not cands:
            return None
        best_aff = max(c[0] for c in cands)
        top = [c[3] for c in cands if c[0] == best_aff]
        def fingerprints(cells):
            return {(canonical_model(mv, self.config.unit_scales), mv.currency) for mv in cells}

        vals = fingerprints(top)
        if len(vals) != 1 and self.context:
            # "US specialty segment" slide: the cells on the "US Specialty" sheet are the ones meant.
            ctx = self.context.lower()
            named = [mv for mv in top if mv.sheet_name and re.search(
                r"\b" + re.escape(mv.sheet_name.lower()) + r"\b", ctx)]
            if named:
                top = named
                vals = fingerprints(top)
        if len(vals) != 1:
            return None  # same label, same period, different values: ambiguous
        top.sort(key=lambda mv: (-mv.sheet_priority, mv.hidden))
        mv = top[0]
        return mv, canonical_model(mv, self.config.unit_scales)[1]


def verify_derived(matches: list[Match], model_values: list[ModelValue], config,
                   slides: list[SlideInfo] | None = None) -> int:
    """Upgrade unmatched growth/CAGR/margin percentages using model cells. Returns count changed."""
    def is_delta(m: Match) -> bool:
        return "change" in {q.lower() for q in m.deck.qualifiers} and m.deck.unit in ("bps", "percent")

    def growthy(m: Match) -> bool:
        d = m.deck
        return d.unit == "percent" and (_growth_intent(d) != 0 or bool(_CAGR_WORDS.search(d.label)))

    # Unmatched percentages, plus ambiguous ones that are clearly changes/growth rates: the exact
    # recomputation from the model beats a tie between similarly labelled growth cells.
    targets = [m for m in matches if (m.status in ("unmatched", "matched_value_only") and (
        is_delta(m) or m.deck.unit in ("percent", "multiple")))
        or (m.status == "ambiguous" and (is_delta(m) or growthy(m)))]
    # Ranges, approximations and targets/guidance are forward-looking statements: never recompute them.
    # A projection tied to one model year ("projected to grow 8.0% in FY25E") is a model figure, though.
    targets = [m for m in targets if (not m.deck.is_range or m.deck.unit == "multiple") and m.deck.comparator == "="
               and ("guidance" not in (m.deck.qualifiers or [])
                    or re.fullmatch(r"(?:fy|cy|y)\d{2}", m.deck.period or ""))]
    if not targets or not model_values:
        return 0
    lookup = _Lookup(model_values, config)
    hints = {s.slide_number: s.period_hint for s in slides or [] if s.period_hint}
    verified_levels: dict[tuple[int, int], list[Match]] = {}
    for m in matches:
        if m.status == "matched" and m.model and m.deck.unit in _LEVEL_UNITS:
            verified_levels.setdefault((m.deck.slide_number, m.deck.shape_id), []).append(m)
    changed = 0
    for m in targets:
        lookup.context = m.deck.slide_context or m.deck.slide_title or ""
        try:
            nearby = verified_levels.get((m.deck.slide_number, m.deck.shape_id), [])
            if is_delta(m):
                # "+12.0%" / "grow 8.0%": first a percentage-point change of a rate, else a growth rate of a level
                ok = _verify_delta(m, lookup, config, hints) or (
                    m.deck.unit == "percent" and m.status != "mismatch"
                    and _verify_one(m, lookup, config, hints, nearby))
            else:
                ok = _verify_one(m, lookup, config, hints, nearby)
            if ok:
                changed += 1
        except (ValueError, ZeroDivisionError, OverflowError):
            log.debug("derived verification failed for %s", m.deck.raw_text, exc_info=True)
    return changed


_UP = re.compile(r"\b(grew|grow(?:th|s|ing)?|increas(?:e|ed|es|ing)|rose|rising|up|gain(?:ed)?|expand(?:ed|ing)? by|"
                 r"improv(?:e|ed|ing) by|compound(?:ed)?|climb(?:ed|ing)?|cagr|y/?y|yoy)\b[^.;:]{0,12}$", re.I)
_DOWN = re.compile(r"\b(declin(?:e|ed|es|ing)|decreas(?:e|ed|es|ing)|fell|falling|down|dropp?(?:ed)?|"
                   r"contract(?:ed|ing)? by|shrank|deteriorat(?:ed|ing)?|erod(?:ed|ing)?|slipp(?:ed|ing)?)\b[^.;:]{0,12}$", re.I)

_CAGR_WORDS = re.compile(r"\b(?:cagr|compounded|compound\s+annual|annuali[sz]ed|"
                         r"per\s+(?:annum|year)|annually|each\s+year)\b", re.I)
_ANNUAL_GROWTH = re.compile(r"\bannual\s+(?:growth|decline|rate\s+of\s+(?:growth|decline))\b", re.I)
_AVERAGE_ANNUAL = re.compile(r"\baverage\s+annual\b", re.I)
_N_YEAR = re.compile(r"\b([1-9]\d?)\s*[-– ]?\s*years?\b", re.I)
_SENTENCE = re.compile(r"(?:[^;!?\n.]|(?<=\d)\.(?=\d))+")


def _growth_intent(d: DeckNumber) -> int:
    """+1 growth wording before the number, -1 decline wording, 0 none ("expanded to 23%" is a level)."""
    if d.char_start is None or d.char_start < 0 or not d.context:
        return 0
    window = d.context[max(0, d.char_start - 40):d.char_start]
    if re.search(r"\bto\s*$", window, re.I):
        return 0
    if d.unit == "percent" and (d.raw_text or "").lstrip("(").startswith("+"):
        return 1   # "led by Aerospace Components (+12.0%)": an explicitly signed percentage is a change
    if d.unit == "percent" and (d.raw_text or "").lstrip("(").startswith(("-", "−", "–")):
        return -1
    if _DOWN.search(window):
        return -1
    if _UP.search(window):
        return 1
    return 0


def _compatible_inputs(a: ModelValue, b: ModelValue, config) -> bool:
    adim = canonical_model(a, config.unit_scales)[0]
    bdim = canonical_model(b, config.unit_scales)[0]
    return (dimensions_compatible(adim, bdim)
            and not (a.currency and b.currency and a.currency != b.currency)
            and (a.scale == b.scale or (a.scale_known and b.scale_known)))


def _same_series(a: ModelValue, b: ModelValue) -> bool:
    if (a.workbook, a.sheet_name) != (b.workbook, b.sheet_name):
        return False
    return (a.row == b.row if a.row and b.row else a.row_label == b.row_label)


def _apply_growth(m, start, end, config, *, years=1, name="growth", decline=False, confirm_only=False) -> bool:
    if not start or not end:
        return False
    a, first = start
    b, last = end
    if not _same_series(a, b) or not _compatible_inputs(a, b, config) or first <= 0 or last < 0:
        return False
    pa, pb = parse_key(a.period), parse_key(b.period)
    if pa and pb and pa.basis != pb.basis and "y" not in (pa.basis, pb.basis):
        return False
    expected = ((last / first) ** (1 / years) - 1) * 100
    if decline and m.deck.value >= 0:
        expected = -expected
    return _apply(m, expected, b, f"{name} of {b.ref} over {a.ref}", config,
                  other=a, confirm_only=confirm_only)


def _projected_cagr(m: Match, base: frozenset[str], nearby: list[Match], config) -> bool:
    """Use a verified actual and forecast stated together in the same text shape."""
    d = m.deck
    if d.char_start is None:
        return False
    forecasts = []
    actuals = []
    for level in nearby:
        cell = level.model
        label = split_label(level.deck.label, config)
        if cell is None or (label.tokens | label.qualifiers) != base:
            continue
        periods = [p for p, _, _ in find_periods(level.deck.context)
                   if p.is_full_year and p.key == level.deck.period]
        for p in set(periods):
            if p.status in ("e", "p", "f") and level.deck.context == d.context:
                if level.deck.char_start is not None and 0 <= level.deck.char_start < d.char_start:
                    forecasts.append((p, cell))
            elif p.status == "a" and level.deck.context != d.context:
                actuals.append((p, cell))

    if len(forecasts) != 1:
        return False
    end_period, end = forecasts[0]
    starts = {(p.key, cell.ref): (p, cell) for p, cell in actuals
              if p.year < end_period.year and _same_series(cell, end)}
    if len(starts) != 1:
        return False
    start_period, start = next(iter(starts.values()))
    years = end_period.year - start_period.year
    return _apply_growth(m, (start, canonical_model(start, config.unit_scales)[1]),
                         (end, canonical_model(end, config.unit_scales)[1]), config,
                         years=years, name=f"{years}-year CAGR (actual to forecast)", confirm_only=True)


def _verify_one(m: Match, lookup: _Lookup, config, hints: dict[int, str], nearby: list[Match]) -> bool:
    d = m.deck
    if d.is_range:
        # Range endpoints are only ever *confirmed* (an implied multiple of a named value), never flagged.
        return d.unit == "multiple" and _implied_multiple(
            m, lookup, config, d.period or hints.get(d.slide_number, "") or config.base_period)
    # Sentence punctuation bounds the claim while preserving decimal values and fiscal-year ranges.
    clause = next((piece.group() for piece in _SENTENCE.finditer(d.context)
                   if piece.start() <= d.char_start < piece.end()), "") if d.char_start is not None else ""
    text = " ".join(filter(None, (d.label, d.row_label, d.col_label, clause or d.context)))
    parts = split_label(d.label or d.context, config)
    toks = set(parts.tokens | parts.qualifiers)
    period = d.period or hints.get(d.slide_number, "") or config.base_period
    intent = _growth_intent(d)
    cagr_signal = bool(_CAGR_WORDS.search(text) or _ANNUAL_GROWTH.search(text))
    if (intent or cagr_signal) and not toks & _GROWTH:
        toks |= {"growth"}
    if d.unit != "multiple" and toks & _GROWTH:
        if _AVERAGE_ANNUAL.search(text) and not re.search(r"\b(?:cagr|compound(?:ed)?)\b", text, re.I):
            return False  # Arithmetic mean of annual changes and CAGR are different measures.
        base = frozenset(t for t in toks if t not in _NOISE) - _UNIT_WORDS
        if not base or base not in lookup.by_tokens:
            # "Aftermarket Services provides a recurring base, growing 8.2%": the metric is the model label
            # named in the sentence, not the whole phrase before the number.
            base = _metric_from_clause(d, lookup) or base
        if not base:
            return False
        span = parse_span(text)
        if span:
            start_key, end_key, years = span
            start, end = lookup.value(base, start_key), lookup.value(base, end_key)
            return _apply_growth(m, start, end, config, years=years if cagr_signal else 1,
                                 name=f"{years}-year CAGR" if cagr_signal else "cumulative growth",
                                 decline=intent < 0)
        if cagr_signal:
            duration = _N_YEAR.search(text)
            end_periods = {p.key: p for p, _, _ in find_periods(text) if p.is_full_year}
            if duration and len(end_periods) == 1:
                end_period = next(iter(end_periods.values()))
                years = int(duration.group(1))
                start_key = Period(end_period.basis, end_period.year - years).key
                start, end = lookup.value(base, start_key), lookup.value(base, end_period.key)
                return _apply_growth(m, start, end, config, years=years, name=f"{years}-year CAGR",
                                     decline=intent < 0)
            if _projected_cagr(m, base, nearby, config):
                return True
            # "revenue of $1,463m by FY27E, a 7.8% CAGR": a projection CAGR runs from the last actual year.
            if len(end_periods) == 1:
                end_period = next(iter(end_periods.values()))
                last_a = _last_actual(lookup, base)
                if last_a is not None and last_a.year < end_period.year:
                    years = end_period.year - last_a.year
                    start = lookup.value(base, last_a.key)
                    end = lookup.value(base, end_period.key)
                    return _apply_growth(m, start, end, config, years=years,
                                         name=f"{years}-year CAGR from the last actual year", confirm_only=True)
            return False
        if not period:
            return False
        if len({p.key for p, _, _ in find_periods(clause or text) if p.year is not None}) > 1:
            return False  # Multiple stated dates require a recognised range.
        cur, prev = lookup.value(base, period), lookup.value(base, previous_period(period))
        return _apply_growth(m, prev, cur, config, decline=intent < 0)

    if d.unit != "multiple" and _verify_share(m, lookup, config, period):
        return True
    if d.unit == "multiple" and (_table_ratio(m, lookup, config, period) or _implied_multiple(m, lookup, config, period)):
        return True

    from numcheck.checks.arithmetic import RELATIONS

    for rel in RELATIONS:
        if (rel.kind == "multiple") != (d.unit == "multiple"):
            continue
        # Require the complete label so a segment or adjusted result cannot pick up group inputs.
        if frozenset(toks) not in rel.results or not period:
            continue
        num = _first(lookup, rel.numerator, period)
        den = _first(lookup, rel.denominator, period)
        if (not num or not den or den[1] <= 0 or num[0].workbook != den[0].workbook
                or not _compatible_inputs(num[0], den[0], config)):
            continue
        ratio = num[1] / den[1]
        expected = ratio if rel.kind == "multiple" else (ratio - (rel.kind == "premium")) * 100.0
        formula = f"{rel.name} = {num[0].ref} / {den[0].ref}"
        if rel.kind == "premium":
            formula += " − 1"
        return _apply(m, expected, num[0], formula, config, other=den[0])
    return False


def _verify_share(m: Match, lookup: _Lookup, config, period: str) -> bool:
    """"Debt funds 62.4% of total sources": numerator named before, base named after "of".

    Only *confirms* (never flags): the numerator is inferred from the wording, so a disagreement
    more likely means we picked the wrong cells than that the deck is wrong.
    """
    d = m.deck
    if not d.context or d.char_end is None or d.char_end < 0:
        return False
    tail = re.match(r"\s*(?:of|to)\s+((?:the\s+)?[a-z][a-z &/-]{2,40})", d.context[d.char_end:d.char_end + 50], re.I)
    if not tail:
        return False
    words = re.findall(r"[a-z]+", tail.group(1).lower())
    base = None
    for k in range(len(words), 0, -1):
        key = frozenset(words[:k]) - _UNIT_WORDS - {"the"}
        if key in lookup.by_tokens:
            base = key
            break
    num_key = _metric_from_clause(d, lookup)
    if not base or not num_key or num_key == base:
        return False
    for per in ([period] if period else []) + [""]:
        num = lookup.value(num_key, per) if per else _any_value(lookup, num_key)
        den = lookup.value(base, per) if per else _any_value(lookup, base)
        if num and den and den[1] and _compatible_inputs(num[0], den[0], config):
            expected = num[1] / den[1] * 100.0
            _dim, lo, hi = deck_interval(d, config)
            if lo - 1e-9 <= expected <= hi + 1e-9:
                return _apply(m, expected, num[0], f"share = {num[0].ref} / {den[0].ref}", config, other=den[0])
    return False


_RATIO_HEADER_RE = re.compile(r"^\s*(?:x|×|/|÷|as\s+a\s+multiple\s+of|multiple\s+of)\s+(.+)$", re.I)
_RANGE_ROLE = {"low": re.compile(r"\b(?:low|min(?:imum)?|bottom)\b", re.I),
               "high": re.compile(r"\b(?:high|max(?:imum)?|top)\b", re.I)}
_GENERIC_WORDS = {"implied", "implies", "implying", "value", "range", "of", "the", "and", "at", "x", "times",
                  "total", "per", "share", "fy", "ltm", "ntm"}


def _phrase_metric(phrase: str, lookup: _Lookup, config) -> tuple[frozenset[str] | None, str]:
    """(model metric key, period) named by a short phrase ("FY24A Adj. EBITDA" -> adjusted ebitda, fy24)."""
    if not phrase:
        return None, ""
    periods = [p.key for p, _, _ in find_periods(phrase)]
    parts = split_label(phrase, config)
    key = frozenset((parts.tokens | parts.qualifiers) - _UNIT_WORDS)
    if key in lookup.by_tokens:
        return key, (periods[0] if periods else "")
    best = None
    for k in lookup.by_tokens:
        if k and k <= key and (best is None or len(k) > len(best)):
            best = k
    return best, (periods[0] if periods else "")


def _table_ratio(m: Match, lookup: _Lookup, config, period: str) -> bool:
    """Leverage tables: row "Term Loan B" x column "x FY24A Adj. EBITDA" = Term Loan B / FY24A Adj. EBITDA."""
    d = m.deck
    head = _RATIO_HEADER_RE.match(d.col_label or "")
    if not head or not d.row_label:
        return False
    den_key, den_period = _phrase_metric(head.group(1), lookup, config)
    num_key, _ = _phrase_metric(d.row_label, lookup, config)
    if not den_key or not num_key or den_key == num_key:
        return False
    per = den_period or period
    num = lookup.value(num_key, per) if per else _any_value(lookup, num_key)
    num = num or _any_value(lookup, num_key)
    den = lookup.value(den_key, per) if per else _any_value(lookup, den_key)
    if not num or not den or den[1] <= 0 or not _compatible_inputs(num[0], den[0], config):
        return False
    return _apply(m, num[1] / den[1], num[0], f"{num[0].ref} / {den[0].ref}", config, other=den[0], confirm_only=True)


def _implied_multiple(m: Match, lookup: _Lookup, config, period: str) -> bool:
    """"Reference range implies 11.1x-13.4x FY24A Adjusted EBITDA": a value named in the sentence over
    the metric named after the multiple. Confirms only, and only when exactly one named cell fits."""
    d = m.deck
    if not d.context or d.char_end is None or d.char_end < 0:
        return False
    tail = d.context[d.char_end:d.char_end + 60]
    tail = re.split(r"\b(?:of|and|at|with|for)\b|[,;(]", tail, maxsplit=1)[0]
    den_key, den_period = _phrase_metric(tail, lookup, config)
    if not den_key:
        return False
    den = lookup.value(den_key, den_period or period) if (den_period or period) else _any_value(lookup, den_key)
    if not den or den[1] <= 0:
        return False
    head = d.context[:d.char_start]
    clause = re.split(r"(?<!\d)\.(?!\d)|[;\n]", head)[-1]   # "11.1x" is a number, not a sentence end
    words = {w for w in re.findall(r"[a-z]+", clause.lower()) if len(w) > 2} - _GENERIC_WORDS - set(den_key)
    role = _RANGE_ROLE.get(d.range_role) if d.is_range else None
    if not words and role is None:
        return False
    _dim, lo, hi = deck_interval(d, config)
    hits = []
    seen = set()
    for key, cells in lookup.by_tokens.items():
        for mv in cells:
            if mv.ref in seen or mv.unit not in ("currency", "none") or not _compatible_inputs(mv, den[0], config):
                continue
            seen.add(mv.ref)
            label_text = f"{mv.row_label} {mv.col_label}"
            label_words = set(re.findall(r"[a-z]+", label_text.lower()))
            if words and not words & label_words:
                continue
            if not words and not role.search(label_text):
                continue   # "11.1x-13.4x FY24A Adj. EBITDA" alone: only an explicit low / high cell can be the source
            ratio = canonical_model(mv, config.unit_scales)[1] / den[1]
            if lo - 1e-9 <= ratio <= hi + 1e-9:
                hits.append((mv, ratio))
    if role is not None and len(hits) > 1:
        hits = [h for h in hits if role.search(f"{h[0].row_label} {h[0].col_label}")] or hits
    if len(hits) != 1:
        return False
    mv, ratio = hits[0]
    return _apply(m, ratio, mv, f"{mv.ref} / {den[0].ref}", config, other=den[0], confirm_only=True)


def verify_pie_shares(matches: list[Match], model_values: list[ModelValue], config) -> int:
    """Doughnut / pie slices in percent ("Debt mix": Term Loan B 73.5%, Senior notes 26.5%).

    When every slice's category is a model figure, each slice is that figure's share of their sum.
    Confirms only: the chart may use a different base than the slices shown.
    """
    lookup = _Lookup(model_values, config)
    groups: dict[tuple, list[Match]] = {}
    for m in matches:
        d = m.deck
        if (getattr(d.source, "value", d.source) == "chart" and d.unit == "percent"
                and re.search(r"pie|doughnut", d.chart_type or "", re.I)):
            groups.setdefault((d.slide_number, d.shape_id, d.series_index), []).append(m)
    changed = 0
    for members in groups.values():
        if len(members) < 2 or not any(m.status in ("unmatched", "ambiguous") for m in members):
            continue
        cells = []
        for m in members:
            key, per = _phrase_metric(m.deck.col_label, lookup, config)
            per = per or m.deck.period
            hit = (lookup.value(key, per) if per else None) or _any_value(lookup, key) if key else None
            if not hit or hit[1] < 0:
                cells = []
                break
            cells.append(hit)
        if not cells or len({c[0].ref for c in cells}) != len(cells):
            continue
        if not all(_compatible_inputs(cells[0][0], c[0], config) for c in cells[1:]):
            continue
        total = sum(c[1] for c in cells)
        if total <= 0:
            continue
        for m, (mv, v) in zip(members, cells, strict=True):
            if m.status in ("unmatched", "ambiguous") and _apply(
                    m, v / total * 100.0, mv, f"{mv.ref} / sum of the chart's slices", config,
                    other=cells[0][0], confirm_only=True):
                changed += 1
    return changed


def verify_adjustments(matches: list[Match], model_values: list[ModelValue], config) -> int:
    """"FY24A adjustments total $19.3m" = Adjusted EBITDA - reported EBITDA (confirm only), then
    "$19.3m, or 7.6% of Adjusted EBITDA" = that amount / the base (confirm only)."""
    from numcheck.matching.normalizer import canonical_deck, parsed_from_deck

    lookup = _Lookup(model_values, config)
    changed = 0
    for m in matches:
        d = m.deck
        if m.status not in ("unmatched", "ambiguous") or d.unit != "currency" or d.is_range:
            continue
        text = f"{d.label} {d.slide_context or d.slide_title or ''}".lower()
        if not re.search(r"\badjustments?\b|\badd[- ]?backs?\b", text):
            continue
        metric = next((w for w in ("ebitda", "ebit", "net income", "eps", "earnings") if w in
                       f"{d.context} {d.slide_context or d.slide_title or ''}".lower()), None)
        if not metric:
            continue
        mt = frozenset(metric.split())
        adj_key = next((k for k in lookup.by_tokens if mt <= k and "adjusted" in k and len(k) == len(mt) + 1), None)
        rep_key = next((k for k in lookup.by_tokens if mt <= k and "reported" in k and len(k) == len(mt) + 1), None)
        rep_key = rep_key or (mt if mt in lookup.by_tokens else None)
        per = d.period
        if not adj_key or not rep_key or not per:
            continue
        a, r = lookup.value(adj_key, per), lookup.value(rep_key, per)
        if not a or not r or not _compatible_inputs(a[0], r[0], config):
            continue
        if _apply(m, a[1] - r[1], a[0], f"{a[0].ref} - {r[0].ref} (total adjustments)", config,
                  other=r[0], confirm_only=True):
            changed += 1
    # shares of a just-verified amount: "<amount> as % of <base>"
    for i, m in enumerate(matches):
        d = m.deck
        if m.status not in ("unmatched", "ambiguous") or d.unit != "percent" or " as % of " not in (d.label or ""):
            continue
        prev = next((p for p in reversed(matches[:i]) if p.deck.slide_number == d.slide_number
                     and p.deck.shape_id == d.shape_id and p.deck.paragraph_index == d.paragraph_index
                     and p.deck.char_start < d.char_start and p.status == "matched"), None)
        if prev is None or prev.deck.unit != "currency":
            continue
        base_key, base_period = _phrase_metric(d.label.split(" as % of ", 1)[1], lookup, config)
        if not base_key:
            continue
        base = lookup.value(base_key, base_period or d.period) if (base_period or d.period) else None
        if not base or base[1] <= 0:
            continue
        amount = canonical_deck(parsed_from_deck(prev.deck), config.unit_scales)[1]
        if _apply(m, amount / base[1] * 100.0, base[0], f"{prev.deck.raw_text} / {base[0].ref}", config,
                  other=base[0], confirm_only=True):
            changed += 1
    return changed


_ACTUAL_COL_RE = re.compile(r"(?:\d{2}|\d{4})\s*A\b|\bactual\b", re.I)


def _last_actual(lookup: _Lookup, key: frozenset[str]) -> Period | None:
    """Latest full-year period whose model column is marked as actual ("FY24A", "2024A", "Actual")."""
    best = None
    for mv in lookup.by_tokens.get(key, []):
        p = parse_key(mv.period) if mv.period else None
        if p is None or not p.is_full_year or p.year is None or not _ACTUAL_COL_RE.search(mv.col_label or ""):
            continue
        if best is None or p.year > best.year:
            best = p
    return best


def _any_value(lookup: _Lookup, key: frozenset[str]):
    """The single value of a period-less metric (S&U tables, bridges), or None when it is ambiguous."""
    cells = [mv for mv in lookup.by_tokens.get(key, []) if mv.unit in _LEVEL_UNITS]
    vals = {round(canonical_model(mv, lookup.config.unit_scales)[1], 6) for mv in cells}
    if len(vals) != 1:
        return None
    return cells[0], canonical_model(cells[0], lookup.config.unit_scales)[1]


def _metric_from_clause(d: DeckNumber, lookup: _Lookup) -> frozenset[str] | None:
    """Nearest model metric named earlier in the sentence ("Revenue fell ...; a decline of 10.3%").

    Picks the model label whose words all occur in the preceding text, preferring the one
    mentioned closest to the number (and the longer label on ties).
    """
    if not d.context:
        return None
    end = d.char_start if d.char_start and d.char_start > 0 else len(d.context)
    pre = d.context[max(0, end - 160):end].lower()
    words = re.findall(r"[a-z]+", pre)
    if not words:
        return None
    pre_toks = set(words) | {w.rstrip("s") for w in words}
    best, best_key = None, None
    for key in lookup.by_tokens:
        if not key or not key <= pre_toks:
            continue
        pos = max(pre.rfind(t) for t in key)
        k = (pos, len(key))
        if best_key is None or k > best_key:
            best, best_key = key, k
    return best


_SINCE_RE = re.compile(r"\b(?:since|from|vs\.?|versus|compared\s+(?:to|with)|over)\s+((?:FY|CY|Q[1-4]\s*)?'?\d{2,4}[AEFP]?)\b",
                       re.I)
_QOQ_RE = re.compile(r"\b(q\s?o\s?q|q/q|sequential|vs\.?\s+prior\s+quarter|quarter[- ]on[- ]quarter)\b", re.I)
_YOY_RE = re.compile(r"\b(y\s?o\s?y|y/y|year[- ](?:on|over)[- ]year|vs\.?\s+prior\s+year)\b", re.I)


def _verify_delta(m: Match, lookup: _Lookup, config, hints: dict[int, str]) -> bool:
    """bps / percentage-point changes: level(period) - level(prior period) of the same metric."""
    from numcheck.periods import previous_quarter

    d = m.deck
    text = " ".join(x for x in (d.label, d.row_label, d.col_label, d.context) if x)
    parts = split_label(d.row_label or d.label or d.context, config)
    base = (parts.tokens | parts.qualifiers) - _CHANGE_WORDS - _UNIT_WORDS
    if not base or (base not in lookup.by_tokens and not parts.qualifiers and not parts.entity):
        base = _metric_from_clause(d, lookup) or base
    if not base:
        return False
    units = ("percent", "bps")
    period = d.period or hints.get(d.slide_number, "") or config.base_period
    known = lookup.periods_of(base, units)
    if not known:
        return False
    if not period:
        period = known[-1]   # no stated period: the latest period the model has for this metric
    p = parse_key(period)
    qoq = bool(_QOQ_RE.search(text)) or (p is not None and p.part.startswith("q") and not _YOY_RE.search(text))
    prior = previous_quarter(period) if qoq else previous_period(period)
    since = _SINCE_RE.search(d.context or "")
    if since:
        # "Gross margin up 190bps since FY21A": the change runs from the named base period to the latest
        # actual (or the stated period), not over one year.
        base_period = next((pp for pp, _, _ in find_periods(since.group(1))), None)
        if base_period is not None and base_period.key != period:
            prior = base_period.key
            if not d.period:
                last_a = _last_actual(lookup, base)
                if last_a is not None:
                    period = last_a.key
    if not prior:
        return False
    cur, prev = lookup.value(base, period, units), lookup.value(base, prior, units)
    if not cur or not prev or not _same_series(cur[0], prev[0]):
        return False
    if d.unit == "percent":
        # A segment's "up 12%" can be its growth rate. It does not say its growth
        # rate rose by 12 percentage points.
        labels = (split_label(cell.row_label, config).tokens for cell in (cur[0], prev[0]))
        if any(tokens & _GROWTH for tokens in labels):
            return False
    expected = cur[1] - prev[1]          # percentage points (canonical percent)
    return _apply(m, expected, cur[0],
                  f"change in {cur[0].ref} vs {prev[0].ref} ({'QoQ' if qoq else 'YoY'})", config, other=prev[0])


def _first(lookup: _Lookup, token_sets, period: str):
    hits = [hit for key in token_sets if (hit := lookup.value(key, period))]
    if len({(hit[0].unit, hit[0].currency, hit[1]) for hit in hits}) != 1:
        return None
    return hits[0]


def _apply(m: Match, expected: float, source: ModelValue, how: str, config, *,
           other: ModelValue, confirm_only: bool = False) -> bool:
    if not math.isfinite(expected):
        return False
    from numcheck.matching.normalizer import canonical_deck, parsed_from_deck

    dim, lo, hi = deck_interval(m.deck, config)
    deck_val = canonical_deck(parsed_from_deck(m.deck), config.unit_scales)[1]   # bps -> percent points
    if "valence" in (m.deck.qualifiers or []) and expected * deck_val < 0:
        expected = -expected   # "improved by 120bps" of a combined ratio is a fall: compare the magnitude
    tol = max((hi - lo) / 2.0, 1e-9)
    ok = lo - 1e-9 <= expected <= hi + 1e-9
    if confirm_only and not ok:
        return False
    m.comparison = Comparison(
        ok=ok, dimension=dim, deck_value=deck_val, model_value=expected, tolerance=tol,
        difference=deck_val - expected, interpretation=f"derived: {how}",
        rounding_rel=abs(deck_val - expected) / max(abs(expected), 1e-12),
    )
    m.model = source
    m.derivation_inputs = (source, other)
    m.method = "derived"
    m.status = "matched" if ok else "mismatch"
    m.confidence = 0.8 if ok else 0.75
    m.note = f"derived from model: {how}"
    return True


__all__ = ["verify_derived", "DeckNumber"]
