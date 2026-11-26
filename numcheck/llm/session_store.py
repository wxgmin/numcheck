"""User-local storage for rotating ChatGPT OAuth credentials."""
from __future__ import annotations

import ctypes
import json
import os
import stat
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from tempfile import NamedTemporaryFile


def state_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "NumCheck"


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        return data
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.addressof(buffer))
    output = _Blob()
    crypt = ctypes.windll.crypt32
    convert = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not convert(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("Windows could not access the saved ChatGPT credentials.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(output.data))


class SessionStore:
    @contextmanager
    def locked(self):
        directory = state_directory()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / "chatgpt-session.lock").open("a+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield directory / "chatgpt-session"
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def read(self, path: Path) -> dict | None:
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                info = os.fstat(stream.fileno())
                if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode)):
                    raise PermissionError("The ChatGPT session file is accessible to other users.")
                return json.loads(_protect(stream.read(), decrypt=True))
        except FileNotFoundError:
            return None

    def write(self, path: Path, session: dict) -> None:
        protected = _protect(json.dumps(session).encode())
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(protected)
                stream.close()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
