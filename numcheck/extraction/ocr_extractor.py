"""Optional OCR fallback for numbers baked into slide images.

Requires LibreOffice (`soffice`) to render slides and `pytesseract` + Tesseract.
Returns an empty list when those binaries are unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from numcheck.models import DeckNumber, Source


def extract_deck_ocr(pptx_path: str) -> list[DeckNumber]:
    if shutil.which("soffice") is None and shutil.which("libreoffice") is None:
        return []
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return []

    return _run(pptx_path)


def _run(pptx_path: str) -> list[DeckNumber]:
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return []

    out: list[DeckNumber] = []
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(pptx_path)
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "png", "--outdir", tmp, str(src)],
            check=False,
            capture_output=True,
            timeout=120,
        )
        pngs = sorted(Path(tmp).glob("*.png"))
        for i, png in enumerate(pngs, start=1):
            try:
                text = pytesseract.image_to_string(Image.open(png), config="--psm 6")
            except Exception:
                continue
            for token, start, end in _number_tokens(text):
                try:
                    value = float(token.replace(",", "").replace("$", "").replace("%", ""))
                except ValueError:
                    continue
                out.append(
                    DeckNumber(
                        slide_number=i,
                        shape_id="ocr",
                        source=Source.OCR,
                        raw_text=token,
                        value=value,
                        label=text[max(0, start - 40):start].strip(),
                        context=text.strip(),
                        confidence=0.7,
                    )
                )
    return out


def _number_tokens(text: str):
    import re

    return [
        (m.group(0), m.start(), m.end())
        for m in re.finditer(r"[$£€]?\s*\(?-?\d[\d,]*(?:\.\d+)?\s*(?:%|bn|mm|m|k|b|x)?", text)
    ]
