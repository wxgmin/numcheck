"""Turn match outcomes into issues, and finalize the issue list.

``build_issues`` is a pure translation of what the matcher decided (the numeric
comparison already happened, deterministically, in ``normalizer.compare``).
``finalize_issues`` then assigns severity, folds duplicates so each deck number
carries one primary issue, orders the list and numbers it.
"""

from __future__ import annotations

from dataclasses import replace

from numcheck.checks import IssueCollector, location_fields
from numcheck.checks.severity import assign_severity, severity_rank
from numcheck.matching.normalizer import (
    display_tolerance,
    format_canonical,
    format_like,
    format_tolerance,
    parsed_from_deck,
)
from numcheck.models import Issue, IssueType, Match, format_model_value

# Which issue wins when several concern the same deck number (lower = primary).
_PRIORITY = {
    IssueType.SOURCE_ERROR: 0, IssueType.STALE: 1, IssueType.SIGN_ERROR: 2, IssueType.SCALE_ERROR: 3,
    IssueType.PERIOD_AMBIGUOUS: 4, IssueType.MISMATCH: 5, IssueType.SUM_ERROR: 6,
    IssueType.PCT_RECONCILE: 7, IssueType.RATIO_RECONCILE: 7, IssueType.GROWTH_RECONCILE: 7,
    IssueType.INCONSISTENT_ACROSS_SLIDES: 8, IssueType.UNIT_PERIOD_MISMATCH: 9,
    IssueType.AMBIGUOUS: 10, IssueType.UNMATCHED: 11, IssueType.ROUNDING: 12, IssueType.FOOTNOTE: 13,
}


# --------------------------------------------------------------------------- #
# build_issues
# --------------------------------------------------------------------------- #

def _model_label(mv) -> str:
    return " / ".join(x for x in (mv.row_label, mv.col_label) if x) if mv else ""


def _expected_text(m: Match, config) -> str:
    c = m.comparison
    if c is not None and m.model is not None and not m.model.is_error:
        return format_like(m.deck, c.model_value, config)
    return format_model_value(m.model)


def _tol_text(c, deck=None, config=None) -> str:
    if c is None:
        return ""
    if deck is not None and config is not None:
        return format_tolerance(deck, c.tolerance, c.model_value, config)
    return format_canonical(c.dimension, c.tolerance)


def _base_fields(m: Match, config, check: str = "model_match") -> dict:
    """Fields common to every model-match issue."""
    d, mv, c = m.deck, m.model, m.comparison
    f: dict = {
        "confidence": m.confidence,
        "model_label": _model_label(mv),
        "check": check,
        **location_fields(d),
    }
    details: dict = {"method": m.method, "score": m.score, "deck_source": d.source.value,
                     "deck_context": d.context, "slide_title": d.slide_title}
    if c is not None:
        f["difference"] = c.difference
        f["tolerance"] = c.tolerance
        f["suggestion"] = format_like(d, c.model_value, config)
        details.update(interpretation=c.interpretation, inferred=c.inferred,
                       relative_difference=c.relative_difference, expected_canonical=c.model_value,
                       dimension=c.dimension)
        if c.notes:
            details["notes"] = list(c.notes)
    if m.note:
        details["note"] = m.note
    f["details"] = details
    return f


def _add(collector, m: Match, config, issue_type: IssueType, message: str, *,
         expected: str | None = None, source_cell: str | None = None, **over):
    d, mv = m.deck, m.model
    f = _base_fields(m, config)
    related = over.pop("related", [])
    extra_details = over.pop("details", {})
    f["details"].update(extra_details)
    f.update(over)
    return collector.add(
        d.slide_number, d.raw_text,
        expected if expected is not None else _expected_text(m, config),
        source_cell if source_cell is not None else (mv.ref if mv else ""),
        issue_type, message,
        metric_key=d.metric_key,
        shape_id=d.shape_id, related=related, **f,
    )


def _what(m: Match) -> str:
    """Name a deck number for messages; never 'unnamed' - fall back to its table position."""
    d = m.deck
    name = d.label or d.row_label or d.metric_key
    if name and name != "unnamed":
        return name
    parts = [x for x in (d.row_label, d.col_label) if x]
    if parts:
        return " / ".join(parts)
    if d.table_row >= 0 and d.table_col >= 0:
        return f"table cell r{d.table_row}c{d.table_col}"
    return "value"


def _mismatch(m: Match, config, collector) -> None:
    d, mv, c = m.deck, m.model, m.comparison
    if mv is None:
        _add(collector, m, config, IssueType.UNMATCHED,
             f"No model cell found for '{_what(m)}' ({d.raw_text})", expected="", source_cell="")
        return
    if mv.is_error:
        _add(collector, m, config, IssueType.SOURCE_ERROR,
             f"'{_what(m)}' ({d.raw_text}) maps to model cell {mv.ref} which is an Excel error "
             f"({mv.error_text or '#ERROR'}); fix the model before the deck can be checked",
             expected=mv.error_text or "#ERROR", suggestion="")
        return
    expected = _expected_text(m, config)
    tol = f"; tolerance {_tol_text(c, d, config)}" if c is not None else ""
    if c is not None and c.sign_flip:
        itype, why = IssueType.SIGN_ERROR, f"sign differs: the model shows {expected}"
    elif c is not None and c.scale_slip:
        itype, why = IssueType.SCALE_ERROR, f"digits match the model at a {c.scale_slip} scale (model {expected})"
    else:
        itype, why = IssueType.MISMATCH, f"the model has {expected}"
    msg = f"'{_what(m)}' is {d.raw_text} in the deck but {why} at {mv.ref}{tol}"
    period_note = m.note.startswith("value equals")
    if period_note:
        msg += f". Note: {m.note}"
    _add(collector, m, config, itype, msg)
    if period_note:
        _add(collector, m, config, IssueType.PERIOD_AMBIGUOUS,
             f"'{_what(m)}' ({d.raw_text}) does not match its own period in the model but {m.note}; "
             f"the number may belong to a different period",
             related=[{"note": m.note, "source_cell": mv.ref, "expected_value": expected}],
             details={"period_note": m.note})


def _stale(m: Match, config, collector) -> None:
    d, mv, old = m.deck, m.model, m.old_model
    expected = _expected_text(m, config)
    old_txt = format_model_value(old) if old else "?"
    where = mv.ref if mv else "the new model"
    msg = (f"'{_what(m)}' is {d.raw_text}, which matches the previous model version "
           f"({old.ref if old else '?'} = {old_txt}), but the current model has {expected} at {where}; "
           f"the deck was probably not refreshed")
    related = [{"role": "previous_model", "source_cell": old.ref, "value": old_txt}] if old else []
    _add(collector, m, config, IssueType.STALE, msg, related=related,
         details={"old_cell": old.ref if old else "", "old_value": old_txt})


def _ambiguous(m: Match, config, collector) -> None:
    d = m.deck
    cands = [{"ref": c.ref, "row_label": c.row_label, "col_label": c.col_label, "period": c.period,
              "value": format_model_value(c), "label": " / ".join(x for x in (c.row_label, c.col_label) if x)}
             for c in m.candidates]
    if not cands and m.model is not None:
        c = m.model
        cands = [{"ref": c.ref, "row_label": c.row_label, "col_label": c.col_label, "period": c.period,
                  "value": format_model_value(c), "label": _model_label(c)}]
    listing = "; ".join(
        f"{c['ref']} ('{_model_label_dict(c)}'{', ' + c['period'] if c['period'] else ''} = {c['value']})"
        for c in cands[:8]
    ) or "no candidates recorded"
    more = f" (+{len(cands) - 8} more)" if len(cands) > 8 else ""
    _add(collector, m, config, IssueType.AMBIGUOUS,
         f"'{_what(m)}' ({d.raw_text}) on slide {d.slide_number} could refer to several model cells: "
         f"{listing}{more}",
         expected="", source_cell="", suggestion="", difference=None, tolerance=None,
         related=[{"source_cell": c["ref"], "value": c["value"], "label": _model_label_dict(c)} for c in cands],
         details={"candidates": cands[:8], "candidate_count": len(cands)})


def _model_label_dict(c: dict) -> str:
    return " / ".join(x for x in (c["row_label"], c["col_label"]) if x)


def _unmatched(m: Match, config, collector) -> None:
    d = m.deck
    _add(collector, m, config, IssueType.UNMATCHED,
         f"No model cell found for '{_what(m)}' ({d.raw_text}); it could not be verified",
         expected="", source_cell="", suggestion="", difference=None, tolerance=None)


def _precision_suggestion(m: Match, config) -> str:
    """Model value with enough decimals that display rounding drops below ~1% of it."""
    d, c = m.deck, m.comparison
    base = max(d.decimals, 0)
    text = format_like(d, c.model_value, config)
    for extra in range(1, 5):
        clone = replace(d, decimals=base + extra)
        text = format_like(clone, c.model_value, config)
        if display_tolerance(parsed_from_deck(clone), config.unit_scales) <= 0.01 * abs(c.model_value):
            break
    return text


def _rounding(m: Match, config, collector) -> None:
    c, d = m.comparison, m.deck
    if c is None or not c.ok:
        return
    if c.truncated:
        _add(collector, m, config, IssueType.ROUNDING,
             f"'{_what(m)}' {d.raw_text} equals the model value ({_expected_text(m, config)}) only if it "
             f"was truncated rather than rounded; consider rounding properly",
             details={"truncated": True})
    elif d.comparator == "=" and d.decimals == 0 and c.rounding_rel > config.coarse_rounding_threshold:
        # Only whole-number displays are "coarse"; 0.1x for 0.14 is valid 1-dp rounding.
        sug = _precision_suggestion(m, config)
        _add(collector, m, config, IssueType.ROUNDING,
             f"'{_what(m)}' {d.raw_text} is valid rounding of the model value but hides "
             f"{c.rounding_rel:.1%} of it; a more precise display is {sug}",
             suggestion=sug, details={"coarse_rounding": c.rounding_rel})


def build_issues(matches: list[Match], config, collector: IssueCollector, slides=None) -> None:
    """Convert match outcomes into issues (see module docstring)."""
    for m in matches:
        if m.status == "mismatch":
            _mismatch(m, config, collector)
        elif m.status == "stale":
            _stale(m, config, collector)
        elif m.status == "ambiguous":
            _ambiguous(m, config, collector)
        elif m.status == "unmatched":
            _unmatched(m, config, collector)
        elif m.status == "matched":
            _rounding(m, config, collector)
        # "matched_value_only" / "skipped": nothing to report


# --------------------------------------------------------------------------- #
# finalize_issues
# --------------------------------------------------------------------------- #

_INDEPENDENT = {IssueType.UNIT_PERIOD_MISMATCH}


def _group_key(i: Issue):
    # Arithmetic and labelling findings are independent evidence (a total that does
    # not foot is a separate fix from the total disagreeing with the model), so they
    # stay as their own issues; cross-slide repeats fold into the model finding.
    if i.issue_type in _INDEPENDENT:
        return None
    if i.deck_uid:
        return ("uid", i.deck_uid)
    if i.table_row >= 0 or i.char_start >= 0:
        return (i.slide_number, i.shape_id, i.paragraph_index, i.char_start, i.table_row, i.table_col, i.deck_value)
    return None


def _fold_entry(i: Issue) -> dict:
    return {"issue_type": i.issue_type.value, "check": i.check, "message": i.message,
            "expected_value": i.expected_value, "source_cell": i.source_cell,
            "severity": i.severity.value, "related": i.related}


def _merge(group: list[Issue]) -> Issue:
    group = sorted(group, key=lambda i: _PRIORITY.get(i.issue_type, 99))
    primary, rest = group[0], group[1:]
    for other in rest:
        primary.related.append(_fold_entry(other))
        primary.details.setdefault("also", []).append(other.issue_type.value)
        if severity_rank(other.severity) < severity_rank(primary.severity):
            primary.severity = other.severity
        if not primary.suggestion and other.suggestion:
            primary.suggestion = other.suggestion
    return primary


def _position(i: Issue) -> tuple:
    return (i.slide_number, i.table_row if i.table_row >= 0 else i.paragraph_index,
            i.table_col, i.char_start)


def finalize_issues(issues: list[Issue], config, slides=None) -> list[Issue]:
    """Assign severity, merge duplicates per deck number, order and renumber.

    Cross-slide / arithmetic findings on a number that also has a model finding
    are folded into the model issue's ``related`` (the model is the authority),
    so each number surfaces once.
    """
    for i in issues:
        assign_severity(i, config, slides)

    seen: set[tuple] = set()
    unique: list[Issue] = []
    for i in issues:
        sig = (i.issue_type, i.slide_number, i.shape_id, i.deck_value, i.message, i.deck_uid)
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(i)

    groups: dict = {}
    order: list = []
    for n, i in enumerate(unique):
        k = _group_key(i)
        key = k if k is not None else ("solo", n)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)

    merged = [_merge(groups[k]) if len(groups[k]) > 1 else groups[k][0] for k in order]
    merged.sort(key=lambda i: (severity_rank(i.severity),) + _position(i))
    for n, i in enumerate(merged, 1):
        i.id = n
    return merged
