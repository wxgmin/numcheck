"""Staleness check against a previous model version."""

from __future__ import annotations

from numcheck.checks import IssueCollector
from numcheck.config import Config
from numcheck.matching.pipeline import match_deck_to_model
from numcheck.models import DeckNumber, IssueType, ModelValue, format_model_value


def check_staleness(
    deck_numbers: list[DeckNumber],
    new_model_values: list[ModelValue],
    old_model_values: list[ModelValue],
    config: Config,
    collector: IssueCollector,
) -> None:
    if not old_model_values:
        return

    old_matches = match_deck_to_model(deck_numbers, old_model_values, config)
    new_matches = match_deck_to_model(deck_numbers, new_model_values, config)

    for i, d in enumerate(deck_numbers):
        old = old_matches[i]
        new = new_matches[i]
        if old.status != "matched":
            continue
        # Matches the old model; flag if it no longer matches the new one.
        if new.status == "matched":
            continue

        new_expected = _find_new_value(old.model, new_model_values, config)
        expected_str = (
            f"{format_model_value(new_expected)} ({new_expected.sheet_name}!{new_expected.cell_address})"
            if new_expected
            else "changed in new model"
        )
        collector.add(
            slide_number=d.slide_number,
            deck_value=d.raw_text,
            expected_value=expected_str,
            source_cell=f"{old.model.sheet_name}!{old.model.cell_address}" if old.model else "",
            issue_type=IssueType.STALE,
            message=(
                f"'{d.label or d.metric_key}' matches the old model "
                f"({format_model_value(old.model)}) but the new model value differs"
            ),
            metric_key=d.metric_key,
            shape_id=d.shape_id,
        )


def _find_new_value(
    old_model: ModelValue | None,
    new_model_values: list[ModelValue],
    config: Config,
) -> ModelValue | None:
    if old_model is None:
        return None
    for mv in new_model_values:
        if mv.metric_key and mv.metric_key == old_model.metric_key:
            return mv
    for mv in new_model_values:
        if mv.sheet_name == old_model.sheet_name and mv.cell_address == old_model.cell_address:
            return mv
    return None
