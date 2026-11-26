"""Cross-slide consistency: the same metric and period must show the same number.

Numbers are compared as *display intervals* ("$1.2bn" = [1.15bn, 1.25bn]) so a
figure shown at two precisions ("$1.2bn" vs "$1,234m") is consistent, while a
real disagreement ("$540m" vs "$512m") is flagged exactly once - on the outlier.
Grouping includes the canonical dimension so a percent is never compared with a
dollar amount, and the qualifier set so "adjusted EBITDA" never meets "EBITDA".
"""

from __future__ import annotations

import re
from collections import defaultdict

from numcheck.checks import (
    IssueCollector,
    canonical_of,
    interval_of,
    intervals_overlap,
    location_fields,
    metric_key_of,
    ref_dict,
)
from numcheck.config import Config
from numcheck.matching.normalizer import format_like
from numcheck.models import DeckNumber, IssueType, Match

_Entry = tuple[DeckNumber, float, float, float]  # (deck number, low, high, canonical centre)


def _group_key(d: DeckNumber, config: Config, vocab: frozenset[str] = frozenset()) -> tuple | None:
    metric = metric_key_of(d)
    if not metric or metric == "unnamed":
        return None
    dim = canonical_of(d, config)[0]
    return (metric, d.period, dim, frozenset(q.lower() for q in d.qualifiers), _entity(d, vocab))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z&]{2,}", (text or "").lower())}


def _entity_vocab(matches) -> frozenset[str]:
    """Entity words used by the model's sheet / section names (segments, companies), generic names removed."""
    from numcheck.matching.index import GENERIC_CONTEXT

    # Entities show up as parallel structure: the same row label repeated on 3+ sheets/sections
    # ("Revenue" on each segment tab). Functional tabs ("Comps", "Operating", "DCF") don't qualify.
    by_label: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for m in matches or []:
        for mv in ([m.model] if m.model is not None else []) + list(m.candidates or []):
            if mv.row_label:
                by_label[mv.row_label.strip().lower()].add((mv.sheet_name, mv.section_label))
    out: set[str] = set()
    for places in by_label.values():
        if len(places) >= 3:
            for sheet, section in places:
                out |= _words(sheet) | _words(section)
    return frozenset(out - set(GENERIC_CONTEXT))


_NAMED_ENTITY_RE = re.compile(
    r"\b((?i:segment|division|region|plant|site|facility|building|tower|store|project|portfolio|fund|tranche|"
    r"phase|asset|property|deal|portco|campus|mine|well|field|vessel|vintage|sleeve|pool|unit))\s+"
    r"((?:[A-Z][\w&'-]*|\d+[A-Za-z]?|[IVX]+)\b)")


def _named_entities(d: DeckNumber) -> frozenset[str]:
    """Explicitly named things in the number's own sentence and slide title ("Segment 3", "Project Atlas", "Fund II")."""
    ctx = d.context or ""
    if 0 <= d.char_start <= len(ctx):
        before = ctx[:d.char_start]
        cut = max(before.rfind(". "), before.rfind("; "), before.rfind("\n"))
        after = ctx[d.char_start:]
        stop = min([i for i in (after.find(". "), after.find("; "), after.find("\n")) if i >= 0] or [len(after)])
        ctx = before[cut + 1:] + after[:stop]
    text = f"{d.slide_context or d.slide_title or ''} | {ctx}"
    out = {f"{m.group(1).lower()} {m.group(2).lower()}" for m in _NAMED_ENTITY_RE.finditer(text)}
    # "Behavioral Health revenue grew ... a 22.2% CAGR": a capitalised name right before the metric word
    for m in _SUBJECT_ENTITY_RE.finditer(ctx):
        words = [w for w in m.group(1).split() if w.lower() not in _GENERIC_CAPS]
        if words:
            out.add(" ".join(words).lower())
    return frozenset(out)


_SUBJECT_ENTITY_RE = re.compile(
    r"((?:[A-Z][A-Za-z&'-]+\s+){1,3})(?:revenues?|sales|ebitda|ebit|margins?|income|volumes?|bookings|arr|"
    r"contribution|profit|segment|division|business)\b")
_GENERIC_CAPS = {"total", "group", "adj", "adj.", "adjusted", "net", "gross", "reported", "organic", "pro", "forma",
                 "consolidated", "company", "annual", "fiscal", "combined", "standalone", "operating", "recurring",
                 "the", "our", "its", "fy", "ltm", "ntm", "q1", "q2", "q3", "q4", "h1", "h2", "core", "underlying"}


def _entity(d: DeckNumber, vocab: frozenset[str]) -> frozenset[str]:
    """Segment/company the number is about: a title word the model knows ("Aerospace - summary"),
    or an entity named outright next to it ("Segment 3 revenue", "Project Atlas EBITDA")."""
    title = d.slide_context or d.slide_title
    known = frozenset(_words(title) & vocab) if vocab and title else frozenset()
    return known | _named_entities(d)


def _cluster(entries: list[_Entry]) -> list[list[_Entry]]:
    """Greedy clusters whose members all overlap the cluster's running intersection."""
    clusters: list[tuple[float, float, list[_Entry]]] = []
    for e in entries:
        _d, lo, hi, _c = e
        for i, (clo, chi, members) in enumerate(clusters):
            if intervals_overlap(lo, hi, clo, chi):
                clusters[i] = (max(lo, clo), min(hi, chi), members + [e])
                break
        else:
            clusters.append((lo, hi, [e]))
    return [m for _lo, _hi, m in clusters]


def _verified_uids(matches) -> set[tuple]:
    out: set[tuple] = set()
    for m in matches or []:
        if m.status == "matched":
            out.add(_ident(m.deck))
    return out


def _verified_cells(matches) -> dict[tuple, tuple]:
    """Deck number identity -> model cell it was verified against (matched / value-only)."""
    out: dict[tuple, tuple] = {}
    for m in matches or []:
        if m.status in ("matched", "matched_value_only") and m.model is not None:
            mv = m.model
            out[_ident(m.deck)] = (mv.workbook, mv.sheet_name, mv.cell_address)
    return out


def _distinct_entities(a: DeckNumber, b: DeckNumber, cells: dict[tuple, tuple]) -> bool:
    """Both numbers were verified by the model against DIFFERENT cells: different things.

    Segment revenue and group revenue share a label and period but are separate
    model cells; the model already vouches for each, so comparing them is noise.
    """
    ca, cb = cells.get(_ident(a)), cells.get(_ident(b))
    return ca is not None and cb is not None and ca != cb


def _ident(d: DeckNumber) -> tuple:
    return ("uid", d.uid) if d.uid else (d.slide_number, d.shape_id, d.paragraph_index, d.char_start,
                                         d.table_row, d.table_col, d.raw_text)


def _pick_reference(clusters: list[list[_Entry]], verified: set[tuple]) -> list[_Entry]:
    """Model-verified cluster if any, else the largest (ties: earliest slide)."""
    def vscore(c):
        return sum(1 for e in c if _ident(e[0]) in verified)
    best = max(clusters, key=lambda c: (vscore(c), len(c), -min(e[0].slide_number for e in c)))
    return best


def _different_place(a: DeckNumber, b: DeckNumber) -> bool:
    return a.slide_number != b.slide_number or a.shape_id != b.shape_id


def check_cross_slide(deck_numbers: list[DeckNumber], config: Config, collector: IssueCollector,
                      matches: list[Match] | None = None) -> None:
    """Flag each outlier once, naming the other slides where the metric differs."""
    groups: dict[tuple, list[_Entry]] = defaultdict(list)
    vocab = _entity_vocab(matches)
    for d in deck_numbers:
        if d.skip_reason or d.label_cell or d.comparator in (">", ">=", "<", "<=") or d.is_range:
            continue   # bounds and ranges ("10-12% target") are not point claims to compare
        key = _group_key(d, config, vocab)
        if key is None:
            continue
        try:
            _dim, lo, hi = interval_of(d, config)
            centre = canonical_of(d, config)[1]
        except Exception:  # noqa: BLE001 - one malformed number never aborts the check
            continue
        groups[key].append((d, lo, hi, centre))

    verified = _verified_uids(matches)
    cells = _verified_cells(matches)
    for key, entries in groups.items():
        if len({(e[0].slide_number, e[0].shape_id) for e in entries}) < 2:
            continue
        clusters = _cluster(entries)
        if len(clusters) < 2:
            continue
        ref = _pick_reference(clusters, verified)
        ref_verified = any(_ident(e[0]) in verified for e in ref)
        if not key[1] and len(clusters) >= 3 and len(ref) < 2 and not ref_verified:
            # A period-less metric with several different values and no repeated or model-backed
            # one is per-entity data ("EBITDA of $X" on each portfolio company page), not one fact.
            continue
        ref_d = ref[0][0]
        for cl in clusters:
            if cl is ref:
                continue
            for d, _lo, _hi, centre in cl:
                if _ident(d) in cells:
                    # The model itself vouches for this number; flagging it would point the
                    # analyst at the correct figure. Any real problem sits on the other side.
                    continue
                others = [e[0] for e in ref
                          if _different_place(d, e[0]) and not _distinct_entities(d, e[0], cells)]
                if not others:
                    continue
                _emit(d, ref_d, others, key[0], config, collector)


def _emit(d: DeckNumber, ref_d: DeckNumber, others: list[DeckNumber], metric: str, config: Config,
          collector: IssueCollector) -> None:
    ref_centre = canonical_of(ref_d, config)[1]
    expected = format_like(d, ref_centre, config)
    slides = sorted({o.slide_number for o in others})
    where = ", ".join(str(s) for s in slides[:6]) + ("..." if len(slides) > 6 else "")
    shown_ref = ref_d.raw_text
    msg = (f"'{metric}' ({d.period or 'no period'}) is {d.raw_text} here but {shown_ref} on "
           f"slide{'s' if len(slides) > 1 else ''} {where}")
    diff = canonical_of(d, config)[1] - ref_centre
    collector.add(
        d.slide_number, d.raw_text, expected, "", IssueType.INCONSISTENT_ACROSS_SLIDES, msg,
        metric_key=metric, shape_id=d.shape_id, suggestion=expected, difference=diff, check="cross_slide",
        related=[ref_dict(o) for o in others[:12]],
        details={"relative_difference": abs(diff) / max(abs(ref_centre), 1e-12),
                 "expected_canonical": ref_centre, "reference_slides": slides},
        **location_fields(d),
    )
