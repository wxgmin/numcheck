"""Local, read-only previews of the original Office evidence."""
from __future__ import annotations

import base64
import io
import threading
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter
from pptx import Presentation

from numcheck.extraction.fileio import soffice_binary, soffice_convert
from numcheck.models import ModelValue
from numcheck.numfmt import parse_number_format
from numcheck.report.annotate_pptx import _find_shape

_PDF_RENDER_LOCK = threading.Lock()  # PDFium is not thread-safe across documents.


class PreviewUnavailable(Exception):
    """A source could not be shown at the requested location."""


def _display(cell) -> str:
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        fmt = parse_number_format(cell.number_format)
        if fmt.decimals >= 0:
            value /= fmt.divisor
            if fmt.dimension == "percent":
                return f"{value:.{fmt.decimals}%}"
            return f"{value:,.{fmt.decimals}f}" if "," in cell.number_format else f"{value:.{fmt.decimals}f}"
    return str(value)[:500]


def workbook_preview(path: Path, model: ModelValue) -> dict:
    """Show a small grid from the saved workbook without recalculating it."""
    row, col = coordinate_to_tuple(model.cell_address)
    top, left = max(1, row - 5), max(1, col - 4)
    bottom, right = min(1_048_576, row + 6), min(16_384, col + 5)
    with closing(load_workbook(path, read_only=True, data_only=True, keep_links=False)) as workbook:
        if model.sheet_name not in workbook.sheetnames:
            raise PreviewUnavailable("The cited sheet is unavailable in the original workbook.")
        sheet = workbook[model.sheet_name]
        rows = []
        for r, cells in enumerate(sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right), top):
            rows.append({"row": r, "cells": [{
                "address": f"{get_column_letter(c)}{r}",
                "text": _display(cell),
                "numeric": isinstance(cell.value, (float, int)) and not isinstance(cell.value, bool),
                "bold": bool(getattr(getattr(cell, "font", None), "bold", False)),
            } for c, cell in enumerate(cells, left)]})
        target = next((c["text"] for r in rows for c in r["cells"] if c["address"] == model.cell_address), "")
        return {
            "sheet": sheet.title, "address": model.cell_address, "formula": model.formula,
            "columns": [get_column_letter(c) for c in range(left, right + 1)], "rows": rows,
            "saved_value": target, "number_format": model.number_format, "hidden_sheet": sheet.sheet_state != "visible",
            "row_label": model.row_label, "col_label": model.col_label,
        }


def _region(presentation, slide, location):
    shape, ancestor = _find_shape(slide, location.shape_id)
    if ancestor is None or ancestor.rotation:
        return None, ""
    x, y, w, h = ancestor.left, ancestor.top, ancestor.width, ancestor.height
    kind = "Chart" if getattr(shape, "has_chart", False) else "Slide object"
    if shape is ancestor and shape.has_table and location.table_row >= 0 and location.table_col >= 0:
        table = shape.table
        r, c = location.table_row, location.table_col
        if r < len(table.rows) and c < len(table.columns):
            while table.cell(r, c).is_spanned:
                origin = next(((ri, ci) for ri in range(r + 1) for ci in range(c + 1)
                               if table.cell(ri, ci).is_merge_origin
                               and r < ri + table.cell(ri, ci).span_height
                               and c < ci + table.cell(ri, ci).span_width), None)
                if origin is None:
                    break
                r, c = origin
            cell = table.cell(r, c)
            x += sum(table.columns[i].width for i in range(c))
            y += sum(table.rows[i].height for i in range(r))
            w = sum(table.columns[i].width for i in range(c, c + cell.span_width))
            h = sum(table.rows[i].height for i in range(r, r + cell.span_height))
            kind = "Table cell"
    if min(w, h) <= 0:
        return None, ""
    return (x / presentation.slide_width, y / presentation.slide_height,
            (x + w) / presentation.slide_width, (y + h) / presentation.slide_height), kind


def _text_region(page, token, region):
    """Return a text box only when the token has a unique hit inside the recorded location."""
    if not token or not region:
        return None
    width, height = page.get_size()
    with closing(page.get_textpage()) as text:
        with closing(text.search(token, match_case=True, match_whole_word=True)) as search:
            hits = []
            while (hit := search.get_next()) is not None:
                boxes = [text.get_charbox(i) for i in range(hit[0], hit[0] + hit[1])]
                if not boxes:
                    continue
                left, bottom = min(box[0] for box in boxes), min(box[1] for box in boxes)
                right, top = max(box[2] for box in boxes), max(box[3] for box in boxes)
                x, y = (left + right) / (2 * width), 1 - (top + bottom) / (2 * height)
                if region[0] <= x <= region[2] and region[1] <= y <= region[3]:
                    hits.append((left / width, 1 - top / height, right / width, 1 - bottom / height))
                    if len(hits) > 1:
                        return None
            return hits[0] if hits else None


def slide_preview(path: Path, directory: Path, location, token: str, *, exact_text: bool) -> dict:
    try:
        import pypdfium2 as pdfium
        from PIL import ImageDraw
    except ImportError as exc:
        raise PreviewUnavailable("Install NumCheck with the preview extra to render slides.") from exc
    pdf = directory / f"{path.stem}.pdf"
    if not pdf.is_file():
        if not soffice_binary():
            raise PreviewUnavailable("Install LibreOffice on this computer to render the original PowerPoint.")
        directory.mkdir(exist_ok=True)
        export = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'
        with TemporaryDirectory(dir=directory) as staging:
            converted = soffice_convert(str(path), staging, export, timeout=75)
            if not converted:
                raise PreviewUnavailable("LibreOffice could not render this presentation.")
            Path(converted).replace(pdf)
    presentation = Presentation(path)
    index = location.slide_number - 1
    with _PDF_RENDER_LOCK, closing(pdfium.PdfDocument(pdf)) as document:
        if not 0 <= index < len(presentation.slides) or len(document) != len(presentation.slides):
            raise PreviewUnavailable("The exported pages could not be mapped reliably to the original slides.")
        region, kind = _region(presentation, presentation.slides[index], location)
        with closing(document[index]) as page:
            if exact_text and kind != "Chart":
                exact = _text_region(page, token, region)
                if exact is not None:
                    region, kind = exact, "Number"
            with closing(page.render(scale=1440 / page.get_width())) as bitmap:
                image = bitmap.to_pil().convert("RGB")
                if region:
                    x0, y0, x1, y1 = region
                    box = (max(0, x0 * image.width - 3), max(0, y0 * image.height - 3),
                           min(image.width - 1, x1 * image.width + 3), min(image.height - 1, y1 * image.height + 3))
                    if box[0] < box[2] and box[1] < box[3]:
                        draw = ImageDraw.Draw(image)
                        draw.rectangle(box, outline="#fff6cf", width=7)
                        draw.rectangle(box, outline="#bd5b06", width=3)
                    else:
                        kind = ""
                output = io.BytesIO()
                image.save(output, format="PNG")
    return {"image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"),
            "highlight": kind, "renderer": "LibreOffice"}
