"""LLM disambiguation payloads (labels only) and the backward-compatible shim.

Privacy rule (DESIGN.md principle 1): the model never receives a number.  Entries
therefore carry the deck *label*, the deck context with every digit run replaced
by ``#``, the slide title, the period key and candidate cells described only by
their labels.  Candidate refs are cell addresses, which are identifiers, not values.
"""

from __future__ import annotations

import re

from numcheck.llm import resolve_ambiguous
from numcheck.models import DeckNumber, ModelValue
from numcheck.periods import find_periods

__all__ = ["resolve_ambiguous", "build_entry", "mask_digits", "mask_numbers_keep_periods"]

_DIGITS = re.compile(r"\d+")
_NUMBERISH = re.compile(r"\d[\d,]*(?:\.\d+)?")
MAX_CANDIDATES = 8
MAX_CONTEXT = 400


def mask_digits(text: str) -> str:
    """Replace every run of digits by ``#`` ("Revenue $1,200m" -> "Revenue $#,#m")."""
    return _DIGITS.sub("#", text or "")


def mask_numbers_keep_periods(text: str) -> str:
    """Mask numeric values but keep period words (FY24, Q3 2024): they are labels, not values."""
    text = text or ""
    spans = [(a, b) for _p, a, b in find_periods(text)]
    out: list[str] = []
    last = 0
    for a, b in spans:
        out.append(_NUMBERISH.sub("#", text[last:a]))
        out.append(text[a:b])
        last = b
    out.append(_NUMBERISH.sub("#", text[last:]))
    return "".join(out)


def build_entry(
    entry_id: str,
    deck: DeckNumber,
    deck_label: str,
    deck_period: str,
    candidates: list[tuple[ModelValue, str]],
) -> dict:
    """One LLM item.  ``candidates`` is ``[(ModelValue, period key)]``; never includes values."""
    return {
        "id": entry_id,
        "deck_label": mask_numbers_keep_periods(deck_label),
        "deck_context": mask_digits((deck.context or "")[:MAX_CONTEXT]),
        "slide_title": mask_numbers_keep_periods(deck.slide_title),
        "deck_period": deck_period or "",
        "candidates": [
            {
                "ref": mv.ref,
                "sheet": mv.sheet_name,
                "row_label": mask_numbers_keep_periods(mv.row_label),
                "col_label": mask_numbers_keep_periods(mv.col_label),
                "section_label": mask_numbers_keep_periods(mv.section_label),
                "named_range": mv.named_range,
                "period": period or "",
            }
            for mv, period in candidates[:MAX_CANDIDATES]
        ],
    }
