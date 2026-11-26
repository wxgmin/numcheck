"""Verify deck figures that the workbook states in words rather than in a value cell.

Models carry facility sizes, coupons and spreads inside text: "Revolving credit facility ($250m)",
"6.375% Senior notes", a pricing column holding "S+275". A deck repeating such a figure is backed by
the model even though no numeric cell holds it. This pass confirms - never flags - an otherwise
unresolved deck number when:

* the same figure (value, unit and scale) appears in workbook text, and
* every such appearance sits in one row of one sheet (one instrument), and
* the deck's own labels share a word with that row, or the figure is distinctive (3+ significant
  digits with a unit, such as "6.375%").
"""

from __future__ import annotations

import math
import re

from numcheck.matching.normalizer import Comparison, scan_numbers
from numcheck.models import Match, ModelText, ModelValue
from numcheck.periods import periods_compatible

_WORD_RE = re.compile(r"[a-z][a-z&]{2,}")
_STOP = {"the", "and", "for", "with", "total", "less", "plus", "amount", "value", "of"}


def _words(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for w in _WORD_RE.findall((t or "").lower()):
            if w not in _STOP:
                out.add(w)
                out.add(w.rstrip("s"))
                if len(w) > 6:
                    out.add(w[:6])   # "revolver" ~ "revolving", "facility" ~ "facilities"
    return out


def _same_figure(d, p) -> bool:
    if p.skip_reason:
        return False
    if getattr(p, "is_range", False) and not (d.is_range and d.range_role == getattr(p, "range_role", "")):
        return False   # "WACC 8.5%-9.5%" in a note bounds a sensitivity; it does not state that WACC is 9.5%
    dim_d, dim_p = d.unit or "none", p.dimension or "none"
    if dim_d != dim_p:
        return False
    if (d.scale or "") != (p.scale or ""):
        return False
    if d.currency and p.currency and d.currency != p.currency:
        return False
    # "(250m)" inside a label is an aside, not an accounting negative: compare magnitudes as displayed.
    return math.isclose(abs(d.value), abs(p.value), rel_tol=0, abs_tol=1e-9)


def _significant(raw: str) -> int:
    digits = re.sub(r"\D", "", raw or "").lstrip("0")
    return len(digits)


_GENERIC = {"value", "values", "total", "share", "group", "revenue", "amount", "figure", "number", "represent",
            "represents", "implied", "range", "per", "cent", "percent", "ratio", "multiple"}


def corroborate_value_only(matches: list[Match]) -> int:
    """Upgrade a unique-value match to verified when the deck sentence names the cell's own labels.

    "Terminal value represents 76.1% of enterprise value" -> "PV of terminal value (% of EV)": the value
    occurs once in the workbook *and* the sentence shares a specific word with that cell's labels.
    """
    changed = 0
    for m in matches:
        if m.status != "matched_value_only" or m.model is None:
            continue
        d, mv = m.deck, m.model
        if d.period and mv.period and not periods_compatible(d.period, mv.period):
            continue
        deck_words = _words(d.label, d.context, d.row_label, d.col_label) - _GENERIC
        cell_words = _words(mv.row_label, mv.col_label, getattr(mv, "section_label", ""), mv.named_range) - _GENERIC
        shared = {w for w in deck_words & cell_words if len(w) >= 4}
        if not shared:
            continue
        m.status = "matched"
        m.method = "value_label"
        m.confidence = max(m.confidence, 0.7)
        m.note = (f"The value occurs once in the workbook and the slide names the same item "
                  f"({', '.join(sorted(shared)[:3])}).")
        changed += 1
    return changed


def resolve_ambiguous_by_value(matches: list[Match], model_values: list[ModelValue], config) -> int:
    """An ambiguous label whose words all appear in exactly one cell that also equals the displayed value.

    "Precedent transactions: 2,783" ties between deal rows on a "Precedents" sheet, none equal to 2,783,
    while the valuation row "Precedent transactions ... / EV - low" holds exactly 2,783.
    """
    from numcheck.matching.normalizer import compare

    changed = 0
    for m in matches:
        d = m.deck
        if m.status != "ambiguous" or not d.label:
            continue
        need = {w for w in _WORD_RE.findall(d.label.lower()) if w not in _STOP}
        if not need:
            continue
        hits = []
        for mv in model_values:
            if mv.is_error:
                continue
            words = set(_WORD_RE.findall(f"{mv.row_label} {mv.col_label} {getattr(mv, 'section_label', '')}".lower()))
            if not need <= words:
                continue
            if d.period and mv.period and not periods_compatible(d.period, mv.period):
                continue   # a 2025E figure equal to the 2024A cell is a wrong-period error, not a match
            c = compare(d, mv, config)
            if c is not None and c.ok and not c.inferred:
                hits.append((mv, c))
                if len(hits) > 12:
                    break
        # One cell, or several cells holding the very same value (a figure linked across sheets:
        # DCF!C29 = Cap!C17): the input cell - not a formula pointing at it - is the source.
        if not hits or len(hits) > 12 or len({round(h[0].value, 9) for h in hits}) != 1:
            continue
        mv, c = min(hits, key=lambda h: (bool(h[0].formula), -(h[0].sheet_priority or 1.0), h[0].hidden))
        m.model, m.comparison, m.status, m.method = mv, c, "matched", "label_value"
        m.confidence = max(m.confidence, 0.75)
        m.note = "The only cell carrying every word of the slide label also equals the displayed value."
        changed += 1
    return changed


def _initials(label: str) -> str:
    words = [w for w in re.findall(r"[a-z]+", label.lower()) if w not in {"of", "the", "and", "a", "an", "to", "in"}]
    return "".join(w[0] for w in words) if len(words) >= 2 else ""


def _descriptor(d) -> set[str]:
    """Words that name a row-label figure: "(9.0% WACC)" -> {wacc}; "6.375% Senior notes" -> {senior, notes}."""
    text = d.context or d.row_label or ""
    raw = d.raw_text.strip("() ")
    at = text.find(raw)
    scope = text
    if at >= 0:
        open_ = text.rfind("(", 0, at)
        close = text.find(")", at + len(raw))
        if open_ >= 0 and close >= 0 and ")" not in text[open_:at]:
            inner = (text[open_ + 1:at] + " " + text[at + len(raw):close]).strip()
            scope = inner if re.search(r"[A-Za-z]{2,}", inner) else text[:open_] + " " + text[close + 1:]
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z&]+", scope)}
    return {w for w in words if w not in _STOP and w not in _GENERIC and len(w) >= 2 and not w.isdigit()} - {"m", "bn"}


def resolve_label_figures(matches: list[Match], model_values: list[ModelValue], config) -> int:
    """Confirm a figure written inside a table row label against the one equal cell its own words name.

    "Discount factor (9.0% WACC)" -> the "WACC" cell holding 9.0% (not a segment growth rate that also
    equals 9%); "PV of terminal value (2.5% TGR)" -> "Terminal growth rate" (TGR as initials).
    Confirms only; never flags.
    """
    from numcheck.matching.normalizer import compare

    changed = 0
    for m in matches:
        d = m.deck
        if not getattr(d, "label_cell", False) or m.status not in ("unmatched", "ambiguous", "matched_value_only"):
            continue
        need = _descriptor(d)
        if not need:
            continue
        hits = []
        for mv in model_values:
            if mv.is_error:
                continue
            label = f"{mv.row_label} {mv.col_label} {getattr(mv, 'section_label', '')}"
            words = set(_WORD_RE.findall(label.lower())) | {w.rstrip("s") for w in _WORD_RE.findall(label.lower())}
            words |= {_initials(mv.row_label), _initials(mv.col_label)} - {""}
            if not all(w in words or w.rstrip("s") in words for w in need):
                continue
            if d.period and mv.period and not periods_compatible(d.period, mv.period):
                continue
            c = compare(d, mv, config)
            if c is not None and c.ok and not c.inferred:
                hits.append((mv, c))
                if len(hits) > 12:
                    break
        if not hits or len(hits) > 12 or len({round(h[0].value, 9) for h in hits}) != 1:
            continue
        mv, c = min(hits, key=lambda h: (bool(h[0].formula), -(h[0].sheet_priority or 1.0), h[0].hidden))
        m.model, m.comparison, m.status, m.method = mv, c, "matched", "label_value"
        m.confidence = max(m.confidence, 0.75)
        m.note = "The figure's own words in the row label name this cell, and it equals the displayed value."
        changed += 1
    return changed


def verify_text_mentions(matches: list[Match], texts: list[ModelText]) -> int:
    """Upgrade unresolved deck numbers backed by workbook text. Returns how many were confirmed."""
    if not texts:
        return 0
    parsed = []
    for t in texts:
        try:
            nums = [p for p in scan_numbers(t.text) if not p.skip_reason]
        except Exception:  # noqa: BLE001 - odd text never breaks the review
            continue
        if nums:
            parsed.append((t, nums))
    if not parsed:
        return 0
    changed = 0
    for m in matches:
        d = m.deck
        if m.status not in ("unmatched", "ambiguous") or d.skip_reason or d.is_range:
            continue
        hits = [t for t, nums in parsed if any(_same_figure(d, p) for p in nums)]
        if not hits or len({(t.workbook, t.sheet_name, t.row) for t in hits}) != 1:
            continue
        deck_words = _words(d.label, d.row_label, d.col_label, d.context)
        best = max(hits, key=lambda t: len(deck_words & _words(t.col_label)) * 2 + (t.text != t.row_label))
        row_words = _words(best.row_label, best.text, *(h.text for h in hits))
        distinctive = _significant(d.raw_text) >= 3 and (d.unit or "none") != "none"
        if not (deck_words & row_words) and not distinctive:
            continue
        label = best.row_label if best.row_label and best.row_label != best.text else best.text
        mv = ModelValue(sheet_name=best.sheet_name, cell_address=best.cell_address, value=abs(d.value),
                        row_label=label, col_label=best.col_label, unit=d.unit or "none", scale=d.scale or "",
                        workbook=best.workbook, row=best.row, col=best.col, number_format="@",
                        currency=d.currency or "", scale_known=True, hidden=best.hidden)
        m.model = mv
        m.status = "matched"
        m.method = "model_text"
        m.confidence = 0.7
        m.comparison = Comparison(ok=True, dimension=d.unit or "none", deck_value=abs(d.value),
                                  model_value=abs(d.value), tolerance=0.0, difference=0.0,
                                  interpretation=f"stated in workbook text: “{best.text}”",
                                  rounding_rel=0.0)
        m.note = f"The workbook states this figure in text at {best.sheet_name}!{best.cell_address}: “{best.text}”."
        changed += 1
    return changed
