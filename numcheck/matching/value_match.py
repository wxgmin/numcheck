"""Deterministic value candidate generation and proximity scoring.

``ValueIndex`` answers "which model cells could this displayed deck number be?"
without scanning every cell: model values are kept in two sorted arrays (canonical
value and raw cell value) and queried with the deck number's display interval.
Every hit is then verified with :func:`numcheck.matching.normalizer.compare`, so
the index only prunes; it never decides.
"""

from __future__ import annotations

import bisect
import math
import re

from numcheck.config import Config
from numcheck.matching.normalizer import (
    ParsedNumber,
    canonical_deck,
    canonical_model,
    compare,
    deck_interval,
    dimensions_compatible,
    parsed_from_deck,
    relative_tolerance,
    significant_digits,
    values_match,
)
from numcheck.models import DeckNumber, ModelValue

_NUM_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+")
MAX_HITS = 60


def value_similarity(
    deck: DeckNumber,
    parsed: ParsedNumber,
    mv: ModelValue,
    config: Config,
) -> float:
    """Soft 0..1 score, high when values are close but never fully zero for a matchable dimension."""
    d_canon = canonical_deck(parsed, config.unit_scales)
    m_canon = canonical_model(mv, config.unit_scales)
    if d_canon[0] != m_canon[0]:
        return 0.0
    rel = abs(d_canon[1] - m_canon[1]) / max(abs(m_canon[1]), abs(d_canon[1]), 1e-12)
    return 1.0 / (1.0 + rel * 10.0)


def value_candidates(
    deck: DeckNumber,
    parsed: ParsedNumber,
    model_values: list[ModelValue],
    config: Config,
) -> list[tuple[ModelValue, float]]:
    """Return model values within tolerance, scored by numeric proximity (1 = exact)."""
    d_canon = canonical_deck(parsed, config.unit_scales)
    out: list[tuple[ModelValue, float]] = []
    for mv in model_values:
        m_canon = canonical_model(mv, config.unit_scales)
        if d_canon[0] != m_canon[0]:
            continue
        if not values_match(d_canon, m_canon, parsed, config.relative_tolerance, config.absolute_tolerance):
            continue
        rel = abs(d_canon[1] - m_canon[1]) / max(abs(m_canon[1]), abs(d_canon[1]), 1e-12)
        tol = relative_tolerance(parsed, config.relative_tolerance)
        score = max(0.0, 1.0 - rel / tol)
        out.append((mv, score))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def significant_digits_of(deck: DeckNumber) -> int:
    """Significant digits of the displayed number (``raw_text``), the evidence value-only matching needs."""
    m = _NUM_RE.search(deck.raw_text or "")
    if m:
        return significant_digits(m.group(0))
    return max(int(deck.significant or 1), 1)


def _raw_value(mv: ModelValue) -> float:
    """The number as a person would read the cell (percent cells in points)."""
    if mv.unit == "percent":
        if mv.pct_basis == "points":
            return mv.value
        if mv.pct_basis == "ratio":
            return mv.value * 100.0
        return mv.value * 100.0 if abs(mv.value) <= 1.5 else mv.value
    return mv.value


class ValueIndex:
    """Sorted arrays of canonical and raw model values for interval lookups."""

    def __init__(self, values: list[ModelValue], config: Config):
        self.values = values
        self.config = config
        canon: list[tuple[float, int]] = []
        raw: list[tuple[float, int]] = []
        for i, mv in enumerate(values):
            v = mv.value
            if mv.is_error or v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            try:
                canon.append((canonical_model(mv, config.unit_scales)[1], i))
                raw.append((_raw_value(mv), i))
            except Exception:  # noqa: BLE001 - one odd cell must not break the index
                continue
        canon.sort()
        raw.sort()
        self._ckeys = [k for k, _ in canon]
        self._cidx = [i for _, i in canon]
        self._rkeys = [k for k, _ in raw]
        self._ridx = [i for _, i in raw]
        self._cache: dict[tuple, list[tuple[int, object]]] = {}

    def _range(self, keys: list[float], idx: list[int], lo: float, hi: float) -> list[int]:
        a = bisect.bisect_left(keys, lo)
        b = bisect.bisect_right(keys, hi)
        if b - a > 5000:   # a hopelessly common value (0, 1, ...) is no evidence at all
            return idx[a:a + 5000]
        return idx[a:b]

    def hits(self, deck: DeckNumber) -> list[tuple[int, object]]:
        """Cells whose value is consistent with *deck* (verified by ``compare``).

        Returns ``[(cell_index, Comparison)]`` ordered by closeness, capped at ``MAX_HITS``.
        """
        cfg = self.config
        ck = (deck.raw_text, deck.value, deck.unit, deck.scale, deck.decimals, deck.currency, deck.comparator)
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            dim, lo, hi = deck_interval(deck, cfg)
        except Exception:  # noqa: BLE001
            return []
        p = parsed_from_deck(deck)
        slack_c = cfg.relative_tolerance * max(abs(lo), abs(hi)) + cfg.absolute_tolerance + 1e-9 * max(abs(lo), abs(hi), 1.0)
        found = set(self._range(self._ckeys, self._cidx, lo - slack_c, hi + slack_c))
        tol_raw = 0.5 * 10.0 ** (-p.decimals) if p.decimals is not None and p.decimals >= 0 else 1e-9 * max(abs(p.value), 1.0)
        tol_raw = 2.0 * tol_raw + cfg.relative_tolerance * abs(p.value) + cfg.absolute_tolerance
        if p.comparator == "~":
            tol_raw = max(tol_raw, cfg.approx_relative * abs(p.value))
        found |= set(self._range(self._rkeys, self._ridx, p.value - tol_raw, p.value + tol_raw))
        out: list[tuple[int, object]] = []
        for i in found:
            mv = self.values[i]
            if not dimensions_compatible(deck.unit, mv.unit):
                continue
            c = compare(deck, mv, cfg)
            if c is not None and c.ok:
                out.append((i, c))
        out.sort(key=lambda t: (t[1].inferred, abs(t[1].difference), t[0]))
        out = out[:MAX_HITS]
        if len(self._cache) < 50000:
            self._cache[ck] = out
        return out
