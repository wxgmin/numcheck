"""Evidence discovery for unresolved slide figures."""
from __future__ import annotations

import heapq

from numcheck.llm import resolve_ambiguous
from numcheck.matching.index import ModelIndex
from numcheck.matching.label_matcher import qualifier_factor
from numcheck.matching.llm_matcher import MAX_CANDIDATES, build_entry, mask_digits
from numcheck.matching.normalizer import dimensions_compatible
from numcheck.models import DeckNumber, ModelValue, ReviewWarning
from numcheck.periods import period_affinity


def possible_sources(deck: DeckNumber, index: ModelIndex) -> list[tuple[ModelValue, str]]:
    """Retrieve a bounded label shortlist independently of the displayed figure."""
    label = deck.label or " ".join((deck.row_label, deck.col_label, deck.chart_series_name))
    parts = index.parts(label)
    if not parts.tokens:
        return []
    query = index.query(parts.tokens)
    deck_period = deck.period or parts.period
    positions = {i for token in parts.tokens for i in index.postings.get(token, ())}
    eligible = []
    for i in positions:
        value = index.values[i]
        affinity = period_affinity(deck_period, index.periods[i])
        if (value.is_error or not dimensions_compatible(deck.unit, value.unit)
                or (deck.currency and value.currency and deck.currency != value.currency)
                or affinity == 0):
            continue
        qualifiers = qualifier_factor(parts.qualifiers, index.quals[i])
        if qualifiers <= 0.3 or not (value.row_label or value.named_range or value.col_label):
            continue
        score, _ = index.label_score(query, i)
        score *= qualifiers * (affinity if affinity is not None else 0.8)
        if score >= 0.16:
            eligible.append((score, not value.hidden, value.sheet_priority or 0.0, -i))
    return [(index.values[-i], index.periods[-i]) for _, _, _, i in heapq.nlargest(MAX_CANDIDATES, eligible)]


def suggest_source(deck, candidates, config, warnings: list[ReviewWarning]) -> str:
    """Ask for one selection using opaque references and digit-masked source names."""
    entry = build_entry("0", deck, deck.label, deck.period, candidates)
    refs = {}
    for i, candidate in enumerate(entry["candidates"]):
        handle = f"candidate-{i + 1}"
        refs[handle] = candidate["ref"]
        candidate["ref"] = handle
        for field in ("sheet", "named_range"):
            candidate[field] = mask_digits(candidate[field])
    chosen = resolve_ambiguous([entry], config, warnings).get("0", "")
    return refs.get(chosen, "")
