"""Deterministic value candidate generation and proximity scoring."""

from __future__ import annotations

from numcheck.config import Config
from numcheck.matching.normalizer import (
    ParsedNumber,
    canonical_deck,
    canonical_model,
    relative_tolerance,
    values_match,
)
from numcheck.models import DeckNumber, ModelValue


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
