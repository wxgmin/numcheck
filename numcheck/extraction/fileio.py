"""Input file validation shared by deck and model extraction.

Office Open XML files are zip archives with a ``[Content_Types].xml`` part.
Encrypted Office files are OLE compound documents (signature D0CF11E0), which is
also the format of legacy ``.xls`` / ``.ppt`` files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from numcheck.models import NumCheckInputError

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_KIND_MARKERS = {
    "pptx": ("presentationml",),
    "xlsx": ("spreadsheetml",),
}

_LEGACY_HINT = {
    "pptx": "Save it as .pptx (PowerPoint: File > Save As > PowerPoint Presentation) and try again.",
    "xlsx": "Save it as .xlsx (Excel: File > Save As > Excel Workbook) and try again.",
}


def sniff_office_file(path: str | os.PathLike, kind: str, password: str = "", max_mb: float = 0) -> str:
    """Validate *path* is a readable OOXML file of *kind* ("pptx" | "xlsx").

    Returns a path to read (a decrypted temp copy when a password was used).
    Raises :class:`NumCheckInputError` with an actionable message otherwise.
    """
    p = Path(path)
    name = p.name
    if not p.is_file():
        raise NumCheckInputError(f"File not found: {name}", file=name)
    size = p.stat().st_size
    if size == 0:
        raise NumCheckInputError(f"{name} is empty.", file=name)
    if max_mb and size > max_mb * 1024 * 1024:
        raise NumCheckInputError(
            f"{name} is {size / 1e6:.0f} MB, above the {max_mb:.0f} MB limit.",
            file=name, hint="Raise extraction.max_file_mb in numcheck.toml if this is expected.",
        )
    with open(p, "rb") as fh:
        head = fh.read(8)

    if head == OLE_SIGNATURE:
        if _is_encrypted_ole(p):
            if not password:
                raise NumCheckInputError(
                    f"{name} is password-protected.",
                    file=name, hint="Remove the password, or pass --password (requires `pip install msoffcrypto-tool`).",
                )
            return _decrypt(p, password)
        raise NumCheckInputError(
            f"{name} is a legacy binary Office file (.{'ppt' if kind == 'pptx' else 'xls'}), which is not supported.",
            file=name, hint=_LEGACY_HINT[kind],
        )

    if not zipfile.is_zipfile(p):
        raise NumCheckInputError(f"{name} is not a valid .{kind} file (it is not an Office Open XML package).",
                                 file=name, hint=_LEGACY_HINT[kind])
    try:
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" not in names:
                raise NumCheckInputError(f"{name} is a zip archive but not an Office document.", file=name)
            ct = zf.read("[Content_Types].xml").decode("utf-8", "ignore").lower()
            if zf.testzip() is not None:
                raise NumCheckInputError(f"{name} is corrupted (bad zip entry).", file=name,
                                         hint="Re-save the file from Office and try again.")
    except zipfile.BadZipFile as exc:
        raise NumCheckInputError(f"{name} is corrupted: {exc}", file=name) from exc

    markers = _KIND_MARKERS[kind]
    if not any(m in ct for m in markers):
        other = "xlsx" if kind == "pptx" else "pptx"
        if any(m in ct for m in _KIND_MARKERS[other]):
            raise NumCheckInputError(
                f"{name} looks like a .{other} file, but a .{kind} was expected.", file=name,
                hint="Check that the deck and model were uploaded in the right slots.",
            )
        raise NumCheckInputError(f"{name} is not a .{kind} document.", file=name)
    if "xlsb" in name.lower() or "binary.macroenabled" in ct and kind == "xlsx":
        raise NumCheckInputError(f"{name} is an .xlsb binary workbook, which is not supported.",
                                 file=name, hint=_LEGACY_HINT["xlsx"])
    return str(p)


def _is_encrypted_ole(p: Path) -> bool:
    try:
        data = p.read_bytes()[:65536]
    except OSError:
        return False
    return b"E\x00n\x00c\x00r\x00y\x00p\x00t\x00e\x00d\x00P\x00a\x00c\x00k\x00a\x00g\x00e" in data or \
        b"EncryptionInfo" in data.replace(b"\x00", b"")


def _decrypt(p: Path, password: str) -> str:
    try:
        import msoffcrypto  # type: ignore
    except ImportError as exc:
        raise NumCheckInputError(
            f"{p.name} is password-protected and msoffcrypto-tool is not installed.",
            file=p.name, hint="pip install msoffcrypto-tool",
        ) from exc
    out = Path(tempfile.mkdtemp(prefix="numcheck_dec_")) / p.name
    try:
        with open(p, "rb") as src, open(out, "wb") as dst:
            office = msoffcrypto.OfficeFile(src)
            office.load_key(password=password)
            office.decrypt(dst)
    except Exception as exc:  # noqa: BLE001 - library raises many types
        raise NumCheckInputError(f"Could not decrypt {p.name}: wrong password?", file=p.name) from exc
    return str(out)


def soffice_binary() -> str | None:
    """Path to LibreOffice's soffice, if installed."""
    configured = os.environ.get("NUMCHECK_SOFFICE")
    if configured and Path(configured).is_file():
        return configured
    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    for guess in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                  r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                  "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if os.path.isfile(guess):
            return guess
    return None


def soffice_convert(src: str, out_dir: str, target: str, timeout: int = 180) -> str | None:
    """Convert *src* with LibreOffice (e.g. target="xlsx", "pdf"). Returns output path or None."""
    exe = soffice_binary()
    if not exe:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="numcheck_office_") as profile:
            user_profile = Path(profile) / "user"
            user_profile.mkdir()
            (user_profile / "registrymodifications.xcu").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
                '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
                '</item></oor:items>', encoding="utf-8",
            )
            result = subprocess.run(
                [exe, "-env:UserInstallation=" + Path(profile).as_uri(), "--headless", "--norestore",
                 "--convert-to", target, "--outdir", out_dir, src],
                check=False, capture_output=True, timeout=timeout,
            )
            if result.returncode:
                return None
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = Path(out_dir) / (Path(src).stem + "." + target.split(":")[0])
    return str(out) if out.exists() else None
