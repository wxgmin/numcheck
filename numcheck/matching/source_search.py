"""Evidence discovery for unresolved slide figures."""
from __future__ import annotations

import heapq
import re
from collections import Counter
from dataclasses import replace
from functools import lru_cache

from numcheck.config import Config
from numcheck.llm import resolve_ambiguous
from numcheck.matching.index import DECK_DECORATION, ModelIndex, covenant_words, expresses_change, stat_groups
from numcheck.matching.label_matcher import STRICT_QUALIFIERS, normalize_qualifiers, qualifier_factor, split_label
from numcheck.matching.llm_matcher import MAX_CANDIDATES, build_entry, mask_digits
from numcheck.matching.normalizer import Comparison, compare, dimensions_compatible
from numcheck.models import DeckNumber, Match, ModelValue, ReviewWarning, Source
from numcheck.periods import find_periods, period_affinity

UNRESOLVED = frozenset({"unmatched", "ambiguous", "matched_value_only"})
_GROWTH_DRIVER = re.compile(r"^(?:led by|driven by|contributed by)\s+(.+)$", re.IGNORECASE)
_SOURCE_QUALIFIERS = STRICT_QUALIFIERS | {"basic", "reported", "gaap", "non gaap", "same center", "comparable store"}
_SCENARIO_WORDS = r"actuals?|historical|budget(?:ed)?|forecast(?:ed)?|projected|estimated?"
_SCENARIO_BEFORE = re.compile(rf"\b({_SCENARIO_WORDS})\s*[-:(]?\s*$", re.IGNORECASE)
_SCENARIO_AFTER = re.compile(rf"^[\s(\-:]*({_SCENARIO_WORDS})\b", re.IGNORECASE)


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


@lru_cache(maxsize=32768)
def _period_scenario(period: str, *texts: str) -> str:
    if period:
        for text in texts:
            statuses = set()
            for item, start, end in find_periods(text or ""):
                if (period_affinity(period, item.key) or 0) < 0.85:
                    continue
                if item.status:
                    statuses.add("forecast" if item.status in {"e", "p", "f"} else item.status)
                    continue
                adjacent = _SCENARIO_AFTER.search(text[end:]) or _SCENARIO_BEFORE.search(text[:start])
                if adjacent:
                    word = adjacent[1].lower()
                    statuses.add("a" if word.startswith(("actual", "historical")) else
                                 "b" if word.startswith("budget") else "forecast")
            if statuses:
                return next(iter(statuses)) if len(statuses) == 1 else ""
    return ""


def _label_evidence(deck, value, parts):
    label = parts(_source_label(deck)).tokens
    deck_context = parts(deck.slide_context or deck.slide_title).tokens | DECK_DECORATION
    cell_context = parts(f"{value.section_label} {value.sheet_name} {value.col_label}").tokens | DECK_DECORATION
    labels = [parts(text).tokens for text in (value.row_label, value.named_range) if text]
    exact = label in labels
    if value.row_label and value.col_label:
        labels.append(parts(f"{value.row_label} {value.col_label}").tokens)
    supported = any(tokens & label and (tokens - label) <= deck_context
                    and (label - tokens) <= cell_context for tokens in labels)
    return exact, supported


def _rank_sources(deck: DeckNumber, index: ModelIndex):
    """Rank by label evidence only. Keep the strongest rivals even when they disagree numerically."""
    label = _source_label(deck)
    parts = index.parts(label)
    if not parts.tokens:
        return []
    query = index.query(parts.tokens)
    deck_period = deck.period or parts.period
    deck_qualifiers = (parts.qualifiers | normalize_qualifiers(deck.qualifiers, index.config)) - {"change"}
    change = _is_change(deck, label)
    status = _period_scenario(deck_period, deck.col_label, label, deck.context)
    scope = parts.tokens | index.tokens_of(deck.slide_context or deck.slide_title)
    deck_stats = stat_groups(f"{label} {deck.row_label} {deck.col_label}")
    if deck.is_range:
        range_stat = {"low": "min", "high": "max"}.get(deck.range_role)
        if range_stat:
            deck_stats = deck_stats | {range_stat}
    deck_covenant = covenant_words(f"{label} {deck.row_label} {deck.col_label}")
    positions = {i for token in parts.tokens for i in index.postings.get(token, ())}
    eligible = []
    for i in positions:
        value = index.values[i]
        period = index.periods[i]
        affinity = period_affinity(deck_period, period)
        if (value.is_error or not dimensions_compatible(deck.unit, value.unit)
                or (deck.currency and value.currency and deck.currency != value.currency)
                or affinity == 0 or index.is_change(i) != change
                or (index.col_entities(i) - scope)
                or index.stats(i) != deck_stats or index.covenant(i) - deck_covenant
                or not parts.tokens & (index.row_tokens(i) | index.col_tokens(i))):
            continue
        if deck.is_range and deck.range_role not in index.quals[i] | index.stats(i):
            continue
        model_qualifiers = index.quals[i] - {"change"}
        if (deck_qualifiers ^ model_qualifiers) & _SOURCE_QUALIFIERS:
            continue
        cell_status = _period_scenario(period, value.col_label, value.row_label, value.named_range)
        if status and cell_status and status != cell_status:
            continue
        score, _ = index.label_score(query, i)
        score *= qualifier_factor(deck_qualifiers, model_qualifiers) * (affinity if affinity is not None else 0.9)
        if status and not cell_status:
            score *= 0.88
        if score >= 0.38:
            eligible.append((score, not value.hidden, value.sheet_priority or 0.0, -i))
    return eligible


def source_comparison(deck: DeckNumber, value: ModelValue, config: Config) -> Comparison | None:
    """Compare an exact figure at displayed precision, independent of issue tolerance."""
    if value.is_error:
        return None
    return compare(deck if deck.comparator == "=" else replace(deck, comparator="="), value, config)


def display_precision(config: Config) -> Config:
    return replace(config, relative_tolerance=0.0, absolute_tolerance=0.0)


def _reconciling_sources(deck, index, ranked, precise):
    if deck.comparator != "=":
        return []
    candidates = []
    for rank in ranked:
        i = -rank[3]
        value = index.values[i]
        if not _label_evidence(deck, value, index.parts)[1]:
            continue
        comparison = source_comparison(deck, value, precise)
        if comparison is not None and comparison.ok:
            candidates.append(rank)
    return [(index.values[-i], index.periods[-i]) for _, _, _, i in heapq.nlargest(MAX_CANDIDATES, candidates)]


def _inspection_sources(deck, index, precise, reconciling):
    """Keep related cells visible when the displayed value or context is incomplete."""
    text = _source_label(deck)
    label = index.parts(text)
    query = index.query(label.tokens)
    deck_period = deck.period or label.period
    qualifiers = (label.qualifiers | normalize_qualifiers(deck.qualifiers, index.config)) - {"change"}
    change = _is_change(deck, text)
    positions = {i for token in label.tokens for i in index.postings.get(token, ())}
    options = {value.ref: (value, period) for value, period in reconciling}
    ranked = []
    for i in positions:
        value, period = index.values[i], index.periods[i]
        if (value.ref in options or value.is_error
                or not dimensions_compatible(deck.unit, value.unit)
                or (deck.currency and value.currency and deck.currency != value.currency)
                or not (value.row_label or value.named_range or value.col_label)):
            continue
        affinity = period_affinity(deck_period, period)
        if affinity == 0:
            continue
        base, _ = index.label_score(query, i)
        alignment = qualifier_factor(qualifiers, index.quals[i] - {"change"})
        if alignment <= 0.3:
            continue
        score = base * alignment * (affinity if affinity is not None else 0.8)
        if score >= 0.16:
            consistent = index.is_change(i) == change
            ranked.append((score * (1 if consistent else 0.65), not value.hidden, value.sheet_priority or 0, -i))
    # Check the best label leads first. Broader value coincidences cannot displace them.
    leads = []
    for score, visible, priority, pos in heapq.nlargest(MAX_CANDIDATES * 3, ranked):
        value = index.values[-pos]
        comparison = source_comparison(deck, value, precise)
        tier = 2 if comparison and comparison.ok and not comparison.inferred else int(bool(comparison and comparison.ok))
        leads.append((tier, score, visible, priority, pos))
    for _, _, _, _, pos in heapq.nlargest(MAX_CANDIDATES - len(options), leads):
        value = index.values[-pos]
        options[value.ref] = value, index.periods[-pos]
    return list(options.values())


def discover_sources(deck: DeckNumber, index: ModelIndex):
    precise = display_precision(index.config)
    reconciling = _reconciling_sources(deck, index, _rank_sources(deck, index), precise)
    return _inspection_sources(deck, index, precise, reconciling), ai_candidates(deck, reconciling, index.config)


def ai_candidates(deck, candidates, config):
    """Restrict AI to cells that reconcile in known units and have a compatible period."""
    if deck.comparator != "=" or deck.is_range or "guidance" in deck.qualifiers:
        return []
    precise = display_precision(config)
    deck_period = deck.period or split_label(_source_label(deck), config).period
    status = _period_scenario(deck_period, deck.col_label, _source_label(deck), deck.context)
    supported = []
    for value, period in candidates:
        if value.hidden or ((deck_period or period) and (period_affinity(deck_period, period) or 0) < 0.85):
            continue
        if status and status != _period_scenario(period, value.col_label, value.row_label, value.named_range):
            continue
        comparison = source_comparison(deck, value, precise)
        if comparison is not None and comparison.ok and not comparison.inferred:
            supported.append((value, period))
    return supported


def suggest_sources(targets, config, warnings: list[ReviewWarning]) -> dict[str, str]:
    """Submit bounded shortlists together using opaque references and digit-masked source names."""
    entries, refs = [], {}
    for entry_id, deck, candidates in targets:
        if not candidates:
            continue
        entry = build_entry(entry_id, deck, _source_label(deck), deck.period, candidates)
        refs[entry_id] = {}
        for i, candidate in enumerate(entry["candidates"]):
            handle = f"candidate-{i + 1}"
            refs[entry_id][handle] = candidate["ref"]
            candidate["ref"] = handle
            for field in ("sheet", "named_range"):
                candidate[field] = mask_digits(candidate[field])
        descriptions = {
            candidate["ref"]: tuple(str(value).strip().casefold()
                                    for field, value in candidate.items() if field != "ref")
            for candidate in entry["candidates"]
        }
        counts = Counter(descriptions.values())
        refs[entry_id] = {handle: ref for handle, ref in refs[entry_id].items()
                          if counts[descriptions[handle]] == 1}
        entries.append(entry)
    if not entries:
        return {}
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
    exact, supported = _label_evidence(deck, value, index.parts)
    ai_supported = bool(not truncated and suggested == value.ref)
    if not exact and not (ai_supported and supported):
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
    status = _period_scenario(deck_period, deck.col_label, deck_label, deck.context)
    if status and status != _period_scenario(period, value.col_label, value.row_label, value.named_range):
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
    precise = display_precision(config)
    for pos, match in enumerate(matches):
        if match.status not in UNRESOLVED:
            continue
        deck = match.deck
        label = _source_label(deck)
        key = (label, deck.period, deck.unit, deck.currency, tuple(deck.qualifiers),
               deck.row_label, deck.col_label, deck.slide_context or deck.slide_title,
               deck.is_range, deck.range_role,
               _period_scenario(deck.period or index.parts(label).period, deck.context))
        rank = rankings.get(key)
        if rank is None:
            rank = rankings[key] = _rank_sources(deck, index)
        ranked = heapq.nlargest(2, rank)
        reconciling = _reconciling_sources(deck, index, rank, precise)
        eligible = ai_candidates(deck, reconciling, config) if reconciling else []
        refs = {value.ref for value, _ in eligible}
        updated = replace(match, source_options=reconciling, source_ai_options=eligible,
                          source_suggestion=match.source_suggestion if match.source_suggestion in refs else "")
        truncated = len(rank) > MAX_CANDIDATES
        results[pos] = _verified_source(updated, index, ranked, truncated, precise, "")
        if results[pos].status in UNRESOLVED:
            results[pos].source_options = _inspection_sources(deck, index, precise, reconciling)
            if eligible:
                pending[str(pos)] = (ranked, truncated)
    suggestions = {}
    ai_targets = []
    if ai_config is not None:
        ai_targets = [(key, results[int(key)].deck,
                       results[int(key)].source_ai_options)
                      for key in pending]
    if ai_targets:
        suggestions = suggest_sources(
            ai_targets, ai_config, warnings,
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
        "ai_requested": int(ai_config is not None),
        "ai_reviewed": len(ai_targets),
        "suggested": sum(bool(ref) for ref in suggestions.values()),
        "remaining": sum(m.status in UNRESOLVED for m in results),
    }
