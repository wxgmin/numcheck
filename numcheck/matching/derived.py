"""Verify derived percentages against the model when no cell stores them.

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
from numcheck.matching.normalizer import Comparison, canonical_model, deck_interval
from numcheck.models import DeckNumber, Match, ModelValue, SlideInfo
from numcheck.periods import parse_span, period_affinity, previous_period

log = logging.getLogger(__name__)

_GROWTH = {"growth", "cagr"}
_NOISE = _GROWTH | {"rate", "annual", "annualized", "compound", "yoy", "year", "y", "over", "grew", "increase",
                    "increased", "up", "rise", "rose"}


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
            label = mv.named_range or mv.row_label
            if not label:
                continue
            toks = frozenset(split_label(label, config).metric) - _UNIT_WORDS
            if toks:
                self.by_tokens.setdefault(toks, []).append(mv)

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
        vals = {round(canonical_model(mv, self.config.unit_scales)[1], 6) for mv in top}
        if len(vals) != 1 and self.context:
            # "US specialty segment" slide: the cells on the "US Specialty" sheet are the ones meant.
            ctx = self.context.lower()
            named = [mv for mv in top if mv.sheet_name and re.search(
                r"\b" + re.escape(mv.sheet_name.lower()) + r"\b", ctx)]
            if named:
                top = named
                vals = {round(canonical_model(mv, self.config.unit_scales)[1], 6) for mv in top}
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
        return m.deck.unit in ("percent", "none") and "%" in m.deck.raw_text and _growth_intent(m.deck) != 0

    # Unmatched percentages, plus ambiguous ones that are clearly changes/growth rates: the exact
    # recomputation from the model beats a tie between similarly labelled growth cells.
    targets = [m for m in matches if (m.status == "unmatched" and (
        is_delta(m) or (m.deck.unit in ("percent", "none") and "%" in m.deck.raw_text)))
        or (m.status == "ambiguous" and (is_delta(m) or growthy(m)))]
    # Ranges, approximations and targets/guidance are forward-looking statements: never recompute them.
    targets = [m for m in targets if not m.deck.is_range and m.deck.comparator == "="
               and "guidance" not in (m.deck.qualifiers or [])]
    if not targets or not model_values:
        return 0
    lookup = _Lookup(model_values, config)
    hints = {s.slide_number: s.period_hint for s in slides or [] if s.period_hint}
    changed = 0
    for m in targets:
        lookup.context = m.deck.slide_context or m.deck.slide_title or ""
        try:
            ok = _verify_delta(m, lookup, config, hints) if is_delta(m) else _verify_one(m, lookup, config, hints)
            if ok:
                changed += 1
        except (ValueError, ZeroDivisionError, OverflowError):
            log.debug("derived verification failed for %s", m.deck.raw_text, exc_info=True)
    return changed


_UP = re.compile(r"\b(grew|grow(?:th|s|ing)?|increas(?:e|ed|es|ing)|rose|rising|up|gain(?:ed)?|expand(?:ed|ing)? by|"
                 r"improv(?:e|ed|ing) by|cagr|y/?y|yoy)\b[^.;:]{0,12}$", re.I)
_DOWN = re.compile(r"\b(declin(?:e|ed|es|ing)|decreas(?:e|ed|es|ing)|fell|falling|down|dropp?(?:ed)?|"
                   r"contract(?:ed|ing)? by|shrank)\b[^.;:]{0,12}$", re.I)


def _growth_intent(d: DeckNumber) -> int:
    """+1 growth wording before the number, -1 decline wording, 0 none ("expanded to 23%" is a level)."""
    if d.char_start is None or d.char_start < 0 or not d.context:
        return 0
    window = d.context[max(0, d.char_start - 40):d.char_start]
    if re.search(r"\bto\s*$", window, re.I):
        return 0
    if _DOWN.search(window):
        return -1
    if _UP.search(window):
        return 1
    return 0


def _verify_one(m: Match, lookup: _Lookup, config, hints: dict[int, str]) -> bool:
    d = m.deck
    text = f"{d.label} {d.context}" if d.label else d.context
    parts = split_label(d.label or d.context, config)
    toks = set(parts.metric)
    period = d.period or hints.get(d.slide_number, "") or config.base_period
    intent = _growth_intent(d)
    if intent and not toks & _GROWTH:
        toks |= {"growth"}
    already_signed = "decline" in {q.lower() for q in d.qualifiers} or d.value < 0
    sign = -1.0 if (intent < 0 and not already_signed) else 1.0
    if toks & _GROWTH:
        base = frozenset(t for t in toks if t not in _NOISE) - _UNIT_WORDS
        if not base or base not in lookup.by_tokens:
            base = _metric_from_clause(d, lookup) or base
        if not base:
            return False
        if "cagr" in toks:
            span = parse_span(text)
            if not span:
                return False
            start_key, end_key, years = span
            a, b = lookup.value(base, start_key), lookup.value(base, end_key)
            if not a or not b or a[1] <= 0 or b[1] <= 0:
                return False
            expected = ((b[1] / a[1]) ** (1.0 / years) - 1.0) * 100.0
            return _apply(m, expected, b[0], f"{years}-year CAGR of {b[0].ref} over {a[0].ref}", config)
        if not period:
            return False
        cur, prev = lookup.value(base, period), lookup.value(base, previous_period(period))
        if not cur or not prev or prev[1] == 0:
            return False
        expected = sign * (cur[1] / prev[1] - 1.0) * 100.0
        return _apply(m, expected, cur[0], f"growth of {cur[0].ref} over {prev[0].ref}", config)

    if _verify_share(m, lookup, config, period):
        return True

    from numcheck.checks.arithmetic import RELATIONS

    for rel in RELATIONS:
        if rel.kind != "pct":
            continue
        if not any(set(s) <= toks for s in rel.results) or not period:
            continue
        num = _first(lookup, rel.numerator, period)
        den = _first(lookup, rel.denominator, period)
        if not num or not den or den[1] == 0:
            continue
        if num[0].scale != den[0].scale and not (num[0].scale_known and den[0].scale_known):
            continue   # $000 operating income over a revenue cell of unknown scale: not comparable
        expected = num[1] / den[1] * 100.0
        if abs(expected) > 1000.0:
            continue   # an implausible margin means the operands are not what we think

        return _apply(m, expected, num[0], f"{rel.name} = {num[0].ref} / {den[0].ref}", config)
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
        if num and den and den[1] and num[0].unit == den[0].unit:
            expected = num[1] / den[1] * 100.0
            _dim, lo, hi = deck_interval(d, config)
            if lo - 1e-9 <= expected <= hi + 1e-9:
                return _apply(m, expected, num[0], f"share = {num[0].ref} / {den[0].ref}", config)
    return False


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


_QOQ_RE = re.compile(r"\b(q\s?o\s?q|q/q|sequential|vs\.?\s+prior\s+quarter|quarter[- ]on[- ]quarter)\b", re.I)
_YOY_RE = re.compile(r"\b(y\s?o\s?y|y/y|year[- ](?:on|over)[- ]year|vs\.?\s+prior\s+year)\b", re.I)


def _verify_delta(m: Match, lookup: _Lookup, config, hints: dict[int, str]) -> bool:
    """bps / percentage-point changes: level(period) - level(prior period) of the same metric."""
    from numcheck.periods import parse_key, previous_quarter

    d = m.deck
    text = " ".join(x for x in (d.label, d.row_label, d.col_label, d.context) if x)
    base = frozenset(t for t in split_label(d.row_label or d.label or d.context, config).metric
                     if t not in _CHANGE_WORDS) - _UNIT_WORDS
    if not base or base not in lookup.by_tokens:
        base = _metric_from_clause(d, lookup) or base
    if not base:
        return False
    units = ("percent", "bps")
    period = d.period or hints.get(d.slide_number, "") or config.base_period
    known = lookup.periods_of(base, units)
    if not known:
        return False
    if not period or not any(period_affinity(period, k) for k in known):
        period = known[-1]   # no stated period: the latest period the model has for this metric
    p = parse_key(period)
    qoq = bool(_QOQ_RE.search(text)) or (p is not None and p.part.startswith("q") and not _YOY_RE.search(text))
    prior = previous_quarter(period) if qoq else previous_period(period)
    if not prior:
        return False
    cur, prev = lookup.value(base, period, units), lookup.value(base, prior, units)
    if not cur or not prev:
        return False
    expected = cur[1] - prev[1]          # percentage points (canonical percent)
    return _apply(m, expected, cur[0],
                  f"change in {cur[0].ref} vs {prev[0].ref} ({'QoQ' if qoq else 'YoY'})", config)


def _first(lookup: _Lookup, token_sets, period: str):
    for s in token_sets:
        hit = lookup.value(frozenset(s), period)
        if hit:
            return hit
    return None


def _apply(m: Match, expected: float, source: ModelValue, how: str, config) -> bool:
    if not math.isfinite(expected):
        return False
    from numcheck.matching.normalizer import canonical_deck, parsed_from_deck

    _dim, lo, hi = deck_interval(m.deck, config)
    deck_val = canonical_deck(parsed_from_deck(m.deck), config.unit_scales)[1]   # bps -> percent points
    if "valence" in (m.deck.qualifiers or []) and expected * deck_val < 0:
        expected = -expected   # "improved by 120bps" of a combined ratio is a fall: compare the magnitude
    tol = max((hi - lo) / 2.0, 1e-9)
    ok = lo - 1e-9 <= expected <= hi + 1e-9
    m.comparison = Comparison(
        ok=ok, dimension="percent", deck_value=deck_val, model_value=expected, tolerance=tol,
        difference=deck_val - expected, interpretation=f"derived: {how}",
        rounding_rel=abs(deck_val - expected) / max(abs(expected), 1e-12),
    )
    m.model = source
    m.method = "derived"
    m.status = "matched" if ok else "mismatch"
    m.confidence = 0.8 if ok else 0.75
    m.note = f"derived from model: {how}"
    return True


__all__ = ["verify_derived", "DeckNumber"]
