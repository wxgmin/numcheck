"""Annotated deck: flagged numbers highlighted, numbered markers, notes, summary slides."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.text.text import _Run
from pptx.util import Emu, Pt

from numcheck.models import Issue, ReviewResult
from numcheck.report import as_result, sort_issues, type_label

log = logging.getLogger(__name__)

ISSUES_PER_SUMMARY_SLIDE = 14
_COLORS = {
    "high": RGBColor(0xB2, 0x3A, 0x3A),
    "medium": RGBColor(0xC0, 0x7A, 0x00),
    "low": RGBColor(0x55, 0x65, 0x7A),
}
_INK = RGBColor(0x15, 0x18, 0x1C)
_ACCENT = RGBColor(0x1F, 0x3A, 0x5F)
_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _color(issue: Issue) -> RGBColor:
    return _COLORS.get(issue.severity.value, _COLORS["low"])


# --------------------------------------------------------------------------- #
# Shape lookup
# --------------------------------------------------------------------------- #

def _search(shape, shape_id: str):
    if str(shape.shape_id) == shape_id:
        return shape
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for child in shape.shapes:
            found = _search(child, shape_id)
            if found is not None:
                return found
    return None


def _find_shape(slide, shape_id: str):
    """Return (shape, top_level_ancestor) or (None, None)."""
    if not shape_id:
        return None, None
    for top in slide.shapes:
        found = _search(top, str(shape_id))
        if found is not None:
            return found, top
    return None, None


# --------------------------------------------------------------------------- #
# Run highlighting
# --------------------------------------------------------------------------- #

def _segments(paragraph) -> list[tuple[_Run | None, int, int]]:
    """(run or None, start, end) over the paragraph text, matching ``paragraph.text`` offsets."""
    out: list[tuple[_Run | None, int, int]] = []
    pos = 0
    for child in paragraph._p:
        tag = child.tag
        if tag == f"{_A}r":
            run = _Run(child, paragraph)
            n = len(run.text)
            out.append((run, pos, pos + n))
            pos += n
        elif tag == f"{_A}br":
            pos += 1
        elif tag == f"{_A}fld":
            t = child.find(f"{_A}t")
            pos += len(t.text or "") if t is not None else 0
    return out


def _mark_run(run: _Run, color: RGBColor) -> None:
    run.font.color.rgb = color
    run.font.bold = True
    run.font.underline = True


def _highlight_span(paragraph, start: int, end: int, color: RGBColor) -> int:
    n = 0
    for run, s, e in _segments(paragraph):
        if run is not None and s < end and e > start and e > s:
            _mark_run(run, color)
            n += 1
    return n


def _highlight_value(paragraph, value: str, color: RGBColor) -> int:
    """Highlight every occurrence of *value* in the paragraph (runs overlapping each occurrence)."""
    if not value:
        return 0
    text = "".join(
        run.text if run is not None else "" for run, _s, _e in _segments(paragraph)
    )
    n, idx = 0, text.find(value)
    while idx >= 0:
        n += _highlight_span(paragraph, idx, idx + len(value), color)
        idx = text.find(value, idx + len(value))
    return n


def _highlight_frame(text_frame, issue: Issue, color: RGBColor, targeted: bool) -> int:
    paras = list(text_frame.paragraphs)
    if targeted and 0 <= issue.paragraph_index < len(paras) and 0 <= issue.char_start < issue.char_end:
        n = _highlight_span(paras[issue.paragraph_index], issue.char_start, issue.char_end, color)
        if n:
            return n
    if not issue.deck_value:
        return 0
    return sum(_highlight_value(p, issue.deck_value, color) for p in paras)


def _highlight_shape(shape, issue: Issue, color: RGBColor) -> None:
    if getattr(shape, "has_table", False) and shape.has_table:
        table = shape.table
        r, c = issue.table_row, issue.table_col
        if 0 <= r < len(table.rows) and 0 <= c < len(table.columns):
            if _highlight_frame(table.cell(r, c).text_frame, issue, color, True):
                return
        for row in table.rows:
            for cell in row.cells:
                _highlight_frame(cell.text_frame, issue, color, False)
        return
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        _highlight_frame(shape.text_frame, issue, color, True)
    # Charts: no run-level highlight possible; the marker flags them.


# --------------------------------------------------------------------------- #
# Markers
# --------------------------------------------------------------------------- #

def _add_marker(slide, prs, anchor, issue: Issue, offset_index: int) -> None:
    size = int(min(prs.slide_width, prs.slide_height) * 0.04)
    size = max(size, int(Emu(190000)))
    gap = int(size * 1.1)
    try:
        if anchor is not None and None not in (anchor.left, anchor.top, anchor.width):
            x = anchor.left + anchor.width - size // 2 - offset_index * gap
            y = anchor.top - size // 2
        else:
            raise TypeError
    except TypeError:
        x = prs.slide_width - size - int(size * 0.5) - offset_index * gap
        y = int(size * 0.5)
    x = int(min(max(x, 0), prs.slide_width - size))
    y = int(min(max(y, 0), prs.slide_height - size))
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, x, y, size, size)
    shp.name = f"NumCheck marker {issue.id}"
    shp.fill.solid()
    shp.fill.fore_color.rgb = _color(issue)
    shp.line.color.rgb = _WHITE
    shp.line.width = Pt(0.75)
    shp.shadow.inherit = False
    tf = shp.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.word_wrap = False
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = str(issue.id)
    run.font.size = Pt(8 if len(str(issue.id)) < 3 else 6)
    run.font.bold = True
    run.font.color.rgb = _WHITE


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #

def _append_notes(slide, issues: list[Issue]) -> None:
    tf = slide.notes_slide.notes_text_frame
    if tf is None:
        raise ValueError("slide has no notes body placeholder")
    existing = bool(tf.text.strip())
    head = tf.add_paragraph() if existing or tf.paragraphs else tf.paragraphs[0]
    head.text = f"NumCheck: {len(issues)} issue{'s' if len(issues) != 1 else ''} on this slide"
    for i in issues:
        line = f"#{i.id} [{i.severity.value.upper()}] {type_label(i.issue_type.value)}: {i.message}"
        extras = []
        if i.deck_value:
            extras.append(f"deck {i.deck_value}")
        if i.expected_value:
            extras.append(f"expected {i.expected_value}")
        if i.source_cell:
            extras.append(f"source {i.source_cell}")
        if extras:
            line += " (" + "; ".join(extras) + ")"
        tf.add_paragraph().text = line


# --------------------------------------------------------------------------- #
# Summary slides
# --------------------------------------------------------------------------- #

def _blank_layout(prs):
    """A layout with no placeholders if there is one, else the layout with the fewest."""
    layouts = list(prs.slide_layouts)
    for layout in layouts:
        if len(layout.placeholders) == 0:
            return layout
    return min(layouts, key=lambda lo: len(lo.placeholders))


def _strip_placeholders(slide) -> None:
    for ph in list(slide.placeholders):
        ph._element.getparent().remove(ph._element)


def _set_cell(cell, text: str, size: float, bold=False, color=_INK, fill=None, align=None) -> None:
    cell.margin_left = cell.margin_right = Emu(60000)
    cell.margin_top = cell.margin_bottom = Emu(20000)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.fill.solid()
    cell.fill.fore_color.rgb = fill if fill is not None else _WHITE
    tf = cell.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    if align is not None:
        p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color


def _add_summary_slide(prs, issues: list[Issue], page: int, pages: int) -> None:
    slide = prs.slides.add_slide(_blank_layout(prs))
    _strip_placeholders(slide)
    W, H = prs.slide_width, prs.slide_height
    scale = W / 12192000.0
    margin = int(W * 0.04)

    tb = slide.shapes.add_textbox(margin, int(H * 0.04), W - 2 * margin, int(H * 0.09))
    tb.name = "NumCheck summary title"
    tf = tb.text_frame
    tf.word_wrap = True
    r = tf.paragraphs[0].add_run()
    r.text = "NumCheck: flagged issues" + (f" ({page} of {pages})" if pages > 1 else "")
    r.font.size = Pt(max(16, 24 * scale))
    r.font.bold = True
    r.font.color.rgb = _ACCENT

    top = int(H * 0.15)
    avail_h = H - top - int(H * 0.05)
    rows = len(issues) + 1
    row_h = int(avail_h / (ISSUES_PER_SUMMARY_SLIDE + 1))
    gf = slide.shapes.add_table(rows, 5, margin, top, W - 2 * margin, row_h * rows)
    gf.name = "NumCheck summary table"
    table = gf.table
    tw = W - 2 * margin
    fracs = (0.05, 0.08, 0.06, 0.17, 0.64)
    for idx, f in enumerate(fracs):
        table.columns[idx].width = int(tw * f)
    for rr in table.rows:
        rr.height = row_h
    size = max(7.0, 10.0 * scale)
    max_chars = max(50, int(tw * fracs[4] / 12700 / (size * 0.5)))
    for idx, h in enumerate(("#", "Severity", "Slide", "Type", "Issue")):
        _set_cell(table.cell(0, idx), h, size, True, _WHITE, _ACCENT)
    for n, i in enumerate(issues, start=1):
        msg = i.message if len(i.message) <= max_chars else i.message[: max_chars - 1].rstrip() + "…"
        fill = _WHITE
        _set_cell(table.cell(n, 0), str(i.id), size, False, _INK, fill, PP_ALIGN.RIGHT)
        _set_cell(table.cell(n, 1), i.severity.value.upper(), size, True, _color(i), fill)
        _set_cell(table.cell(n, 2), str(i.slide_number), size, False, _INK, fill, PP_ALIGN.RIGHT)
        _set_cell(table.cell(n, 3), type_label(i.issue_type.value), size, False, _INK, fill)
        _set_cell(table.cell(n, 4), msg, size, False, _INK, fill)


def _add_summary_slides(prs, issues: list[Issue]) -> None:
    ordered = sort_issues(issues)
    pages = -(-len(ordered) // ISSUES_PER_SUMMARY_SLIDE)
    for p in range(pages):
        chunk = ordered[p * ISSUES_PER_SUMMARY_SLIDE:(p + 1) * ISSUES_PER_SUMMARY_SLIDE]
        _add_summary_slide(prs, chunk, p + 1, pages)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def annotate_deck(src_path: str, out_path: str, result: "ReviewResult | list[Issue]") -> list[str]:
    """Write an annotated copy of the deck. Returns human-readable failure messages."""
    res = as_result(result)
    failures: list[str] = []
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if Path(src_path).resolve() != Path(out_path).resolve():
        shutil.copyfile(src_path, out_path)
    prs = Presentation(out_path)
    n_slides = len(prs.slides)

    by_slide: dict[int, list[Issue]] = {}
    for issue in sort_issues(res.issues):
        by_slide.setdefault(issue.slide_number, []).append(issue)

    for slide_number, slide_issues in sorted(by_slide.items()):
        if slide_number < 1 or slide_number > n_slides:
            failures.append(f"Slide {slide_number}: not in the deck ({len(slide_issues)} issue(s) only on summary).")
            continue
        slide = prs.slides[slide_number - 1]
        marker_count: dict[int, int] = {}
        for issue in slide_issues:
            try:
                shape, top = _find_shape(slide, issue.shape_id)
                if shape is not None:
                    _highlight_shape(shape, issue, _color(issue))
                key = id(top) if top is not None else 0
                idx = marker_count.get(key, 0)
                marker_count[key] = idx + 1
                _add_marker(slide, prs, top, issue, idx)
            except Exception as exc:  # noqa: BLE001 - one shape must never abort the deck
                log.warning("annotate: issue %s failed: %s", issue.id, exc)
                failures.append(f"Issue #{issue.id} (slide {slide_number}): {exc}")
        try:
            _append_notes(slide, slide_issues)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Slide {slide_number}: speaker notes not written ({exc})")

    if res.issues:
        try:
            _add_summary_slides(prs, res.issues)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Summary slides not written ({exc})")
    prs.save(out_path)
    return failures
