"""Orchestrates deck -> model matching.

Matching is label-first: the best label match identifies the intended source
cell, then value is checked deterministically. Value proximity is used only to
break ties among cells that share the same label.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from numcheck.config import Config
from numcheck.llm import resolve_ambiguous
from numcheck.matching.label_matcher import label_score, metric_key, normalize_label
from numcheck.matching.normalizer import canonical_deck, canonical_model, parsed_from_deck, values_match
from numcheck.matching.value_match import value_similarity
from numcheck.models import DeckNumber, ModelValue

MIN_LABEL_SCORE = 0.35
LABEL_BAND = 0.15
VALUE_AMBIGUITY_BAND = 0.05


@dataclass
class Match:
    deck: DeckNumber
    model: ModelValue | None
    status: str  # matched | mismatch | unmatched | ambiguous
    candidates: list[ModelValue] = field(default_factory=list)
    score: float = 0.0


def assign_metric_keys(
    deck_numbers: list[DeckNumber],
    model_values: list[ModelValue],
    config: Config,
) -> None:
    for d in deck_numbers:
        d.metric_key = metric_key(d.label or d.context or d.chart_series_name, config) or "unnamed"
    for mv in model_values:
        mv.metric_key = metric_key(f"{mv.named_range} {mv.row_label} {mv.col_label}", config) or "unnamed"


def match_deck_to_model(
    deck_numbers: list[DeckNumber],
    model_values: list[ModelValue],
    config: Config,
) -> list[Match]:
    assign_metric_keys(deck_numbers, model_values, config)

    matches: list[Match] = []
    ambiguous_entries: list[tuple[Match, dict]] = []

    for d in deck_numbers:
        parsed = parsed_from_deck(d)
        deck_norm = normalize_label(d.label or d.context or d.chart_series_name, config)
        d_canon = canonical_deck(parsed, config.unit_scales)

        label_candidates: list[tuple[ModelValue, float]] = []
        for mv in model_values:
            lscore = label_score(deck_norm, mv, config)
            if lscore >= MIN_LABEL_SCORE:
                label_candidates.append((mv, lscore))
        label_candidates.sort(key=lambda x: x[1], reverse=True)

        if not label_candidates:
            matches.append(Match(d, None, "unmatched"))
            continue

        best_label = label_candidates[0][1]
        pool = [mv for mv, l in label_candidates if l >= best_label - LABEL_BAND]

        def rank(mv: ModelValue) -> float:
            vscore = value_similarity(d, parsed, mv, config)
            period_bonus = 1.0 if (d.period and mv.period == d.period) else 0.0
            return vscore + period_bonus * 0.5

        pool.sort(key=rank, reverse=True)
        best_model = pool[0]
        best_rank = rank(best_model)

        contenders = [mv for mv in pool if rank(mv) >= best_rank - VALUE_AMBIGUITY_BAND]
        contender_values = {canonical_model(mv, config.unit_scales)[1] for mv in contenders}

        if len(contender_values) <= 1:
            value_ok = values_match(
                d_canon,
                canonical_model(best_model, config.unit_scales),
                parsed,
                config.relative_tolerance,
                config.absolute_tolerance,
            )
            matches.append(Match(d, best_model, "matched" if value_ok else "mismatch", pool, best_label))
            continue

        m = Match(d, best_model, "ambiguous", pool, best_label)
        entry = {
            "id": len(ambiguous_entries),
            "deck_label": d.label or d.context,
            "deck_value": d.raw_text,
            "candidates": [
                {
                    "cell_address": f"{mv.sheet_name}!{mv.cell_address}",
                    "sheet_name": mv.sheet_name,
                    "row_label": mv.row_label,
                    "col_label": mv.col_label,
                    "named_range": mv.named_range,
                    "value": mv.value,
                }
                for mv in pool[:5]
            ],
        }
        ambiguous_entries.append((m, entry))
        matches.append(m)

    if ambiguous_entries and (config.llm or "off").strip().lower() not in ("off", "none", ""):
        resolutions = resolve_ambiguous([entry for _, entry in ambiguous_entries], config)
        for m, entry in ambiguous_entries:
            chosen = resolutions.get(str(entry["id"]), "")
            if chosen:
                for mv in m.candidates:
                    if f"{mv.sheet_name}!{mv.cell_address}" == chosen or mv.cell_address == chosen:
                        m.model = mv
                        parsed = parsed_from_deck(m.deck)
                        value_ok = values_match(
                            canonical_deck(parsed, config.unit_scales),
                            canonical_model(mv, config.unit_scales),
                            parsed,
                            config.relative_tolerance,
                            config.absolute_tolerance,
                        )
                        m.status = "matched" if value_ok else "mismatch"
                        break

    return matches
