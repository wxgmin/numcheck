"""Staleness check against a previous model version.

Staleness is now decided inside the matching pipeline
(:func:`numcheck.matching.pipeline.match_deck_to_model` with ``old_model_values``):
a deck number that mismatches the new model but equals the corresponding old cell
(same sheet+address, else same label parts + period) ends in ``Match.status == "stale"``
with ``Match.old_model`` set, and the issue builder turns that into a STALE issue.

``check_staleness`` remains only so older callers keep working; it is a deliberate
no-op (re-running full matching for the old model would triple the work and
produce duplicate issues).
"""

from __future__ import annotations

from typing import Any


def check_staleness(*_args: Any, **_kwargs: Any) -> None:
    """Compatibility no-op: staleness is derived from matches in the pipeline."""
    return None
