"""Extract numbers from a PowerPoint deck (text boxes, tables, footnotes)."""

from __future__ import annotations

import re

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from numcheck.extraction.chart_extractor import extract_chart_numbers
from numcheck.matching.normalizer import extract_period, find_number_tokens, parse_token
from numcheck.models import DeckNumber, Source

_FOOTNOTE_PREFIXES = ("source:", "note:", "notes:", "(*", "source ")


def extract_deck(pptx_path: str) -> list[DeckNumber]:
    prs = Presentation(pptx_path)
    out: list[DeckNumber] = []
    for slide_idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            out.extend(_walk_shape(slide_idx, shape))
    return out


def extract_footnotes(pptx_path: str) -> list[dict]:
    """Return footnote-ish text per slide (for source/period drift checks)."""
    prs = Presentation(pptx_path)
    footnotes: list[dict] = []
    for slide_idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text and _looks_like_footnote(text):
                    footnotes.append({"slide_number": slide_idx, "text": text})
            elif shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        text = cell.text.strip()
                        if text and _looks_like_footnote(text):
                            footnotes.append({"slide_number": slide_idx, "text": text})
    return footnotes


def _looks_like_footnote(text: str) -> bool:
    t = text.lower()
    return t.startswith(_FOOTNOTE_PREFIXES) or t.startswith("source")


def _walk_shape(slide_idx: int, shape) -> list[DeckNumber]:
    try:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            out: list[DeckNumber] = []
            for child in shape.shapes:
                out.extend(_walk_shape(slide_idx, child))
            return out

        # `has_table` / `has_chart` only exist on GraphicFrame shapes; pictures,
        # media, and connectors do not have them, so use getattr guards.
        if getattr(shape, "has_table", False):
            return _extract_table(slide_idx, shape)
        if getattr(shape, "has_chart", False):
            try:
                return extract_chart_numbers(slide_idx, shape)
            except Exception:
                return []
        if getattr(shape, "has_text_frame", False):
            if _looks_like_footnote(shape.text_frame.text):
                return []
            return _extract_text_shape(slide_idx, shape)
    except Exception:
        # One malformed shape should never abort the whole deck review.
        return []
    return []


def _extract_text_shape(slide_idx: int, shape) -> list[DeckNumber]:
    out: list[DeckNumber] = []
    shape_id = str(shape.shape_id)
    for para in shape.text_frame.paragraphs:
        text = para.text
        if not text.strip():
            continue
        out.extend(_numbers_from_text(slide_idx, shape_id, Source.TEXT_BOX, text, ""))
    return out


def _extract_table(slide_idx: int, shape) -> list[DeckNumber]:
    out: list[DeckNumber] = []
    shape_id = str(shape.shape_id)
    table = shape.table
    unit_hint = _table_unit_hint(table)
    nrows = len(table.rows)
    ncols = len(table.columns)
    for r in range(nrows):
        for c in range(ncols):
            cell = table.cell(r, c)
            text = cell.text
            if not text.strip():
                continue
            row_label = table.cell(r, 0).text if c > 0 else ""
            col_label = table.cell(0, c).text if r > 0 else ""
            label = f"{row_label} {col_label}".strip()
            for dn in _numbers_from_text(slide_idx, shape_id, Source.TABLE, text, unit_hint):
                dn.label = label or dn.label
                dn.table_row = r
                dn.table_col = c
                out.append(dn)
    return out


def _numbers_from_text(
    slide_idx: int,
    shape_id: str,
    source: Source,
    text: str,
    unit_hint: str,
) -> list[DeckNumber]:
    out: list[DeckNumber] = []
    for token, start, end in find_number_tokens(text):
        parsed = parse_token(token, unit_hint)
        if parsed is None:
            continue
        preceding = text[:start].strip()
        if len(preceding) > 80:
            preceding = "..." + preceding[-77:]
        label = _clean_label(preceding)
        period = extract_period(text[:start] + " " + text[end:]) or extract_period(label)
        out.append(
            DeckNumber(
                slide_number=slide_idx,
                shape_id=shape_id,
                source=source,
                raw_text=token,
                value=parsed.value,
                unit=parsed.dimension,
                scale=parsed.scale,
                period=period,
                label=label,
                context=text.strip(),
                significant=parsed.significant,
            )
        )
    return out


def _clean_label(label: str) -> str:
    return re.sub(r"[:;,\-–—|]+$", "", label).strip()


def _table_unit_hint(table) -> str:
    """Detect a dominant unit hint from table headers (e.g. '$m', '$bn', '%', 'x')."""
    headers: list[str] = []
    for c in range(len(table.columns)):
        headers.append(table.cell(0, c).text)
    for r in range(min(3, len(table.rows))):
        headers.append(table.cell(r, 0).text)
    joined = " ".join(headers).lower()
    if "%" in joined:
        return "%"
    if "x" in joined and "(" not in joined:
        return "x"
    for scale in ("bn", "mm", "m", "k", "b", "t"):
        if "$" + scale in joined or scale in joined and "$" in joined:
            return "$" + scale
    if "$" in joined:
        return "$"
    return ""
