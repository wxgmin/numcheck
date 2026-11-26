"""Coverage: how much of the deck was actually verified, and where the gaps are.

Coverage is a first-class output (design principle 4): a clean issue list on a
deck where 40% of numbers were never verified must not read as "all good".
"""

from __future__ import annotations

from collections import Counter

from numcheck.models import DeckExtraction, Match


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def compute_coverage(matches: list[Match], extraction: DeckExtraction | None) -> dict:
    """Summarise match outcomes overall, by source and per slide.

    ``checkable`` counts matches that were attempted (status other than
    ``skipped``); ``verified`` excludes value-only coincidences.
    """
    live = [m for m in matches if m.status != "skipped"]
    counts = Counter(m.status for m in live)
    verified = counts["matched"]

    skipped: Counter = Counter()
    if extraction is not None:
        for d in extraction.skipped:
            skipped[d.skip_reason or "other"] += 1

    by_source: dict[str, dict[str, int]] = {}
    slides: dict[int, dict] = {}
    if extraction is not None:
        for s in extraction.slides:
            slides[s.slide_number] = {"slide_number": s.slide_number, "title": s.title,
                                      "checkable": 0, "verified": 0, "issues": 0}
    for m in live:
        src = getattr(m.deck.source, "value", str(m.deck.source))
        row = by_source.setdefault(src, {"checkable": 0, "verified": 0})
        row["checkable"] += 1
        sl = slides.setdefault(m.deck.slide_number, {"slide_number": m.deck.slide_number,
                                                     "title": m.deck.slide_title,
                                                     "checkable": 0, "verified": 0, "issues": 0})
        sl["checkable"] += 1
        if m.status == "matched":
            row["verified"] += 1
            sl["verified"] += 1
        if not sl["title"]:
            sl["title"] = m.deck.slide_title

    return {
        "checkable": len(live),
        "matched": counts["matched"],
        "matched_value_only": counts["matched_value_only"],
        "mismatch": counts["mismatch"],
        "stale": counts["stale"],
        "ambiguous": counts["ambiguous"],
        "unmatched": counts["unmatched"],
        "verified": verified,
        "verified_pct": _pct(verified, len(live)),
        "skipped": dict(skipped),
        "by_source": by_source,
        "per_slide": [slides[k] for k in sorted(slides)],
    }
