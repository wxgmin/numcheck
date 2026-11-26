"""Footnote and source-line checks.

Two things are verified deterministically:

* "as of" dates should agree across slides (a deck mixing 30-Sep and 31-Dec
  market data is usually an unrefreshed page).
* a footnote/source that states a *data period* ("FY2023 figures", "Company
  filings, Q3 2025") must not contradict the periods of the numbers on that slide.

Publication, filing and access years ("filed March 2025", "as of", "(c)") are
not data periods and are never flagged.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from numcheck.checks import IssueCollector
from numcheck.config import Config
from numcheck.models import DeckNumber, IssueType, Severity, SlideInfo
from numcheck.periods import find_periods, parse_key

# Words right before a year that mark it as a publication/filing/access date.
_NON_DATA_CONTEXT = re.compile(
    r"(?:\bfiled\b|\bas\s+of\b|\bpublished\b|\baccessed\b|\bretrieved\b|\bdated\b|\bupdated\b|\bpublication\b"
    r"|\breleased\b|\bcopyright\b|\bdownloaded\b|©|\(c\))[^.;]{0,30}$", re.I)


def _slide_years(nums: list[DeckNumber]) -> Counter:
    c: Counter = Counter()
    for d in nums:
        p = parse_key(d.period)
        if p and p.year is not None:
            c[p.year] += 1
    return c


def _data_periods(text: str) -> list[tuple[str, int, str]]:
    """(label, year, key) for explicit data periods in *text*, minus publication dates."""
    out = []
    for p, s, e in find_periods(text):
        if p.year is None:
            continue
        if p.basis == "y" and not p.part:
            continue  # bare year: could be a publication or filing date
        if _NON_DATA_CONTEXT.search(text[max(0, s - 40):s]):
            continue
        out.append((text[s:e], p.year, p.key))
    return out


def _footnote_texts(slides, footnotes) -> dict[int, list[str]]:
    by_slide: dict[int, list[str]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()

    def add(n: int, t: str) -> None:
        t = (t or "").strip()
        if not t or (n, t) in seen:
            return
        # "Source: X" (footnote) and "X" (parsed source) are the same statement.
        low = t.lower()
        if any(low in prev.lower() or prev.lower() in low for prev in by_slide[n]):
            return
        seen.add((n, t))
        by_slide[n].append(t)

    for f in footnotes or []:
        if isinstance(f, dict):
            add(int(f.get("slide_number", 0)), f.get("text", ""))
    for s in slides or []:
        for t in list(s.footnotes) + list(s.sources):
            add(s.slide_number, t)
    return by_slide


def check_footnotes(deck_numbers: list[DeckNumber], slides: list[SlideInfo] | None, footnotes: list[dict] | None,
                    config: Config, collector: IssueCollector) -> None:
    for step in (_check_as_of, _check_footnote_periods):
        try:
            step(deck_numbers, slides, footnotes, config, collector)
        except Exception:  # noqa: BLE001 - never abort the run
            continue


def _check_as_of(deck_numbers, slides, footnotes, config, collector) -> None:
    per_slide = {s.slide_number: sorted(set(s.as_of_dates)) for s in (slides or []) if s.as_of_dates}
    dates = sorted({d for ds in per_slide.values() for d in ds})
    if len(dates) < 2:
        return
    first = min(per_slide)
    listing = "; ".join(f"slide {n}: {', '.join(ds)}" for n, ds in sorted(per_slide.items()))
    collector.add(
        first, ", ".join(per_slide[first]), "", "", IssueType.FOOTNOTE,
        f"'As of' dates differ across slides ({', '.join(dates)}); {listing}",
        severity=Severity.LOW, check="as_of", metric_key="as-of date",
        related=[{"slide_number": n, "as_of": ds} for n, ds in sorted(per_slide.items())],
        details={"dates": dates},
    )


def _check_footnote_periods(deck_numbers, slides, footnotes, config, collector) -> None:
    nums_by_slide: dict[int, list[DeckNumber]] = defaultdict(list)
    for d in deck_numbers:
        nums_by_slide[d.slide_number].append(d)
    deck_years = _slide_years(deck_numbers)
    hints = {s.slide_number: s.period_hint for s in (slides or []) if s.period_hint}
    for slide_no, texts in _footnote_texts(slides, footnotes).items():
        years = _slide_years(nums_by_slide.get(slide_no, []))
        if not years and hints.get(slide_no):
            p = parse_key(hints[slide_no])
            if p and p.year is not None:
                years = Counter({p.year: 1})
        if not years and deck_years:
            # A slide without its own numbers (appendix, sources page) is judged
            # against the deck's dominant period only: projections for later years
            # elsewhere in the deck must not excuse a stale data-period footnote.
            years = Counter({deck_years.most_common(1)[0][0]: 1})
        if not years:
            continue
        dominant = years.most_common(1)[0][0]
        dom_key = next((d.period for d in nums_by_slide.get(slide_no, []) if parse_key(d.period)
                        and parse_key(d.period).year == dominant), "") or hints.get(slide_no, "")  # type: ignore[union-attr]
        dom_key = dom_key or next((d.period for d in deck_numbers if parse_key(d.period)
                                   and parse_key(d.period).year == dominant), f"{dominant}")  # type: ignore[union-attr]
        for text in texts:
            for label, year, _key in _data_periods(text):
                if year in years:
                    continue
                anchor = (nums_by_slide.get(slide_no) or [None])[0]
                collector.add(
                    slide_no, label, "", "", IssueType.UNIT_PERIOD_MISMATCH,
                    f"Footnote mentions '{year}' which conflicts with slide period '{dom_key}' "
                    f"(footnote: \"{text[:120]}\")",
                    severity=Severity.MEDIUM, check="footnote_period", metric_key="footnote",
                    shape_id=anchor.shape_id if anchor is not None else "",
                    details={"footnote_year": year, "slide_years": sorted(years), "footnote": text,
                             "footnote_period": label},
                )
                break  # one finding per footnote text
