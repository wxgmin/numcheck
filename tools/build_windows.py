"""Build the self-contained NumCheck Windows x64 ZIP."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libreoffice", required=True, type=Path,
                        help="LibreOffice x64 administrative image directory from msiexec /a")
    parser.add_argument("--output", required=True, type=Path, help="Path to the output ZIP")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64") or sys.maxsize <= 2**32:
        parser.error("Build using a 64-bit Python interpreter on Windows x64.")
    office = args.libreoffice.resolve()
    required = ("program/soffice.com", "program/soffice.exe", "program/soffice.bin",
                "share/registry", "System64/vcruntime140.dll", "System64/msvcp140.dll", "LICENSE.html", "NOTICE")
    missing = [part for part in required if not (office / part).exists()]
    if missing:
        parser.error(f"LibreOffice administrative image is incomplete: {', '.join(missing)}")
    output = args.output.resolve()
    if output.suffix.lower() != ".zip" or output.exists():
        parser.error("Choose a new output path ending in .zip.")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="numcheck-build-", dir=output.parent) as folder:
        staging = Path(folder)
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--noupx", "--name", "NumCheck",
            "--paths", str(ROOT), "--distpath", str(staging / "dist"),
            "--workpath", str(staging / "work"), "--specpath", str(staging),
            "--collect-data", "numcheck", "--collect-data", "pypdfium2",
            "--hidden-import", "msoffcrypto", "--exclude-module", "pymupdf", "--exclude-module", "fitz",
            "--copy-metadata", "rapidocr-onnxruntime",
        ]
        for package in ("openai", "anthropic", "pypdfium2_raw", "onnxruntime", "rapidocr_onnxruntime"):
            command.extend(("--collect-all", package))
        subprocess.run([*command, str(ROOT / "tools" / "portable_numcheck.py")], cwd=ROOT, check=True)
        app = staging / "dist" / "NumCheck"
        shutil.copy2(ROOT / "LICENSE", app)

        (app / "Start Here.txt").write_text(
            "NumCheck for Windows x64\n\n"
            "Extract the entire ZIP. Open NumCheck.exe to start the local browser app.\n"
            "Keep the NumCheck window open while you review your files.\n\n"
            "Slide previews, workbook review and OCR run locally.\n"
            "The LibreOffice folder contains The Document Foundation's renderer, licenses and notices.\n"
            "LibreOffice source releases: https://downloadarchive.documentfoundation.org/libreoffice/old/\n"
            "The optional RapidOCR engine is available under Apache 2.0: https://github.com/RapidAI/RapidOCR\n",
            encoding="utf-8",
        )
        archive = staging / output.name
        bundled = Path(app.name) / "LibreOffice"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=3) as package:
            for item in app.rglob("*"):
                if item.is_file():
                    package.write(item, item.relative_to(app.parent))
            # The admin image includes an installer and system DLLs. Stream the
            # application tree directly into the ZIP and place x64 DLLs app-locally.
            for item in office.rglob("*"):
                relative = item.relative_to(office)
                if item.is_file() and relative.parts[0] not in ("System", "System64") and item.suffix.lower() != ".msi":
                    package.write(item, bundled / relative)
            for dll in (office / "System64").glob("*.dll"):
                if not (office / "program" / dll.name).is_file():
                    package.write(dll, bundled / "program" / dll.name)
        archive.rename(output)
    print(output)


if __name__ == "__main__":
    main()
