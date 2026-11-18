"""Annotated deck copy with flagged numbers highlighted."""

from __future__ import annotations

import shutil
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches, Pt

from numcheck.models import Issue
from numcheck.report import SEVERITY_COLORS

_PPTX_COLORS = {
    "high": RGBColor(0xD9, 0x53, 0x4F),
    "medium": RGBColor(0xF0, 0xAD, 0x4E),
    "low": RGBColor(0x5B, 0xC0, 0xDE),
}


def annotate_deck(src_path: str, out_path: str, issues: list[Issue]) -> None:
    shutil.copyfile(src_path, out_path)
    prs = Presentation(out_path)

    # group issues by slide
    by_slide: dict[int, list[Issue]] = {}
    for issue in issues:
        by_slide.setdefault(issue.slide_number, []).append(issue)

    for slide_number, slide_issues in by_slide.items():
        if slide_number < 1 or slide_number > len(prs.slides):
            continue
        slide = prs.slides[slide_number - 1]
        for issue in slide_issues:
            color = _PPTX_COLORS.get(issue.severity.value, _PPTX_COLORS["low"])
            shape = _find_shape(slide, issue.shape_id)
            if shape is not None:
                _highlight_shape(shape, issue.deck_value, color)

    _add_notes_slide(prs, issues)
    prs.save(out_path)


def _find_shape(slide, shape_id: str):
    if not shape_id:
        return None
    for shape in slide.shapes:
        found = _search_shape(shape, shape_id)
        if found is not None:
            return found
    return None


def _search_shape(shape, shape_id: str):
    if str(shape.shape_id) == shape_id:
        return shape
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for child in shape.shapes:
            found = _search_shape(child, shape_id)
            if found is not None:
                return found
    return None


def _highlight_shape(shape, deck_value: str, color: RGBColor) -> None:
    try:
        if getattr(shape, "has_text_frame", False):
            _highlight_text_frame(shape.text_frame, deck_value, color)
        elif getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    _highlight_text_frame(cell.text_frame, deck_value, color)
        elif getattr(shape, "has_chart", False):
            try:
                shape.line.color.rgb = color
                shape.line.width = Pt(2.0)
            except Exception:
                pass
    except Exception:
        # Annotation is best-effort; never fail the report for one shape.
        pass


def _highlight_text_frame(text_frame, deck_value: str, color: RGBColor) -> None:
    for para in text_frame.paragraphs:
        for run in para.runs:
            if not deck_value or deck_value in run.text:
                run.font.color.rgb = color
                run.font.bold = True


def _add_notes_slide(prs: Presentation, issues: list[Issue]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout
    left, top, width, height = Inches(0.6), Inches(0.5), Inches(12), Inches(0.8)
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.text = "NumCheck — flagged issues"
    tf.paragraphs[0].font.size = Pt(24)
    tf.paragraphs[0].font.bold = True

    body = slide.shapes.add_textbox(Inches(0.6), Inches(1.4), Inches(12), Inches(6.5))
    btf = body.text_frame
    btf.word_wrap = True
    lines = [f"Slide {i.slide_number}: [{i.issue_type.value}] {i.message}" for i in issues]
    for idx, line in enumerate(lines):
        p = btf.paragraphs[0] if idx == 0 else btf.add_paragraph()
        p.text = line
        p.font.size = Pt(12)
