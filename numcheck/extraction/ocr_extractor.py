"""Optional OCR fallback for numbers baked into slide pictures.

RapidOCR reads embedded raster pictures directly. The legacy slide-render and
Tesseract path remains available when its optional dependencies are installed.

Every dependency is optional.  A missing one yields ``ReviewWarning(
"ocr_unavailable", <what is missing and how to install it>)`` and an empty list,
never an exception: OCR only ever *adds* coverage.

OCR numbers carry ``confidence = 0.7`` and are deduplicated against the native
numbers of the same slide, because a picture-in-picture of live text (or text
that LibreOffice rasterised while rendering) would otherwise be counted twice.
"""

from __future__ import annotations

import importlib.util
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from numcheck.config import Config
from numcheck.extraction import fileio
from numcheck.models import DeckNumber, ReviewWarning, Source

OCR_CONFIDENCE = 0.7
_RENDER_DPI = 200
_MIN_WORD_CONF = 20.0  # Tesseract reports -1 for non-words; very low scores are noise


def _warn(warnings: list | None, message: str) -> None:
    if warnings is not None:
        warnings.append(ReviewWarning("ocr_unavailable", message))


# --------------------------------------------------------------------------- #
# Dependency checks
# --------------------------------------------------------------------------- #


def _find_tesseract_deps() -> tuple[object | None, str]:
    """(pytesseract module, "") when OCR is usable, else (None, reason)."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # noqa: F401
    except Exception:  # noqa: BLE001
        return None, "pytesseract and Pillow are not installed (pip install pytesseract pillow)"
    try:
        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001
        return None, ("the Tesseract OCR program was not found (install it from "
                      "https://github.com/tesseract-ocr/tesseract and make sure it is on PATH)")
    return pytesseract, ""


def _can_render_pages() -> bool:
    if shutil.which("pdftoppm"):
        return True
    try:
        import fitz  # type: ignore  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _render_pdf_pages(pdf: str, out_dir: str) -> list[str]:
    """PNG path per page, in page order. Prefers ``pdftoppm``, falls back to PyMuPDF."""
    exe = shutil.which("pdftoppm")
    if exe:
        try:
            subprocess.run([exe, "-png", "-r", str(_RENDER_DPI), pdf, str(Path(out_dir) / "page")],
                           check=False, capture_output=True, timeout=300)
        except (subprocess.TimeoutExpired, OSError):
            pass
        pngs = sorted(Path(out_dir).glob("page*.png"))  # pdftoppm zero-pads page numbers
        if pngs:
            return [str(p) for p in pngs]
    try:
        import fitz  # type: ignore

        paths = []
        with fitz.open(pdf) as doc:
            for i, page in enumerate(doc, start=1):
                target = str(Path(out_dir) / f"fitz-{i:04d}.png")
                page.get_pixmap(dpi=_RENDER_DPI).save(target)
                paths.append(target)
        return paths
    except Exception:  # noqa: BLE001
        return []


def _visible_slide_numbers(pptx_path: str) -> list[int]:
    """Slide numbers LibreOffice exports (hidden slides are left out of the PDF)."""
    try:
        from pptx import Presentation

        prs = Presentation(pptx_path)
        return [i for i, s in enumerate(prs.slides, start=1) if s._element.get("show") != "0"]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# Text reconstruction
# --------------------------------------------------------------------------- #


def lines_from_data(data: dict) -> list[str]:
    """Rebuild text lines from ``pytesseract.image_to_data`` output (dict form).

    Words are grouped by (block, paragraph, line) and ordered by their left edge,
    so a table row read as separate cells stays one line (label then numbers).
    """
    texts = data.get("text", [])
    groups: dict[tuple, list[tuple[int, str]]] = {}
    order: list[tuple] = []
    for i, word in enumerate(texts):
        word = (word or "").strip()
        if not word:
            continue
        try:
            conf = float(data.get("conf", [])[i])
        except (TypeError, ValueError, IndexError):
            conf = 100.0
        if 0 <= conf < _MIN_WORD_CONF:
            continue
        key = (data.get("block_num", [0] * len(texts))[i], data.get("par_num", [0] * len(texts))[i],
               data.get("line_num", [0] * len(texts))[i])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((int(data.get("left", [0] * len(texts))[i]), word))
    return [" ".join(w for _l, w in sorted(groups[k])) for k in order]


# --------------------------------------------------------------------------- #
# Scanning + dedupe
# --------------------------------------------------------------------------- #


def _same_number(a: DeckNumber, b: DeckNumber) -> bool:
    return abs(a.value - b.value) <= 1e-9 * max(abs(a.value), abs(b.value), 1.0) and (
        a.unit == b.unit or "none" in (a.unit, b.unit))


def numbers_from_lines(slide_number: int, lines: list[str], native: list[DeckNumber], config: Config,
                       *, shape_id: str = "ocr") -> list[DeckNumber]:
    """Scan OCR lines of one slide; drop numbers already read natively on that slide."""
    from numcheck.extraction.pptx_extractor import _SlideCtx, scan_paragraph

    ctx = _SlideCtx(number=slide_number, config=config)
    # reuse the slide's own unit banner if a native number inherited one
    hint = None
    out: list[DeckNumber] = []
    pool = [n for n in native if n.slide_number == slide_number and n.source != Source.OCR]
    for li, line in enumerate(lines):
        try:
            nums, _skipped = scan_paragraph(ctx, line, hint, source=Source.OCR, shape_id=shape_id, para_idx=li,
                                            uid_prefix=f"s{slide_number}-{shape_id}-ocr-p{li}")
        except Exception:  # noqa: BLE001 - one odd line must not abort OCR
            continue
        for dn in nums:
            dn.confidence = OCR_CONFIDENCE
            twin = next((p for p in pool if _same_number(p, dn)), None)
            if twin is not None:
                pool.remove(twin)  # each native number cancels one OCR twin
                continue
            out.append(dn)
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _rapid_available() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def ocr_capability() -> dict:
    if _rapid_available():
        return {"available": True, "detail": "Reads embedded pictures locally with RapidOCR."}
    module, reason = _find_tesseract_deps()
    if module and fileio.soffice_binary() and _can_render_pages():
        return {"available": True, "detail": "Reads rendered slides locally with Tesseract."}
    return {"available": False,
            "detail": f"Install NumCheck with its OCR extra for RapidOCR. Tesseract fallback: {reason or 'slide renderer missing'}."}


def _rapid_lines(results) -> list[str]:
    boxes = []
    for polygon, text, score in results or []:
        if float(score) < 0.6 or not text.strip():
            continue
        xs, ys = zip(*polygon)
        height = max(ys) - min(ys)
        if height > 0:
            boxes.append(((max(ys) + min(ys)) / 2, min(xs), height, text.strip()))
    rows = []
    for center, left, height, text in sorted(boxes):
        if not rows or abs(center - rows[-1][0]) > 0.45 * min(height, rows[-1][1]):
            rows.append((center, height, []))
        rows[-1][2].append((left, text))
    # OCR sometimes drops the space between a fiscal tag and a title-cased metric.
    return [re.sub(r"(\b(?:FY|CY)(?:\d{4}|\d{2})(?:[AEF](?=[A-Z][a-z]))?)(?=[A-Z][a-z])",
                   r"\1 ", " ".join(text for _, text in sorted(row))) for _, _, row in rows]


def _picture_png(shape) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(shape.image.blob)) as original:
        w, h = original.size
        if w * h > 24_000_000:
            raise ValueError("image exceeds the OCR size limit")
        crop = (max(0, round(w * shape.crop_left)), max(0, round(h * shape.crop_top)),
                min(w, round(w * (1 - shape.crop_right))), min(h, round(h * (1 - shape.crop_bottom))))
        with original.crop(crop) as visible:
            if not visible.width or not visible.height:
                raise ValueError("image has no visible area")
            visible.thumbnail((2400, 2400))
            with visible.convert("RGBA") as transparent, Image.new("RGB", visible.size, "white") as image:
                image.paste(transparent, mask=transparent.getchannel("A"))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()


def _extract_rapid(path: str, config: Config, warnings: list | None) -> list[DeckNumber]:
    from pptx import Presentation
    from rapidocr_onnxruntime import RapidOCR

    from numcheck.extraction.pptx_extractor import _is_picture, _iter_shapes

    deck = Presentation(path)
    out = []
    engine = None
    for slide_no, slide in enumerate(deck.slides, 1):
        for shape, _ in _iter_shapes(slide.shapes):
            if not _is_picture(shape):
                continue
            if engine is None:
                engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
            try:
                boxes, _ = engine(_picture_png(shape), use_cls=False)
                out.extend(numbers_from_lines(slide_no, _rapid_lines(boxes), [], config,
                                              shape_id=str(shape.shape_id)))
            except Exception as exc:
                if warnings is not None:
                    warnings.append(ReviewWarning("ocr_picture_failed",
                                                  f"A picture on slide {slide_no} could not be read by OCR ({exc}).",
                                                  slide_number=slide_no, count=1))
    return out


def extract_deck_ocr(path: str, existing: list[DeckNumber] | None = None,
                     warnings: list | None = None, config: Config | None = None) -> list[DeckNumber]:
    """Read picture figures with the available local engine."""
    config = config or Config()
    if _rapid_available():
        try:
            return _extract_rapid(path, config, warnings)
        except Exception:
            _warn(warnings, "RapidOCR could not initialize. Numbers inside pictures were not read.")
            return []
    existing = existing or []
    pytesseract, why = _find_tesseract_deps()
    if pytesseract is None:
        _warn(warnings, f"OCR was requested but {why}. Numbers inside pictures were not read.")
        return []
    if not fileio.soffice_binary():
        _warn(warnings, "OCR was requested but LibreOffice was not found (needed to render slides). "
                        "Install LibreOffice from https://www.libreoffice.org. Numbers inside pictures were not read.")
        return []
    if not _can_render_pages():
        _warn(warnings, "OCR was requested but neither pdftoppm (poppler) nor PyMuPDF is installed "
                        "(pip install pymupdf). Numbers inside pictures were not read.")
        return []

    out: list[DeckNumber] = []
    try:
        with tempfile.TemporaryDirectory(prefix="numcheck_ocr_") as tmp:
            pdf = fileio.soffice_convert(str(path), tmp, "pdf")
            if not pdf:
                _warn(warnings, "OCR was requested but LibreOffice could not convert the deck to PDF. "
                                "Numbers inside pictures were not read.")
                return []
            pages = _render_pdf_pages(pdf, tmp)
            if not pages:
                _warn(warnings, "OCR was requested but the PDF pages could not be rendered to images. "
                                "Numbers inside pictures were not read.")
                return []
            visible = _visible_slide_numbers(str(path))
            slide_numbers = visible if len(visible) == len(pages) else list(range(1, len(pages) + 1))
            from PIL import Image

            for slide_no, png in zip(slide_numbers, pages):
                try:
                    with Image.open(png) as img:
                        data = pytesseract.image_to_data(img, config="--psm 6",
                                                         output_type=pytesseract.Output.DICT)
                    out.extend(numbers_from_lines(slide_no, lines_from_data(data), existing, config))
                except Exception as exc:  # noqa: BLE001
                    if warnings is not None:
                        warnings.append(ReviewWarning(
                            "ocr_page_failed", f"OCR failed on slide {slide_no} ({exc}).", slide_number=slide_no,
                            count=1))
    except Exception as exc:  # noqa: BLE001
        _warn(warnings, f"OCR failed unexpectedly ({exc}). Numbers inside pictures were not read.")
        return []
    return out
