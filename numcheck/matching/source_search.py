"""Evidence discovery for unresolved slide figures."""
from __future__ import annotations

import heapq
import re
from dataclasses import replace

from numcheck.llm import resolve_ambiguous
from numcheck.matching.index import DECK_DECORATION, ModelIndex, covenant_words, expresses_change, stat_groups
from numcheck.matching.label_matcher import normalize_qualifiers, qualifier_factor
from numcheck.matching.llm_matcher import MAX_CANDIDATES, build_entry, mask_digits
from numcheck.matching.normalizer import compare, dimensions_compatible
from numcheck.models import DeckNumber, Match, ModelValue, ReviewWarning, Source
from numcheck.periods import period_affinity

UNRESOLVED = frozenset({"unmatched", "ambiguous", "matched_value_only"})
_GROWTH_DRIVER = re.compile(r"^(?:led by|driven by|contributed by)\s+(.+)$", re.IGNORECASE)


def _source_label(deck: DeckNumber) -> str:
    label = (deck.label or " ".join(filter(None, (deck.row_label, deck.col_label, deck.chart_series_name)))
             or deck.context[:160]).strip()
    if deck.unit == "percent" and "change" in deck.qualifiers and not expresses_change(label):
        driver = _GROWTH_DRIVER.fullmatch(label)
        if driver:
            return f"{driver[1]} growth"
    return label


def _is_change(deck: DeckNumber, text: str) -> bool:
    return "change" in deck.qualifiers or expresses_change(text)


def _rank_sources(deck: DeckNumber, index: ModelIndex):
    """Rank by label evidence only. Keep the strongest rivals even when they disagree numerically."""
    label = _source_label(deck)
    parts = index.parts(label)
    if not parts.tokens:
        return [], False
    query = index.query(parts.tokens)
    deck_period = deck.period or parts.period
    deck_qualifiers = (parts.qualifiers | normalize_qualifiers(deck.qualifiers, index.config)) - {"change"}
    change = _is_change(deck, label)
    positions = {i for token in parts.tokens for i in index.postings.get(token, ())}
    eligible = []
    for i in positions:
        value = index.values[i]
        affinity = period_affinity(deck_period, index.periods[i])
        if (value.is_error or not dimensions_compatible(deck.unit, value.unit)
                or (deck.currency and value.currency and deck.currency != value.currency)
                or affinity == 0 or (change and not index.is_change(i))):
            continue
        qualifiers = qualifier_factor(deck_qualifiers, index.quals[i] - {"change"})
        if qualifiers <= 0.3 or not (value.row_label or value.named_range or value.col_label):
            continue
        score, _ = index.label_score(query, i)
        score *= qualifiers * (affinity if affinity is not None else 0.8)
        if score >= 0.16:
            eligible.append((score, not value.hidden, value.sheet_priority or 0.0, -i))
    return heapq.nlargest(MAX_CANDIDATES, eligible), len(eligible) > MAX_CANDIDATES


def _options(index, ranked):
    return [(index.values[-i], index.periods[-i]) for _, _, _, i in ranked]


def possible_sources(deck: DeckNumber, index: ModelIndex) -> list[tuple[ModelValue, str]]:
    ranked, _ = _rank_sources(deck, index)
    return _options(index, ranked)


def suggest_sources(targets, config, warnings: list[ReviewWarning]) -> dict[str, str]:
    """Submit bounded shortlists together using opaque references and digit-masked source names."""
    entries, refs = [], {}
    for entry_id, deck, candidates in targets:
        entry = build_entry(entry_id, deck, _source_label(deck), deck.period, candidates)
        refs[entry_id] = {}
        for i, candidate in enumerate(entry["candidates"]):
            handle = f"candidate-{i + 1}"
            refs[entry_id][handle] = candidate["ref"]
            candidate["ref"] = handle
            for field in ("sheet", "named_range"):
                candidate[field] = mask_digits(candidate[field])
        entries.append(entry)
    return {entry_id: refs.get(entry_id, {}).get(chosen, "")
            for entry_id, chosen in resolve_ambiguous(entries, config, warnings).items()}


def _verified_source(match, index, ranked, truncated, config, suggested):
    """Promote a unique, corroborated source. A suggestion alone never changes a finding."""
    deck = match.deck
    if not ranked or deck.is_range or deck.comparator != "=" or "guidance" in deck.qualifiers:
        return match
    score, _, _, position = ranked[0]
    if score < 0.76 or (len(ranked) > 1 and score - ranked[1][0] < 0.12):
        return match
    i = -position
    value, period = index.values[i], index.periods[i]
    if value.hidden or (value.sheet_priority is not None and value.sheet_priority < 1.0):
        return match
    deck_label = _source_label(deck)
    deck_parts = index.parts(deck_label)
    label_views = [index.parts(label) for label in (value.row_label, value.named_range) if label]
    exact = any(deck_parts.tokens == parts.tokens for parts in label_views)
    ai_supported = bool(not truncated and suggested == value.ref)
    if not exact:
        context_tokens = index.tokens_of(deck.slide_context or deck.slide_title)
        cell_context = index.context_entities(i) | index.col_tokens(i)
        if not ai_supported or not any(
            parts.tokens & deck_parts.tokens
            and parts.tokens - deck_parts.tokens <= context_tokens | DECK_DECORATION
            and deck_parts.tokens - parts.tokens <= cell_context | DECK_DECORATION
            for parts in label_views
        ):
            return match
    deck_text = f"{deck_label} {deck.row_label} {deck.col_label}"
    if (stat_groups(deck_text) != index.stats(i)
            or _is_change(deck, deck_text) != index.is_change(i)
            or covenant_words(deck_text) != index.covenant(i)):
        return match
    entity_context = deck_parts.tokens | index.tokens_of(deck.slide_context or deck.slide_title)
    if index.col_entities(i) - entity_context:
        return match
    deck_period = deck.period or deck_parts.period
    if (deck_period or period) and period_affinity(deck_period, period) != 1.0:
        return match
    deck_qualifiers = deck_parts.qualifiers | normalize_qualifiers(deck.qualifiers, config)
    if deck_qualifiers - {"change"} != index.quals[i] - {"change"}:
        return match
    comparison = compare(deck, value, config)
    if comparison is None or not comparison.ok or comparison.inferred:
        return match
    confidence = min(0.8, deck.confidence) if deck.source == Source.OCR else 0.8
    return replace(match, model=value, status="matched", method="source_ai" if not exact else "source_search",
                   confidence=confidence, score=score, comparison=comparison,
                   note="Workbook label, period and displayed value corroborate the source.")


def review_sources(matches, index, config, warnings, ai_config=None):
    """Search every unresolved figure, then recheck only independently corroborated sources."""
    results = list(matches)
    pending, rankings = {}, {}
    precise = replace(config, relative_tolerance=0.0, absolute_tolerance=0.0)
    for pos, match in enumerate(matches):
        if match.status not in UNRESOLVED:
            continue
        deck = match.deck
        key = (_source_label(deck), deck.period, deck.unit, deck.currency, tuple(deck.qualifiers))
        rank = rankings.get(key)
        if rank is None:
            rank = rankings[key] = _rank_sources(deck, index)
        ranked, truncated = rank
        options = _options(index, ranked)
        updated = replace(match, source_options=options)
        results[pos] = _verified_source(updated, index, ranked, truncated, precise, "")
        if options and results[pos].status in UNRESOLVED:
            pending[str(pos)] = (ranked, truncated)
    suggestions = {}
    if ai_config is not None and pending:
        suggestions = suggest_sources(
            [(key, results[int(key)].deck, results[int(key)].source_options) for key in pending],
            ai_config, warnings,
        )
        for key, ref in suggestions.items():
            if not ref or key not in pending:
                continue
            pos = int(key)
            updated = replace(results[pos], source_suggestion=ref)
            results[pos] = _verified_source(updated, index, *pending[key], precise, ref)
    checked = [m for original, m in zip(matches, results) if original.status in UNRESOLVED]
    return results, {
        "searched": len(checked),
        "with_candidates": sum(bool(m.source_options) for m in checked),
        "verified": sum(m.status == "matched" for m in checked),
        "suggested": sum(bool(ref) for ref in suggestions.values()),
        "remaining": sum(m.status in UNRESOLVED for m in results),
    }
